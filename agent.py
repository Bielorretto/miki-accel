"""Miki — agent vocal 115 : parleur Mistral Large 4, voix ElevenLabs, en anglais, fiche en direct.

Tout ce que dit Miki (accueil compris) est généré par le parleur Mistral.
Avec les clés LiveKit, le détecteur de fin de tour tourne sur LiveKit Cloud (sinon repli sur
le VAD seul), et le barge-in est désactivé : le bruit ambiant ne coupe plus Miki.
La fiche est publiée à chaque analyse du penseur dans sessions/en_cours/, que dashboard.py
affiche en temps réel ; en fin d'appel elle devient un dossier dans dossiers/.

Lancer :  python agent.py console     (et dans un autre terminal : python dashboard.py)
"""

import asyncio
import csv
import json
import logging
import os
import re
import statistics
import time
import wave
from pathlib import Path

import numpy as np
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from livekit import rtc
from openai import AsyncOpenAI
from livekit.agents import room_io
from livekit.agents.types import NOT_GIVEN
from livekit.agents.utils import is_given
from livekit.agents.voice.generation import INSTRUCTIONS_MESSAGE_ID
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    ConversationItemAddedEvent,
    JobContext,
    JobProcess,
    cli,
    inference,
    llm,
    stt,
)
from livekit.plugins import deepgram, elevenlabs, groq, silero
from livekit.plugins import anthropic as lk_anthropic
from livekit.plugins import noise_cancellation
from livekit.plugins import openai as lk_openai

from priorite import compute_priority
from prompts import GREETING_TEXT, TALKER_PROMPT, THINKER_PROMPT
from suivi import LANGUES_PARLEES, SuiviAppel

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger("miki")
OUT_DIR = Path(__file__).parent / "sessions"
LIVE_DIR = OUT_DIR / "en_cours"
DOSSIERS_DIR = Path(__file__).parent / "dossiers"

# filet de sécurité si le parleur écrit un numéro en chiffres ("115" serait lu "un un cinq")
NUMEROS = {"115": "cent quinze", "112": "cent douze", "119": "cent dix-neuf", "15": "quinze", "17": "dix-sept"}
NUMEROS_RE = re.compile(r"\b(" + "|".join(NUMEROS) + r")\b")


async def numeros_en_lettres(text):
    reste = ""  # chiffres en fin de morceau : le nombre peut continuer dans le suivant
    async for chunk in text:
        chunk = reste + chunk
        m = re.search(r"\d+$", chunk)
        reste = m.group() if m else ""
        if out := chunk[: len(chunk) - len(reste)]:
            yield NUMEROS_RE.sub(lambda m: NUMEROS[m.group()], out)
    if reste:
        yield NUMEROS_RE.sub(lambda m: NUMEROS[m.group()], reste)


# Gain de la voix de MIKI (MIKI_VOLUME, 1.0 = inchangé). ElevenLabs sort un niveau moyen bas
# (-18 dBFS) mais des crêtes proches du maximum : un simple gain saturerait. La tangente
# hyperbolique est un limiteur doux : quasi linéaire à bas niveau, elle arrondit les crêtes.
VOLUME = float(os.environ.get("MIKI_VOLUME", "2.0"))


def amplifier(frame: rtc.AudioFrame) -> rtc.AudioFrame:
    if VOLUME == 1.0:
        return frame
    x = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
    y = np.tanh(x * VOLUME) * 32767.0
    return rtc.AudioFrame(y.astype(np.int16).tobytes(), frame.sample_rate, frame.num_channels,
                          frame.samples_per_channel)


# mot transcrit avec une confiance Deepgram sous ce seuil : marqué comme douteux pour MIKI
CONFIANCE_MIN = float(os.environ.get("STT_CONFIANCE_MIN", "0.6"))
DOUTE = " (?)"

# vocabulaire que Deepgram doit reconnaître en priorité (keyterm prompting de nova-3)
TERMES_115 = [
    "cent quinze", "SAMU social", "hébergement", "mise à l'abri", "foyer", "CHRS", "hôtel social",
    "maraude", "accueil de jour", "Emmaüs", "Croix-Rouge", "Restos du Cœur", "squat", "expulsé",
    "expulsion", "demandeur d'asile", "titre de séjour", "OFII", "CADA", "violences conjugales",
    "enceinte", "Paris", "Marseille", "Lyon", "Toulouse", "Nice", "Nantes", "Montpellier",
    "Strasbourg", "Bordeaux", "Lille", "Rennes", "Grenoble", "Saint-Denis", "gare", "Part-Dieu",
]


