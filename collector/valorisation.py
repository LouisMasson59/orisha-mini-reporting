"""Valorisation du stock par les mouvements : PMP et dernier prix d'achat (DPA), par magasin.

Principe (rejoué intégralement chaque nuit, pour intégrer les factures arrivées après la réception) :
  - mouvements de stock lus dans l'API (StockTransaction/by*, incrémental via lastUpdated) et conservés dans
    docs/data/valo/<entrepôt>.json ;
  - réception fournisseur (V+) : prix d'entrée = prix de la FACTURE d'achat de la commande (même article),
    sinon prix de la ligne de COMMANDE ; PMP = (stock avant × PMP + qté reçue × prix) / stock après ; DPA = prix ;
  - entrée sans prix (inventaire d'ouverture, I+ sur stock nul, transfert entrant…) : prix du fournisseur habituel
    (onglet Purchasing) si l'article n'a pas encore de PMP ;
  - sorties (ventes, retours fournisseur, transferts, inventaire négatif) : quantité diminuée, PMP inchangé.
  - Pas de frais d'approche (PMP = PRMP hors frais).
L'historique de valeur est reconstruit jour par jour depuis le premier mouvement.
Contrôle : la quantité rejouée est comparée au stock de l'API ; un écart est recalé (et compté) au jour du calcul.
Sortie : docs/data/stocks/valorisation.json
"""
from collections import defaultdict
from datetime import date, timedelta

from . import refs as R
from .store import CONFIG, load_json, num, r2, save_json

ENDPOINTS = ["byVendor", "byVendorAWO", "byCustomer", "byCustomerAWO", "byInventory", "byInventoryAWO",
             "byGoodsMovement", "byGoodsMovementAWO", "byBomMovement"]
DEBUT = "2000-01-01 00:00:00"


def _ref(v):
    return (v.get("searchKey") or v.get("name") or v.get("id")) if isinstance(v, dict) else v


def entrepots() -> list:
    """(code, entrepôt, libellé, nom d'organisation) des magasins suivis en stock et des entrepôts hors magasin."""
    out = [(s["code"], str(s["warehouse"]), s["label"], s.get("api_name")) for s in CONFIG["stores"] if s.get("stocks", True)]
    out += [(str(e.get("code", e["warehouse"])), str(e["warehouse"]), e.get("label", e["warehouse"]), None) for e in CONFIG.get("stock_extra", [])]
    return out


def _lire_mouvements(client, depuis: str) -> list:
    seen, out = set(), []
    for ep in ENDPOINTS:
        try:
            rows = client.get(f"StockTransaction/{ep}", {"lastUpdated": depuis})
        except SystemExit as e:
            print(f"  StockTransaction/{ep} indisponible : {str(e)[:120]}")
            continue
        for t in rows:
            if t.get("id") not in seen:
                seen.add(t.get("id"))
                out.append(t)
    return out


def _prix_reception(client, refs, lignes_recep: set, cache: dict) -> None:
    """Complète cache["recep"] (ligne de réception → commande, article, ligne de commande),
    cache["cde"] (prix commande) et cache["fac"] (prix facture) pour les lignes de réception demandées."""
    manquantes = {x for x in lignes_recep if x and x not in cache["recep"]}
    if manquantes:
        dates = sorted(cache.get("_dates_recep") or [])
        if dates:
            d, fin = date.fromisoformat(dates[0]) - timedelta(days=1), date.fromisoformat(dates[-1]) + timedelta(days=1)
            while d <= fin:
                e = min(fin, d + timedelta(days=30))
                for g in client.get("ShipmentInOut/goodsReceiptByMovementDateRange", {"dateFrom": d.isoformat(), "dateTo": e.isoformat()}):
                    for l in g.get("lines") or []:
                        if l.get("id") in manquantes:
                            ol = l.get("orderLine")
                            cache["recep"][l["id"]] = [g.get("orderDocumentNo"), R.sk_of(refs, l), ol.get("id") if isinstance(ol, dict) else ol]
                d = e + timedelta(days=1)
    docs = {v[0] for k, v in cache["recep"].items() if k in lignes_recep and v[0]}
    # prix commande (une fois connus, ils ne changent plus)
    a_lire = sorted(d for d in docs if d not in cache["cde_docs"])
    for i in range(0, len(a_lire), 20):
        try:
            orders = client.get("Order/byDocumentNumbers", {"documentNumbers": ",".join(a_lire[i:i + 20])})
        except SystemExit:
            orders = []
        for o in orders:
            for l in o.get("lines") or []:
                p = num(l.get("netUnitPrice")) or num(l.get("unitPrice"))
                if p:
                    cache["cde"][f"{o.get('documentNo')}|{R.sk_of(refs, l)}"] = p
                    if l.get("id"):
                        cache["cde"][l["id"]] = p
            cache["cde_docs"].append(o.get("documentNo"))
    # prix facture (relu tant que la commande n'est pas facturée)
    for doc in sorted(d for d in docs if d not in cache["fac_docs"]):
        try:
            invs = client.get("Invoice/byOrderDocumentNo", {"orderDocumentNo": doc})
        except SystemExit:
            invs = []
        trouve = False
        for inv in invs:
            if inv.get("isSale") is True or inv.get("isVoid") is True:
                continue
            for l in inv.get("lines") or []:
                p = num(l.get("unitPrice")) or num(l.get("netUnitPrice"))
                if p and num(l.get("invoicedQuantity")) > 0:
                    cache["fac"][f"{doc}|{R.sk_of(refs, l)}"] = p
                    trouve = True
        if trouve:
            cache["fac_docs"].append(doc)


