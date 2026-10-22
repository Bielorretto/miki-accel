# Étape : panneau à la sélection, catégories de places, filtre région DIHAL

Suite de [carte-3d-theme-dihal.md](carte-3d-theme-dihal.md).

## 1. Carte, météo et hébergements seulement sur une fiche sélectionnée (`dashboard.py`)

- Au chargement, aucune fiche n'est sélectionnée : le tableau de bord n'affiche que les fiches, en grille sur toute
  la largeur, avec l'aide « Sélectionnez une fiche pour afficher le lieu, la météo et les hébergements ».
- Un clic sur une fiche ouvre le panneau à droite (disposition 40/60). Cliquer de nouveau sur la même fiche ne le
  referme pas, pour pouvoir la lire tranquillement.
- Le panneau se ferme avec « Fermer ✕ » ou la touche Échap. Il se ferme aussi si la fiche disparaît de la liste.
- Le panneau et la carte 3D ne sont créés qu'à la première sélection : ni WebGL ni tuiles tant que personne n'a
  besoin de la carte.
- Un scénario sélectionne sa fiche automatiquement, puisqu'on vient de le lancer.

## 2. Catégories de places : femmes, familles avec enfants, hommes

**Données** (`static/carte/demo_hebergements.json`) : chaque centre indique les catégories qu'il accueille, avec
ses places pour chacune.

```json
"places": {"femmes": {"total": 10, "disponibles": 2}, "familles": {"total": 12, "disponibles": 1}, "hommes": {"total": 18, "disponibles": 0}}
```

Certains centres n'accueillent qu'une catégorie : halte de nuit pour hommes, maison pour familles, halte pour
femmes, etc. Les données restent fictives (`"isDemo": true` sur chaque centre).

**Logique** (`static/carte/geo.mjs`) :
- `CATEGORIES` : la liste des trois catégories.
- `places(h, categorie)` : places d'un centre pour une catégorie, ou le total de toutes ses catégories.
- `hebergementsProches(position, liste, rayon, categorie)` : avec une catégorie, ne garde que les centres qui
  l'accueillent et compte uniquement ses places.
- `categorieDeFiche(fiche)` : catégorie présélectionnée d'après la fiche. Enfants (`nb_enfants`, `ages_enfants`,
  `criteres.enfants_presents`) → familles ; sinon femme ou grossesse → femmes ; homme → hommes ; sinon toutes.

**Affichage** (`panneau.mjs`, `carte3d.mjs`) :
- Boutons « Places : Toutes · Femmes · Familles avec enfants · Hommes ». La catégorie de la fiche est
  présélectionnée et signalée par « d'après la fiche ». Si la fiche change pendant l'appel (enfants mentionnés),
  la catégorie suit, tant que l'écoutant n'en a pas choisi une autre.
- La couleur des marqueurs et la hauteur des faisceaux dépendent des places de la catégorie choisie.
- Chaque centre de la liste affiche ses places par catégorie, par exemple « Femmes 2/10 · Familles avec enfants
  1/12 · Hommes 0/18 ». La catégorie visée est cerclée d'or.
- L'étiquette du centre choisi sur la carte détaille aussi les places par catégorie.
- Le bandeau de la carte indique « Centres · familles avec enfants : 3 · 3 avec places ».
- Si l'écoutant change de catégorie et que le centre choisi ne l'accueille pas, le trajet est retiré.

**Scénarios** : chacun a maintenant un profil différent, pour montrer la présélection.

| Scénario | Lieu | Appelant | Catégorie présélectionnée |
|---|---|---|---|
| A | Gare de Lyon | Karim, 42 ans, homme | Hommes |
| B | République | Amina, 31 ans, deux enfants | Familles avec enfants |
| C | Gare du Nord | Julie, 24 ans, femme | Femmes |

## 3. Retrait des mentions « démo », « hackathon », « fictif », « simulation »

- Retirés de l'écran : le bandeau « Hébergements et disponibilités fictifs — démonstration hackathon », « DÉMO ·
  fiche fictive », « MODE DÉMO » (devenu « SCÉNARIOS »), « places fictives », « complet (fictif) », « Hébergement
  fictif », « Donnée de démonstration, non réelle », et les mots « Démo » dans les noms des centres.
- **Météo simulée supprimée** : sans étiquette, de fausses valeurs passeraient pour la vraie météo. Si
  Open-Meteo ne répond pas, le panneau affiche « Service météo momentanément indisponible », en démo comme hors
  démo.
- Les coordonnées de secours des scénarios s'affichent comme « position approximative ».
- Le caractère fictif reste écrit **dans les données et le code** : `isDemo: true` dans le JSON, commentaires de
  `panneau.mjs` et `carte3d.mjs`. À retenir si ces écrans servent un jour hors démonstration.
- Laissé tel quel : le filtre « Scénarios simulés » de la page DIHAL. C'est une source de données existante des
  statistiques, distincte des appels réels, pas une étiquette de démo.

## 4. Filtre région sur la page DIHAL (`static/dihal.html`)

- Liste « Toutes les régions » dans l'en-tête, à côté de la recherche : les 18 régions (13 métropolitaines et 5
  d'outre-mer).
- Correspondance département → région écrite dans la page (`REGIONS`, régions de 2016). Elle couvre les 101
  départements, chacun une seule fois ; la base n'est pas modifiée.
- Région choisie :
  - ses départements s'allument (bord turquoise lumineux) ;
  - leurs liens vers l'œil MIKI s'éclairent ;
  - les autres départements passent à 18 % d'opacité ;
  - un résumé apparaît en bas : « Île-de-France · 8 départements · 6 couverts par MIKI · 708 appels ».
- Le filtre se combine avec la recherche et avec le choix de la source (Tout, Appels réels, Scénarios simulés). La
  surbrillance est réappliquée après chaque rechargement des départements et chaque redimensionnement.
- Le total d'appels du résumé ne compte que les valeurs publiées : les petits effectifs masqués (moins de 5) n'y
  entrent pas.

## Tests

- **Tests unitaires** : 13/13. Nouveaux tests : places par catégorie, filtre par catégorie, cohérence du jeu de
  données (chaque catégorie a des centres avec et sans places), déduction de la catégorie depuis la fiche.
- **Tableau de bord dans Chrome (GPU)** :
  - pas de panneau au départ ;
  - clic sur une fiche d'homme : panneau, carte 3D et « Hommes · d'après la fiche » ;
  - passage manuel à « Femmes » ;
  - scénario B : « Familles avec enfants · d'après la fiche » ;
  - « Fermer » masque le panneau ;
  - aucun texte visible contenant « fictif », « démo », « hackathon » ou « simulation ».
- **Page DIHAL** : faute de base de données sur cette machine (pas de Docker), testée avec un serveur de test qui
  sert la vraie page et de fausses réponses d'API. Île-de-France : 8 départements en surbrillance, 93 estompés,
  8 liens éclairés et le résumé ; Occitanie : 13 départements ; « Toutes les régions » rétablit l'affichage.
  **Non testé avec la vraie base PostgreSQL.**