# Accusé de réception dit dès la fin de la phrase de l'appelant, pendant que le modèle prépare sa réponse :
# la personne entend MIKI en quelques centaines de millisecondes au lieu de 1,5 à 2 s. Enregistrés au
# début de l'appel avec la voix de MIKI, joués à tour de rôle. MIKI_ACCUSE=0 pour couper.
# sans « mmh » ni points de suspension : la voix les rendait en « mmmh », « euuuh » traînants
# (pas de « très bien » : déplacé après « il m'a frappée »)
ACCUSES = ["D'accord.", "Je vois.", "Entendu."]
NOTE_ACCUSE = ("(Consigne interne, ne pas la lire à voix haute) Une courte interjection (« d'accord », « je vois », "
               "« entendu ») vient d'être dite à l'appelant juste avant ta réponse : n'en commence pas par une "
               "interjection, enchaîne directement.")


def notes_en_fin(chat_ctx: ChatContext) -> ChatContext:
    """Garde le prompt système fixe et passe les notes de la base (qui changent pendant l'appel) en fin.

    Claude ne relit son cache que si le début de la requête est identique d'un tour à l'autre : avec les
    notes collées au prompt système, le cache était réécrit à chaque tour sans jamais être relu.
    """
    i = chat_ctx.index_by_id(INSTRUCTIONS_MESSAGE_ID)
    texte = chat_ctx.items[i].text_content if i is not None else None
    if not texte or not texte.startswith(TALKER_PROMPT) or texte == TALKER_PROMPT:
        return chat_ctx
    dynamique = texte[len(TALKER_PROMPT):]
    chat_ctx = chat_ctx.copy()
    chat_ctx.items[i] = ChatMessage(id=INSTRUCTIONS_MESSAGE_ID, role="system", content=[TALKER_PROMPT])
    chat_ctx.items.append(ChatMessage(role="user", content=[f"(Consigne interne, ne pas la lire à voix haute){dynamique}"]))
    return chat_ctx


