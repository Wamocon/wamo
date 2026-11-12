/**
 * Breite der beiden Seitenleisten der Kommandozentrale (links Hinweise, rechts Tafeln), vom Nutzer per Ziehen oder Tastatur wählbar.
 * Reine Logik ohne Browser und ohne React, damit sie sich mit node:test prüfen lässt (seitenbreite.test.ts). Keine Importe.
 *
 * Regeln:
 *  - Jede Seite hat einen festen Bereich (LINKS_MIN bis LINKS_MAX, RECHTS_MIN bis RECHTS_MAX) und einen Standard. Die Standardwerte
 *    sind die Breiten, die kommando.css ohne Wahl setzt (300 px und 380 px).
 *  - Die Mitte behält mindestens MITTE_MIN. Reicht das Fenster nicht, gibt zuerst die rechte, dann die linke Seite nach (passend);
 *    die gewählte Breite bleibt dabei erhalten und gilt wieder, sobald das Fenster größer wird.
 *  - Die Wahl liegt je Gerät in localStorage (Schlüssel wamo.seitenbreite.links und wamo.seitenbreite.rechts, ganze Pixel). Jeder
 *    Zugriff steht in try/catch. Beim Lesen wird ein Wert in den Bereich geklemmt, Unbrauchbares verworfen (dann gilt der Standard).
 *    Ohne nutzbaren Speicher gilt die Wahl bis zum Neuladen.
 */

export type Seite = "links" | "rechts";
export type Grenzen = { min: number; max: number };
export type Breiten = { links: number; rechts: number };
/** Das, was die Funktionen von localStorage brauchen (in Tests ein Mapspeicher). */
export type SpeicherZugriff = Pick<Storage, "getItem" | "setItem" | "removeItem">;

// ---------- feste Werte ----------
export const LINKS_MIN = 200;
export const LINKS_MAX = 520;
export const LINKS_STANDARD = 300;
export const RECHTS_MIN = 260;
export const RECHTS_MAX = 640;
export const RECHTS_STANDARD = 380;
/** So breit bleibt die Mitte mindestens. */
export const MITTE_MIN = 360;
/** Schritt der Pfeiltasten, mit Umschalt der große Schritt. */
export const SCHRITT = 16;
export const SCHRITT_GROSS = 48;

export const BEREICH: Record<Seite, Grenzen & { standard: number }> = {
  links: { min: LINKS_MIN, max: LINKS_MAX, standard: LINKS_STANDARD },
  rechts: { min: RECHTS_MIN, max: RECHTS_MAX, standard: RECHTS_STANDARD },
};
export const STANDARD_BREITEN: Breiten = { links: LINKS_STANDARD, rechts: RECHTS_STANDARD };

export const SCHLUESSEL: Record<Seite, string> = {
  links: "wamo.seitenbreite.links",
  rechts: "wamo.seitenbreite.rechts",
};

// ---------- Klemmen und Platz ----------

/** Ganze Pixel im Bereich min bis max; keine Zahl ergibt den Mindestwert, vertauschte Grenzen ebenso. */
export function klemmen(wert: number, min: number, max: number): number {
  if (!Number.isFinite(wert)) return min;
  return Math.min(Math.max(min, max), Math.max(min, Math.round(wert)));
}

/**
 * Grenzen einer Seite bei bekannter Gesamtbreite: Die Mitte behält MITTE_MIN neben der anderen Seite. Gesamtbreite 0 heißt
 * "unbekannt" (Server, Test): dann gelten nur die festen Grenzen. Der Höchstwert fällt nie unter den Mindestwert.
 */
export function grenzenFuer(seite: Seite, andere: number, gesamt: number): Grenzen {
  const { min, max } = BEREICH[seite];
  if (gesamt <= 0) return { min, max };
  return { min, max: Math.max(min, Math.min(max, gesamt - MITTE_MIN - andere)) };
}

/** Breiten, die in diese Gesamtbreite passen: erst jede Seite in ihren Bereich, dann bei Platzmangel rechts, danach links kürzen. */
export function passend(breiten: Breiten, gesamt: number): Breiten {
  let links = klemmen(breiten.links, LINKS_MIN, LINKS_MAX);
  let rechts = klemmen(breiten.rechts, RECHTS_MIN, RECHTS_MAX);
  if (gesamt <= 0) return { links, rechts };
  rechts = Math.max(RECHTS_MIN, rechts - Math.max(0, links + rechts + MITTE_MIN - gesamt));
  links = Math.max(LINKS_MIN, links - Math.max(0, links + rechts + MITTE_MIN - gesamt));
  return { links, rechts };
}

// ---------- Speicher ----------

