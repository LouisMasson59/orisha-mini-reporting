"""Ventes : un fichier détaillé par jour + un historique d'agrégats (1 ligne / jour / magasin)."""
from collections import defaultdict
from datetime import date, timedelta

from . import orgs
from . import refs as R
from .store import CONFIG, load_json, num, r2, save_json, upsert


def is_purchase(o: dict) -> bool:
    doc = (str(o.get("documentType") or "") + " " + str(o.get("documentType$_identifier") or "")).lower()
    return any(k in doc for k in CONFIG["purchase_document_keywords"])


def is_excluded(o: dict) -> bool:
    return any(o.get(f) is True for f in CONFIG["excluded_order_flags"])


def fetch(client, store: dict, d1: date, d2: date) -> list:
    """Commandes de vente d'un magasin entre deux dates (par tranches de 31 jours).

    La borne de fin est élargie d'un jour (au cas où l'API la traite comme exclusive),
    puis les doublons entre tranches sont retirés ; le tri par jour se fait ensuite sur orderDate.
    """
    org = orgs.resolve(client).get(store["code"]) or store["api_name"]
    seen, out, d = set(), [], d1
    while d <= d2:
        e = min(d2, d + timedelta(days=30))
        for o in client.get("Order/byOrgOrderDateRange", {"organization": org, "dateFrom": d.isoformat(), "dateTo": (e + timedelta(days=1)).isoformat()}):
            key = o.get("id") or o.get("documentNo")
            if key in seen:
                continue
            seen.add(key)
            out.append(o)
        d = e + timedelta(days=1)
    return [o for o in out if not is_purchase(o) and not is_excluded(o)]


def _hour(o: dict, day: date):
    """Heure du ticket, seulement si la date de création = date de vente (sinon : données chargées a posteriori)."""
    ts = str(o.get("localCreationDate") or o.get("creationDate") or "")
    if len(ts) >= 13 and ts[:10] == day.isoformat():
        try:
            return int(ts[11:13])
        except ValueError:
            return None
    return None


def build_block(orders: list, refs: dict, day: date) -> dict:
    k = defaultdict(float)
    heures = [0.0] * 24
    tickets_h = [0] * 24
    heure_connue = False
    vend = defaultdict(lambda: {"ca_ttc": 0.0, "tickets": 0})
    prod = {}
    pay = defaultdict(float)
    sect = defaultdict(lambda: {"qte": 0.0, "ca_ttc": 0.0, "ca_ht": 0.0, "marge": 0.0})
    for o in orders:
        ret = o.get("isReturn") is True
        if not ret:
            k["tickets"] += 1
        head_vendor = o.get("salesRepresentative") or "Non renseigné"
        if not ret:
            vend[head_vendor]["tickets"] += 1
        h = _hour(o, day)
        if h is not None and not ret:
            tickets_h[h] += 1
        for l in o.get("lines") or []:
            if l.get("isDeleted"):
                continue
            pi = l.get("product_info") or {}
            sk = l.get("product") or pi.get("searchKey")
            qty, ttc, ht = num(l.get("orderedQuantity")), num(l.get("grossAmount")), num(l.get("netAmount"))
            cost = num(l.get("purchasePrice")) or refs.get("couts", {}).get(sk, 0.0)
            marge = ht - qty * cost
            if num(l.get("grossListPrice")):
                remise = max(0.0, num(l.get("grossListPrice")) * qty - ttc) if qty > 0 else 0.0
            else:
                remise = sum(num(p.get("totalAmount")) for p in l.get("promotions") or [])
            k["ca_ttc"] += ttc
            k["ca_ht"] += ht
            k["articles"] += qty
            k["marge"] += marge
            k["remise"] += remise
            if h is not None:
                heures[h] += ttc
                heure_connue = True
            v = l.get("salesRepresentative") or head_vendor
            vend[v]["ca_ttc"] += ttc
            sec = R.sector(refs, sk, pi.get("productCategory"), pi.get("mainProductCategoryName") or pi.get("productCategoryName"))
            p = prod.setdefault(sk, {"sk": sk, "nom": R.product_name(refs, sk, pi.get("name")), "secteur": sec, "qte": 0.0, "ca_ttc": 0.0, "marge": 0.0})
            p["qte"] += qty
            p["ca_ttc"] += ttc
            p["marge"] += marge
            s = sect[sec]
            s["qte"] += qty
            s["ca_ttc"] += ttc
            s["ca_ht"] += ht
            s["marge"] += marge
        for pm in o.get("payments") or []:
            pay[pm.get("name") or pm.get("paymentType") or "Autre"] += num(pm.get("amount"))
    t = k["tickets"]
    kpi = {
        "ca_ttc": r2(k["ca_ttc"]), "ca_ht": r2(k["ca_ht"]), "tickets": int(t), "articles": r2(k["articles"]),
        "panier": r2(k["ca_ttc"] / t) if t else None, "upt": r2(k["articles"] / t) if t else None,
        "marge": r2(k["marge"]), "taux_marge": r2(k["marge"] / k["ca_ht"] * 100) if k["ca_ht"] else None,
        "remise": r2(k["remise"]), "taux_remise": r2(k["remise"] / (k["ca_ttc"] + k["remise"]) * 100) if k["ca_ttc"] + k["remise"] else None,
    }
    return {
        "kpi": kpi,
        "par_heure": [r2(x) for x in heures] if heure_connue else None,
        "tickets_heure": tickets_h if heure_connue else None,
        "vendeurs": sorted(({"nom": n, "ca_ttc": r2(v["ca_ttc"]), "tickets": v["tickets"]} for n, v in vend.items()), key=lambda x: -x["ca_ttc"]),
        "produits": sorted(({**p, "qte": r2(p["qte"]), "ca_ttc": r2(p["ca_ttc"]), "marge": r2(p["marge"])} for p in prod.values()), key=lambda x: -x["ca_ttc"]),
        "paiements": {n: r2(v) for n, v in sorted(pay.items(), key=lambda x: -x[1])},
        "secteurs": {n: {kk: r2(vv) for kk, vv in s.items()} for n, s in sorted(sect.items(), key=lambda x: -x[1]["ca_ttc"])},
    }