class Assistant115(Agent):
    """Agent d'accueil : aucun outil, il ne fait que poser des questions."""

    def __init__(self) -> None:
        super().__init__(instructions=TALKER_PROMPT)
        # MIKI_ENREGISTRER_VOIX=1 : garde la voix générée pour la comparer à ce qu'entend l'appelant
        self.voix: list[bytes] | None = [] if os.environ.get("MIKI_ENREGISTRER_VOIX") == "1" else None
        self.voix_format = (16000, 1)
        self.accuses: dict[str, list[rtc.AudioFrame]] = {}   # préenregistrés au début de l'appel
        self.accuse_suivant = 0
        self.en_francais = True

    async def llm_node(self, chat_ctx: ChatContext, tools, model_settings):
        # réponse à l'appelant (et non consigne système) : l'accusé de réception est déjà parti
        accuse = self.accuse_actif() and isinstance(chat_ctx.items[-1] if chat_ctx.items else None, ChatMessage) \
            and chat_ctx.items[-1].role == "user"
        # Mistral Large 4 se met à raisonner (réponse illisible en streaming) quand le dernier
        # message est une consigne système : on la lui passe en message utilisateur.
        last = chat_ctx.items[-1] if chat_ctx.items else None
        if isinstance(last, ChatMessage) and last.role == "system" and len(chat_ctx.items) > 1:
            chat_ctx = chat_ctx.copy()
            chat_ctx.items[-1] = ChatMessage(
                role="user",
                content=[f"(Consigne interne, ne pas la lire à voix haute) {last.text_content}"],
            )
        chat_ctx = notes_en_fin(chat_ctx)
        if accuse:
            chat_ctx = chat_ctx.copy()
            chat_ctx.items.append(ChatMessage(role="user", content=[NOTE_ACCUSE]))
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk

    def accuse_actif(self) -> bool:
        # en français seulement : les accusés sont enregistrés dans cette langue
        return len(self.accuses) == len(ACCUSES) and self.en_francais

    async def preparer_accuses(self, tts) -> None:
        for texte in ACCUSES:
            self.accuses[texte] = [amplifier(ev.frame) async for ev in tts.synthesize(texte)]

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        # appelé à la fin du tour, juste avant que la réponse (déjà en préparation) ne soit planifiée :
        # l'accusé passe donc avant elle
        if not self.accuse_actif():
            return
        texte = ACCUSES[self.accuse_suivant % len(ACCUSES)]
        self.accuse_suivant += 1

        async def audio():
            for frame in self.accuses[texte]:
                yield frame
        self.session.say(texte, audio=audio(), allow_interruptions=False, add_to_chat_ctx=False)

    async def stt_node(self, audio, model_settings):
        # Deepgram donne une confiance par mot : les mots douteux sont suivis de « (?) ». MIKI ne fait
        # confirmer que ceux-là, au lieu de faire répéter chaque réponse courte
        async for ev in Agent.default.stt_node(self, audio, model_settings):
            if isinstance(ev, stt.SpeechEvent) and ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                for alt in ev.alternatives:
                    if alt.words and all(is_given(w.confidence) for w in alt.words):
                        alt.text = " ".join(str(w) + (DOUTE if w.confidence < CONFIANCE_MIN else "")
                                            for w in alt.words)
            yield ev

    async def tts_node(self, text, model_settings):
        async for frame in Agent.default.tts_node(self, numeros_en_lettres(text), model_settings):
            frame = amplifier(frame)
            if self.voix is not None:   # diagnostic : copie de la voix générée, avant tout transport
                self.voix_format = (frame.sample_rate, frame.num_channels)
                self.voix.append(bytes(frame.data))
            yield frame

    def sauver_voix(self, chemin: Path) -> None:
        if not self.voix:
            return
        with wave.open(str(chemin), "wb") as w:
            w.setnchannels(self.voix_format[1])
            w.setsampwidth(2)
            w.setframerate(self.voix_format[0])
            w.writeframes(b"".join(self.voix))


# La grille de priorité (compute_priority) vit dans priorite.py, avec le classement par département.


def texte_reponse(content) -> str:
    """Mistral Large 4 / Magistral renvoient une liste de morceaux (réflexion + texte) : on garde le texte."""
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if c.get("type") == "text")
    return content


