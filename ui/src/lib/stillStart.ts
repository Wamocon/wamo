/**
 * Start beim ersten Tippen (Komfort-Paket KP-1A, nur Oberfläche): Wer es einschaltet, kann WAMO ohne "Gespräch starten" bedienen.
 * Der erste Tastendruck im Eingabefeld startet still eine Sitzung (WebRTC ohne Mikrofon, Antworten als Text, Wiedergabe stumm).
 * Reine Logik ohne Browser und ohne React, damit sie sich mit node:test prüfen lässt (stillStart.test.ts). Alle Importe relativ.
 *
 * Schutzregeln (jede steht als Test):
 *  - Der Schalter gilt je Gerät UND je Person (localStorage, Schlüssel mit der user_id), nie als Server-Zustand. Standard an (seit
 *    12.11.2026: wer nie entschieden hat, kann sofort tippen); eine ausdrückliche Wahl ("an" oder "aus") bleibt gespeichert.
 *  - Laborkonten (labor-NN@wamo.test, labor-schau@wamo.test) starten nie von selbst: Die Oberfläche kennt das Laborprofil vor dem
 *    Verbinden sonst nicht (das Profil kommt erst vom Kern); die Ablehnung im Kern selbst kommt erst mit KP-1C.
 *  - Ein stiller Start darf eine laufende Sitzung nie verdrängen. Das lässt sich ohne Kern nur teilweise sichern: Der Kern löst
 *    je Profil die alte Sitzung ab (voice-core/sitzungen.py), und die Oberfläche erfährt vor dem Start nichts von einer Sitzung auf
 *    einem anderen Gerät. Erkennbar ist nur der umgekehrte Fall: Hat eine neuere Sitzung diese hier abgelöst, startet das Tippen
 *    hier nicht wieder von selbst (kein Hin und Her), sondern verlangt "Hier übernehmen".
 *  - Die Leerlaufregel gilt nur für per Tippen gestartete Sitzungen und trennt nie, solange die PC-Steuerung an ist, eine Karte
 *    offen ist, ein Entwicklungsauftrag, Vorgang, das Live-Bild oder eine Antwort läuft (Befund L1).
 */

import type { Presence } from "./wamo.ts"; // nur der Typ: wird beim Laden gestrichen

// ---------- feste Werte ----------
/** Ruhezeit, nach der eine per Tippen gestartete Sitzung getrennt wird (Befund L2: bis dahin zählt sie für die Ruheprüfung auf dem Server). */
export const LEERLAUF_MS = 20 * 60 * 1000;
/** So oft schaut die Oberfläche nach der Ruhezeit. */
export const LEERLAUF_TAKT_MS = 30_000;
/** Höchstens so viele stille Starts je Zeitfenster (Schutz vor Schleifen, zum Beispiel bei einem Kern, der dauernd ablehnt). */
export const STARTS_MAX = 6;
export const STARTS_FENSTER_MS = 10 * 60 * 1000;

/** Text des Kerns, wenn eine neue Sitzung dieses Profils die laufende ablöst (voice-core/sitzungen.py, GRUND_UEBERNOMMEN). Ein Test hält beide gleich. */
export const GRUND_UEBERNOMMEN = "Eine neue Sitzung dieses Profils hat übernommen.";
export const HINWEIS_ANDERES_FENSTER = "WAMO läuft in einem anderen Fenster. Mit „Hier übernehmen“ startet die Sitzung hier, die andere endet dabei.";
export const HINWEIS_ZU_OFT = "Zu viele stille Starts in kurzer Zeit. Bitte „Gespräch starten“ benutzen oder ein paar Minuten warten.";
export const HINWEIS_RUHE = "Die Sitzung wurde nach 20 Minuten ohne Eingabe beendet. Beim nächsten Tippen startet WAMO wieder.";
export const TEXT_VERBINDET = "Verbinde, deine Nachricht wird gleich gesendet.";
export const TEXT_STILL_BEREIT = "Bereit, Mikrofon aus. WAMO antwortet als Text. Mit dem Mikrofonknopf sprichst du mit ihm.";

/** Hat der Kern diese Sitzung mit dem Übernahmetext beendet? */
export function istUebernommen(grund: string | null | undefined): boolean {
  return grund === GRUND_UEBERNOMMEN;
}

