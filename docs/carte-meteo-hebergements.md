# Étape : carte interactive, météo et hébergements dans le tableau de bord des écoutants

> **Mise à jour** : la carte Leaflet décrite ici a ensuite été remplacée par une carte 3D MapLibre au thème DIHAL.
> Voir [carte-3d-theme-dihal.md](carte-3d-theme-dihal.md). La logique décrite ci-dessous (synchronisation,
> géocodage, météo, hébergements, mode démo) reste valable.

Branche : `frontendAdVlad`. Cette étape ajoute au tableau de bord des écoutants (`dashboard.py`) un panneau qui suit
la fiche remplie en direct pendant l'appel : lieu déclaré sur une carte, météo réelle du lieu, hébergements
**fictifs** à proximité.

L'assistant vocal n'a pas changé. Il recueille les informations, remplit la fiche, classe l'urgence et attribue
le dossier. Il ne recherche, ne recommande et n'attribue **jamais** d'hébergement : seul l'écoutant consulte la
carte et choisit.

## Architecture existante (constatée avant l'intégration)

- **Backend** : Python. L'agent vocal LiveKit (`agent.py`) s'appuie sur `suivi.py` pour écrire dans PostgreSQL et
  Valkey. `serveur.py` (FastAPI) sert le tableau de bord DIHAL et la file `/ecoutants`.
- **Frontend** : HTML et JavaScript sans framework ni bundler. Pas de React, pas de npm.
- **Formulaire en direct** : `dashboard.py` (port 8115). À chaque analyse, l'agent (`Thinker.publish()`) réécrit
  `sessions/en_cours/{id}.json`. La page relit `/api/appels` toutes les secondes. La localisation arrive donc
  **pendant** l'appel, pas à la fin.
- **Champs de lieu dans la fiche** : `localisation` (texte libre, souvent en anglais, par exemple « near the Gare
  de Lyon train station ») et `commune`.

**Choix d'intégration** : le panneau se branche sur ce rafraîchissement existant. Aucun second formulaire, aucune
nouvelle infrastructure temps réel, aucune modification du pipeline vocal ni de la base.

## Fichiers

| Fichier | Statut | Rôle |
|---|---|---|
| `dashboard.py` | modifié | Disposition 40/60, choix de la fiche suivie par la carte (clic sur une fiche), mode démo, envoi des fichiers `/carte/*` (liste fermée : `.mjs`, `.css`, `.json` de `static/carte/`) |
| `static/carte/geo.mjs` | créé | Appels API et calculs, sans affichage : géocodage IGN, météo Open-Meteo, distances (Haversine), proximité, caches |
| `static/carte/panneau.mjs` | créé | Affichage : carte Leaflet, carte météo, liste des hébergements, rayon, correction du lieu |
| `static/carte/panneau.css` | créé | Styles, qui reprennent les couleurs du tableau de bord (clair et sombre) |
| `static/carte/demo_hebergements.json` | créé | 16 centres fictifs dans Paris, tous `isDemo: true` |
| `tests/geo.test.mjs` | créé | 9 tests Node (`node:test`), sans réseau |

Fichiers **non modifiés** : `agent.py`, `suivi.py`, `priorite.py`, `prompts.py`, `serveur.py`, `base.py`, `db/`.

## Dépendances

- **Leaflet 1.9.4**, chargé depuis le CDN unpkg avec vérification d'intégrité (SRI). Aucun paquet npm ni Python
  n'a été ajouté.
- **Tuiles OpenStreetMap**, avec l'attribution affichée sur la carte.
- **API externes sans clé**, appelées directement depuis le navigateur (elles acceptent ces appels, CORS vérifié) :
  - géocodage IGN Géoplateforme : `https://data.geopf.fr/geocodage/search` ;
  - météo Open-Meteo : `https://api.open-meteo.com/v1/forecast`.
- **Données personnelles** : seuls le texte du lieu et la commune partent vers l'IGN, seules les coordonnées
  partent vers Open-Meteo. Jamais de nom, d'âge ni de récit.

## Fonctionnement

### Synchronisation avec la fiche en direct

1. À chaque rafraîchissement (toutes les secondes), le tableau de bord transmet la fiche suivie au panneau
   (`panneauCarte.suivre({id, localisation, commune})`).
2. Le panneau ne fait rien tant que le lieu n'a pas changé (comparaison sur le texte normalisé). La carte ne
   provoque donc pas de boucle de mises à jour.