class Thinker:
    """LLM puissant en parallèle : remplit la fiche, détecte l'urgence, guide le parleur."""

    def __init__(self, session: AgentSession, agent: Assistant115, call_id: str, suivi: SuiviAppel,
                 suivi_pret: asyncio.Future) -> None:
        self.session = session
        self.suivi = suivi
        self.suivi_pret = suivi_pret  # ouverture de l'appel en base, à attendre avant d'écrire
        self.agent = agent
        self.call_id = call_id
        self.debut = time.strftime("%Y-%m-%dT%H:%M:%S")
        # "fournisseur:modèle", essayés dans l'ordre (repli si saturé ou quota dépassé)
        self.models = os.environ.get("THINKER_MODELS", "mistral:mistral-large-4").split(",")
        # un client par fournisseur dont la clé est présente : une clé absente (Mistral, plus utilisé
        # en production) ne doit pas faire planter l'appel, le modèle est simplement sauté
        self.groq = AsyncOpenAI(
            api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1"
        ) if os.environ.get("GROQ_API_KEY") else None
        self.mistral = AsyncOpenAI(
            api_key=os.environ["MISTRAL_API_KEY"], base_url="https://api.mistral.ai/v1",
            timeout=45, max_retries=0,  # Large 4 peut rester bloqué : on passe au modèle suivant
        ) if os.environ.get("MISTRAL_API_KEY") else None
        self.claude = AsyncAnthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
        self.gemini = genai.Client() if os.environ.get("GOOGLE_API_KEY") else None
        self.last: dict = {}
        self.vital_alerted = False
        self._task: asyncio.Task | None = None
        self._pending = False

    def trigger(self) -> None:
        if self._task and not self._task.done():
            self._pending = True  # relancer après l'analyse en cours
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            self._pending = False
            try:
                await self._analyze()
            except Exception:
                logger.exception("erreur du penseur")
            if not self._pending:
                return

    def transcript(self) -> str:
        return "\n".join(
            f"{'APPELANT' if m.role == 'user' else 'ACCUEIL'}: {m.text_content}"
            for m in self.session.history.items
            if isinstance(m, ChatMessage) and m.role in ("user", "assistant") and m.text_content
        )

    def dossier(self, statut: str) -> dict:
        fiche = {k: v for k, v in self.last.items() if k not in ("consigne", "priorite")}
        return {
            "id": self.call_id,
            "statut": statut,
            "debut": self.debut,
            "maj": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "priorite": self.last.get("priorite"),
            "langue": self.suivi.langue,   # langue de l'appelant, suivie pendant l'appel (affichée sur la fiche)
            **fiche,
            "transcription": self.transcript(),
        }

    def publish(self) -> None:
        """Fiche en cours, lue en direct par dashboard.py (écriture atomique)."""
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LIVE_DIR / f".{self.call_id}.tmp"
        tmp.write_text(json.dumps(self.dossier("en_cours"), ensure_ascii=False, indent=2))
        tmp.replace(LIVE_DIR / f"{self.call_id}.json")

    async def finalize(self) -> None:
        """Dernière analyse en fin d'appel, pour intégrer les derniers échanges."""
        if self._task and not self._task.done():
            await self._task
        await self._analyze(final=True)

    async def _call(self, model: str, transcript: str) -> str:
        provider, name = model.split(":", 1)
        if provider in ("groq", "mistral"):
            client = self.groq if provider == "groq" else self.mistral
            # Large 4 sans réflexion : environ 3 s au lieu de plus d'une minute, fiche aussi juste
            kw = {"reasoning_effort": "high" if provider == "groq" else "none"}
            resp = await client.chat.completions.create(
                model=name,
                **kw,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": THINKER_PROMPT},
                    {"role": "user", "content": transcript},
                ],
            )
            return texte_reponse(resp.choices[0].message.content)
        if provider == "claude":
            resp = await self.claude.messages.create(
                model=name,
                max_tokens=16000,
                # réflexion courte : la fiche doit arriver avant le tour suivant
                output_config={"effort": "low"},
                system=THINKER_PROMPT,
                messages=[{"role": "user", "content": transcript}],
            )
            text = next(b.text for b in resp.content if b.type == "text")
            # Claude peut entourer le JSON de ```json ... ``` : on garde l'objet seul
            return text[text.find("{") : text.rfind("}") + 1]
        resp = await self.gemini.aio.models.generate_content(
            model=name,
            contents=transcript,
            config=genai_types.GenerateContentConfig(
                system_instruction=THINKER_PROMPT, response_mime_type="application/json"
            ),
        )
        return resp.text

    async def _analyze(self, final: bool = False) -> None:
        transcript = self.transcript()
        if not transcript:
            return
        transcript = self.suivi.contexte_penseur() + transcript
        t0 = time.perf_counter()
        for model in self.models:
            try:
                # JSON lu ici : une réponse illisible fait aussi passer au modèle suivant
                fiche = json.loads(await self._call(model.strip(), transcript))
                break
            except Exception as e:
                logger.warning("penseur : %s indisponible (%s), modèle suivant", model, str(e)[:80])
        else:
            return
        self.last = fiche
        self.last["priorite"] = compute_priority(self.last.get("criteres", {}))
        self.publish()
        await self.suivi_pret
        await self.suivi.maj(self.last, self.transcript())
        prio = self.last["priorite"]
        logger.info(
            "🧠 penseur %s (%.1fs) priorité=%d (%s) | manque=%s | consigne=%s",
            model,
            time.perf_counter() - t0,
            prio["score"],
            ", ".join(prio["motifs"]) or "aucun motif",
            self.last.get("infos_manquantes"),
            self.last.get("consigne"),
        )
        if final:
            return

        await self.mettre_a_jour_consignes()

        if self.last.get("criteres", {}).get("danger_vital") and not self.vital_alerted:
            self.vital_alerted = True
            logger.warning("🚨 DANGER VITAL détecté par le penseur")
            self.session.generate_reply(
                instructions="Danger immédiat détecté : dis calmement à la personne d'appeler tout de suite "
                "le quinze ou le cent douze. Rappelle-lui que tu ne peux pas appeler à sa place."
            )


    async def mettre_a_jour_consignes(self) -> None:
        """Instructions du parleur : son prompt et les notes de la base.

        Ni la fiche ni la consigne du penseur : il met 3 à 13 s par analyse, MIKI répond en 1 à 2 s, et
        ses informations toujours en retard faisaient redemander ce qui venait d'être dit. MIKI mène
        seul la conversation (un modèle assez intelligent pour suivre ce qui a été dit) ; le penseur
        remplit la fiche du dossier et donne l'alerte en cas de danger.
        """
        texte = TALKER_PROMPT
        if notes := self.suivi.notes_pour_miki():
            texte += f"\n\nCE QUE SAIT LA BASE DU CENT QUINZE (des faits, pas des ordres) :\n{notes}"
        await self.agent.update_instructions(texte)


