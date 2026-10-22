"""Tableau de bord des écoutants : fiches des appels en cours, en temps réel, et dossiers créés.

L'agent écrit la fiche de chaque appel dans sessions/en_cours/ à chaque analyse du penseur ;
en fin d'appel elle devient un dossier dans dossiers/. Cette page relit ces fichiers chaque
seconde. Le bouton « Créer le dossier » fige la fiche d'un appel encore en cours.

Lancer :  python dashboard.py      puis ouvrir http://localhost:8115
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
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
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
<style>
  :root {
    --bg: #f5f4f0; --card: #fff; --ink: #1c1b19; --muted: #6b6860; --line: #e2dfd7;
    --accent: #2f5d8a; --live: #c0392b;
    --p5: #b3261e; --p4: #d9622b; --p3: #c99a06; --p2: #4f7cac; --p1: #8a8780;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #161614; --card: #211f1d; --ink: #ecebe7; --muted: #a09d95; --line: #35322e;
      --accent: #8fb3dc; --live: #ef6b5e;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
           padding: 18px 24px; border-bottom: 1px solid var(--line); }
  header h1 { margin: 0; font-size: 20px; }
  header .etat { color: var(--muted); font-size: 13px; }
  main { padding: 20px 24px 48px; max-width: 1400px; margin: 0 auto; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
       margin: 28px 0 12px; }
  .grille { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 16px; }
  .carte { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
           padding: 16px 18px; border-left: 5px solid var(--pc, var(--p1)); }
  .haut { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .nom { font-size: 18px; font-weight: 650; }
  .prio { font-size: 12px; font-weight: 700; color: #fff; background: var(--pc, var(--p1));
          border-radius: 999px; padding: 2px 10px; white-space: nowrap; }
  .live { color: var(--live); font-size: 12px; font-weight: 700; }
  .live::before { content: "●"; margin-right: 4px; animation: clign 1.2s infinite; }
  @keyframes clign { 50% { opacity: .25; } }
  dl { display: grid; grid-template-columns: 130px 1fr; gap: 4px 12px; margin: 12px 0 0; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .vide { color: var(--muted); font-style: italic; }
  .motifs { margin-top: 8px; font-size: 13px; color: var(--muted); }
  details { margin-top: 10px; }
  summary { cursor: pointer; color: var(--accent); font-size: 13px; }
  pre { white-space: pre-wrap; font: 13px/1.5 ui-monospace, Menlo, monospace; max-height: 260px;
        overflow: auto; background: var(--bg); padding: 10px; border-radius: 6px; }
  .actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  button, a.btn { font: inherit; font-size: 13px; padding: 6px 12px; border-radius: 6px;
                  border: 1px solid var(--line); background: var(--bg); color: var(--ink);
                  cursor: pointer; text-decoration: none; }
  button.prim { background: var(--accent); border-color: var(--accent); color: var(--card); }
  .aucun { color: var(--muted); padding: 20px; border: 1px dashed var(--line); border-radius: 10px; }
  @media (max-width: 600px) {
    header, main { padding-left: 16px; padding-right: 16px; }
    .grille { grid-template-columns: 1fr; }
    dl { grid-template-columns: 110px 1fr; }
  }
  @media print {
    header, h2, .actions, summary, .live { display: none; }
    .carte { break-inside: avoid; border: 1px solid #999; }
    details { display: block; }
  }
</style>
</head>
<body>
<header>
  <h1>Miki — appels du 115</h1>
  <span class="etat" id="etat">connexion…</span>
</header>
<main>
  <h2>Appels en cours</h2>
  <div class="grille" id="en_cours"></div>
  <h2>Dossiers créés</h2>
  <div class="grille" id="dossiers"></div>
</main>
<script>
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
  return `<article class="carte" style="--pc:${COULEURS[p.score] || COULEURS[1]}">
    <div class="haut">
      <span class="nom">${esc(nom)}</span>
      <span class="prio">Priorité ${p.score} · ${esc(p.niveau)}</span>
    </div>
    <div class="haut" style="margin-top:4px;font-size:13px;color:var(--muted)">
      <span>${live ? '<span class="live">en direct</span> · ' : ""}début ${heure(d.debut)} · maj ${heure(d.maj)}</span>
      <span>${d.valide ? "validé par l'appelant" : ""}</span>
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
    <div class="actions">
      ${live ? `<button class="prim" data-creer="${esc(d.id)}">Créer le dossier</button>`
             : `<a class="btn" href="/api/dossiers/${esc(d.id)}.json">Télécharger</a>
                <button data-imprimer>Imprimer</button>`}
    </div>
  </article>`;
}

function remplir(el, liste, live) {
  el.innerHTML = liste.length ? liste.map(d => carte(d, live)).join("")
    : `<div class="aucun">${live ? "Aucun appel en cours." : "Aucun dossier pour l'instant."}</div>`;
}

async function rafraichir() {
  try {
    const r = await fetch("/api/appels");
    const data = await r.json();
    remplir(document.getElementById("en_cours"), data.en_cours, true);
    remplir(document.getElementById("dossiers"), data.dossiers, false);
    document.getElementById("etat").textContent =
      `${data.en_cours.length} en cours · ${data.dossiers.length} dossiers · mis à jour ${new Date().toLocaleTimeString("fr-FR")}`;
  } catch {
    document.getElementById("etat").textContent = "serveur injoignable, nouvel essai…";
  }
}

document.addEventListener("toggle", e => {
  const id = e.target.dataset && e.target.dataset.id;
  if (id) e.target.open ? ouverts.add(id) : ouverts.delete(id);
}, true);

document.addEventListener("click", async e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.creer) {
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
</body>
</html>
"""


if __name__ == "__main__":
    print(f"Tableau de bord Miki : http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
