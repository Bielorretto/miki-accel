"""Suivi d'un appel en base : chaque appel écrit réellement dans PostgreSQL et Valkey.

- au décroché : une ligne dans appels, la session appel:{id} dans Valkey, et la recherche des
  appelants déjà connus pour ce numéro ;
- à chaque analyse du penseur : département (d'après le lieu), reconnaissance de l'appelant,
  fiche, priorité, file, et les places restantes pour son profil ;
- quand la langue de l'appelant change : écoutant parlant cette langue, ou « langue non couverte » ;
- en fin d'appel : clôture de l'appel et une ligne anonyme dans stats_calls.

Ce que la base apprend à MIKI passe par des notes (notes_pour_miki), que l'agent ajoute à ses
instructions. Toute erreur de base est journalisée sans jamais couper l'appel.
"""

import json
import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timezone

from base import CLE_VERSION, classer_fiche, connecter, hash_tel, tranche_age
from lieux import localiser

logger = logging.getLogger("miki.suivi")
CAUSES = {"expulsion_locative", "rupture_familiale", "violences_conjugales", "separation",
          "sortie_institution", "perte_ressources", "arrivee_migratoire", "logement_insalubre", "autre"}
CHAMPS_IDENTITE = ("nom", "sexe", "age")
SESSION_TTL = 2 * 3600
LANGUES = {"fr": "français", "en": "anglais", "ar": "arabe", "es": "espagnol", "pt": "portugais",
           "ro": "roumain", "ru": "russe", "uk": "ukrainien", "tr": "turc", "ps": "pachto", "fa": "persan",
           "ti": "tigrinya", "wo": "wolof", "de": "allemand", "it": "italien", "nl": "néerlandais"}
# langues que la voix de MIKI (ElevenLabs Flash v2.5) sait parler, parmi celles reconnues au 115
LANGUES_PARLEES = {"fr", "en", "ar", "es", "pt", "ro", "ru", "uk", "tr", "de", "it", "nl"}


def normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sans_accents.lower()).strip()


def nom_langue(code: str) -> str:
    return LANGUES.get(code, code)


