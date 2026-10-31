/**
 * Texteingabe im Gespräch (Eingabepille in components/kommando/Bedienung.tsx): reine Logik ohne Browser, damit sie sich
 * testen lässt (textEingabe.test.ts).
 *
 * Sicherheitsregeln:
 *  - Ein Schlüssel (sk-ant-…, auch in gesprochener Schreibweise) verlässt den Browser nie. Er wird vor dem Senden
 *    ersetzt, deshalb steht er weder im Verlauf noch in den Untertiteln noch in einer Meldung. Der Kern filtert
 *    getippten Text zusätzlich (voice-core/schluessel_filter.py), die Oberfläche verlässt sich aber nicht darauf.
 *  - Der Text selbst wird nie geloggt.
 *  - Getippter Text ist eine gewöhnliche Nutzernachricht. Freigaben und Aufträge gehen nur über die Karten
 *    (useWamo: decide, entscheideAuftrag), ein getipptes "Ja" ersetzt sie nie.
 */

/** Höchstlänge einer Nachricht (auch das Feld der Eingabepille). */
export const MAX_TEXT = 500;

export const SCHLUESSEL_MARKE = "[Schlüssel entfernt]";

// Gleiche Erkennung wie der Kern (voice-core/schluessel_filter.py): genaue Form und gesprochene Form
const GENAU = /sk-ant-[A-Za-z0-9_-]{6,}/gi;
const SEP = "(?:[\\s\\-_–—.]|strich|minus|bindestrich|dash){0,3}";
const SKANT = `\\bs${SEP}k${SEP}a${SEP}n${SEP}t`;
const ART = `(?:api|oat|a${SEP}p${SEP}i|o${SEP}a${SEP}t)`;
const LOCKER = new RegExp(`${SKANT}${SEP}${ART}${SEP}\\d{0,2}(?:${SEP}[A-Za-z0-9]{1,6}){3,60}`, "gi");
const ANFANG = new RegExp(`${SKANT}${SEP}${ART}\\w*`, "gi");

/** Ersetzt Schlüssel im Text durch die Marke. entfernt: es wurde etwas ersetzt. */
export function schluesselEntfernen(text: string): { text: string; entfernt: boolean } {
  if (!text) return { text, entfernt: false };
  const neu = text.replace(GENAU, SCHLUESSEL_MARKE).replace(LOCKER, SCHLUESSEL_MARKE).replace(ANFANG, SCHLUESSEL_MARKE);
  return { text: neu, entfernt: neu !== text };
}

export type VorbereiteterText = {
  /** Text für den Kern und die Anzeige (ohne Schlüssel, getrimmt, höchstens MAX_TEXT Zeichen) */
  text: string;
  /** ein Schlüssel wurde ersetzt */
  geschwaerzt: boolean;
  /** Ist nach dem Ersetzen noch etwas zu sagen? Sonst (nur ein Schlüssel) wird nichts gesendet. */
  senden: boolean;
};

/**
 * Macht aus der Eingabe die Nachricht: Steuerzeichen und Zeilenumbrüche werden zu Leerzeichen, Schlüssel ersetzt, auf
 * MAX_TEXT begrenzt. null bei leerer Eingabe.
 */
export function bereiteText(roh: string): VorbereiteterText | null {
  // Obergrenze vor dem Filtern: der Filter soll nie unbegrenzt lange Eingaben prüfen müssen
  const eingabe = roh.slice(0, MAX_TEXT * 4).replace(/[\p{Cc}\u2028\u2029]+/gu, " ").trim();
  if (!eingabe) return null;
  const { text, entfernt } = schluesselEntfernen(eingabe);
  const gekuerzt = text.slice(0, MAX_TEXT).trim();
  // Ein abgeschnittenes Stück der Marke ist keine Nachricht
  const rest = gekuerzt.split(SCHLUESSEL_MARKE).join("").replace(/[\s.,;:!?-]+/g, "");
  return { text: gekuerzt, geschwaerzt: entfernt, senden: rest.length > 0 };
}