// ---------- Zustand im Store ----------
/** still: Sitzung ohne Mikrofon und ohne Stimme. perTippen: von selbst gestartet, nur dann gilt die Leerlaufregel.
 *  uebernommen: eine neuere Sitzung dieses Profils hat die letzte hier abgelöst. */
export type StillStand = { still: boolean; perTippen: boolean; uebernommen: boolean };
export const STILL_AUS: StillStand = Object.freeze({ still: false, perTippen: false, uebernommen: false });

// ---------- Schalter je Gerät und Person ----------
export const SCHALTER_PRAEFIX = "wamo.stillStart.";

export type Speicher = {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
};

/** Schlüssel im Speicher für diese Person; null, wenn die Kennung fehlt oder nicht wie eine user_id aussieht (nur Buchstaben, Ziffern, Bindestrich). */
export function schalterSchluessel(userId: string | null | undefined): string | null {
  if (typeof userId !== "string" || !/^[A-Za-z0-9-]{1,64}$/.test(userId)) return null;
  return `${SCHALTER_PRAEFIX}${userId}`;
}

/** Standard, solange die Person nichts gewählt hat. */
export const SCHALTER_STANDARD = true;

/**
 * Ist der Schalter für diese Person auf diesem Gerät an? "an" und "aus" sind ausdrückliche Wahlen, ein fehlender Eintrag oder ein
 * unlesbarer Speicher heißt Standard (an), jeder andere Wert gilt als aus. Ohne brauchbare Kennung immer aus (kein Schlüssel, keine Person).
 */
export function schalterLesen(speicher: Speicher | null | undefined, userId: string | null | undefined): boolean {
  const k = schalterSchluessel(userId);
  if (!k) return false;
  let wert: string | null = null;
  try {
    wert = speicher ? speicher.getItem(k) : null;
  } catch {
    wert = null;
  }
  if (wert === "an") return true;
  if (wert === "aus") return false;
  return wert === null ? SCHALTER_STANDARD : false;
}

/** Schalter schreiben (ausdrücklich "an" oder "aus"). true, wenn der Speicher den Wert angenommen hat (sonst gilt er nur bis zum Neuladen, das regelt der Aufrufer). */
export function schalterSchreiben(speicher: Speicher | null | undefined, userId: string | null | undefined, an: boolean): boolean {
  const k = schalterSchluessel(userId);
  if (!k || !speicher) return false;
  try {
    speicher.setItem(k, an ? "an" : "aus");
    return true;
  } catch {
    return false;
  }
}

// ---------- Laborkonten ----------
// Die Konten legt scripts/wamo_labor.py an: labor-01 bis labor-16 (Plätze) und labor-schau (Schaufenster), Domain wamo.test.
const LABOR_MAIL = /^labor-(0[1-9]|1[0-6]|schau)@wamo\.test$/;

/** Ist das ein Konto des Testlabors? Dort startet das Tippen nie von selbst. */
export function istLaborKonto(email: string | null | undefined): boolean {
  return typeof email === "string" && LABOR_MAIL.test(email.trim().toLowerCase());
}

// ---------- Startentscheidung ----------
export type StartLage = {
  schalter: boolean;
  laborKonto: boolean;
  /** Tab sichtbar (document.visibilityState === "visible") */
  sichtbar: boolean;
  presence: Presence;
  uebernommen: boolean;
  /** Zeitpunkte der letzten stillen Starts (Date.now) */
  startZeiten: readonly number[];
  jetzt: number;
};

export type NeinGrund = "schalter_aus" | "labor" | "verdeckt" | "laeuft" | "uebernommen" | "zu_oft";
export type Entscheidung = { art: "starten" } | { art: "nein"; grund: NeinGrund; hinweis?: string };

