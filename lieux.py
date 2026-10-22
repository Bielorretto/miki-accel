"""Lieu de l'appelant → commune → département, pour toute la France.

Source : l'API Géo du gouvernement (geo.api.gouv.fr), gratuite et sans clé. Seul le nom de la
ville ou le code postal y est envoyé, jamais l'identité de l'appelant. Les communes trouvées
sont gardées dans la table communes, qui sert aussi de repli si l'API ne répond pas.

Homonymes : quand une commune est beaucoup plus peuplée que les autres du même nom (Montreuil
en Seine-Saint-Denis face aux Montreuil de 800 habitants), c'est elle ; quand deux sont
comparables (Saint-Denis 93 et Saint-Denis 974), on ne choisit pas : MIKI demande.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger("miki.lieux")
API = "https://geo.api.gouv.fr/communes"
CHAMPS = "nom,code,codesPostaux,codeDepartement,population"
DOMINANCE = 10   # une commune l'emporte si elle est au moins 10 fois plus peuplée que la suivante


def normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sans_accents.lower()).strip()


@dataclass
class Lieu:
    departement: str | None = None
    commune: str | None = None
    code_insee: str | None = None
    code_postal: str | None = None
    # communes possibles quand on ne peut pas trancher : [(nom, code du département)]
    homonymes: list[tuple[str, str]] = field(default_factory=list)
    recherche: str | None = None   # ce qui a été cherché, pour dire « je ne trouve pas … »


def _choisir(candidats: list[dict], recherche: str) -> Lieu:
    if not candidats:
        return Lieu(recherche=recherche)
    q = normaliser(recherche)
    pop = lambda c: c.get("population") or 0
    exacts = sorted((c for c in candidats if normaliser(c["nom"]) == q), key=pop, reverse=True)
    prefixes = [c for c in candidats if normaliser(c["nom"]).startswith(q) and c not in exacts]
    # le nom exact prime ; un nom plus long (« Saint-Ouen-sur-Seine » pour « Saint Ouen ») n'entre en
    # concurrence que s'il est plus peuplé, et sans nom exact « Brive » mène à Brive-la-Gaillarde
    if exacts:
        proches = exacts + [c for c in prefixes if pop(c) > pop(exacts[0])]
    else:
        proches = prefixes or list(candidats)
    proches.sort(key=pop, reverse=True)
    premier = proches[0]
    second = proches[1] if len(proches) > 1 else None
    if second and second["codeDepartement"] != premier["codeDepartement"] and \
            (premier.get("population") or 0) < DOMINANCE * (second.get("population") or 0):
        return Lieu(recherche=recherche,
                    homonymes=[(c["nom"], c["codeDepartement"]) for c in proches[:4]])
    return Lieu(departement=premier["codeDepartement"], commune=premier["nom"], code_insee=premier["code"],
                code_postal=(premier.get("codesPostaux") or [None])[0], recherche=recherche)


async def localiser(conn, commune: str | None, localisation: str | None) -> Lieu:
    """Code postal s'il y en a un, sinon le nom de la commune ; repli sur la table locale."""
    texte = " ".join(filter(None, [commune, localisation]))
    if not texte.strip():
        return Lieu()
    params = None
    if cp := re.search(r"\b\d{5}\b", texte):
        params = {"codePostal": cp.group(), "fields": CHAMPS}
        recherche = cp.group()
    elif commune:
        params = {"nom": commune, "fields": CHAMPS, "boost": "population", "limit": "8"}
        recherche = commune
    else:
        recherche = localisation
    lieu = None
    if params:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as http:
                async with http.get(API, params=params) as r:
                    r.raise_for_status()
                    candidats = await r.json()
            if "codePostal" in params:
                # un code postal peut couvrir plusieurs communes, mais un seul département
                lieu = _choisir(sorted(candidats, key=lambda c: -(c.get("population") or 0))[:1], recherche)
                lieu.code_postal = recherche
            else:
                lieu = _choisir(candidats, recherche)
        except Exception as e:
            logger.warning("API Géo indisponible (%s) : repli sur la table locale", e)
    if lieu is None:
        lieu = await _localiser_en_base(conn, recherche)
    if lieu.departement and lieu.code_insee and lieu.code_postal:
        await conn.execute(
            "INSERT INTO communes (code_insee, code_postal, nom, departement) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT DO NOTHING", lieu.code_insee, lieu.code_postal, lieu.commune, lieu.departement)
    return lieu


async def _localiser_en_base(conn, recherche: str) -> Lieu:
    q = normaliser(recherche)
    lignes = await conn.fetch("SELECT DISTINCT nom, departement, code_insee, code_postal FROM communes "
                              "WHERE code_postal = $1", recherche) if recherche.isdigit() else \
        [r for r in await conn.fetch("SELECT DISTINCT nom, departement, code_insee, code_postal FROM communes")
         if normaliser(r["nom"]) == q]
    depts = {r["departement"] for r in lignes}
    if len(depts) == 1:
        r = lignes[0]
        return Lieu(r["departement"], r["nom"], r["code_insee"], r["code_postal"], recherche=recherche)
    if len(depts) > 1:
        return Lieu(recherche=recherche, homonymes=[(r["nom"], r["departement"]) for r in lignes][:4])
    return Lieu(recherche=recherche)
