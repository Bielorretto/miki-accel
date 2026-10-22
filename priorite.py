"""Priorité MIKI : fonctions pures, sans accès à la base, pour être testables et rejouables.

Deux mesures distinctes :
- le niveau d'urgence (1 à 5), grille fixe calculée à partir des critères extraits par le LLM ;
- le rang dans la file, propre à chaque 115 : règles de sécurité d'abord, puis l'ordre des
  profils du département en sautant ceux qui n'ont plus de place.
Le LLM extrait les faits, il ne décide jamais du classement.
"""

from dataclasses import dataclass

PRIORITY_GRID = [
    (5, ("danger_vital", "violences", "mineur_seul")),
    (4, ("enfants_presents", "grossesse", "probleme_sante")),
    (3, ("age_65_plus", "handicap", "premiere_nuit_rue")),
    (2, ("sans_solution_ce_soir",)),
]
PRIORITY_LABELS = {5: "vitale", 4: "très haute", 3: "haute", 2: "moyenne", 1: "basse"}


def compute_priority(criteres: dict) -> dict:
    motifs = [c for _, keys in PRIORITY_GRID for c in keys if criteres.get(c)]
    score = next((lvl for lvl, keys in PRIORITY_GRID if any(criteres.get(k) for k in keys)), 1)
    return {"score": score, "niveau": PRIORITY_LABELS[score], "motifs": motifs}


def profils_candidats(fiche: dict) -> list[str]:
    """Profils auxquels la personne peut prétendre, du plus spécifique au plus général.

    Une famille ou un couple n'a qu'un profil possible (une place « femme seule » ne convient
    pas à une mère avec ses enfants) ; une femme enceinte seule peut aussi occuper une place
    « femme seule », une personne âgée une place seule de son sexe.
    """
    criteres = fiche.get("criteres") or {}
    appelant = fiche.get("appelant") or {}
    cas = fiche.get("cas_particuliers") or {}
    if criteres.get("enfants_presents") or (cas.get("nb_enfants") or 0) > 0:
        return ["famille_enfants"]
    if criteres.get("en_couple"):
        return ["couple"]
    candidats = []
    if criteres.get("grossesse") or cas.get("grossesse"):
        candidats.append("femme_enceinte")
    if criteres.get("age_65_plus") or (appelant.get("age") or 0) >= 65:
        candidats.append("personne_agee")
    if appelant.get("sexe") == "femme":
        candidats.append("femme_seule")
    elif appelant.get("sexe") == "homme":
        candidats.append("homme_seul")
    return candidats


@dataclass
class Decision:
    profil: str | None
    rang: int
    securite: bool
    aucune_place: bool
    ordre_effectif: list[str]
    profils_sautes: list[str]
    explication: str


def decider(fiche: dict, departement: str, regle: dict, places: dict[str, int]) -> Decision:
    """Classe une fiche selon les règles du département.

    regle  : {"version": int, "ordre_profils": [...], "criteres_securite": [...]}
    places : {profil: places disponibles}
    Rang 0 = sécurité ; puis 1..n dans l'ordre effectif ; puis profil indéterminé ;
    puis « aucune place, orientation alternative » (reste en file, un humain décide).
    """
    ordre = regle["ordre_profils"]
    ordre_effectif = [p for p in ordre if places.get(p, 0) > 0]
    sautes = [p for p in ordre if places.get(p, 0) <= 0]
    candidats = profils_candidats(fiche)
    possibles = [p for p in ordre_effectif if p in candidats]
    criteres = fiche.get("criteres") or {}
    alertes = [c for c in regle["criteres_securite"] if criteres.get(c)]
    ref = f"règles du {departement} v{regle['version']}"
    note_sautes = f" ; profils sautés faute de place : {', '.join(sautes)}" if sautes else ""

    if possibles:
        profil, aucune_place = possibles[0], False
    elif candidats:
        profil = min(candidats, key=lambda p: ordre.index(p) if p in ordre else len(ordre))
        aucune_place = True
    else:
        profil, aucune_place = None, False

    if alertes:
        return Decision(profil, 0, True, aucune_place, ordre_effectif, sautes,
                        f"Règle de sécurité ({', '.join(alertes)}) : en tête de file, "
                        f"avant tout profil ({ref}).")
    if possibles:
        rang = ordre_effectif.index(profil) + 1
        return Decision(profil, rang, False, False, ordre_effectif, sautes,
                        f"Profil {profil} : rang {rang} dans l'ordre effectif "
                        f"{' > '.join(ordre_effectif)} ({ref}){note_sautes}.")
    if profil is None:
        return Decision(None, len(ordre) + 1, False, False, ordre_effectif, sautes,
                        f"Profil indéterminé (informations manquantes) : après les profils "
                        f"connus, en attendant la suite de l'appel ({ref}).")
    return Decision(profil, len(ordre) + 2, False, True, ordre_effectif, sautes,
                    f"Aucune place pour le profil {profil} : reste en file avec la mention "
                    f"« aucune place, orientation alternative », un écoutant décide ({ref}).")


def score_file(rang: int, arrivee_ms: int) -> float:
    """Score du sorted set Valkey : le rang d'abord, l'heure d'arrivée départage (FIFO)."""
    return rang * 10**13 + arrivee_ms
