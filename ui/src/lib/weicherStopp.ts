/**
 * Weicher Stopp bei Unterbrechung: Nach der Servernachricht "leiser_stopp" spricht WAMO das laufende Wort zu
 * Ende und wird dann in 140 ms ausgeblendet (useWamo.ts, fadeOutAudio). Wann ausgeblendet wird, entscheidet
 * diese reine Funktion; die 20-Hz-Pegelschleife in useWamo.ts ruft sie mit dem selbst gemessenen Pegel auf
 * (AnalyserNode auf WAMOs Tonspur). SmallWebRTC meldet keinen Pegel der Gegenseite.
 */

/** armedAt: Zeitpunkt von "leiser_stopp" (performance.now) oder null; fading: Ausblenden läuft schon. */
export type SoftStop = { armedAt: number | null; fading: boolean };

/** Unter diesem Pegel (RMS, Skala wie lv.bot) gilt WAMO als gerade still: eine Wortpause. */
export const LEISE_PEGEL = 0.015;
/** Spätestens nach dieser Zeit wird ausgeblendet, auch ohne Wortpause. */
export const SPAETESTENS_MS = 600;

/** true, wenn jetzt ausgeblendet werden soll: weicher Stopp gesetzt, noch kein Ausblenden, und WAMO ist
 * gerade leise oder die Frist ist abgelaufen. Ein ungültiger Pegel zählt nicht als leise. */
export function sollAusblenden(stop: SoftStop, pegel: number, jetzt: number): boolean {
  if (stop.armedAt === null || stop.fading) return false;
  return pegel < LEISE_PEGEL || jetzt - stop.armedAt > SPAETESTENS_MS;
}
