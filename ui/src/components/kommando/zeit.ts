/**
 * Reine Hilfen für Uhrzeit, Dauer und Sitzungsfortschritt der Kommandozentrale.
 * Ohne React und ohne Browser, damit sie mit node:test geprüft werden können (zeit.test.ts).
 */

const zweistellig = (n: number) => String(n).padStart(2, "0");

/** "HH:MM" in lokaler Zeit. */
export function uhrzeit(d: Date): string {
  return `${zweistellig(d.getHours())}:${zweistellig(d.getMinutes())}`;
}

/** Dauer als "HH:MM:SS" (negativ wird zu 0). */
export function formatDauer(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${zweistellig(Math.floor(s / 3600))}:${zweistellig(Math.floor((s % 3600) / 60))}:${zweistellig(s % 60)}`;
}

/** Standard der längsten Sitzung in Minuten (wie WAMO_MAX_SITZUNG_MIN im Voice-Core). */
export const SITZUNG_MAX_STANDARD_MIN = 120;

/** Höchstdauer aus der Umgebung (ganze Minuten, 1 bis 1440), sonst der Standard. */
export function maxSitzungMinuten(roh: string | undefined): number {
  const text = (roh ?? "").trim();
  if (!/^\d{1,4}$/.test(text)) return SITZUNG_MAX_STANDARD_MIN;
  const n = Number(text);
  return n >= 1 && n <= 1440 ? n : SITZUNG_MAX_STANDARD_MIN;
}

/** Anteil der Sitzung in ganzen Prozent (0 bis 100); ohne Beginn 0. */
export function sitzungsProzent(startMs: number | null, jetztMs: number, maxMin: number): number {
  if (startMs === null || !(maxMin > 0)) return 0;
  const anteil = (jetztMs - startMs) / (maxMin * 60_000);
  return Math.min(100, Math.max(0, Math.round(anteil * 100)));
}

/** Gesprochene Form für Screenreader: "12 Minuten von 120". */
export function sitzungsText(startMs: number | null, jetztMs: number, maxMin: number): string {
  const min = startMs === null ? 0 : Math.max(0, Math.floor((jetztMs - startMs) / 60_000));
  return `${min} ${min === 1 ? "Minute" : "Minuten"} von ${maxMin}`;
}
