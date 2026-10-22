"""Liste et teste les voix Gradium (catalogue + voix perso), en français par défaut.

Lancer :  python voix_gradium.py
"""

import os
import subprocess
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

API = "https://api.gradium.ai/api"
KEY = os.environ["GRADIUM_API_KEY"]
TEXTE_DEFAUT = "Bonjour, vous êtes bien au 115. Je vous écoute, dites-moi ce qui se passe."


def lister_voix(langue):
    r = requests.get(f"{API}/voices/", headers={"x-api-key": KEY},
                     params={"include_catalog": "true", "limit": 1000}, timeout=15)
    r.raise_for_status()
    voix = [v for v in r.json() if not langue or v.get("language") == langue]
    # voix perso d'abord, puis catalogue ; femmes / hommes regroupés
    return sorted(voix, key=lambda v: (v.get("is_catalog", False), v.get("gender") or "~", v["name"]))


def decrire(v):
    genre = {"Male": "H", "Female": "F"}.get(v.get("gender"), "?")
    source = "catalogue" if v.get("is_catalog") else "perso"
    return f"{v['name']:<20} {genre} [{source}] {(v.get('description') or '')[:80]}"


def jouer(voice_id, texte):
    r = requests.post(
        f"{API}/post/speech/tts",
        headers={"x-api-key": KEY},
        json={"text": texte, "voice_id": voice_id, "output_format": "wav", "only_audio": True},
        timeout=60,
    )
    if r.status_code != 200:
        print(f"  Erreur {r.status_code} : {r.text[:200]}")
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(r.content)
    subprocess.run(["afplay", f.name])
    os.unlink(f.name)


def choisir_dans_env(voice_id):
    lignes = ENV_FILE.read_text().splitlines()
    lignes = [f"GRADIUM_VOICE_ID={voice_id}" if l.startswith("GRADIUM_VOICE_ID=") else l for l in lignes]
    if not any(l.startswith("GRADIUM_VOICE_ID=") for l in lignes):
        lignes.append(f"GRADIUM_VOICE_ID={voice_id}")
    ENV_FILE.write_text("\n".join(lignes) + "\n")
    print(f"  GRADIUM_VOICE_ID={voice_id} enregistré dans .env")


def main():
    langue = "fr"
    voix = lister_voix(langue)
    actuelle = os.environ.get("GRADIUM_VOICE_ID")
    texte = TEXTE_DEFAUT
    while True:
        print(f"\nLangue : {langue or 'toutes'} — {len(voix)} voix")
        for i, v in enumerate(voix, 1):
            print(f"{i:>3}. {'*' if v['uid'] == actuelle else ' '} {decrire(v)}")
        print("\n  <numéro> tester | a tester toutes | t changer le texte | l<code> langue (l = toutes)"
              " | c<numéro> choisir pour l'agent | q quitter")
        cmd = input("> ").strip().lower()
        if cmd == "q":
            break
        if cmd == "t":
            texte = input("Texte : ").strip() or TEXTE_DEFAUT
        elif cmd.startswith("l"):
            langue = cmd[1:]
            voix = lister_voix(langue)
        elif cmd == "a":
            for v in voix:
                print(f"  ▶ {v['name']}")
                jouer(v["uid"], texte)
        elif cmd.startswith("c") and cmd[1:].isdigit() and 1 <= int(cmd[1:]) <= len(voix):
            actuelle = voix[int(cmd[1:]) - 1]["uid"]
            choisir_dans_env(actuelle)
        elif cmd.isdigit() and 1 <= int(cmd) <= len(voix):
            v = voix[int(cmd) - 1]
            print(f"  ▶ {v['name']} ({v['uid']})")
            jouer(v["uid"], texte)


if __name__ == "__main__":
    main()

