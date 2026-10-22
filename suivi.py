"""Suivi d'un appel en base : chaque appel écrit réellement dans PostgreSQL et Valkey.

- au décroché : une ligne dans appels, la session appel:{id} dans Valkey ;
- à chaque analyse du penseur : département, reconnaissance de l'appelant, fiche, priorité, file ;
- en fin d'appel : clôture de l'appel et une ligne anonyme dans stats_calls.

Toute erreur de base est journalisée sans jamais couper l'appel : MIKI continue à répondre.
"""

import json
import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timezone

from base import CLE_VERSION, classer_fiche, connecter, hash_tel, tranche_age

logger = logging.getLogger("miki.suivi")
CAUSES = {"expulsion_locative", "rupture_familiale", "violences_conjugales", "separation",
          "sortie_institution", "perte_ressources", "arrivee_migratoire", "logement_insalubre", "autre"}
CHAMPS_IDENTITE = ("nom", "sexe", "age")
SESSION_TTL = 2 * 3600


def normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sans_accents.lower()).strip()


class SuiviAppel:
    def __init__(self) -> None:
        self.pool = self.vk = None
        self.appel_id = None
        self.tel_hash: bytes | None = None
        self.debut = datetime.now(timezone.utc)
        self.langue = os.environ.get("STT_LANGUAGE", "en")
        self.departement: str | None = None
        self.couvert = True
        self.appelant_id = None
        self.appelant_cree_ici = False
        self.doute = False
        self.communes: list = []
        self.noms_departements: list = []

    async def ouvrir(self, numero: str | None) -> None:
        try:
            self.pool, self.vk = await connecter()
            self.tel_hash = hash_tel(numero) if numero else None
            async with self.pool.acquire() as conn:
                self.communes = [(normaliser(r["nom"]), r["code_postal"], r["departement"])
                                 for r in await conn.fetch("SELECT nom, code_postal, departement FROM communes")]
                self.noms_departements = [(normaliser(r["nom"]), r["code"])
                                          for r in await conn.fetch("SELECT code, nom FROM departements")]
                self.appel_id = await conn.fetchval(
                    "INSERT INTO appels (tel_hash, debut, langue, source) VALUES ($1, $2, $3, 'direct') "
                    "RETURNING id", self.tel_hash, self.debut, self.langue)
            await self.vk.hset(f"appel:{self.appel_id}", mapping={
                "debut": self.debut.isoformat(), "statut": "en_cours", "langue": self.langue})
            await self.vk.expire(f"appel:{self.appel_id}", SESSION_TTL)
            logger.info("📞 appel %s ouvert en base (numéro %s)", self.appel_id,
                        "haché" if self.tel_hash else "absent")
        except Exception:
            logger.exception("base indisponible : l'appel continue sans enregistrement")
            self.appel_id = None

    async def message(self, role: str, texte: str) -> None:
        """Chaque phrase de l'appel, dès qu'elle est dite : liste Valkey + notification en direct."""
        if not self.appel_id or not texte:
            return
        try:
            ligne = json.dumps({"role": "miki" if role == "assistant" else "appelant", "texte": texte,
                                "t": round(time.time(), 1)}, ensure_ascii=False)
            cle = f"appel:{self.appel_id}:transcription"
            await self.vk.rpush(cle, ligne)
            await self.vk.expire(cle, SESSION_TTL)
            await self.vk.publish(f"transcription:{self.appel_id}", ligne)
        except Exception:
            logger.exception("transcription en direct impossible (l'appel continue)")

    def _trouver_departement(self, fiche: dict) -> tuple[str | None, bool]:
        """Commune → département. Renvoie (département, ambigu)."""
        texte = " ".join(filter(None, [fiche.get("commune"), fiche.get("localisation")]))
        if cp := re.search(r"\b\d{5}\b", texte):
            depts = {d for _, c, d in self.communes if c == cp.group()}
            if len(depts) == 1:
                return depts.pop(), False
        for source in (fiche.get("commune"), fiche.get("localisation")):
            if not source:
                continue
            n = f" {normaliser(source)} "
            trouves = [(nom, d) for nom, _, d in self.communes if f" {nom} " in n]
            if trouves:
                # le nom le plus long l'emporte (« Saint-Denis » plutôt que « Paris » dans « Paris, Saint-Denis »)
                plus_long = max(len(nom) for nom, _ in trouves)
                depts = {d for nom, d in trouves if len(nom) == plus_long}
                return (depts.pop(), False) if len(depts) == 1 else (None, True)
        # repli : le nom du département lui-même (« in the Gironde »), le plus long l'emporte
        n = f" {normaliser(texte)} "
        trouves = [(nom, code) for nom, code in self.noms_departements if f" {nom} " in n]
        if trouves:
            return max(trouves, key=lambda t: len(t[0]))[1], False
        return None, False

    async def _reconnaitre(self, conn, appelant: dict) -> None:
        prenom, age = appelant.get("prenom"), appelant.get("age")
        if self.appelant_id:
            await self._mettre_a_jour_appelant(conn, appelant)
            return
        if not (prenom and isinstance(age, int)) or self.doute:
            return
        correspondances = []
        if self.tel_hash:
            for c in await conn.fetch("SELECT id, prenom, age, age_declare_le FROM appelants "
                                      "WHERE tel_hash = $1", self.tel_hash):
                if c["prenom"] and normaliser(c["prenom"]) == normaliser(prenom) and c["age"] is not None:
                    attendu = c["age"] + (date.today() - (c["age_declare_le"] or date.today())).days // 365
                    if abs(attendu - age) <= 1:   # un anniversaire a pu passer entre deux appels
                        correspondances.append(c["id"])
        if len(correspondances) == 1:
            self.appelant_id = correspondances[0]
            logger.info("🔁 appelant reconnu : fiche existante reprise")
            await self._mettre_a_jour_appelant(conn, appelant)
        elif len(correspondances) > 1:
            self.doute = True   # jamais de fusion automatique : un écoutant tranchera
            logger.info("❓ plusieurs fiches possibles pour cet appelant : aucune fusion")
        else:
            self.appelant_id = await conn.fetchval(
                "INSERT INTO appelants (tel_hash, cle_version, prenom, nom, age, age_declare_le, sexe) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                self.tel_hash, CLE_VERSION, prenom, appelant.get("nom"), age, date.today(),
                appelant.get("sexe") if appelant.get("sexe") in ("homme", "femme", "autre") else None)
            self.appelant_cree_ici = True
        if self.appelant_id:
            await conn.execute("UPDATE appels SET appelant_id = $2 WHERE id = $1", self.appel_id, self.appelant_id)

    async def _mettre_a_jour_appelant(self, conn, appelant: dict) -> None:
        """La valeur la plus récente va dans appelants ; l'ancienne est gardée dans l'historique."""
        actuel = await conn.fetchrow("SELECT prenom, nom, sexe, age FROM appelants WHERE id = $1", self.appelant_id)
        champs = ("prenom", *CHAMPS_IDENTITE) if self.appelant_cree_ici else CHAMPS_IDENTITE
        for champ in champs:
            nouveau = appelant.get(champ)
            if champ == "sexe" and nouveau not in ("homme", "femme", "autre"):
                continue
            if nouveau is None or nouveau == actuel[champ]:
                continue
            # au cours de l'appel qui a créé la fiche, une correction n'est pas un changement d'historique
            if not self.appelant_cree_ici and actuel[champ] is not None:
                await conn.execute(
                    "INSERT INTO appelant_historique (appelant_id, champ, ancienne_valeur, nouvelle_valeur, appel_id) "
                    "VALUES ($1, $2, $3, $4, $5)", self.appelant_id, champ, str(actuel[champ]), str(nouveau),
                    self.appel_id)
            extra = ", age_declare_le = current_date" if champ == "age" else ""
            await conn.execute(f"UPDATE appelants SET {champ} = $2, maj = now(){extra} WHERE id = $1",
                               self.appelant_id, nouveau)

    async def maj(self, fiche: dict, transcription: str) -> str | None:
        """Enregistre la fiche. Renvoie une consigne pour MIKI si le lieu pose problème."""
        if not self.appel_id:
            return None
        note = None
        try:
            async with self.pool.acquire() as conn, conn.transaction():
                dept, ambigu = self._trouver_departement(fiche)
                if dept and dept != self.departement:
                    self.departement = dept
                    d = await conn.fetchrow("SELECT nom, couvert FROM departements WHERE code = $1", dept)
                    self.couvert = d["couvert"]
                    couverte = bool(await conn.fetchval(
                        "SELECT 1 FROM ecoutants WHERE departement = $1 AND actif AND $2 = ANY(langues)",
                        dept, self.langue))
                    ecoutant = await self.vk.srandmember(f"dispo:{dept}:{self.langue}") if couverte else None
                    await conn.execute(
                        "UPDATE appels SET departement = $2, langue_couverte = $3, ecoutant_id = $4 WHERE id = $1",
                        self.appel_id, dept, couverte, int(ecoutant) if ecoutant else None)
                    await self.vk.hset(f"appel:{self.appel_id}", mapping={"departement": dept})
                    logger.info("📍 département %s (%s)%s%s", dept, d["nom"],
                                "" if self.couvert else ", NON COUVERT",
                                "" if couverte else f", langue {self.langue} non couverte")
                if self.departement and not self.couvert:
                    orientation = await conn.fetchval("SELECT orientation FROM departements WHERE code = $1",
                                                      self.departement)
                    note = (f"The caller is in an area MIKI does not cover yet. Tell them kindly, in your own "
                            f"words: {orientation}")
                elif ambigu:
                    note = ("Several towns match the place the caller gave. Ask which town and which "
                            "department they are in right now, then confirm it aloud.")

                await self._reconnaitre(conn, fiche.get("appelant") or {})
                cause = fiche.get("cause") if fiche.get("cause") in CAUSES else None
                contenu = {**fiche, "langue": self.langue, "reconnaissance": "doute" if self.doute else None}
                delai = int((datetime.now(timezone.utc) - self.debut).total_seconds()) if fiche.get("complet") else None
                fiche_id = await conn.fetchval(
                    "INSERT INTO fiches (appel_id, appelant_id, departement, localisation, criteres, cause, "
                    "niveau_urgence, complet, contenu, transcription, creee_le, delai_fiche_s) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
                    "ON CONFLICT (appel_id) DO UPDATE SET appelant_id = EXCLUDED.appelant_id, "
                    "departement = EXCLUDED.departement, localisation = EXCLUDED.localisation, "
                    "criteres = EXCLUDED.criteres, cause = EXCLUDED.cause, "
                    "niveau_urgence = EXCLUDED.niveau_urgence, complet = EXCLUDED.complet, "
                    "contenu = EXCLUDED.contenu, transcription = EXCLUDED.transcription, maj = now(), "
                    "delai_fiche_s = COALESCE(fiches.delai_fiche_s, EXCLUDED.delai_fiche_s) RETURNING id",
                    self.appel_id, self.appelant_id, self.departement,
                    fiche.get("localisation"), fiche.get("criteres") or {}, cause,
                    (fiche.get("priorite") or {}).get("score"), bool(fiche.get("complet")), contenu,
                    transcription, self.debut, delai)
                await classer_fiche(conn, self.vk, fiche_id)
                await self.vk.hset(f"appel:{self.appel_id}", mapping={"fiche": str(fiche_id), "maj": time.time()})
        except Exception:
            logger.exception("enregistrement de la fiche impossible (l'appel continue)")
        return note

    async def clore(self) -> None:
        if not self.appel_id:
            return
        try:
            async with self.pool.acquire() as conn, conn.transaction():
                f = await conn.fetchrow(
                    "SELECT contenu, profil, cause, niveau_urgence, aucune_place, delai_fiche_s, criteres "
                    "FROM fiches WHERE appel_id = $1", self.appel_id)
                if self.departement and not self.couvert:
                    issue = "hors_zone"
                elif f is None:
                    issue = "abandon"
                elif (f["criteres"] or {}).get("danger_vital"):
                    issue = "orientation_urgence"
                elif f["aucune_place"]:
                    issue = "aucune_place_orientation"
                else:
                    issue = "en_attente"
                langue_couverte = await conn.fetchval("SELECT langue_couverte FROM appels WHERE id = $1",
                                                      self.appel_id)
                await conn.execute("UPDATE appels SET fin = now(), statut = 'termine', issue = $2 WHERE id = $1",
                                   self.appel_id, issue)
                appelant = (f["contenu"] if f else {}).get("appelant") or {}
                age = appelant.get("age") if isinstance(appelant.get("age"), int) else None
                sexe = appelant.get("sexe") if appelant.get("sexe") in ("homme", "femme", "autre") else "inconnu"
                # statistiques : uniquement des champs anonymes, aucune clé vers l'appel ou l'appelant
                await conn.execute(
                    "INSERT INTO stats_calls (jour, departement, tranche_age, sexe, cause, profil, langue, "
                    "langue_couverte, niveau_urgence, issue, delai_fiche_s, source) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'direct')",
                    self.debut.date(), self.departement, tranche_age(age), sexe,
                    (f["cause"] or "inconnue") if f else "inconnue", f["profil"] if f else None, self.langue,
                    langue_couverte, f["niveau_urgence"] if f else None, issue,
                    f["delai_fiche_s"] if f else None)
            await self.vk.hset(f"appel:{self.appel_id}", mapping={"statut": "termine"})
            if self.departement:
                await self.vk.publish(f"fiches:{self.departement}", json.dumps({"appel": str(self.appel_id),
                                                                               "statut": "termine"}))
            logger.info("✅ appel %s clos en base (issue : %s)", self.appel_id, issue)
        except Exception:
            logger.exception("clôture de l'appel en base impossible")
        finally:
            await self.pool.close()
            await self.vk.aclose()