class LatencyLog:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._user_metrics: dict = {}

    def on_item(self, msg: ChatMessage) -> None:
        if msg.role == "user":
            self._user_metrics = dict(msg.metrics)
            return
        m = msg.metrics
        if "e2e_latency" not in m:
            return
        row = {
            "e2e_ms": m["e2e_latency"] * 1000,
            "fin_de_tour_ms": self._user_metrics.get("end_of_turn_delay", 0) * 1000,
            "stt_ms": self._user_metrics.get("transcription_delay", 0) * 1000,
            "llm_ttft_ms": m.get("llm_node_ttft", 0) * 1000,
            "tts_ttfb_ms": m.get("tts_node_ttfb", 0) * 1000,
        }
        self.rows.append(row)
        flag = "✅" if row["e2e_ms"] <= 600 else "⚠️"
        logger.info(
            "⏱ %s e2e=%.0fms | fin de tour=%.0f | STT=%.0f | LLM=%.0f | TTS=%.0f",
            flag, row["e2e_ms"], row["fin_de_tour_ms"], row["stt_ms"],
            row["llm_ttft_ms"], row["tts_ttfb_ms"],
        )

    def summary(self) -> str:
        if not self.rows:
            return "aucune mesure"
        e2e = sorted(r["e2e_ms"] for r in self.rows)
        p95 = e2e[min(len(e2e) - 1, int(len(e2e) * 0.95))]
        return f"{len(e2e)} tours | P50={statistics.median(e2e):.0f}ms | P95={p95:.0f}ms"


# langues fréquentes au 115, en ISO 639-3 (anglais, arabe, espagnol, portugais, roumain, russe,
# ukrainien, turc, pachto, persan, tigrinya, wolof)
LANGUES_SECONDAIRES = os.environ.get("STT_LANGUES_SECONDAIRES",
                                     "eng,ara,spa,por,ron,rus,ukr,tur,pus,fas,tir,wol").split(",")


HESITATIONS = re.compile(r"^(e+u+h*|h?e+m+|h?u+m+|a+h+|o+h+|b+e+n+|b+a+h+|э+|а+|у+|м+|ээ+)$", re.IGNORECASE)


def vrais_mots(texte: str) -> int:
    """Nombre de mots d'au moins deux lettres, sans les hésitations (« euh », « э-э-э »…)."""
    mots = [m for m in re.findall(r"\w+", texte) if len(m) >= 2]
    return sum(1 for m in mots if not HESITATIONS.match(m))


def build_stt():
    """Transcription de l'appelant : ElevenLabs Scribe (temps réel) ou Deepgram, selon STT_PROVIDER."""
    langue = os.environ.get("STT_LANGUAGE", "en")
    # l'audio d'un appel téléphonique est en 8 kHz : envoyer plus doublerait le débit montant
    # sans rien apporter, et sature les petites connexions au détriment de la voix de MIKI
    frequence = int(os.environ.get("STT_SAMPLE_RATE", "8000"))
    if os.environ.get("STT_PROVIDER", "elevenlabs") == "elevenlabs":
        # ElevenLabs détecte lui-même la fin de phrase (0,5 s de silence) : sur une ligne téléphonique
        # bruitée, le VAD local ne voit jamais le silence et la phrase n'était validée que bien plus tard
        silence = float(os.environ.get("STT_SILENCE_S", "0.5"))
        return elevenlabs.STT(
            model="scribe_v2_realtime", language_code=langue, sample_rate=frequence,
            server_vad={"vad_silence_threshold_secs": silence, "min_silence_duration_ms": int(silence * 1000),
                        "vad_threshold": float(os.environ.get("STT_VAD_SEUIL", "0.55"))},
            # le français reste la langue attendue, mais ces langues sont reconnues et la langue
            # entendue est signalée : MIKI suit l'appelant et on cherche un écoutant qui la parle
            secondary_languages=LANGUES_SECONDAIRES, include_language_detection=True,
        )
    return deepgram.STT(model=os.environ.get("STT_MODEL", "nova-3"), language=langue, sample_rate=frequence,
                        keyterm=TERMES_115)


