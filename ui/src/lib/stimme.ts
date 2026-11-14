/**
 * Logik der Stimmerkennung für die Oberfläche: Stand und Ergebnis der letzten Äußerung, das Modul in der Kopfzeile, die Zeilen der
 * Einstellungen und der Ablauf beim Einlernen des Stimmabdrucks (Einwilligung, Start, Aufnahme, Rechnen, Ergebnis). Der Kern prüft die
 * Stimme und speichert den Abdruck, die Oberfläche zeigt nur an und schickt die drei Aktionen start, abbrechen und loeschen (useWamo.ts).
 * Reine Funktionen ohne React und ohne Browser, damit sie sich mit node:test prüfen lassen (stimme.test.ts). Importe relativ mit Endung.
 */
import type { AbdruckStatus, StimmeErgebnis, StimmeModus, StimmeStatus } from "./nachrichten.ts";

// ---------- Stand der Stimmerkennung ----------

export type StimmeStand = {
  status: StimmeStatus | null;
  modus: StimmeModus | null;
  /** Ergebnis der letzten Äußerung, nur solange der Abdruck bereit ist */
  ergebnis: StimmeErgebnis | null;
  /** Ähnlichkeit mit dem Abdruck, 0 bis 1 */
  wert: number | null;
};

export const STIMME_LEER: StimmeStand = { status: null, modus: null, ergebnis: null, wert: null };

export function stimmeStatusAnwenden(s: StimmeStand, m: { status: StimmeStatus; modus: StimmeModus | null }): StimmeStand {
  const bleibt = m.status === "bereit";
  return { status: m.status, modus: m.modus ?? s.modus, ergebnis: bleibt ? s.ergebnis : null, wert: bleibt ? s.wert : null };
}

export function stimmeErgebnisAnwenden(
  s: StimmeStand,
  m: { ergebnis: StimmeErgebnis; wert: number | null; modus: StimmeModus | null },
): StimmeStand {
  return { ...s, ergebnis: m.ergebnis, wert: m.wert, modus: m.modus ?? s.modus };
}

/** Wert mit deutschem Komma und führendem Leerzeichen, leer ohne Wert: " 0,78". */
const wertText = (w: number | null) => (w === null ? "" : ` ${w.toFixed(2).replace(".", ",")}`);

const MODUS_TITEL: Record<StimmeModus, string> = {
  aus: "Stimmerkennung aus",
  beobachten: "Beobachten: misst nur, sperrt noch nichts",
  streng: "Nur deine Stimme darf Aufträge auslösen",
};

export type StimmeModul = {
  /** ja: eigene Stimme, warn: braucht Aufmerksamkeit, aus: ruhig */
  an: "ja" | "warn" | "aus";
  text: string;
  titel: string;
};

/** Das Modul "Stimme" in der Kopfzeile; null, wenn es nichts zu zeigen gibt (kein Stand oder Stimmerkennung aus). */
export function stimmeModul(s: StimmeStand): StimmeModul | null {
  if (!s.status || s.status === "aus") return null;
  const titel = MODUS_TITEL[s.modus ?? "beobachten"];
  if (s.status === "fehlt") return { an: "warn", text: "Nicht eingelernt", titel: "In den Einstellungen die Stimme einlernen" };
  if (s.status === "kaputt") return { an: "warn", text: "Fehler", titel: "Modell oder Abdruck konnte nicht geladen werden" };
  if (s.status === "laedt") return { an: "aus", text: "Lädt", titel };
  const wert = wertText(s.wert);
  if (s.ergebnis === "eigen") return { an: "ja", text: `Eigen${wert}`, titel };
  if (s.ergebnis === "fremd") return { an: "warn", text: `Fremd${wert}`, titel };
  if (s.ergebnis === "unklar") return { an: "aus", text: `Unklar${wert}`, titel };
  return { an: "aus", text: "Bereit", titel };
}

const STATUS_ZEILE: Record<StimmeStatus, string> = {
  aus: "Aus",
  fehlt: "Noch nicht eingelernt",
  laedt: "Wird geladen",
  kaputt: "Fehler: Modell oder Abdruck konnte nicht geladen werden",
  bereit: "Eingelernt",
};

/** Zeile "Status" in den Einstellungen. */
export const stimmeStatusText = (s: StimmeStand): string => (s.status ? STATUS_ZEILE[s.status] : "Der Kern meldet noch nichts");

const MODUS_ZEILE: Record<StimmeModus, string> = {
  aus: "Aus",
  beobachten: "Beobachten: misst nur, sperrt noch nichts",
  streng: "Streng: nur deine Stimme darf Aufträge auslösen",
};

/** Zeile "Betriebsart" in den Einstellungen. */
export const modusText = (m: StimmeModus | null): string => (m ? MODUS_ZEILE[m] : "unbekannt");

/** Zeile "Letzte Äußerung" in den Einstellungen. */
export function ergebnisText(s: StimmeStand): string {
  if (s.status !== "bereit" || !s.ergebnis) return "noch keine Äußerung gemessen";
  const name = s.ergebnis === "eigen" ? "Deine Stimme" : s.ergebnis === "fremd" ? "Fremde Stimme" : "Unklar";
  return `${name}${wertText(s.wert)}`;
}

/** Einlernen braucht eine laufende Sitzung und eine eingeschaltete Stimmerkennung, die nicht gerade lädt. */
export const kannEinlernen = (s: StimmeStand, online: boolean): boolean =>
  online && (s.status === "fehlt" || s.status === "bereit" || s.status === "kaputt");

/** Löschen gibt es nur, wenn ein Abdruck da ist (bereit oder defekt), und nur mit laufender Sitzung (der Kern löscht). */
export const kannLoeschen = (s: StimmeStand, online: boolean): boolean => online && (s.status === "bereit" || s.status === "kaputt");

