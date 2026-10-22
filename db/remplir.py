"""Remplit la base avec des données 100 % synthétiques : 10 appelants avec leurs fiches en file,
et environ 2 000 lignes de statistiques sur 90 jours, toutes marquées source = 'simule'.

Les numéros utilisent la plage 06 39 98 xx xx, réservée par l'ARCEP aux œuvres de fiction.

Lancer :  python db/remplir.py           (après python db/migrer.py)
          python db/remplir.py --reset   (efface d'abord appels, fiches, appelants et stats)
"""

import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from base import CLE_VERSION, classer_fiche, connecter, hash_tel, publier_disponibilites, tranche_age  # noqa: E402
from priorite import compute_priority  # noqa: E402

CRITERES = ("danger_vital", "violences", "mineur_seul", "enfants_presents", "grossesse", "probleme_sante",
            "age_65_plus", "handicap", "premiere_nuit_rue", "sans_solution_ce_soir")

# prénom, nom, âge, sexe, dépt, commune, langue, cause, enfants, critères vrais, temps à la rue, récit
APPELANTS = [
    ("Fatou", "Diallo", 34, "femme", "93", "Saint-Denis", "fr", "expulsion_locative", [4, 9],
     ["enfants_presents", "sans_solution_ce_soir"], "deux jours",
     "Expulsée de son logement avec ses deux enfants, elle dort dans la gare depuis deux nuits."),
    ("Amina", "Benali", 27, "femme", "93", "Montreuil", "ar", "rupture_familiale", [],
     ["grossesse", "sans_solution_ce_soir"], "une semaine",
     "Enceinte de cinq mois, mise à la porte par sa famille, elle est hébergée au jour le jour par des amies."),
    ("Dragos", "Popescu", 45, "homme", "93", "Aubervilliers", "ro", "perte_ressources", [],
     ["sans_solution_ce_soir"], "trois mois",
     "A perdu son travail sur un chantier, ne peut plus payer sa chambre, dort dans sa voiture."),
    ("Sophie", "Martin", 52, "femme", "75", "Paris", "fr", "violences_conjugales", [],
     ["violences", "premiere_nuit_rue", "sans_solution_ce_soir"], "ce soir",
     "A quitté le domicile ce soir après des violences de son conjoint, n'a nulle part où aller."),
    ("Jean-Pierre", "Lefèvre", 68, "homme", "75", "Paris", "fr", "expulsion_locative", [],
     ["age_65_plus", "probleme_sante", "sans_solution_ce_soir"], "deux semaines",
     "Expulsé de son appartement, diabétique, dort dans un hall d'immeuble."),
    ("Karim", "Haddad", 31, "homme", "13", "Marseille", "ar", "sortie_institution", [],
     ["sans_solution_ce_soir"], "un jour",
     "Sorti de détention hier sans solution d'hébergement."),
    ("Oksana", "Kovalenko", 39, "femme", "69", "Lyon", "uk", "arrivee_migratoire", [7],
     ["enfants_presents", "sans_solution_ce_soir"], "quatre jours",
     "Arrivée en France avec son fils, l'hébergement provisoire chez une connaissance s'est terminé."),
    ("Thomas", "Dubois", 23, "homme", "59", "Lille", "fr", "rupture_familiale", [],
     ["premiere_nuit_rue", "sans_solution_ce_soir"], "ce soir",
     "Dispute avec ses parents, il a dû partir ce soir et n'a pas d'endroit où dormir."),
    ("Senait", "Tesfaye", 26, "femme", "33", "Bordeaux", "ti", "arrivee_migratoire", [],
     ["sans_solution_ce_soir"], "dix jours",
     "Arrivée récemment, parle tigrinya, dort dans un parc près de la gare."),
    ("Lucas", "Moreau", 41, "homme", "06", "Nice", "fr", "perte_ressources", [],
     ["en_couple", "sans_solution_ce_soir"], "un mois",
     "En couple, tous deux sans emploi depuis la fin de leur contrat saisonnier, ils dorment sous une tente."),
]

# poids relatifs des grands départements ; les autres valent POIDS_DEFAUT (volumes fictifs)
POIDS_DEPT = {"75": 22, "93": 18, "13": 12, "69": 10, "59": 9, "92": 8, "94": 8, "33": 7, "31": 6, "44": 6,
              "67": 5, "06": 5, "34": 5, "95": 5, "91": 5, "77": 5, "78": 4, "62": 4, "76": 4, "38": 4, "35": 4,
              "973": 3, "971": 3, "972": 2, "976": 3}
POIDS_DEFAUT = 1.2
NB_STATS = 2000
POIDS_LANGUE = {"fr": 58, "ar": 12, "en": 6, "ro": 5, "uk": 4, "ps": 3, "ti": 3, "wo": 3, "es": 2,
                "tr": 2, "ru": 2}
POIDS_CAUSE = {"rupture_familiale": 18, "expulsion_locative": 16, "perte_ressources": 15,
               "arrivee_migratoire": 14, "separation": 10, "violences_conjugales": 9,
               "sortie_institution": 8, "logement_insalubre": 4, "autre": 3, "inconnue": 3}