def build_tts():
    # clé lue dans ELEVEN_API_KEY ; voix par défaut : Sarah, voix « premade » utilisable en gratuit
    return elevenlabs.TTS(
        voice_id=os.environ.get("ELEVEN_VOICE_ID", "EXAVITQu4vr4xnSDxMaL"),
        model=os.environ.get("ELEVEN_MODEL", "eleven_flash_v2_5"),
        language=os.environ.get("TTS_LANGUAGE", "en"),
        # audio brut plutôt que le MP3 32 kbit/s par défaut : au téléphone, la double compression
        # (MP3 puis G.711 à 8 kHz) produit grésillements et son métallique
        encoding=os.environ.get("ELEVEN_ENCODING", "pcm_16000"),
        # débit de parole : 0.8 (lent) → 1.2 (rapide), 1.0 = normal
        voice_settings=elevenlabs.VoiceSettings(
            stability=0.5, similarity_boost=0.75, speed=float(os.environ.get("ELEVEN_SPEED", "0.9"))
        ),
    )


class ClaudeVoix(lk_anthropic.LLM):
    """Claude pour la voix de MIKI : sans réflexion et à effort bas, la première syllabe arrive vite.

    Le plugin LiveKit n'expose pas ces réglages : on les ajoute à chaque requête.
    """

    def chat(self, *, extra_kwargs=NOT_GIVEN, **kwargs):
        # Sonnet 5.5 refuse « disabled » : « between_tools » coupe la réflexion (hors outils, qu'il n'a pas)
        coupee = "between_tools" if "sonnet-5-5" in self.model else "disabled"
        extra = {"thinking": {"type": coupee}, "output_config": {"effort": "low"}}
        if is_given(extra_kwargs):
            extra.update(extra_kwargs)
        return super().chat(extra_kwargs=extra, **kwargs)


def build_llm():
    # un ou plusieurs modèles séparés par des virgules, essayés dans l'ordre (repli si erreur)
    models = [m.strip() for m in os.environ.get("TALKER_MODEL", "mistral:mistral-large-4").split(",") if m.strip()]
    llms = []
    for model in models:
        # "mistral:<modèle>" passe par l'API Mistral, un nom seul reste sur Groq
        if model.startswith("mistral:"):
            llms.append(lk_openai.LLM(
                model=model.split(":", 1)[1],
                base_url="https://api.mistral.ai/v1",
                api_key=os.environ["MISTRAL_API_KEY"],
                temperature=0.4,
                # sans ça Large 4 raisonne avant de parler : lent, et format illisible en streaming
                reasoning_effort="none",
            ))
            continue
        # "claude:<modèle>" passe par l'API Anthropic (clé ANTHROPIC_API_KEY) ; pas de temperature,
        # refusée par les modèles Claude récents
        if model.startswith("claude:"):
            llms.append(ClaudeVoix(model=model.split(":", 1)[1], max_tokens=400, caching="ephemeral"))
            continue
        # "gemini:<modèle>" passe par l'API Gemini compatible OpenAI
        if model.startswith("gemini:"):
            llms.append(lk_openai.LLM(
                model=model.split(":", 1)[1],
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=os.environ["GOOGLE_API_KEY"],
                temperature=0.4,
                reasoning_effort="none",
            ))
            continue
        # jamais de raisonnement long dans le chemin critique
        kw = {}
        if "gpt-oss" in model:
            kw["reasoning_effort"] = "low"
        elif "qwen" in model:
            kw["reasoning_effort"] = "none"
        llms.append(groq.LLM(model=model, temperature=0.4, **kw))
    # Claude répond d'habitude en moins d'une seconde : au-delà de 2 s sans réponse, le modèle suivant
    # prend le relais (le délai par défaut de 5 s laissait l'appelant dans le silence)
    attente = float(os.environ.get("LLM_ATTENTE_MAX_S", "2.0"))
    return llms[0] if len(llms) == 1 else llm.FallbackAdapter(llms, attempt_timeout=attente)


