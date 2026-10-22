"""Discuter avec Miki au clavier, sans voix, pour tester l'intelligence des modèles.

Même parleur (TALKER_MODEL) et même penseur (THINKER_MODELS) que agent.py,
avec le même décalage : la consigne du penseur ne sert qu'à la réponse suivante.

Lancer :  python chat_texte.py
Changer de modèle sans toucher au .env :
          TALKER_MODEL=mistral:mistral-large-4 THINKER_MODELS=mistral:mistral-large-4 python chat_texte.py
Commandes : /fiche (fiche complète)  /reset (nouvel appel)  /quit
"""

import asyncio
import json
import os
import time
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI

from agent import compute_priority, texte_reponse
from prompts import GREETING_TEXT, TALKER_PROMPT, THINKER_PROMPT

load_dotenv(Path(__file__).parent / ".env")

TALKER_MODELS = [m.strip() for m in os.environ.get("TALKER_MODEL", "mistral:mistral-large-4").split(",") if m.strip()]
THINKER_MODELS = [m.strip() for m in os.environ.get("THINKER_MODELS", "mistral:mistral-large-4").split(",") if m.strip()]

groq = AsyncOpenAI(api_key=os.environ.get("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1")
mistral = AsyncOpenAI(api_key=os.environ.get("MISTRAL_API_KEY"), base_url="https://api.mistral.ai/v1", timeout=45, max_retries=0)
claude = AsyncAnthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
gemini = genai.Client() if os.environ.get("GOOGLE_API_KEY") else None

GRIS, BLEU, JAUNE, ROUGE, FIN = "\033[90m", "\033[94m", "\033[93m", "\033[91m", "\033[0m"


def instructions(fiche: dict) -> str:
    """Mêmes instructions que celles injectées par le penseur dans agent.py."""
    if not fiche:
        return TALKER_PROMPT
    visible = {k: v for k, v in fiche.items() if k not in ("consigne", "infos_manquantes")}
    return (
        f"{TALKER_PROMPT}\n\nCURRENT RECORD (do not ask again for what is known): "
        f"{json.dumps(visible, ensure_ascii=False)}"
        f"\nMISSING INFORMATION: {', '.join(fiche.get('infos_manquantes') or []) or 'none'}"
        f"\n\nSUPERVISOR INSTRUCTION, TO FOLLOW FIRST in your next answer "
        f"(unless the person just said something urgent): "
        f"{fiche.get('consigne') or 'none'}"
    )


async def parler(historique: list[dict], fiche: dict) -> tuple[str, str, float]:
    messages = [{"role": "system", "content": instructions(fiche)}, *historique]
    for model in TALKER_MODELS:
        client, name = (mistral, model.split(":", 1)[1]) if model.startswith("mistral:") else (groq, model)
        # Large 4 sans réflexion : sinon il réfléchit de longues secondes avant de répondre
        kw = {"reasoning_effort": "none"} if model.startswith("mistral:") else {}
        if "gpt-oss" in model:
            kw["reasoning_effort"] = "low"
        elif "qwen" in model:
            kw["reasoning_effort"] = "none"
        t0 = time.perf_counter()
        try:
            resp = await client.chat.completions.create(model=name, temperature=0.4, messages=messages, **kw)
            return texte_reponse(resp.choices[0].message.content).strip(), model, time.perf_counter() - t0
        except Exception as e:
            print(f"{GRIS}  parleur : {model} indisponible ({str(e)[:80]}), modèle suivant{FIN}")
    return "[aucun modèle parleur disponible]", "-", 0.0


def transcription(historique: list[dict]) -> str:
    return "\n".join(
        f"{'APPELANT' if m['role'] == 'user' else 'ACCUEIL'}: {m['content']}" for m in historique
    )


async def penser(historique: list[dict]) -> tuple[dict, str, float]:
    texte = transcription(historique)
    for model in THINKER_MODELS:
        provider, name = model.split(":", 1)
        t0 = time.perf_counter()
        try:
            if provider in ("groq", "mistral"):
                client = groq if provider == "groq" else mistral
                kw = {"reasoning_effort": "high" if provider == "groq" else "none"}
                resp = await client.chat.completions.create(
                    model=name,
                    **kw,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": THINKER_PROMPT}, {"role": "user", "content": texte}],
                )
                raw = texte_reponse(resp.choices[0].message.content)
            elif provider == "claude":
                resp = await claude.messages.create(
                    model=name,
                    max_tokens=16000,
                    output_config={"effort": "low"},
                    system=THINKER_PROMPT,
                    messages=[{"role": "user", "content": texte}],
                )
                text = next(b.text for b in resp.content if b.type == "text")
                raw = text[text.find("{") : text.rfind("}") + 1]
            else:
                resp = await gemini.aio.models.generate_content(
                    model=name,
                    contents=texte,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=THINKER_PROMPT, response_mime_type="application/json"
                    ),
                )
                raw = resp.text
            fiche = json.loads(raw)
            fiche["priorite"] = compute_priority(fiche.get("criteres", {}))
            return fiche, model, time.perf_counter() - t0
        except Exception as e:
            print(f"{GRIS}  penseur : {model} indisponible ({str(e)[:80]}), modèle suivant{FIN}")
    return {}, "-", 0.0


def afficher_penseur(fiche: dict, model: str, duree: float) -> None:
    if not fiche:
        return
    prio = fiche["priorite"]
    print(f"{JAUNE}  🧠 {model} ({duree:.1f}s) priorité={prio['score']} ({prio['niveau']})"
          f" {', '.join(prio['motifs'])}")
    print(f"     manque : {', '.join(fiche.get('infos_manquantes') or []) or 'rien'}")
    print(f"     consigne pour le tour suivant : {fiche.get('consigne')}{FIN}")
    if fiche.get("criteres", {}).get("danger_vital"):
        print(f"{ROUGE}  🚨 DANGER VITAL détecté{FIN}")


async def main() -> None:
    print(f"{GRIS}Parleur : {', '.join(TALKER_MODELS)} | Penseur : {', '.join(THINKER_MODELS)}")
    print(f"Commandes : /fiche  /reset  /quit{FIN}\n")
    historique = [{"role": "assistant", "content": GREETING_TEXT}]
    fiche: dict = {}
    print(f"{BLEU}Miki : {GREETING_TEXT}{FIN}")
    while True:
        try:
            texte = (await asyncio.to_thread(input, "\nVous : ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not texte:
            continue
        if texte == "/quit":
            break
        if texte == "/fiche":
            print(json.dumps(fiche, ensure_ascii=False, indent=2))
            continue
        if texte == "/reset":
            historique, fiche = [{"role": "assistant", "content": GREETING_TEXT}], {}
            print(f"{BLEU}Miki : {GREETING_TEXT}{FIN}")
            continue

        historique.append({"role": "user", "content": texte})
        # comme en vocal : le parleur répond avec la fiche du tour précédent,
        # pendant que le penseur analyse le nouveau message en parallèle
        (reponse, m_parleur, d_parleur), (nouvelle_fiche, m_penseur, d_penseur) = await asyncio.gather(
            parler(historique, fiche), penser(historique)
        )
        historique.append({"role": "assistant", "content": reponse})
        print(f"{BLEU}Miki : {reponse}{FIN}")
        print(f"{GRIS}  💬 {m_parleur} ({d_parleur:.1f}s){FIN}")
        afficher_penseur(nouvelle_fiche, m_penseur, d_penseur)
        fiche = nouvelle_fiche or fiche

    if fiche:
        print("\nFiche finale :")
        print(json.dumps(fiche, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
