"""Achats & fournisseurs : commandes ouvertes, retards, taux de service, ponctualité, réceptions."""
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import refs as R
from .store import CONFIG, num, r2, save_json, upsert
from .ventes import is_excluded, is_purchase


def _d(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def collect(client, refs: dict, day: date, now_iso: str) -> None:
    hist_days = int(CONFIG["purchase_history_days"])
    couts = refs.get("couts", {})
    d0 = day - timedelta(days=hist_days)
    pos = []
    for org in CONFIG["purchase_orgs"]:
        pos += client.get("Order/byOrgOrderDateRange", {"organization": org, "dateFrom": d0.isoformat(), "dateTo": day.isoformat()})
    pos = [o for o in pos if is_purchase(o) and not is_excluded(o)]

    sup = defaultdict(lambda: defaultdict(float))
    sectors = defaultdict(Counter)
    due_by_doc, order_date_by_doc = {}, {}
    retards = []
    for o in pos:
        bp = o.get("businessPartner") or "Fournisseur inconnu"
        od = _d(o.get("orderDate"))
        for l in o.get("lines") or []:
            pi = l.get("product_info") or {}
            sk = l.get("product") or pi.get("searchKey")
            ordered = num(l.get("orderedQuantity"))
            recv = num(l.get("deliveredQuantity")) or num(l.get("shippedQuantity"))
            price = num(l.get("netUnitPrice")) or couts.get(sk, 0.0)
            due = _d(l.get("deliveryDate")) or od
            sectors[bp][R.sector(refs, sk)] += 1
            if due:
                doc = o.get("documentNo")
                due_by_doc[doc] = max(due_by_doc.get(doc, due), due)
                order_date_by_doc[doc] = od
            rest = max(0.0, ordered - recv)
            s = sup[bp]
            if rest > 0:
                s["lignes_ouvertes"] += 1
                s["qte_restante"] += rest
                s["valeur_restante"] += rest * price
                if due and due < day:
                    s["lignes_retard"] += 1
                    s["valeur_retard"] += rest * price
                    retards.append({"doc": o.get("documentNo"), "fournisseur": bp, "produit": R.product_name(refs, sk, pi.get("name")),
                                    "qte_restante": r2(rest), "valeur": r2(rest * price), "date_prevue": due.isoformat(), "jours_retard": (day - due).days})
            if due and day - timedelta(days=30) <= due < day:
                s["cmd_30"] += ordered
                s["recu_30"] += min(recv, ordered)

    receipts = client.get("ShipmentInOut/goodsReceiptByMovementDateRange", {"dateFrom": (day - timedelta(days=30)).isoformat(), "dateTo": day.isoformat()})
    rec_rows = defaultdict(lambda: defaultdict(float))
    for g in receipts:
        if g.get("isSale") is True or g.get("isReturn") is True:
            continue
        bp = g.get("businessPartner") or "Fournisseur inconnu"
        md = _d(g.get("movementDate"))
        qty = sum(num(l.get("movementQuantity")) for l in g.get("lines") or [])
        val = sum(num(l.get("movementQuantity")) * couts.get(l.get("product") or (l.get("product_info") or {}).get("searchKey"), 0.0) for l in g.get("lines") or [])
        s = sup[bp]
        s["receptions_30"] += 1
        s["valeur_recue_30"] += val
        doc = g.get("orderDocumentNo")
        if md and doc in due_by_doc:
            s["rec_rapprochees"] += 1
            s["rec_a_l_heure"] += 1 if md <= due_by_doc[doc] else 0
            if order_date_by_doc.get(doc):
                s["delai_total"] += (md - order_date_by_doc[doc]).days
        if md:
            r = rec_rows[(md.isoformat(), bp)]
            r["qte"] += qty
            r["valeur"] += val

    fournisseurs = []
    for bp, s in sup.items():
        fournisseurs.append({
            "fournisseur": bp,
            "secteur": sectors[bp].most_common(1)[0][0] if sectors[bp] else None,
            "lignes_ouvertes": int(s["lignes_ouvertes"]), "qte_restante": r2(s["qte_restante"]), "valeur_restante": r2(s["valeur_restante"]),
            "lignes_retard": int(s["lignes_retard"]), "valeur_retard": r2(s["valeur_retard"]),
            "taux_service_30j": r2(s["recu_30"] / s["cmd_30"] * 100) if s["cmd_30"] else None,
            "ponctualite_30j": r2(s["rec_a_l_heure"] / s["rec_rapprochees"] * 100) if s["rec_rapprochees"] else None,
            "delai_moyen_j": r2(s["delai_total"] / s["rec_rapprochees"]) if s["rec_rapprochees"] else None,
            "receptions_30j": int(s["receptions_30"]), "valeur_recue_30j": r2(s["valeur_recue_30"]),
        })
    fournisseurs.sort(key=lambda x: (x["secteur"] or "", x["fournisseur"]))
    save_json("achats/fournisseurs.json", {"date": day.isoformat(), "maj": now_iso, "source": client.source,
                                           "fournisseurs": fournisseurs,
                                           "retards": sorted(retards, key=lambda x: -x["jours_retard"])[:25]})
    upsert("achats/historique.json",
           [{"date": day.isoformat(), **{k: f[k] for k in ("fournisseur", "secteur", "valeur_retard", "valeur_restante", "taux_service_30j", "ponctualite_30j")}} for f in fournisseurs],
           ("date", "fournisseur"))
    if rec_rows:
        upsert("achats/receptions.json", [{"date": d, "fournisseur": bp, "qte": r2(v["qte"]), "valeur": r2(v["valeur"])} for (d, bp), v in rec_rows.items()],
               ("date", "fournisseur"))
