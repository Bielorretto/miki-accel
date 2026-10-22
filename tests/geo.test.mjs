// Tests de static/carte/geo.mjs, sans réseau :  node --test tests/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  CATEGORIES, categorieDeFiche, cercle, choisir, distanceKm, formatDistance, formatDuree, hebergementsProches, ligneDroite,
  places, requeteLieu,
} from "../static/carte/geo.mjs";

const HEBERGEMENTS = JSON.parse(readFileSync(new URL("../static/carte/demo_hebergements.json", import.meta.url)));
const GARE_DE_LYON = { lat: 48.8443, lon: 2.3744 };

test("Haversine : distances connues", () => {
  assert.equal(distanceKm(GARE_DE_LYON, GARE_DE_LYON), 0);
  // Paris (Notre-Dame) → Lyon (Bellecour) : environ 392 km à vol d'oiseau
  const d = distanceKm({ lat: 48.853, lon: 2.3499 }, { lat: 45.7578, lon: 4.832 });
  assert.ok(Math.abs(d - 392) < 3, d);
  // un degré de latitude ≈ 111,2 km
  assert.ok(Math.abs(distanceKm({ lat: 48, lon: 2 }, { lat: 49, lon: 2 }) - 111.19) < 0.1);
});

test("hébergements : filtrés par rayon et triés par distance", () => {
  for (const rayon of [2, 5, 10]) {
    const liste = hebergementsProches(GARE_DE_LYON, HEBERGEMENTS, rayon);
    assert.ok(liste.every(h => h.distance <= rayon));
    assert.deepEqual(liste.map(h => h.distance), [...liste.map(h => h.distance)].sort((a, b) => a - b));
  }
  const deux = hebergementsProches(GARE_DE_LYON, HEBERGEMENTS, 2), dix = hebergementsProches(GARE_DE_LYON, HEBERGEMENTS, 10);
  assert.ok(deux.length > 0 && deux.length < dix.length);
  assert.equal(deux[0].id, "shelter-002");   // Quai de la Rapée, le plus proche de la gare de Lyon
});

test("jeu d'hébergements : complet, marqué fictif dans les données, places par catégorie cohérentes", () => {
  assert.ok(HEBERGEMENTS.length >= 15);
  assert.equal(new Set(HEBERGEMENTS.map(h => h.id)).size, HEBERGEMENTS.length);
  for (const h of HEBERGEMENTS) {
    assert.equal(h.isDemo, true);
    const cats = Object.keys(h.places);
    assert.ok(cats.length > 0 && cats.every(c => c in CATEGORIES), h.id);
    for (const p of Object.values(h.places)) assert.ok(p.disponibles >= 0 && p.disponibles <= p.total, h.id);
    assert.ok(distanceKm({ lat: 48.8566, lon: 2.3522 }, { lat: h.latitude, lon: h.longitude }) < 8);
  }
  for (const c of Object.keys(CATEGORIES)) {
    const avec = HEBERGEMENTS.filter(h => h.places[c]);
    assert.ok(avec.some(h => h.places[c].disponibles > 0) && avec.some(h => h.places[c].disponibles === 0), c);
  }
});

test("places : total ou par catégorie, filtre des centres par catégorie", () => {
  const bastille = HEBERGEMENTS.find(h => h.id === "shelter-001");
  assert.deepEqual(places(bastille, null), { total: 40, disponibles: 3 });
  assert.deepEqual(places(bastille, "femmes"), { total: 10, disponibles: 2 });
  assert.deepEqual(places(bastille, "hommes"), { total: 18, disponibles: 0 });
  const familles = hebergementsProches(GARE_DE_LYON, HEBERGEMENTS, 10, "familles");
  assert.ok(familles.length > 0 && familles.every(h => h.places.familles));
  assert.ok(familles.every(h => h.disponibles === h.places.familles.disponibles && h.capacite === h.places.familles.total));
  assert.ok(!familles.some(h => h.id === "shelter-002"));   // halte de nuit hommes seuls
});

test("catégorie déduite de la fiche : enfants, puis sexe", () => {
  assert.equal(categorieDeFiche({ appelant: { sexe: "femme" }, cas_particuliers: { nb_enfants: 2 } }), "familles");
  assert.equal(categorieDeFiche({ appelant: { sexe: "homme" }, criteres: { enfants_presents: true } }), "familles");
  assert.equal(categorieDeFiche({ appelant: { sexe: "femme" }, cas_particuliers: { nb_enfants: null } }), "femmes");
  assert.equal(categorieDeFiche({ appelant: { sexe: "homme" } }), "hommes");
  assert.equal(categorieDeFiche({ appelant: {} }), null);
  assert.equal(categorieDeFiche(null), null);
});

