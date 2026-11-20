"""API Openbravo simulée (OB_MOCK=1).

Reproduit la forme des réponses ExportService (Order, Stock, ShipmentInOut,
Product, ProductCategory, ProductPrice, Organization) avec des données de démo
déterministes et scénarisées :
  - 0001 magasin moteur (Sport fort), 0002 Passy orienté Luxe/Mode,
    0003 magasin en difficulté (Bricolage/Ameublement, tendance baissière), ECOM web ;
  - 2 fournisseurs par secteur : un fiable, un en retard ;
  - ruptures sur best-sellers Sport à 0001, surstock Luxe à 0002, stock dormant à 0003.
"""
import hashlib
import math
import random
from datetime import date, datetime, timedelta

from .store import CONFIG, now_local, parse_day, today

CATALOGUE = {
    "Sport": {"Running": [("Chaussures running homme", 89.99, 32), ("Chaussures running femme", 84.99, 30),
                          ("T-shirt respirant", 19.99, 6), ("Montre GPS sport", 149, 70), ("Gourde isotherme", 14.99, 4.5)],
              "Vélo": [("Casque vélo urbain", 39.99, 14), ("Antivol U", 29.99, 9), ("Pompe à pied", 24.99, 8), ("Gants vélo", 17.99, 5)]},
    "Fashion & Accessoires": {"Prêt-à-porter": [("Jean slim", 59.9, 18), ("Chemise oxford", 49.9, 14), ("Pull col rond laine", 79.9, 24), ("Robe midi fleurie", 69.9, 20)],
                              "Accessoires": [("Sac cabas cuir", 129, 45), ("Ceinture cuir", 39.9, 11), ("Écharpe laine", 34.9, 9), ("Casquette coton", 24.9, 6)]},
    "Ameublement": {"Salon": [("Canapé 3 places", 899, 380), ("Table basse chêne", 249, 95), ("Lampadaire design", 149, 55)],
                    "Décoration": [("Coussin velours", 29.9, 8), ("Miroir rond", 89, 30), ("Plaid laine", 59, 19)]},
    "Bricolage & Construction": {"Outillage": [("Perceuse visseuse 18V", 129, 62), ("Coffret embouts 32 pcs", 19.9, 6), ("Niveau laser", 79, 35)],
                                 "Matériaux": [("Sac ciment 25 kg", 8.9, 4.8), ("Peinture blanche 10 L", 54.9, 24), ("Rouleau laine 180 mm", 12.9, 4)]},
    "Luxe": {"Maroquinerie": [("Sac à main cuir grainé", 1450, 420), ("Portefeuille cuir", 390, 105)],
             "Horlogerie & Bijoux": [("Montre automatique acier", 2900, 980), ("Bracelet or 18 carats", 1850, 760), ("Boucles d'oreilles perles", 690, 210)]},
    "Électronique": {"Audio": [("Casque sans fil réduction de bruit", 299, 150), ("Enceinte bluetooth", 99, 45), ("Écouteurs true wireless", 149, 62)],
                     "Informatique": [("Ordinateur portable 15 pouces", 899, 610), ("Souris sans fil", 29.9, 10), ("Disque SSD externe 1 To", 109, 62)]},
    "Épicerie": {"Épicerie salée": [("Pâtes 500 g", 1.1, 0.83), ("Riz basmati 1 kg", 2.7, 2.05), ("Huile d'olive 50 cl", 3.9, 2.6)],
                 "Épicerie sucrée": [("Chocolat noir 100 g", 1.9, 1.1), ("Confiture fraise 370 g", 2.9, 1.7), ("Café moulu 250 g", 4.5, 2.9)]},
}
SECTOR_KEYS = {"Sport": "SPO", "Fashion & Accessoires": "FAS", "Ameublement": "AME", "Bricolage & Construction": "BRI",
               "Luxe": "LUX", "Électronique": "ELE", "Épicerie": "EPI"}
SUPPLIERS = {"Sport": ("Athletix Distribution", "Velocity Supply"), "Fashion & Accessoires": ("Maison Textile Lyon", "Urban Threads"),
             "Ameublement": ("Bois & Forme", "Nordic Home"), "Bricolage & Construction": ("ProOutil France", "BatiPlus Négoce"),
             "Luxe": ("Atelier Vendôme", "Prestige Horlogerie"), "Électronique": ("TechSource Europe", "Digital Wave"),
             "Épicerie": ("Saveurs du Terroir", "EpiGros")}
