"""Serveur web MIKI (FastAPI) : tableau de bord DIHAL, écoutants en temps réel, API.

Lancer :  venv/bin/python serveur.py        puis ouvrir http://localhost:8000

Pages :
  /             DIHAL : statistiques anonymes par département (lecture seule)
  /ecoutants    écoutants : fiches en file, triées par priorité, en temps réel
API :
  GET  /api/dihal/departements?source=tous|direct|simule
  GET  /api/dihal/stats?source=…&departement=93      (sans departement : tous départements)
  GET  /api/ecoutants/file/{dept}
  GET  /api/appels/en_cours     GET /api/appels/{appel_id}   (fiche + transcription)
  GET  /api/places/{dept}       POST /api/places      {"departement", "profil", "disponibles"}
  WS   /ws                      fiches et phrases des appels en direct (pub/sub Valkey)
  GET  /sante
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

import stats
from base import changer_places, connecter

STATIC = Path(__file__).parent / "static"
logger = logging.getLogger("miki.serveur")
SOURCES = ("tous", "direct", "simule")


class Diffuseur:
    """Relaie le pub/sub Valkey (fiches:*) vers tous les navigateurs connectés en WebSocket.
    Chaque instance du serveur s'abonne elle-même : ça marche avec plusieurs conteneurs."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def ecouter(self, vk) -> None:
        while True:   # si Valkey décroche, on se réabonne
            try:
                pubsub = vk.pubsub()
                await pubsub.psubscribe("fiches:*", "transcription:*")
                async for msg in pubsub.listen():
                    if msg["type"] != "pmessage":
                        continue
                    canal, cle = msg["channel"].split(":", 1)
                    if canal == "fiches":
                        evenement = {"type": "fiche", "departement": cle, **json.loads(msg["data"])}
                    else:
                        evenement = {"type": "transcription", "appel": cle, **json.loads(msg["data"])}
                    texte = json.dumps(evenement, ensure_ascii=False)
                    await asyncio.gather(*(self._envoyer(ws, texte) for ws in list(self.clients)))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("relais pub/sub interrompu, nouvelle tentative")
                await asyncio.sleep(1)

    async def _envoyer(self, ws: WebSocket, evenement: str) -> None:
        """Un navigateur fermé brutalement ne doit pas bloquer les autres : délai court, puis on l'oublie."""
        try:
            await asyncio.wait_for(ws.send_text(evenement), timeout=2)
        except Exception:
            self.clients.discard(ws)


diffuseur = Diffuseur()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool, app.state.vk = await connecter()
    tache = asyncio.create_task(diffuseur.ecouter(app.state.vk))
    yield
    tache.cancel()
    await app.state.pool.close()
    await app.state.vk.aclose()


app = FastAPI(title="MIKI", lifespan=lifespan)


def verifier_source(source: str) -> str:
    if source not in SOURCES:
        raise HTTPException(400, f"source doit valoir {', '.join(SOURCES)}")
    return source


@app.get("/")
async def page_dihal():
    return FileResponse(STATIC / "dihal.html")


@app.get("/ecoutants")
async def page_ecoutants():
    return FileResponse(STATIC / "ecoutants.html")


@app.get("/sante")
async def sante():
    async with app.state.pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    await app.state.vk.ping()
    return {"postgres": "ok", "valkey": "ok"}


@app.get("/api/dihal/departements")
async def api_departements(source: str = "tous"):
    async with app.state.pool.acquire() as conn:
        return await stats.departements(conn, verifier_source(source))


@app.get("/api/dihal/stats")
async def api_stats(source: str = "tous", departement: str | None = Query(None)):
    async with app.state.pool.acquire() as conn:
        return await stats.detail(conn, verifier_source(source), departement)


