# Étape : carte 3D « tech » et thème DIHAL pour le tableau de bord des écoutants

> **Mise à jour** : la météo a ensuite été retirée du panneau (voir [navigation-langue-scenarios.md](navigation-langue-scenarios.md)).

Suite de [carte-meteo-hebergements.md](carte-meteo-hebergements.md). Cette étape donne au tableau de bord des
écoutants (`dashboard.py`) le design de la page DIHAL (liste des départements autour de l'œil MIKI), et remplace
la carte Leaflet 2D par une **carte 3D MapLibre** inspirée des images du dossier `references/`. Le but est un effet
« WOW » pendant la démonstration.

La logique de l'étape précédente ne change pas : synchronisation avec la fiche en direct, géocodage, météo, rayon,
hébergements fictifs, mode démo. L'assistant vocal n'est pas modifié, et aucun centre n'est choisi
automatiquement : le trajet n'apparaît que lorsque l'écoutant clique sur un centre.

## Choix validés avec l'équipe

| Question | Choix |
|---|---|
| Moteur de carte | 3D MapLibre GL (à essayer d'abord) |
| Ligne vers le centre choisi | Itinéraire piéton IGN, ligne droite si le service ne répond pas |
| Œil MIKI | Sur la carte, tant que la localisation n'est pas connue |
| Thème | Sombre uniquement (l'impression des dossiers reste en clair) |

## Inspirations

- **Page DIHAL** (`static/dihal.html`) : fond bleu nuit `#061113`, cartes en verre légèrement teinté, turquoise
  `#5fd4c4` pour l'interface, or `#f5c35b` pour l'élément actif, œil animé (ondes, clignement, pupille qui suit
  la souris).
- **`references/14be83…jpg`** : bâtiments 3D sombres à arêtes claires, marqueurs reliés à leur étiquette par une
  tige, trajet lumineux en pointillés.
- **`references/jpeg`** : tableau de bord cartographique sombre, étiquettes d'information reliées aux marqueurs.

## Ce qui change à l'écran

### Tableau de bord (`dashboard.py`)

- Palette DIHAL partout : en-tête « **MIKI** · Écoutants du 115 », voyant « direct », titres en petites capitales
  turquoise, fiches en verre, bord gauche à la couleur de la priorité, fiche sélectionnée cerclée d'or.
- Priorités recolorées pour le fond sombre (5 rouge, 4 orange, 3 or, 2 turquoise, 1 gris).
- Bandeau « MODE DÉMO » en pilule dorée.
- Thème clair supprimé. L'impression d'un dossier repasse en clair.

### Carte 3D (`static/carte/carte3d.mjs`)

- **Fond** : tuiles vectorielles OpenFreeMap (sans clé), style sombre recoloré aux teintes DIHAL. Eau bleu-vert,
  rues turquoise plus ou moins intenses selon leur importance, libellés discrets, aucun pictogramme.
- **Bâtiments en 3D** : hauteurs réelles issues d'OpenStreetMap. Plus un bâtiment est haut, plus il est clair, avec
  un contour turquoise au sol (effet « fil de fer » de la référence).
- **Faisceaux lumineux** : une colonne rouge de 420 m sur la personne, une colonne verte sur chaque centre avec
  places (hauteur selon le nombre de places fictives), une colonne grise sur les centres complets. Le centre
  choisi passe en or.
- **Zone de recherche** : disque turquoise au sol, bord pointillé lumineux, et mur holographique translucide qui
  marque la limite du rayon.
- **Survol cinématique en deux temps** à chaque nouvelle localisation :
  1. plongée en gros plan 3D sur la personne (zoom 15,9, inclinaison 64°) ;
  2. pause de 1,8 s ;
  3. recul jusqu'à la vue du rayon (inclinaison 55°).

  Ensuite, orbite lente pendant 25 s, qui s'arrête dès que l'écoutant manipule la carte.
- **Étiquettes HUD** (texte HTML) reliées au marqueur par une tige :
  - la personne : « LIEU DÉCLARÉ », avec un radar rouge pulsé ;
  - les centres : « HÉBERGEMENT FICTIF · 500 M », le nom, les places fictives. Trois centres ont une étiquette
    permanente (le plus proche et les deux plus proches avec places) ; les autres apparaissent au survol.
- **Centre choisi**, par clic dans la liste ou sur la carte :
  - itinéraire piéton IGN tracé en or, avec un halo et des points lumineux qui avancent ;
  - la caméra cadre la personne et le centre ;
  - l'étiquette devient une fiche détaillée : type, public, PMR, secteur, mention « Donnée de démonstration, non
    réelle » et « 17 MIN À PIED · 1,0 KM ». Elle remplace la popup, qui masquait le trajet.
- **Bandeau de coordonnées** en haut à gauche : position, rayon, nombre de centres fictifs dont ceux avec places.
- **Légende** : personne, places fictives, complet (fictif), trajet à pied.

### Attente et résolution (`static/carte/oeil.mjs`)

- Sans localisation : grand œil MIKI animé (repris de la page DIHAL) au centre de la carte, avec « En attente de la
  localisation » et « MIKI écoute l'appel : la carte se placera dès que le lieu sera dit ». Derrière, Paris en vue
  inclinée.
- Pendant le géocodage : balayage radar, la pupille scrute de gauche à droite, « Localisation en cours de
  résolution… ».
- Dès que le lieu est trouvé, le voile s'efface en fondu et le survol commence.

## Fichiers

| Fichier | Statut | Rôle |
|---|---|---|
| `static/carte/carte3d.mjs` | créé | Carte MapLibre : recoloration du style, bâtiments 3D, faisceaux, zone, trajet animé, étiquettes, caméra |
| `static/carte/oeil.mjs` | créé | Œil MIKI en composant réutilisable (clignement, pupille, mode « cherche ») |
| `static/carte/panneau.mjs` | réécrit | Même logique qu'avant, branchée sur la carte 3D ; ajout du choix d'un centre et de l'itinéraire |
| `static/carte/panneau.css` | réécrit | Styles DIHAL du panneau, étiquettes, radar, voile et œil |
| `static/carte/geo.mjs` | complété | `itineraire()` (IGN, avec cache), `ligneDroite()`, `formatDuree()`, `cercle()` |
| `dashboard.py` | modifié | Thème DIHAL sombre, MapLibre à la place de Leaflet |
| `tests/geo.test.mjs` | complété | 11 tests, dont les cercles et la ligne de secours |

## Dépendances et services

- **MapLibre GL JS 5.24.0**, chargé depuis le CDN unpkg avec vérification d'intégrité (SRI sha384). Leaflet est
  retiré. Aucun paquet npm ni Python ajouté.
- **OpenFreeMap** (`tiles.openfreemap.org`) : style sombre et tuiles vectorielles, sans clé, attribution
  OpenMapTiles et OpenStreetMap affichée.
- **IGN Géoplateforme, itinéraire** : `https://data.geopf.fr/navigation/itineraire`, profil piéton, ressource
  `bdtopo-osrm`. Seules les coordonnées de départ et d'arrivée sont envoyées.
- Géocodage IGN et météo Open-Meteo : inchangés.

## Performances

- **Machine de démo** (Intel HD Graphics 620, Chrome sur Vulkan) : **43 à 58 images par seconde** pendant le
  survol, mesuré dans Chrome sur le vrai GPU.
- **Aucun `backdrop-filter` au-dessus de la carte** : recalculé à chaque image WebGL, il divisait le débit par dix
  (de 18,7 à 2 images par seconde mesurées).
- **L'orbite s'arrête au bout de 25 s** : une carte immobile ne consomme plus rien.
- **Garde-fou sans GPU** : si le débit reste sous environ 12 images par seconde, l'orbite et les pointillés animés
  se coupent seuls (message dans la console).
- **Sans WebGL ou sans MapLibre** : message « Carte 3D indisponible ». La météo, la liste et la synchronisation
  continuent de fonctionner.
- `prefers-reduced-motion` : vols instantanés, aucune orbite ni animation continue.

**Conseil de démo** : lancer Chrome avec l'accélération matérielle active (`chrome://gpu`). En rendu logiciel
(SwiftShader), une image prend environ 3 s.

## Lancer

```bash
MIKI_DEMO=1 python dashboard.py      # http://localhost:8115, avec les scénarios A, B, C
node --test tests/                   # 11 tests unitaires
```

Pour inspecter la carte depuis la console du navigateur : `document.querySelector(".pc-carte").carte3d`
(l'objet `maplibregl.Map`).

## Tests effectués

- **Tests unitaires** : 11/11.
- **Itinéraire IGN réel** : gare de Lyon → Bastille, 1 240 m, 22 min, 79 points ; Gare du Nord → Gare de l'Est,
  670 m, 11 min.
- **Test de synchronisation dans Chrome (sur le vrai GPU)**, avec une fausse fiche « en cours » supprimée
  ensuite. Toutes les étapes arrivent à temps :
  - deux changements de lieu rapprochés : seul le dernier est retenu (Gare du Nord) ;
  - le rayon change le nombre de centres (6, 13 puis 16) ;
  - clic sur un centre : itinéraire et fiche détaillée ;
  - lieu non reconnu, correction manuelle, fiche sans lieu ;
  - scénarios A, B, C, puis retour à la fiche réelle.
- **Déroulé de la caméra** mesuré seconde par seconde : plongée au zoom 15,9 entre 5 et 9 s, recul jusqu'au zoom du
  rayon à 13 s.
- **Captures vérifiées** :
  - œil en attente ;
  - balayage pendant la résolution ;
  - gros plan 3D (faisceau rouge, bâtiments) ;
  - vue du rayon ;
  - trajet doré vers Bastille ;
  - affichage mobile.
- **Non testé** : un vrai appel téléphonique de bout en bout (pipeline vocal inchangé).

## Limites connues

- La carte a besoin d'internet (CDN MapLibre, OpenFreeMap, IGN, Open-Meteo).
- Les libellés de la carte (quartiers, rues) sont fournis en anglais par les tuiles OpenFreeMap (« 12TH
  ARRONDISSEMENT »). Ils pourraient être passés en français en choisissant le champ `name:fr` des libellés.
- Avec beaucoup de centres proches les uns des autres, des étiquettes peuvent se chevaucher. Seules trois sont
  permanentes, les autres apparaissent au survol.
- Le rayon par défaut passe de 5 à 2 km : c'est la vue où la 3D se lit le mieux. 5 et 10 km restent
  disponibles.
- Pendant les tests automatisés, un dossier de test (`dossiers/test-carte-claude.json`) a été créé une fois par
  l'action « Créer le dossier », sans que la cause soit identifiée. Il ne s'est pas reproduit sur deux passages
  complets et il a été supprimé.

## Scénario de démonstration (60 secondes)

1. **0 s** : `MIKI_DEMO=1 python dashboard.py`, ouvrir http://localhost:8115. La carte affiche l'œil MIKI : « En
   attente de la localisation ».
2. **5 s** : cliquer « A · Gare de Lyon ». La fiche démo arrive, puis le lieu est dit. Balayage radar, puis le
   voile s'efface et la caméra **plonge en 3D** sur la gare (faisceau rouge, bâtiments) avant de reculer pour
   montrer le rayon de 2 km et les centres fictifs.
3. **20 s** : cliquer « Centre d'accueil Démo Bastille » dans la liste. Le trajet à pied s'allume en or, avec
   « 17 MIN À PIED · 1,0 KM » et la fiche détaillée sur la carte.
4. **35 s** : cliquer « C · Gare du Nord ». Retour de l'œil, nouvelle plongée, autres centres, météo mise à jour.
5. **50 s** : passer à 5 km pour montrer le mur holographique du rayon, puis conclure : l'IA écoute et localise,
   c'est l'écoutant qui choisit l'hébergement. Les places sont fictives.
