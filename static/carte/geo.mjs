// Logique géographique du panneau carte : géocodage et itinéraires IGN, hébergements proches.
// Aucun accès au DOM ici : panneau.mjs s'occupe de l'affichage, les tests Node importent ce module.
// Seul le texte du lieu (localisation, commune) ou des coordonnées partent vers les API : jamais de nom, d'âge ni de récit.

const IGN = "https://data.geopf.fr/geocodage/search";
const IGN_ITINERAIRE = "https://data.geopf.fr/navigation/itineraire";
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

// tournures qui entourent le lieu dit (« je suis près de la… », « near the… ») : elles perturbent l'IGN
const AUTOUR_FR = /(^|[\s,])(je suis|je me trouve|actuellement|pr[eè]s d(?:e|u|es)|devant|derri[eè]re|[àa] c[oô]t[ée] d(?:e|u|es)|en face d(?:e|u|es)|vers|autour d(?:e|u|es)|au niveau d(?:e|u|es))(?=[\s,'’]|$)/gi;
const AUTOUR_EN = /\b(i'?m|i am|currently|right now|near(by)?|next to|in front of|outside( of)?|around|close to|behind|opposite|at|the)\b/gi;

// texte envoyé à l'IGN : le lieu seul, suivi de la commune si elle n'y figure pas
export function requeteLieu(localisation, commune) {
  const brut = String(localisation ?? "");
  let lieu = brut.replace(AUTOUR_FR, "$1 ").replace(AUTOUR_EN, " ").replace(/\b(train )?station\b/gi, " ")
    .replace(/\s+/g, " ").replace(/^[\s,]+|[\s,]+$/g, "");
  // « près de la gare… » → « gare… » : l'article n'est retiré que s'il suivait une tournure
  if (lieu !== brut.trim()) lieu = lieu.replace(/^(?:la |le |les |l['’]\s*|'\s*)/i, "");
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

// ---------- hébergements ----------

// catégories de places ; chaque centre n'accueille que certaines d'entre elles
export const CATEGORIES = { femmes: "Femmes", familles: "Familles avec enfants", hommes: "Hommes" };

// places d'un centre pour une catégorie (ou toutes, si categorie est null)
export function places(h, categorie) {
  const cats = categorie ? [categorie] : Object.keys(h.places || {});
  return cats.reduce((t, c) => {
    const p = h.places?.[c];
    return p ? { total: t.total + p.total, disponibles: t.disponibles + p.disponibles } : t;
  }, { total: 0, disponibles: 0 });
}

// catégorie à présélectionner d'après la fiche : enfants → familles, puis sexe ; l'écoutant peut en changer
export function categorieDeFiche(fiche) {
  const cas = fiche?.cas_particuliers || {}, criteres = fiche?.criteres || {};
  if (cas.nb_enfants > 0 || cas.ages_enfants?.length || criteres.enfants_presents) return "familles";
  const sexe = fiche?.appelant?.sexe;
  if (sexe === "femme" || cas.grossesse || criteres.grossesse) return "femmes";
  if (sexe === "homme") return "hommes";
  return null;
}

// à vol d'oiseau, dans le rayon, du plus proche au plus éloigné ; avec une catégorie,
// seuls les centres qui l'accueillent, et les places de cette catégorie
export function hebergementsProches(position, hebergements, rayonKm, categorie = null) {
  return hebergements
    .filter(h => !categorie || h.places?.[categorie])
    .map(h => {
      const p = places(h, categorie);
      return { ...h, distance: distanceKm(position, { lat: h.latitude, lon: h.longitude }), disponibles: p.disponibles, capacite: p.total };
    })
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