/** Meldung, wenn ein Schlüssel ersetzt wurde (nennt nie den Wert). */
export function schluesselHinweis(senden: boolean): string {
  return senden
    ? "In deiner Nachricht stand ein Schlüssel. Er wurde entfernt und nicht gesendet, der Rest ging an WAMO. Schlüssel gehören in die Einstellungen unter Modellzugang."
    : "Das war ein Schlüssel. Er wurde nicht gesendet. Schlüssel gehören in die Einstellungen unter Modellzugang.";
}

/** Alles, was das Senden braucht; useWamo verdrahtet es mit dem Client, der Test mit Attrappen. */
export type TextWege = {
  /** besteht eine Verbindung und nimmt der Kern Text an? */
  bereit: () => "ok" | "offline" | "kein_text";
  /** läuft schon eine Sendung? */
  besetzt: () => boolean;
  setzeBesetzt: (besetzt: boolean) => void;
  /** Text an den Kern (nur schon von Schlüsseln befreit) */
  senden: (text: string) => Promise<unknown>;
  /** Meldung ohne Fehlercharakter (Schlüssel entfernt) */
  hinweis: (text: string) => void;
  fehler: (art: "verbindung" | "dienst" | "sonst", text: string) => void;
  /** Der Kern hat die Nachricht angenommen: in die Anzeige übernehmen */
  angenommen: (text: string) => void;
  /** Gehört der Rückruf noch zur selben Verbindung? */
  aktuell: () => boolean;
};

/**
 * Eine getippte Nachricht abschicken. true, wenn die Eingabe angenommen wurde (das Feld wird geleert), false, wenn sie
 * stehen bleiben soll (keine Verbindung, vorige Sendung läuft noch, leer). Außer dem bereinigten Text sieht keine Stelle
 * etwas vom Eingegebenen: weder Anzeige noch Meldungen noch Protokoll.
 */
export function textAbschicken(roh: string, w: TextWege): boolean {
  const bereit = w.bereit();
  if (bereit === "offline") {
    w.fehler("verbindung", "Keine Verbindung: Die Nachricht konnte nicht gesendet werden.");
    return false;
  }
  if (bereit === "kein_text") {
    w.fehler("dienst", "Dieser Kern nimmt keinen getippten Text an. Bitte sprich mit WAMO.");
    return false;
  }
  if (w.besetzt()) return false; // die vorige Nachricht ist noch unterwegs: das Feld behält den Text
  const t = bereiteText(roh);
  if (!t) return false;
  if (t.geschwaerzt) w.hinweis(schluesselHinweis(t.senden));
  if (!t.senden) return true; // nur ein Schlüssel: nichts senden, das Feld wird trotzdem geleert
  w.setzeBesetzt(true);
  w.senden(t.text)
    .then(
      () => {
        if (w.aktuell()) w.angenommen(t.text);
      },
      () => {
        if (w.aktuell()) w.fehler("sonst", "Die Nachricht konnte nicht gesendet werden. Bitte noch einmal versuchen.");
      },
    )
    .finally(() => w.setzeBesetzt(false));
  return true;
}

/**
 * Nimmt der Kern getippten Text an? Die Protokollversion (RTVI) steht in der Meldung "Bot bereit". Text senden
 * (send-text) kennt Protokoll 2 und neuer, bei fehlender oder unlesbarer Angabe bleibt das Feld gesperrt.
 */
export function unterstuetztText(version: unknown): boolean {
  if (typeof version !== "string") return false;
  const m = /^(\d{1,3})\.\d{1,3}(?:\.\d{1,3})?/.exec(version.trim());
  return m !== null && Number(m[1]) >= 2;
}

/** Was gerade zu beachten ist, wenn jemand tippt (für den Hinweis unter der Eingabepille). */
export type EingabeLage = {
  /** offene Freigaben und Entwicklungsaufträge */
  entscheidungen: number;
  /** ein Werkzeug läuft gerade */
  werkzeugLaeuft: boolean;
};

/** Kurzer Hinweis unter der Eingabepille oder null. Freigaben haben Vorrang, denn sie sind sicherheitsrelevant. */
export function eingabeHinweis(l: EingabeLage): string | null {
  if (l.entscheidungen > 0) {
    return "Freigaben gibst du nur mit Klick auf die Karte. Ein getipptes Ja ersetzt sie nicht.";
  }
  if (l.werkzeugLaeuft) return "WAMO arbeitet gerade. Eine neue Nachricht unterbricht die Arbeit.";
  return null;
}
