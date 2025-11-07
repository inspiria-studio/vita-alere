from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
import re
import math
import time


class FilterNearbyCities(Tool):
    def execute(self, context: Context) -> TextResponse:
        cep = context.parameters.get("cep", "")
        places_key = context.credentials.get("places_apikey", "")
        routes_key = context.credentials.get("test_apikey", "")  # Chave para Google Routes API

        if not cep:
            return TextResponse(data="CEP nao fornecido.")
        if not places_key:
            return TextResponse(data="Chave da API do Google Maps nao fornecida.")

        cep = re.sub(r'\D', '', cep)
        #print(f"[DEBUG] CEP processado: {cep}")
        coords, estado = self.get_coordinates_by_cep(cep, places_key)
        #print(f"[DEBUG] Coordenadas obtidas: {coords}, Estado: {estado}")
        
        if not coords:
            return TextResponse(data="Nao foi possivel obter coordenadas a partir do CEP.")

        lat = coords["lat"]
        lng = coords["lng"]
        #print(f"[DEBUG] Lat: {lat}, Lng: {lng}")

        cidades = self.buscar_cidades_por_overpass(lat, lng, estado)
        #print(f"[DEBUG] Cidades encontradas pelo Overpass: {len(cidades) if isinstance(cidades, list) else 'erro'}")
        if isinstance(cidades, str):
            return TextResponse(data=cidades)
        elif not cidades:
            return TextResponse(data="Nenhuma cidade encontrada em ate 50 km.")

        # Filtrar cidades que possuem servicos de saude mental
        # Passa as coordenadas do usuário e a chave da API de rotas se disponível
        cidades_com_servicos = self.filtrar_cidades_com_servicos(
            cidades, 
            user_coords=coords if routes_key else None,
            routes_api_key=routes_key
        )
        
        if not cidades_com_servicos:
            return TextResponse(data="Nenhuma cidade proxima possui servicos de saude mental disponiveis.")

        return TextResponse(data={
            "status": "success",
            "action": "com a lista de cidades proximas que possuem servicos de saude mental, utilize o agente Get Services para buscar o servico que o usuario procura nessas cidades.",
            "cidades_proximas": cidades_com_servicos
        })

    def get_coordinates_by_cep(self, cep, api_key):
        try:
            via_url = f"https://viacep.com.br/ws/{cep}/json/"
            response = requests.get(via_url)
            data = response.json()
            if "erro" in data:
                return None

            cidade = data.get("localidade", "")
            estado = data.get("uf", "")
            if not cidade or not estado:
                return None

            query = f"{cidade}, {estado}, Brasil"
            geo_url = "https://maps.googleapis.com/maps/api/geocode/json"
            geo_response = requests.get(geo_url, params={"address": query, "key": api_key})
            geo_data = geo_response.json()
            if geo_data.get("status") == "OK":
                location = geo_data["results"][0]["geometry"]["location"]
                return {"lat": location["lat"], "lng": location["lng"]}, estado
            else:
                return None, None
        except Exception:
            return None, None

    def buscar_cidades_por_overpass(self, lat, lng, estado):
        endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.openstreetmap.ru/api/interpreter",
        ]

        # Query: restringe ao Brasil por area e busca relations admin_level 8 num raio
        query = f"""
        [out:json][timeout:60];
        area["ISO3166-1"="BR"][admin_level=2]->.br;
        relation
          ["boundary"="administrative"]
          ["admin_level"="8"]
          (around:50000,{lat},{lng})
          (area.br);
        out tags center;
        """

        headers = {"User-Agent": "Weni-Agent/1.0 (contato@inspiria.studio)",
                   "Content-Type": "application/x-www-form-urlencoded"}

        last_err = None
        data = None

        for overpass_url in endpoints:
            try:
                resp = requests.post(overpass_url, data={"data": query},
                                     headers=headers, timeout=45)
                # Tratamento de erros comuns
                if resp.status_code == 429:
                    last_err = "HTTP 429 (rate limit)"
                    continue
                if resp.status_code == 504:
                    last_err = "HTTP 504 (gateway timeout)"
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except requests.exceptions.Timeout:
                last_err = "timeout"
                continue
            except requests.exceptions.RequestException as e:
                last_err = str(e)
                continue

        if data is None:
            return f"Erro ao consultar Overpass: {last_err or 'desconhecido'}"

        elementos = data.get("elements", [])
        if not elementos:
            # Plano B: repetir sem o filtro de area (as vezes o servidor falha no indice de area)
            query2 = f"""
            [out:json][timeout:60];
            relation
              ["boundary"="administrative"]
              ["admin_level"="8"]
              (around:50000,{lat},{lng});
            out tags center;
            """
            try:
                resp2 = requests.post(endpoints[0], data={"data": query2},
                                      headers=headers, timeout=45)
                if resp2.ok:
                    elementos = resp2.json().get("elements", [])
            except Exception:
                pass

        if not elementos:
            return []

        # Processa e deduplica
        cidades = []
        for el in elementos:
            tags = el.get("tags", {}) or {}
            nome = tags.get("name")
            centro = el.get("center") or {}
            clat, clng = centro.get("lat"), centro.get("lon")
            if not nome or clat is None or clng is None:
                continue

            #dist_km = round(self.haversine(lat, lng, clat, clng), 2)
            uf_sigla, uf_nome = self._extrai_uf(tags)
            # Tenta obter populacao a partir dos tags do Overpass (quando disponivel)
            pop_raw = (tags.get("population") or "").strip()
            try:
                populacao = int(''.join(ch for ch in pop_raw if ch.isdigit())) if pop_raw else 0
            except Exception:
                populacao = 0

            cidades.append({
                "nome": nome,
                "uf_sigla": uf_sigla,
                "uf_nome": uf_nome or estado,
                "populacao": populacao,
            })

            

        # Dedup por (nome, uf_sigla ou uf_nome)
        vistos = set()
        unicas = []
        # Ordena por maior populacao (quando conhecida); desempate por menor distancia
        for c in sorted(cidades, key=lambda x: (-(x.get("populacao", 0) or 0))):
            chave = (c["nome"], c["uf_sigla"] or c["uf_nome"])
            if chave not in vistos:
                vistos.add(chave)
                unicas.append(c)

        return unicas[:10]

    def haversine(self, lat1, lon1, lat2, lon2):
        R = 6371
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    def verificar_servicos_cidade(self, cidade, estado_sigla):
        """
        Busca servicos de saude mental na API do Mapa Saude Mental e retorna até 2 serviços
        """
        try:
            # Normalizar nome da cidade para URL
            cidade_normalizada = cidade.lower().replace(' ', '+').replace('ã', 'a').replace('á', 'a').replace('â', 'a').replace('à', 'a').replace('é', 'e').replace('ê', 'e').replace('í', 'i').replace('ó', 'o').replace('ô', 'o').replace('õ', 'o').replace('ú', 'u').replace('ç', 'c')
            
            url = "https://mapasaudemental.com.br/wp-json/latlng/v1/latlng-results"
            # Usar parâmetros já codificados para evitar dupla codificação
            url_with_params = f"{url}?formato=presencial&pagamento=&tipo=buscas-por-estados%2Cambulat%C3%B3rio+sa%C3%BAde+mental%2Caten%C3%A7%C3%A3o+b%C3%A1sica%2Ccaps%2Ccentro+de+refer%C3%AAncia%2Chospital%2Chospital+psiqui%C3%A1trico%2Cterceiro+setor%2Cupa%2Cservi%C3%A7o+escola%2Ccentro+de+especialidades%2Csocioassistencial%2Ctrabalhos+volunt%C3%A1rios&mapa=saude+mental%2Cdiversidade%2Ctecnologia%2Cmulher%2Cfavelas&estado={estado_sigla.lower()}&cidade={cidade_normalizada}&nocache={int(time.time() * 1000)}"
            
            headers = {
                "accept": "*/*",
                "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "referer": "https://mapasaudemental.com.br/sobre-o-mapa/",
                "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "priority": "u=1, i",
                "Cookie": "_ga=GA1.1.1033524720.1758152382; _ga_G2CXLE7Z8E=GS2.1.s1759174007$o5$g0$t1759174007$j60$l0$h0; visits=180564006"
            }
            
            response = requests.get(url_with_params, headers=headers, timeout=30)
            data = response.json()
            
            # Verifica se a resposta indica que nao ha servicos
            if data.get("status") == "error" and data.get("message") == "No locations found":
                return []
            
            # Extrai os serviços da resposta
            servicos = []
            if data.get("status") == "success" and "locations" in data and isinstance(data["locations"], list):
                for servico in data["locations"][:2]:  # Limita a 2 serviços
                    servico_info = {
                        "name": servico.get("name", ""),
                        "lat": servico.get("lat", ""),
                        "long": servico.get("long", ""),
                        "cidade": servico.get("cidade", ""),
                        "estado": servico.get("estado", ""),
                        "endereco": servico.get("endereco", ""),
                        "tipo": servico.get("tipo", ""),
                        "pagamento": servico.get("pagamento", ""),
                        "formato": servico.get("formato", ""),
                        "servico": servico.get("servico", ""),
                        "telefone1": servico.get("telefone1", ""),
                        "telefone2": servico.get("telefone2", ""),
                        "whatsapp": servico.get("whatsapp", ""),
                        "sigla": servico.get("sigla", ""),
                        "numero": servico.get("numero", ""),
                        "complemento": servico.get("complemento", ""),
                        "bairro": servico.get("bairro", "")
                    }
                    servicos.append(servico_info)
            
            return servicos
            
        except Exception as e:
            # Em caso de erro na consulta, retorna lista vazia
            return []

    def filtrar_cidades_com_servicos(self, cidades, user_coords=None, routes_api_key=None):
        """
        Filtra a lista de cidades retornando apenas aquelas que possuem servicos de saude mental
        e adiciona os serviços encontrados a cada cidade, incluindo distâncias se coordenadas do usuário fornecidas
        """
        cidades_com_servicos = []
        
        for i, cidade in enumerate(cidades):
            # Usar a sigla do estado se disponivel, senao usar o nome completo
            estado_para_consulta = cidade.get("uf_sigla") or cidade.get("uf_nome", "")
            
            if not estado_para_consulta:
                continue
                
            # Buscar serviços da cidade
            servicos = self.verificar_servicos_cidade(cidade["nome"], estado_para_consulta)
            if servicos:  # Se encontrou serviços
                # Criar estrutura simplificada da cidade
                cidade_com_servicos = {
                    "cidade": cidade["nome"]
                }
                
                # Se temos coordenadas do usuário e chave da API, calcular distâncias
                if user_coords and routes_api_key:
                    servicos_com_distancia = []
                    for servico in servicos:
                        # Calcular distância usando Google Routes API
                        distancia_info = self.calcular_distancia_servico(
                            user_coords["lat"], user_coords["lng"],
                            servico["lat"], servico["long"],
                            routes_api_key
                        )
                        
                        # Criar estrutura completa do serviço
                        servico_info = {
                            "name": servico["name"],
                            "lat": servico["lat"],
                            "long": servico["long"],
                            "cidade": servico["cidade"],
                            "estado": servico["estado"],
                            "endereco": servico["endereco"],
                            "tipo": servico["tipo"],
                            "pagamento": servico["pagamento"],
                            "formato": servico["formato"],
                            "servico": servico["servico"],
                            "telefone1": servico["telefone1"],
                            "telefone2": servico["telefone2"],
                            "whatsapp": servico["whatsapp"],
                            "sigla": servico["sigla"],
                            "numero": servico["numero"],
                            "complemento": servico["complemento"],
                            "bairro": servico["bairro"]
                        }
                        
                        # Adicionar informações de distância se disponível
                        if distancia_info:
                            servico_info["distancia"] = distancia_info["distance_text"]
                            servico_info["tempo_viagem"] = distancia_info["duration_text"]
                        
                        servicos_com_distancia.append(servico_info)
                    
                    cidade_com_servicos["servicos"] = servicos_com_distancia
                else:
                    # Sem distâncias, retornar estrutura completa
                    servicos_completos = []
                    for servico in servicos:
                        servico_info = {
                            "name": servico["name"],
                            "lat": servico["lat"],
                            "long": servico["long"],
                            "cidade": servico["cidade"],
                            "estado": servico["estado"],
                            "endereco": servico["endereco"],
                            "tipo": servico["tipo"],
                            "pagamento": servico["pagamento"],
                            "formato": servico["formato"],
                            "servico": servico["servico"],
                            "telefone1": servico["telefone1"],
                            "telefone2": servico["telefone2"],
                            "whatsapp": servico["whatsapp"],
                            "sigla": servico["sigla"],
                            "numero": servico["numero"],
                            "complemento": servico["complemento"],
                            "bairro": servico["bairro"]
                        }
                        servicos_completos.append(servico_info)
                    
                    cidade_com_servicos["servicos"] = servicos_completos
                
                cidades_com_servicos.append(cidade_com_servicos)
        
        return cidades_com_servicos

    def calcular_distancia_servico(self, origin_lat, origin_lng, dest_lat, dest_lng, api_key):
        """
        Calcula a distancia de carro usando a Google Maps Routes API
        """
        url = "https://routes.googleapis.com/directions/v2:computeRoutes"
        
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
        }
        
        payload = {
            "origin": {
                "location": {
                    "latLng": {
                        "latitude": float(origin_lat),
                        "longitude": float(origin_lng)
                    }
                }
            },
            "destination": {
                "location": {
                    "latLng": {
                        "latitude": float(dest_lat),
                        "longitude": float(dest_lng)
                    }
                }
            },
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "computeAlternativeRoutes": False,
            "routeModifiers": {
                "avoidTolls": False,
                "avoidHighways": False,
                "avoidFerries": False
            },
            "languageCode": "pt-BR",
            "units": "METRIC"
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                if "routes" in data and len(data["routes"]) > 0:
                    route = data["routes"][0]
                    
                    distance_meters = route.get("distanceMeters", 0)
                    duration_seconds = route.get("duration", "0s")
                    
                    # Converter distancia para texto legivel
                    if distance_meters >= 1000:
                        distance_text = f"{distance_meters/1000:.1f} km"
                    else:
                        distance_text = f"{distance_meters} metros"
                    
                    # Converter duracao para texto legivel
                    if isinstance(duration_seconds, str):
                        # Se já está em formato string, extrair apenas o valor numérico
                        import re
                        match = re.search(r'(\d+)', duration_seconds)
                        if match:
                            duration_seconds = int(match.group(1))
                        else:
                            duration_seconds = 0
                    
                    # Converter segundos para minutos
                    minutes = duration_seconds // 60
                    if minutes >= 60:
                        hours = minutes // 60
                        remaining_minutes = minutes % 60
                        duration_text = f"{hours}h {remaining_minutes}min" if remaining_minutes > 0 else f"{hours}h"
                    else:
                        duration_text = f"{minutes}min"
                    
                    return {
                        "distance_meters": distance_meters,
                        "distance_text": distance_text,
                        "duration_text": duration_text
                    }
                else:
                    return None
            
            elif response.status_code == 400:
                print(f"Erro na requisicao: Parametros invalidos")
                return None
            
            elif response.status_code == 403:
                print(f"Erro na requisicao: Chave da API invalida ou sem permissoes")
                return None
            
            elif response.status_code == 429:
                print(f"Erro na requisicao: Limite de requisicoes excedido")
                return None
            
            else:
                print(f"Erro na requisicao: HTTP {response.status_code}")
                return None
        
        except requests.exceptions.Timeout:
            print(f"Timeout ao calcular distancia")
            return None
        
        except requests.exceptions.RequestException as e:
            print(f"Erro de conexao ao calcular distancia: {str(e)}")
            return None
        
        except Exception as e:
            print(f"Erro inesperado ao calcular distancia: {str(e)}")
            return None

    def _extrai_uf(self, tags):
        iso = tags.get("ISO3166-2")
        if iso and iso.startswith("BR-") and len(iso) == 5:
            return iso[-2:], None  # (sigla, nome=None)
        # fallback: nomes
        uf_nome = tags.get("addr:state") or tags.get("is_in:state")
        return None, uf_nome
