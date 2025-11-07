from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode


class GetMentalHealthServices(Tool):
    BASE_URL = "https://mapasaudemental.com.br/wp-json/latlng/v1/latlng-results"
    HEADERS = {
        'Accept': '*/*',
        'User-Agent': 'Vita Alere Assistant/1.0',
        'Content-Type': 'application/json'
    }
    
    # Campos conforme a API atual (minúsculos) + coordenadas
    FIELDS_TO_KEEP = [
        'name', 'lat', 'long', 'cidade', 'estado', 'endereco', 'tipo',
        'pagamento', 'formato', 'telefone1', 'telefone2',
        'whatsapp', 'sigla', 'numero', 'complemento', 'bairro',
        'site', 'instagram', 'facebook', 'email', 'youtube'
    ]

    def execute(self, context: Context) -> TextResponse:
        
        #urn = context.contact.get("urn","")
        
        # Obtém parâmetros do contexto (estado/cidade podem ser derivados de CEP)
        estado = context.parameters.get("estado")
        cidade_param = context.parameters.get("cidade")
        cep_param = context.parameters.get("cep")

        # Se CEP for informado, usa ViaCEP para preencher cidade e estado
        if cep_param and (not estado or not cidade_param):
            try:
                cep_digits = "".join([c for c in str(cep_param) if c.isdigit()])

                viacep_url = f"https://viacep.com.br/ws/{cep_digits}/json/"
                viacep_resp = requests.get(viacep_url, timeout=8)
                if viacep_resp.status_code >= 400:
                    return TextResponse(data={
                        "status": "error",
                        "message": "Não foi possível consultar o ViaCEP no momento.",
                    })
                viacep_json = viacep_resp.json()
                if viacep_json.get("erro"):
                    return TextResponse(data={
                        "status": "error",
                        "message": "CEP não encontrado no ViaCEP. Verifique e tente novamente."
                    })
                # Preenche apenas o que estiver faltando
                if not cidade_param:
                    cidade_param = viacep_json.get("localidade")
                if not estado:
                    estado = viacep_json.get("uf")
            except Exception:
                return TextResponse(data={
                    "status": "error",
                    "message": "Erro ao consultar o ViaCEP. Tente novamente mais tarde."
                })

        # Valida obrigatórios após possível preenchimento por CEP
        if not estado:
            return TextResponse(data={"status": "error", "message": "O parâmetro 'estado' é obrigatório (ou informe um CEP válido)"})
            
        if not cidade_param:
            return TextResponse(data={"status": "error", "message": "O parâmetro 'cidade' é obrigatório (ou informe um CEP válido)"})

        # Permite múltiplas cidades separadas por vírgula
        cidades = [c.strip() for c in str(cidade_param).split(',') if str(c).strip()]
        if not cidades:
            return TextResponse(data={"status": "error", "message": "O parâmetro 'cidade' não pode ser vazio"})

        # Get optional parameters
        formato = context.parameters.get("formato", "")
        tipo = context.parameters.get("tipo", "")
        
        # Debug opcional: use 'cidades' (lista) em vez de 'cidade' fora do loop
        # print(estado, cidades, formato, pagamento, tipo)
        

        try:
            # Consulta cada cidade e agrega resultados
            all_locations = []
            raw_results = []
            for cidade in cidades:
                response = self.get_mental_health_services(
                    estado=estado,
                    cidade=cidade,
                    formato=formato,
                    pagamento="",
                    tipo=tipo
                )

                if not response:
                    continue

                if isinstance(response, dict) and isinstance(response.get('locations'), list):
                    all_locations.extend(response.get('locations', []))
                else:
                    raw_results.append({"cidade": cidade, "response": response})

            if all_locations:
                return TextResponse(data={"status": "success", "action": "com a lista de servicos de saude mental, utilize a tool calcular_distancias_carro para calcular a distancia e tempo de viagem entre a localizacao do usuario e os servicos de saude mental passando a lista de servicos de saude mental no formato: [{name=<servico1> Nome do estabelecimento, lat=xxxxxxx, lng=xxxxxxx}", "locations": all_locations})
            if raw_results:
                return TextResponse(data={"status": "multi", "results": raw_results, "message": "Vamos utilizar o agente de localização para buscar as cidades próximas e os serviços de saúde mental nessas cidades."})
            return TextResponse(data={"status": "false", "locations": [], "message": "Vamos utilizar o agente de localização para buscar as cidades próximas e os serviços de saúde mental nessas cidades."})                    
            
        except Exception as e:
            return TextResponse(data={
                "status": "error",
                "message": f"Erro ao consultar o Mapa da Saúde Mental: {str(e)}"
            })

    def filter_service_fields(self, service: Dict[str, Any]) -> Dict[str, Any]:
        """Filter only required fields with case-insensitive key matching."""
        try:
            if not isinstance(service, dict):
                return {}
            # Mapa case-insensitive das chaves retornadas pela API
            lower_key_to_value = {str(k).lower(): v for k, v in service.items()}
            filtered: Dict[str, Any] = {}
            for field in self.FIELDS_TO_KEEP:
                filtered[field] = lower_key_to_value.get(field, '')
            return filtered
        except Exception:
            return {}

    def get_mental_health_services(
        self,
        estado: str,
        cidade: str,
        formato: Optional[str] = None,
        pagamento: Optional[str] = "",
        tipo: Optional[str] = None,
        
    ) -> Dict[str, Any]:
        # Build query parameters
        params = {
            'estado': estado,
            'cidade': cidade
        }
        if formato:
            params['formato'] = str(formato).lower()
        if pagamento:
            params['pagamento'] = ""
        if tipo:
            try:
                # Processa lista separada por vírgula mantendo o formato original
                processed_tipo = ",".join([
                    part.strip()
                    for part in str(tipo).split(",") if part.strip()
                ])
                if processed_tipo:
                    params['tipo'] = processed_tipo
            except Exception:
                # Se houver erro no processamento, usar o valor original
                params['tipo'] = str(tipo)

        # Build URL with parameters
        url = f"{self.BASE_URL}"
        if params:
            url = f"{url}?{urlencode(params)}"

        try:
            # Make request with headers
            response = requests.get(url, headers=self.HEADERS, timeout=10)

            if response.status_code >= 400:
                # Tenta extrair payload de erro do WP REST
                try:
                    err = response.json()
                except ValueError:
                    err = None
                if isinstance(err, dict) and err.get('code') and err.get('message'):
                    return {
                        "status": "error",
                        "http_status": response.status_code,
                        "code": err.get('code'),
                        "message": err.get('message'),
                        "url": url,
                    }
                return {
                    "status": "error",
                    "http_status": response.status_code,
                    "message": f"Erro HTTP {response.status_code} ao consultar a API",
                    "url": url,
                }
            try:
                api_response = response.json()
                if 'locations' in api_response:
                    filtered_locations = []
                    for location in api_response['locations']:
                        filtered_location = {
                            'name': location.get('name', ''),
                            'lat': location.get('lat', ''),
                            'long': location.get('long', ''),
                            'cidade': cidade,
                            'estado': estado,
                            'endereco': location.get('endereco', ''),
                            'tipo': location.get('tipo', ''),
                            'pagamento': location.get('pagamento', ''),
                            'formato': location.get('formato', ''),
                            'telefone1': location.get('telefone1', ''),
                            'telefone2': location.get('telefone2', ''),
                            'whatsapp': location.get('whatsapp', ''),
                            'site': location.get('site', ''),
                            'instagram': location.get('instagram', ''),
                            'facebook': location.get('facebook', ''),
                            'email': location.get('email', ''),
                            'youtube': location.get('youtube', ''),
                            'sigla': location.get('sigla', ''),
                            'numero': location.get('numero', ''),
                            'complemento': location.get('complemento', ''),
                            'bairro': location.get('bairro', '')
                        }
                        filtered_locations.append(filtered_location)
                    
                    return {
                        "status": "success",
                        "action": "com a lista de servicos de saude mental, utilize o agente location analyzer e a tool calcular_distancias_carro para calcular a distancia e tempo de viagem entre a localizacao do usuario e os servicos de saude mental passando a lista de servicos de saude mental no formato: [{name=<servico1> Nome do estabelecimento, lat=xxxxxxx, lng=xxxxxxx}",
                        "locations": filtered_locations
                        }
                else:
                    return api_response
            except ValueError:
                return {
                    "status": "error",
                    "message": "Resposta inválida da API (não é JSON válido)",
                    "url": url,
                }
        
        except requests.exceptions.Timeout:
            return {
                "status": "error",
                "message": "Tempo limite excedido ao consultar a API",
                "url": url,
            }
        except requests.exceptions.RequestException as e:
            resp = getattr(e, 'response', None)
            if resp is not None:
                try:
                    err = resp.json()
                except ValueError:
                    err = None
                if isinstance(err, dict) and err.get('code') and err.get('message'):
                    return {
                        "status": "error",
                        "http_status": resp.status_code,
                        "code": err.get('code'),
                        "message": err.get('message'),
                        "url": url,
                    }
                return {
                    "status": "error",
                    "http_status": resp.status_code if hasattr(resp, 'status_code') else None,
                    "message": f"Erro ao consultar a API: {str(e)}",
                    "url": url,
                }
            return {
                "status": "error",
                "message": f"Erro na requisição: {str(e)}",
                "url": url,
            } 