STORE_PROFILE = {
    "0001": {"base": 70, "trend": 0.10, "mix": {"Sport": 3, "Fashion & Accessoires": 1.5, "Ameublement": 0.6, "Bricolage & Construction": 0.8, "Luxe": 0.2, "Électronique": 1.2, "Épicerie": 2.5},
             "vendors": ["Camille Durand", "Lucas Martin", "Inès Lefebvre", "Hugo Petit"]},
    "0002": {"base": 45, "trend": 0.0, "mix": {"Sport": 0.8, "Fashion & Accessoires": 2.5, "Ameublement": 0.8, "Bricolage & Construction": 0.1, "Luxe": 2.0, "Électronique": 1.0, "Épicerie": 1.0},
             "vendors": ["Sophie Bernard", "Thomas Moreau", "Léa Fournier"]},
    "0003": {"base": 32, "trend": -0.18, "mix": {"Sport": 0.6, "Fashion & Accessoires": 0.5, "Ameublement": 2.0, "Bricolage & Construction": 2.5, "Luxe": 0, "Électronique": 0.6, "Épicerie": 0.8},
             "vendors": ["Nicolas Girard", "Emma Lambert", "Antoine Roux"]},
    "ECOM": {"base": 40, "trend": 0.15, "mix": {"Sport": 2.0, "Fashion & Accessoires": 2.0, "Ameublement": 0.8, "Bricolage & Construction": 0.4, "Luxe": 0.4, "Électronique": 2.2, "Épicerie": 0},
             "vendors": ["Site e-commerce"]},
}
WEEKDAY = [0.8, 0.85, 1.0, 0.9, 1.05, 1.5, 0.0]
STORE_HOURS = list(range(9, 20))
STORE_HOUR_W = [3, 5, 7, 10, 9, 6, 6, 7, 9, 10, 6]
NO_SALE = {("0003", "Niveau laser")}                       # stock dormant scénarisé
RUPTURES = {("0001", "Chaussures running homme"), ("0001", "T-shirt respirant"), ("0001", "Gourde isotherme")}
OVERSTOCK = {("0002", "Montre automatique acier"), ("0002", "Bracelet or 18 carats")}


