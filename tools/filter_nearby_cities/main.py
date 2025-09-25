from weni import Tool
from weni.context import Context
from weni.responses import TextResponse
import requests
import re
import math


class FilterNearbyCities(Tool):
    def execute(self, context: Context) -> TextResponse:
        cep = context.parameters.get("cep", "")
        places_key = context.credentials.get("places_apikey", "")

        if not cep:
            return TextResponse(data="CEP não fornecido.")
        if not places_key:
            return TextResponse(data="Chave da API do Google Maps não fornecida.")

        cep = re.sub(r'\D', '', cep)
        coords, estado = self.get_coordinates_by_cep(cep, places_key)
        if not coords:
            return TextResponse(data="Não foi possível obter coordenadas a partir do CEP.")

        lat = coords["lat"]
        lng = coords["lng"]

        cidades = self.buscar_cidades_por_overpass(lat, lng, estado)
        if isinstance(cidades, str):
            return TextResponse(data=cidades)
        elif not cidades:
            return TextResponse(data="Nenhuma cidade encontrada em até 50 km.")

        return TextResponse(data={
            "status": "success",
            "cidades_proximas": cidades
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

        # Query: restringe ao Brasil por área e busca relations admin_level 8 num raio
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
            # Plano B: repetir sem o filtro de área (às vezes o servidor falha no índice de área)
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

            dist_km = round(self.haversine(lat, lng, clat, clng), 2)
            uf_sigla, uf_nome = self._extrai_uf(tags)
            # Tenta obter populacao a partir dos tags do Overpass (quando disponivel)
            pop_raw = (tags.get("population") or "").strip()
            try:
                populacao = int(''.join(ch for ch in pop_raw if ch.isdigit())) if pop_raw else 0
            except Exception:
                populacao = 0

            """ cidades.append({
                "nome": nome,
                "uf_sigla": uf_sigla,     # pode ser None
                "uf_nome": uf_nome or estado,       # pode ser None
                "lat": clat,
                "lng": clng,
                "distancia_km": dist_km,
                "osm_type": el.get("type"),
                "osm_id": el.get("id"),
                "admin_level": tags.get("admin_level", ""),
            }) """

            cidades.append({
                "nome": nome,
                "uf_sigla": uf_sigla,
                "uf_nome": uf_nome or estado,
                "distancia_km": dist_km,
                "populacao": populacao,
            })

            



        # Dedup por (nome, uf_sigla ou uf_nome)
        vistos = set()
        unicas = []
        # Ordena por maior populacao (quando conhecida); desempate por menor distancia
        for c in sorted(cidades, key=lambda x: (-(x.get("populacao", 0) or 0), x["distancia_km"])):
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

    def _extrai_uf(self, tags):
        iso = tags.get("ISO3166-2")
        if iso and iso.startswith("BR-") and len(iso) == 5:
            return iso[-2:], None  # (sigla, nome=None)
        # fallback: nomes
        uf_nome = tags.get("addr:state") or tags.get("is_in:state")
        return None, uf_nome