/** Gespeicherte Breite einer Seite, geklemmt; null bei fehlendem, unbrauchbarem oder gesperrtem Speicher. */
export function breiteLesen(sp: Pick<Storage, "getItem"> | null, seite: Seite): number | null {
  try {
    const roh = sp?.getItem(SCHLUESSEL[seite])?.trim();
    if (!roh || !/^\d+(\.\d+)?$/.test(roh)) return null;
    const { min, max } = BEREICH[seite];
    return klemmen(Number(roh), min, max);
  } catch {
    return null;
  }
}

/** Beide Breiten aus dem Speicher; was fehlt oder ungültig ist, bekommt den Standard. */
export function breitenLesen(sp: Pick<Storage, "getItem"> | null): Breiten {
  return {
    links: breiteLesen(sp, "links") ?? LINKS_STANDARD,
    rechts: breiteLesen(sp, "rechts") ?? RECHTS_STANDARD,
  };
}

/** Breite speichern (geklemmt, ganze Pixel); false, wenn der Speicher fehlt, gesperrt oder voll ist. */
export function breiteSchreiben(sp: Pick<Storage, "setItem"> | null, seite: Seite, px: number): boolean {
  try {
    if (!sp) return false;
    const { min, max } = BEREICH[seite];
    sp.setItem(SCHLUESSEL[seite], String(klemmen(px, min, max)));
    return true;
  } catch {
    return false;
  }
}

/** Gespeicherte Wahl entfernen (zurück auf den Standard); false, wenn der Speicher fehlt oder gesperrt ist. */
export function breiteLoeschen(sp: Pick<Storage, "removeItem"> | null, seite: Seite): boolean {
  try {
    if (!sp) return false;
    sp.removeItem(SCHLUESSEL[seite]);
    return true;
  } catch {
    return false;
  }
}

// ---------- Bedienung ----------

/**
 * Neue Breite nach einer Taste, null bei jeder anderen Taste. Der Pfeil folgt dem Griff: Links wird die linke Leiste mit Pfeil
 * rechts breiter, rechts wird die rechte Leiste mit Pfeil links breiter. Pos1 gibt die Mindest-, Ende die Höchstbreite.
 */
export function tastenBreite(seite: Seite, taste: string, umschalt: boolean, wert: number, grenzen: Grenzen): number | null {
  const schritt = umschalt ? SCHRITT_GROSS : SCHRITT;
  const nachRechts = seite === "links" ? schritt : -schritt;
  const ziel =
    taste === "ArrowRight" ? wert + nachRechts
    : taste === "ArrowLeft" ? wert - nachRechts
    : taste === "Home" ? grenzen.min
    : taste === "End" ? grenzen.max
    : null;
  return ziel === null ? null : klemmen(ziel, grenzen.min, grenzen.max);
}

/** Breite beim Ziehen: Startbreite plus Weg des Zeigers (links mit ihm, rechts gegen ihn), in den Grenzen. */
export function ziehBreite(seite: Seite, start: number, weg: number, grenzen: Grenzen): number {
  return klemmen(start + (seite === "links" ? weg : -weg), grenzen.min, grenzen.max);
}

// ---------- kleiner Speicher mit Hörern (für useSyncExternalStore) ----------

/**
 * Hält die gewählten Breiten. Gelesen wird erst beim ersten Zugriff (im Browser nach dem Laden, nie beim Rendern auf dem Server).
 * setzen ändert nur den Stand (Vorschau beim Ziehen), festlegen schreibt zusätzlich, standardSetzen entfernt die Wahl.
 * Der Schnappschuss von lesen() bleibt derselbe Wert, solange sich nichts ändert.
 */
export function erzeugeBreitenSpeicher(sp: SpeicherZugriff | null) {
  let aktuell: Breiten | null = null;
  const hoerer = new Set<() => void>();

  const lesen = (): Breiten => (aktuell ??= breitenLesen(sp));
  const setzen = (seite: Seite, px: number) => {
    const alt = lesen();
    const { min, max } = BEREICH[seite];
    const wert = klemmen(px, min, max);
    if (wert === alt[seite]) return;
    aktuell = { ...alt, [seite]: wert };
    hoerer.forEach((h) => h());
  };

  return {
    lesen,
    setzen,
    festlegen(seite: Seite, px: number) {
      setzen(seite, px);
      breiteSchreiben(sp, seite, px);
    },
    standardSetzen(seite: Seite) {
      setzen(seite, BEREICH[seite].standard);
      breiteLoeschen(sp, seite);
    },
    abonnieren(h: () => void) {
      hoerer.add(h);
      return () => {
        hoerer.delete(h);
      };
    },
  };
}

export type BreitenSpeicher = ReturnType<typeof erzeugeBreitenSpeicher>;
