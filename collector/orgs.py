"""Résolution des noms d'organisation : le filtre `organization` des API attend le NOM exact.
On le retrouve à partir du search key du magasin (config `search_key`), avec repli sur `api_name`."""
from .store import CONFIG

_cache = {}


def resolve(client) -> dict:
    """{code_magasin: nom exact de l'organisation}"""
    if "stores" in _cache:
        return _cache["stores"]
    by_sk = {}
    try:
        by_sk = {str(o.get("searchKey")): o.get("name") for o in client.get("Organization")}
    except SystemExit:
        pass
    res = {st["code"]: by_sk.get(str(st.get("search_key", st["code"]))) or st.get("api_name") for st in CONFIG["stores"]}
    _cache["stores"] = res
    return res


def purchase_orgs(client) -> list:
    names = []
    for o in CONFIG["purchase_orgs"]:
        names += list(resolve(client).values()) if o == "@magasins" else [o]
    return list(dict.fromkeys(names))
