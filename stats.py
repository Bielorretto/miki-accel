"""Requêtes statistiques DIHAL, sur stats_calls uniquement (données anonymes).

Règles :
- les phrases disent « parmi les appelants de MIKI », jamais « en France » ;
- tout effectif entre 1 et SEUIL - 1 est masqué (renvoyé à null), pour qu'une combinaison rare
  ne permette pas de reconnaître une personne ;
- source : 'tous', 'direct' (vrais appels) ou 'simule' (scénarios enregistrés).
"""

SEUIL = 5

# --- Les requêtes du brief, utilisables telles quelles dans psql ($1 = source, $2 = département ou NULL) ---

FILTRE = "($1 = 'tous' OR source = $1) AND ($2::text IS NULL OR departement = $2)"

CAUSE_PRINCIPALE = f"""
SELECT c.libelle AS cle, count(*) AS n
FROM stats_calls s JOIN causes c ON c.code = s.cause
WHERE {FILTRE} AND s.cause <> 'inconnue'
GROUP BY c.libelle ORDER BY n DESC"""

PAR_SEXE = f"""
SELECT sexe AS cle, count(*) AS n FROM stats_calls WHERE {FILTRE}
GROUP BY sexe ORDER BY n DESC"""

PAR_AGE = f"""
SELECT tranche_age AS cle, count(*) AS n FROM stats_calls WHERE {FILTRE}
GROUP BY tranche_age
ORDER BY array_position(ARRAY['moins_18','18_24','25_34','35_49','50_64','65_plus','inconnu'], tranche_age)"""

PAR_PROFIL = f"""
SELECT p.libelle AS cle, count(*) AS n
FROM stats_calls s JOIN profils p ON p.code = s.profil
WHERE {FILTRE} GROUP BY p.libelle ORDER BY n DESC"""

PAR_LANGUE = f"""
SELECT langue AS cle, count(*) AS n, bool_and(langue_couverte) AS couverte
FROM stats_calls WHERE {FILTRE} AND langue IS NOT NULL
GROUP BY langue ORDER BY n DESC"""

PAR_ISSUE = f"""
SELECT issue AS cle, count(*) AS n FROM stats_calls WHERE {FILTRE}
GROUP BY issue ORDER BY n DESC"""

PAR_SEMAINE = f"""
SELECT date_trunc('week', jour)::date AS semaine, count(*) AS n
FROM stats_calls WHERE {FILTRE}
GROUP BY semaine ORDER BY semaine"""

RESUME = f"""
SELECT count(*) AS appels,
       count(*) FILTER (WHERE sexe = 'femme') AS femmes,
       round(avg(delai_fiche_s))::int AS delai_moyen_s,
       count(*) FILTER (WHERE issue = 'aucune_place_orientation') AS sans_place,
       count(*) FILTER (WHERE langue_couverte = false) AS langue_non_couverte,
       count(*) FILTER (WHERE niveau_urgence >= 4) AS urgents
FROM stats_calls WHERE {FILTRE}"""

# Besoins non couverts : profils sans place et langues sans écoutant.
BESOINS_NON_COUVERTS = f"""
SELECT 'Aucune place : ' || coalesce(p.libelle, 'profil inconnu') AS cle, count(*) AS n
FROM stats_calls s LEFT JOIN profils p ON p.code = s.profil
WHERE {FILTRE} AND s.issue = 'aucune_place_orientation' GROUP BY p.libelle
UNION ALL
SELECT 'Langue sans écoutant : ' || langue, count(*)
FROM stats_calls WHERE {FILTRE} AND langue_couverte = false GROUP BY langue
ORDER BY n DESC"""

PAR_DEPARTEMENT = """
SELECT d.code, d.nom, d.siao, d.couvert,
       count(s.*) AS appels,
       count(s.*) FILTER (WHERE s.sexe = 'femme') AS femmes,
       round(avg(s.delai_fiche_s))::int AS delai_moyen_s,
       count(s.*) FILTER (WHERE s.issue = 'aucune_place_orientation') AS sans_place
FROM departements d
LEFT JOIN stats_calls s ON s.departement = d.code AND ($1 = 'tous' OR s.source = $1)
GROUP BY d.code ORDER BY appels DESC, d.code"""

