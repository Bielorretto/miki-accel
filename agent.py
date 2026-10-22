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
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
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
)
from livekit.plugins import deepgram, elevenlabs, groq, silero
from livekit.plugins import openai as lk_openai

from priorite import compute_priority
from prompts import GREETING_TEXT, TALKER_PROMPT, THINKER_PROMPT
from suivi import SuiviAppel

load_dotenv(Path(__file__).parent / ".env")
logger = logging.getLogger("miki")
OUT_DIR = Path(__file__).parent / "sessions"
LIVE_DIR = OUT_DIR / "en_cours"
DOSSIERS_DIR = Path(__file__).parent / "dossiers"

# filet de sécurité si le parleur écrit un numéro en chiffres ("115" serait lu "one one five")
NUMEROS = {"115": "one hundred fifteen", "112": "one hundred twelve", "15": "fifteen", "17": "seventeen"}
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


class Assistant115(Agent):
    """Agent d'accueil : aucun outil, il ne fait que poser des questions."""

    def __init__(self) -> None:
        super().__init__(instructions=TALKER_PROMPT)

    async def llm_node(self, chat_ctx: ChatContext, tools, model_settings):
        # Mistral Large 4 se met à raisonner (réponse illisible en streaming) quand le dernier
        # message est une consigne système : on la lui passe en message utilisateur.
        last = chat_ctx.items[-1] if chat_ctx.items else None
        if isinstance(last, ChatMessage) and last.role == "system" and len(chat_ctx.items) > 1:
            chat_ctx = chat_ctx.copy()
            chat_ctx.items[-1] = ChatMessage(
                role="user",
                content=[f"(Internal instruction, do not read it aloud) {last.text_content}"],
            )
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk

    async def tts_node(self, text, model_settings):
        async for frame in Agent.default.tts_node(self, numeros_en_lettres(text), model_settings):
            yield frame


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
        self.groq = AsyncOpenAI(
            api_key=os.environ.get("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1"
        )
        self.mistral = AsyncOpenAI(
            api_key=os.environ.get("MISTRAL_API_KEY"), base_url="https://api.mistral.ai/v1",
            timeout=45, max_retries=0,  # Large 4 peut rester bloqué : on passe au modèle suivant
        )
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
            "langue": os.environ.get("STT_LANGUAGE", "en"),   # langue de l'appel, affichée sur la fiche
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
        note_lieu = await self.suivi.maj(self.last, self.transcript())
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

        fiche = {k: v for k, v in self.last.items() if k not in ("consigne", "infos_manquantes")}
        await self.agent.update_instructions(
            f"{TALKER_PROMPT}\n\nCURRENT RECORD (do not ask again for what is known): "
            f"{json.dumps(fiche, ensure_ascii=False)}"
            f"\nMISSING INFORMATION: {', '.join(self.last.get('infos_manquantes') or []) or 'none'}"
            f"\n\nSUPERVISOR INSTRUCTION, TO FOLLOW FIRST in your next answer "
            f"(unless the person just said something urgent): "
            f"{self.last.get('consigne') or 'none'}"
            + (f"\n\nLOCATION NOTE, TO HANDLE NOW: {note_lieu}" if note_lieu else "")
        )

        if self.last.get("criteres", {}).get("danger_vital") and not self.vital_alerted:
            self.vital_alerted = True
            logger.warning("🚨 DANGER VITAL détecté par le penseur")
            self.session.generate_reply(
                instructions="Life-threatening danger detected: calmly tell the person to call "
                "fifteen or one hundred twelve right now. Remind them you cannot call for them."
            )


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


def build_tts():
    # clé lue dans ELEVEN_API_KEY ; voix par défaut : Sarah, voix « premade » utilisable en gratuit
    return elevenlabs.TTS(
        voice_id=os.environ.get("ELEVEN_VOICE_ID", "EXAVITQu4vr4xnSDxMaL"),
        model=os.environ.get("ELEVEN_MODEL", "eleven_flash_v2_5"),
        language=os.environ.get("TTS_LANGUAGE", "en"),
        # débit de parole : 0.8 (lent) → 1.2 (rapide), 1.0 = normal
        voice_settings=elevenlabs.VoiceSettings(
            stability=0.5, similarity_boost=0.75, speed=float(os.environ.get("ELEVEN_SPEED", "0.9"))
        ),
    )


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
    return llms[0] if len(llms) == 1 else llm.FallbackAdapter(llms)


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


# laisse le temps à l'analyse finale du penseur avant de couper le process
server = AgentServer(setup_fnc=prewarm, shutdown_process_timeout=30)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    if not (os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET")):
        raise RuntimeError("LIVEKIT_API_KEY / LIVEKIT_API_SECRET manquants dans .env : "
                           "le détecteur de fin de tour LiveKit Cloud ne peut pas tourner")
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(
            model=os.environ.get("STT_MODEL", "nova-3"),
            language=os.environ.get("STT_LANGUAGE", "en"),
        ),
        llm=build_llm(),
        tts=build_tts(),
        turn_handling={
            # "v1" tourne sur LiveKit Cloud avec les clés du .env (meilleur modèle que le
            # "v1-mini" local) ; si le cloud échoue, repli automatique sur le local
            "turn_detection": inference.TurnDetector(version="v1"),
            # patient avec les pauses : jusqu'à 3 s si la phrase semble inachevée
            "endpointing": {"min_delay": 0.4, "max_delay": 3.0},
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
        await suivi.message(role, texte)

    async def _ouvrir_suivi() -> None:
        try:
            # le numéro arrive dans les attributs du participant SIP ; il n'est jamais stocké en clair
            participant = await ctx.wait_for_participant()
            await suivi.ouvrir(participant.attributes.get("sip.phoneNumber"))
        finally:
            suivi_pret.set_result(None)
    thinker = Thinker(session, agent, call_id, suivi, suivi_pret)
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
        await suivi_pret
        await suivi.clore()
        logger.info("📋 Dossier créé :\n%s", json.dumps(appel, ensure_ascii=False, indent=2))
        logger.info("📊 Latence : %s — résultats dans %s", latency.summary(), OUT_DIR)

    ctx.add_shutdown_callback(_save)

    await session.start(agent=agent, room=ctx.room, record=False)
    asyncio.create_task(_ouvrir_suivi())
    # l'accueil passe aussi par le parleur Mistral : tout ce que dit Miki vient de lui
    session.generate_reply(
        instructions=f"Start of the call: greet the person with this exact message, word for word: "
        f"\"{GREETING_TEXT}\""
    )


if __name__ == "__main__":
    cli.run_app(server)