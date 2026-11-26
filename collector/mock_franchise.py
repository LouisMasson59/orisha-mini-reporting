"""Données simulées du magasin franchisé 0011 (Courcelles) — reflet de ce qui est chargé sur Event.

Stock initial (inventaire 01-AVA), deux commandes en cours (MEUBLEDESIGN en retard, Centrale à l'heure),
onglet Purchasing des articles Furniture (fournisseur habituel MEUBLEDESIGN, ligne SUP-CENTRALE ajoutée).
"""
from datetime import timedelta

from .store import today

ORG = {"id": "0011", "name": "Courcelles - Store", "searchKey": "0011"}
WH = "0011"

# search key: (nom, catégorie, prix MEUBLEDESIGN, prix NORDICHOME)
PRODUITS = {
    "COUSSIN-DECO-GREY": ("Decorative Cushion - Grey", "Furniture-Decor", 12, 13.08),
    "COUSSIN-DECO-BEIGE": ("Decorative Cushion - Beige", "Furniture-Decor", 12, 13.08),
    "COUSSIN-DECO-TERRACOTTA": ("Decorative Cushion - Terracotta", "Furniture-Decor", 12, 13.08),
    "TAPIS-DECO-160X230": ("Decorative Rug - 160x230cm", "Furniture-Decor", 64.5, 70.305),
    "SUSPENSION-BLACK": ("Designer Pendant Light - Black", "Furniture-Decor", 44.5, 48.505),
    "SUSPENSION-WHITE": ("Designer Pendant Light - White", "Furniture-Decor", 44.5, 48.505),
    "MIROIR-ROND": ("Round Wall Mirror", "Furniture-Decor", 29.5, 32.155),
    "CHAISE-SCANDINAVE-GREY": ("Scandinavian Chair - Grey", "Furniture-Dining-Chairs", 39.5, 43.055),
    "CHAISE-SCANDINAVE-BLACK": ("Scandinavian Chair - Black", "Furniture-Dining-Chairs", 39.5, 43.055),
    "TABOURET-BAR-BLACK": ("Bar Stool - Black", "Furniture-Dining-Chairs", 34.5, 37.605),
    "MATELAS-140-MEDIUM": ("Memory Foam Mattress 140x190 - Medium", "Furniture-Bedroom-Beds", 174.5, 190.205),
    "MATELAS-140-FIRM": ("Memory Foam Mattress 140x190 - Firm", "Furniture-Bedroom-Beds", 174.5, 190.205),
    "TABLE-CHEVET-WHITE": ("Bedside Table - White", "Furniture-Bedroom-Storage", 29.5, 32.155),
    "CANAPE-ANGLE-GREY": ("Corner Sofa Bed 4-Seater - Grey", "Furniture-LivingRoom-Sofas", 399.5, 435.455),
    "CANAPE-DROIT-BEIGE": ("Fixed 3-Seater Sofa - Beige", "Furniture-LivingRoom-Sofas", 299.5, 326.455),
    "ARMOIRE-WHITE": ("2-Door Wardrobe - White", "Furniture-Bedroom-Storage", 149.5, 162.955),
    "LIT-DOUBLE-GREY": ("Double Bed with Headboard - Grey", "Furniture-Bedroom-Beds", 199.5, 217.455),
}


def _v(mn, pk, lt, qt, std=None):
    d = {"minimumOrderQty": mn, "quantityPerPackage": pk, "purchasingLeadTime": lt, "quantityType": qt}
    if std:
        d["standardQuantity"] = std
    return d


# Ligne Centrale ajoutée (C) ou ligne MEUBLEDESIGN mise à jour (M) — cf. 02_Product_Purchasing_Furniture
PLAN = {
    "CHAISE-SCANDINAVE-GREY": ("C", _v(2, 2, 5, "M", 2)), "CHAISE-SCANDINAVE-BLACK": ("C", _v(2, 2, 5, "M", 2)),
    "TABOURET-BAR-BLACK": ("C", _v(2, 2, 5, "M", 2)),
    "MATELAS-140-MEDIUM": ("C", _v(1, 1, 5, "A")), "MATELAS-140-FIRM": ("C", _v(1, 1, 5, "A")), "TABLE-CHEVET-WHITE": ("C", _v(1, 1, 5, "A")),
    "CANAPE-ANGLE-GREY": ("C", _v(1, 1, 21, "A")), "CANAPE-DROIT-BEIGE": ("C", _v(1, 1, 21, "A")),
    "ARMOIRE-WHITE": ("C", _v(1, 1, 21, "A")), "LIT-DOUBLE-GREY": ("C", _v(1, 1, 21, "A")),
    "COUSSIN-DECO-GREY": ("M", _v(12, 6, 10, "M", 6)), "COUSSIN-DECO-BEIGE": ("M", _v(12, 6, 10, "M", 6)),
    "COUSSIN-DECO-TERRACOTTA": ("M", _v(12, 6, 10, "M", 6)), "TAPIS-DECO-160X230": ("M", _v(2, 1, 14, "A")),
    "SUSPENSION-BLACK": ("M", _v(2, 2, 14, "M", 2)), "SUSPENSION-WHITE": ("M", _v(2, 2, 14, "M", 2)), "MIROIR-ROND": ("M", _v(2, 2, 14, "M", 2)),
}

