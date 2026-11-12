// Gemeinsame reine Funktionen der Oberflächen: kein DOM, kein Netz, nur Eingabe und Ausgabe (mit node --test geprüft, tests/js).

export const PLATZ_TOKEN_MUSTER = /^[0-9a-f]{32}$/;
export const BETREUER_TOKEN_MUSTER = /^[A-Za-z0-9._~+/=-]{43,200}$/;

export const GRUNDPAUSE_MS = 3000;
export const OBERGRENZE_MS = 60000;
export const RETRY_AFTER_MAX_S = 120;

export const platzTokenGueltig = (wert) => typeof wert === "string" && PLATZ_TOKEN_MUSTER.test(wert);
export const betreuerTokenGueltig = (wert) => typeof wert === "string" && BETREUER_TOKEN_MUSTER.test(wert);

// Der Wert des Schlüssels aus einem URL-Fragment ("#t=abc&x=1"). Der Wert darf = + und / enthalten (Zeichen eines Tokens). Eine Abfrage
// ("?t=abc") zählt nie. Fehlt der Schlüssel oder ist sein Wert leer, ist das Ergebnis null.
export function fragmentLesen(fragment, name) {
  if (typeof fragment !== "string") return null;
  const roh = fragment.startsWith("#") ? fragment.slice(1) : fragment;
  for (const teil of roh.split("&")) {
    const gleich = teil.indexOf("=");
    if (gleich > 0 && teil.slice(0, gleich) === name) return teil.length > gleich + 1 ? teil.slice(gleich + 1) : null;
  }
  return null;
}

// Wie lange die Seite nach einem Fehler wartet. Mit Retry-After in ganzen Sekunden (mindestens 1, höchstens 120), sonst mit einer Pause, die mit
// jedem Fehler in Folge wächst (3, 6, 12, 24, 48 Sekunden, höchstens 60).
export function ruecknahmeMs(retryAfter, fehlerFolge) {
  if (typeof retryAfter === "string" && /^\s*\d+\s*$/.test(retryAfter)) {
    return Math.min(Math.max(Number.parseInt(retryAfter, 10), 1), RETRY_AFTER_MAX_S) * 1000;
  }
  const folge = Number.isInteger(fehlerFolge) && fehlerFolge > 0 ? fehlerFolge : 1;
  return Math.min(OBERGRENZE_MS, GRUNDPAUSE_MS * 2 ** (folge - 1));
}

// "2026-11-28T09:30:00Z" als "28.11.2026, 10:30" in der gewünschten Zeitzone (Standard Berlin). Unbrauchbares ergibt "".
export function formatZeit(iso, zeitzone = "Europe/Berlin") {
  if (typeof iso !== "string") return "";
  const zeit = new Date(iso);
  if (Number.isNaN(zeit.getTime())) return "";
  return new Intl.DateTimeFormat("de-DE", {
    timeZone: zeitzone, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).format(zeit);
}

export const kurzeId = (kennung) => (typeof kennung === "string" ? kennung.slice(0, 8) : "");
