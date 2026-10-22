// Logique géographique du panneau carte : géocodage IGN, météo Open-Meteo, hébergements proches.
// Aucun accès au DOM ici : panneau.mjs s'occupe de l'affichage, les tests Node importent ce module.
// Seul le texte du lieu (localisation, commune) ou des coordonnées partent vers les API : jamais de nom, d'âge ni de récit.

const IGN = "https://data.geopf.fr/geocodage/search";
const OPEN_METEO = "https://api.open-meteo.com/v1/forecast";
const IGN_ITINERAIRE = "https://data.geopf.fr/navigation/itineraire";
const METEO_TTL_MS = 10 * 60 * 1000;
const DELAI_RESEAU_MS = 8000;

// mots de liaison ignorés pour comparer le lieu dit et les résultats (la fiche est souvent en anglais)
const MOTS_VIDES = new Set(("the a an of in at on to by near next front outside inside around close behind " +
  "opposite across from just currently right now i im am is station train metro area street " +
  "de la le les du des d l au aux a en pres devant derriere cote vers dans sur").split(" "));

export function normaliser(texte) {
  return String(texte ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

const mots = texte => normaliser(texte).split(" ").filter(m => m && !MOTS_VIDES.has(m));

// texte envoyé à l'IGN : les tournures anglaises (« near the », « outside ») le perturbent
export function requeteLieu(localisation, commune) {
  const lieu = String(localisation ?? "")
    .replace(/\b(i'?m|i am|currently|right now|near(by)?|next to|in front of|outside( of)?|around|close to|behind|opposite|at|the)\b/gi, " ")
    .replace(/\b(train )?station\b/gi, " ")
    .replace(/\s+/g, " ").replace(/^[\s,]+|[\s,]+$/g, "");
  const ville = String(commune ?? "").trim();
  if (!lieu) return ville;
  return ville && !normaliser(lieu).includes(normaliser(ville)) ? `${lieu}, ${ville}` : lieu;
}

async function getJson(url, signal) {
  const delai = AbortSignal.timeout(DELAI_RESEAU_MS);
  const r = await fetch(url, { signal: signal ? AbortSignal.any([signal, delai]) : delai });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// ---------- géocodage ----------

// un résultat IGN → candidat commun aux index « address » et « poi » (GeoJSON : [lon, lat])
function candidat(f) {
  const p = f.properties || {};
  const [lon, lat] = f.geometry.coordinates;
  const poi = p._type === "poi";
  const villes = [].concat(p.city || []);
  return {
    lat, lon, poi, type: poi ? (p.category || [])[0] : p.type,
    nom: poi ? p.toponym : p.name,
    libelle: poi ? [p.toponym, villes[0]].filter(Boolean).join(", ") : p.label,
    villes, scoreIgn: p.score || 0,
  };
}

// note sur 1 : les mots du résultat présents dans ce qui a été dit, la ville, puis la note IGN
export function noter(c, motsRequete, motsVille) {
  const motsNom = mots(c.nom);
  const couverture = motsNom.length ? motsNom.filter(m => motsRequete.has(m)).length / motsNom.length : 0;
  const villeOk = !motsVille.length ? 0.5
    : c.villes.some(v => mots(v).some(m => motsVille.includes(m))) ? 1 : 0;
  // un numéro dit (« 15 rue … ») : la rue seule n'est qu'une approximation
  const numeroManque = [...motsRequete].some(m => /^\d{1,4}$/.test(m)) && c.type !== "housenumber" ? 0.15 : 0;
  return 0.55 * couverture + 0.25 * Math.min(c.scoreIgn, 1) + 0.2 * villeOk - numeroManque;
}

export function distanceKm(a, b) {
  const R = 6371, rad = d => d * Math.PI / 180;
  const dLat = rad(b.lat - a.lat), dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

// classe les candidats, puis qualifie le meilleur : précis, approximatif, ou ambigu (autre lieu crédible loin)
// reference : dernière position connue (ou centre du territoire) pour départager des homonymes lointains
export function choisir(candidats, requete, commune, reference) {
  const motsRequete = new Set(mots(requete));
  // ville : la commune de la fiche, sinon les mots du texte (« … Paris »)
  const motsVille = commune ? mots(commune) : [...motsRequete];
  const loin = c => reference && distanceKm(reference, c) > 50 ? 0.05 : 0;
  const notes = candidats.map(c => ({ ...c, note: noter(c, motsRequete, motsVille) - loin(c) }))
    .sort((a, b) => b.note - a.note);
  const meilleur = notes[0];
  if (!meilleur || meilleur.note < 0.45) return null;
  const autres = [];
  for (const c of notes.slice(1)) {
    if (meilleur.note - c.note > 0.12 || autres.length >= 3) break;
    if ([meilleur, ...autres].every(x => distanceKm(x, c) > 1.5)) autres.push(c);
  }
  const precis = meilleur.note >= 0.75;
  const motsVilles = [...(commune ? mots(commune) : []), ...meilleur.villes.flatMap(mots)];
  const villeSeule = meilleur.type === "municipality" || mots(meilleur.nom).every(m => motsVilles.includes(m));
  return {
    lat: meilleur.lat, lon: meilleur.lon, libelle: meilleur.libelle, requete,
    // seule la commune est reconnue (« Paris ») : elle ne situe pas la personne, même sans homonyme
    qualite: villeSeule ? "approximative" : autres.length ? "ambigue" : precis ? "precise" : "approximative",
    alternatives: autres.map(({ lat, lon, libelle }) => ({ lat, lon, libelle })),
  };
}

const cacheGeocodage = new Map();

// renvoie {lat, lon, libelle, qualite, alternatives} ou null si rien de crédible ; lève en cas de panne réseau
export async function geocoder(localisation, commune, { signal, reference } = {}) {
  const requete = requeteLieu(localisation, commune);
  if (!requete) return null;
  const cle = normaliser(requete);
  if (cacheGeocodage.has(cle)) return cacheGeocodage.get(cle);
  const proche = reference ? { lat: reference.lat.toFixed(4), lon: reference.lon.toFixed(4) } : {};
  const url = index => `${IGN}?${new URLSearchParams({ q: requete, index, limit: "10", ...proche })}`;
  const reponses = await Promise.allSettled([getJson(url("address"), signal), getJson(url("poi"), signal)]);
  if (reponses.every(r => r.status === "rejected")) throw reponses[0].reason;
  const candidats = reponses.flatMap(r => r.status === "fulfilled" ? (r.value.features || []) : []).map(candidat);
  const resultat = choisir(candidats, requete, commune, reference);
  cacheGeocodage.set(cle, resultat);
  return resultat;
}

// ---------- météo ----------

// codes WMO d'Open-Meteo → libellé et icône
const WMO = [
  [[0], "ciel dégagé", "☀️"], [[1], "peu nuageux", "🌤️"], [[2], "partiellement nuageux", "⛅"],
  [[3], "couvert", "☁️"], [[45, 48], "brouillard", "🌫️"], [[51, 53, 55], "bruine", "🌦️"],
  [[56, 57], "bruine verglaçante", "🌧️"], [[61, 63, 65], "pluie", "🌧️"], [[66, 67], "pluie verglaçante", "🧊"],
  [[71, 73, 75, 77], "neige", "🌨️"], [[80, 81, 82], "averses", "🌦️"], [[85, 86], "averses de neige", "🌨️"],
  [[95], "orage", "⛈️"], [[96, 99], "orage avec grêle", "⛈️"],
];
export function conditions(code) {
  const t = WMO.find(([codes]) => codes.includes(code));
  return t ? { libelle: t[1], icone: t[2] } : { libelle: "inconnu", icone: "❔" };
}

// réponse Open-Meteo → ce que la carte météo affiche (les 8 prochaines heures)
export function lireMeteo(d) {
  const c = d.current, h = d.hourly;
  const heureCourante = c.time.slice(0, 13);
  const debut = Math.max(0, h.time.findIndex(t => t.slice(0, 13) >= heureCourante));
  return {
    temperature: c.temperature_2m, ressenti: c.apparent_temperature, humidite: c.relative_humidity_2m,
    precipitation: c.precipitation, vent: c.wind_speed_10m, ...conditions(c.weather_code), heure: c.time,
    prevision: h.time.slice(debut + 1, debut + 9).map((t, i) => ({
      heure: t.slice(11, 16), temperature: h.temperature_2m[debut + 1 + i],
      pluie: h.precipitation_probability[debut + 1 + i],
    })),
    simulee: false,
  };
}

const cacheMeteo = new Map();

export async function meteo(lat, lon, signal) {
  const cle = `${lat.toFixed(2)},${lon.toFixed(2)}`;   // ~1 km : inutile de redemander pour quelques mètres
  const enCache = cacheMeteo.get(cle);
  if (enCache && Date.now() - enCache.t < METEO_TTL_MS) return enCache.meteo;
  const params = new URLSearchParams({
    latitude: lat.toFixed(4), longitude: lon.toFixed(4),
    current: "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
    hourly: "temperature_2m,precipitation_probability", forecast_days: "2", timezone: "Europe/Paris",
  });
  const m = lireMeteo(await getJson(`${OPEN_METEO}?${params}`, signal));
  cacheMeteo.set(cle, { t: Date.now(), meteo: m });
  return m;
}

// mode démo uniquement, si Open-Meteo ne répond pas : valeurs fixes, signalées comme simulées à l'écran
export function meteoSimulee() {
  const h = new Date().getHours();
  return {
    temperature: 4, ressenti: 1, humidite: 88, precipitation: 0.6, vent: 18, ...conditions(61), heure: null,
    prevision: Array.from({ length: 8 }, (_, i) => ({
      heure: `${String((h + i + 1) % 24).padStart(2, "0")}:00`, temperature: 4 - Math.min(i, 3), pluie: 70 - i * 5,
    })),
    simulee: true,
  };
}

// ---------- hébergements ----------

// à vol d'oiseau, dans le rayon, du plus proche au plus éloigné
export function hebergementsProches(position, hebergements, rayonKm) {
  return hebergements
    .map(h => ({ ...h, distance: distanceKm(position, { lat: h.latitude, lon: h.longitude }) }))
    .filter(h => h.distance <= rayonKm)
    .sort((a, b) => a.distance - b.distance);
}

export const formatDistance = km => km < 1 ? `${Math.round(km * 1000 / 10) * 10} m` : `${km.toFixed(1).replace(".", ",")} km`;

// ---------- itinéraire à pied (affiché seulement quand l'écoutant choisit un centre) ----------

const cacheItineraire = new Map();

// renvoie {coordonnees: [[lon, lat], …], distanceM, dureeS} ; lève si le service ne répond pas
export async function itineraire(depart, arrivee, signal) {
  const pt = p => `${p.lon.toFixed(5)},${p.lat.toFixed(5)}`;
  const cle = `${pt(depart)}>${pt(arrivee)}`;
  if (cacheItineraire.has(cle)) return cacheItineraire.get(cle);
  const params = new URLSearchParams({
    resource: "bdtopo-osrm", profile: "pedestrian", optimization: "fastest",
    start: pt(depart), end: pt(arrivee), geometryFormat: "geojson", distanceUnit: "meter", timeUnit: "second",
  });
  const r = await getJson(`${IGN_ITINERAIRE}?${params}`, signal);
  if (!r.geometry?.coordinates?.length) throw new Error("itinéraire vide");
  const resultat = { coordonnees: r.geometry.coordinates, distanceM: r.distance, dureeS: r.duration };
  cacheItineraire.set(cle, resultat);
  return resultat;
}

// ligne droite de secours, à environ 4,5 km/h
export function ligneDroite(depart, arrivee) {
  const distanceM = distanceKm(depart, arrivee) * 1000;
  return { coordonnees: [[depart.lon, depart.lat], [arrivee.lon, arrivee.lat]], distanceM, dureeS: distanceM / 1.25, volOiseau: true };
}

export const formatDuree = s => s < 3600 ? `${Math.max(1, Math.round(s / 60))} min` : `${Math.floor(s / 3600)} h ${String(Math.round(s % 3600 / 60)).padStart(2, "0")}`;

// ---------- géométries ----------

// anneau d'un cercle de rayon donné (km), en [lon, lat], fermé : rayon de recherche, faisceaux lumineux
export function cercle(centre, rayonKm, points = 64) {
  const R = 6371, d = rayonKm / R, rad = x => x * Math.PI / 180, deg = x => x * 180 / Math.PI;
  const lat1 = rad(centre.lat), lon1 = rad(centre.lon);
  const anneau = [];
  for (let i = 0; i <= points; i++) {
    const cap = 2 * Math.PI * (i % points) / points;
    const lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(cap));
    const lon2 = lon1 + Math.atan2(Math.sin(cap) * Math.sin(d) * Math.cos(lat1), Math.cos(d) - Math.sin(lat1) * Math.sin(lat2));
    anneau.push([deg(lon2), deg(lat2)]);
  }
  return anneau;
}
