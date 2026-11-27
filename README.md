# Mini-reporting retail – environnements Event / Presales

Petit système de reporting **sans serveur et sans installation** :

- **GitHub Actions** interroge les API Openbravo *ExportService* à heures fixes ;
- les indicateurs sont **calculés et stockés au fil de l'eau en JSON** dans `docs/data/` ;
- **GitHub Pages** publie le tableau de bord (`docs/index.html`), qui lit ces JSON.

```
Openbravo ExportService ──► GitHub Actions (Python) ──► docs/data/*.json ──► GitHub Pages (tableau de bord)
```

Par défaut, tout tourne en **mode simulé** (données de démo scénarisées). On bascule sur Event en changeant une variable.

---

## Ce qui est collecté

| Tâche planifiée | Quand (heure de Paris, hiver) | Contenu |
|---|---|---|
| **Ventes du jour** (`ventes-jour.yml`) | toutes les 30 min, 8h–20h30 | `Order/byOrgOrderDateRange` par magasin → fichier du jour réécrit à chaque passage |
| **Collecte de nuit** (`nuit.yml`) | 2h15 | référentiel (produits, catégories, coûts) · clôture des ventes de la veille · **photo de stock** · achats et fournisseurs |
| **Rattrapage** (`rattrapage.yml`) | à la demande | recharge l'historique des ventes entre deux dates (option : purger les données existantes) |

> GitHub lance les tâches planifiées avec quelques minutes de retard, parfois plus aux heures chargées. Les horaires sont en UTC : ils se décalent d'une heure à l'heure d'été.

### Fichiers produits (`docs/data/`)

| Fichier | Contenu | Croissance |
|---|---|---|
| `ventes/jour/AAAA-MM-JJ.json` | détail du jour : KPI, CA et tickets par heure, vendeurs, produits, secteurs, paiements (par magasin + total) | 1 fichier / jour |
| `ventes/historique.json` | 1 ligne / jour / magasin : CA TTC/HT, tickets, panier, articles par ticket, marge, remise | +4 lignes / jour |
| `stocks/photo.json` | dernière photo : valeur et couverture par magasin × secteur, ruptures, dormants | réécrit chaque nuit |
| `stocks/historique.json` | 1 ligne / jour / magasin / secteur | +~28 lignes / jour |
| `achats/fournisseurs.json` | taux de service, ponctualité, délai moyen, retards, reste à livrer + liste des lignes en retard | réécrit chaque nuit |
| `achats/historique.json`, `achats/receptions.json` | suivi quotidien par fournisseur | +1 ligne / fournisseur / jour |
| `ref/refs.json` | référentiel produits, catégories, coûts | réécrit chaque nuit |

On ne stocke que des **agrégats** (jamais les tickets bruts) : environ 3 Mo pour 4 mois de données de démo.

---

## Mise en place (100 % navigateur, environ 15 min)

1. **Créer le dépôt** sur github.com : *New repository*, par exemple `orisha-mini-reporting`.
   - Compte gratuit : GitHub Pages ne fonctionne qu'avec un dépôt **public**. Les données sont fictives (démo) et les identifiants Openbravo restent dans les secrets.
   - Organisation GitHub Team ou Enterprise : le dépôt peut être privé.
2. **Déposer les fichiers** : *Add file › Upload files*, puis glisser le **contenu** du dossier décompressé (`collector/`, `docs/`, `.github/`, `README.md`) et *Commit changes*.
   - Si le dossier `.github` n'est pas pris en compte (dossier masqué sous Windows), créer les 3 fichiers à la main : *Add file › Create new file*, nom `.github/workflows/ventes-jour.yml`, coller le contenu, puis idem pour `nuit.yml` et `rattrapage.yml`.
3. **Autoriser les tâches à écrire** : *Settings › Actions › General › Workflow permissions* : cocher **Read and write permissions**, puis *Save*.
4. **Publier le tableau de bord** : *Settings › Pages › Build and deployment* : *Deploy from a branch*, branche `main`, dossier **`/docs`**, puis *Save*. L'URL s'affiche après environ une minute (`https://<compte>.github.io/<dépôt>/`).
5. **Premier test** : onglet *Actions*, tâche **Ventes du jour**, bouton *Run workflow*. Le badge du tableau de bord indique « données simulées ».

## Brancher sur Event (données réelles)

1. *Settings › Secrets and variables › Actions*
   - onglet **Secrets** : `OB_URL` (hôte sans `https://`, par exemple `monenv.openbravo.com/openbravo`), `OB_USER`, `OB_PASSWORD` ;
   - onglet **Variables** : `OB_MOCK` = `0`.
2. Modifier `collector/config.json` (crayon ✏️ dans GitHub) :
   - `stores[].api_name` : **nom exact** de chaque organisation magasin (le filtre `organization` des API attend le nom) ;
   - `stores[].warehouse` : **search key** de l'entrepôt du magasin ;
   - `purchase_orgs` : organisation(s) qui passent les commandes d'achat ;
   - `sales_price_list` : liste de prix portant le coût (par défaut « Sales Price List »).
3. *Actions › Rattrapage de l'historique › Run workflow* : dates `du`/`au` (par exemple les 12 derniers mois) et **purger = oui**, pour remplacer la simulation.
4. Les photos de stock et l'historique achats démarrent à la première nuit : l'API Stock ne donne que le stock actuel, l'historique se construit jour après jour.

### À vérifier sur les premières données réelles

