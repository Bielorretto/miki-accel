"""Tableau de bord des écoutants : fiches des appels en cours, en temps réel, et dossiers créés.

L'agent écrit la fiche de chaque appel dans sessions/en_cours/ à chaque analyse du penseur ;
en fin d'appel elle devient un dossier dans dossiers/. Cette page relit ces fichiers chaque
seconde. Le bouton « Créer le dossier » fige la fiche d'un appel encore en cours.

À droite, la carte 3D suit la fiche sélectionnée (static/carte/) : lieu géocodé par l'IGN,
hébergements fictifs proches, trajet à pied IGN vers le centre que l'écoutant choisit.
MIKI_DEMO=1 ajoute trois scénarios sans appel réel.

Lancer :  python dashboard.py      puis ouvrir http://localhost:8115
          MIKI_DEMO=1 python dashboard.py      (avec les scénarios de démonstration)
"""

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from stats import LANGUES

ROOT = Path(__file__).parent
LIVE_DIR = ROOT / "sessions" / "en_cours"
DOSSIERS_DIR = ROOT / "dossiers"
CARTE_DIR = ROOT / "static" / "carte"
TYPES = {".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8"}
DEMO = os.environ.get("MIKI_DEMO", "").lower() in ("1", "true", "oui")
# vue DIHAL (serveur.py) ; vide : même machine, port 8000
DIHAL_URL = os.environ.get("DIHAL_URL", "")
PORT = int(os.environ.get("DASHBOARD_PORT", "8115"))
ID_RE = re.compile(r"^[\w-]+$")


