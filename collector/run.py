"""Point d'entrée du collecteur.

  python -m collector.run ventes [--date AAAA-MM-JJ]       ventes du jour (relancé toutes les 30 min)
  python -m collector.run nuit [--date AAAA-MM-JJ]         référentiel + clôture des ventes de la veille + stocks + achats
  python -m collector.run diagnostic                        teste les API réelles et indique les valeurs de config.json
  python -m collector.run rattrapage --du AAAA-MM-JJ --au AAAA-MM-JJ
                                                           recharge l'historique des ventes (et, en simulation,
                                                           des photos de stock / achats pour la démo)
"""
import argparse
import time
from datetime import timedelta

from . import achats, refs, stocks, ventes
from .ob_client import OBClient
from .store import days_between, now_local, parse_day, today, write_meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("commande", choices=["ventes", "nuit", "rattrapage", "diagnostic"])
    ap.add_argument("--date")
    ap.add_argument("--du")
    ap.add_argument("--au")
    a = ap.parse_args()
    t0 = time.time()
    client = OBClient()
    now_iso = now_local().isoformat(timespec="seconds")
    day = parse_day(a.date) if a.date else today()

    if a.commande == "diagnostic":
        from . import diagnostic
        diagnostic.main(client)
        return

    if a.commande == "ventes":
        rf = refs.load(client)
        n = ventes.collect(client, rf, day, day, now_iso)
        print(f"Ventes {day} : {n} commandes")

    elif a.commande == "nuit":
        rf = refs.build(client)
        veille = day - timedelta(days=1)
        n = ventes.collect(client, rf, veille, veille, now_iso)
        print(f"Clôture ventes {veille} : {n} commandes")
        stocks.collect(client, rf, day, now_iso)
        print("Photo de stock enregistrée")
        achats.collect(client, rf, day, now_iso)
        print("Achats / fournisseurs mis à jour")

    else:
        if not (a.du and a.au):
            raise SystemExit("--du et --au sont obligatoires pour le rattrapage")
        d1, d2 = parse_day(a.du), parse_day(a.au)
        rf = refs.build(client)
        n = ventes.collect(client, rf, d1, d2, now_iso)
        print(f"Rattrapage ventes {d1} → {d2} : {n} commandes")
        if client.mock:
            for d in days_between(d1, d2):
                stocks.collect(client, rf, d, now_iso)
                if d.weekday() == 0 or d == d2:
                    achats.collect(client, rf, d, now_iso)
            print("Simulation : photos de stock et historique achats générés")
        else:
            # Données réelles : pas d'historique de stock possible, mais on reprend la photo du jour
            # et les achats tout de suite (sinon, après une purge, l'onglet reste vide jusqu'à la nuit).
            stocks.collect(client, rf, today(), now_iso)
            achats.collect(client, rf, today(), now_iso)
            print("Photo de stock et achats du jour enregistrés")

    write_meta(client.source)
    print(f"Terminé en {time.time() - t0:.1f} s ({client.calls} appels API)")


if __name__ == "__main__":
    main()
