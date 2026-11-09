import type { Darstellung } from "./darstellung.ts"; // relativ, damit node:test diese Datei laden kann

/**
 * Welche Tafel steht im Bild? Reine Logik des Rahmens (Rahmen.tsx), ohne React und ohne Browser (tafeln.test.ts). Das CSS
 * entscheidet über data-rechts und data-mobil, was zu sehen ist; hier steht dieselbe Regel für das Einhängen: Tafeln mit
 * Abruf oder Livefortschritt (Büro, Vorgänge) werden nur eingehängt, solange man sie sieht, dann ruht ihre Arbeit.
 */

export type TafelRechts = "auftraege" | "vorgaenge" | "erledigt" | "buero";
export type TafelMobil = "kern" | "auftraege" | "vorgaenge" | "canvas" | "hinweise" | "buero";

export type TafelLage = {
  /** Die Einstellungsseite ist offen (die Kommandozentrale bleibt eingehängt, ist aber verborgen). */
  einstellungen: boolean;
  /** Drei Spalten (ab 1100 px) statt Reiterleiste. */
  breit: boolean;
  /** Darstellung, die gerade gilt (offene Freigaben erzwingen "normal"). */
  effektiv: Darstellung;
  rechts: TafelRechts;
  /** Der wirksame mobile Reiter: in Fokus und Zentrale immer "kern". */
  mobilEff: TafelMobil;
};

/**
 * Breit zeigt nur der gewählte rechte Reiter seine Tafel, und das nicht in Fokus und Zentrale (dort ist die Spalte verborgen).
 * Schmal zählt allein der wirksame mobile Reiter. Unter den Einstellungen ist keine Tafel zu sehen.
 */
export function tafelSichtbar(lage: TafelLage, tafel: "vorgaenge" | "buero"): boolean {
  if (lage.einstellungen) return false;
  if (lage.breit) return lage.effektiv !== "fokus" && lage.effektiv !== "zentrale" && lage.rechts === tafel;
  return lage.mobilEff === tafel;
}
