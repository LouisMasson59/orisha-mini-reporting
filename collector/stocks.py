"""Stocks : photo quotidienne par magasin × secteur (quantité, valeur, couverture, ruptures, dormants).

L'API Stock ne donne que le stock ACTUEL : l'historique se construit en accumulant
une photo par nuit (en mode réel, pas de rattrapage possible du passé).
"""
from collections import defaultdict
from datetime import date, timedelta

from . import refs as R
from .store import CONFIG, load_json, num, r2, save_json, upsert


def _sales_window(code: str, day: date, n_days: int) -> dict:
    """Quantités vendues par produit sur les n derniers jours (lues dans les fichiers jour)."""
    qty = defaultdict(float)
    for i in range(n_days):
        f = load_json(f"ventes/jour/{(day - timedelta(days=i)).isoformat()}.json")
        if not f:
            continue
        for p in ((f.get("magasins") or {}).get(code) or {}).get("produits") or []:
            qty[p["sk"]] += num(p.get("qte"))
    return qty


def collect(client, refs: dict, day: date, now_iso: str) -> None:
    win = int(CONFIG["sales_window_days"])
    over = int(CONFIG["overstock_days"])
    couts = refs.get("couts", {})
    photo = {"date": day.isoformat(), "maj": now_iso, "source": client.source, "magasins": {}}
    rows = []
    for st in CONFIG["stores"] + CONFIG.get("stock_extra", []):
        sells = st in CONFIG["stores"]  # un entrepôt central ne vend pas : pas de rupture / dormant / couverture
        params = {"warehouse": st["warehouse"]}
        if client.mock:
            params["_day"] = day.isoformat()
        stock = defaultdict(float)
        names = {}
        for rec in client.get("Stock/byWarehouse", params):
            pi = rec.get("product_info") or {}
            sk = R.sk_of(refs, rec)
            stock[sk] += num(rec.get("quantityOnHand"))
            names[sk] = pi.get("name") or rec.get("product")
        sold = _sales_window(st["code"], day, win) if sells else {}
        negatifs = sum(1 for v in stock.values() if v < 0)
        sect = defaultdict(lambda: {"qte": 0.0, "valeur": 0.0, "refs": 0, "ruptures": 0, "dormants": 0, "vendu": 0.0})
        ruptures, dormants = [], []
        sans_cout = 0
        for sk in set(stock) | set(sold):
            q, s = max(0.0, stock.get(sk, 0.0)), sold.get(sk, 0.0)  # stock négatif ramené à 0 (compté à part)
            sec = R.sector(refs, sk)
            nom = R.product_name(refs, sk, names.get(sk))
            val = q * couts.get(sk, 0.0)
            if q > 0 and sk not in couts:
                sans_cout += 1
            a = sect[sec]
            a["qte"] += q
            a["valeur"] += val
            a["vendu"] += s
            if q > 0:
                a["refs"] += 1
            if s > 0 and q <= 0:
                a["ruptures"] += 1
                ruptures.append({"sk": sk, "nom": nom, "secteur": sec, "vendu": r2(s)})
            elif q > 0 and s == 0 and sells:
                a["dormants"] += 1
                dormants.append({"sk": sk, "nom": nom, "secteur": sec, "qte": r2(q), "valeur": r2(val)})
        detail = {}
        for sec, a in sorted(sect.items()):
            cov = a["qte"] / (a["vendu"] / win) if sells and a["vendu"] > 0 else None
            detail[sec] = {"qte": r2(a["qte"]), "valeur": r2(a["valeur"]), "refs": a["refs"], "ruptures": a["ruptures"],
                           "dormants": a["dormants"], "couverture_j": r2(cov), "surstock": bool(cov and cov > over)}
            rows.append({"date": day.isoformat(), "magasin": st["code"], "secteur": sec, **{k: detail[sec][k] for k in ("qte", "valeur", "ruptures", "dormants", "couverture_j")}})
        photo["magasins"][st["code"]] = {
            "secteurs": detail,
            "refs_sans_cout": sans_cout,
            "refs_stock_negatif": negatifs,
            "vend": sells,
            "ruptures": sorted(ruptures, key=lambda x: -x["vendu"])[:15],
            "dormants": sorted(dormants, key=lambda x: -x["valeur"])[:15],
        }
    save_json("stocks/photo.json", photo)
    upsert("stocks/historique.json", rows, ("date", "magasin", "secteur"))
