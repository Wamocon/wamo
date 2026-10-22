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

/** Ausblenden beim Stopp (Servernachricht leiser_stopp, der Kern rechnet damit: Kern/soft_interrupt.py STOPP_FADE_SECS). */
export const AUSBLENDEN_MS = 140;
/** Vorsorgliches Dämpfen, sobald die Person zu sprechen beginnt (Servernachricht stimme_leiser): Zielpegel und Zeiten. */
export const GEDAEMPFT = 0.3;
export const DAEMPFEN_MS = 90;
export const ENTDAEMPFEN_MS = 250;
/** Längste Dämpfung ohne Entwarnung des Kerns (der Kern nimmt sie nach 3 s selbst zurück). */
export const DAEMPFEN_MAX_MS = 4000;

/** Lautstärke eines linearen Verlaufs von von nach nach nach vergangenMs von dauerMs. Ungültige Werte ergeben das Ziel. */
export function verlauf(von: number, nach: number, dauerMs: number, vergangenMs: number): number {
  if (![von, nach, dauerMs, vergangenMs].every(Number.isFinite) || dauerMs <= 0 || vergangenMs >= dauerMs) return nach;
  const t = Math.max(0, vergangenMs / dauerMs); // am Ende genau das Ziel (sonst liefe die Animation bei Rundungsfehlern weiter)
  return von + (nach - von) * t;
}