test("requête de lieu : tournures anglaises retirées, commune ajoutée une seule fois", () => {
  assert.equal(requeteLieu("near the Gare de Lyon train station", "Paris"), "Gare de Lyon, Paris");
  assert.equal(requeteLieu("Gare du Nord, Paris", "Paris"), "Gare du Nord, Paris");
  assert.equal(requeteLieu("près de la gare de Lyon", "Paris"), "gare de Lyon, Paris");
  assert.equal(requeteLieu("devant la gare du Nord", "Paris"), "gare du Nord, Paris");
  assert.equal(requeteLieu("Je suis à côté de l'Hôtel de Ville", "Paris"), "Hôtel de Ville, Paris");
  assert.equal(requeteLieu("place de la République", "Paris"), "place de la République, Paris");
  assert.equal(requeteLieu("Le Mans, place de la République", null), "Le Mans, place de la République");
  assert.equal(requeteLieu(null, "Paris"), "Paris");
  assert.equal(requeteLieu(null, null), "");
});

// réponses IGN réduites à l'essentiel (GeoJSON : coordonnées [lon, lat])
const poi = (nom, villes, lon, lat, score) => ({ lat, lon, poi: true, type: "gare", nom, libelle: nom, villes, scoreIgn: score });
const adresse = (nom, ville, type, lon, lat, score) => ({ lat, lon, poi: false, type, nom, libelle: `${nom} ${ville}`, villes: [ville], scoreIgn: score });

test("géocodage : la gare de Lyon à Paris plutôt que la rue de Lyon ou Lyon Part-Dieu", () => {
  const r = choisir([
    adresse("Rue de Lyon", "Paris", "street", 2.3708, 48.8498, 0.67),
    poi("Gare de la Part-Dieu", ["Lyon"], 4.8604, 45.7605, 0.38),
    poi("Gare de Lyon", ["Paris 12e Arrondissement", "Paris"], 2.3747, 48.8442, 0.37),
  ], "Gare de Lyon, Paris", null);
  assert.equal(r.lat, 48.8442);
  assert.equal(r.lon, 2.3747);
  assert.equal(r.qualite, "precise");
});

test("géocodage : homonymes lointains signalés comme ambigus, avec alternatives", () => {
  const r = choisir([
    adresse("Place de la République", "Le Mans", "street", 0.1966, 48.004, 0.97),
    adresse("Place de la République", "Paris", "street", 2.3654, 48.8668, 0.97),
  ], "Place de la République", null);
  assert.equal(r.qualite, "ambigue");
  assert.equal(r.alternatives.length, 1);
  // avec une position de référence (Paris), le candidat parisien passe devant
  const r2 = choisir([
    adresse("Place de la République", "Le Mans", "street", 0.1966, 48.004, 0.97),
    adresse("Place de la République", "Paris", "street", 2.3654, 48.8668, 0.97),
  ], "Place de la République", null, { lat: 48.8566, lon: 2.3522 });
  assert.equal(r2.lat, 48.8668);
});

test("géocodage : numéro de rue préféré à la rue seule, rien de crédible → null", () => {
  const r = choisir([
    adresse("Rue de Rivoli", "Paris", "street", 2.3362, 48.8624, 0.76),
    adresse("15 Rue de Rivoli", "Paris", "housenumber", 2.3577, 48.8559, 0.97),
  ], "15 rue de Rivoli, Paris", null);
  assert.equal(r.lat, 48.8559);
  assert.equal(r.qualite, "precise");
  assert.equal(choisir([adresse("Rue Elie Paris", "Lyon", "street", 4.87, 45.75, 0.3)], "blabla xyz", null), null);
  assert.equal(choisir([], "Gare de Lyon", null), null);
});

test("distances affichées : mètres sous 1 km, sinon kilomètres", () => {
  assert.equal(formatDistance(0.734), "730 m");
  assert.equal(formatDistance(2.46), "2,5 km");
});

test("géocodage : seule la commune est reconnue → approximatif", () => {
  const r = choisir([poi("Paris", ["Paris"], 2.347, 48.859, 0.9)], "blabla xyz, Paris", "Paris");
  assert.equal(r.qualite, "approximative");
  const r2 = choisir([adresse("Paris", "Paris", "municipality", 2.347, 48.859, 0.95)], "Paris", null);
  assert.equal(r2.qualite, "approximative");
});

test("cercle : fermé, tous les points au rayon demandé", () => {
  const anneau = cercle(GARE_DE_LYON, 5, 32);
  assert.equal(anneau.length, 33);
  assert.deepEqual(anneau[0], anneau[32]);
  for (const [lon, lat] of anneau) assert.ok(Math.abs(distanceKm(GARE_DE_LYON, { lat, lon }) - 5) < 0.01);
});

test("itinéraire de secours : ligne droite, durée à pied plausible", () => {
  const bastille = { lat: 48.8532, lon: 2.3691 };
  const l = ligneDroite(GARE_DE_LYON, bastille);
  assert.equal(l.coordonnees.length, 2);
  assert.equal(l.volOiseau, true);
  assert.ok(Math.abs(l.distanceM - distanceKm(GARE_DE_LYON, bastille) * 1000) < 1);
  assert.equal(formatDuree(l.dureeS), `${Math.round(l.distanceM / 1.25 / 60)} min`);
  assert.equal(formatDuree(30), "1 min");
  assert.equal(formatDuree(3900), "1 h 05");
});
