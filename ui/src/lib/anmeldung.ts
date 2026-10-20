/**
 * Höchstalter der Anmeldung und "Angemeldet bleiben".
 *
 * Gemessen wird ab Passwort und Code: Supabase schreibt den Zeitpunkt als Claim amr ins Access-Token und behält ihn
 * beim Erneuern bei. In der Desktop-App gelten 7 Tage (wie der Standard von WAMO_ANMELDUNG_MAX_STUNDEN im Voice-Core),
 * im Browser kürzer (NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER, Standard 24 Stunden). Desktop und Browser laden
 * beide von localhost:3000; unterschieden wird über window.wamoDesktop, nicht über den Origin.
 * Das ist eine Komfortgrenze der Oberfläche, keine Sicherheitsgrenze: Das Token wird hier nur gelesen. Erzwungen wird
 * das Höchstalter im Kern (voice-core/auth_gate.py, eigene Einstellung) und von GoTrue (Zeitbox der Sitzung).
 *
 * "Angemeldet bleiben": mit Haken liegt eine neue Sitzung im localStorage (bleibt über Neustarts), ohne Haken im
 * sessionStorage (endet mit dem Tab oder der App). Eine laufende Sitzung bleibt, wo sie liegt, auch wenn ein anderer
 * Tab die Wahl ändert. Die Wahl steht unter BLEIBEN_SCHLUESSEL; fehlt sie (Anmeldungen von vor der Checkbox), gilt
 * "bleiben". Ohne nutzbaren Speicher lebt die Sitzung nur im Speicher der Seite.
 */

export const DESKTOP_MAX_STUNDEN = 168;
export const BROWSER_MAX_STUNDEN = 24;
export const BLEIBEN_SCHLUESSEL = "wamo-angemeldet-bleiben";

/** Grenze in Stunden: Desktop 168, Browser aus der Umgebung (ganze Stunden, 1 bis 168), sonst 24. */
export function maxAnmeldungStunden(desktop: boolean, browserWert: string | undefined): number {
  if (desktop) return DESKTOP_MAX_STUNDEN;
  const roh = (browserWert ?? "").trim();
  if (!/^\d{1,3}$/.test(roh)) return BROWSER_MAX_STUNDEN;
  const stunden = Number(roh);
  return stunden >= 1 && stunden <= DESKTOP_MAX_STUNDEN ? stunden : BROWSER_MAX_STUNDEN;
}

/** "7 Tagen", "24 Stunden", "1 Stunde" (für "abgelaufen nach ..."). */
export function dauerText(stunden: number): string {
  if (stunden >= 48 && stunden % 24 === 0) return `${stunden / 24} Tagen`;
  return stunden === 1 ? "1 Stunde" : `${stunden} Stunden`;
}

function base64urlText(teil: string): string {
  const b64 = teil.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(teil.length / 4) * 4, "=");
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/** Beginn der Anmeldung in ms (frühester Zeitstempel in amr) oder null. Das Token wird nur gelesen, nicht geprüft. */
export function anmeldeZeitpunkt(accessToken: string): number | null {
  try {
    const claims = JSON.parse(base64urlText(accessToken.split(".")[1] ?? "")) as { amr?: unknown };
    if (!Array.isArray(claims.amr)) return null;
    const stempel = claims.amr
      .map((e) => (typeof e === "object" && e !== null ? (e as { timestamp?: unknown }).timestamp : undefined))
      .filter((t): t is number => typeof t === "number" && Number.isFinite(t) && t > 0);
    return stempel.length ? Math.min(...stempel) * 1000 : null;
  } catch {
    return null;
  }
}

/** So weit darf der Anmeldezeitpunkt in der Zukunft liegen (Uhren von PC und Server laufen etwas auseinander). */
const ZUKUNFT_MS = 5 * 60_000;

/** Ist die Anmeldung zu alt? Ohne Token: nein (nichts abzumelden). Ohne glaubwürdigen Anmeldezeitpunkt: ja. */
export function anmeldungAbgelaufen(accessToken: string | null | undefined, jetzt: number, maxStunden: number): boolean {
  if (!accessToken) return false;
  const seit = anmeldeZeitpunkt(accessToken);
  return seit === null || seit > jetzt + ZUKUNFT_MS || jetzt - seit > maxStunden * 3600_000;
}

/** Fehlt der Anmeldezeitpunkt (oder ist er unglaubwürdig)? Für eine eigene Meldung statt "abgelaufen". */
export function anmeldeZeitpunktFehlt(accessToken: string, jetzt: number): boolean {
  const seit = anmeldeZeitpunkt(accessToken);
  return seit === null || seit > jetzt + ZUKUNFT_MS;
}

