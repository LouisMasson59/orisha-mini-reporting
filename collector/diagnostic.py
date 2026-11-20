"""Diagnostic des API Openbravo réelles : vérifie chaque source du reporting et
indique les valeurs exactes à mettre dans config.json.

Résultat affiché dans le journal de l'action et dans son résumé (onglet Summary).
"""
import os
from collections import Counter
from datetime import timedelta

from .store import CONFIG, num, today


def _try(fn):
    try:
        return fn(), None
    except SystemExit as e:
        return None, str(e).splitlines()[0][:200]
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"[:200]


def _org_label(o: dict) -> str:
    info = o.get("organization_info") or {}
    return str(info.get("name") or o.get("organization$_identifier") or o.get("organization") or "?")


def run(client) -> str:
    out = ["# Diagnostic API Openbravo", ""]
    ok = lambda b: "✅" if b else "❌"  # noqa: E731
    t = today()

    # 1. Connexion + organisations
    orgs, err = _try(lambda: client.get("Organization"))
    out += ["## 1. Connexion et organisations", ""]
    if err:
        out += [f"❌ Échec : `{err}`", "", "→ Vérifier les secrets OB_URL / OB_USER / OB_PASSWORD et que l'environnement est joignable depuis Internet.", ""]
        return "\n".join(out)
    out += [f"{ok(orgs)} {len(orgs)} organisation(s) visibles par l'utilisateur API", "", "| Nom (à utiliser dans `api_name`) | Search key |", "|---|---|"]
    out += [f"| {o.get('name')} | {o.get('searchKey')} |" for o in orgs[:40]]
    out += [""]

    # 2. Commandes sans filtre
    out += ["## 2. Commandes visibles (sans filtre)", ""]
    body, err = _try(lambda: client.get_page("Order", {}, 50))
    orders = (body or {}).get("data") or []
    if isinstance(orders, dict):
        orders = orders.get("records") or []
    if err:
        out += [f"❌ `{err}`", ""]
    else:
        out += [f"{ok(orders)} {len(orders)} commande(s) sur la première page"]
        if orders:
            orgs_seen = Counter(_org_label(o) for o in orders)
            types = Counter(str(o.get("documentType$_identifier") or o.get("documentType")) for o in orders)
            out += ["", "Organisations rencontrées : " + ", ".join(f"`{k}` ({v})" for k, v in orgs_seen.most_common()),
                    "", "Types de document : " + ", ".join(f"`{k}` ({v})" for k, v in types.most_common())]
            from .ventes import is_purchase
            sales = [x for x in orders if not is_purchase(x)]
            o = sales[0] if sales else orders[0]
            l0 = (o.get("lines") or [{}])[0]
            out += ["", "Exemple de commande de vente :", "```",
                    f"documentNo={o.get('documentNo')}  orderDate={o.get('orderDate')}  creationDate={o.get('creationDate')}",
                    f"organization={o.get('organization')}  organization_info={o.get('organization_info')}",
                    f"isSale={o.get('isSale')}  isReturn={o.get('isReturn')}  salesRepresentative={o.get('salesRepresentative')}",
                    f"grossAmount={o.get('grossAmount')}  netAmount={o.get('netAmount')}  lignes={len(o.get('lines') or [])}  paiements={len(o.get('payments') or [])}",
                    f"ligne 1 : product={l0.get('product')}  qty={l0.get('orderedQuantity')}  grossAmount={l0.get('grossAmount')}  netAmount={l0.get('netAmount')}  grossListPrice={l0.get('grossListPrice')}  purchasePrice={l0.get('purchasePrice')}  vendeur={l0.get('salesRepresentative')}",
                    "```"]
            pos = [x for x in orders if is_purchase(x)]
            if pos:
                pl = (pos[0].get("lines") or [{}])[0]
                out += ["", "Exemple de ligne de commande d'achat :", "```",
                        f"documentNo={pos[0].get('documentNo')}  businessPartner={pos[0].get('businessPartner')}  organization={_org_label(pos[0])}",
                        f"orderedQuantity={pl.get('orderedQuantity')}  deliveredQuantity={pl.get('deliveredQuantity')}  shippedQuantity={pl.get('shippedQuantity')}  deliveryDate={pl.get('deliveryDate')}  netUnitPrice={pl.get('netUnitPrice')}",
                        "```"]
    out += [""]

    # 3. Commandes par magasin (filtre utilisé par le reporting)
    out += ["## 3. Ventes des 30 derniers jours par magasin (filtre `organization`)", "", "| Magasin | Valeur testée | Commandes | Résultat |", "|---|---|---|---|"]
    d1, d2 = (t - timedelta(days=30)).isoformat(), t.isoformat()
    from . import orgs as ORG
    resolved = ORG.resolve(client)
    for st in CONFIG["stores"]:
        candidates = [resolved.get(st["code"]) or st["api_name"]]
        for alt in (st["api_name"], st["code"], st["label"]):
            if alt and alt not in candidates and not str(alt).startswith("A_RENSEIGNER"):
                candidates.append(alt)
        for c in candidates:
            res, err = _try(lambda c=c: client.get("Order/byOrgOrderDateRange", {"organization": c, "dateFrom": d1, "dateTo": d2}))
            n = len(res or [])
            out += [f"| {st['code']} | `{c}` | {err or n} | {ok(n > 0)} |"]
            if n:
                break
    out += [""]

    # 4. Stock
    out += ["## 4. Stock actuel par entrepôt (`warehouse`)", "", "| Magasin | Entrepôt | Lignes | Quantité totale |", "|---|---|---|---|"]
    for st in CONFIG["stores"]:
        res, err = _try(lambda st=st: client.get("Stock/byWarehouse", {"warehouse": st["warehouse"]}))
        out += [f"| {st['code']} | `{st['warehouse']}` | {err or len(res)} | {'' if err else round(sum(num(r.get('quantityOnHand')) for r in res))} |"]
    out += [""]

    # 5. Référentiel
    out += ["## 5. Référentiel", ""]
    for label, path, params in (("Produits", "Product", {}), ("Catégories", "ProductCategory", {}),
                                (f"Prix « {CONFIG['sales_price_list']} »", "ProductPrice/byPriceList", {"priceList": CONFIG["sales_price_list"]}),
                                (f"Prix « {CONFIG.get('cost_price_list')} » (coût de repli)", "ProductPrice/byPriceList", {"priceList": CONFIG.get("cost_price_list")})):
        res, err = _try(lambda path=path, params=params: client.get(path, params))
        extra = ""
        if res and path == "ProductPrice/byPriceList":
            extra = f" — dont {sum(1 for r in res if num(r.get('cost')) > 0)} avec un coût, {sum(1 for r in res if num(r.get('unitPrice')) > 0)} avec un prix"
        if res and path == "ProductCategory":
            extra = f" — dont {sum(1 for r in res if not r.get('parentCategory'))} de niveau racine (= secteurs)"
        out += [f"- {ok(res)} {label} : {err or len(res)}{extra}"]
    body, err = _try(lambda: client.get_page("ProductPrice", {}, 100))
    pl = (body or {}).get("data") or []
    if isinstance(pl, dict):
        pl = pl.get("records") or []
    if pl:
        names = Counter(str(r.get("priceList$_identifier") or r.get("priceList")) for r in pl)
        out += ["- Listes de prix rencontrées : " + ", ".join(f"`{k}`" for k in names) + "  ← à reporter dans `sales_price_list` / `cost_price_list` si différent"]
    elif err:
        out += [f"- Listes de prix : `{err}`"]
    out += [""]

    # 6. Achats
    out += ["## 6. Achats", ""]
    res, err = _try(lambda: client.get("ShipmentInOut/goodsReceiptByMovementDateRange", {"dateFrom": d1, "dateTo": d2}))
    out += [f"- {ok(res)} Réceptions fournisseurs 30 j : {err or len(res)}"]
    if res:
        g = res[0]
        out += [f"  - exemple : movementDate={g.get('movementDate')} businessPartner={g.get('businessPartner')} orderDocumentNo={g.get('orderDocumentNo')}"]
    from .ventes import is_purchase as _isp
    for org in ORG.purchase_orgs(client):
        res, err = _try(lambda org=org: client.get("Order/byOrgOrderDateRange", {"organization": org, "dateFrom": (t - timedelta(days=180)).isoformat(), "dateTo": d2}))
        pur = [o for o in res or [] if _isp(o)]
        out += [f"- {ok(pur)} Commandes d'achat via `{org}` (180 j) : {err or f'{len(pur)} sur {len(res)} commandes'}"]
    out += ["", f"_{client.calls} appels API_"]
    return "\n".join(out)


def main(client):
    report = run(client)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")
