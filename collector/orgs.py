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


def decouvrir(client, now_iso: str) -> dict:
    """Liste des organisations Openbravo pour l'onglet Administration (docs/data/ref/organisations.json).
    Un magasin = une organisation avec un entrepôt de stock (onHandWarehouses) ou un client anonyme de caisse."""
    from .store import CONFIG, save_json
    out = []
    for o in client.get("Organization"):
        whs = sorted((w for w in o.get("onHandWarehouses") or [] if isinstance(w, dict) and w.get("active", True) is not False),
                     key=lambda w: w.get("priority") or 0)
        parent = o.get("parent")
        out.append({"code": str(o.get("searchKey") or o.get("id")), "nom": o.get("name"),
                    "entrepots": [w.get("warehouse") for w in whs if w.get("warehouse")],
                    "parent": parent.get("name") if isinstance(parent, dict) else parent,
                    "magasin": bool(whs or o.get("anonymousCustomer") or o.get("lastTouchPointStoreOpening"))})
    out.sort(key=lambda x: (not x["magasin"], x["code"]))
    # configuration en vigueur (codes des historiques existants, libellés, entrepôts) : point de départ de l'écran
    actuelle = {}
    for s in CONFIG["stores"]:
        actuelle[str(s.get("search_key", s["code"]))] = {"code": s["code"], "label": s["label"], "warehouse": s["warehouse"], "type": s.get("type", "Magasin"),
                                                         "ventes": s.get("ventes", True), "stocks": s.get("stocks", True), "reappro": False}
    for m in (CONFIG.get("reappro") or {}).get("magasins", []):
        e = actuelle.setdefault(str(m.get("search_key", m["code"])), {"code": m["code"], "label": m.get("label", m["code"]), "warehouse": m["warehouse"],
                                                                       "type": "Magasin", "ventes": False, "stocks": False})
        e.update(reappro=True, prefixe_commande=m.get("prefixe_commande"))
    res = {"maj": now_iso, "source": client.source, "organisations": out, "config_actuelle": actuelle}
    save_json("ref/organisations.json", res)
    return res