def write_days(by_day: dict, refs: dict, source: str, now_iso: str) -> None:
    """by_day = {date: {code_magasin: [commandes]}} → fichiers jour + historique + index."""
    hist_rows = []
    for day, by_store in sorted(by_day.items()):
        mags = {}
        for st in CONFIG["stores"]:
            mags[st["code"]] = build_block(by_store.get(st["code"], []), refs, day)
            hist_rows.append({"date": day.isoformat(), "magasin": st["code"], **mags[st["code"]]["kpi"]})
        all_orders = [o for lst in by_store.values() for o in lst]
        save_json(f"ventes/jour/{day.isoformat()}.json",
                  {"date": day.isoformat(), "maj": now_iso, "source": source, "total": build_block(all_orders, refs, day), "magasins": mags})
    if hist_rows:
        upsert("ventes/historique.json", hist_rows, ("date", "magasin"))
    jours = set(load_json("ventes/jours.json", []) or []) | {d.isoformat() for d in by_day}
    save_json("ventes/jours.json", sorted(jours))


def sale_day(o: dict):
    """Jour de rattachement d'une vente : date de commande (la date métier POS peut rester
    sur une ancienne journée tant que la caisse n'a pas été clôturée)."""
    for f in ("orderDate", "businessDate"):
        try:
            return date.fromisoformat(str(o.get(f))[:10])
        except ValueError:
            continue
    return None


def collect(client, refs: dict, d1: date, d2: date, now_iso: str) -> int:
    by_day = {d1 + timedelta(days=i): {} for i in range((d2 - d1).days + 1)}
    n = 0
    for st in CONFIG["stores"]:
        got = fetch(client, st, d1, d2)
        kept = 0
        for o in got:
            od = sale_day(o)
            if od in by_day:
                by_day[od].setdefault(st["code"], []).append(o)
                kept += 1
        n += kept
        print(f"  {st['code']} {st['label']} : {len(got)} commandes de vente reçues, {kept} retenues sur la période")
    write_days(by_day, refs, client.source, now_iso)
    return n