POIDS_ISSUE = {"en_attente": 35, "place_attribuee": 40, "aucune_place_orientation": 15,
               "orientation_urgence": 4, "abandon": 6}


def tirer(poids: dict, rng: random.Random):
    return rng.choices(list(poids), weights=list(poids.values()))[0]


def fiche_contenu(a) -> dict:
    prenom, nom, age, sexe, dept, commune, langue, cause, enfants, vrais, temps, recit = a
    criteres = {c: c in vrais for c in CRITERES}
    if "en_couple" in vrais:
        criteres["en_couple"] = True
    return {
        "appelant": {"prenom": prenom, "nom": nom, "age": age, "sexe": sexe},
        "situation": {"recit": recit, "temps_a_la_rue": temps},
        "localisation": commune,
        "cas_particuliers": {"grossesse": "grossesse" in vrais, "handicap": None, "blessure": None,
                             "sante": "diabète" if "probleme_sante" in vrais else None,
                             "nb_enfants": len(enfants), "ages_enfants": enfants,
                             "autres_accompagnants": "conjoint" if "en_couple" in vrais else None,
                             "animal": False},
        "criteres": criteres,
        "cause": cause,
        "langue": langue,
        "priorite": compute_priority(criteres),
        "infos_manquantes": [],
        "complet": True,
    }


def transcription(a) -> str:
    """Dialogue fictif cohérent avec la fiche, au format de l'agent (« ACCUEIL: » / « APPELANT: »)."""
    prenom, nom, age, sexe, dept, commune, langue, cause, enfants, vrais, temps, recit = a
    lignes = [
        ("ACCUEIL", "Bonjour, vous êtes bien au 115. Je m'appelle Miki, je suis l'assistante vocale du 115. "
                    "Pour préparer votre dossier, qui sera transmis aux équipes du 115, j'ai besoin de vous poser "
                    "quelques questions. Certaines informations anonymes servent aussi aux statistiques. "
                    "Êtes-vous d'accord ?"),
        ("APPELANT", "Oui, d'accord."),
        ("ACCUEIL", "Merci. Quel est votre prénom ?"),
        ("APPELANT", f"{prenom}."),
        ("ACCUEIL", f"Votre prénom est {prenom}, c'est bien ça ?"),
        ("APPELANT", "Oui."),
        ("ACCUEIL", "Merci. Et votre nom de famille ?"),
        ("APPELANT", f"{nom}."),
        ("ACCUEIL", "Merci. Quel âge avez-vous ?"),
        ("APPELANT", f"J'ai {age} ans."),
        ("ACCUEIL", f"Vous avez {age} ans, c'est bien ça ?"),
        ("APPELANT", "Oui c'est ça."),
        ("ACCUEIL", "Pouvez-vous me raconter ce qui vous arrive, avec vos mots ?"),
        ("APPELANT", recit),
        ("ACCUEIL", "Je suis désolée de ce qui vous arrive. Depuis combien de temps êtes-vous à la rue ?"),
        ("APPELANT", f"Depuis {temps}."),
        ("ACCUEIL", "Où vous trouvez-vous en ce moment ? La ville, une rue ou un repère près de vous."),
        ("APPELANT", f"Je suis à {commune}."),
        ("ACCUEIL", f"Vous êtes à {commune}, c'est bien ça ?"),
        ("APPELANT", "Oui."),
        ("ACCUEIL", "Avez-vous un problème de santé, un handicap, des enfants ou d'autres personnes avec vous ?"),
        ("APPELANT", f"J'ai {len(enfants)} enfant{'s' if len(enfants) > 1 else ''} avec moi." if enfants
                     else "Je suis avec mon conjoint." if "en_couple" in vrais
                     else "Je suis diabétique." if "probleme_sante" in vrais
                     else "Je suis enceinte, c'est tout." if "grossesse" in vrais else "Non, rien de particulier."),
        ("ACCUEIL", f"Merci {prenom}. Votre demande est enregistrée et transmise aux équipes du 115. "
                    "Prenez soin de vous, au revoir."),
    ]
    return "\n".join(f"{qui}: {texte}" for qui, texte in lignes)


async def reset(conn, vk) -> None:
    hote = urlparse(os.environ["DATABASE_URL"]).hostname
    if hote not in ("localhost", "127.0.0.1") and "--force" not in sys.argv:
        sys.exit(f"Refus d'effacer une base distante ({hote}) sans --force.")
    await conn.execute("TRUNCATE decisions_priorite, fiches, appels, appelant_historique, appelants, "
                       "stats_calls")
    for motif in ("file:*", "appel:*"):   # appel:* couvre aussi appel:{id}:transcription
        async for cle in vk.scan_iter(motif):
            await vk.delete(cle)