def reduction_bruit(params):
    """Supprime le bruit ET les voix de fond de l'appelant avant la transcription (LiveKit BVC).

    Sans ça, dans la rue ou un hall de gare, les conversations autour sont prises pour la voix de
    l'appelant : MIKI attend qu'il « finisse » (longs silences) et répond à des phrases qui ne lui
    sont pas adressées. Modèle téléphonique pour les appels SIP. NOISE_CANCELLATION=off pour couper.
    """
    if os.environ.get("NOISE_CANCELLATION", "bvc") == "off":
        return None
    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        return noise_cancellation.BVCTelephony()
    return noise_cancellation.BVC()


def prewarm(proc: JobProcess) -> None:
    # moins sensible que par défaut (0,5 et 50 ms) : un bruit bref ne passe plus pour de la parole
    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=float(os.environ.get("VAD_SEUIL", "0.6")),
        min_speech_duration=float(os.environ.get("VAD_PAROLE_MIN_S", "0.15")),
        # silence avant de considérer que la personne a fini (0,55 s par défaut) : c'était 0,58 s perdues
        # à chaque tour ; le détecteur de fin de tour garde la main si la phrase semble inachevée
        min_silence_duration=float(os.environ.get("VAD_SILENCE_MIN_S", "0.4")),
    )


# laisse le temps à l'analyse finale du penseur avant de couper le process
server = AgentServer(setup_fnc=prewarm, shutdown_process_timeout=30)