def rng(*parts) -> random.Random:
    return random.Random(int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


class MockAPI:
    def __init__(self):
        self.stores = {s["code"]: s for s in CONFIG["stores"]}
        self.by_name = {s["api_name"]: s["code"] for s in CONFIG["stores"]}
        self.by_wh = {s["warehouse"]: s["code"] for s in CONFIG["stores"]}
        self.products, self.categories = [], []
        for sector, fams in CATALOGUE.items():
            sk = SECTOR_KEYS[sector]
            self.categories.append({"searchKey": f"SEC-{sk}", "name": sector, "parentCategory": None, "summaryLevel": True})
            for fi, (fam, items) in enumerate(fams.items()):
                fk = f"FAM-{sk}-{fi + 1}"
                self.categories.append({"searchKey": fk, "name": fam, "parentCategory": f"SEC-{sk}", "summaryLevel": False})
                for i, (name, price, cost) in enumerate(items):
                    self.products.append({"searchKey": f"{sk}-{fi + 1}{i + 1:02d}", "name": name, "productCategory": fk,
                                          "sector": sector, "price": price, "cost": cost, "vat": 0.055 if sector == "Épicerie" else 0.20})
        self.by_sk = {p["searchKey"]: p for p in self.products}
        self._po_cache = None

    # ------------------------------------------------------------------ routing
    def get(self, path: str, p: dict) -> list:
        if path == "Product":
            return [{k: v for k, v in x.items() if k in ("searchKey", "name", "productCategory")} for x in self.products]
        if path == "ProductCategory":
            return self.categories
        if path == "ProductPrice/byPriceList":
            return [{"product": x["searchKey"], "listPrice": x["price"], "unitPrice": x["price"], "cost": x["cost"], "priceList": p.get("priceList")} for x in self.products]
        if path == "Organization":
            return [{"id": c, "name": s["api_name"], "searchKey": s.get("search_key", c)} for c, s in self.stores.items()]
        if path == "Order":
            return self._sales("0001", today() - timedelta(days=1)) + [o for o in self._pos()][:5]
        if path == "Order/byOrgOrderDateRange":
            d1, d2 = parse_day(p["dateFrom"]), parse_day(p["dateTo"])
            code = self.by_name.get(p["organization"])
            if code:
                out = []
                d = d1
                while d <= d2:
                    out += self._sales(code, d)
                    d += timedelta(days=1)
                return out
            return [o for o in self._pos() if d1 <= parse_day(o["orderDate"]) <= d2]
        if path == "Stock":
            d = parse_day(p.get("_day", today().isoformat()))
            return [dict(r, organization=self.stores[c].get("search_key", c)) for c in self.stores for r in self._stock(c, d)]
        if path == "Stock/byWarehouse":
            return self._stock(self.by_wh.get(p["warehouse"]), parse_day(p.get("_day", today().isoformat())))
        if path == "ShipmentInOut/goodsReceiptByMovementDateRange":
            d1, d2 = parse_day(p["dateFrom"]), parse_day(p["dateTo"])
            return [r for r in self._receipts() if d1 <= parse_day(r["movementDate"]) <= d2]
        return []

    # ------------------------------------------------------------------ ventes
    def _factor(self, code: str, d: date) -> float:
        prof = STORE_PROFILE[code]
        wd = 0.9 if code == "ECOM" else WEEKDAY[d.weekday()]
        season = {11: 1.1, 12: 1.45 if d.day <= 24 else 0.7, 1: 0.95, 7: 0.85, 8: 0.75}.get(d.month, 1.0)
        progress = (d - (today() - timedelta(days=365))).days / 365
        return wd * season * (1 + prof["trend"] * (progress - 0.5) * 2)

    def _sales(self, code: str, d: date) -> list:
        if code not in STORE_PROFILE or d > today():
            return []
        prof, store = STORE_PROFILE[code], self.stores[code]
        r = rng("sales", code, d)
        n = max(0, int(r.gauss(prof["base"] * self._factor(code, d), prof["base"] * 0.08)))
        sale_period = d.month in (1, 7) and d.day <= 28
        weights = []
        for x in self.products:
            w = prof["mix"].get(x["sector"], 0) / math.sqrt(x["price"])
            if (code, x["name"]) in NO_SALE:
                w = 0
            weights.append(w)
        vend_w = [1.6, 1.25, 1.0, 0.8][: len(prof["vendors"])]
        now = now_local()
        orders = []
        for i in range(n):
            if code == "ECOM":
                hh = r.choices(range(24), weights=[1, 1, 0, 0, 0, 0, 1, 2, 3, 4, 4, 5, 6, 6, 5, 5, 5, 6, 7, 8, 9, 9, 6, 3])[0]
            else:
                hh = r.choices(STORE_HOURS, weights=STORE_HOUR_W)[0]
            mm, ss = r.randrange(60), r.randrange(60)
            future = d == now.date() and (hh, mm) > (now.hour, now.minute)
            vendor = r.choices(prof["vendors"], weights=vend_w)[0]
            is_return = r.random() < 0.02
            lines, gross_t, net_t = [], 0.0, 0.0
            for li in range(r.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]):
                x = r.choices(self.products, weights=weights)[0]
                qty = r.choice([1, 2, 3, 4]) if x["sector"] == "Épicerie" else r.choices([1, 2], weights=[85, 15])[0]
                disc = 0.0
                if r.random() < (0.35 if sale_period else 0.12):
                    disc = r.choice([0.3, 0.4, 0.5]) if sale_period else r.choice([0.1, 0.15, 0.2, 0.3])
                sign = -1 if is_return else 1
                gross = round(sign * qty * x["price"] * (1 - disc), 2)
                net = round(gross / (1 + x["vat"]), 2)
                gross_t += gross
                net_t += net
                promos = [{"name": "Remise démo", "totalAmount": round(qty * x["price"] * disc, 2)}] if disc else []
                lines.append({"lineNo": (li + 1) * 10, "product": x["searchKey"],
                              "product_info": {"searchKey": x["searchKey"], "name": x["name"], "productCategory": x["productCategory"]},
                              "orderedQuantity": sign * qty, "grossAmount": gross, "netAmount": net,
                              "grossListPrice": x["price"], "purchasePrice": x["cost"], "salesRepresentative": vendor,
                              "promotions": promos, "isDeleted": False})
            if code == "ECOM":
                pay = r.choices(["Carte bancaire (web)", "PayPal"], weights=[80, 20])[0]
            else:
                pay = r.choices(["Carte bancaire", "Adyen Tap to Pay", "Espèces", "Carte cadeau"], weights=[62, 18, 12, 8])[0]
            if future:  # tirages aléatoires consommés quand même : la journée reste stable d'un passage à l'autre
                continue
            ts = f"{d.isoformat()}T{hh:02d}:{mm:02d}:{ss:02d}+01:00"
            orders.append({"documentNo": f"{code}-{d:%Y%m%d}-{i + 1:04d}", "orderDate": d.isoformat(), "businessDate": d.isoformat(),
                           "creationDate": ts, "localCreationDate": ts, "organization": code,
                           "organization_info": {"name": store["api_name"], "searchKey": code},
                           "documentType": "POS Return" if is_return else "POS Order", "isSale": True, "isReturn": is_return,
                           "isCancelled": False, "isVoid": False, "salesRepresentative": vendor,
                           "typeOfOrder": {"name": "Home delivery" if code == "ECOM" else "In-Store Purchase"},
                           "grossAmount": round(gross_t, 2), "netAmount": round(net_t, 2), "lines": lines,
                           "payments": [{"name": pay, "paymentType": pay, "amount": round(gross_t, 2)}]})
        return orders

    # ------------------------------------------------------------------ achats
    def _pos(self) -> list:
        if self._po_cache is not None:
            return self._po_cache
        t, pos, receipts = today(), [], []
        for sector, (good, bad) in SUPPLIERS.items():
            items = [x for x in self.products if x["sector"] == sector]
            for si, sup in enumerate((good, bad)):
                reliable = si == 0
                d = t - timedelta(days=200)
                while d <= t:
                    if d.weekday() == len(sup) % 5:  # un jour de commande fixe par fournisseur
                        r = rng("po", sup, d)
                        lead = 7 if reliable else 10
                        due = d + timedelta(days=lead)
                        lines = []
                        for li, x in enumerate(r.sample(items, k=min(len(items), r.randint(2, 4)))):
                            q = r.randint(2, 5) if sector == "Luxe" else (r.randint(50, 200) if sector == "Épicerie" else r.randint(8, 40))
                            delay = (0 if r.random() < 0.98 else 1) if reliable else r.choice([0, 0, 2, 3, 5, 8])
                            recv_day = due + timedelta(days=delay)
                            if recv_day <= t:
                                full = r.random() < (0.97 if reliable else 0.8)
                                recv = q if full or (t - recv_day).days > 30 else int(q * r.choice([0, 0.5, 0.7, 0.9]))
                            else:
                                recv, recv_day = 0, None
                            lines.append({"lineNo": (li + 1) * 10, "product": x["searchKey"],
                                          "product_info": {"searchKey": x["searchKey"], "name": x["name"]},
                                          "orderedQuantity": q, "deliveredQuantity": recv, "deliveryDate": due.isoformat(),
                                          "netUnitPrice": x["cost"], "netAmount": round(q * x["cost"], 2), "_recv_day": recv_day})
                        doc = f"PO-{SECTOR_KEYS[sector]}{si + 1}-{d:%Y%m%d}"
                        pos.append({"documentNo": doc, "orderDate": d.isoformat(), "documentType": "Purchase Order", "isSale": False,
                                    "businessPartner": sup, "organization": "France", "lines": lines,
                                    "netAmount": round(sum(l["netAmount"] for l in lines), 2)})
                    d += timedelta(days=1)
        for po in pos:
            by_day = {}
            for l in po["lines"]:
                if l["_recv_day"] and l["deliveredQuantity"] > 0:
                    by_day.setdefault(l["_recv_day"], []).append(l)
            for rd, ls in by_day.items():
                receipts.append({"documentNo": f"GR-{po['documentNo'][3:]}-{rd:%m%d}", "movementDate": rd.isoformat(), "isSale": False,
                                 "businessPartner": po["businessPartner"], "orderDocumentNo": po["documentNo"],
                                 "lines": [{"product": l["product"], "product_info": l["product_info"], "movementQuantity": l["deliveredQuantity"]} for l in ls]})
            for l in po["lines"]:
                l.pop("_recv_day", None)
        self._po_cache, self._receipts_cache = pos, receipts
        return pos

    def _receipts(self) -> list:
        self._pos()
        return self._receipts_cache

    # ------------------------------------------------------------------ stock
    def _stock(self, code: str | None, d: date) -> list:
        if code not in STORE_PROFILE:
            return []
        prof, wh = STORE_PROFILE[code], self.stores[code]["warehouse"]
        out = []
        for x in self.products:
            mix = prof["mix"].get(x["sector"], 0)
            if mix == 0 and (code, x["name"]) not in NO_SALE:
                continue
            base = max(2.0, 200 * mix / math.sqrt(x["price"])) * (3 if x["sector"] == "Épicerie" else 1)
            wave = 0.8 + 0.4 * (0.5 + 0.5 * math.sin((d.toordinal() + len(x["name"])) / 9))
            q = round(base * wave * (0.85 + 0.3 * rng("stk", code, x["searchKey"], d).random()))
            if (code, x["name"]) in RUPTURES and (today() - d).days < 12:
                q = 0
            if (code, x["name"]) in OVERSTOCK:
                q = 70 + max(0, min(40, (d - (today() - timedelta(days=120))).days // 3))
            if (code, x["name"]) in NO_SALE:
                q = 25
            bins = [q] if q < 6 else [q - q // 3, q // 3]
            for bi, bq in enumerate(bins):
                out.append({"warehouse": wh, "bin": f"{wh}-{'SURFACE' if bi == 0 else 'RESERVE'}", "product": x["name"],
                            "product_info": {"searchKey": x["searchKey"], "name": x["name"]}, "quantityOnHand": bq})
        return out
