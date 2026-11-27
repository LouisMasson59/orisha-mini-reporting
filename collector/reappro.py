"""Réapprovisionnement mini/maxi — V0 du complément franchisé.

Calcul (par magasin) :
  disponible = stock physique (entrepôt du magasin) + reste à recevoir des commandes fournisseurs
  si disponible < mini  →  besoin = maxi − disponible, arrondi au colisage du fournisseur
                            (quantité minimale de commande, multiple) lu dans l'onglet Purchasing d'Openbravo.
Les seuils (mini, maxi, fournisseur imposé) viennent de collector/seuils/<magasin>.csv
(téléversé depuis le tableau de bord).

Assortiment : seuls les articles de l'assortiment du magasin (Product/byStore) peuvent être commandés.
Un article de l'assortiment sans seuil est listé « sans seuil » (commandable à la main) ; un seuil sur un
article hors assortiment est signalé en anomalie et ignoré. Le fichier de propositions contient les exceptions
(à commander, couverts) et, jusqu'à reappro.max_lignes_ok, le reste de l'assortiment.

Commande : les lignes validées sont regroupées par fournisseur et importées dans Openbravo
(ImportService/PurchaseOrder → commande au statut « Booked »).

Fichiers produits :
  docs/data/reappro/<magasin>.json    propositions (lues par le tableau de bord et le Google Sheet)
  docs/data/reappro/commandes.json    journal des commandes créées
"""
import csv
import json
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import refs as R
from .store import CONFIG, load_json, now_local, num, r2, save_json
from .ventes import is_excluded, is_purchase

RC = CONFIG.get("reappro", {})
SEUILS_DIR = Path(__file__).parent / "seuils"


# ------------------------------------------------------------------ paramètres
def magasin(code: str) -> dict:
    for m in RC.get("magasins", []):
        if str(m["code"]) == str(code):
            return m
    raise SystemExit(f"Magasin {code} absent de config.json > reappro.magasins")


def lire_seuils(code: str, seuils_json: str | None = None) -> dict:
    """{search key: {mini, maxi, fournisseur}} depuis le JSON du Sheet ou le CSV du dépôt."""
    if seuils_json:
        rows = json.loads(seuils_json)
    else:
        f = SEUILS_DIR / f"{code}.csv"
        if not f.exists():  # magasin sans fichier de seuils : tout l'assortiment est « sans seuil » (commande manuelle)
            return {}
        txt = f.read_text(encoding="utf-8-sig")
        first = txt.split("\n", 1)[0]
        rows = list(csv.DictReader(txt.splitlines(), delimiter=";" if first.count(";") >= first.count(",") else ","))
        rows = [{str(k or "").strip().lower(): v for k, v in r.items()} for r in rows]
    out = {}
    for r in rows:
        sk = str(r.get("produit") or r.get("sk") or r.get("code") or "").strip()
        if not sk or sk.startswith("#"):
            continue
        mini, maxi = (num(str(r.get(k) or "").replace(",", ".")) for k in ("mini", "maxi"))
        out[sk] = {"mini": mini, "maxi": max(mini, maxi), "fournisseur": (str(r.get("fournisseur") or "").strip() or None)}
    return out


def _key(v):
    """Référence Openbravo → search key / nom (les exports renvoient une chaîne ou un objet)."""
    if isinstance(v, dict):
        return v.get("searchKey") or v.get("name") or v.get("_identifier") or v.get("id")
    return v


# ------------------------------------------------------------------ données Openbravo
def org_name(client, m: dict) -> str:
    try:
        for o in client.get("Organization"):
            if str(o.get("searchKey")) == str(m.get("search_key", m["code"])):
                return o.get("name")
    except SystemExit:
        pass
    return m.get("api_name") or m["code"]


def stock_magasin(client, refs: dict, m: dict) -> tuple[dict, dict]:
    qty, names = defaultdict(float), {}
    for rec in client.get("Stock/byWarehouse", {"warehouse": m["warehouse"]}):
        sk = R.sk_of(refs, rec)
        qty[sk] += num(rec.get("quantityOnHand"))
        names[sk] = (rec.get("product_info") or {}).get("name") or rec.get("product")
    return qty, names


