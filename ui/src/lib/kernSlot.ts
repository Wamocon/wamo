/**
 * Kern-Slots (Schritt 2.2 der Selbstüberarbeitung, Konzept docs/selbstueberarbeitung.md Abschnitt A.2): Zwei Kern-Instanzen laufen
 * nebeneinander, Caddy routet /i/<slot>/ auf je eine. Die Oberfläche fragt vor dem Verbinden, welcher Slot aktiv ist (GET /wamo/slot), und startet
 * mit ${kern}/i/<slot>/start. Der WebRTC-Transport bildet daraus die Adressen der Sitzung (…/i/<slot>/sessions/<id>/api/offer), jede Sitzung
 * bleibt damit auf ihrer Instanz. Ältere Server kennen den Pfad nicht: dann gilt wie bisher ${kern}/start.
 *
 * Reine Logik ohne React (kernSlot.test.ts). Nie eine Annahme über die Antwort: nur "a" oder "b" zählt.
 */

export type Slot = "a" | "b";

const SLOT_ZEIT_MS = 3000;

/** Antworttext von GET /wamo/slot als Slot; alles andere (HTML, Fehlertext, leer) ist kein Slot. */
export function alsSlot(roh: unknown): Slot | null {
  if (typeof roh !== "string") return null;
  const t = roh.trim();
  return t === "a" || t === "b" ? t : null;
}

/** Start-Adresse: mit Slot über den Pfad der Instanz, ohne Slot (älterer Server) wie bisher. */
export function startUrl(kern: string, slot: Slot | null): string {
  const basis = kern.replace(/\/+$/, "");
  return slot ? `${basis}/i/${slot}/start` : `${basis}/start`;
}

/** Aus der Antwort 409 einer auslaufenden Instanz ({"slot":"b"}) der Slot, auf den neu zu starten ist. */
export function slotAus409(status: number | null | undefined, antwort: unknown): Slot | null {
  if (status !== 409 || antwort === null || typeof antwort !== "object") return null;
  return alsSlot((antwort as { slot?: unknown }).slot);
}

type HoleFn = (url: string, init?: RequestInit) => Promise<Pick<Response, "ok" | "status" | "text">>;

/**
 * Fragt den aktiven Slot ab. Kein Slot (Fehler, Zeitüberschreitung, unbekannte Antwort) ist kein Fehler des Verbindens: null heißt "ohne Slot starten".
 * Der Server verbietet das Zwischenspeichern (Cache-Control: no-store); die Anfrage trägt kein Token und keine Cookies.
 */
export async function aktiverSlot(kern: string, hole: HoleFn = fetch, zeitMs: number = SLOT_ZEIT_MS): Promise<Slot | null> {
  const basis = kern.replace(/\/+$/, "");
  const abbruch = new AbortController();
  const timer = setTimeout(() => abbruch.abort(), zeitMs);
  try {
    const antwort = await hole(`${basis}/wamo/slot`, { method: "GET", cache: "no-store", credentials: "omit", signal: abbruch.signal });
    return antwort.ok ? alsSlot(await antwort.text()) : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
