"""Client minimal des API Openbravo ExportService (Master Data + Transactional).

Variables d'environnement :
  OB_URL       hôte Openbravo, ex. monenv.openbravo.com/openbravo (sans https://)
  OB_USER      utilisateur API (authentification basique)
  OB_PASSWORD  mot de passe
  OB_MOCK=1    utilise l'API simulée (aucun appel réseau)
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .store import CONFIG


class OBClient:
    def __init__(self):
        self.mock = os.environ.get("OB_MOCK", "0") == "1"
        self.page_size = int(CONFIG.get("page_size", 100))
        self.calls = 0
        if self.mock:
            from .mock_api import MockAPI
            self._mock = MockAPI()
            return
        host = os.environ.get("OB_URL", "").replace("https://", "").replace("http://", "").rstrip("/")
        user, pwd = os.environ.get("OB_USER"), os.environ.get("OB_PASSWORD")
        if not (host and user and pwd):
            raise SystemExit("OB_URL, OB_USER et OB_PASSWORD doivent être définis (ou OB_MOCK=1).")
        self.base = f"https://{host}/ws/org.openbravo.api.ExportService/"
        token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    @property
    def source(self) -> str:
        return "simulation" if self.mock else "api"

    def get(self, path: str, params: dict | None = None) -> list:
        """Récupère toutes les pages d'un endpoint ExportService."""
        params = dict(params or {})
        if self.mock:
            self.calls += 1
            return self._mock.get(path, params)
        out, page = [], 1
        while page <= 1000:
            q = dict(params, _page=page, _pageSize=self.page_size)
            body = self._request(self.base + path + "?" + urllib.parse.urlencode(q))
            data = body.get("data") or []
            if isinstance(data, dict):  # certains services renvoient data.records
                data = data.get("records") or []
            out.extend(data)
            has_next = bool((body.get("links") or {}).get("next"))
            if not data or (not has_next and len(data) < self.page_size):
                break
            page += 1
        return out

    def get_page(self, path: str, params: dict | None = None, size: int = 20) -> dict:
        """Une seule page, réponse brute (pour le diagnostic)."""
        if self.mock:
            self.calls += 1
            return {"data": self._mock.get(path, dict(params or {}))[:size]}
        q = dict(params or {}, _page=1, _pageSize=size)
        return self._request(self.base + path + ("?" + urllib.parse.urlencode(q)))

    def _request(self, url: str) -> dict:
        for attempt in range(4):
            try:
                self.calls += 1
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == 3:
                    raise SystemExit(f"Erreur HTTP {e.code} sur {url}\n{e.read()[:500]!r}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 3:
                    raise SystemExit(f"Openbravo injoignable ({e}) : {url}")
            time.sleep(5 * (attempt + 1))
        return {}
