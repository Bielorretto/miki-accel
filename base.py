"""Couche de données MIKI : PostgreSQL (ce qui dure) et Valkey (l'état en direct).

Tout se configure par variables d'environnement, pour passer du local à Aiven sans toucher au code :
  DATABASE_URL       postgresql://… (Aiven : ajouter ?sslmode=require)
  VALKEY_URL         redis://… en local, rediss://… sur Aiven
  MIKI_HMAC_SECRET   secret du hachage des numéros de téléphone

Clés Valkey :
  appel:{id}              hash, session de l'appel en cours (expire après 2 h)
  file:{dept}             sorted set des fiches en file, score = priorite.score_file
  dispo:{dept}:{langue}   set des écoutants libres parlant cette langue
  fiches:{dept}           canal pub/sub, une notification à chaque changement de fiche
"""

import hashlib
import hmac
import json
import os

import asyncpg
import valkey.asyncio as valkey
from dotenv import load_dotenv

from priorite import decider, score_file

load_dotenv()

CLE_VERSION = 1


async def connecter() -> tuple[asyncpg.Pool, valkey.Valkey]:
    # Aiven (plan Developer) limite à 15 connexions : chaque appel et le serveur web ont leur pool,
    # on reste petit pour tenir plusieurs appels simultanés
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1,
                                     max_size=int(os.environ.get("DB_POOL_MAX", "3")),
                                     init=_codecs)
    vk = valkey.from_url(os.environ["VALKEY_URL"], decode_responses=True)
    return pool, vk


async def _codecs(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def hash_tel(numero: str) -> bytes:
    """HMAC-SHA256 du numéro normalisé (chiffres et '+' seulement). Le numéro n'est jamais stocké."""
    normalise = "".join(c for c in numero if c.isdigit() or c == "+")
    return hmac.new(os.environ["MIKI_HMAC_SECRET"].encode(), normalise.encode(), hashlib.sha256).digest()


def tranche_age(age: int | None) -> str:
    if age is None:
        return "inconnu"
    for borne, tranche in ((18, "moins_18"), (25, "18_24"), (35, "25_34"), (50, "35_49"), (65, "50_64")):
        if age < borne:
            return tranche
    return "65_plus"


async def classer_fiche(conn: asyncpg.Connection, vk: valkey.Valkey, fiche_id) -> None:
    """Recalcule le rang d'une fiche, journalise la décision si elle change, met à jour la file."""
    f = await conn.fetchrow(
        "SELECT id, departement, contenu, profil, rang, securite, aucune_place, en_file, creee_le "
        "FROM fiches WHERE id = $1", fiche_id)
    if f is None or f["departement"] is None:
        return
    dept = f["departement"]
    regle = await conn.fetchrow(
        "SELECT version, ordre_profils, criteres_securite FROM regles_departement "
        "WHERE departement = $1 AND active", dept)
    if regle is None:   # département non couvert : pas de file
        return
    places = dict(await conn.fetch("SELECT profil, disponibles FROM places WHERE departement = $1", dept))
    d = decider(f["contenu"], dept, dict(regle), places)

    change = (d.profil, d.rang, d.securite, d.aucune_place) != (
        f["profil"], f["rang"], f["securite"], f["aucune_place"])
    # dossier en attente faute de place, et une place vient de se libérer pour son profil
    place_liberee = bool(f["aucune_place"]) and not d.aucune_place and f["en_file"]
    if place_liberee:
        await conn.execute("UPDATE fiches SET place_liberee_le = now() WHERE id = $1", fiche_id)
    if change or not await conn.fetchval("SELECT 1 FROM decisions_priorite WHERE fiche_id = $1", fiche_id):
        await conn.execute(
            "UPDATE fiches SET profil = $2, rang = $3, securite = $4, aucune_place = $5, maj = now() "
            "WHERE id = $1", fiche_id, d.profil, d.rang, d.securite, d.aucune_place)
        await conn.execute(
            "INSERT INTO decisions_priorite (fiche_id, departement, regle_version, profil, ordre_effectif, "
            "profils_sautes, rang, securite, aucune_place, explication) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            fiche_id, dept, regle["version"], d.profil, d.ordre_effectif, d.profils_sautes,
            d.rang, d.securite, d.aucune_place, d.explication)

    if f["en_file"]:
        arrivee_ms = int(f["creee_le"].timestamp() * 1000)
        await vk.zadd(f"file:{dept}", {str(fiche_id): score_file(d.rang, arrivee_ms)})
    else:
        await vk.zrem(f"file:{dept}", str(fiche_id))
    evenement = {"fiche": str(fiche_id), "rang": d.rang}
    if place_liberee:
        evenement |= {"alerte": "place_liberee", "profil": d.profil}
    await vk.publish(f"fiches:{dept}", json.dumps(evenement))


async def reclasser_departement(conn: asyncpg.Connection, vk: valkey.Valkey, dept: str) -> None:
    """À appeler quand les places ou les règles d'un département changent : toute la file est recalculée."""
    await vk.delete(f"file:{dept}")
    for (fiche_id,) in await conn.fetch("SELECT id FROM fiches WHERE departement = $1 AND en_file", dept):
        await classer_fiche(conn, vk, fiche_id)


async def changer_places(conn: asyncpg.Connection, vk: valkey.Valkey, dept: str, profil: str,
                         disponibles: int) -> None:
    await conn.execute("UPDATE places SET disponibles = $3, maj = now() "
                       "WHERE departement = $1 AND profil = $2", dept, profil, disponibles)
    await reclasser_departement(conn, vk, dept)


async def publier_disponibilites(conn: asyncpg.Connection, vk: valkey.Valkey) -> None:
    """Reconstruit les sets dispo:{dept}:{langue} à partir des écoutants actifs."""
    async for cle in vk.scan_iter("dispo:*"):
        await vk.delete(cle)
    for e in await conn.fetch("SELECT id, departement, langues FROM ecoutants WHERE actif"):
        for langue in e["langues"]:
            await vk.sadd(f"dispo:{e['departement']}:{langue}", e["id"])
