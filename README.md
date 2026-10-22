# MIKI — agent vocal pour le 115

MIKI décroche les appels du 115 (hébergement d'urgence, France), recueille la situation de
l'appelant à la voix, remplit sa fiche en direct et la classe selon les règles de priorité du
115 de son département. Les écoutants suivent les fiches en temps réel ; la DIHAL consulte des
statistiques anonymes.

> Projet de hackathon. **Toutes les données sont synthétiques** : aucune vraie personne.

## Ce que fait MIKI pendant un appel

| Fonction | Comment |
|---|---|
| **Recueillir la situation** | prénom, nom, âge, sexe, récit, cause de la mise à la rue, temps à la rue, lieu actuel, cas particuliers. Chaque réponse courte est confirmée à voix haute. |
| **Reconnaître un appelant** | le numéro n'est jamais stocké : seulement son HMAC. Si le numéro est connu, MIKI demande « Est-ce que je parle bien à … ? » et ne révèle rien avant la confirmation du prénom et de l'âge. Jamais de fusion automatique de deux fiches. |
| **Trouver le bon 115** | lieu actuel → commune → département, pour toute la France (API Géo du gouvernement). Homonymes : la commune nettement plus peuplée l'emporte, sinon MIKI demande. Confirmation à voix haute ; département non couvert → orientation. |
| **Parler la langue de l'appelant** | 12 langues reconnues en plus du français. MIKI bascule dans la langue de l'appelant (si sa voix la parle) et un écoutant du département qui la parle est attribué ; sinon la fiche est marquée « langue non couverte ». |
| **Places en temps réel** | MIKI dit s'il reste des places pour le profil de l'appelant, sans jamais en promettre. Sans place, le dossier reste en attente et les écoutants sont alertés dès qu'une place se libère. |
| **Priorité explicable** | règles de sécurité d'abord (danger vital, violences, mineur seul : orientation 15/112, 17, 119), puis l'ordre des profils du département en sautant ceux sans place. Chaque décision est journalisée avec la règle appliquée. |
| **Statistiques anonymes** | en fin d'appel, une ligne sans aucun lien avec l'identité (jour, département, tranche d'âge, sexe, cause, profil, langue, issue, délai). Les effectifs de moins de 5 sont masqués. |

Le modèle de langage extrait les faits ; le classement est calculé en Python, de façon
déterministe et rejouable (`priorite.py`).

## Architecture

```
Téléphone ──► Twilio (SIP trunk) ──► LiveKit Cloud (SIP) ──► agent.py (LiveKit Agents)
                                                               │  transcription : ElevenLabs Scribe
                                                               │  voix          : ElevenLabs Flash
                                                               │  parleur       : Claude Haiku 5.5
                                                               │  penseur       : Claude Haiku 5.5 (fiche JSON)
                                                               ▼
                                    PostgreSQL (Aiven) ◄── suivi.py ──► Valkey (Aiven)
                                    fiches, appelants,                  sessions, files de priorité,
                                    règles, places, stats               écoutants libres, pub/sub
                                                               ▲
                                   serveur.py (FastAPI) ───────┘
                                   /            dashboard DIHAL (statistiques)
                                   /ecoutants   fiches en temps réel (WebSocket)
```

Tout l'état vit dans PostgreSQL et Valkey : l'agent et le serveur sont sans état, prêts pour
un conteneur (Aiven Runtime).

## Fichiers

| Fichier | Rôle |
|---|---|
| `agent.py` | l'agent vocal : transcription, parleur, penseur, voix, réduction du bruit |
| `prompts.py` | prompts du parleur (ce que dit MIKI) et du penseur (fiche JSON) |
| `suivi.py` | écritures en base pendant l'appel et notes de la base pour MIKI |
| `lieux.py` | lieu → commune → département |
| `priorite.py` | niveau d'urgence et classement par département (fonctions pures) |
| `base.py` | connexions PostgreSQL / Valkey, HMAC, classement, files, places |
| `serveur.py`, `stats.py`, `static/` | serveur web, requêtes statistiques, dashboards |
| `db/` | migrations SQL (`migrer.py`) et données synthétiques (`remplir.py`) |
| `chat_texte.py` | conversation en texte avec MIKI, sans voix |
| `maquettes/` | maquette du dashboard DIHAL |

## Installation

Python 3.13.

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### Variables d'environnement (`.env`, jamais commité)

**Base de données**
```
DATABASE_URL=postgres://…?sslmode=require     # Aiven, ou postgresql://…@localhost:5442/miki en local
VALKEY_URL=rediss://…                          # Aiven, ou redis://localhost:6389/0 en local
MIKI_HMAC_SECRET=…                             # secret du hachage des numéros (ne jamais le changer sans migration)
DB_POOL_MAX=3                                  # connexions par processus (Aiven Developer : 15 au total)
```

**LiveKit et téléphonie**
```
LIVEKIT_URL=wss://….livekit.cloud
LIVEKIT_API_KEY=…
LIVEKIT_API_SECRET=…
```

**Modèles**
```
ANTHROPIC_API_KEY=…
TALKER_MODEL=claude:claude-haiku-5-5,openai/gpt-oss-120b     # voix de MIKI, avec repli Groq
THINKER_MODELS=claude:claude-haiku-5-5,groq:openai/gpt-oss-120b
GROQ_API_KEY=…                                                # repli
ELEVEN_API_KEY=…                                              # voix et transcription
ELEVEN_VOICE_ID=…
ELEVEN_MODEL=eleven_flash_v2_5
STT_PROVIDER=elevenlabs        # ou deepgram (DEEPGRAM_API_KEY)
STT_LANGUAGE=fr
TTS_LANGUAGE=fr
```

**Réglages de la conversation** (valeurs par défaut)
```
MIKI_VOLUME=2.0                # gain de la voix au téléphone, crêtes adoucies
FIN_DE_TOUR_MIN_S=0.2          # attente après la phrase de l'appelant
FIN_DE_TOUR_MAX_S=1.0
TURN_DETECTOR=v1-mini          # local ; "v1" (LiveKit Cloud) si l'agent tourne dans le cloud
NOISE_CANCELLATION=bvc         # suppression du bruit et des voix de fond ; "off" pour couper
VAD_SEUIL=0.6                  # sensibilité de la détection de parole
STT_VAD_SEUIL=0.55
```

### Base de données

En local avec Docker (ports 5442 et 6389) :
```bash
docker compose up -d
```
Ou sur Aiven : créer un service PostgreSQL et un service Valkey, puis mettre leurs URI dans `.env`.

Puis, dans les deux cas :
```bash
venv/bin/python db/migrer.py          # schéma (rejouable, n'applique que les nouvelles migrations)
venv/bin/python db/remplir.py         # données synthétiques ; --reset pour repartir de zéro (local seulement)
```

## Lancer

```bash
venv/bin/python agent.py dev          # MIKI répond au téléphone (via LiveKit)
venv/bin/python agent.py console      # parler à MIKI avec le micro du Mac, sans téléphone
venv/bin/python serveur.py            # dashboards : http://localhost:8000 et /ecoutants
```

## Déploiement sur Aiven Runtime

Le dépôt contient tout ce qu'il faut : `compose.aiven.yaml` (le manifeste), `Dockerfile` (serveur
web, port 8080) et `Dockerfile.agent` (MIKI, sans port : il se connecte lui-même à LiveKit).

1. Console Aiven → projet → **Runtime** → **Deploy application** → dépôt `miki-accel`, branche
   `main` → manifeste `compose.aiven.yaml` → **Scan**. Deux applications sont détectées : `web` et
   `agent`.
2. Pour **chaque** application, **Connected services** → connecter le service PostgreSQL existant
   (variable `DATABASE_URL`) et le service Valkey existant (variable `VALKEY_URL`).
3. Ajouter les **secrets** :
   - `web` : `MIKI_HMAC_SECRET`
   - `agent` : `MIKI_HMAC_SECRET`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`,
     `ANTHROPIC_API_KEY`, `ELEVEN_API_KEY`, `GROQ_API_KEY`
   Le même `MIKI_HMAC_SECRET` partout, celui de la base actuelle : sinon les appelants déjà
   connus ne sont plus reconnus.
4. **Deploy**. L'adresse publique du tableau de bord apparaît dans **Connection information** de
   l'application `web`.

Un push sur GitHub ne redéploie pas : **Actions** → **Deploy latest commit**. Une fois l'agent en
ligne, arrêter celui qui tourne en local, sinon les appels se répartissent entre les deux.

## Téléphonie (Twilio → LiveKit)

1. LiveKit : un trunk SIP entrant limité au numéro Twilio, et une règle de dispatch qui ouvre
   une room par appel (sans le numéro de l'appelant dans le nom de la room).
   ```bash
   lk sip inbound create --name miki-twilio --numbers +1XXXXXXXXXX
   lk sip dispatch create --name miki-appels --trunks <ID du trunk> --callee appel- --randomize
   ```
2. Twilio : un Elastic SIP Trunk dont l'origination pointe vers l'URI SIP du projet LiveKit
   (`sip:<projet>.sip.livekit.cloud;transport=tcp`), et le numéro rattaché à ce trunk.

L'agent se connecte à LiveKit en sortie : il n'a besoin d'aucun port ouvert.

## Données et confidentialité

- Données **100 % synthétiques**.
- Numéros de téléphone : HMAC-SHA256 avec un secret en variable d'environnement, jamais en clair.
- `stats_calls` ne contient aucun nom, aucun numéro, aucune clé vers un appel ou un appelant,
  pas d'heure ; identifiants aléatoires. Effectifs de moins de 5 masqués sur le dashboard DIHAL.
- Les phrases statistiques disent « parmi les appelants de MIKI », jamais « en France ».
- MIKI annonce à l'appelant que des informations anonymes servent aux statistiques.
- Seul le nom de la ville ou le code postal est envoyé à l'API Géo.
- Les résultats `simule` (données de démonstration) et `direct` (vrais appels) sont distingués.

## Dépannage

**La voix de MIKI est hachée au téléphone mais propre en console.** C'est le réseau de la machine
qui fait tourner l'agent : la voix repart en temps réel vers LiveKit et un débit montant faible ou
irrégulier (partage de connexion, Wi-Fi saturé, iCloud qui synchronise) fait perdre des paquets.
Solution durable : faire tourner l'agent dans le cloud.

**MIKI attend longtemps ou répond à côté dans un lieu bruyant.** Vérifier que
`NOISE_CANCELLATION=bvc` est actif ; ajuster `VAD_SEUIL` et `STT_VAD_SEUIL`.

**La base ne répond pas.** L'appel continue sans enregistrement ; voir les logs `miki.suivi`.
