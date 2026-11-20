"""Référentiel : produits, catégories (→ secteur racine) et coûts. Rafraîchi chaque nuit."""
from .store import CONFIG, load_json, num, save_json, now_local


def _fr(x: dict) -> str | None:
    """Nom traduit en français s'il existe (l'anglais est la langue maître), sinon le nom."""
    for t in x.get("translations") or []:
        if str(t.get("language", "")).lower().startswith("fr") and t.get("name"):
            return t["name"]
    return x.get("name")


def build(client) -> dict:
    cats = {c.get("searchKey"): {"nom": _fr(c), "parent": c.get("parentCategory")} for c in client.get("ProductCategory")}
    raw_prods = client.get("Product")
    prods = {p.get("searchKey"): {"nom": _fr(p), "cat": p.get("productCategory")} for p in raw_prods}
    fournisseurs = {}
    try:
        for bp in client.get("BusinessPartner/byVendor", {"isVendor": "true"}):
            fournisseurs[bp.get("searchKey")] = bp.get("commercialName") or bp.get("fiscalName") or bp.get("name") or bp.get("searchKey")
    except SystemExit:
        pass
    noms = {p.get("name"): p.get("searchKey") for p in raw_prods if p.get("name")}  # noms maîtres (anglais), tels que renvoyés par Stock et réceptions
    noms.update({v["nom"]: k for k, v in prods.items() if v.get("nom") and v["nom"] not in noms})
    norm = lambda v: v if v in prods else noms.get(v, v)  # noqa: E731  (le champ product peut être un nom ou un search key)
    couts = {}
    for pp in client.get("ProductPrice/byPriceList", {"priceList": CONFIG["sales_price_list"]}):
        if num(pp.get("cost")) > 0:
            couts[norm(pp.get("product"))] = num(pp.get("cost"))
    source = "coût de la liste de vente"
    if not couts and CONFIG.get("cost_price_list"):
        # repli : prix d'achat HT de la liste d'achat comme coût
        for pp in client.get("ProductPrice/byPriceList", {"priceList": CONFIG["cost_price_list"]}):
            v = num(pp.get("unitPrice")) or num(pp.get("listPrice"))
            if v > 0:
                couts[norm(pp.get("product"))] = v
        source = "prix de la liste d'achat"
    refs = {"maj": now_local().isoformat(timespec="seconds"), "categories": cats, "produits": prods, "couts": couts, "source_couts": source, "noms": noms, "fournisseurs": fournisseurs}
    save_json("ref/refs.json", refs)
    return refs


def load(client) -> dict:
    return load_json("ref/refs.json") or build(client)


def sector(refs: dict, sk: str | None, cat: str | None = None, fallback: str | None = None) -> str:
    """Remonte la hiérarchie de catégories jusqu'au niveau racine (= secteur)."""
    cats = refs.get("categories", {})
    c = cat or (refs.get("produits", {}).get(sk) or {}).get("cat")
    seen = set()
    while c in cats and cats[c].get("parent") and c not in seen:
        seen.add(c)
        c = cats[c]["parent"]
    if c in cats:
        return cats[c]["nom"]
    return fallback or "Non classé"


def sk_of(refs: dict, rec: dict) -> str | None:
    """Search key d'un produit à partir d'une ligne (commande, stock, réception).
    Selon l'API, `product` est un search key (commandes) ou un nom (stock, réceptions)."""
    pi = rec.get("product_info") or {}
    if pi.get("searchKey"):
        return pi["searchKey"]
    v = rec.get("product")
    if v in refs.get("produits", {}):
        return v
    return refs.get("noms", {}).get(v, v)


def supplier(refs: dict, v: str | None) -> str:
    """Nom lisible d'un fournisseur (les commandes donnent le search key, les réceptions le nom)."""
    return refs.get("fournisseurs", {}).get(v) or v or "Fournisseur inconnu"


def product_name(refs: dict, sk: str, fallback: str | None = None) -> str:
    return (refs.get("produits", {}).get(sk) or {}).get("nom") or fallback or sk
