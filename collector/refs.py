"""Référentiel : produits, catégories (→ secteur racine) et coûts. Rafraîchi chaque nuit."""
from collections import defaultdict

from .store import CONFIG, load_json, num, save_json, now_local


def est_stocke(p: dict) -> bool:
    """Article géré en stock : type « Item » (pas service, frais, ressource…) et suivi de quantité actif."""
    t = str(p.get("productType") or "I").strip().lower()
    return t in ("i", "item", "article") and p.get("stocked", True) is not False


def _fr(x: dict) -> str | None:
    """Nom traduit en français s'il existe (l'anglais est la langue maître), sinon le nom."""
    for t in x.get("translations") or []:
        if str(t.get("language", "")).lower().startswith("fr") and t.get("name"):
            return t["name"]
    return x.get("name")


def build(client) -> dict:
    cats = {c.get("searchKey"): {"nom": _fr(c), "parent": c.get("parentCategory")} for c in client.get("ProductCategory")}
    raw_prods = client.get("Product")
    prods = {p.get("searchKey"): {"nom": _fr(p), "cat": p.get("productCategory"),
                                  **({} if est_stocke(p) else {"non_stocke": True})} for p in raw_prods}
    fournisseurs = {}
    try:
        for bp in client.get("BusinessPartner/byVendor", {"isVendor": "true"}):
            fournisseurs[bp.get("searchKey")] = bp.get("commercialName") or bp.get("fiscalName") or bp.get("name") or bp.get("searchKey")
    except SystemExit:
        pass
    if not fournisseurs:  # repli : export complet des tiers, filtré sur les fournisseurs
        try:
            for bp in client.get("BusinessPartner"):
                if bp.get("isVendor") or bp.get("vendor"):
                    fournisseurs[bp.get("searchKey")] = bp.get("commercialName") or bp.get("fiscalName") or bp.get("name") or bp.get("searchKey")
        except SystemExit:
            pass
    noms = {p.get("name"): p.get("searchKey") for p in raw_prods if p.get("name")}  # noms maîtres (anglais), tels que renvoyés par Stock et réceptions
    noms.update({v["nom"]: k for k, v in prods.items() if v.get("nom") and v["nom"] not in noms})
    norm = lambda v: v if v in prods else noms.get(v, v)  # noqa: E731  (le champ product peut être un nom ou un search key)
    # Coût unitaire, article par article : coût de la liste de vente, sinon prix de la liste d'achat,
    # sinon prix d'achat du fournisseur habituel (onglet Purchasing de la fiche article).
    couts, origine = {}, {}
    for pp in client.get("ProductPrice/byPriceList", {"priceList": CONFIG["sales_price_list"]}):
        if num(pp.get("cost")) > 0:
            couts[norm(pp.get("product"))] = num(pp.get("cost"))
            origine[norm(pp.get("product"))] = "liste de vente"
    if CONFIG.get("cost_price_list"):
        try:
            for pp in client.get("ProductPrice/byPriceList", {"priceList": CONFIG["cost_price_list"]}):
                v, sk = num(pp.get("unitPrice")) or num(pp.get("listPrice")), norm(pp.get("product"))
                if v > 0 and sk not in couts:
                    couts[sk], origine[sk] = v, "liste d'achat"
        except SystemExit:
            pass
    prix_fournisseur = {}  # prix d'achat du fournisseur habituel : valorisation des entrées sans prix
    for p in raw_prods:
        sk = p.get("searchKey")
        lines = [l for l in p.get("productPurchasing") or [] if l.get("isActive", True) is not False]
        l = next((x for x in lines if x.get("currentVendor")), lines[0] if lines else None)
        v = l and (num(l.get("purchaseOrderPrice")) or num(l.get("lastPurchasePrice")))
        if v:
            prix_fournisseur[sk] = v
            if sk not in couts:
                couts[sk], origine[sk] = v, "prix fournisseur"
    nb = defaultdict(int)
    for v in origine.values():
        nb[v] += 1
    source = " · ".join(f"{k} ({n})" for k, n in sorted(nb.items(), key=lambda x: -x[1])) or "aucun coût"
    refs = {"maj": now_local().isoformat(timespec="seconds"), "categories": cats, "produits": prods, "couts": couts, "source_couts": source, "prix_fournisseur": prix_fournisseur, "noms": noms, "fournisseurs": fournisseurs}
    save_json("ref/refs.json", refs)
    return refs


def non_stocke(refs: dict, sk: str) -> bool:
    return bool((refs.get("produits", {}).get(sk) or {}).get("non_stocke"))


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