@app.get("/api/ecoutants/file/{dept}")
async def api_file(dept: str):
    """Fiches en file, dans l'ordre de la file Valkey, avec la dernière décision (explicabilité)."""
    ids = await app.state.vk.zrange(f"file:{dept}", 0, -1)
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT f.id, f.appel_id, f.profil, f.rang, f.securite, f.aucune_place, f.niveau_urgence, f.complet, f.cause,
                   f.localisation, f.creee_le, f.contenu, f.place_liberee_le, a.langue, a.langue_couverte, a.statut,
                   e.prenom AS ecoutant,
                   (SELECT explication FROM decisions_priorite d WHERE d.fiche_id = f.id
                    ORDER BY decidee_le DESC LIMIT 1) AS explication
            FROM fiches f JOIN appels a ON a.id = f.appel_id LEFT JOIN ecoutants e ON e.id = a.ecoutant_id
            WHERE f.id = ANY($1::uuid[])""", ids)
    par_id = {str(r["id"]): r for r in rows}
    file = []
    for i in ids:
        r = par_id.get(i)
        if r is None:
            continue
        c = r["contenu"] or {}
        file.append({
            "id": i, "appel_id": str(r["appel_id"]), "profil": r["profil"], "rang": r["rang"], "securite": r["securite"],
            "aucune_place": r["aucune_place"], "niveau_urgence": r["niveau_urgence"], "complet": r["complet"],
            "cause": r["cause"], "localisation": r["localisation"], "arrivee": r["creee_le"].isoformat(),
            "langue": r["langue"], "langue_nom": stats.LANGUES.get(r["langue"], r["langue"]),
            "langue_couverte": r["langue_couverte"], "en_cours": r["statut"] == "en_cours",
            "ecoutant": r["ecoutant"], "explication": r["explication"],
            "place_liberee": r["place_liberee_le"].isoformat() if r["place_liberee_le"] else None,
            "appelant": c.get("appelant") or {}, "recit": (c.get("situation") or {}).get("recit"),
            "cas_particuliers": c.get("cas_particuliers") or {},
        })
    return file


def lire_transcription(texte: str | None) -> list[dict]:
    """Transcription enregistrée (« ACCUEIL: … » / « APPELANT: … ») → liste de répliques."""
    lignes = []
    for ligne in (texte or "").splitlines():
        role, _, contenu = ligne.partition(": ")
        if role in ("ACCUEIL", "APPELANT") and contenu:
            lignes.append({"role": "miki" if role == "ACCUEIL" else "appelant", "texte": contenu})
        elif lignes:   # une réplique sur plusieurs lignes
            lignes[-1]["texte"] += " " + ligne
    return lignes


@app.get("/api/appels/en_cours")
async def api_appels_en_cours():
    """Appels décrochés et pas encore terminés, même si le département n'est pas encore connu."""
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT a.id, a.debut, a.departement, a.langue, f.contenu -> 'appelant' ->> 'prenom' AS prenom
            FROM appels a LEFT JOIN fiches f ON f.appel_id = a.id
            WHERE a.statut = 'en_cours' AND a.debut > now() - interval '2 hours'
            ORDER BY a.debut DESC""")
    return [{"appel_id": str(r["id"]), "debut": r["debut"].isoformat(), "departement": r["departement"],
             "langue": r["langue"], "langue_nom": stats.LANGUES.get(r["langue"], r["langue"]),
             "prenom": r["prenom"]} for r in rows]


@app.get("/api/appels/{appel_id}")
async def api_appel(appel_id: str):
    """Fiche complète et transcription : en direct depuis Valkey pendant l'appel, sinon depuis PostgreSQL."""
    try:
        uuid.UUID(appel_id)
    except ValueError:
        raise HTTPException(400, "identifiant d'appel invalide")
    async with app.state.pool.acquire() as conn:
        r = await conn.fetchrow("""
            SELECT a.id, a.debut, a.fin, a.statut, a.issue, a.departement, a.langue, a.langue_couverte,
                   a.source, e.prenom AS ecoutant, f.id AS fiche_id, f.profil, f.rang, f.securite, f.aucune_place,
                   f.niveau_urgence, f.cause, f.complet, f.delai_fiche_s, f.contenu, f.transcription,
                   (SELECT explication FROM decisions_priorite d WHERE d.fiche_id = f.id
                    ORDER BY decidee_le DESC LIMIT 1) AS explication
            FROM appels a LEFT JOIN fiches f ON f.appel_id = a.id LEFT JOIN ecoutants e ON e.id = a.ecoutant_id
            WHERE a.id = $1""", uuid.UUID(appel_id))
    if r is None:
        raise HTTPException(404, "appel inconnu")
    direct = [json.loads(x) for x in await app.state.vk.lrange(f"appel:{appel_id}:transcription", 0, -1)]
    contenu = r["contenu"] or {}
    return {
        "appel_id": appel_id, "debut": r["debut"].isoformat(), "fin": r["fin"].isoformat() if r["fin"] else None,
        "en_cours": r["statut"] == "en_cours", "issue": r["issue"], "departement": r["departement"],
        "langue": r["langue"], "langue_nom": stats.LANGUES.get(r["langue"], r["langue"]),
        "langue_couverte": r["langue_couverte"], "source": r["source"], "ecoutant": r["ecoutant"],
        "profil": r["profil"], "rang": r["rang"], "securite": r["securite"], "aucune_place": r["aucune_place"],
        "niveau_urgence": r["niveau_urgence"], "cause": r["cause"], "complet": r["complet"],
        "delai_fiche_s": r["delai_fiche_s"], "explication": r["explication"],
        "appelant": contenu.get("appelant") or {}, "situation": contenu.get("situation") or {},
        "localisation": contenu.get("localisation"), "cas_particuliers": contenu.get("cas_particuliers") or {},
        "infos_manquantes": contenu.get("infos_manquantes") or [],
        "transcription": direct or lire_transcription(r["transcription"]),
    }


