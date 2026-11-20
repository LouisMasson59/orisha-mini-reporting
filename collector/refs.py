"""Référentiel : produits, catégories (→ secteur racine) et coûts. Rafraîchi chaque nuit."""
from .store import CONFIG, load_json, num, save_json, now_local


def build(client) -> dict:
    cats = {c.get("searchKey"): {"nom": c.get("name"), "parent": c.get("parentCategory")} for c in client.get("ProductCategory")}
    prods = {p.get("searchKey"): {"nom": p.get("name"), "cat": p.get("productCategory")} for p in client.get("Product")}
    couts = {}
    for pp in client.get("ProductPrice/byPriceList", {"priceList": CONFIG["sales_price_list"]}):
        if num(pp.get("cost")) > 0:
            couts[pp.get("product")] = num(pp.get("cost"))
    source = "coût de la liste de vente"
    if not couts and CONFIG.get("cost_price_list"):
        # repli : prix d'achat HT de la liste d'achat comme coût
        for pp in client.get("ProductPrice/byPriceList", {"priceList": CONFIG["cost_price_list"]}):
            v = num(pp.get("unitPrice")) or num(pp.get("listPrice"))
            if v > 0:
                couts[pp.get("product")] = v
        source = "prix de la liste d'achat"
    refs = {"maj": now_local().isoformat(timespec="seconds"), "categories": cats, "produits": prods, "couts": couts, "source_couts": source}
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


def product_name(refs: dict, sk: str, fallback: str | None = None) -> str:
    return (refs.get("produits", {}).get(sk) or {}).get("nom") or fallback or sk