def _adresse(loc) -> str | None:
    if isinstance(loc, dict):
        return loc.get("name") or loc.get("address")
    return loc if isinstance(loc, str) and loc else None


def commandes_magasin(client, org: str, day: date, n_days: int) -> list:
    """Toutes les commandes (ventes et achats) de l'organisation sur n jours, par tranches de 31 jours."""
    seen, out, d = set(), [], day - timedelta(days=n_days)
    while d <= day:
        e = min(day, d + timedelta(days=30))
        for o in client.get("Order/byOrgOrderDateRange", {"organization": org, "dateFrom": d.isoformat(), "dateTo": (e + timedelta(days=1)).isoformat()}):
            k = o.get("id") or o.get("documentNo")
            if k not in seen:
                seen.add(k)
                out.append(o)
        d = e + timedelta(days=1)
    return out


def commandes_ouvertes(client, refs: dict, orders: list, day: date, adresses: dict | None = None) -> tuple[dict, list]:
    """Reste à recevoir par article (commandes fournisseurs du magasin) + numéros de documents existants.
    `adresses` (optionnel) est complété avec l'adresse fournisseur des commandes existantes."""
    d0 = day - timedelta(days=int(RC.get("historique_commandes_j", 90)))
    pos = [o for o in orders if is_purchase(o)]
    pos = list({(o.get("id") or o.get("documentNo")): o for o in pos}.values())
    docs = [o.get("documentNo") for o in pos if o.get("documentNo")]
    if adresses is not None:
        for o in sorted(pos, key=lambda o: str(o.get("orderDate") or "")):
            a = _adresse(o.get("invoiceAddress")) or _adresse(o.get("shipmentAddress"))
            if a:
                adresses[_key(o.get("businessPartner"))] = a
    pos = [o for o in pos if not is_excluded(o) and str(o.get("docStatus") or "").upper() != "CL"]
    receipts = [g for g in client.get("ShipmentInOut/goodsReceiptByMovementDateRange", {"dateFrom": d0.isoformat(), "dateTo": (day + timedelta(days=1)).isoformat()})
                if g.get("isSale") is not True and g.get("isReturn") is not True
                and str(g.get("documentStatus") or "CO").upper() in ("CO", "CL")]
    by_line, by_doc = defaultdict(float), defaultdict(float)
    for g in receipts:
        for l in g.get("lines") or []:
            ol = l.get("orderLine")
            ol_id = ol.get("id") if isinstance(ol, dict) else (ol if isinstance(ol, str) else None)
            if ol_id:
                by_line[ol_id] += num(l.get("movementQuantity"))
            by_doc[(g.get("orderDocumentNo"), R.sk_of(refs, l))] += num(l.get("movementQuantity"))
    out = defaultdict(lambda: {"qte": 0.0, "retard": False, "docs": []})
    for o in pos:
        head_due = o.get("scheduledDeliveryDate") or o.get("deliveryDate") or o.get("datePromised")
        for l in o.get("lines") or []:
            sk = R.sk_of(refs, l)
            recv = by_line[l["id"]] if l.get("id") in by_line else by_doc.get((o.get("documentNo"), sk), 0.0)
            recv = max(recv, num(l.get("deliveredQuantity")))
            rest = num(l.get("orderedQuantity")) - recv
            if rest <= 0:
                continue
            due = str(l.get("deliveryDate") or l.get("scheduledDeliveryDate") or head_due or "")[:10] or None
            late = bool(due and date.fromisoformat(due) < day)
            e = out[sk]
            e["qte"] += rest
            e["retard"] |= late
            e["docs"].append({"doc": o.get("documentNo"), "fournisseur": _key(o.get("businessPartner")), "qte": r2(rest), "date_prevue": due, "retard": late})
    return out, docs


def produits(client, sks: set) -> dict:
    """Fiches article (avec onglet Purchasing) des seuls articles demandés : un appel par article,
    ou l'export complet si la liste dépasse reappro.max_lectures_articles."""
    sks = {s for s in sks if s}
    if len(sks) > int(RC.get("max_lectures_articles", 1500)):
        return {p.get("searchKey"): p for p in client.get("Product") if p.get("searchKey") in sks}
    out = {}
    for sk in sorted(sks):
        try:
            found = client.get("Product/bySearchKey", {"productSearchKey": sk})
        except SystemExit:
            found = []
        p = next((x for x in found if x.get("searchKey") == sk), None)
        if p:
            out[sk] = p
    return out


