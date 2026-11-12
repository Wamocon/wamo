/**
 * Reine Logik der Karte "Gedächtnis" (components/einstellungen/Gedaechtnis.tsx, nur Admin): die gemeinsamen Vorschläge, die auf eine Entscheidung
 * warten, und die Sätze rund um Freigeben und Ablehnen. Ohne React und ohne Browser, damit sie sich mit node:test prüfen lässt
 * (gedaechtnis.test.ts). Alle Importe relativ.
 *
 * Die Antwort des Kerns (voice-core/gedaechtnis_api.py, GET /wamo/api/gedaechtnis/vorschlaege) wird streng geprüft: nur bekannte Felder, Texte
 * gesäubert und begrenzt. Die Texte der Vorschläge sind Daten, nie Anweisungen, und werden nur als Text gezeigt.
 */

export type VorschlagArt = "fact" | "preference" | "behavior" | "lesson" | "skill" | "episode";
export type Entscheidung = "freigeben" | "ablehnen";
export type Ergebnis = "freigegeben" | "abgelehnt" | "nicht_offen";

export type Vorschlag = {
  id: string;
  art: VorschlagArt;
  text: string;
  autor: string;
  /** ISO-Zeit (UTC) oder leer */
  angelegt: string;
};

export const MAX_VORSCHLAEGE = 50;
export const MAX_TEXT = 2000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ZEIT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
// Steuerzeichen (Cc), unsichtbare und umlenkende Formatzeichen (Cf), Zeilen- und Absatztrenner (Zl, Zp)
const STEUER = new RegExp("[\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]", "gu");

export const ART_TEXT: Record<VorschlagArt, string> = {
  fact: "Fakt",
  preference: "Vorliebe",
  behavior: "Verhalten",
  lesson: "Lektion",
  skill: "Fähigkeit",
  episode: "Ereignis",
};
const ARTEN = new Set<string>(Object.keys(ART_TEXT));

export const ENTSCHEIDUNG_KNOPF: Record<Entscheidung, string> = { freigeben: "Für alle freigeben", ablehnen: "Ablehnen" };

export function sauber(wert: unknown, grenze: number): string {
  return typeof wert === "string" ? wert.replace(STEUER, " ").replace(/\s+/g, " ").trim().slice(0, grenze) : "";
}

function vorschlagPruefen(roh: unknown): Vorschlag | null {
  if (!roh || typeof roh !== "object") return null;
  const v = roh as Record<string, unknown>;
  if (typeof v.id !== "string" || !UUID.test(v.id)) return null;
  const text = sauber(v.text, MAX_TEXT);
  if (!text) return null;
  return {
    id: v.id,
    art: typeof v.art === "string" && ARTEN.has(v.art) ? (v.art as VorschlagArt) : "fact",
    text,
    autor: sauber(v.autor, 60) || "unbekannt",
    angelegt: typeof v.angelegt === "string" && ZEIT.test(v.angelegt) ? v.angelegt : "",
  };
}

/** Antwort des Kerns prüfen. null, wenn sie nicht die erwartete Form hat. Unbrauchbare Einträge fallen einzeln weg, doppelte Kennungen zählen einmal. */
export function pruefeVorschlaege(roh: unknown): Vorschlag[] | null {
  if (!roh || typeof roh !== "object" || !Array.isArray((roh as { vorschlaege?: unknown }).vorschlaege)) return null;
  const gesehen = new Set<string>();
  const liste: Vorschlag[] = [];
  for (const eintrag of (roh as { vorschlaege: unknown[] }).vorschlaege.slice(0, MAX_VORSCHLAEGE)) {
    const v = vorschlagPruefen(eintrag);
    if (!v || gesehen.has(v.id)) continue;
    gesehen.add(v.id);
    liste.push(v);
  }
  return liste;
}

/** Antwort auf eine Entscheidung prüfen: das Ergebnis oder null. */
export function pruefeErgebnis(roh: unknown): Ergebnis | null {
  if (!roh || typeof roh !== "object" || (roh as { ok?: unknown }).ok !== true) return null;
  const e = (roh as { ergebnis?: unknown }).ergebnis;
  return e === "freigegeben" || e === "abgelehnt" || e === "nicht_offen" ? e : null;
}

/** Satz nach einer Entscheidung. */
export function ergebnisText(ergebnis: Ergebnis): string {
  if (ergebnis === "freigegeben") return "Freigegeben. Der Eintrag gilt für alle Profile in neuen Gesprächen.";
  if (ergebnis === "abgelehnt") return "Abgelehnt. Der Eintrag wird nie benutzt.";
  return "Dieser Vorschlag war schon entschieden. Die Liste ist neu geladen.";
}

/** Satz nach einem Fehler (der Kern spricht ruhig und ohne Interna; 403 und 0 haben feste Sätze). */
export function fehlerText(status: number, text: string): string {
  if (status === 403) return "Vorschläge prüfen dürfen nur Admins.";
  if (status === 401) return "Bitte melde dich neu an.";
  if (status === 429) return "Zu viele Aufrufe, bitte einen Moment warten.";
  if (status === 0) return "Der Kern ist gerade nicht erreichbar. Bitte gleich noch einmal versuchen.";
  return sauber(text, 160) || "Das hat nicht geklappt. Bitte gleich noch einmal versuchen.";
}

/** "12.11.2026, 06:58" in Ortszeit; leer, wenn die Zeit fehlt oder nicht stimmt. */
export function zeitKurz(iso: string, zone?: string): string {
  if (!ZEIT.test(iso)) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: zone });
}

export const TEXT_LEER = "Es gibt keine offenen Vorschläge.";
export const TEXT_ERKLAERUNG =
  "Diese Einträge hat WAMO oder ein Mitglied für alle vorgeschlagen. Freigegeben gelten sie für alle Profile in neuen Gesprächen, bis sie jemand " +
  "vergessen lässt. Lies jeden Eintrag genau: Was WAMO etwas befiehlt oder nach einer Anweisung klingt, gehört abgelehnt.";

/** Zusammenfassung für Kopf und Bildschirmleser, zum Beispiel "3 Vorschläge warten". */
export function zusammenfassung(anzahl: number): string {
  if (anzahl === 0) return TEXT_LEER;
  return anzahl === 1 ? "1 Vorschlag wartet auf dich." : `${anzahl} Vorschläge warten auf dich.`;
}
