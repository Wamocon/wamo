import {
  parseServerMessage,
  type Vorgang,
  type VorgangArt,
  type VorgangKriterium,
  type VorgangSchritt,
  type VorgangSchrittStatus,
  type VorgangStatus,
} from "./nachrichten.ts"; // relativ, damit node:test diese Datei laden kann

/**
 * Hilfen für die Tafel "Vorgänge" (components/kommando/Vorgaenge.tsx): die Liste führen, Prozent, Zeiten und Status als Text.
 * Alles hier ist rein (keine Uhr, kein DOM), damit es sich ohne Browser prüfen lässt (vorgaenge.test.ts).
 */

/** So viele Vorgänge hält die Oberfläche je Sitzung; zuerst fällt der älteste beendete heraus, nie ein laufender. */
export const MAX_VORGAENGE = 30;
/** So viele Vorgänge liefert die Schnittstelle des Kerns für den Verlauf (voice-core/vorgaenge_api.py) höchstens. */
export const MAX_VERLAUF = 20;
/** So viele beendete Vorgänge zeigt "Zuletzt beendet" höchstens (Sitzung und Verlauf zusammen). */
export const MAX_BEENDET = 25;

const ENDE: readonly VorgangStatus[] = ["fertig", "fehler", "abgebrochen"];
export const istBeendet = (v: Vorgang) => ENDE.includes(v.status);

/** Status als Wort: Farbe und Form allein tragen keine Bedeutung (Barrierefreiheit). */
export const statusText: Record<VorgangStatus, string> = {
  geplant: "geplant",
  laeuft: "läuft",
  wartet_freigabe: "wartet auf Freigabe",
  pruefen: "wird geprüft",
  fertig: "fertig",
  fehler: "fehlgeschlagen",
  abgebrochen: "abgebrochen",
};

export const schrittStatusText: Record<VorgangSchrittStatus, string> = {
  offen: "offen",
  laeuft: "läuft",
  fertig: "erledigt",
  fehler: "Fehler",
  uebersprungen: "übersprungen",
  wartet_freigabe: "wartet auf Freigabe",
};

/** Gesamtfortschritt (0 bis 1) als ganze Prozent, 0 bis 100. */
export function prozent(gesamt: number): number {
  return Number.isFinite(gesamt) ? Math.round(Math.max(0, Math.min(1, gesamt)) * 100) : 0;
}