def ligne_achat(prod: dict | None, fournisseur: str | None):
    lines = [l for l in (prod or {}).get("productPurchasing") or []
             if l.get("isActive", True) is not False and not l.get("isDiscontinued") and not l.get("discontinued")]
    if fournisseur:
        return next((l for l in lines if _key(l.get("businessPartner")) == fournisseur), None)
    return next((l for l in lines if l.get("currentVendor")), lines[0] if lines else None)


def arrondir(besoin: float, l: dict) -> tuple[float, float, float]:
    """Quantité commandée = max(besoin, minimum de commande), arrondie au multiple supérieur."""
    moq = num(l.get("minimumOrderQty"))
    if str(l.get("quantityType") or "").upper() == "M":
        mult = num(l.get("standardQuantity")) or num(l.get("quantityPerPackage")) or 1
    else:
        mult = num(l.get("quantityPerPackage")) if num(l.get("quantityPerPackage")) > 1 else 1
    q = max(besoin, moq)
    return math.ceil(q / mult - 1e-9) * mult, moq, mult


# ------------------------------------------------------------------ calcul
def _alternative(la: dict, fournisseurs: dict, refs_cout: float) -> dict:
    sup = _key(la.get("businessPartner"))
    moq = num(la.get("minimumOrderQty"))
    _, _, mult = arrondir(0, la)
    return {"fournisseur": sup, "nom": fournisseurs.get(sup) or sup, "habituel": bool(la.get("currentVendor")),
            "prix": r2(num(la.get("purchaseOrderPrice")) or num(la.get("lastPurchasePrice")) or refs_cout),
            "delai_j": int(num(la.get("purchasingLeadTime")) or RC.get("delai_defaut_j", CONFIG.get("default_lead_days", 7))),
            "minimum_cde": r2(moq), "multiple": r2(mult)}


def _actives(prod: dict | None) -> list:
    return [l for l in (prod or {}).get("productPurchasing") or []
            if l.get("isActive", True) is not False and not l.get("isDiscontinued") and not l.get("discontinued")]


_ASSORT = {}


def assortiment(client, m: dict) -> dict | None:
    """{search key: fiche article} de l'assortiment du magasin, onglet Purchasing compris. None si indisponible."""
    try:
        ps = client.get("Product/byStore", {"store": m.get("search_key", m["code"])})
    except SystemExit:
        return None
    return {p.get("searchKey"): p for p in ps if p.get("searchKey")} or None


