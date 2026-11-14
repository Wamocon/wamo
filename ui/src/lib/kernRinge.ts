/**
 * Zahlen und Rechnungen für die HUD-Ringe um den Kern (components/kommando/KernRinge.tsx).
 * Die Zeichnung ist ein SVG mit der Kantenlänge 400 (viewBox), Mittelpunkt 200. Hier steht nur, was sich ohne
 * Browser prüfen lässt: der Pfad der Skala, die Länge des Pegelbogens und die Wahl, wessen Stimme er zeigt.
 * Importe relativ mit Endung, damit node:test diese Datei laden kann (kernRinge.test.ts).
 */

export const KANTE = 400;
export const MITTE = KANTE / 2;
export const SKALA_RADIUS = 192;
export const BOGEN_RADIUS = 172;
/** so viel vom Ring füllt der Pegelbogen bei voller Stimme */
export const BOGEN_ANTEIL = 0.38;
export const UMFANG = 2 * Math.PI * BOGEN_RADIUS;

const STRICHE = 120;

/** Pfad der Skala: 120 Striche, jeder zehnte lang, jeder fünfte mittel, die übrigen kurz. */
export function skalaPfad(): string {
  let d = "";
  for (let i = 0; i < STRICHE; i++) {
    const w = (i / STRICHE) * Math.PI * 2;
    const laenge = i % 10 === 0 ? 10 : i % 5 === 0 ? 6 : 3;
    const x1 = MITTE + Math.cos(w) * SKALA_RADIUS;
    const y1 = MITTE + Math.sin(w) * SKALA_RADIUS;
    const x2 = MITTE + Math.cos(w) * (SKALA_RADIUS - laenge);
    const y2 = MITTE + Math.sin(w) * (SKALA_RADIUS - laenge);
    d += `M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`;
  }
  return d;
}

/** Wert für stroke-dasharray: sichtbare Länge des Bogens, dann eine Lücke über den Rest des Rings. */
export function bogenStrich(pegel: number): string {
  const p = Math.min(1, Math.max(0, pegel));
  return `${(p * UMFANG * BOGEN_ANTEIL).toFixed(1)} ${UMFANG.toFixed(1)}`;
}

/** Ein Schritt der Glättung: Anstieg schnell, Abfall langsam. */
export function glaetten(gezeigt: number, ziel: number): number {
  return gezeigt + (ziel - gezeigt) * (ziel > gezeigt ? 0.35 : 0.08);
}

export type Sprecher = "bot" | "user" | "fremd";

/**
 * Zielwert (0 bis 1) und Sprecher des Pegelbogens aus den Abtastwerten. WAMO und Mensch rechnen mit dem Faktor 3,2;
 * bei Gleichstand gewinnt WAMO. Spricht die lautere Seite als weitere Stimme (speaker > 0), heißt der Sprecher "fremd".
 * Bei reduzierter Bewegung gibt es nur drei Stufen (0, 0,5 und 1) statt Zittern.
 */
export function pegelAuswahl(
  lv: { user: number; bot: number; speaker?: number },
  reduziert: boolean,
): { ziel: number; wer: Sprecher } {
  const user = Math.min(1, Math.max(0, lv.user) * 3.2);
  const bot = Math.min(1, Math.max(0, lv.bot) * 3.2);
  const roh = Math.max(user, bot);
  const ziel = reduziert ? Math.round(roh * 2) / 2 : roh;
  const wer: Sprecher = user > bot ? ((lv.speaker ?? 0) > 0 ? "fremd" : "user") : "bot";
  return { ziel, wer };
}
