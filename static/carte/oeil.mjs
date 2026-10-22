// L'œil MIKI de la page DIHAL (static/dihal.html), en composant : clignement et pupille qui suit la souris.
// Affiché sur la carte tant que la localisation de l'appelant n'est pas connue.

let numero = 0;

export function creerOeil(conteneur) {
  const n = ++numero;   // identifiants SVG uniques si plusieurs yeux dans la page
  conteneur.innerHTML = `
    <svg class="oeil" viewBox="0 0 200 160" aria-hidden="true">
      <defs>
        <clipPath id="oeil-ouverture-${n}"><path data-o="clip" d=""/></clipPath>
        <linearGradient id="oeil-degrade-${n}" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="150">
          <stop offset="0" stop-color="#2a8f86"/><stop offset=".55" stop-color="#5fd4c4"/><stop offset="1" stop-color="#c9f3ec"/>
        </linearGradient>
        <radialGradient id="oeil-iris-${n}" cx=".38" cy=".35" r=".7">
          <stop offset="0" stop-color="#fff1c9"/><stop offset=".45" stop-color="#f5c35b"/><stop offset="1" stop-color="#d98a12"/>
        </radialGradient>
      </defs>
      <g fill="none" stroke="url(#oeil-degrade-${n})" stroke-width="8" stroke-linecap="round">
        <path class="onde" d="M14,72 Q100,-14 186,72"/>
        <path class="onde o2" d="M38,88 Q100,26 162,88"/>
      </g>
      <g clip-path="url(#oeil-ouverture-${n})"><circle data-o="pupille" class="pupille" cx="100" cy="112" r="14" fill="url(#oeil-iris-${n})"/></g>
      <g fill="none" stroke="url(#oeil-degrade-${n})" stroke-width="8" stroke-linecap="round">
        <path data-o="haut" d=""/>
        <path d="M66,122 Q100,146 134,122"/>
      </g>
    </svg>`;
  const svg = conteneur.querySelector("svg");
  const $ = nom => svg.querySelector(`[data-o="${nom}"]`);
  const haut = $("haut"), clip = $("clip"), pupille = $("pupille");
  const OUVERT = 64, FERME = 132;
  let fermeture = 0, actif = true, minuteur = null;

  function paupiere() {
    const cy = OUVERT + (FERME - OUVERT) * fermeture;
    haut.setAttribute("d", `M56,110 Q100,${cy} 144,110`);
    clip.setAttribute("d", `M56,110 Q100,${cy} 144,110 Q100,150 56,110 Z`);
  }
  function cligner(apres) {
    const duree = 170, t0 = performance.now();
    (function pas(t) {
      const k = Math.max(0, Math.min((t - t0) / duree, 1));
      fermeture = k < 0.5 ? k * 2 : (1 - k) * 2;
      paupiere();
      if (k < 1) requestAnimationFrame(pas); else { fermeture = 0; paupiere(); apres?.(); }
    })(t0);
  }
  function programmer() {
    minuteur = setTimeout(() => {
      if (!actif) return programmer();
      if (Math.random() < 0.2) cligner(() => setTimeout(() => cligner(programmer), 120));
      else cligner(programmer);
    }, 4000 + Math.random() * 1500);
  }

  let cible = { x: 0, y: 0 }, pos = { x: 0, y: 0 };
  addEventListener("mousemove", e => {
    const r = svg.getBoundingClientRect();
    if (!r.width) return;
    const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height * 0.7);
    const dist = Math.hypot(dx, dy) || 1, force = Math.min(dist / 300, 1);
    cible = { x: dx / dist * 18 * force, y: dy / dist * 7 * force };
  });
  (function suivre() {
    if (actif) {
      pos.x += (cible.x - pos.x) * 0.15; pos.y += (cible.y - pos.y) * 0.15;
      pupille.setAttribute("cx", 100 + pos.x); pupille.setAttribute("cy", 112 + pos.y);
    }
    requestAnimationFrame(suivre);
  })();

  paupiere();
  programmer();
  return {
    // œil masqué : plus d'animation inutile
    actif(oui) { actif = oui; },
    // pendant la résolution du lieu, la pupille balaie de gauche à droite (« MIKI cherche »)
    chercher(oui) { svg.classList.toggle("cherche", oui); },
  };
}