- `Order` renvoie-t-il des commandes ? Aujourd'hui l'appel revient vide : vérifier le nom d'organisation et les droits du rôle API (*Rôle › Accès organisation*).
- `lines[].purchasePrice` est-il renseigné (coût à la vente) ? Sinon, le coût de la liste de prix est utilisé.
- Les commandes d'achat sortent-elles par `Order` (type de document contenant « Purchase ») ? Sinon, ajuster `purchase_document_keywords`.
- Heures : pour l'historique chargé a posteriori, la date de création ≠ date de vente. Le tableau de bord n'affiche alors pas la courbe horaire de ces journées (c'est voulu).

## Lancer à la main (facultatif, n'importe quel poste avec Python 3.10+)

```bash
OB_MOCK=1 python -m collector.run rattrapage --du 2026-07-01 --au 2026-11-16
OB_MOCK=1 python -m collector.run ventes
OB_MOCK=1 python -m collector.run nuit
```

## Structure

```
collector/
  config.json     magasins, organisations d'achat, seuils (surstock 90 j, fenêtre de vente 30 j)
  ob_client.py    client ExportService (authentification basique, pagination, reprises)
  mock_api.py     API simulée et scénarisée (OB_MOCK=1)
  refs.py         référentiel produits → secteur (racine de la hiérarchie de catégories), coûts
  ventes.py       agrégats ventes du jour et historique
  stocks.py       photo de stock, couverture, ruptures, dormants
  achats.py       commandes ouvertes, retards, taux de service, ponctualité, réceptions
  run.py          commandes ventes / nuit / rattrapage
docs/
  index.html      tableau de bord (charte Orisha, rafraîchissement automatique toutes les 5 min)
  vendor/         Chart.js (embarqué, sans CDN)
  data/           les JSON
.github/workflows/  les 3 tâches planifiées
```

## Réappro mini/maxi (complément franchisé — V0)

- **Seuils** : `collector/seuils/<magasin>.csv` (`produit;mini;maxi;fournisseur`, fournisseur vide = fournisseur habituel).
  Le colisage (minimum de commande, multiple) et le délai sont lus dans l'onglet **Purchasing** des fiches article Openbravo.
- **Règle** : commande si *disponible* (stock + reste à recevoir) < mini ; quantité = maxi − disponible, arrondie au colisage.
- **Import des seuils** : bouton **Importer des seuils** (CSV `produit;mini;maxi;fournisseur`, `;` ou `,`, en-têtes souples),
  contrôlé dans le navigateur (doublons, valeurs, maxi < mini) puis enregistré dans `collector/seuils/<magasin>.csv`
  et recalculé. Bouton **Seuils** pour télécharger le fichier en cours.
- **Volumétrie** : pas de lecture du catalogue complet ; fiches article lues une par une pour les seules exceptions
  (au-delà de `max_lectures_articles`, export complet) ; le fichier de propositions ne contient que les exceptions
  (+ les articles OK tant qu'ils sont moins de `max_lignes_ok`).
- **Calcul** : bouton **Recalculer** de l'onglet **Réappro franchisé** (ou Actions › *Réappro mini/maxi* › `calcul`), et chaque nuit.
- **Grille** : filtres (statut, fournisseur, famille, seuil, ruptures, retards, recherche), choix du fournisseur parmi ceux de
  l'onglet Purchasing (quantité réarrondie au colisage), quantité modifiable, bouton **Commander la sélection**.
- **Boutons** : ils lancent le workflow via l'API GitHub avec un jeton personnel *fine-grained* (dépôt unique, Actions : read & write,
  Contents : read & write) saisi une fois et conservé seulement dans le navigateur (lien « Connecter GitHub »).
- **Commande** : Actions › *Réappro mini/maxi* › `commande` → une commande d'achat par fournisseur importée dans Openbravo
  (ImportService/PurchaseOrder, statut « Booked »). `lignes_json` vide = toutes les propositions ;
  sinon `[{"produit":"TAPIS-DECO-160X230","qte":3}]` (fournisseur optionnel).
- Magasins concernés : `config.json` › `reappro.magasins` (préfixe de numérotation, entrepôt). Taxe : le champ attend le
  **taux** (« VAT 20% »), déduit de la catégorie de taxe de l'article.

## Administration des magasins

Onglet **⚙ Administration** du tableau de bord : liste des organisations Openbravo (relue chaque nuit ou avec le bouton
« Relire les magasins Openbravo », workflow `admin.yml` → `docs/data/ref/organisations.json`). Pour chaque magasin :
libellé, entrepôt, type, et les onglets où il apparaît (**Ventes**, **Stocks**, **Réappro** + préfixe de numérotation des
commandes) et sa **couleur** dans les graphiques ; les entrepôts hors magasin (dépôt central…) peuvent être masqués de
l'onglet Stocks. Un **mot de passe** (empreinte SHA-256 dans le fichier) verrouille l'onglet : c'est un verrou d'interface,
l'écriture reste protégée par le jeton GitHub. L'enregistrement écrit `docs/data/admin/magasins.json`, qui remplace `config.json` (stores, reappro.magasins)
pour les collectes suivantes ; la page propose de lancer la collecte de nuit tout de suite.

## Assortiment (réappro)

Le réappro s'appuie sur l'assortiment du magasin (`Product/byStore`) : seuls ses articles sont commandables ; un article
de l'assortiment sans seuil apparaît « Sans seuil » (commande manuelle possible) ; un seuil sur un article hors
assortiment est signalé en anomalie et ignoré. Un magasin sans fichier de seuils fonctionne (tout est « sans seuil »).