3. Quand le lieu change, le panneau attend 700 ms avant de géocoder, puis recentre la carte, charge la météo et
   recalcule les hébergements proches.
4. Si plusieurs lieux arrivent coup sur coup, seule la réponse du dernier est prise en compte (les requêtes
   précédentes sont annulées).
5. Le panneau n'écrit jamais dans la fiche. Une correction faite par l'écoutant reste locale à son écran, et
   s'efface dès que l'appelant précise un nouveau lieu.

### Géocodage

- Le panneau interroge à la fois les adresses et les lieux-dits (gares, places…) de l'IGN, puis classe lui-même les
  résultats. Le classement tient compte des mots du lieu retrouvés dans le résultat, de la ville et de la note IGN.
  C'était nécessaire : pour « Gare de Lyon, Paris », l'IGN renvoyait d'abord « Rue de Lyon » et la gare Lyon
  Part-Dieu.
- Les tournures anglaises (« near the », « outside », « train station ») sont retirées avant l'envoi à l'IGN.
- Un numéro de rue présent dans la requête l'emporte sur la rue seule.
- Entre deux lieux de même nom, le plus proche de la dernière position connue est retenu (Paris par défaut).
- Chaque résultat porte une étiquette :
  - **précise** ;
  - **approximative** : par exemple quand seule la commune est reconnue ;
  - **ambiguë, à vérifier** : d'autres lieux crédibles existent à plus de 1,5 km. Ils sont proposés en boutons
    cliquables.
- L'écoutant peut corriger le lieu dans un champ de saisie.
- Les résultats sont gardés en cache pour la session.
- En cas d'erreur réseau, la dernière position valide reste affichée et le panneau réessaie seul au bout de
  15 s. Le formulaire n'est jamais bloqué.

### Carte

- Épingle rouge pour le lieu déclaré.
- Cercle du rayon de recherche : 2, 5 ou 10 km (5 km par défaut).
- Pastille **verte** : places fictives disponibles. Pastille **grise** : complet (fictif).
- Une popup par centre : nom, type, public, secteur, distance à vol d'oiseau, places fictives sur la capacité,
  accessibilité PMR, mention « Donnée de démonstration, non réelle ».
- Légende sur la carte et déplacement animé lors d'un changement de lieu.
- Sans lieu connu : voile « En attente de la localisation » et **aucun marqueur** (aucune position inventée).

### Météo

- Paramètres envoyés :
  - `current=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m` ;
  - `hourly=temperature_2m,precipitation_probability` ;
  - `forecast_days=2`, `timezone=Europe/Paris`.
- Affichage : température, ressenti, conditions avec une icône (codes WMO), précipitations, vent, humidité,
  prévision des 8 prochaines heures.
- Cache de 10 minutes par zone d'environ 1 km.
- Un squelette s'affiche pendant le chargement, et un message discret si le service est indisponible.
- **La météo ne modifie jamais le niveau d'urgence de la fiche.**

### Hébergements fictifs

- 16 centres répartis dans les quartiers de Paris : Bastille, Quai de la Rapée, Bercy, Daumesnil, Nation,
  République, Canal Saint-Martin, Oberkampf, Gare de l'Est, La Chapelle, Barbès, Belleville, Place d'Italie,
  Montparnasse, Batignolles, Les Halles.
- Champs de chaque centre : `id`, `name`, `address`, `latitude`, `longitude`, `type`, `public`, `capacity`,
  `availablePlaces`, `accessible`, `isDemo`.
- Sélection : distance à vol d'oiseau (formule de Haversine), filtre sur le rayon choisi, tri par distance
  croissante.
- Liste sous la carte : un clic sur un centre recentre la carte et ouvre sa popup.
- Le bandeau « Hébergements et disponibilités fictifs — démonstration hackathon » est toujours visible.

### Mode démonstration