class SuiviAppel:
    def __init__(self) -> None:
        self.pool = self.vk = None
        self.appel_id = None
        self.tel_hash: bytes | None = None
        self.debut = datetime.now(timezone.utc)
        self.langue = os.environ.get("STT_LANGUAGE", "en")
        self.langue_couverte: bool | None = None
        self.departement: str | None = None
        self.nom_departement: str | None = None
        self.couvert = True
        self.appelant_id = None
        self.appelant_cree_ici = False
        self.doute = False
        self.connus: list = []            # appelants déjà venus avec ce numéro
        self.dossier_connu: dict | None = None
        self.lieu_cherche = None
        self.noms_departements: list = []
        self.notes: dict[str, str] = {}   # thème → consigne pour MIKI

    # --- notes pour MIKI ---------------------------------------------------------------

    def notes_pour_miki(self) -> str:
        return "\n".join(f"- {n}" for n in self.notes.values())

    def contexte_penseur(self) -> str:
        """Ce que la base sait déjà d'un appelant reconnu, pour que le penseur ne le redemande pas."""
        if not self.dossier_connu:
            return ""
        d = self.dossier_connu
        return (f"DOSSIER EXISTANT (appelant reconnu par le 115, identité confirmée) : prénom={d['prenom']}, "
                f"nom={d['nom']}, sexe={d['sexe']}. Ces informations sont connues, ne les mets pas dans "
                f"infos_manquantes.\n\n")

    # --- décroché ----------------------------------------------------------------------

    async def ouvrir(self, numero: str | None) -> None:
        try:
            self.pool, self.vk = await connecter()
            self.tel_hash = hash_tel(numero) if numero else None
            async with self.pool.acquire() as conn:
                self.noms_departements = [(normaliser(r["nom"]), r["code"], r["nom"])
                                          for r in await conn.fetch("SELECT code, nom FROM departements")]
                self.appel_id = await conn.fetchval(
                    "INSERT INTO appels (tel_hash, debut, langue, source) VALUES ($1, $2, $3, 'direct') "
                    "RETURNING id", self.tel_hash, self.debut, self.langue)
                if self.tel_hash:
                    self.connus = await conn.fetch(
                        "SELECT p.id, p.prenom, p.nom, p.sexe, max(a.debut) AS dernier FROM appelants p "
                        "JOIN appels a ON a.appelant_id = p.id WHERE p.tel_hash = $1 AND p.prenom IS NOT NULL "
                        "GROUP BY p.id ORDER BY dernier DESC LIMIT 3", self.tel_hash)
            await self.vk.hset(f"appel:{self.appel_id}", mapping={
                "debut": self.debut.isoformat(), "statut": "en_cours", "langue": self.langue})
            await self.vk.expire(f"appel:{self.appel_id}", SESSION_TTL)
            if self.connus:
                prenoms = " ou ".join(c["prenom"] for c in self.connus)
                dernier = self.connus[0]["dernier"].strftime("%d/%m")
                self.notes["reconnaissance"] = (
                    f"APPELANT DÉJÀ CONNU : ce numéro a déjà appelé le cent quinze (dernier appel le {dernier}), "
                    f"prénom connu : {prenoms}. Après le consentement, au lieu de demander le prénom, demande : "
                    f"« Est-ce que je parle bien à {prenoms} ? ». Un téléphone peut être prêté : ne donne aucune "
                    f"information du dossier avant que la personne ait confirmé son prénom puis son âge. Si elle "
                    f"dit non, continue normalement avec ses informations à elle.")
            logger.info("📞 appel %s ouvert en base (numéro %s%s)", self.appel_id,
                        "haché" if self.tel_hash else "absent",
                        f", {len(self.connus)} appelant(s) déjà connu(s)" if self.connus else "")
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

    # --- langue ------------------------------------------------------------------------

    async def changer_langue(self, langue: str) -> None:
        """L'appelant parle une autre langue : MIKI la suit, et on cherche un écoutant qui la parle."""
        if not self.appel_id or langue == self.langue:
            return
        self.langue = langue
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("UPDATE appels SET langue = $2 WHERE id = $1", self.appel_id, langue)
                await self.vk.hset(f"appel:{self.appel_id}", mapping={"langue": langue})
                await self._attribuer_ecoutant(conn)
            logger.info("🌍 langue de l'appelant : %s", nom_langue(langue))
        except Exception:
            logger.exception("changement de langue non enregistré (l'appel continue)")
        self._note_langue()

    def _note_langue(self) -> None:
        nom = nom_langue(self.langue)
        if self.langue == "fr":
            self.notes.pop("langue", None)
            return
        if self.langue in LANGUES_PARLEES:
            note = f"LANGUE : l'appelant parle {nom}. Parle-lui en {nom} jusqu'à la fin de l'appel."
        else:
            note = (f"LANGUE : l'appelant parle {nom}, que ta voix ne sait pas parler. Continue en français avec "
                    f"des phrases très courtes et simples, ou en anglais si la personne le comprend mieux.")
        if self.departement and self.couvert:
            if self.langue_couverte:
                note += (f" Un écoutant du cent quinze qui parle {nom} traitera son dossier : dis-le-lui "
                         f"une fois, pour le rassurer.")
            else:
                note += (f" Aucun écoutant de ce département ne parle {nom} : son dossier est signalé "
                         f"« langue non couverte » et les équipes s'organiseront ; ne le fais pas porter à la "
                         f"personne.")
        self.notes["langue"] = note

    async def _attribuer_ecoutant(self, conn) -> None:
        """Écoutant libre du département qui parle la langue de l'appelant (Valkey dispo:{dept}:{langue})."""
        if not self.departement or not self.couvert:
            return
        ecoutant = await self.vk.srandmember(f"dispo:{self.departement}:{self.langue}")
        if ecoutant is None:   # Valkey vide (redémarrage) : on regarde directement en base
            ecoutant = await conn.fetchval(
                "SELECT id FROM ecoutants WHERE departement = $1 AND actif AND $2 = ANY(langues) "
                "ORDER BY random() LIMIT 1", self.departement, self.langue)
        self.langue_couverte = ecoutant is not None
        await conn.execute("UPDATE appels SET langue_couverte = $2, ecoutant_id = $3 WHERE id = $1",
                           self.appel_id, self.langue_couverte, int(ecoutant) if ecoutant else None)
        if not self.langue_couverte:
            logger.info("🗣 langue %s non couverte dans le %s", nom_langue(self.langue), self.departement)

    # --- lieu → département ------------------------------------------------------------

    async def _maj_lieu(self, conn, fiche: dict) -> None:
        cle = (fiche.get("commune"), fiche.get("localisation"))
        if cle == self.lieu_cherche or not any(cle):
            return
        self.lieu_cherche = cle
        lieu = await localiser(conn, *cle)
        dept = lieu.departement
        if not dept and not lieu.homonymes:
            # repli : le nom du département lui-même (« je suis en Gironde »), le plus long l'emporte
            n = f" {normaliser(' '.join(filter(None, cle)))} "
            trouves = [(nom, code) for nom, code, _ in self.noms_departements if f" {nom} " in n]
            dept = max(trouves, key=lambda t: len(t[0]))[1] if trouves else None
        if lieu.homonymes:
            choix = ", ".join(f"{c} ({self._nom_dept(d)})" for c, d in lieu.homonymes)
            self.notes["lieu"] = (f"LIEU À PRÉCISER : plusieurs communes correspondent à « {lieu.recherche} » : "
                                  f"{choix}. Demande dans quel département ou quelle commune exacte la personne "
                                  f"se trouve en ce moment, puis confirme à voix haute.")
            return
        if not dept:
            self.notes["lieu"] = (f"LIEU INTROUVABLE : aucune commune française ne correspond à « {lieu.recherche} ». "
                                  f"Demande de répéter ou d'épeler le nom de la ville, ou le code postal.")
            return
        if dept == self.departement:
            return
        self.departement = dept
        d = await conn.fetchrow("SELECT nom, couvert, orientation FROM departements WHERE code = $1", dept)
        self.nom_departement, self.couvert = d["nom"], d["couvert"]
        await conn.execute("UPDATE appels SET departement = $2 WHERE id = $1", self.appel_id, dept)
        await self.vk.hset(f"appel:{self.appel_id}", mapping={"departement": dept})
        await self._attribuer_ecoutant(conn)
        ville = lieu.commune or self.nom_departement
        if self.couvert:
            self.notes["lieu"] = (
                f"LIEU IDENTIFIÉ : {ville}, en {self.nom_departement}. Si ce n'est pas déjà fait, confirme-le à "
                f"voix haute une fois (« Vous êtes bien à {ville}, en {self.nom_departement} ? ») et dis que sa "
                f"demande sera transmise au cent quinze de {self.nom_departement}.")
        else:
            self.notes["lieu"] = (f"ZONE NON COUVERTE : {ville} ({self.nom_departement}) n'est pas encore couvert "
                                  f"par MIKI. Dis-le avec bienveillance, avec tes mots : {d['orientation']}")
        self._note_langue()
        logger.info("📍 %s → département %s (%s)%s", ville, dept, self.nom_departement,
                    "" if self.couvert else ", NON COUVERT")

    def _nom_dept(self, code: str) -> str:
        return next((nom for _, c, nom in self.noms_departements if c == code), code)

    # --- reconnaissance ----------------------------------------------------------------

    async def _reconnaitre(self, conn, appelant: dict) -> None:
        prenom, age = appelant.get("prenom"), appelant.get("age")
        if self.appelant_id:
            await self._mettre_a_jour_appelant(conn, appelant)
            return
        if not (prenom and isinstance(age, int)) or self.doute:
            return
        correspondances = []
        if self.tel_hash:
            for c in await conn.fetch("SELECT id, prenom, nom, sexe, age, age_declare_le FROM appelants "
                                      "WHERE tel_hash = $1", self.tel_hash):
                if c["prenom"] and normaliser(c["prenom"]) == normaliser(prenom) and c["age"] is not None:
                    attendu = c["age"] + (date.today() - (c["age_declare_le"] or date.today())).days // 365
                    if abs(attendu - age) <= 1:   # un anniversaire a pu passer entre deux appels
                        correspondances.append(c)
        if len(correspondances) == 1:
            c = correspondances[0]
            self.appelant_id = c["id"]
            self.dossier_connu = dict(c)
            self.notes["reconnaissance"] = (
                f"APPELANT RECONNU : {c['prenom']} a confirmé son prénom et son âge, son dossier existant est repris. "
                f"Dis-lui que tu retrouves son dossier. Ne redemande ni son nom ni son sexe ; demande seulement "
                f"ce qui a pu changer depuis son dernier appel (sa situation, l'endroit où la personne se trouve).")
            logger.info("🔁 appelant reconnu : fiche existante reprise")
            await self._mettre_a_jour_appelant(conn, appelant)
        elif len(correspondances) > 1:
            self.doute = True   # jamais de fusion automatique : un écoutant tranchera
            self.notes.pop("reconnaissance", None)
            logger.info("❓ plusieurs fiches possibles pour cet appelant : aucune fusion")
        else:
            self.notes.pop("reconnaissance", None)
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

    # --- places ------------------------------------------------------------------------

    async def _note_places(self, conn, fiche_id) -> None:
        """Places restantes pour le profil de l'appelant, lues en direct dans la table places."""
        f = await conn.fetchrow(
            "SELECT f.profil, f.aucune_place, f.securite, p.libelle, pl.disponibles FROM fiches f "
            "LEFT JOIN profils p ON p.code = f.profil "
            "LEFT JOIN places pl ON pl.departement = f.departement AND pl.profil = f.profil WHERE f.id = $1",
            fiche_id)
        if not f or not f["profil"] or not self.couvert or f["securite"]:
            self.notes.pop("places", None)
            return
        situation = f["libelle"].lower()
        if f["aucune_place"]:
            self.notes["places"] = (
                f"PLACES : il n'y a plus de place pour sa situation ({situation}) en {self.nom_departement} en ce "
                f"moment. Dis-le avec délicatesse, une fois. Explique que son dossier est mis en attente et que "
                f"les équipes du cent quinze seront alertées dès qu'une place se libère. Ne promets rien.")
        else:
            self.notes["places"] = (
                f"PLACES : il reste en ce moment des places pour sa situation ({situation}) en "
                f"{self.nom_departement}. Tu peux le lui dire une fois, sans promettre : c'est un écoutant du "
                f"cent quinze qui attribue les places, selon les priorités.")

    # --- analyse du penseur ------------------------------------------------------------

    async def maj(self, fiche: dict, transcription: str) -> None:
        """Enregistre la fiche et met à jour les notes pour MIKI."""
        if not self.appel_id:
            return
        try:
            async with self.pool.acquire() as conn, conn.transaction():
                await self._maj_lieu(conn, fiche)
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
                await self._note_places(conn, fiche_id)
                await self.vk.hset(f"appel:{self.appel_id}", mapping={"fiche": str(fiche_id), "maj": time.time()})
        except Exception:
            logger.exception("enregistrement de la fiche impossible (l'appel continue)")

    # --- fin d'appel -------------------------------------------------------------------

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
                    self.langue_couverte, f["niveau_urgence"] if f else None, issue,
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