STOCK = {"COUSSIN-DECO-GREY": 4, "COUSSIN-DECO-BEIGE": 18, "COUSSIN-DECO-TERRACOTTA": 2, "TAPIS-DECO-160X230": 1,
         "SUSPENSION-BLACK": 3, "MIROIR-ROND": 5, "CHAISE-SCANDINAVE-GREY": 2, "CHAISE-SCANDINAVE-BLACK": 8,
         "TABOURET-BAR-BLACK": 3, "MATELAS-140-MEDIUM": 1, "MATELAS-140-FIRM": 3, "CANAPE-ANGLE-GREY": 1,
         "CANAPE-DROIT-BEIGE": 1, "ARMOIRE-WHITE": 1, "LIT-DOUBLE-GREY": 1}

FOURNISSEURS = {
    "SUP-MEUBLEDESIGN": ("Meuble Design", "9 rue du Mobilier"),
    "SUP-NORDICHOME": ("Nordic Home", "12 rue de Scandinavie"),
    "SUP-CENTRALE": ("Centrale d'achat réseau", "1 avenue de la Logistique"),
}


def products() -> list:
    out = []
    for sk, (name, cat, md, nh) in PRODUITS.items():
        mode, vals = PLAN[sk]
        md_line = {"businessPartner": "SUP-MEUBLEDESIGN", "currentVendor": True, "purchaseOrderPrice": md, "isActive": True}
        if mode == "M":
            md_line.update(vals)
        else:
            md_line["purchasingLeadTime"] = 7 if vals["purchasingLeadTime"] == 5 else 28
        lines = [{"businessPartner": "SUP-NORDICHOME", "currentVendor": False, "purchaseOrderPrice": nh, "isActive": True}, md_line]
        if mode == "C":
            lines.append({"businessPartner": "SUP-CENTRALE", "currentVendor": False, "purchaseOrderPrice": md, "isActive": True, **vals})
        out.append({"searchKey": sk, "name": name, "productCategory": cat, "taxCategory": "Normal", "productPurchasing": lines})
    return out


def stock() -> list:
    return [{"warehouse": WH, "organization": "0011", "bin": "01-AVA", "product": PRODUITS[sk][0],
             "product_info": {"searchKey": sk, "name": PRODUITS[sk][0]}, "quantityOnHand": q} for sk, q in STOCK.items()]


def orders(created: list) -> list:
    t = today()
    base = [
        ("FR11-PO-0001", "SUP-MEUBLEDESIGN", t - timedelta(days=17), t - timedelta(days=7),
         [("COUSSIN-DECO-TERRACOTTA", 12, 12.0), ("SUSPENSION-WHITE", 4, 44.5)]),
        ("FR11-PO-0002", "SUP-CENTRALE", t - timedelta(days=2), t + timedelta(days=3), [("TABOURET-BAR-BLACK", 4, 34.5)]),
    ]
    out = []
    for doc, bp, od, due, ls in base:
        out.append({"id": doc, "documentNo": doc, "orderDate": od.isoformat(), "scheduledDeliveryDate": due.isoformat(),
                    "documentType": "Purchase Order", "isSale": False, "docStatus": "CO", "businessPartner": bp, "organization": "0011",
                    "lines": [{"id": f"{doc}-{i}", "lineNo": (i + 1) * 10, "product": sk, "product_info": {"searchKey": sk, "name": PRODUITS[sk][0]},
                               "orderedQuantity": q, "deliveredQuantity": 0, "netUnitPrice": p} for i, (sk, q, p) in enumerate(ls)]})
    for po in created:  # commandes importées pendant l'exécution (ImportService simulé)
        out.append({"id": po["documentNo"], "documentNo": po["documentNo"], "orderDate": po["orderDate"],
                    "scheduledDeliveryDate": po.get("scheduledDeliveryDate"), "documentType": "Purchase Order", "isSale": False,
                    "docStatus": "CO", "businessPartner": po["businessPartner"], "organization": "0011",
                    "lines": [{"id": f"{po['documentNo']}-{i}", "lineNo": l["lineNo"], "product": l["product"],
                               "product_info": {"searchKey": l["product"], "name": PRODUITS.get(l["product"], (l["product"],))[0]},
                               "orderedQuantity": l["qty"], "deliveredQuantity": 0, "netUnitPrice": l["netUnitPrice"],
                               "deliveryDate": l.get("scheduledDeliveryDate")} for i, l in enumerate(po["lines"])]})
    return out


def business_partner(sk: str) -> list:
    if sk not in FOURNISSEURS:
        return []
    name, addr = FOURNISSEURS[sk]
    return [{"searchKey": sk, "commercialName": name, "isVendor": True, "purchasePriceList": "Purchase Price List", "poPaymentTerms": "30 jours",
             "locations": [{"name": addr, "address": addr, "invoiceAddress": True, "shippingAddress": True}]}]


def vendors() -> list:
    return [{"searchKey": k, "commercialName": v[0], "isVendor": True} for k, v in FOURNISSEURS.items()]


TAX_RATES = [{"searchKey": "VAT 20%", "name": "VAT 20%", "taxCategory": "Normal", "rate": 20},
             {"searchKey": "VAT 5.5%", "name": "VAT 5.5%", "taxCategory": "Reduced", "rate": 5.5},
             {"searchKey": "VAT 0%", "name": "VAT 0%", "taxCategory": "Exempt", "rate": 0}]