def calculer(client, code: str, day: date, now_iso: str, seuils_json: str | None = None, demande: str | None = None) -> dict:
    m = magasin(code)
    refs = R.load(client)
    tous_seuils = lire_seuils(code, seuils_json)
    assort = assortiment(client, m)
    _ASSORT[code] = assort
    if assort is None:
        seuils, hors = tous_seuils, []
    else:
        assort = {sk: p for sk, p in assort.items() if R.est_stocke(p)}  # services / non stockés : pas de réappro
        _ASSORT[code] = assort
        seuils = {sk: v for sk, v in tous_seuils.items() if sk in assort}
        hors = sorted(sk for sk in tous_seuils if sk not in assort)
        for sk in assort:  # article de l'assortiment sans seuil : suivi, jamais proposé automatiquement
            seuils.setdefault(sk, {"mini": 0.0, "maxi": 0.0, "fournisseur": None, "sans_seuil": True})
    org = org_name(client, m)
    stock, names = stock_magasin(client, refs, m)
    orders = commandes_magasin(client, org, day, int(RC.get("historique_commandes_j", 90)))
    ouvert, _docs = commandes_ouvertes(client, refs, orders, day)
    fournisseurs, couts, cats = refs.get("fournisseurs", {}), refs.get("couts", {}), refs.get("categories", {})

    # 1re passe : statut de chaque article suivi (stock + reste à recevoir vs seuils) — sans lire les fiches article
    statuts = {}
    for sk, s in seuils.items():
        st, oc = max(0.0, stock.get(sk, 0.0)), ouvert.get(sk, {"qte": 0.0})
        dispo = st + oc["qte"]
        statuts[sk] = ("sans_seuil" if s.get("sans_seuil") else "a_commander" if s["mini"] > 0 and dispo < s["mini"]
                       else "couvert" if s["mini"] > 0 and st < s["mini"] else "ok")
    exceptions = [sk for sk, x in statuts.items() if x in ("a_commander", "couvert")]
    max_ok = int(RC.get("max_lignes_ok", 5000))
    autres = [sk for sk, x in statuts.items() if x in ("ok", "sans_seuil")]
    detail_autres = len(autres) <= max_ok

    # 2e passe : fiches article (Purchasing) — l'assortiment les fournit ; sinon lecture des seules exceptions
    besoin_fiches = set(exceptions) | (set(autres) if detail_autres else set())
    prods = {sk: assort[sk] for sk in besoin_fiches if sk in assort} if assort else {}
    manquantes = {sk for sk in exceptions if "productPurchasing" not in (prods.get(sk) or {})}
    if manquantes:
        prods.update(produits(client, manquantes))
    lignes, ok_lignes = [], []
    anomalies = ([f"{sk} : hors assortiment du magasin — seuil ignoré" for sk in hors]
                 + (["Assortiment du magasin indisponible (Product/byStore) : contrôle d'assortiment désactivé"] if assort is None else []))
    for sk, s in seuils.items():
        st, oc = max(0.0, stock.get(sk, 0.0)), ouvert.get(sk, {"qte": 0.0, "retard": False, "docs": []})
        dispo = st + oc["qte"]
        cat = (refs.get("produits", {}).get(sk) or {}).get("cat")
        base = {"sk": sk, "nom": R.product_name(refs, sk, names.get(sk)), "famille": (cats.get(cat) or {}).get("nom") or cat or "Non classé",
                "stock": r2(st), "en_commande": r2(oc["qte"]), "disponible": r2(dispo), "mini": r2(s["mini"]), "maxi": r2(s["maxi"]),
                "rupture": st <= 0, "statut": statuts[sk]}
        if statuts[sk] in ("ok", "sans_seuil"):
            if detail_autres:
                prod = prods.get(sk)
                ok_lignes.append({**base, "nom": R.product_name(refs, sk, names.get(sk) or (prod or {}).get("name")),
                                  "alternatives": sorted((_alternative(l, fournisseurs, couts.get(sk, 0.0)) for l in _actives(prod)),
                                                         key=lambda a: (not a["habituel"], a["prix"] or 0)),
                                  "fournisseur": _key((ligne_achat(prod, s["fournisseur"]) or {}).get("businessPartner"))})
            continue
        prod = prods.get(sk)
        alts = sorted((_alternative(l, fournisseurs, couts.get(sk, 0.0)) for l in _actives(prod)), key=lambda a: (not a["habituel"], a["prix"] or 0))
        la = ligne_achat(prod, s["fournisseur"])
        row = {**base, "nom": R.product_name(refs, sk, names.get(sk) or (prod or {}).get("name")),
               "commande_en_retard": oc["retard"], "commandes": oc["docs"], "besoin": 0, "qte_proposee": 0,
               "fournisseur": None, "fournisseur_nom": None, "minimum_cde": None, "multiple": None, "delai_j": None,
               "livraison_prevue": None, "prix": None, "montant": 0, "alternatives": alts}
        if prod is None:
            anomalies.append(f"{sk} : article introuvable dans Openbravo")
        elif la is None:
            anomalies.append(f"{sk} : fournisseur {s['fournisseur'] or 'habituel'} absent de l'onglet Purchasing")
        if la:
            a = _alternative(la, fournisseurs, couts.get(sk, 0.0))
            row.update({"fournisseur": a["fournisseur"], "fournisseur_nom": a["nom"], "delai_j": a["delai_j"], "prix": a["prix"],
                        "minimum_cde": a["minimum_cde"], "multiple": a["multiple"]})
        if statuts[sk] == "a_commander":
            row["besoin"] = r2(s["maxi"] - dispo)
            if la:
                q, _, _ = arrondir(s["maxi"] - dispo, la)
                row.update({"qte_proposee": r2(q), "livraison_prevue": (day + timedelta(days=row["delai_j"])).isoformat(),
                            "montant": r2(q * (row["prix"] or 0))})
        lignes.append(row)
    ordre = {"a_commander": 0, "couvert": 1}
    lignes.sort(key=lambda r: (ordre[r["statut"]], r["fournisseur"] or "", r["sk"]))
    groupes = defaultdict(lambda: {"lignes": 0, "qte": 0.0, "montant": 0.0, "livraison_prevue": None})
    for r in lignes:
        if r["statut"] == "a_commander" and r["qte_proposee"]:
            g = groupes[r["fournisseur"]]
            g["lignes"] += 1
            g["qte"] += r["qte_proposee"]
            g["montant"] += r["montant"]
            g["livraison_prevue"] = max(filter(None, [g["livraison_prevue"], r["livraison_prevue"]]))
    res = {"date": day.isoformat(), "maj": now_iso, "source": client.source, "demande": demande,
           "magasin": {"code": code, "label": m.get("label", code), "organisation": org, "entrepot": m["warehouse"]},
           "regle": "Commande si disponible (stock + reste à recevoir) < mini ; quantité = maxi − disponible, arrondie au colisage fournisseur (onglet Purchasing).",
           "resume": {"assortiment": len(assort) if assort is not None else None,
                      "articles_suivis": sum(1 for s in seuils.values() if not s.get("sans_seuil")),
                      "a_commander": sum(r["statut"] == "a_commander" for r in lignes),
                      "couverts": sum(r["statut"] == "couvert" for r in lignes),
                      "ok": sum(x == "ok" for x in statuts.values()), "sans_seuil": sum(x == "sans_seuil" for x in statuts.values()),
                      "hors_assortiment": len(hors),
                      "ruptures": sum(1 for sk, s in seuils.items() if not s.get("sans_seuil") and stock.get(sk, 0.0) <= 0),
                      "en_stock_sans_seuil": sum(1 for sk, q in stock.items() if q > 0 and sk not in seuils),
                      "fiches_lues": len(prods), "montant_propose": r2(sum(g["montant"] for g in groupes.values()))},
           "commandes_proposees": [{"fournisseur": k, "fournisseur_nom": fournisseurs.get(k) or k, **{x: r2(v) if isinstance(v, float) else v for x, v in g.items()}}
                                   for k, g in sorted(groupes.items())],
           "lignes": lignes,
           "lignes_ok": ok_lignes if detail_autres else None,  # OK + sans seuil ; au-delà du plafond : seulement les nombres
           "anomalies": anomalies}
    save_json(f"reappro/{code}.json", res)
    actifs = {str(x["code"]) for x in RC.get("magasins", [])}
    idx = [x for x in (load_json("reappro/index.json", []) or []) if x.get("code") != code and x.get("code") in actifs]
    save_json("reappro/index.json", sorted(idx + [{"code": code, "label": m.get("label", code)}], key=lambda x: x["code"]))
    return res


