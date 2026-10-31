// Lichtrahmen und Klickwelle im Stimmen-Rand: Wenn WAMO an diesem Laptop handelt, liegt ein roter Rahmen um das Zielfenster,
// bei Klicks läuft eine Welle vom Klickpunkt aus. Beides ist nicht anklickbar (das Rand-Fenster ignoriert die Maus, die
// Elemente zusätzlich pointer-events: none), WMC-Rot (#F40E0E), und verschwindet kurz nach der Aktion von selbst.
// main.js hat die Werte geprüft und auf dieses Fenster umgerechnet; hier wird nur gezeichnet.
//
// * Rahmen: bleibt HALTEN_MS stehen und blendet dann in ABBLENDEN_MS aus; eine neue Markierung verlängert ihn.
// * Welle: ein einzelner Ring, WELLE_MS lang (höchstens ein Puls je Klick, nie ein Blitzen: WCAG 2.3.1).
// * Bei reduzierter Bewegung (prefers-reduced-motion): kein Einblenden, kein Ausblenden, keine wachsende Welle. Der
//   Rahmen steht still, die Welle ist ein ruhender Ring am Klickpunkt, beides verschwindet nach der gleichen Zeit.
// * Bei offener Freigabe oder ohne Gespräch (Zustand approval oder offline) verschwindet alles sofort.
(function () {
  "use strict";

  const HALTEN_MS = 1500;
  const ABBLENDEN_MS = 350;
  const WELLE_MS = 800;
  const MIN_KANTE = 8; // kleinere Rechtecke zeichnet niemand

  /** Rechteck in die Fenstergröße einpassen; null, wenn es ungültig ist oder (fast) nichts übrig bleibt. */
  function rahmenGeometrie(f, breite, hoehe) {
    if (!f || ![f.x, f.y, f.b, f.h, breite, hoehe].every(Number.isFinite)) return null;
    const x = Math.max(0, f.x);
    const y = Math.max(0, f.y);
    const rechts = Math.min(breite, f.x + f.b);
    const unten = Math.min(hoehe, f.y + f.h);
    if (rechts - x < MIN_KANTE || unten - y < MIN_KANTE) return null;
    return { x, y, b: rechts - x, h: unten - y };
  }

  /** Klickpunkt, nur wenn er im Fenster liegt. */
  function welleGeometrie(p, breite, hoehe) {
    if (!p || ![p.x, p.y, breite, hoehe].every(Number.isFinite)) return null;
    return p.x >= 0 && p.y >= 0 && p.x < breite && p.y < hoehe ? { x: p.x, y: p.y } : null;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { rahmenGeometrie, welleGeometrie, HALTEN_MS, ABBLENDEN_MS, WELLE_MS };
  }
  if (typeof document === "undefined" || !window.overlay || !window.overlay.onMarkierung) return;

  const reduziert = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const rahmen = document.getElementById("rahmen");
  const welle = document.getElementById("welle");
  if (!rahmen || !welle) return;
  let rahmenTimer = 0;
  let ausTimer = 0;
  let welleTimer = 0;

  function rahmenWeg(sofort) {
    clearTimeout(rahmenTimer);
    clearTimeout(ausTimer);
    rahmen.classList.remove("an");
    if (sofort || reduziert) rahmen.hidden = true;
    else ausTimer = setTimeout(() => (rahmen.hidden = true), ABBLENDEN_MS);
  }

  function welleWeg() {
    clearTimeout(welleTimer);
    welle.classList.remove("an");
    welle.hidden = true;
  }

  function alleWeg() {
    rahmenWeg(true);
    welleWeg();
  }

  window.overlay.onMarkierung((m) => {
    const f = m && rahmenGeometrie(m.fenster, window.innerWidth, window.innerHeight);
    if (!f) return;
    clearTimeout(rahmenTimer);
    clearTimeout(ausTimer);
    Object.assign(rahmen.style, { left: `${f.x}px`, top: `${f.y}px`, width: `${f.b}px`, height: `${f.h}px` });
    rahmen.hidden = false;
    void rahmen.offsetWidth; // Übergang neu starten
    rahmen.classList.add("an");
    rahmenTimer = setTimeout(() => rahmenWeg(false), HALTEN_MS);

    const p = m.art === "klick" ? welleGeometrie(m.punkt, window.innerWidth, window.innerHeight) : null;
    if (p) {
      welleWeg();
      Object.assign(welle.style, { left: `${p.x}px`, top: `${p.y}px` });
      welle.hidden = false;
      void welle.offsetWidth;
      welle.classList.add("an");
      welleTimer = setTimeout(welleWeg, WELLE_MS);
    }
  });

  // Offene Freigabe oder kein Gespräch: nichts zeigen (die Zustände kommen mit den Pegeln wie bei overlay.js)
  window.overlay.onLevels((s) => {
    if (s && (s.zustand === "approval" || s.zustand === "offline")) alleWeg();
  });
})();
