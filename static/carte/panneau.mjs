// Panneau de l'écoutant : carte 3D, hébergements proches par catégorie de places, trajet à pied
// vers le centre choisi. Les centres et leurs places viennent du jeu fictif static/carte/demo_hebergements.json.
// Il suit la fiche sélectionnée du tableau de bord : creerPanneau(racine).suivre({id, localisation, commune}).
// Il ne modifie jamais la fiche : la correction de lieu reste locale à l'écran de l'écoutant,
// et aucun centre n'est choisi automatiquement : le trajet n'apparaît que sur clic de l'écoutant.

import { creerCarte3d } from "./carte3d.mjs";
import {
  CATEGORIES, categorieDeFiche, formatDistance, formatDuree, geocoder, hebergementsProches, itineraire, ligneDroite, normaliser, requeteLieu,
} from "./geo.mjs";
import { creerOeil } from "./oeil.mjs";

const DEBOUNCE_MS = 700;
const REESSAI_MS = 15000;
const RAYONS = [2, 5, 10];
const PARIS = { lat: 48.8566, lon: 2.3522 };   // vue d'attente et départage des homonymes, jamais un marqueur
const QUALITES = {
  precise: ["précise", "ok"], approximative: ["approximative", "alerte"], ambigue: ["ambiguë, à vérifier", "alerte"],
  choisie: ["choisie par l'écoutant", "ok"], secours: ["position approximative", "alerte"],
};

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nombre = v => v === null || v === undefined ? "—" : String(Math.round(v));
const coord = (v, pos, neg) => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}`;

const GABARIT = `
  <div class="pc-lieu">
    <div class="pc-lieu-texte"><span class="pc-etiquette">Localisation déclarée</span>
      <b data-r="declaree">—</b><span data-r="statut" class="pc-statut"></span></div>
    <div class="pc-rayons" role="group" aria-label="Rayon de recherche">
      ${RAYONS.map(r => `<button type="button" data-rayon="${r}">${r} km</button>`).join("")}</div>
  </div>
  <div class="pc-categories" role="group" aria-label="Catégorie de places">
    <span class="pc-etiquette">Places</span>
    <button type="button" data-cat="">Toutes</button>
    ${Object.entries(CATEGORIES).map(([c, lib]) => `<button type="button" data-cat="${c}">${lib}</button>`).join("")}
    <span data-r="cat-auto" class="pc-cat-auto"></span>
  </div>
  <div data-r="alternatives" class="pc-alternatives" hidden></div>
  <form data-r="correction" class="pc-correction" hidden>
    <input name="lieu" placeholder="Corriger le lieu (ex. 15 rue de Rivoli, Paris)" autocomplete="off">
    <button type="submit">Localiser</button>
    <button type="button" data-r="annuler" hidden>Revenir à la fiche</button>
  </form>
  <div class="pc-carte-cadre">
    <div data-r="carte" class="pc-carte"></div>
    <div class="pc-hud" data-r="hud" hidden></div>
    <div data-r="attente" class="pc-voile">
      <div data-r="oeil" class="pc-oeil"></div>
      <div class="pc-balayage"></div>
      <div data-r="attente-texte" class="pc-voile-texte">En attente de la localisation</div>
      <div class="pc-voile-sous">MIKI écoute l'appel : la carte se placera dès que le lieu sera dit.</div>
    </div>
    <div class="pc-legende">
      <span><i class="pc-pt personne"></i>Personne (lieu déclaré)</span>
      <span><i class="pc-pt libre"></i>Places disponibles</span>
      <span><i class="pc-pt complet"></i>Complet</span>
      <span><i class="pc-pt trajet"></i>Trajet à pied</span>
    </div>
  </div>
  <div class="pc-bas">
    <section class="pc-liste-cadre">
      <h3 data-r="titre-liste" class="pc-titre-liste">Hébergements proches</h3>
      <div data-r="liste" class="pc-liste"></div>
    </section>
  </div>`;

export function creerPanneau(racine, { hebergementsUrl = "/carte/demo_hebergements.json" } = {}) {
  racine.innerHTML = GABARIT;
  const $ = nom => racine.querySelector(`[data-r="${nom}"]`);
  const e = {
    dossierId: null, cle: null, texte: null, commune: null, secours: null, correction: null,
    position: null, statut: "attente", rayon: 2,   // 2 km : la vue où la 3D se lit le mieux
    choisi: null, route: null,   // centre cliqué par l'écoutant et trajet à pied vers lui
    categorie: null, categorieFiche: null, categorieManuelle: false,   // catégorie de places affichée
  };
  let hebergements = [];
  let jeton = 0, minuteur = null, controleur = null, jetonRoute = 0, controleurRoute = null;
  const oeil = creerOeil($("oeil"));

  // ---------- carte 3D (chargée en arrière-plan : le reste du panneau fonctionne sans elle) ----------
  let carte = null;
  creerCarte3d($("carte"), { surChoix: id => choisir(id) })
    .then(c => {
      carte = c;
      new ResizeObserver(() => carte.redimensionner()).observe($("carte"));
      if (e.position) { carte.placer(e.position, e.rayon, libelleCourt()); majCarteHebergements(); } else carte.attente();
    })
    .catch(err => {
      $("carte").innerHTML = `<div class="pc-indispo">Carte 3D indisponible (${esc(err.message)}).<br>La liste des hébergements reste à jour.</div>`;
    });

  fetch(hebergementsUrl).then(r => r.json()).then(h => { hebergements = h; dessinerHebergements(); })
    .catch(() => { $("liste").innerHTML = `<div class="pc-discret">Liste des hébergements indisponible.</div>`; });

  const libelleCourt = () => e.position ? e.position.libelle.split(",")[0] : "";
  const proches = () => e.position ? hebergementsProches(e.position, hebergements, e.rayon, e.categorie) : [];

  // ---------- affichage ----------
  function afficherLieu() {
    const texte = e.correction ?? e.texte;
    $("declaree").textContent = texte || (e.commune ? `${e.commune} (commune seulement)` : "—");
    let html = "";
    if (e.statut === "resolution") html = `<span class="pc-tag"><span class="pc-rond"></span>résolution…</span>`;
    else if (e.statut === "introuvable") html = `<span class="pc-tag alerte">lieu non reconnu${e.position ? " · position précédente affichée" : ""}</span>`;
    else if (e.statut === "erreur") html = `<span class="pc-tag alerte">géocodage indisponible${e.position ? " · position précédente affichée" : ""}</span>`;
    if (e.position && e.statut !== "resolution") {
      const [lib, ton] = QUALITES[e.position.qualite] || QUALITES.approximative;
      html = `<span class="pc-tag ${ton}" title="${esc(e.position.libelle)}">${esc(e.position.libelle)} · ${lib}</span>` + html;
    }
    $("statut").innerHTML = html;
    const attente = !e.position;
    $("attente").classList.toggle("cache", !attente);
    $("attente").classList.toggle("cherche", e.statut === "resolution");
    oeil.actif(attente); oeil.chercher(e.statut === "resolution");
    $("attente-texte").textContent = e.statut === "resolution" ? "Localisation en cours de résolution…"
      : e.statut === "erreur" ? "Service de géocodage injoignable, nouvel essai automatique…"
      : e.statut === "introuvable" ? "Lieu non reconnu : corrigez-le ci-dessus" : "En attente de la localisation";
    $("correction").hidden = !e.dossierId;
    $("annuler").hidden = e.correction === null;
    racine.querySelectorAll("[data-rayon]").forEach(b => b.classList.toggle("actif", +b.dataset.rayon === e.rayon));
    racine.querySelectorAll("[data-cat]").forEach(b => b.classList.toggle("actif", (b.dataset.cat || null) === e.categorie));
    $("cat-auto").textContent = e.categorieFiche && !e.categorieManuelle ? "d'après la fiche" : "";
    const alt = e.position?.alternatives || [];
    $("alternatives").hidden = !alt.length;
    $("alternatives").innerHTML = alt.length ? `<span>Autres lieux possibles :</span>` + alt.map((a, i) =>
      `<button type="button" data-alt="${i}">${esc(a.libelle)}</button>`).join("") : "";
    afficherHud();
  }

  // bandeau d'informations sur la carte : coordonnées, rayon, nombre de centres
  function afficherHud() {
    const hud = $("hud");
    hud.hidden = !e.position;
    if (!e.position) return;
    const liste = proches(), libres = liste.filter(h => h.disponibles > 0).length;
    hud.innerHTML = `<span><small>Position</small>${coord(e.position.lat, "N", "S")} · ${coord(e.position.lon, "E", "O")}</span>
      <span><small>Rayon</small>${e.rayon} km</span>
      <span><small>Centres${e.categorie ? ` · ${CATEGORIES[e.categorie].toLowerCase()}` : ""}</small>${liste.length} · ${libres} avec places</span>`;
  }

  function majCarteHebergements() {
    if (!carte) return;
    carte.choisir(e.choisi);
    carte.hebergements(proches(), e.route, e.categorie);
    carte.trajet(e.route);
  }

  function dessinerHebergements() {
    const liste = proches();
    majCarteHebergements();
    afficherHud();
    $("titre-liste").textContent = `Hébergements proches${e.categorie ? ` · ${CATEGORIES[e.categorie]}` : ""}`;
    const el = $("liste");
    el.classList.remove("pc-fondu"); void el.offsetWidth; el.classList.add("pc-fondu");
    if (!e.position) { el.innerHTML = `<div class="pc-discret">Les hébergements proches apparaîtront dès que le lieu sera connu.</div>`; return; }
    el.innerHTML = liste.length ? liste.map(h => {
      const route = h.id === e.choisi ? e.route : null;
      return `
      <button type="button" class="pc-heb ${h.disponibles > 0 ? "libre" : "complet"}${h.id === e.choisi ? " choisi" : ""}" data-heb="${esc(h.id)}">
        <span class="pc-heb-haut"><b>${esc(h.name)}</b><span>${formatDistance(h.distance)}</span></span>
        <span class="pc-heb-bas">${esc(h.type)}${h.accessible ? " · ♿ PMR" : ""}</span>
        <span class="pc-cats">${Object.entries(h.places || {}).map(([c, p]) =>
          `<span class="pc-cat${p.disponibles > 0 ? " libre" : ""}${c === e.categorie ? " vise" : ""}">${CATEGORIES[c]} <b>${p.disponibles}</b>/${p.total}</span>`).join("")}</span>
        <span class="pc-heb-pied"><span class="pc-places">${h.disponibles > 0 ? `${h.disponibles} place${h.disponibles > 1 ? "s" : ""} disponible${h.disponibles > 1 ? "s" : ""}` : "complet"}</span>
        ${route ? `<span class="pc-route">${route.volOiseau ? "≈ " : ""}${formatDuree(route.dureeS)} à pied · ${formatDistance(route.distanceM / 1000)}${route.volOiseau ? " (vol d'oiseau)" : ""}</span>`
                : h.id === e.choisi ? `<span class="pc-route">calcul du trajet…</span>` : ""}</span>
      </button>`;
    }).join("")
      : `<div class="pc-discret">Aucun hébergement${e.categorie ? ` pour « ${CATEGORIES[e.categorie]} »` : ""} à moins de ${e.rayon} km.</div>`;
  }

  function toutDessiner() {
    afficherLieu(); dessinerHebergements();
  }

  // ---------- enchaînement : lieu → coordonnées → carte, hébergements ----------
  async function resoudre() {
    const moi = ++jeton;
    controleur?.abort();
    controleur = new AbortController();
    const texte = e.correction ?? e.texte;
    const secours = () => ({ ...e.secours, libelle: texte ?? e.commune, qualite: "secours", alternatives: [] });
    try {
      let pos = await geocoder(texte, e.commune, { signal: controleur.signal, reference: e.position || PARIS });
      if (moi !== jeton) return;   // une localisation plus récente est arrivée entre-temps
      if (!pos && e.secours) pos = secours();
      if (!pos) { e.statut = "introuvable"; afficherLieu(); return; }
      appliquer(pos);
    } catch {
      if (moi !== jeton) return;
      if (e.secours) return appliquer(secours());
      e.statut = "erreur"; afficherLieu();
      minuteur = setTimeout(() => moi === jeton && resoudre(), REESSAI_MS);   // panne réseau : on réessaie seul
    }
  }

  function appliquer(pos) {
    oublierChoix();
    e.position = pos; e.statut = "ok";
    toutDessiner();
    carte?.placer(pos, e.rayon, libelleCourt());
    majCarteHebergements();
  }

  // l'écoutant clique un centre (liste ou carte) : trajet à pied IGN, ligne droite si le service ne répond pas
  async function choisir(id) {
    const h = proches().find(x => x.id === id);
    if (!h) return;
    const moi = ++jetonRoute;
    controleurRoute?.abort();
    controleurRoute = new AbortController();
    e.choisi = id; e.route = null;
    dessinerHebergements();
    carte?.cadrerChoix(h);
    const depart = e.position, arrivee = { lat: h.latitude, lon: h.longitude };
    let route;
    try { route = await itineraire(depart, arrivee, controleurRoute.signal); } catch { route = ligneDroite(depart, arrivee); }
    if (moi !== jetonRoute || e.position !== depart) return;
    e.route = route;
    dessinerHebergements();
  }

  function oublierChoix() {
    jetonRoute++; controleurRoute?.abort();
    e.choisi = null; e.route = null;
  }

  function planifier() {
    clearTimeout(minuteur);
    e.statut = "resolution"; afficherLieu();
    minuteur = setTimeout(resoudre, DEBOUNCE_MS);
  }

  function remettreAZero() {
    clearTimeout(minuteur); jeton++; controleur?.abort(); oublierChoix();
    Object.assign(e, { cle: null, texte: null, commune: null, secours: null, correction: null, position: null,
                       statut: "attente",
                       categorie: null, categorieFiche: null, categorieManuelle: false });
    carte?.attente();
  }

  // ---------- interactions ----------
  racine.addEventListener("click", ev => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.dataset.cat !== undefined) {
      e.categorie = b.dataset.cat || null; e.categorieManuelle = true;
      if (e.choisi && !proches().some(h => h.id === e.choisi)) oublierChoix();   // centre sans cette catégorie
      dessinerHebergements(); afficherLieu();
    } else if (b.dataset.rayon) {
      e.rayon = +b.dataset.rayon;
      if (e.choisi && !proches().some(h => h.id === e.choisi)) oublierChoix();   // centre sorti du rayon
      carte?.rayon(e.rayon);
      dessinerHebergements(); afficherLieu();
    } else if (b.dataset.heb) {
      choisir(b.dataset.heb);
    } else if (b.dataset.alt) {
      const a = e.position.alternatives[+b.dataset.alt];
      jeton++;
      appliquer({ ...a, qualite: "choisie", alternatives: [] });
    } else if (b.dataset.r === "annuler") {
      e.correction = null; planifier();
    }
  });
  $("correction").addEventListener("submit", ev => {
    ev.preventDefault();
    const lieu = ev.target.lieu.value.trim();
    if (!lieu) return;
    e.correction = lieu; ev.target.lieu.value = ""; planifier();
  });

  toutDessiner();
  return {
    // appelé à chaque rafraîchissement du tableau de bord : ne fait rien tant que le lieu n'a pas changé
    suivre(dossier) {
      const id = dossier?.id ?? null;
      if (id !== e.dossierId) { remettreAZero(); e.dossierId = id; toutDessiner(); }
      if (!dossier) return;
      // catégorie déduite de la fiche (enfants, sexe) : suivie tant que l'écoutant n'en a pas choisi une autre
      const cat = categorieDeFiche(dossier);
      if (cat !== e.categorieFiche) {
        e.categorieFiche = cat;
        if (!e.categorieManuelle && cat !== e.categorie) {
          e.categorie = cat;
          if (e.choisi && !proches().some(h => h.id === e.choisi)) oublierChoix();
          dessinerHebergements();
        }
        afficherLieu();
      }
      const texte = dossier.localisation || null, commune = dossier.commune || null;
      const cle = normaliser(requeteLieu(texte, commune));
      if (cle === e.cle) return;
      e.cle = cle; e.texte = texte; e.commune = commune; e.secours = dossier.secours || null;
      e.correction = null;   // l'appelant a précisé son lieu : sa parole reprend le dessus sur la correction
      if (!cle) {
        clearTimeout(minuteur); jeton++; oublierChoix();
        e.position = null; e.statut = "attente"; toutDessiner(); carte?.attente(); return;
      }
      planifier();
    },
  };
}