/** Startet der erste Tastendruck jetzt eine stille Sitzung? Die Reihenfolge der Prüfungen ist Absicht: erst die harten Sperren. */
export function startEntscheidung(l: StartLage): Entscheidung {
  if (!l.schalter) return { art: "nein", grund: "schalter_aus" };
  if (l.laborKonto) return { art: "nein", grund: "labor" };
  if (!l.sichtbar) return { art: "nein", grund: "verdeckt" };
  if (l.presence !== "offline") return { art: "nein", grund: "laeuft" };
  if (l.uebernommen) return { art: "nein", grund: "uebernommen", hinweis: HINWEIS_ANDERES_FENSTER };
  const imFenster = l.startZeiten.filter((t) => t <= l.jetzt && l.jetzt - t < STARTS_FENSTER_MS);
  if (imFenster.length >= STARTS_MAX) return { art: "nein", grund: "zu_oft", hinweis: HINWEIS_ZU_OFT };
  return { art: "starten" };
}

/** Start eintragen und alte Einträge außerhalb des Zeitfensters verwerfen. */
export function startNotieren(zeiten: readonly number[], jetzt: number): number[] {
  return [...zeiten.filter((t) => t <= jetzt && jetzt - t < STARTS_FENSTER_MS), jetzt];
}

// ---------- Leerlauf ----------
export type LeerlaufLage = {
  /** per Tippen gestartet (eine mit "Gespräch starten" geklickte Sitzung bleibt unverändert) */
  perTippen: boolean;
  online: boolean;
  presence: Presence;
  /** PC-Steuerung dieser Sitzung an */
  pcSteuerung: boolean;
  /** offene Freigabekarten und Entwicklungsaufträge, die auf eine Entscheidung warten */
  karten: number;
  /** Entwicklungsauftrag gesendet oder laufend (lib/auftragStart.ts, auftragLaeuft) */
  auftragLaeuft: boolean;
  /** irgendein Vorgang läuft oder wartet auf Freigabe */
  vorgangLaeuft: boolean;
  liveBild: boolean;
  werkzeugLaeuft: boolean;
};

/** Grund, warum jetzt nicht getrennt werden darf, oder null. Die Texte sind für Protokoll und Tests, nicht für die Anzeige. */
export function leerlaufBlockiert(l: LeerlaufLage): string | null {
  if (l.pcSteuerung) return "PC-Steuerung an";
  if (l.karten > 0) return "Karte wartet auf Entscheidung";
  if (l.auftragLaeuft) return "Entwicklungsauftrag läuft";
  if (l.vorgangLaeuft) return "Vorgang läuft";
  if (l.liveBild) return "Live-Bild offen";
  if (l.presence !== "idle") return "Antwort oder Gespräch läuft";
  if (l.werkzeugLaeuft) return "Werkzeug läuft";
  return null;
}

/**
 * Ein Schritt der Leerlaufprüfung. letzte ist der Zeitpunkt der letzten Eingabe (Date.now). Besteht eine Blockade, beginnt die
 * Ruhezeit neu (nach einem langen Auftrag nicht sofort trennen). Eine zurückgestellte Uhr (letzte in der Zukunft) trennt nie.
 */
export function leerlaufPruefen(p: { jetzt: number; letzte: number; lage: LeerlaufLage }): { trennen: boolean; letzte: number } {
  const { jetzt, letzte, lage } = p;
  if (!lage.perTippen || !lage.online) return { trennen: false, letzte };
  if (leerlaufBlockiert(lage) !== null) return { trennen: false, letzte: jetzt };
  const still = jetzt - letzte;
  return { trennen: still >= LEERLAUF_MS, letzte };
}

// ---------- Halten der ersten Zeile ----------
/**
 * Hält höchstens eine Zeile, bis die Sitzung steht. nehmen() gibt sie genau einmal heraus (kein Doppelversand), verwerfen() löscht sie
 * (Abbruch beim Verbinden). Der Text liegt nur im Arbeitsspeicher und wird nie geloggt.
 */
export class ZeilenHalter {
  #zeile: string | null = null;

  get hat(): boolean {
    return this.#zeile !== null;
  }

  /** false, wenn schon eine Zeile gehalten wird oder die Zeile leer ist. */
  halten(text: string): boolean {
    if (this.#zeile !== null || !text.trim()) return false;
    this.#zeile = text;
    return true;
  }

  nehmen(): string | null {
    const z = this.#zeile;
    this.#zeile = null;
    return z;
  }

  verwerfen(): void {
    this.#zeile = null;
  }
}