# ------------------------------------------------------------------ commande
def _taux_taxes(client) -> dict:
    """{catégorie de taxe produit: (search key du taux, %)} — le champ tax des documents attend le TAUX."""
    out = {k: tuple(v) for k, v in (RC.get("taxes") or {}).items()}
    try:
        for t in client.get("TaxRate"):
            cat = _key(t.get("taxCategory"))
            if cat and cat not in out and t.get("searchKey"):
                out[cat] = (t["searchKey"], num(t.get("rate")))
    except SystemExit:
        pass
    return out


def _fiche_fournisseur(client, sk: str, adresses_cdes: dict) -> dict:
    """Adresse fournisseur : fiche tiers (bySearchKey, puis liste des fournisseurs) > dernière commande > config."""
    bp, trace = {}, []
    for path, params in (("BusinessPartner/bySearchKey", {"businessPartnerSearchKey": sk}), ("BusinessPartner/byVendor", {"isVendor": "true"})):
        try:
            found = [b for b in client.get(path, params) if b.get("searchKey") == sk]
        except SystemExit as e:
            found, _ = [], trace.append(f"{path} : {str(e)[:120]}")
        if found:
            bp = found[0]
            if bp.get("locations"):
                break
    locs = [l for l in bp.get("locations") or [] if isinstance(l, dict)]
    loc = next((l for l in locs if l.get("invoiceAddress")), locs[0] if locs else {})
    conf = (RC.get("fournisseurs") or {}).get(sk) or {}
    adresse = _adresse(loc) or adresses_cdes.get(sk) or conf.get("adresse")
    if not adresse:
        trace.append(f"fiche tiers {'trouvée' if bp else 'introuvable'}, champs : {sorted(bp)[:25]}, adresses : {len(locs)}")
    return {"adresse": adresse, "trace": "; ".join(trace),
            "liste_prix": _key(bp.get("purchasePriceList")) or RC.get("liste_prix_achat", "Purchase Price List"),
            "paiement": _key(bp.get("poPaymentTerms")) or RC.get("conditions_paiement", "30 jours")}


