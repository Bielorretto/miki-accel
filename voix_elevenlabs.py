"""Teste d'un coup les voix ElevenLabs, en anglais par défaut, et choisit celle de Miki.

Liste les voix du compte ; si la clé n'a pas le droit de les lister, utilise les voix
« premade » d'ElevenLabs (utilisables avec le forfait gratuit).
Chaque extrait consomme des crédits (environ un par caractère) : un extrait déjà joué est
gardé en mémoire et rejoué sans être refacturé.

Lancer :  python voix_elevenlabs.py
"""

import os
import subprocess
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)

API = "https://api.elevenlabs.io/v1"
KEY = os.environ["ELEVEN_API_KEY"]
MODEL = os.environ.get("ELEVEN_MODEL", "eleven_flash_v2_5")
TEXTE_DEFAUT = "Hello, you have reached the one hundred fifteen. My name is Miki. I'm listening."

# voix « premade » d'ElevenLabs : (nom, genre, accent / style, id)
PREMADE = [
    ("Sarah", "F", "américaine, douce", "EXAVITQu4vr4xnSDxMaL"),
    ("Laura", "F", "américaine, enjouée", "FGY2WhTYpPnrIDTdsKH5"),
    ("Jessica", "F", "américaine, expressive", "cgSgspJ2msm6clMCkdW9"),
    ("Aria", "F", "américaine, posée", "9BWtsMINqrJLrRacOk9x"),
    ("Matilda", "F", "américaine, chaleureuse", "XrExE9yKIg1WjnnlVkGX"),
    ("Alice", "F", "britannique, claire", "Xb7hH8MSUJpSbSDYk0k2"),
    ("Lily", "F", "britannique, calme", "pFZP5JQG7iQjIQuC4Bku"),
    ("River", "?", "américaine, neutre", "SAz9YHcvj6GT2YYXdXww"),
    ("Roger", "H", "américaine, assurée", "CwhRBWXzGAHq8TQ4Fs17"),
    ("Eric", "H", "américaine, aimable", "cjVigY5qzO86Huf0OWal"),
    ("Chris", "H", "américaine, décontractée", "iP95p4xoKVk53GoZ742B"),
    ("Brian", "H", "américaine, grave", "nPczCjzI2devNBz1zQrb"),
    ("Will", "H", "américaine, amicale", "bIHbv24MWmeRgasZH58o"),
    ("Liam", "H", "américaine, jeune", "TX3LPaxmHKxFdv7VOQHJ"),
    ("Bill", "H", "américaine, âgée", "pqHfZKP75CvOlQylNhV4"),
    ("George", "H", "britannique, chaleureuse", "JBFqnCBsd6RMkjVDRZzb"),
    ("Daniel", "H", "britannique, posée", "onwK4e9ZLuTAKqWW03F9"),
    ("Callum", "H", "transatlantique, rauque", "N2lVS1w4EtoT3dr4eOWO"),
    ("Charlie", "H", "australienne, naturelle", "IKne3meq5aSn9XLyUdCD"),
]


def lister_voix():
    try:
        r = requests.get(f"{API}/voices", headers={"xi-api-key": KEY}, timeout=15)
        r.raise_for_status()
        voix = [
            (v["name"], {"male": "H", "female": "F"}.get((v.get("labels") or {}).get("gender"), "?"),
             ", ".join(filter(None, [(v.get("labels") or {}).get("accent"), v.get("category")])),
             v["voice_id"])
            for v in r.json()["voices"]
        ]
        return sorted(voix, key=lambda v: (v[1], v[0])) or PREMADE
    except Exception as e:
        print(f"Liste des voix indisponible ({str(e)[:80]}) : voix premade utilisées.")
        return PREMADE


cache: dict[tuple[str, str], bytes] = {}


def jouer(voice_id, texte):
    if (voice_id, texte) not in cache:
        r = requests.post(
            f"{API}/text-to-speech/{voice_id}",
            headers={"xi-api-key": KEY},
            json={"text": texte, "model_id": MODEL},
            timeout=60,
        )
        if r.status_code != 200:
            print(f"    Erreur {r.status_code} : {r.text[:200]}")
            return
        cache[(voice_id, texte)] = r.content
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(cache[(voice_id, texte)])
    subprocess.run(["afplay", f.name])
    os.unlink(f.name)


def choisir_dans_env(voice_id):
    lignes = ENV_FILE.read_text().splitlines()
    lignes = [f"ELEVEN_VOICE_ID={voice_id}" if l.startswith("ELEVEN_VOICE_ID=") else l for l in lignes]
    if not any(l.startswith("ELEVEN_VOICE_ID=") for l in lignes):
        lignes.append(f"ELEVEN_VOICE_ID={voice_id}")
    ENV_FILE.write_text("\n".join(lignes) + "\n")
    print(f"  ELEVEN_VOICE_ID={voice_id} enregistré dans .env")


def main():
    voix = lister_voix()
    actuelle = os.environ.get("ELEVEN_VOICE_ID")
    texte = TEXTE_DEFAUT
    while True:
        print(f"\n{len(voix)} voix — texte : « {texte} »")
        for i, (nom, genre, style, vid) in enumerate(voix, 1):
            print(f"{i:>3}. {'*' if vid == actuelle else ' '} {nom:<10} {genre}  {style}")
        print("\n  a tester toutes | <numéro> tester une | f / h toutes les femmes / hommes"
              " | t changer le texte | c<numéro> choisir pour Miki | q quitter")
        cmd = input("> ").strip().lower()
        if cmd == "q":
            break
        if cmd == "t":
            texte = input("Texte : ").strip() or TEXTE_DEFAUT
        elif cmd in ("a", "f", "h"):
            for i, (nom, genre, style, vid) in enumerate(voix, 1):
                if cmd == "a" or genre == cmd.upper():
                    print(f"  ▶ {i}. {nom} ({style})")
                    jouer(vid, texte)
        elif cmd.startswith("c") and cmd[1:].isdigit() and 1 <= int(cmd[1:]) <= len(voix):
            actuelle = voix[int(cmd[1:]) - 1][3]
            choisir_dans_env(actuelle)
        elif cmd.isdigit() and 1 <= int(cmd) <= len(voix):
            nom, _, style, vid = voix[int(cmd) - 1]
            print(f"  ▶ {nom} ({vid})")
            jouer(vid, texte)


if __name__ == "__main__":
    main()
