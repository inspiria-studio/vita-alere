from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
import json
import re


class CalculateDrivingDistance(Tool):
    def execute(self, context: Context) -> TextResponse:
        # Obter parametros
        establishments_raw = context.parameters.get("establishments", [])
        api_key = context.credentials.get("test_apikey", "")
        cep = context.parameters.get("cep", "")
        places_key = context.credentials.get("places_apikey", "")
        
        # Processar establishments se for string
        if isinstance(establishments_raw, str):
            establishments = self.parse_establishments_string(establishments_raw)
            if isinstance(establishments, str):  # Se retornou string, é erro
                return TextResponse(data=establishments)
        else:
            establishments = establishments_raw
        
        if not establishments:
            return TextResponse(data="Lista de estabelecimentos e obrigatoria.")
        
        if not api_key:
            return TextResponse(data="Chave da API do Google Maps nao fornecida.")

        if not cep:
            return TextResponse(data="CEP não fornecido.")

        cep = re.sub(r'\D', '', cep)
        coords = self.get_coordinates_by_cep(cep, places_key)
        if not coords:
            return TextResponse(data="Nao foi possivel obter coordenadas a partir do CEP.")

        lat = coords["lat"]
        lng = coords["lng"]

        try:
            user_lat = float(lat)
            user_lng = float(lng)
        except (ValueError, TypeError):
            return TextResponse(data="Coordenadas do usuario devem ser numeros validos.")

        # Validar formato dos estabelecimentos
        if not isinstance(establishments, list):
            return TextResponse(data="Estabelecimentos deve ser uma lista.")
        
        if len(establishments) == 0:
            return TextResponse(data="Lista de estabelecimentos nao pode estar vazia.")

        # Processar cada estabelecimento
        results = []
        for i, establishment in enumerate(establishments):
            if not isinstance(establishment, dict):
                return TextResponse(data=f"Estabelecimento {i+1} deve ser um objeto com 'lat', 'lng' e 'name'.")
            
            if "lat" not in establishment or "lng" not in establishment:
                return TextResponse(data=f"Estabelecimento {i+1} deve ter 'lat' e 'lng'.")
            
            if "name" not in establishment:
                return TextResponse(data=f"Estabelecimento {i+1} deve ter 'name'.")
            
            try:
                est_lat = float(establishment["lat"])
                est_lng = float(establishment["lng"])
            except (ValueError, TypeError):
                return TextResponse(data=f"Coordenadas do estabelecimento {i+1} devem ser numeros validos.")

            # Calcular distancia usando Google Maps API
            distance_result = self.calculate_distance(
                user_lat, user_lng, est_lat, est_lng, establishment["name"], api_key
            )
            
            if isinstance(distance_result, str):
                return TextResponse(data=distance_result)
            
            results.append(distance_result)

        # Ordenar por distancia
        results.sort(key=lambda x: x["distance_meters"])
        
        # Formatar resposta
        response_text = "Distancias de carro para os estabelecimentos:\n\n"
        
        for result in results:
            response_text += f"- {result['name']}\n"
            response_text += f"   Distancia: {result['distance_text']}\n"
            response_text += f"   Tempo estimado: {result['duration_text']}\n\n"

        return TextResponse(data=response_text)

    def parse_establishments_string(self, establishments_str):
        """
        Faz parsing manual da string de establishments que vem no formato:
        [{name=CAPS Conselheiro Lafaiete, lat=-20.672687, lng=-43.80858}, ...]
        """
        try:
            # Primeiro tenta JSON normal
            return json.loads(establishments_str)
        except json.JSONDecodeError:
            pass
        
        try:
            # Remove colchetes externos
            content = establishments_str.strip()
            if content.startswith('[') and content.endswith(']'):
                content = content[1:-1]
            
            establishments = []
            # Divide por }, { para separar os objetos
            parts = content.split('}, {')
            
            for i, part in enumerate(parts):
                # Limpa chaves extras
                if i == 0:
                    part = part.strip()
                else:
                    part = '{' + part.strip()
                
                if i == len(parts) - 1:
                    part = part.strip()
                else:
                    part = part + '}'
                
                # Converte formato name=valor para "name":"valor"
                part = part.replace('name=', '"name":"')
                part = part.replace(', lat=', '", "lat":')
                part = part.replace(', lng=', ', "lng":')
                
                # Adiciona aspas nas chaves se não tiver
                if not part.startswith('{'):
                    part = '{' + part
                if not part.endswith('}'):
                    part = part + '}'
                
                # Parse do objeto individual
                establishment = json.loads(part)
                establishments.append(establishment)
            
            return establishments
            
        except Exception as e:
            return f"Erro ao processar establishments: {str(e)}. Formato esperado: lista de objetos com name, lat, lng"

    def calculate_distance(self, origin_lat, origin_lng, dest_lat, dest_lng, establishment_name, api_key):
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
                        "latitude": origin_lat,
                        "longitude": origin_lng
                    }
                }
            },
            "destination": {
                "location": {
                    "latLng": {
                        "latitude": dest_lat,
                        "longitude": dest_lng
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
                        duration_text = duration_seconds
                    else:
                        # Converter segundos para minutos
                        minutes = duration_seconds // 60
                        if minutes >= 60:
                            hours = minutes // 60
                            remaining_minutes = minutes % 60
                            duration_text = f"{hours}h {remaining_minutes}min" if remaining_minutes > 0 else f"{hours}h"
                        else:
                            duration_text = f"{minutes}min"
                    
                    return {
                        "name": establishment_name,
                        "distance_meters": distance_meters,
                        "distance_text": distance_text,
                        "duration_text": duration_text,
                        "lat": dest_lat,
                        "lng": dest_lng
                    }
                else:
                    return f"Rota nao encontrada para {establishment_name}"
            
            elif response.status_code == 400:
                return f"Erro na requisicao para {establishment_name}: Parametros invalidos"
            
            elif response.status_code == 403:
                return f"Erro na requisicao para {establishment_name}: Chave da API invalida ou sem permissoes"
            
            elif response.status_code == 429:
                return f"Erro na requisicao para {establishment_name}: Limite de requisicoes excedido"
            
            else:
                return f"Erro na requisicao para {establishment_name}: HTTP {response.status_code}"
        
        except requests.exceptions.Timeout:
            return f"Timeout ao calcular distancia para {establishment_name}"
        
        except requests.exceptions.RequestException as e:
            return f"Erro de conexao ao calcular distancia para {establishment_name}: {str(e)}"
        
        except Exception as e:
            return f"Erro inesperado ao calcular distancia para {establishment_name}: {str(e)}"

    def get_coordinates_by_cep(self, cep, api_key):
        try:
            via_url = f"https://viacep.com.br/ws/{cep}/json/"
            print(f"[CalculateDrivingDistance][CEP] normalized={cep} url={via_url}")
            response = requests.get(via_url, timeout=10)
            data = response.json()
            print(f"[ViaCEP] status={response.status_code} data={data}")
            if "erro" in data:
                print("[ViaCEP] resposta indicou erro para o CEP informado")
                return None

            cidade = data.get("localidade", "")
            estado = data.get("uf", "")
            print(f"[ViaCEP] cidade={cidade} estado={estado}")
            if not cidade or not estado:
                print("[ViaCEP] cidade/estado ausentes no retorno")
                return None

            query = f"{cidade}, {estado}, Brasil"
            geo_url = "https://maps.googleapis.com/maps/api/geocode/json"
            print(f"[Geocode] query='{query}' endpoint={geo_url}")
            geo_response = requests.get(geo_url, params={"address": query, "key": api_key}, timeout=10)
            geo_data = geo_response.json()
            print(f"[Geocode] http_status={geo_response.status_code} api_status={geo_data.get('status')}")
            if geo_data.get("status") == "OK":
                location = geo_data["results"][0]["geometry"]["location"]
                print(f"[Geocode] location={location}")
                return {"lat": location["lat"], "lng": location["lng"]}
            else:
                print(f"[Geocode] falha. status={geo_data.get('status')} mensagem={geo_data.get('error_message')}")
                return None
        except Exception:
            import traceback as _traceback
            print(f"[get_coordinates_by_cep][Exception] {repr(_traceback.format_exc())}")
            return None
