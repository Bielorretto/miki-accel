"""Applique dans l'ordre les fichiers db/NNN_*.sql qui ne l'ont pas encore été.

Lancer :  python db/migrer.py        (même commande en local et sur Aiven : seul DATABASE_URL change)
"""

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv()
DOSSIER = Path(__file__).parent


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute("CREATE TABLE IF NOT EXISTS migrations "
                           "(fichier text PRIMARY KEY, appliquee_le timestamptz NOT NULL DEFAULT now())")
        faites = {r["fichier"] for r in await conn.fetch("SELECT fichier FROM migrations")}
        for f in sorted(DOSSIER.glob("[0-9][0-9][0-9]_*.sql")):
            if f.name in faites:
                continue
            async with conn.transaction():
                await conn.execute(f.read_text())
                await conn.execute("INSERT INTO migrations (fichier) VALUES ($1)", f.name)
            print(f"appliquée : {f.name}")
        print("base à jour")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
