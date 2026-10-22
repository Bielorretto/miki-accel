# Étape : fiche en français, langue de l'appel, navigation entre les vues, scénarios dans la liste

Suite de [selection-categories-regions.md](selection-categories-regions.md).

## 1. Fiche toujours en français (`prompts.py`)

Le prompt du « penseur » (`THINKER_PROMPT`), le LLM qui remplit la fiche pendant l'appel, demandait d'écrire les
valeurs en anglais. Il demande maintenant de les écrire **en français, quelle que soit la langue de l'appelant**,
avec une traduction fidèle (noms et lieux gardés tels qu'ils ont été dits). Le récit est en français, et
« none » devient « aucun ».

- **Inchangé** : la `consigne` pour MIKI (toujours en anglais), donc la voix et la conduite de l'appel ; les clés
  du JSON ; les codes (`cause`, `sexe`, `criteres`) ; le calcul de priorité.
- **Effet de bord utile** : la commune et le lieu arrivent en français, ce qui correspond mieux à la table des
  communes (recherche du département dans `suivi.py`) et au géocodage IGN.
- **Non testé avec un vrai appel** : vérifier sur un appel en anglais que la fiche sort bien en français.

## 2. Langue de l'appel sur la fiche

- `agent.py` : la fiche publiée pour le tableau de bord contient `"langue"`, la langue que MIKI transcrit
  (`STT_LANGUAGE`, la même que celle déjà enregistrée en base par `suivi.py`).
- `dashboard.py` : nouvelle ligne « Langue de l'appel » en tête de fiche, en toutes lettres grâce à la table
  `LANGUES` de `stats.py` (en → Anglais, ar → Arabe…).
- La transcription garde la langue d'origine, en `dir="auto"` pour que l'arabe s'affiche de droite à gauche.
- **Limite** : la langue est celle configurée pour la transcription, pas détectée dans la conversation. Un
  appelant qui changerait de langue en cours d'appel ne serait pas signalé.

## 3. Barre de navigation entre les deux vues

- **Les deux vues** :
  - **Écoutants 115** : `dashboard.py`, port 8115 ;
  - **Tableau de bord DIHAL** : `serveur.py`, port 8000, page de l'œil et des départements.
- La même barre en pilules apparaît dans l'en-tête des deux pages, onglet actif en surbrillance turquoise.
- **Adresses** : par défaut, même machine et ports par défaut. Côté écoutants, `DIHAL_URL` permet d'en donner une
  autre (`DIHAL_URL=https://… python dashboard.py`). Côté DIHAL, le lien vise le port 8115 de la même machine.
- **En-tête DIHAL** : il reste sur une ligne. La mention « lecture seule · données anonymes » est masquée sous
  1 750 px de large, et les chiffres globaux sous 1 450 px.

## 4. Les trois scénarios directement dans la liste

La barre « SCÉNARIOS A · B · C » de l'en-tête est supprimée. Avec `MIKI_DEMO=1`, trois fiches complètes
s'ajoutent aux « Appels en cours ». L'écoutant passe de l'une à l'autre en cliquant dessus, comme pour un vrai
appel.

| Fiche | Langue de l'appel | Lieu | Profil | Priorité (grille `priorite.py`) |
|---|---|---|---|---|
| Karim | Anglais | près de la gare de Lyon | homme, 42 ans, sans solution ce soir | 2 · moyenne |
| Amina | Arabe | place de la République | femme, 31 ans, 2 enfants (4 et 7 ans) | 4 · très haute |
| Julie | Français | devant la gare du Nord | femme, 24 ans, première nuit dehors | 3 · haute |

- Fiches en français, transcription dans la langue de l'appelant.
- La catégorie de places se présélectionne seule : Hommes, Familles avec enfants, Femmes.
- Elles ne sont jamais écrites sur le disque et n'ont pas de bouton « Créer le dossier ».
- Sans `MIKI_DEMO=1`, elles n'apparaissent pas.

**Géocodage** (`geo.mjs`) : les tournures françaises qui entourent le lieu sont maintenant retirées avant l'envoi à
l'IGN (« je suis », « près de », « devant », « à côté de », « en face de », « vers », « au niveau de »…).
L'article qui les suit est retiré aussi (« près de la gare de Lyon » → « gare de Lyon »), mais un nom comme
« Le Mans » reste intact. Vérifié sur l'IGN réel : gare de Lyon, gare du Nord, place de la République, Hôtel de
Ville sont tous résolus avec l'étiquette « précise ».

## 5. Heure retirée

L'en-tête du tableau de bord n'affiche plus l'heure du dernier rafraîchissement : « direct · 3 en cours · 0
dossier ». Les heures de début et de mise à jour de chaque fiche sont gardées.

## 6. Météo retirée

La carte météo n'apportait pas grand-chose à l'écoutant : elle est supprimée.

- `geo.mjs` : plus d'appel à Open-Meteo (`meteo()`, `lireMeteo()`, codes WMO et cache supprimés).
- `panneau.mjs` : plus de carte météo ; la liste des hébergements prend toute la largeur sous la carte.
- Le panneau s'intitule « Lieu et hébergements ». Plus aucun appel ne part vers `api.open-meteo.com`.
- Les sections météo de [carte-meteo-hebergements.md](carte-meteo-hebergements.md) et
  [carte-3d-theme-dihal.md](carte-3d-theme-dihal.md) ne sont plus d'actualité.

## Tests

- **Tests unitaires** : 13/13, dont les nouvelles tournures françaises de `requeteLieu` (le test de lecture
  Open-Meteo est remplacé par un test d'affichage des distances).
- **Chrome (GPU)** :
  - les trois fiches sont dans la liste, avec « Anglais », « Arabe », « Français » ;
  - plus de barre de scénarios, panneau masqué au départ ;
  - Amina → République et « Familles avec enfants » ; Julie → gare du Nord et « Femmes » ; Karim → gare de Lyon
    et « Hommes » ;
  - le lien « Tableau de bord DIHAL » ouvre la page DIHAL, dont l'onglet est actif, et le lien retour vise le
    port 8115.
- **Page DIHAL** : testée avec un serveur de test (fausses réponses d'API), faute de base sur cette machine.
- **Non testé** : un vrai appel (fiche en français produite par le LLM, langue de l'appel).