async def main() -> None:
    rng = random.Random(115)
    pool, vk = await connecter()
    async with pool.acquire() as conn:
        if "--reset" in sys.argv:
            await reset(conn, vk)
        if await conn.fetchval("SELECT count(*) FROM appels"):
            sys.exit("La base contient déjà des appels : relancer avec --reset.")

        poids_dept = {r["code"]: POIDS_DEPT.get(r["code"], POIDS_DEFAUT)
                      for r in await conn.fetch("SELECT code FROM departements WHERE couvert")}
        langues_dept = {}
        for e in await conn.fetch("SELECT departement, langues FROM ecoutants WHERE actif"):
            langues_dept.setdefault(e["departement"], set()).update(e["langues"])

        maintenant = datetime.now(timezone.utc)
        async with conn.transaction():
            for i, a in enumerate(APPELANTS):
                prenom, nom, age, sexe, dept, commune, langue, cause = a[:8]
                contenu = fiche_contenu(a)
                couverte = langue in langues_dept.get(dept, set())
                debut = maintenant - timedelta(minutes=rng.randint(3, 90))
                delai = rng.randint(70, 210)
                tel = f"+3363998{i:04d}"
                ecoutant = await conn.fetchval(   # un écoutant du département qui parle la langue
                    "SELECT id FROM ecoutants WHERE departement = $1 AND actif AND $2 = ANY(langues) "
                    "ORDER BY id LIMIT 1", dept, langue)

                appelant_id = await conn.fetchval(
                    "INSERT INTO appelants (tel_hash, cle_version, prenom, nom, age, age_declare_le, sexe) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                    hash_tel(tel), CLE_VERSION, prenom, nom, age, debut.date(), sexe)
                appel_id = await conn.fetchval(
                    "INSERT INTO appels (appelant_id, tel_hash, debut, fin, departement, langue, "
                    "langue_couverte, ecoutant_id, statut, issue, source) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'termine', 'en_attente', 'simule') RETURNING id",
                    appelant_id, hash_tel(tel), debut, debut + timedelta(seconds=delai + 40), dept,
                    langue, couverte, ecoutant)
                fiche_id = await conn.fetchval(
                    "INSERT INTO fiches (appel_id, appelant_id, departement, localisation, criteres, cause, "
                    "niveau_urgence, complet, contenu, transcription, creee_le, delai_fiche_s) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, true, $8, $9, $10, $11) RETURNING id",
                    appel_id, appelant_id, dept, commune, contenu["criteres"], cause,
                    contenu["priorite"]["score"], contenu, transcription(a), debut, delai)
                await classer_fiche(conn, vk, fiche_id)
                profil, issue = await conn.fetchrow(
                    "SELECT profil, CASE WHEN aucune_place THEN 'aucune_place_orientation' "
                    "ELSE 'en_attente' END FROM fiches WHERE id = $1", fiche_id)
                await conn.execute("UPDATE appels SET issue = $2 WHERE id = $1", appel_id, issue)
                await conn.execute(
                    "INSERT INTO stats_calls (jour, departement, tranche_age, sexe, cause, profil, langue, "
                    "langue_couverte, niveau_urgence, issue, delai_fiche_s, source) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'simule')",
                    debut.date(), dept, tranche_age(age), sexe, cause, profil, langue, couverte,
                    contenu["priorite"]["score"], issue, delai)

            lignes = []
            for _ in range(NB_STATS):
                dept = tirer(poids_dept, rng)
                sexe = rng.choices(["homme", "femme", "autre", "inconnu"], [60, 37, 1, 2])[0]
                tranche = rng.choices(["moins_18", "18_24", "25_34", "35_49", "50_64", "65_plus"],
                                      [2, 17, 27, 29, 18, 7])[0]
                if tranche == "65_plus":
                    profil = "personne_agee"
                elif sexe == "femme":
                    profil = rng.choices(["famille_enfants", "femme_enceinte", "femme_seule", "couple"],
                                         [32, 10, 48, 10])[0]
                else:
                    profil = rng.choices(["homme_seul", "famille_enfants", "couple"], [75, 10, 15])[0]
                cause = tirer(POIDS_CAUSE, rng)
                if cause == "violences_conjugales" and sexe == "homme" and rng.random() < 0.8:
                    cause = "separation"
                langue = tirer(POIDS_LANGUE, rng)
                lignes.append((
                    date.today() - timedelta(days=rng.randint(1, 90)), dept, tranche, sexe, cause, profil,
                    langue, langue in langues_dept.get(dept, set()), rng.choices([1, 2, 3, 4, 5],
                                                                                  [8, 42, 25, 18, 7])[0],
                    tirer(POIDS_ISSUE, rng), int(rng.gauss(140, 45)) if rng.random() > 0.05 else None,
                    "simule"))
            await conn.copy_records_to_table(
                "stats_calls", records=lignes,
                columns=["jour", "departement", "tranche_age", "sexe", "cause", "profil", "langue",
                         "langue_couverte", "niveau_urgence", "issue", "delai_fiche_s", "source"])

        await publier_disponibilites(conn, vk)
    await pool.close()
    await vk.aclose()
    print(f"{len(APPELANTS)} appelants et fiches, {len(APPELANTS) + NB_STATS} lignes de statistiques.")


if __name__ == "__main__":
    asyncio.run(main())
