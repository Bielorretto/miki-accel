// Carte 3D du panneau écoutant (MapLibre GL + tuiles vectorielles OpenFreeMap, sans clé).
// Uniquement l'affichage : panneau.mjs décide quoi montrer, ce module dessine.
//   bâtiments extrudés aux couleurs du tableau de bord DIHAL, faisceaux lumineux sur la personne et les centres
//   (centres et places : jeu fictif static/carte/demo_hebergements.json),
//   zone de recherche en anneau holographique, trajet à pied animé, étiquettes HUD reliées aux marqueurs.

import { CATEGORIES, cercle, formatDistance, formatDuree } from "./geo.mjs";

const STYLE = "https://tiles.openfreemap.org/styles/dark";
const PARIS = [2.3522, 48.8566];
const C = { fond: "#061113", eau: "#0a2629", teal: "#5fd4c4", tealSombre: "#1fa594", or: "#f5c35b",
            personne: "#ff5a52", libre: "#4cd6a0", complet: "#7d8f8d", texte: "#8aa5a2", faible: "#5c7774" };
const reduit = matchMedia("(prefers-reduced-motion: reduce)").matches;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const webglDisponible = () => {
  try { return !!document.createElement("canvas").getContext("webgl2"); } catch { return false; }
};

// le style sombre d'OpenFreeMap, recoloré : fond bleu nuit, voirie turquoise, bâtiments en 3D
function recolorer(style) {
  const layers = [];
  for (const l of style.layers) {
    const p = l.paint ||= {};
    const id = l.id;
    delete p["fill-pattern"];   // motifs du sprite d'origine : inutiles sur fond sombre
    if (l.type === "background") p["background-color"] = C.fond;
    else if (id === "water") p["fill-color"] = C.eau;
    else if (id === "waterway") p["line-color"] = C.eau;
    else if (id === "building") {
      // empreinte au sol (vue de loin) ; les volumes sont ajoutés plus bas
      layers.push({ ...l, paint: { "fill-color": "#0b1d1f", "fill-outline-color": "rgba(95,212,196,.25)" }, maxzoom: 14 });
      continue;
    } else if (l.type === "fill" && /landuse|landcover|park|aeroway|pier/.test(id)) {
      p["fill-color"] = /park|wood/.test(id) ? "#08201f" : "#071719";
    } else if (l.type === "line" && id.startsWith("highway")) {
      const fort = /motorway/.test(id) ? .42 : /major/.test(id) ? .26 : .12;
      p["line-color"] = /casing/.test(id) ? `rgba(95,212,196,${fort / 3})` : `rgba(95,212,196,${fort})`;
    } else if (l.type === "line" && /railway|aeroway|road_pier/.test(id)) {
      p["line-color"] = /dashline/.test(id) ? C.fond : "rgba(140,210,200,.14)";
    } else if (l.type === "line" && id.startsWith("boundary")) p["line-color"] = "rgba(140,210,200,.22)";
    else if (l.type === "symbol") {
      if (!l.layout?.["text-field"]) continue;   // pas de pictogrammes : la carte reste sobre
      p["text-color"] = /place_(city|town|suburb)/.test(id) ? C.texte : C.faible;
      p["text-halo-color"] = C.fond;
      p["text-halo-width"] = 1.2;
      delete p["icon-opacity"];
      if (l.layout) delete l.layout["icon-image"];
    }
    layers.push(l);
  }
  // volumes des bâtiments : plus hauts = plus clairs, comme la référence « fil de fer »
  layers.push({
    id: "batiments-3d", type: "fill-extrusion", source: "openmaptiles", "source-layer": "building", minzoom: 12,
    filter: ["!=", ["get", "hide_3d"], true],
    paint: {
      "fill-extrusion-color": ["interpolate", ["linear"], ["coalesce", ["get", "render_height"], 10], 0, "#0b1d1f", 30, "#123033", 80, "#1b4a4c"],
      "fill-extrusion-height": ["interpolate", ["linear"], ["zoom"], 12, 0, 13.6, ["coalesce", ["get", "render_height"], 10]],
      "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
      "fill-extrusion-opacity": 0.88,
      "fill-extrusion-vertical-gradient": true,
    },
  });
  layers.push({
    id: "batiments-aretes", type: "line", source: "openmaptiles", "source-layer": "building", minzoom: 14,
    paint: { "line-color": "rgba(95,212,196,.22)", "line-width": 0.6 },
  });
  return { ...style, layers };
}

