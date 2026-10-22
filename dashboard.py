"""Tableau de bord des écoutants : fiches des appels en cours, en temps réel, et dossiers créés.

L'agent écrit la fiche de chaque appel dans sessions/en_cours/ à chaque analyse du penseur ;
en fin d'appel elle devient un dossier dans dossiers/. Cette page relit ces fichiers chaque
seconde. Le bouton « Créer le dossier » fige la fiche d'un appel encore en cours.

À droite, la carte 3D suit la fiche sélectionnée (static/carte/) : lieu géocodé par l'IGN, météo
Open-Meteo, hébergements fictifs proches, trajet à pied IGN vers le centre que l'écoutant choisit.
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

ROOT = Path(__file__).parent
LIVE_DIR = ROOT / "sessions" / "en_cours"
DOSSIERS_DIR = ROOT / "dossiers"
CARTE_DIR = ROOT / "static" / "carte"
TYPES = {".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8"}
DEMO = os.environ.get("MIKI_DEMO", "").lower() in ("1", "true", "oui")
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
            page = PAGE.replace("__MIKI_DEMO__", "true" if DEMO else "false")
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
         display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); gap: 24px; align-items: start; }
  .panneau { position: sticky; top: 0; max-height: 100vh; overflow-y: auto; padding-bottom: 16px; }
  .panneau > h2 { margin-top: 28px; }
  .demo { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; margin-left: auto; font-size: 12px; color: var(--muted);
          padding: 3px 3px 3px 12px; border: 1px solid rgba(245,195,91,.35); border-radius: 999px; }
  .demo[hidden] { display: none; }
  .demo b { color: var(--or); font-weight: 600; margin-right: 6px; letter-spacing: .04em; }
  .demo button { border: 0; border-radius: 999px; background: none; color: var(--muted); font-size: 12px; padding: 5px 12px; }
  .demo button:hover { background: rgba(245,195,91,.12); color: var(--ink); }
  .badge-demo { font-size: 11px; font-weight: 600; color: var(--or); border: 1px dashed rgba(245,195,91,.6);
                border-radius: 999px; padding: 1px 8px; }
  h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .12em; color: var(--muted);
       margin: 28px 0 12px; }
  .grille { display: grid; grid-template-columns: 1fr; gap: 14px; }
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
    main { grid-template-columns: minmax(0, 1fr); }
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
  <div class="demo" id="demo" hidden>
    <b>MODE DÉMO</b>
    <button data-scenario="A">A · Gare de Lyon</button>
    <button data-scenario="B">B · République</button>
    <button data-scenario="C">C · Gare du Nord</button>
    <button data-scenario="">Retirer</button>
  </div>
</header>
<main>
  <div>
    <h2>Appels en cours</h2>
    <div class="grille" id="en_cours"></div>
    <h2>Dossiers créés</h2>
    <div class="grille" id="dossiers"></div>
  </div>
  <aside class="panneau">
    <h2>Lieu, météo et hébergements</h2>
    <div class="panneau-carte" id="panneau-carte"></div>
  </aside>
</main>
<script>
const DEMO = __MIKI_DEMO__ || new URLSearchParams(location.search).has("demo");
// scénarios joués dans la page seulement : rien n'est écrit dans sessions/ ni dossiers/.
// secours : coordonnées fixes si le géocodage IGN est injoignable pendant la démo.
const SCENARIOS = {
  A: {localisation: "near the Gare de Lyon train station", secours: {lat: 48.8443, lon: 2.3744}},
  B: {localisation: "Place de la République", secours: {lat: 48.8674, lon: 2.3636}},
  C: {localisation: "outside Gare du Nord", secours: {lat: 48.8809, lon: 2.3553}},
};
let demo = null;       // fiche fictive du scénario en cours
let selection = null;  // fiche suivie par la carte
let fiches = [];       // dernières fiches affichées (démo, en cours, dossiers)
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
      <span>${d.demo ? '<span class="badge-demo">DÉMO · fiche fictive</span>' : d.valide ? "validé par l'appelant" : ""}</span>
    </div>
    <dl>
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
      <summary>Transcription</summary><pre>${esc(d.transcription) || "…"}</pre>
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

// la carte suit la fiche choisie ; sans choix, l'appel en cours le plus urgent
function suivreSelection() {
  if (!fiches.some(d => d.id === selection)) selection = fiches[0] ? fiches[0].id : null;
  document.querySelectorAll(".carte[data-sel]").forEach(c => c.classList.toggle("selectionnee", c.dataset.sel === selection));
  const d = fiches.find(f => f.id === selection);
  if (window.panneauCarte) window.panneauCarte.suivre(d ? {id: d.id, localisation: d.localisation, commune: d.commune, secours: d.secours} : null);
}

async function rafraichir() {
  try {
    const r = await fetch("/api/appels");
    const data = await r.json();
    const en_cours = demo ? [demo, ...data.en_cours] : data.en_cours;
    fiches = [...en_cours, ...data.dossiers];
    if (!fiches.some(d => d.id === selection)) selection = fiches[0] ? fiches[0].id : null;
    remplir(document.getElementById("en_cours"), en_cours, true);
    remplir(document.getElementById("dossiers"), data.dossiers, false);
    const etat = document.getElementById("etat");
    etat.className = "etat";
    etat.innerHTML = `<i></i>direct · ${data.en_cours.length} en cours · ${data.dossiers.length} dossiers · ${new Date().toLocaleTimeString("fr-FR")}`;
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

function jouerScenario(code) {
  if (!code) { demo = null; return rafraichir(); }
  const sc = SCENARIOS[code], maintenant = new Date().toISOString().slice(0, 19);
  // comme pendant un vrai appel : la fiche arrive d'abord sans lieu, puis le lieu est extrait
  demo = {id: "demo-scenario", demo: true, debut: maintenant, maj: maintenant, commune: null, localisation: null,
          appelant: {prenom: "Scénario", nom: code}, situation: {recit: "Fiche fictive de démonstration."},
          infos_manquantes: ["localisation"], transcription: "", secours: sc.secours};
  selection = demo.id;
  rafraichir();
  setTimeout(() => {
    if (!demo || demo.appelant.nom !== code) return;
    Object.assign(demo, {localisation: sc.localisation, commune: "Paris", infos_manquantes: [], maj: new Date().toISOString().slice(0, 19),
                         transcription: `APPELANT: I'm ${sc.localisation}.`});
    rafraichir();
  }, 1500);
}

if (DEMO) document.getElementById("demo").hidden = false;

document.addEventListener("click", async e => {
  const b = e.target.closest("button");
  const choisie = !b && !e.target.closest("details, a") && e.target.closest(".carte[data-sel]");
  if (choisie) { selection = choisie.dataset.sel; return suivreSelection(); }
  if (!b) return;
  if (b.dataset.scenario !== undefined) {
    jouerScenario(b.dataset.scenario);
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

rafraichir();
setInterval(rafraichir, 1000);
</script>
<script src="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js"
        integrity="sha384-5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp" crossorigin="anonymous"></script>
<script type="module">
  import { creerPanneau } from "/carte/panneau.mjs";
  window.panneauCarte = creerPanneau(document.getElementById("panneau-carte"), {demo: DEMO});
  suivreSelection();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print(f"Tableau de bord Miki : http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