/** Dauer als mm:ss; ab einer Stunde zählen die Minuten weiter (62:05). */
export function dauerText(ms: number): string {
  const gesamt = Number.isFinite(ms) && ms > 0 ? Math.floor(ms / 1000) : 0;
  const mm = String(Math.floor(gesamt / 60)).padStart(2, "0");
  const ss = String(gesamt % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/** "Fertig in mm:ss", nur bei einem fertigen Vorgang mit bekanntem Ende. */
export function fertigText(v: Vorgang): string | null {
  if (v.status !== "fertig" || v.beendetMs === null) return null;
  return `Fertig in ${dauerText(Math.max(0, v.beendetMs - v.gestartetMs))}`;
}

/** Restzeit nur, solange gearbeitet wird; ohne Schätzung steht ein Hinweis, nie eine erfundene Zahl. */
export function restzeitText(v: Vorgang): string {
  if (v.status !== "laeuft" && v.status !== "pruefen") return "";
  if (v.restzeitS === null) return "Restzeit wird geschätzt";
  return v.restzeitS === 0 ? "gleich fertig" : `noch etwa ${dauerText(v.restzeitS * 1000)}`;
}

/** Text für aria-valuetext des Gesamtbalkens: Prozent, Runde, Restzeit oder Status. */
export function fortschrittText(v: Vorgang): string {
  const teile = [`${prozent(v.gesamt)} Prozent`];
  if (v.runde !== null) teile.push(v.maxRunden !== null ? `Runde ${v.runde} von ${v.maxRunden}` : `Runde ${v.runde}`);
  teile.push(fertigText(v) ?? (v.status === "laeuft" || v.status === "pruefen" ? restzeitText(v) : statusText[v.status]));
  return teile.join(", ");
}

/**
 * Den Vorgang gleicher Kennung ersetzen (an seiner Stelle) oder hinten anfügen. Ändert sich nichts, kommt dieselbe Liste
 * zurück (kein Neurendern). Über der Grenze fällt der älteste beendete heraus; laufende bleiben immer.
 */
export function vorgangEinordnen(liste: Vorgang[], neu: Vorgang): Vorgang[] {
  const i = liste.findIndex((v) => v.id === neu.id);
  if (i >= 0) {
    if (JSON.stringify(liste[i]) === JSON.stringify(neu)) return liste;
    const kopie = [...liste];
    kopie[i] = neu;
    return kopie;
  }
  const kopie = [...liste, neu];
  while (kopie.length > MAX_VORGAENGE) {
    const alt = kopie.findIndex(istBeendet);
    if (alt < 0) break;
    kopie.splice(alt, 1);
  }
  return kopie;
}

/** Aktive (in der Reihenfolge des Eingangs) und beendete Vorgänge (der zuletzt beendete zuerst). */
export function aufgeteilt(liste: Vorgang[]): { aktiv: Vorgang[]; beendet: Vorgang[] } {
  return {
    aktiv: liste.filter((v) => !istBeendet(v)),
    beendet: liste.filter(istBeendet).sort((a, b) => (b.beendetMs ?? 0) - (a.beendetMs ?? 0)),
  };
}

/** Die Verbindung endet (der Kern bricht seine Vorgänge ab): aktive gelten als abgebrochen, ihre Freigabekarte verfällt mit ihr. */
export function vorgaengeBeenden(liste: Vorgang[], jetztMs: number): Vorgang[] {
  if (!liste.some((v) => !istBeendet(v))) return liste;
  return liste.map((v) =>
    istBeendet(v) ? v : { ...v, status: "abgebrochen" as const, beendetMs: jetztMs, restzeitS: null, freigabeId: null },
  );
}

/** Ansage für Screenreader (role="status"): nur bedeutsame Wechsel, nie jeder Fortschritt. */
export function vorgangAnsage(alt: Vorgang | undefined, neu: Vorgang): string | null {
  if (!alt) return neu.status === "laeuft" ? `Neuer Vorgang: ${neu.titel}` : null;
  if (alt.status === neu.status) return null;
  switch (neu.status) {
    case "wartet_freigabe":
      return `Vorgang ${neu.titel} wartet auf Freigabe`;
    case "fertig": {
      const zeit = fertigText(neu);
      return `Vorgang ${neu.titel} fertig${zeit ? `. ${zeit}` : ""}`;
    }
    case "fehler":
      return `Vorgang ${neu.titel} fehlgeschlagen`;
    case "abgebrochen":
      return `Vorgang ${neu.titel} abgebrochen`;
    default:
      return null;
  }
}

/**
 * Endung des Attributs data-approval der Freigabekarte (components/Freigaben.tsx: "ap-<job>-<id>", alles außer a-z, 0-9, _ und -
 * wird zu _). So findet der Verweis aus dem Vorgang die vorhandene Karte, ohne einen Selektor aus Netzdaten zu bauen.
 */
export function freigabeKartenSuffix(freigabeId: string | null): string | null {
  return freigabeId ? `-${freigabeId.replace(/[^a-zA-Z0-9_-]/g, "_")}` : null;
}

/** CSS-Selektor der Freigabekarte zu diesem Vorgang. Die Kennung ist vorher auf a-z, 0-9, _ und - reduziert, der Selektor also sicher. */
export function freigabeKartenSelektor(freigabeId: string | null): string | null {
  const suffix = freigabeKartenSuffix(freigabeId);
  return suffix ? `[data-approval$="${suffix}"]` : null;
}

// ------------------------------------------------------------------------------------------------ Texte der Karte

export const artText: Record<VorgangArt, string> = {
  aufgabe: "Aufgabe",
  entwicklung: "Entwicklung",
  geraet: "Gerät",
  sonstiges: "Vorgang",
};

/** "Runde 2 von 3", nur "Runde 2" ohne Obergrenze, sonst leer. */
export function rundenText(v: Vorgang): string {
  if (v.runde === null) return "";
  return v.maxRunden !== null ? `Runde ${v.runde} von ${v.maxRunden}` : `Runde ${v.runde}`;
}

/** Zeitangabe unter dem Gesamtbalken: "Fertig in mm:ss" oder die Restzeit; sonst leer (der Status steht als Wort daneben). */
export function zeitZeile(v: Vorgang): string {
  return fertigText(v) ?? restzeitText(v);
}

/** Zeile eines beendeten Vorgangs: "Fertig in mm:ss", sonst der Status als Wort. */
export function endeText(v: Vorgang): string {
  return fertigText(v) ?? statusText[v.status];
}

/** Prüfkriterium als Wort: Haken, Kreuz und Kreis tragen die Bedeutung nie allein. */
export function kriteriumText(k: VorgangKriterium): string {
  if (k.erfuellt === true) return "erfüllt";
  if (k.erfuellt === false) return "nicht erfüllt";
  return "noch offen";
}

/** Form der Marke vor einem Prüfkriterium (das Wort daneben steht in kriteriumText). */
export function kriteriumMarke(k: VorgangKriterium): MarkenArt {
  if (k.erfuellt === true) return "fertig";
  if (k.erfuellt === false) return "fehler";
  return "offen";
}

/** aria-valuetext eines Schrittbalkens: Prozent und Zustand des Schritts. */
export function schrittFortschrittText(s: VorgangSchritt): string {
  return `${prozent(s.fortschritt)} Prozent, ${schrittStatusText[s.status]}`;
}

/** Form der Statusmarke (Symbol zusätzlich zur Farbe): Ring mit Bogen, Haken, Pause, "!", leerer Ring, durchgestrichen. */
export type MarkenArt = "laeuft" | "fertig" | "wartet" | "fehler" | "offen" | "aus";

export function markeArt(status: VorgangStatus | VorgangSchrittStatus): MarkenArt {
  switch (status) {
    case "laeuft":
    case "pruefen":
      return "laeuft";
    case "fertig":
      return "fertig";
    case "fehler":
      return "fehler";
    case "wartet_freigabe":
      return "wartet";
    case "abgebrochen":
    case "uebersprungen":
      return "aus";
    default:
      return "offen";
  }
}

/** Farbe des Balkens: Rot bei Arbeit und Ergebnis, Grau bei Warten und Stillstand. */
export function balkenTon(status: VorgangStatus): "aktiv" | "still" {
  return status === "laeuft" || status === "pruefen" || status === "fertig" || status === "fehler" ? "aktiv" : "still";
}

// ------------------------------------------------------------------------------------------------ Verlauf aus der Datenbank

/**
 * Ein Vorgang aus dem Verlauf (Datenbank): hatte er kein Ende, lief der Kern nicht mehr (Absturz, Neustart). Er gilt dann als
 * abgebrochen und steht nie unter den aktiven; Freigabe und Restzeit gehören einer Sitzung, die es nicht mehr gibt.
 */
export function alsVerlauf(v: Vorgang): Vorgang {
  return istBeendet(v) ? v : { ...v, status: "abgebrochen", beendetMs: null, restzeitS: null, freigabeId: null };
}

/**
 * Antwort von GET /wamo/api/vorgaenge ({vorgaenge: [...]} in der Form der Servernachricht) als Liste für den Verlauf. Jede Zeile
 * läuft durch dieselbe Prüfung wie eine Nachricht des Kerns; Kaputtes fällt weg, Doppelte auch. Eine unbrauchbare Antwort
 * ergibt eine leere Liste (die Tafel zeigt dann einfach keinen Verlauf).
 */
export function vorgaengeAusAntwort(daten: unknown): Vorgang[] {
  const roh = typeof daten === "object" && daten !== null ? (daten as { vorgaenge?: unknown }).vorgaenge : null;
  if (!Array.isArray(roh)) return [];
  const liste: Vorgang[] = [];
  for (const zeile of roh.slice(0, MAX_VERLAUF)) {
    const m = parseServerMessage({ type: "vorgang", vorgang: zeile });
    if (m && m.type === "vorgang" && !liste.some((v) => v.id === m.vorgang.id)) liste.push(alsVerlauf(m.vorgang));
  }
  return liste;
}

const endeOderStart = (v: Vorgang) => v.beendetMs ?? v.gestartetMs;

/**
 * Was die Tafel zeigt: aktive Vorgänge nur aus der Sitzung (der Verlauf kann nichts Laufendes enthalten), beendete aus Sitzung und
 * Verlauf, der zuletzt beendete zuerst. Dieselbe Kennung gilt aus der Sitzung, der Verlauf überschreibt nie.
 */
export function tafelListen(sitzung: Vorgang[], verlauf: Vorgang[]): { aktiv: Vorgang[]; beendet: Vorgang[] } {
  const { aktiv, beendet } = aufgeteilt(sitzung);
  const bekannt = new Set(sitzung.map((v) => v.id));
  const alle = [...beendet, ...verlauf.filter((v) => !bekannt.has(v.id))];
  alle.sort((a, b) => endeOderStart(b) - endeOderStart(a));
  return { aktiv, beendet: alle.slice(0, MAX_BEENDET) };
}