// zoom pour que le rayon de recherche tienne dans la vue inclinée
function zoomPourRayon(conteneur, rayonKm, lat) {
  const largeur = Math.min(conteneur.clientWidth, conteneur.clientHeight * 1.5) || 600;
  const metresParPixelZ0 = 156543 * Math.cos(lat * Math.PI / 180);
  return Math.log2(metresParPixelZ0 * largeur / (rayonKm * 2000 * 1.05));
}

const polygone = (anneaux, props = {}) => ({ type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: anneaux } });
const collection = features => ({ type: "FeatureCollection", features });
const VIDE = collection([]);

export async function creerCarte3d(conteneur, { surChoix } = {}) {
  if (!window.maplibregl) throw new Error("MapLibre n'a pas pu être chargé");
  if (!webglDisponible()) throw new Error("WebGL indisponible");
  const style = recolorer(await (await fetch(STYLE)).json());
  const carte = new maplibregl.Map({
    container: conteneur, style, center: PARIS, zoom: 11.6, pitch: 52, bearing: -18, maxPitch: 75,
    attributionControl: { compact: true, customAttribution: "géocodage et itinéraires © IGN Géoplateforme" },
    canvasContextAttributes: { antialias: true },
  });
  carte.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  conteneur.carte3d = carte;   // inspection depuis la console : document.querySelector(".pc-carte").carte3d
  await new Promise(ok => carte.once("load", ok));

  // ---------- calques maison ----------
  for (const id of ["zone", "mur", "faisceaux", "trajet"]) carte.addSource(id, { type: "geojson", data: VIDE });
  carte.addLayer({ id: "zone-sol", type: "fill", source: "zone", paint: { "fill-color": C.teal, "fill-opacity": 0.045 } }, "batiments-3d");
  carte.addLayer({ id: "zone-halo", type: "line", source: "zone",
                   paint: { "line-color": C.teal, "line-width": 10, "line-blur": 8, "line-opacity": .45 } });
  carte.addLayer({ id: "zone-bord", type: "line", source: "zone",
                   paint: { "line-color": "#c9f3ec", "line-width": 1.8, "line-opacity": .9, "line-dasharray": [4, 2] } });
  carte.addLayer({ id: "zone-mur", type: "fill-extrusion", source: "mur",
                   paint: { "fill-extrusion-color": C.teal, "fill-extrusion-height": ["get", "h"], "fill-extrusion-opacity": 0.22 } });
  carte.addLayer({ id: "trajet-halo", type: "line", source: "trajet", layout: { "line-cap": "round", "line-join": "round" },
                   paint: { "line-color": C.or, "line-width": 20, "line-blur": 14, "line-opacity": .55 } });
  carte.addLayer({ id: "trajet-coeur", type: "line", source: "trajet", layout: { "line-cap": "round", "line-join": "round" },
                   paint: { "line-color": C.or, "line-width": 5, "line-opacity": .95 } });
  carte.addLayer({ id: "trajet-points", type: "line", source: "trajet", layout: { "line-cap": "round", "line-join": "round" },
                   paint: { "line-color": "#fff8e1", "line-width": 3, "line-dasharray": [0, 4, 3] } });
  carte.addLayer({ id: "faisceaux", type: "fill-extrusion", source: "faisceaux",
                   paint: { "fill-extrusion-color": ["get", "couleur"], "fill-extrusion-height": ["get", "h"],
                            "fill-extrusion-opacity": 0.62 } });

  // ---------- animations : orbite lente, points qui avancent sur le trajet ----------
  // l'orbite s'arrête seule au bout de 25 s : une carte immobile ne coûte plus rien au processeur
  const ORBITE_MS = 25000;
  let orbite = false, finOrbite = 0, enVol = false, trajetVisible = false;
  const arreter = () => { orbite = false; };
  const lancerOrbite = () => { orbite = true; finOrbite = performance.now() + ORBITE_MS; };
  for (const ev of ["mousedown", "wheel", "touchstart", "dragstart"]) carte.getCanvas().addEventListener(ev, arreter, { passive: true });
  // motif de pointillés qui glisse (exemple classique MapLibre « animate a line »)
  const PAS = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0],
               [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
  // machine sans GPU (rendu logiciel) : sous ~12 images/s, plus d'orbite ni de pointillés animés
  let econome = false, lentes = 0;
  let pas = -1, dernier = 0, avant = 0;
  (function animer(t) {
    const brut = t - avant; avant = t;
    if (!econome && (orbite || enVol || trajetVisible) && document.visibilityState === "visible") {
      lentes = brut > 85 ? lentes + 1 : Math.max(0, lentes - 1);
      if (lentes > 40) { econome = true; orbite = false; console.info("carte 3D : rendu lent, animations continues coupées"); }
    }
    if (econome) { requestAnimationFrame(animer); return; }
    const dt = Math.min(brut, 100);   // orbite au temps écoulé : même vitesse quel que soit le débit d'images
    if (orbite && t > finOrbite) orbite = false;
    if (orbite && !enVol && !reduit && document.visibilityState === "visible") carte.setBearing(carte.getBearing() + dt * 0.0012);
    if (trajetVisible && t - dernier > 60) {
      dernier = t; pas = (pas + 1) % PAS.length;
      carte.setPaintProperty("trajet-points", "line-dasharray", PAS[pas]);
    }
    requestAnimationFrame(animer);
  })(0);

  // un vol en interrompt un autre : seule la fin du dernier vol relance l'orbite
  // (sinon setBearing, appelé par l'orbite, couperait le vol en cours)
  let vol = 0;
  function voler(options, { puisOrbite = true } = {}) {
    const moi = ++vol;
    enVol = true; orbite = false;
    carte.flyTo({ ...options, duration: reduit ? 0 : options.duration ?? 2500, essential: true });
    const fin = () => { if (moi === vol) { enVol = false; if (puisOrbite) lancerOrbite(); } };
    if (carte.isMoving()) carte.once("moveend", fin); else fin();
  }

  // ---------- marqueurs HTML (étiquettes HUD) ----------
  let marqueurPersonne = null, position = null, rayon = 5;
  const marqueurs = new Map();
  let liste = [], choisi = null, categorie = null;

  function elementPersonne(libelle) {
    const el = document.createElement("div");
    el.className = "m3d personne";
    el.style.zIndex = 10;   // toujours au-dessus des étiquettes des centres
    el.innerHTML = `<div class="m3d-etiquette"><small>Lieu déclaré</small><b></b></div><div class="m3d-tige"></div>
      <div class="m3d-radar"><i></i><i></i><i></i></div><div class="m3d-pastille"></div>`;
    el.querySelector("b").textContent = libelle;
    return el;
  }

  // étiquette HUD ; pour le centre choisi, elle fait office de fiche détaillée (pas de popup qui masquerait le trajet)
  function etiquetteHeb(h, detail, route) {
    const qui = categorie ? ` ${CATEGORIES[categorie].toLowerCase()}` : "";
    const places = h.disponibles > 0 ? `${h.disponibles} place${h.disponibles > 1 ? "s" : ""}${qui}` : `complet${qui}`;
    const trajet = !detail ? "" : route
      ? `<span class="m3d-trajet">${route.volOiseau ? "≈ " : ""}${formatDuree(route.dureeS)} à pied · ${formatDistance(route.distanceM / 1000)}${route.volOiseau ? " (vol d'oiseau)" : ""}</span>`
      : `<span class="m3d-trajet">calcul du trajet…</span>`;
    const parCategorie = Object.entries(h.places || {})
      .map(([c, p]) => `${CATEGORIES[c]} : ${p.disponibles}/${p.total}`).join("<br>");
    const fiche = detail ? `<span class="m3d-detail">${esc(h.type)}${h.accessible ? " · ♿ PMR" : ""}<br>${esc(h.address)}</span>
      <span class="m3d-detail m3d-categories">${parCategorie}</span>` : "";
    return `<small>Hébergement · ${formatDistance(h.distance)}</small><b>${esc(h.name)}</b><span>${places} / ${h.capacite}</span>${fiche}${trajet}`;
  }

  function dessinerFaisceaux() {
    const f = [];
    if (position) f.push(polygone([cercle(position, 0.022, 24)], { couleur: C.personne, h: 420 }));
    for (const h of liste) {
      const libre = h.disponibles > 0;
      f.push(polygone([cercle({ lat: h.latitude, lon: h.longitude }, h.id === choisi ? 0.022 : 0.016, 16)], {
        couleur: h.id === choisi ? C.or : libre ? C.libre : C.complet,
        h: libre ? 110 + 22 * h.disponibles : 55,
      }));
    }
    carte.getSource("faisceaux").setData(collection(f));
  }

  function dessinerZone() {
    if (!position) { carte.getSource("zone").setData(VIDE); carte.getSource("mur").setData(VIDE); return; }
    const exterieur = cercle(position, rayon, 128);
    carte.getSource("zone").setData(polygone([exterieur]));
    // anneau mince extrudé : un « mur » holographique qui marque la limite du rayon
    carte.getSource("mur").setData(polygone([exterieur, cercle(position, rayon * 0.994, 128).reverse()], { h: 40 + rayon * 22 }));
  }

  return {
    carte,

    // aucune localisation : vue d'ensemble de Paris qui tourne doucement derrière l'œil
    attente() {
      position = null; liste = []; choisi = null;
      marqueurPersonne?.remove(); marqueurPersonne = null;
      for (const m of marqueurs.values()) m.remove();
      marqueurs.clear();
      this.trajet(null);
      dessinerZone(); dessinerFaisceaux();
      voler({ center: PARIS, zoom: 11.6, pitch: 52, bearing: carte.getBearing() - 30, duration: 2000 });
    },

    // nouvelle position, en deux temps : plongée en gros plan 3D sur la personne, puis recul jusqu'au rayon
    placer(pos, rayonKm, libelle) {
      const premiere = !position;
      position = pos; rayon = rayonKm;
      if (!marqueurPersonne) marqueurPersonne = new maplibregl.Marker({ element: elementPersonne(libelle), anchor: "bottom" });
      marqueurPersonne.getElement().querySelector("b").textContent = libelle;
      marqueurPersonne.setLngLat([pos.lon, pos.lat]).addTo(carte);
      dessinerZone(); dessinerFaisceaux();
      const vueRayon = () => ({ center: [pos.lon, pos.lat], zoom: zoomPourRayon(conteneur, rayon, pos.lat), pitch: 55, duration: 2600 });
      if (reduit) return voler(vueRayon());
      const moi = vol + 1;
      voler({ center: [pos.lon, pos.lat], zoom: 15.9, pitch: 64, bearing: carte.getBearing() + (premiere ? -60 : -30),
              duration: premiere ? 4200 : 2800, curve: 1.5 });
      carte.once("moveend", () => setTimeout(() => {
        if (vol === moi && position === pos) voler({ ...vueRayon(), bearing: carte.getBearing() - 25 });
      }, 1800));
    },

    rayon(rayonKm) {
      rayon = rayonKm; dessinerZone();
      if (position) voler({ center: [position.lon, position.lat], zoom: zoomPourRayon(conteneur, rayonKm, position.lat), duration: 1400 });
    },

    // centres du rayon, triés par distance : les trois plus proches et le centre choisi ont leur étiquette
    hebergements(nouvelle, route, cat = null) {
      liste = nouvelle; categorie = cat;
      const ids = new Set(liste.map(h => h.id));
      const etiquetes = new Set([liste[0]?.id, ...liste.filter(h => h.disponibles > 0).slice(0, 2).map(h => h.id)].filter(Boolean));
      for (const [id, m] of marqueurs) if (!ids.has(id)) { m.remove(); marqueurs.delete(id); }
      liste.forEach((h, i) => {
        let m = marqueurs.get(h.id);
        if (!m) {
          const el = document.createElement("div");
          el.innerHTML = `<div class="m3d-etiquette"></div><div class="m3d-tige"></div><div class="m3d-pastille"></div>`;
          el.addEventListener("click", ev => { ev.stopPropagation(); surChoix?.(h.id); });
          m = new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat([h.longitude, h.latitude]).addTo(carte);
          marqueurs.set(h.id, m);
        }
        const el = m.getElement();
        // étiquettes permanentes : les deux centres avec places les plus proches, le plus proche, et le centre choisi
        const etiquetee = h.id === choisi || etiquetes.has(h.id);
        el.classList.add("m3d", "heb");   // classList : MapLibre garde ses propres classes de positionnement
        el.classList.toggle("libre", h.disponibles > 0);
        el.classList.toggle("complet", h.disponibles === 0);
        el.classList.toggle("etiquetee", etiquetee);
        el.classList.toggle("choisi", h.id === choisi);
        el.style.setProperty("--tige", `${[48, 88, 128][[...etiquetes].indexOf(h.id) % 3] ?? 60}px`);
        el.querySelector(".m3d-etiquette").innerHTML = etiquetteHeb(h, h.id === choisi, route);
      });
      dessinerFaisceaux();
    },

    choisir(id) { choisi = id; },

    // centre choisi : la caméra le cadre avec la personne (sa fiche détaillée s'affiche dans l'étiquette)
    cadrerChoix(h) {
      if (!marqueurs.has(h.id)) return;
      const b = new maplibregl.LngLatBounds([position.lon, position.lat], [position.lon, position.lat]).extend([h.longitude, h.latitude]);
      const cam = carte.cameraForBounds(b, { padding: { top: 150, bottom: 70, left: 280, right: 240 }, maxZoom: 16.2 });
      voler({ ...cam, pitch: 60, bearing: carte.getBearing(), duration: 1800 }, { puisOrbite: false });
    },

    trajet(route) {
      trajetVisible = !!route;
      carte.getSource("trajet").setData(route
        ? { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: route.coordonnees } } : VIDE);
    },

    redimensionner() { carte.resize(); },
  };
}