/** Was supabase-js als storage braucht (synchron genügt). */
export type Speicher = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

function sicher<T>(f: () => T, ersatz: T): T {
  try {
    return f();
  } catch {
    return ersatz; // privates Fenster, gesperrter Speicher: dann ohne
  }
}

/**
 * Speicher für supabase-js. Lesen: zuerst sessionStorage (die Sitzung dieses Tabs), dann localStorage, zuletzt der
 * Speicher der Seite. Schreiben: dorthin, wo der Eintrag schon liegt; nur ein neuer Eintrag (Anmeldung) folgt der
 * Wahl "bleiben". Die andere Kopie wird erst nach erfolgreichem Schreiben entfernt. Abmelden räumt alle drei.
 */
export function authSpeicher(lokal: Speicher | null, sitzung: Speicher | null, bleiben: () => boolean): Speicher {
  const seite = new Map<string, string>(); // letzter Rückhalt ohne nutzbaren Speicher
  const lesen = (sp: Speicher | null, key: string) => (sp ? sicher(() => sp.getItem(key), null) : null);
  const schreiben = (sp: Speicher | null, key: string, value: string) =>
    sp !== null && sicher(() => (sp.setItem(key, value), true), false);
  return {
    getItem: (key) => lesen(sitzung, key) ?? lesen(lokal, key) ?? seite.get(key) ?? null,
    setItem: (key, value) => {
      const liegt = lesen(sitzung, key) !== null ? "sitzung" : lesen(lokal, key) !== null ? "lokal" : null;
      const wohin = liegt ?? (bleiben() ? "lokal" : "sitzung");
      const ziel = wohin === "sitzung" ? sitzung : lokal;
      const anderer = wohin === "sitzung" ? lokal : sitzung;
      if (schreiben(ziel, key, value)) {
        sicher(() => anderer?.removeItem(key), undefined); // keine zweite, veraltete Kopie
        seite.delete(key);
      } else if ((sicher(() => ziel?.removeItem(key), undefined), !schreiben(anderer, key, value))) {
        seite.set(key, value); // beide Speicher gesperrt oder voll: nur für diese Seite
      }
    },
    removeItem: (key) => {
      sicher(() => lokal?.removeItem(key), undefined);
      sicher(() => sitzung?.removeItem(key), undefined);
      seite.delete(key);
    },
  };
}

// ---------- Wahl "Angemeldet bleiben" (nur im Browser aufrufen) ----------
function lokalerSpeicher(): Speicher | null {
  return sicher(() => (typeof window === "undefined" ? null : window.localStorage), null);
}

/** Gilt "bleiben"? Ohne gespeicherte Wahl ja (frühere Anmeldungen bleiben, wie sie waren). */
export function angemeldetBleiben(): boolean {
  return sicher(() => lokalerSpeicher()?.getItem(BLEIBEN_SCHLUESSEL) !== "0", true);
}

/** Hat die Person den Haken zuletzt selbst gesetzt? (Vorauswahl im Browser) */
export function bleibenGewaehlt(): boolean {
  return sicher(() => lokalerSpeicher()?.getItem(BLEIBEN_SCHLUESSEL) === "1", false);
}

export function setzeAngemeldetBleiben(an: boolean): void {
  sicher(() => lokalerSpeicher()?.setItem(BLEIBEN_SCHLUESSEL, an ? "1" : "0"), undefined);
}

/** Grenze dieses Geräts in Stunden (Desktop-App oder Browser). */
export function maxStundenHier(): number {
  const desktop = typeof window !== "undefined" && Boolean(window.wamoDesktop?.isDesktop);
  return maxAnmeldungStunden(desktop, process.env.NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER);
}

// ---------- Grund der letzten automatischen Abmeldung (LoginGate zeigt ihn an) ----------
const GRUND_GUELTIG_MS = 10_000; // nur für die Abmeldung, die gleich folgt, nicht für eine spätere
let abmeldeGrund: { text: string; seit: number } | null = null;

export function merkeAbmeldeGrund(grund: string, jetzt: number = Date.now()): void {
  abmeldeGrund = { text: grund, seit: jetzt };
}

export function nimmAbmeldeGrund(jetzt: number = Date.now()): string | null {
  const grund = abmeldeGrund;
  abmeldeGrund = null;
  return grund && jetzt - grund.seit <= GRUND_GUELTIG_MS ? grund.text : null;
}