// ---------- Einlernen des Stimmabdrucks ----------

/** einwilligung gibt es nur in der Oberfläche (Text vom Kern, Haken, dann Start); alle übrigen Phasen meldet der Kern. */
export type AbdruckPhase = "einwilligung" | "start" | "aufnahme" | "rechnet" | "fertig" | "fehler";

export type Abdruck = { phase: AbdruckPhase; sekunden: number; ziel: number; text?: string };

export const ABDRUCK_ZIEL = 20;

/** Die Einlernansicht öffnen: zuerst die Einwilligung. */
export const abdruckOeffnen = (): Abdruck => ({ phase: "einwilligung", sekunden: 0, ziel: ABDRUCK_ZIEL });

/** Die Oberfläche hat den Start geschickt (mit der Einwilligung): jetzt führt der Kern. */
export const abdruckGestartet = (a: Abdruck | null): Abdruck => ({ phase: "start", sekunden: 0, ziel: a?.ziel ?? ABDRUCK_ZIEL });

/** Läuft die Aufnahme oder die Berechnung, die sich mit Abbrechen beenden lässt? */
export const abdruckLaeuft = (a: Abdruck | null): boolean => a?.phase === "start" || a?.phase === "aufnahme" || a?.phase === "rechnet";

/**
 * Meldung des Kerns einarbeiten. abgebrochen und geloescht schließen die Ansicht. Ohne geöffnete Ansicht öffnet eine Meldung
 * sie (der Kern führt, die Person soll Fortschritt und Abbrechen sehen). In der Einwilligung gilt keine Meldung außer dem
 * Schließen: ohne Einwilligung geht es nicht weiter.
 */
export function abdruckAnwenden(
  a: Abdruck | null,
  m: { status: AbdruckStatus; sekunden?: number; ziel?: number; text?: string },
): Abdruck | null {
  if (m.status === "abgebrochen" || m.status === "geloescht") return null;
  if (a?.phase === "einwilligung") return a;
  return {
    phase: m.status,
    sekunden: m.sekunden ?? a?.sekunden ?? 0,
    ziel: m.ziel ?? a?.ziel ?? ABDRUCK_ZIEL,
    ...(m.text ? { text: m.text } : {}),
  };
}

/** Fortschritt des Balkens in ganzen Prozent, 0 bis 100. */
export function abdruckProzent(a: Pick<Abdruck, "sekunden" | "ziel">): number {
  if (!(a.ziel > 0)) return 0;
  return Math.min(100, Math.max(0, Math.round((a.sekunden / a.ziel) * 100)));
}

/** Abzeichen oben im Panel: an (ja) für laufend und fertig, wartet, fehler. */
export function abdruckBadge(a: Abdruck): { an: "ja" | "wartet" | "fehler"; text: string } {
  switch (a.phase) {
    case "einwilligung":
      return { an: "wartet", text: "Einwilligung" };
    case "start":
      return { an: "wartet", text: "Gleich" };
    case "aufnahme":
      return { an: "ja", text: `${Math.floor(a.sekunden)} / ${Math.round(a.ziel)} s` };
    case "rechnet":
      return { an: "wartet", text: "Rechnet" };
    case "fertig":
      return { an: "ja", text: "Fertig" };
    case "fehler":
      return { an: "fehler", text: "Fehler" };
  }
}

const PHASE_TEXT: Record<Exclude<AbdruckPhase, "einwilligung">, string> = {
  start: "WAMO sagt kurz, worum es geht. Danach einfach loslesen.",
  aufnahme: "Ich höre zu. Lies einfach weiter, Pausen sind kein Problem.",
  rechnet: "Fast fertig, ich rechne den Abdruck aus.",
  fertig: "Fertig. WAMO erkennt jetzt deine Stimme.",
  fehler: "Das hat nicht geklappt.",
};

/** Statuszeile unter dem Balken; bei einem Fehler gilt der Satz des Kerns, wenn er einen mitschickt. */
export function abdruckStatusText(a: Abdruck): string {
  if (a.phase === "einwilligung") return "Bitte lies den Text und bestätige ihn, dann geht es los.";
  return a.phase === "fehler" && a.text ? a.text : PHASE_TEXT[a.phase];
}

/** Ansage für Screenreader, wenn das Einlernen endet (role="status" der Seite). */
export function abdruckAnsage(status: AbdruckStatus): string | null {
  if (status === "fertig") return "Stimme eingelernt";
  if (status === "fehler") return "Stimme einlernen fehlgeschlagen";
  if (status === "geloescht") return "Stimmabdruck gelöscht";
  if (status === "abgebrochen") return "Stimme einlernen abgebrochen";
  return null;
}

/** "Der Nordwind und die Sonne" (Äsop), der übliche Text für Sprachaufnahmen mit vielen deutschen Lauten. */
export const VORLESETEXT =
  "Einst stritten sich Nordwind und Sonne, wer von ihnen beiden wohl der Stärkere wäre, als ein Wanderer, " +
  "der in einen warmen Mantel gehüllt war, des Weges daherkam. Sie wurden einig, dass derjenige für den " +
  "Stärkeren gelten sollte, der den Wanderer zwingen würde, seinen Mantel abzunehmen. Der Nordwind blies mit " +
  "aller Macht, aber je mehr er blies, desto fester hüllte sich der Wanderer in seinen Mantel ein. Endlich " +
  "gab der Nordwind den Kampf auf. Nun erwärmte die Sonne die Luft mit ihren freundlichen Strahlen, und schon " +
  "nach wenigen Augenblicken zog der Wanderer seinen Mantel aus. Da musste der Nordwind zugeben, dass die " +
  "Sonne von ihnen beiden der Stärkere war.";
