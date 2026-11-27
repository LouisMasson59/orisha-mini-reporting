"""Client minimal des API Openbravo : ExportService (lecture) et ImportService (création, asynchrone).

Variables d'environnement :
  OB_URL       hôte Openbravo, ex. monenv.openbravo.com/openbravo (sans https://)
  OB_USER      utilisateur API (authentification basique)
  OB_PASSWORD  mot de passe
  OB_MOCK=1    utilise l'API simulée (aucun appel réseau)
"""
import base64
import json
import os
import re
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
        self.root = f"https://{host}/ws/"
        self.base = self.root + "org.openbravo.api.ExportService/"
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
            if isinstance(data, dict):  # data.records (listes) ou un objet unique (recherches bySearchKey, byId)
                data = (data.get("records") or []) if "records" in data else [data]
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

    # ------------------------------------------------------------------ import (écriture)
    def post_import(self, entity: str, records: list) -> dict:
        """POST ImportService/<entity>. Réponse brute (request id) ou {"errors": [...]} sans interrompre le programme."""
        if self.mock:
            self.calls += 1
            return self._mock.post_import(entity, records)
        body = json.dumps(records, ensure_ascii=False).encode("utf-8")
        return self._request(self.root + "org.openbravo.api.ImportService/" + entity, data=body, soft=True)

    def status(self, request_id: str) -> dict:
        if self.mock:
            self.calls += 1
            return self._mock.status(request_id)
        return self._request(self.root + "org.openbravo.api.StatusService?id=" + urllib.parse.quote(request_id), soft=True)

    def import_and_wait(self, entity: str, records: list, timeout: int = 240) -> dict:
        """Import asynchrone puis suivi du StatusService jusqu'au résultat.
        Renvoie {"etat": "ok" | "erreur" | "en_cours", "request": id, "reponse": dernière réponse brute}."""
        resp = self.post_import(entity, records)
        rid = (((resp.get("data") or {}).get("request") or {}).get("id")) if isinstance(resp.get("data"), dict) else None
        if not rid:
            return {"etat": "erreur", "request": None, "reponse": resp}
        t0, last = time.time(), None
        while time.time() - t0 < timeout:
            if not self.mock:
                time.sleep(4)
            last = self.status(rid)
            etat = etat_import(last)
            if etat != "en_cours":
                return {"etat": etat, "request": rid, "reponse": last}
        return {"etat": "en_cours", "request": rid, "reponse": last}

    def _request(self, url: str, data: bytes | None = None, soft: bool = False) -> dict:
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data is not None else {}))
        for attempt in range(4):
            try:
                self.calls += 1
                req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                if soft and (e.code < 500 or attempt == 3):
                    raw = e.read()[:2000].decode("utf-8", "replace")
                    try:
                        return json.loads(raw)
                    except ValueError:
                        return {"errors": [{"status": e.code, "detail": raw}]}
                if data is not None and e.code >= 500:
                    raw = e.read()[:2000].decode("utf-8", "replace")
                    return {"errors": [{"status": e.code, "detail": raw}]}  # pas de nouvel essai : l'import a pu être pris en compte
                if e.code < 500 or attempt == 3:
                    raise SystemExit(f"Erreur HTTP {e.code} sur {url}\n{e.read()[:500]!r}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 3:
                    raise SystemExit(f"Openbravo injoignable ({e}) : {url}")
            time.sleep(5 * (attempt + 1))
        return {}


_DONE_OK = ("done", "completed", "complete", "processed", "finished", "ok")


def _statuses(x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            if k.lower() in ("status", "state", "result", "importstatus") and isinstance(v, str):
                out.append(v.lower())
            _statuses(v, out)
    elif isinstance(x, list):
        for v in x:
            _statuses(v, out)
    return out


def etat_import(body: dict | None) -> str:
    """Interprète une réponse du StatusService (format tolérant : on cherche les champs status/state)."""
    if not body:
        return "en_cours"
    if body.get("errors"):
        return "erreur"
    toks = re.findall(r"[a-z]+", " ".join(_statuses(body, [])))
    if any(t.startswith(("error", "fail")) or t in ("ko", "rejected") for t in toks):
        return "erreur"
    if any(t.startswith("success") or t in _DONE_OK for t in toks):
        return "ok"
    return "en_cours"