@app.get("/api/places/{dept}")
async def api_places(dept: str):
    async with app.state.pool.acquire() as conn:
        regle = await conn.fetchrow("SELECT version, ordre_profils, criteres_securite FROM regles_departement "
                                    "WHERE departement = $1 AND active", dept)
        places = await conn.fetch("SELECT p.profil, pr.libelle, p.disponibles, p.total FROM places p "
                                  "JOIN profils pr ON pr.code = p.profil WHERE departement = $1", dept)
        langues = await conn.fetchval("SELECT array_agg(DISTINCT l ORDER BY l) FROM ecoutants, unnest(langues) l "
                                      "WHERE departement = $1 AND actif", dept) or []
    if regle is None:
        raise HTTPException(404, "département non couvert")
    ordre = list(regle["ordre_profils"])
    return {"version": regle["version"], "ordre": ordre,
            "langues_ecoutants": [{"code": l, "nom": stats.LANGUES.get(l, l)} for l in langues], "criteres_securite": list(regle["criteres_securite"]),
            "places": sorted((dict(p) for p in places),
                             key=lambda p: ordre.index(p["profil"]) if p["profil"] in ordre else len(ordre))}


class Places(BaseModel):
    departement: str
    profil: str
    disponibles: int


@app.post("/api/places")
async def api_changer_places(p: Places):
    async with app.state.pool.acquire() as conn:
        total = await conn.fetchval("SELECT total FROM places WHERE departement = $1 AND profil = $2",
                                    p.departement, p.profil)
        if total is None:
            raise HTTPException(404, "département ou profil inconnu")
        if not 0 <= p.disponibles <= total:
            raise HTTPException(400, f"disponibles doit être entre 0 et {total}")
        async with conn.transaction():
            await changer_places(conn, app.state.vk, p.departement, p.profil, p.disponibles)
    return {"ok": True}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    diffuseur.clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()   # garde la connexion ouverte
    except WebSocketDisconnect:
        diffuseur.clients.discard(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