# MIKI_AGENT_NAME=miki-test : ne reçoit que les appels qu'on lui envoie (robot de test), jamais les vrais
@server.rtc_session(agent_name=os.environ.get("MIKI_AGENT_NAME", ""))
async def entrypoint(ctx: JobContext) -> None:
    if not (os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET")):
        raise RuntimeError("LIVEKIT_API_KEY / LIVEKIT_API_SECRET manquants dans .env : "
                           "le détecteur de fin de tour LiveKit Cloud ne peut pas tourner")
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=build_stt(),
        llm=build_llm(),
        tts=build_tts(),
        turn_handling={
            # "v1-mini" tourne sur la machine ; "v1" (LiveKit Cloud) est meilleur mais demande un
            # aller-retour réseau à chaque fin de phrase : sur une connexion faible il dépasse 1 s
            "turn_detection": inference.TurnDetector(version=os.environ.get("TURN_DETECTOR", "v1-mini")),
            # attente après la fin de la phrase : min_delay si elle semble finie, max_delay sinon.
            # 3 s de max faisait attendre 3 s après chaque réponse courte (« oui », « vingt ans ») ;
            # la transcription ElevenLabs a déjà attendu 0,5 s de silence avant
            "endpointing": {
                "min_delay": float(os.environ.get("FIN_DE_TOUR_MIN_S", "0.2")),
                "max_delay": float(os.environ.get("FIN_DE_TOUR_MAX_S", "1.0")),
            },
            # pas de barge-in : Miki finit toujours sa phrase, le bruit ambiant ne la coupe plus
            # (ce qui est dit pendant qu'elle parle est ignoré)
            "interruption": {"enabled": False, "discard_audio_if_uninterruptible": True},
        },
    )
    agent = Assistant115()
    call_id = time.strftime("%Y%m%d-%H%M%S")
    suivi = SuiviAppel()

    # résolu une fois l'appel ouvert en base (ou en échec) : le penseur l'attend avant d'écrire
    suivi_pret = asyncio.get_running_loop().create_future()

    async def _transcrire(role: str, texte: str) -> None:
        await suivi_pret   # l'accueil est dit avant l'ouverture en base : il attend, l'ordre est conservé
        await suivi.message(role, texte.replace(DOUTE, ""))   # le dossier garde le texte sans marques

    async def _ouvrir_suivi() -> None:
        try:
            # le numéro arrive dans les attributs du participant SIP ; il n'est jamais stocké en clair
            participant = await ctx.wait_for_participant()
            await suivi.ouvrir(participant.attributes.get("sip.phoneNumber"))
            await thinker.mettre_a_jour_consignes()   # appelant déjà connu : MIKI le sait dès le début
        finally:
            suivi_pret.set_result(None)
    thinker = Thinker(session, agent, call_id, suivi, suivi_pret)

    async def _changer_langue(langue: str) -> None:
        await suivi_pret
        await suivi.changer_langue(langue)
        if langue in LANGUES_PARLEES:   # sinon la voix reste en français (note dans suivi)
            session.tts.update_options(language=langue)
        agent.en_francais = langue == "fr"
        await thinker.mettre_a_jour_consignes()

    detection = {"langue": None, "fois": 0}

    @session.on("user_input_transcribed")
    def _on_transcription(ev) -> None:
        """Le français reste la langue de l'appel tant que l'appelant n'a pas clairement parlé une autre langue.

        La détection se trompe sur les hésitations et les phrases courtes (un « euh… ouais » a déjà été
        pris pour du russe) : il faut deux phrases d'affilée dans la même langue étrangère, d'au moins
        quatre vrais mots. Une seule phrase en français suffit pour y revenir.
        """
        langue = getattr(ev.language, "language", None) if ev.language else None
        if not ev.is_final or not langue:
            return
        mots = vrais_mots(ev.transcript)
        if langue == suivi.langue:
            detection.update(langue=None, fois=0)
            return
        if langue == "fr":
            if mots >= 3:
                asyncio.create_task(_changer_langue("fr"))
            return
        if mots < 4:
            return
        detection["fois"] = detection["fois"] + 1 if detection["langue"] == langue else 1
        detection["langue"] = langue
        if detection["fois"] >= 2:
            detection.update(langue=None, fois=0)
            asyncio.create_task(_changer_langue(langue))
    latency = LatencyLog()

    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        if not isinstance(ev.item, ChatMessage):
            return
        latency.on_item(ev.item)
        thinker.publish()  # transcription en direct sur le site
        if ev.item.role in ("user", "assistant") and ev.item.text_content:
            asyncio.create_task(_transcrire(ev.item.role, ev.item.text_content))
        if ev.item.role == "user":
            thinker.trigger()

    async def _save() -> None:
        OUT_DIR.mkdir(exist_ok=True)
        stamp = call_id
        if latency.rows:
            with open(OUT_DIR / f"{stamp}-latence.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=latency.rows[0].keys())
                w.writeheader()
                w.writerows(latency.rows)
        try:
            await asyncio.wait_for(thinker.finalize(), timeout=20)
        except Exception:
            logger.exception("analyse finale impossible, dernière fiche conservée")
        # l'appel est fini : la fiche devient un dossier, et quitte la liste « en cours »
        appel = thinker.dossier("termine")
        DOSSIERS_DIR.mkdir(exist_ok=True)
        (DOSSIERS_DIR / f"{call_id}.json").write_text(json.dumps(appel, ensure_ascii=False, indent=2))
        (LIVE_DIR / f"{call_id}.json").unlink(missing_ok=True)
        agent.sauver_voix(OUT_DIR / f"{call_id}-voix.wav")
        await suivi_pret
        await suivi.clore()
        logger.info("📋 Dossier créé :\n%s", json.dumps(appel, ensure_ascii=False, indent=2))
        logger.info("📊 Latence : %s — résultats dans %s", latency.summary(), OUT_DIR)

    ctx.add_shutdown_callback(_save)

    await session.start(
        agent=agent, room=ctx.room, record=False,
        room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions(noise_cancellation=reduction_bruit)),
    )
    asyncio.create_task(_ouvrir_suivi())
    if os.environ.get("MIKI_ACCUSE", "1") != "0":
        asyncio.create_task(agent.preparer_accuses(session.tts))   # pendant l'accueil
    # l'accueil passe aussi par le parleur Mistral : tout ce que dit Miki vient de lui
    session.generate_reply(
        instructions=f"Début de l'appel : accueille la personne avec ce message exact, mot pour mot : "
        f"\"{GREETING_TEXT}\""
    )


if __name__ == "__main__":
    cli.run_app(server)