LIBELLES = {
    "moins_18": "Moins de 18 ans", "18_24": "18-24 ans", "25_34": "25-34 ans", "35_49": "35-49 ans",
    "50_64": "50-64 ans", "65_plus": "65 ans et plus", "inconnu": "Non précisé",
    "homme": "Hommes", "femme": "Femmes", "autre": "Autre",
    "en_attente": "En attente d'un écoutant", "place_attribuee": "Place attribuée",
    "aucune_place_orientation": "Aucune place, orientation alternative",
    "orientation_urgence": "Orienté vers les urgences (15, 17, 112, 119)",
    "hors_zone": "Hors zone couverte", "abandon": "Appel interrompu",
}
LANGUES = {"fr": "Français", "ar": "Arabe", "en": "Anglais", "ro": "Roumain", "uk": "Ukrainien",
           "ps": "Pachto", "ti": "Tigrinya", "wo": "Wolof", "es": "Espagnol", "tr": "Turc", "ru": "Russe",
           "pt": "Portugais", "fa": "Persan", "de": "Allemand", "it": "Italien", "nl": "Néerlandais"}


def masquer(n: int | None) -> int | None:
    """Effectif publiable, ou None s'il est trop petit pour être diffusé."""
    return None if n is not None and 0 < n < SEUIL else n


def _lignes(rows, libelles=LIBELLES) -> list[dict]:
    out = []
    for r in rows:
        ligne = {"cle": libelles.get(r["cle"], r["cle"]), "n": masquer(r["n"])}
        if "couverte" in r.keys():
            ligne["couverte"] = r["couverte"]
        out.append(ligne)
    return out


def _phrases(resume: dict, causes: list[dict], perimetre: str) -> list[str]:
    appels = resume["appels"]
    if not appels or appels < SEUIL:
        return [f"Trop peu d'appels {perimetre} pour publier des chiffres."]
    phrases = [f"Parmi les appelants de MIKI {perimetre}, {appels} appels ont été enregistrés."]
    if resume["femmes"] is not None:
        phrases.append(f"Parmi les appelants de MIKI {perimetre}, {round(100 * resume['femmes'] / appels)} % "
                       f"sont des femmes.")
    if causes and causes[0]["n"]:
        phrases.append(f"La première cause de mise à la rue déclarée parmi les appelants de MIKI {perimetre} : "
                       f"{causes[0]['cle'].lower()} ({round(100 * causes[0]['n'] / appels)} % des appels).")
    if resume["delai_moyen_s"]:
        phrases.append(f"Délai moyen avant fiche complète : {resume['delai_moyen_s'] // 60} min "
                       f"{resume['delai_moyen_s'] % 60:02d} s.")
    return phrases


async def departements(conn, source: str) -> list[dict]:
    return [{**dict(r), "appels": masquer(r["appels"]), "femmes": masquer(r["femmes"]),
             "sans_place": masquer(r["sans_place"]),
             "delai_moyen_s": r["delai_moyen_s"] if (r["appels"] or 0) >= SEUIL else None}
            for r in await conn.fetch(PAR_DEPARTEMENT, source)]


async def detail(conn, source: str, dept: str | None) -> dict:
    args = (source, dept)
    resume = dict(await conn.fetchrow(RESUME, *args))
    trop_peu = (resume["appels"] or 0) < SEUIL
    causes = _lignes(await conn.fetch(CAUSE_PRINCIPALE, *args))
    perimetre = f"du {dept}" if dept else "(tous départements)"
    if dept:
        d = await conn.fetchrow("SELECT nom, siao, couvert FROM departements WHERE code = $1", dept)
        perimetre = f"en {d['nom']}" if d else perimetre
    out = {
        "departement": dept,
        "resume": {k: (v if k == "delai_moyen_s" else masquer(v)) for k, v in resume.items()},
        "phrases": _phrases({**resume, "femmes": masquer(resume["femmes"])}, causes, perimetre),
        "masque": trop_peu,
        "seuil": SEUIL,
    }
    if trop_peu:
        return out
    out.update({
        "causes": causes,
        "sexe": _lignes(await conn.fetch(PAR_SEXE, *args)),
        "ages": _lignes(await conn.fetch(PAR_AGE, *args)),
        "profils": _lignes(await conn.fetch(PAR_PROFIL, *args)),
        "langues": _lignes(await conn.fetch(PAR_LANGUE, *args), LANGUES),
        "issues": _lignes(await conn.fetch(PAR_ISSUE, *args)),
        "besoins": _lignes(await conn.fetch(BESOINS_NON_COUVERTS, *args), {}),
        "semaines": [{"semaine": r["semaine"].isoformat(), "n": masquer(r["n"])}
                     for r in await conn.fetch(PAR_SEMAINE, *args)],
    })
    for b in out["besoins"]:   # code langue → nom lisible
        if b["cle"].startswith("Langue sans écoutant : "):
            code = b["cle"].rsplit(" ", 1)[-1]
            b["cle"] = f"Langue sans écoutant : {LANGUES.get(code, code)}"
    return out