def collect(client, refs: dict, day: date, now_iso: str, stock_api: dict) -> dict:
    """stock_api : {code magasin: {sk: quantité}} issu de la photo de stock du jour (contrôle et recalage)."""
    prix_fourn = refs.get("prix_fournisseur") or {}
    methodes = {s["code"]: s.get("valorisation", "PMP") for s in CONFIG["stores"]}
    res = {"maj": now_iso, "date": day.isoformat(), "magasins": {}}
    etat_global = load_json("valo/_etat.json", {}) or {}
    lieux = entrepots()
    # nouvel entrepôt suivi : relecture complète des mouvements (les exports ne filtrent pas par entrepôt)
    complet = any(load_json(f"valo/{c}.json", None) is None for c, *_ in lieux)
    depuis = DEBUT if complet else etat_global.get("lastUpdated", DEBUT)
    nouveaux = _lire_mouvements(client, depuis)
    max_cree = max([str(t.get("creationDate") or "") for t in nouveaux] + [etat_global.get("lastUpdated", DEBUT)])
    code_par_wh = {wh: c for c, wh, *_ in lieux}
    code_par_org = {str(org): c for c, _wh, _l, org in lieux if org}
    par_code = defaultdict(list)
    for t in nouveaux:
        c = code_par_wh.get(str(_ref(t.get("warehouse")))) or code_par_org.get(str(_ref(t.get("organization"))))
        if c:
            par_code[c].append(t)

    for code, wh, label, _org in lieux:
        st = load_json(f"valo/{code}.json", None) or {"mouvements": {}, "recep": {}, "cde": {}, "cde_docs": [], "fac": {}, "fac_docs": []}
        for t in par_code.get(code, []):
            if R.non_stocke(refs, R.sk_of(refs, t)):
                continue
            st["mouvements"][t["id"]] = [str(t.get("movementDate") or "")[:10], str(t.get("creationDate") or ""), R.sk_of(refs, t),
                                         str(t.get("movementType") or ""), num(t.get("movementQty")), t.get("inOutLineId")]
        mv = sorted(st["mouvements"].values(), key=lambda m: (m[0], m[1]))
        recep = {m[5] for m in mv if m[3].startswith("V") and num(m[4]) > 0 and m[5]}
        st["_dates_recep"] = sorted({m[0] for m in mv if m[5] in recep and m[5] not in st["recep"]})
        if recep:
            _prix_reception(client, refs, recep, st)
        st.pop("_dates_recep", None)

        def prix_entree(m):
            info = st["recep"].get(m[5]) if m[5] else None
            if info:
                doc, sk, ol = info
                p = st["fac"].get(f"{doc}|{sk}")
                if p:
                    return p, "facture"
                p = st["cde"].get(ol) if ol else None
                p = p or st["cde"].get(f"{doc}|{sk}")
                if p:
                    return p, "commande"
            return None, None

        # rejeu chronologique avec valeur cumulée (PMP et DPA) et instantané à chaque fin de journée
        qty, pmp, dpa = defaultdict(float), {}, {}
        tot = {"pmp": 0.0, "dpa": 0.0}
        sec_tot = defaultdict(lambda: {"pmp": 0.0, "dpa": 0.0})
        histo, sans_prix, sources = [], set(), defaultdict(int)

        def maj(sk, q_new, pmp_new, dpa_new):
            sec = R.sector(refs, sk)
            for meth, old_p, new_p in (("pmp", pmp.get(sk, 0.0), pmp_new), ("dpa", dpa.get(sk, 0.0), dpa_new)):
                delta = max(0.0, q_new) * new_p - max(0.0, qty[sk]) * old_p
                tot[meth] += delta
                sec_tot[sec][meth] += delta
            qty[sk], pmp[sk], dpa[sk] = q_new, pmp_new, dpa_new

        def entree_sans_prix(sk):
            p = pmp.get(sk) or prix_fourn.get(sk)
            if not p:
                sans_prix.add(sk)
            return p or 0.0

        jour_courant = None
        for m in mv:
            d, _, sk, typ, q, _l = m
            if jour_courant and d != jour_courant:
                histo.append([jour_courant, r2(tot["pmp"]), r2(tot["dpa"])])
            jour_courant = d
            q0 = qty[sk]
            if q > 0 and typ.startswith("V"):  # réception fournisseur
                p, src = prix_entree(m)
                if p is None:
                    p, src = entree_sans_prix(sk), "prix fournisseur"
                sources[src] += 1
                base = max(0.0, q0)
                new_pmp = (base * pmp.get(sk, 0.0) + q * p) / (base + q) if base + q > 0 else p
                maj(sk, q0 + q, new_pmp, p)
            elif q > 0:  # autre entrée (inventaire, transfert, retour client) : au PMP, sinon prix fournisseur
                p = pmp.get(sk) or entree_sans_prix(sk)
                maj(sk, q0 + q, p, dpa.get(sk) or p)
            else:  # sortie : PMP inchangé
                maj(sk, q0 + q, pmp.get(sk, 0.0), dpa.get(sk, 0.0))
        if jour_courant:
            histo.append([jour_courant, r2(tot["pmp"]), r2(tot["dpa"])])

        # contrôle avec le stock de l'API et recalage au jour du calcul
        api = stock_api.get(code)
        ecarts = 0
        if api is not None:
            for sk in set(api) | {k for k, v in qty.items() if v}:
                if R.non_stocke(refs, sk):
                    continue
                cible = max(0.0, num(api.get(sk)))
                if abs(max(0.0, qty[sk]) - cible) > 1e-6:
                    ecarts += 1
                    p = pmp.get(sk) or entree_sans_prix(sk)
                    maj(sk, cible, p, dpa.get(sk) or p)
            if histo and histo[-1][0] == day.isoformat():
                histo[-1] = [day.isoformat(), r2(tot["pmp"]), r2(tot["dpa"])]
            else:
                histo.append([day.isoformat(), r2(tot["pmp"]), r2(tot["dpa"])])
        save_json(f"valo/{code}.json", st)
        res["magasins"][code] = {
            "entrepot": wh, "label": label, "methode": methodes.get(code, "PMP"),
            "valeur_pmp": r2(tot["pmp"]), "valeur_dpa": r2(tot["dpa"]),
            "secteurs": {s: {"pmp": r2(v["pmp"]), "dpa": r2(v["dpa"])} for s, v in sorted(sec_tot.items()) if abs(v["pmp"]) > 0.005 or abs(v["dpa"]) > 0.005},
            "historique": histo[-400:], "mouvements": len(mv), "premier_mouvement": mv[0][0] if mv else None,
            "receptions": dict(sources), "sans_prix": len(sans_prix), "ecarts_recales": ecarts,
            "articles": {sk: {"q": r2(qty[sk]), "pmp": r2(pmp.get(sk, 0)), "dpa": r2(dpa.get(sk, 0))} for sk in sorted(qty) if qty[sk] > 0}
            if sum(1 for v in qty.values() if v > 0) <= 5000 else None,
        }
    etat_global["lastUpdated"] = max_cree
    save_json("valo/_etat.json", etat_global)
    save_json("stocks/valorisation.json", res)
    return res