- Activation : `MIKI_DEMO=1` (ou `?demo=1` dans l'URL).
- Trois scénarios, joués dans la page uniquement : rien n'est écrit dans `sessions/` ni dans `dossiers/`, aucune
  vraie fiche n'est écrasée.
  - **A** : Gare de Lyon ;
  - **B** : Place de la République ;
  - **C** : Gare du Nord.
- La fiche démo arrive d'abord sans lieu, puis le lieu apparaît 1,5 s plus tard, comme pendant un vrai appel.
- Les scénarios passent par les mêmes étapes que les vraies fiches (géocodage, météo, carte).
- Repli en cas de panne des API, uniquement en mode démo et toujours étiqueté :
  - coordonnées fixes, marquées « coordonnées de secours (démo) » ;
  - météo marquée « Météo simulée (démo) ».

### Disposition

- **Ordinateur** : fiches à gauche (environ 40 %), panneau à droite (environ 60 %). Le panneau reste visible
  pendant le défilement.
- **Moins de 1000 px de large** : disposition verticale, panneau en tête.
- **Impression d'un dossier** : le panneau est masqué.

## Lancer

```bash
python dashboard.py                  # http://localhost:8115
MIKI_DEMO=1 python dashboard.py      # avec les scénarios A, B, C
node --test tests/                   # tests unitaires (Node 20+)
```

## Tests effectués

- **Tests unitaires** : 9/9. Ils couvrent les distances, le filtre par rayon et le tri, le contrôle du jeu fictif,
  la requête de lieu, le choix du résultat de géocodage (gare contre rue, homonymes, numéro de rue, commune
  seule, aucun résultat) et la lecture de la réponse météo.
- **API réelles depuis Node** : Gare de Lyon, Gare du Nord, Place de la République et 15 rue de Rivoli (avec
  Paris) sont tous résolus avec l'étiquette « précise ». « blabla xyz » ne donne aucun résultat.
- **Dans Chrome headless (piloté par CDP)**, avec une fausse fiche « en cours » supprimée ensuite :
  - deux changements de lieu rapprochés : seul le dernier est retenu (Gare du Nord) ;
  - le rayon change le nombre d'hébergements (6, 13 puis 16) ;
  - popup au clic dans la liste, lieu non reconnu (position précédente conservée), correction manuelle, fiche
    sans lieu ;
  - scénarios A, B, C, et retrait de la fiche démo ;
  - IGN et Open-Meteo bloqués : en démo, repli étiqueté ; hors démo, « géocodage indisponible » sans position
    inventée ;
  - Open-Meteo seul bloqué hors démo : « Service météo momentanément indisponible » ;
  - affichage mobile, aucune erreur JavaScript.
- **Non testé** : un vrai appel téléphonique de bout en bout. Le pipeline vocal n'a pas été modifié, et le fichier
  lu par le tableau de bord n'a pas changé de format.

## Limites connues

- La page `/ecoutants` de `serveur.py` (file d'attente sur PostgreSQL et Valkey) n'a pas de carte. Seul
  `dashboard.py`, qui porte le formulaire en direct, a été intégré.
- La carte a besoin d'internet (CDN Leaflet, tuiles OSM, API). Les règles d'utilisation des tuiles OSM conviennent
  à une démo, pas à une mise en production.
- Sans commune, un nom courant reste ambigu : « Place de la République » seule tombe sur Montreuil, signalée
  « ambiguë » avec les autres lieux à choisir. Dès que la fiche indique la commune, le résultat est correct.
- Si seule la commune est connue, la carte se centre sur elle avec l'étiquette « approximative ».
- Les hébergements et leurs disponibilités sont **entièrement fictifs**. Il n'y a aucune intégration avec le SI
  SIAO.

## Scénario de démonstration (60 secondes)

1. **0 s** : lancer `MIKI_DEMO=1 python dashboard.py` et ouvrir http://localhost:8115. Expliquer : à gauche la
   fiche remplie en direct par l'IA, à droite le panneau de l'écoutant.
2. **10 s** : cliquer « A · Gare de Lyon ». La fiche démo apparaît avec « En attente de la localisation », puis le
   lieu s'inscrit, la carte se déplace, l'épingle rouge et la météo réelle apparaissent.
3. **25 s** : passer de 5 à 2 km, puis cliquer sur « Halte de nuit Démo Quai de la Rapée » (complet, en gris) et sur
   « Centre d'accueil Démo Bastille » (3 places fictives). Montrer la popup.
4. **40 s** : cliquer « C · Gare du Nord ». Carte, météo et liste changent. Insister sur le bandeau « Hébergements et
   disponibilités fictifs ».
5. **50 s** : saisir « 15 rue de Rivoli, Paris » dans le champ de correction et conclure : l'IA collecte et classe,
   c'est l'humain qui choisit l'hébergement.
