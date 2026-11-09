/**
 * Reiner Kern der Drosselung einer Anzeige (lib/useGedrosselt.ts), ohne Uhr und ohne React, damit er sich mit node:test prüfen
 * lässt (drossel.test.ts). Ein Zwischenstand wartet höchstens bis zum Ende des Abstands seit der letzten Übernahme.
 */

/**
 * Wie lange ein Zwischenstand noch warten muss, in Millisekunden. 0 heißt: jetzt übernehmen (Abstand um oder ungültige Angabe).
 * Eine rückwärts gelaufene Uhr (negativer Abstand seit der Übernahme) lässt nie länger warten als den vollen Abstand.
 */
export function drosselWartezeit(seitUebernahmeMs: number, intervallMs: number): number {
  if (!Number.isFinite(seitUebernahmeMs) || !Number.isFinite(intervallMs) || intervallMs <= 0) return 0;
  const seit = Math.max(0, seitUebernahmeMs);
  return Math.max(0, intervallMs - seit);
}
