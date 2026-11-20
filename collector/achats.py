"""Achats & fournisseurs : commandes ouvertes, retards, taux de service, ponctualité, réceptions."""
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import orgs
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
    for org in orgs.purchase_orgs(client):
        pos += client.get("Order/byOrgOrderDateRange", {"organization": org, "dateFrom": d0.isoformat(), "dateTo": day.isoformat()})
    pos = [o for o in pos if is_purchase(o) and not is_excluded(o)]
    pos = list({(o.get("id") or o.get("documentNo")): o for o in pos}.values())

    # Réceptions sur toute la fenêtre : servent aussi à calculer les quantités reçues par ligne
    # (sur Event, deliveredQuantity peut rester à 0 même après réception).
    receipts = [g for g in client.get("ShipmentInOut/goodsReceiptByMovementDateRange", {"dateFrom": d0.isoformat(), "dateTo": day.isoformat()})
                if g.get("isSale") is not True and g.get("isReturn") is not True
                and str(g.get("documentStatus") or "CO").upper() in ("CO", "CL")]  # réceptions terminées uniquement
    recv_map, recv_by_line = defaultdict(float), defaultdict(float)
    for g in receipts:
        for l in g.get("lines") or []:
            q = num(l.get("movementQuantity"))
            ol = l.get("orderLine")
            ol_id = ol.get("id") if isinstance(ol, dict) else (ol if isinstance(ol, str) else None)
            if ol_id:
                recv_by_line[ol_id] += q
            recv_map[(g.get("orderDocumentNo"), R.sk_of(refs, l))] += q
    lead = timedelta(days=int(CONFIG.get("default_lead_days", 7)))

    sup = defaultdict(lambda: defaultdict(float))
    sectors = defaultdict(Counter)
    due_by_doc, order_date_by_doc, po_supplier = {}, {}, {}
    retards = []
    for o in pos:
        bp = R.supplier(refs, o.get("businessPartner"))
        po_supplier[o.get("documentNo")] = bp
        od = _d(o.get("orderDate"))
        head_due = _d(o.get("scheduledDeliveryDate") or o.get("deliveryDate") or o.get("datePromised"))
        for l in o.get("lines") or []:
            pi = l.get("product_info") or {}
            sk = R.sk_of(refs, l)
            ordered = num(l.get("orderedQuantity"))
            from_receipts = recv_by_line.get(l.get("id")) if l.get("id") in recv_by_line else recv_map.get((o.get("documentNo"), sk), 0.0)
            recv = max(num(l.get("deliveredQuantity")), num(l.get("shippedQuantity")), from_receipts)
            price = num(l.get("netUnitPrice")) or couts.get(sk, 0.0)
            due = _d(l.get("deliveryDate")) or head_due or (od + lead if od else None)
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
                    retards.append({"doc": o.get("documentNo"), "fournisseur": bp, "produit": R.product_name(refs, sk, pi.get("name")), "sk": sk,
                                    "qte_restante": r2(rest), "valeur": r2(rest * price), "date_prevue": due.isoformat(), "jours_retard": (day - due).days})
            if due and day - timedelta(days=30) <= due < day:
                s["cmd_30"] += ordered
                s["recu_30"] += min(recv, ordered)

    rec_rows = defaultdict(lambda: defaultdict(float))
    for g in receipts:
        bp = po_supplier.get(g.get("orderDocumentNo")) or R.supplier(refs, g.get("businessPartner"))
        md = _d(g.get("movementDate"))
        qty = sum(num(l.get("movementQuantity")) for l in g.get("lines") or [])
        def unit_price(l):
            ol = l.get("orderLine")
            return (num(ol.get("netUnitPrice")) if isinstance(ol, dict) else 0.0) or couts.get(R.sk_of(refs, l), 0.0)
        val = sum(num(l.get("movementQuantity")) * unit_price(l) for l in g.get("lines") or [])
        if md:
            r = rec_rows[(md.isoformat(), bp)]
            r["qte"] += qty
            r["valeur"] += val
        if not md or md < day - timedelta(days=30):
            continue  # les indicateurs fournisseurs portent sur les 30 derniers jours
        s = sup[bp]
        s["receptions_30"] += 1
        s["valeur_recue_30"] += val
        doc = g.get("orderDocumentNo")
        if doc in due_by_doc:
            s["rec_rapprochees"] += 1
            s["rec_a_l_heure"] += 1 if md <= due_by_doc[doc] else 0
            if order_date_by_doc.get(doc):
                s["delai_total"] += (md - order_date_by_doc[doc]).days

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