def _messages(rep) -> str:
    """Messages d'erreur lisibles d'une réponse ImportService / StatusService."""
    if not isinstance(rep, dict):
        return ""
    data = rep.get("data") if isinstance(rep.get("data"), dict) else {}
    errs = (data.get("errors") or []) + (rep.get("errors") or [])
    return "; ".join(str(e.get("message") or e.get("detail") or e.get("title")) for e in errs if isinstance(e, dict))


def _prochain_numero(prefixe: str, existants: list) -> int:
    nums = [int(x[len(prefixe):]) for x in existants if x and x.startswith(prefixe) and re.fullmatch(r"\d+", x[len(prefixe):])]
    return max(nums, default=0) + 1


def commander(client, code: str, day: date, now_iso: str, lignes_json: str | None = None,
              seuils_json: str | None = None, demande: str | None = None) -> list:
    """Crée dans Openbravo les commandes des lignes validées (toutes les propositions si aucune liste)."""
    m = magasin(code)
    prop = calculer(client, code, day, now_iso, seuils_json, demande)  # données fraîches avant de commander
    ref = {r["sk"]: r for r in prop["lignes"] + (prop.get("lignes_ok") or [])}
    if lignes_json:
        demandees = [{"sk": str(x.get("produit") or x.get("sk")), "qte": num(x.get("qte")), "fournisseur": x.get("fournisseur") or None}
                     for x in json.loads(lignes_json)]
    else:
        demandees = [{"sk": r["sk"], "qte": r["qte_proposee"], "fournisseur": r["fournisseur"]} for r in prop["lignes"] if r["statut"] == "a_commander"]
    refs = R.load(client)
    assort = _ASSORT.get(code)
    sks = {d["sk"] for d in demandees}
    prods = {sk: assort[sk] for sk in sks if assort and sk in assort and "productPurchasing" in assort[sk]}
    prods.update(produits(client, sks - set(prods) - (set() if assort is None else sks - set(assort))))
    taxes = _taux_taxes(client)
    par_fournisseur, rejets = defaultdict(list), []
    for d in demandees:
        if d["qte"] <= 0:
            continue
        if assort is not None and d["sk"] not in assort:
            rejets.append(f"{d['sk']} : hors assortiment du magasin — non commandable")
            continue
        r = ref.get(d["sk"], {})
        sup = d["fournisseur"] or r.get("fournisseur")
        la = ligne_achat(prods.get(d["sk"]), sup)
        if la is None:
            rejets.append(f"{d['sk']} : article introuvable" if d["sk"] not in prods else f"{d['sk']} : fournisseur {sup} absent de l'onglet Purchasing")
            continue
        cat = _key((prods.get(d["sk"]) or {}).get("taxCategory")) or "Normal"
        if cat not in taxes:
            rejets.append(f"{d['sk']} : aucun taux de taxe pour la catégorie {cat}")
            continue
        par_fournisseur[_key(la.get("businessPartner"))].append({**d, "ligne": la, "taxe": taxes[cat]})
    journal = load_json("reappro/commandes.json", []) or []
    adresses_cdes = {}
    orders = commandes_magasin(client, prop["magasin"]["organisation"], day, int(RC.get("historique_commandes_j", 90)))
    _, existants = commandes_ouvertes(client, refs, orders, day, adresses_cdes)
    prefixe = m.get("prefixe_commande", f"{m['code']}-PO-")
    n = _prochain_numero(prefixe, existants + [c.get("documentNo") for c in journal if c.get("etat") == "ok"])
    resultats = []
    for sup, ls in sorted(par_fournisseur.items()):
        fiche = _fiche_fournisseur(client, sup, adresses_cdes)
        if not fiche["adresse"]:
            rejets.append(f"{sup} : adresse fournisseur introuvable (renseigner config.json > reappro.fournisseurs) — {fiche['trace']}")
            continue
        doc = f"{prefixe}{n:04d}"
        n += 1
        lines, taxes_doc, net_t, tax_t, due_max = [], defaultdict(lambda: [0.0, 0.0]), 0.0, 0.0, day
        for i, l in enumerate(ls):
            la, (tax_key, rate) = l["ligne"], l["taxe"]
            prix = num(la.get("purchaseOrderPrice")) or num(la.get("lastPurchasePrice"))
            net = round(l["qte"] * prix, 2)
            tax = round(net * rate / 100, 2)
            due = day + timedelta(days=int(num(la.get("purchasingLeadTime")) or RC.get("delai_defaut_j", 7)))
            due_max = max(due_max, due)
            lines.append({"lineNo": (i + 1) * 10, "product": l["sk"], "qty": l["qte"], "uom": RC.get("unite", "Unit"),
                          "netUnitPrice": prix, "netListPrice": prix,
                          "baseGrossUnitPrice": round(prix * (1 + rate / 100), 2), "grossListPrice": round(prix * (1 + rate / 100), 2),
                          "netAmount": net, "grossAmount": round(net + tax, 2), "tax": tax_key, "scheduledDeliveryDate": due.isoformat(),
                          "taxLines": [{"lineNo": 10, "tax": tax_key, "taxableAmount": net, "taxAmount": tax}]})
            taxes_doc[tax_key][0] += net
            taxes_doc[tax_key][1] += tax
            net_t += net
            tax_t += tax
        po = {"organization": m.get("search_key", code), "transactionDocument": RC.get("type_document", "Purchase Order"),
              "documentNo": doc, "orderDate": day.isoformat(), "accountingDate": day.isoformat(),
              "scheduledDeliveryDate": due_max.isoformat(), "businessPartner": sup,
              "partnerAddress": fiche["adresse"], "invoiceFrom": fiche["adresse"], "warehouse": m["warehouse"],
              "currency": RC.get("devise", "EUR"), "priceList": fiche["liste_prix"], "paymentTerms": fiche["paiement"],
              "priceIncludesTax": False, "netAmount": round(net_t, 2), "grossAmount": round(net_t + tax_t, 2),
              "description": f"Réappro mini/maxi — proposition validée{' (' + demande + ')' if demande else ''}",
              "lines": lines,
              "taxes": [{"lineNo": (i + 1) * 10, "tax": k, "taxableAmount": round(v[0], 2), "taxAmount": round(v[1], 2)} for i, (k, v) in enumerate(taxes_doc.items())]}
        res = client.import_and_wait("PurchaseOrder", [po])
        if res["etat"] == "erreur":
            res["reponse"] = _messages(res.get("reponse")) or res.get("reponse")
        resultats.append({"date": day.isoformat(), "maj": now_iso, "demande": demande, "magasin": code, "documentNo": doc,
                          "fournisseur": sup, "lignes": len(lines), "montant_ht": round(net_t, 2), "livraison_prevue": due_max.isoformat(),
                          "etat": res["etat"], "request": res.get("request"),
                          "detail": res.get("reponse") if res["etat"] != "ok" else None})
    for msg in rejets:
        resultats.append({"date": day.isoformat(), "maj": now_iso, "demande": demande, "magasin": code, "documentNo": None,
                          "etat": "rejet", "detail": msg})
    save_json("reappro/commandes.json", (journal + resultats)[-200:])
    if any(r["etat"] == "ok" for r in resultats):
        calculer(client, code, day, now_iso, seuils_json, demande)  # les nouvelles commandes apparaissent en « reste à recevoir »
    return resultats
