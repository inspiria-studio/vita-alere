from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode


class GetMentalHealthServices(Tool):
    BASE_URL = "https://mapasaudemental.com.br/wp-json/latlngplugin/v1/latlng-results"
    HEADERS = {
        'Accept': '*/*',
        'User-Agent': 'Vita Alere Assistant/1.0',
        'Content-Type': 'application/json'
    }
    
    FIELDS_TO_KEEP = [
        'Id', 'Tipo', 'Pagamento', 'Formato', 'Nome', 'Endereco', 'Numero',
        'Complemento', 'Bairro', 'Cidade', 'Cep', 'Observacao'
    ]

    def execute(self, context: Context) -> TextResponse:
        # Get required parameters from context
        estado = context.parameters.get("estado")
        if not estado:
            raise ValueError("O parâmetro 'estado' é obrigatório")
            
        cidade = context.parameters.get("cidade")
        if not cidade:
            raise ValueError("O parâmetro 'cidade' é obrigatório")

        # Get optional parameters
        formato = context.parameters.get("formato", "")
        pagamento = context.parameters.get("pagamento", "")
        tipo = context.parameters.get("tipo", "")

        try:
            # Get services from API
            response = self.get_mental_health_services(
                estado=estado,
                cidade=cidade,
                formato=formato,
                pagamento=pagamento,
                tipo=tipo
            )
            
            if not response:
                return TextResponse(data={
                    "status": "error",
                    "message": "Nenhum serviço encontrado com os parâmetros fornecidos"
                })

            # If it's an error response, return it as is
            if isinstance(response, dict) and response.get('status') == 'error':
                return TextResponse(data=response)

            # If we have locations in the response, filter them
            if isinstance(response, dict) and 'locations' in response:
                filtered_locations = [self.filter_service_fields(service) for service in response['locations']]
                return TextResponse(data={"status": "success", "locations": filtered_locations})
            
            # If it's a list, filter each service
            if isinstance(response, list):
                filtered_services = [self.filter_service_fields(service) for service in response]
                return TextResponse(data={"status": "success", "locations": filtered_services})
            
            # If none of the above, return the original response
            return TextResponse(data=response)
            
        except Exception as e:
            return TextResponse(data={
                "status": "error",
                "message": f"Erro ao consultar o Mapa da Saúde Mental: {str(e)}"
            })

    def filter_service_fields(self, service: Dict[str, Any]) -> Dict[str, Any]:
        """Filter only the required fields from a service entry."""
        return {field: service.get(field, '') for field in self.FIELDS_TO_KEEP if field in service}

    def get_mental_health_services(
        self,
        estado: str,
        cidade: str,
        formato: Optional[str] = None,
        pagamento: Optional[str] = None,
        tipo: Optional[str] = None
    ) -> Dict[str, Any]:
        # Build query parameters
        params = {
            'estado': estado.upper(),
            'cidade': cidade.title()
        }
        if formato:
            params['formato'] = formato.lower()
        if pagamento:
            params['pagamento'] = pagamento.lower()
        if tipo:
            params['tipo'] = tipo.upper()

        # Build URL with parameters
        url = f"{self.BASE_URL}"
        if params:
            url = f"{url}?{urlencode(params)}"

        try:
            # Make request with headers
            response = requests.get(url, headers=self.HEADERS, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise ValueError("Tempo limite excedido ao consultar a API")
        except requests.exceptions.RequestException as e:
            if response.status_code == 404:
                return {"status": "error", "message": "Nenhum serviço encontrado"}
            raise ValueError(f"Erro na requisição: {str(e)}") 