def lire(dossier: Path) -> list[dict]:
    fiches = []
    for f in dossier.glob("*.json"):
        try:
            fiches.append(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError):
            pass  # fichier en cours d'écriture : relu à la prochaine seconde
    # les plus urgents d'abord, puis les plus récents
    return sorted(
        fiches,
        key=lambda d: ((d.get("priorite") or {}).get("score", 0), d.get("debut", "")),
        reverse=True,
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path == "/":
            page = (PAGE.replace("__MIKI_DEMO__", "true" if DEMO else "false")
                    .replace("__LANGUES__", json.dumps(LANGUES, ensure_ascii=False))
                    .replace("__DIHAL_URL__", json.dumps(DIHAL_URL)))
            self._send(200, page.encode(), "text/html; charset=utf-8")
        elif (m := re.fullmatch(r"/carte/([\w-]+(\.mjs|\.css|\.json))", self.path)) and (CARTE_DIR / m.group(1)).is_file():
            self._send(200, (CARTE_DIR / m.group(1)).read_bytes(), TYPES[m.group(2)])
        elif self.path == "/api/appels":
            self._json({"en_cours": lire(LIVE_DIR), "dossiers": lire(DOSSIERS_DIR)})
        elif m := re.fullmatch(r"/api/dossiers/([\w-]+)\.json", self.path):
            f = DOSSIERS_DIR / f"{m.group(1)}.json"
            if not f.exists():
                return self._json({"erreur": "dossier introuvable"}, 404)
            self._send(200, f.read_bytes(), "application/json",
                       {"Content-Disposition": f'attachment; filename="dossier-{m.group(1)}.json"'})
        else:
            self._json({"erreur": "introuvable"}, 404)

    def do_POST(self) -> None:
        # créer le dossier à partir de la fiche d'un appel encore en cours
        m = re.fullmatch(r"/api/dossiers/([\w-]+)", self.path)
        if not m or not ID_RE.match(m.group(1)):
            return self._json({"erreur": "introuvable"}, 404)
        src = LIVE_DIR / f"{m.group(1)}.json"
        if not src.exists():
            return self._json({"erreur": "appel introuvable ou déjà terminé"}, 404)
        fiche = json.loads(src.read_text())
        fiche.update(statut="cree_pendant_appel", dossier_cree=time.strftime("%Y-%m-%dT%H:%M:%S"))
        DOSSIERS_DIR.mkdir(exist_ok=True)
        (DOSSIERS_DIR / f"{m.group(1)}.json").write_text(json.dumps(fiche, ensure_ascii=False, indent=2))
        self._json({"ok": True})

    def log_message(self, *args) -> None:
        pass  # pas de log à chaque rafraîchissement


PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Miki — écoutants 115</title>
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css"
      integrity="sha384-uTttxo/aOKbdE5RlD/SPzSDoDmNvGlUYPjONi2MN/b7c9HPSvW07OIuyP7uL6jxK" crossorigin="anonymous">
<link rel="stylesheet" href="/carte/panneau.css">
<style>
  /* couleurs de la page DIHAL (static/dihal.html) : bleu nuit, turquoise, or */
  :root {
    --bg: #061113; --surface: #0b1d1f; --card: rgba(255,255,255,.035); --ink: #e6efee; --muted: #8aa5a2;
    --faint: #5c7774; --line: rgba(140,210,200,.16); --teal: #5fd4c4; --or: #f5c35b; --accent: #5fd4c4;
    --live: #ff6b6b;
    --p5: #ff6b6b; --p4: #ff9a5c; --p3: #f5c35b; --p2: #5fd4c4; --p1: #5c7774;
    color-scheme: dark;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: radial-gradient(ellipse at 30% 0%, #0e2a2c 0%, var(--bg) 55%) fixed; color: var(--ink);
         font: 15px/1.45 -apple-system, "Inter", "Segoe UI", sans-serif; min-height: 100vh; }
  header { display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
           padding: 16px 28px; border-bottom: 1px solid var(--line); }
  header h1 { margin: 0; font-size: 15px; font-weight: 400; color: var(--muted); }
  header h1 b { color: var(--ink); font-weight: 600; letter-spacing: .02em; }
  header .etat { color: var(--faint); font-size: 12px; display: inline-flex; align-items: center; gap: 6px; }
  header .etat i { width: 7px; height: 7px; border-radius: 50%; background: var(--teal); box-shadow: 0 0 8px var(--teal); }
  header .etat.hs i { background: var(--faint); box-shadow: none; }
  main { padding: 20px 28px 48px; max-width: 1840px; margin: 0 auto;
         display: grid; grid-template-columns: minmax(0, 1fr); gap: 24px; align-items: start; }
  /* carte et hébergements : seulement quand l'écoutant a sélectionné une fiche */
  .panneau { display: none; position: sticky; top: 0; max-height: 100vh; overflow-y: auto; padding-bottom: 16px; }
  body.avec-panneau main { grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); }
  body.avec-panneau .panneau { display: block; animation: apparait .35s ease-out; }
  body.avec-panneau .grille { grid-template-columns: 1fr; }
  @keyframes apparait { from { opacity: 0; transform: translateX(12px); } }
  .panneau-haut { display: flex; justify-content: space-between; align-items: center; margin: 28px 0 12px; }
  .panneau-haut h2 { margin: 0; }
  .aide { font-size: 13px; color: var(--faint); margin: -4px 0 12px; }
  body.avec-panneau .aide { display: none; }
  /* barre de navigation entre les deux vues, même dessin que les filtres de la page DIHAL */
  nav.vues { display: flex; gap: 4px; padding: 3px; border: 1px solid var(--line); border-radius: 999px; margin-left: auto; }
  nav.vues a { color: var(--muted); font-size: 13px; padding: 6px 16px; border-radius: 999px; text-decoration: none; }
  nav.vues a:hover { color: var(--ink); }
  nav.vues a.actif { background: rgba(95,212,196,.14); color: var(--ink); box-shadow: inset 0 0 0 1px rgba(95,212,196,.35); }
  h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .12em; color: var(--muted);
       margin: 28px 0 12px; }
  .grille { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 14px; }
  .carte { background: var(--card); border: 1px solid var(--line); border-radius: 14px; cursor: pointer;
           padding: 16px 18px; border-left: 3px solid var(--pc, var(--p1)); transition: border-color .15s, box-shadow .2s; }
  .carte:hover { border-color: rgba(245,195,91,.45); border-left-color: var(--pc, var(--p1)); }
  .carte.selectionnee { border-color: rgba(245,195,91,.8); border-left-color: var(--pc, var(--p1));
                        box-shadow: 0 0 24px rgba(245,195,91,.14); }
  .haut { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .nom { font-size: 17px; font-weight: 600; }
  .prio { font-size: 11px; font-weight: 700; color: var(--bg); background: var(--pc, var(--p1));
          border-radius: 999px; padding: 2px 10px; white-space: nowrap; box-shadow: 0 0 14px color-mix(in srgb, var(--pc, var(--p1)) 40%, transparent); }
  .live { color: var(--live); font-size: 12px; font-weight: 600; }
  .live::before { content: "●"; margin-right: 4px; animation: clign 1.2s infinite; }
  @keyframes clign { 50% { opacity: .25; } }
  dl { display: grid; grid-template-columns: 130px 1fr; gap: 4px 12px; margin: 12px 0 0; font-size: 14px; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .vide { color: var(--faint); font-style: italic; }
  .motifs { margin-top: 8px; font-size: 12px; color: var(--muted); padding-left: 10px; border-left: 2px solid var(--line); }
  details { margin-top: 10px; }
  summary { cursor: pointer; color: var(--teal); font-size: 13px; }
  pre { white-space: pre-wrap; font: 12px/1.5 ui-monospace, Menlo, monospace; max-height: 260px; color: var(--muted);
        overflow: auto; background: rgba(0,0,0,.25); border: 1px solid var(--line); padding: 10px; border-radius: 8px; }
  .actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  button, a.btn { font: inherit; font-size: 13px; padding: 6px 14px; border-radius: 999px;
                  border: 1px solid var(--line); background: rgba(255,255,255,.04); color: var(--ink);
                  cursor: pointer; text-decoration: none; }
  button:hover, a.btn:hover { border-color: rgba(245,195,91,.7); }
  button.prim { background: rgba(95,212,196,.16); border-color: rgba(95,212,196,.55); color: var(--ink); }
  .aucun { color: var(--faint); padding: 20px; border: 1px dashed var(--line); border-radius: 14px; font-size: 14px; }
  @media (max-width: 1000px) {
    body.avec-panneau main { grid-template-columns: minmax(0, 1fr); }
    .panneau { position: static; order: -1; max-height: none; overflow: visible; }
  }
  @media (max-width: 600px) {
    header, main { padding-left: 16px; padding-right: 16px; }
    dl { grid-template-columns: 110px 1fr; }
  }
  @media print {
    :root { --bg: #fff; --card: #fff; --ink: #1c1b19; --muted: #555; --faint: #777; --line: #ccc; color-scheme: light; }
    body { background: #fff; }
    header, h2, .actions, summary, .live, .panneau { display: none; }
    main { display: block; }
    .carte { break-inside: avoid; border: 1px solid #999; box-shadow: none; }
    .prio { color: #fff; box-shadow: none; }
    details { display: block; }
  }
</style>
</head>
<body>
<header>
  <h1><b>MIKI</b> · Écoutants du 115</h1>
  <span class="etat" id="etat"><i></i>connexion…</span>
  <nav class="vues" aria-label="Vues">
    <a href="/" class="actif" aria-current="page">Écoutants 115</a>
    <a id="lien-dihal" href="#">Tableau de bord DIHAL</a>
  </nav>
</header>
<main>
  <div>
    <h2>Appels en cours</h2>
    <p class="aide">Sélectionnez une fiche pour afficher le lieu et les hébergements proches.</p>
    <div class="grille" id="en_cours"></div>
    <h2>Dossiers créés</h2>
    <div class="grille" id="dossiers"></div>
  </div>
  <aside class="panneau">
    <div class="panneau-haut"><h2>Lieu et hébergements</h2>
      <button type="button" data-fermer title="Échap">Fermer ✕</button></div>
    <div class="panneau-carte" id="panneau-carte"></div>
  </aside>
</main>
<script>
const DEMO = __MIKI_DEMO__ || new URLSearchParams(location.search).has("demo");
const LANGUES = __LANGUES__;
document.getElementById("lien-dihal").href = __DIHAL_URL__ || `${location.protocol}//${location.hostname}:8000/`;

// MIKI_DEMO=1 : trois fiches de scénario ajoutées aux appels en cours, dans la page seulement
// (rien n'est écrit dans sessions/ ni dossiers/). Profils et langues différents : homme seul en anglais,
// famille en arabe, femme seule en français ; la fiche reste en français. Priorités calculées par la vraie
// grille (priorite.py) pour ces critères. secours : coordonnées fixes si le géocodage IGN est injoignable.
const ilYa = min => { const d = new Date(Date.now() - min * 60000 - new Date().getTimezoneOffset() * 60000); return d.toISOString().slice(0, 19); };
const SCENARIOS = !DEMO ? [] : [
  {id: "scenario-a", demo: true, langue: "en", debut: ilYa(14), maj: ilYa(1),
   appelant: {prenom: "Karim", nom: null, age: 42, sexe: "homme"},
   situation: {recit: "A perdu son emploi puis sa chambre la semaine dernière. A dormi devant la gare cette nuit.", temps_a_la_rue: "une semaine"},
   localisation: "près de la gare de Lyon", commune: "Paris", cas_particuliers: {},
   criteres: {sans_solution_ce_soir: true}, priorite: {score: 2, niveau: "moyenne", motifs: ["sans_solution_ce_soir"]},
   infos_manquantes: ["nom"], secours: {lat: 48.8443, lon: 2.3744},
   transcription: "APPELANT: I'm near the Gare de Lyon. I lost my job and my room last week, I slept outside the station last night."},
  {id: "scenario-b", demo: true, langue: "ar", debut: ilYa(9), maj: ilYa(0),
   appelant: {prenom: "Amina", nom: null, age: 31, sexe: "femme"},
   situation: {recit: "Expulsée de son logement ce matin avec ses deux enfants. Pas de famille à Paris, aucune solution pour ce soir.", temps_a_la_rue: "depuis ce matin"},
   localisation: "place de la République", commune: "Paris", cas_particuliers: {nb_enfants: 2, ages_enfants: [4, 7]},
   criteres: {enfants_presents: true, sans_solution_ce_soir: true},
   priorite: {score: 4, niveau: "très haute", motifs: ["enfants_presents", "sans_solution_ce_soir"]},
   infos_manquantes: ["nom"], secours: {lat: 48.8674, lon: 2.3636},
   transcription: "APPELANT: أنا في ساحة الجمهورية مع طفليّ. طُردنا من البيت هذا الصباح وليس لدينا مكان للنوم."},
  {id: "scenario-c", demo: true, langue: "fr", debut: ilYa(5), maj: ilYa(0),
   appelant: {prenom: "Julie", nom: null, age: 24, sexe: "femme"},
   situation: {recit: "A quitté ce soir l'appartement de son compagnon après une dispute. Seule, avec un petit sac. Première nuit dehors.", temps_a_la_rue: "ce soir"},
   localisation: "devant la gare du Nord", commune: "Paris", cas_particuliers: {},
   criteres: {premiere_nuit_rue: true, sans_solution_ce_soir: true},
   priorite: {score: 3, niveau: "haute", motifs: ["premiere_nuit_rue", "sans_solution_ce_soir"]},
   infos_manquantes: ["nom", "cause"], secours: {lat: 48.8809, lon: 2.3553},
   transcription: "APPELANT: Je suis devant la gare du Nord, je ne sais pas où dormir ce soir."},
];
let selection = null;  // fiche suivie par la carte : aucune tant que l'écoutant n'en choisit pas
let fiches = [];       // dernières fiches affichées (en cours, scénarios, dossiers)
const COULEURS = {5: "var(--p5)", 4: "var(--p4)", 3: "var(--p3)", 2: "var(--p2)", 1: "var(--p1)"};
const CAS = {grossesse: "Grossesse", handicap: "Handicap", blessure: "Blessure", sante: "Santé",
             nb_enfants: "Enfants", ages_enfants: "Âges enfants",
             autres_accompagnants: "Accompagnants", animal: "Animal"};
const ouverts = new Set();  // transcriptions dépliées, conservées entre deux rafraîchissements

// Miki dit le numéro en lettres pour la voix ; à l'écran on l'affiche en chiffres
const chiffres = s => String(s ?? "").replace(/\b(one hundred (and )?fifteen|cent quinze)\b/gi, "115");
const esc = s => chiffres(s).replace(/[&<>"']/g, c =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const val = v => {
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length))
    return '<span class="vide">—</span>';
  if (v === true) return "oui";
  if (v === false) return "non";
  return esc(Array.isArray(v) ? v.join(", ") : v);
};
const heure = iso => iso ? new Date(iso).toLocaleTimeString("fr-FR", {hour: "2-digit", minute: "2-digit"}) : "";

function carte(d, live) {
  const a = d.appelant || {}, s = d.situation || {}, p = d.priorite || {score: 1, niveau: "—", motifs: []};
  const cas = Object.entries(d.cas_particuliers || {})
    .filter(([, v]) => v !== null && v !== false && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${CAS[k] || k} : ${val(v)}`).join(" · ");
  const nom = [a.prenom, a.nom].filter(Boolean).join(" ") || "Appelant inconnu";
  const tid = "t-" + d.id;
  const sel = d.id === selection ? " selectionnee" : "";
  return `<article class="carte${sel}" data-sel="${esc(d.id)}" style="--pc:${COULEURS[p.score] || COULEURS[1]}">
    <div class="haut">
      <span class="nom">${esc(nom)}</span>
      <span class="prio">Priorité ${p.score} · ${esc(p.niveau)}</span>
    </div>
    <div class="haut" style="margin-top:4px;font-size:13px;color:var(--muted)">
      <span>${live ? '<span class="live">en direct</span> · ' : ""}début ${heure(d.debut)} · maj ${heure(d.maj)}</span>
      <span>${d.valide ? "validé par l'appelant" : ""}</span>
    </div>
    <dl>
      <dt>Langue de l'appel</dt><dd>${val(LANGUES[d.langue] || d.langue)}</dd>
      <dt>Âge</dt><dd>${val(a.age)}</dd>
      <dt>Sexe</dt><dd>${val(a.sexe)}</dd>
      <dt>Localisation</dt><dd>${val(d.localisation)}</dd>
      <dt>À la rue depuis</dt><dd>${val(s.temps_a_la_rue)}</dd>
      <dt>Situation</dt><dd>${val(s.recit)}</dd>
      <dt>Cas particuliers</dt><dd>${cas || val(null)}</dd>
      ${live ? `<dt>Manque</dt><dd>${val(d.infos_manquantes)}</dd>` : ""}
    </dl>
    ${p.motifs && p.motifs.length ? `<div class="motifs">Motifs : ${esc(p.motifs.join(", ").replaceAll("_", " "))}</div>` : ""}
    <details data-id="${tid}" ${ouverts.has(tid) ? "open" : ""}>
      <summary>Transcription</summary><pre dir="auto">${esc(d.transcription) || "…"}</pre>
    </details>
    ${d.demo ? "" : `<div class="actions">
      ${live ? `<button class="prim" data-creer="${esc(d.id)}">Créer le dossier</button>`
             : `<a class="btn" href="/api/dossiers/${esc(d.id)}.json">Télécharger</a>
                <button data-imprimer>Imprimer</button>`}
    </div>`}
  </article>`;
}

function remplir(el, liste, live) {
  el.innerHTML = liste.length ? liste.map(d => carte(d, live)).join("")
    : `<div class="aucun">${live ? "Aucun appel en cours." : "Aucun dossier pour l'instant."}</div>`;
}

// la carte suit la fiche que l'écoutant a sélectionnée ; sans sélection, le panneau est masqué.
// Le panneau (et la carte 3D) n'est créé qu'à la première sélection.
function suivreSelection() {
  if (!fiches.some(d => d.id === selection)) selection = null;   // fiche disparue
  document.querySelectorAll(".carte[data-sel]").forEach(c => c.classList.toggle("selectionnee", c.dataset.sel === selection));
  document.body.classList.toggle("avec-panneau", !!selection);
  const d = fiches.find(f => f.id === selection) || null;
  if (d && !window.panneauCarte && window.creerPanneauCarte)
    window.panneauCarte = window.creerPanneauCarte(document.getElementById("panneau-carte"));
  window.panneauCarte?.suivre(d);
}

function selectionner(id) {   // id null : fermer le panneau (bouton « Fermer » ou Échap)
  selection = id;
  suivreSelection();
}

async function rafraichir() {
  try {
    const r = await fetch("/api/appels");
    const data = await r.json();
    const en_cours = [...data.en_cours, ...SCENARIOS];
    fiches = [...en_cours, ...data.dossiers];
    remplir(document.getElementById("en_cours"), en_cours, true);
    remplir(document.getElementById("dossiers"), data.dossiers, false);
    const etat = document.getElementById("etat");
    etat.className = "etat";
    etat.innerHTML = `<i></i>direct · ${en_cours.length} en cours · ${data.dossiers.length} dossier${data.dossiers.length > 1 ? "s" : ""}`;
    suivreSelection();
  } catch {
    const etat = document.getElementById("etat");
    etat.className = "etat hs";
    etat.innerHTML = "<i></i>serveur injoignable, nouvel essai…";
  }
}

document.addEventListener("toggle", e => {
  const id = e.target.dataset && e.target.dataset.id;
  if (id) e.target.open ? ouverts.add(id) : ouverts.delete(id);
}, true);


document.addEventListener("click", async e => {
  const b = e.target.closest("button");
  const choisie = !b && !e.target.closest("details, a") && e.target.closest(".carte[data-sel]");
  if (choisie) return selectionner(choisie.dataset.sel);
  if (!b) return;
  if (b.hasAttribute("data-fermer")) {
    selectionner(null);
  } else if (b.dataset.creer) {
    b.disabled = true;
    const r = await fetch(`/api/dossiers/${b.dataset.creer}`, {method: "POST"});
    b.textContent = r.ok ? "Dossier créé" : "Échec";
    rafraichir();
  } else if (b.hasAttribute("data-imprimer")) {
    const carte = b.closest(".carte");
    document.querySelectorAll(".carte").forEach(c => c.style.display = c === carte ? "" : "none");
    window.print();
    document.querySelectorAll(".carte").forEach(c => c.style.display = "");
  }
});

addEventListener("keydown", e => { if (e.key === "Escape" && selection) selectionner(null); });

rafraichir();
setInterval(rafraichir, 1000);
</script>
<script src="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"
        integrity="sha384-5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp" crossorigin="anonymous"></script>
<script type="module">
  import { creerPanneau } from "/carte/panneau.mjs";
  window.creerPanneauCarte = creerPanneau;
  suivreSelection();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print(f"Tableau de bord Miki : http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
