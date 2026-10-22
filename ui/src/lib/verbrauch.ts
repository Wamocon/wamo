import type { Kontingent, KontingentLimit } from "./nachrichten.ts";

/**
 * Anzeigemodell für den Abschnitt "Verbrauch" (Kontingent des Max-Abos, Servernachricht "kontingent").
 *
 * FORMAT UNGEPRÜFT: Der Kern liefert die Werte aus dem Ereignis rate_limit_event von Claude Code (voice-core/kontingent.py,
 * geprüft nur gegen eine API-Attrappe, nicht gegen einen echten Token). Was die Oberfläche von dort bekommt, ist schon
 * geprüft und umgerechnet (lib/nachrichten.ts): prozent ist der genutzte Anteil in Prozent von 0 bis 1000 (über 100
 * kommt vor), endet ist ein ISO-Zeitpunkt in UTC. Jedes Feld außer art kann fehlen. Deshalb ist dieses Modell tolerant:
 * fehlt etwas, zeigt die Zeile "keine Angabe", statt zu raten; es gibt keine Umrechnung von 0 bis 1 auf Prozent, weil
 * der Kern sie schon gemacht hat. Weicht das echte Format später ab, ändert sich nur voice-core/kontingent.py, und die
 * Kennzeichnung VERBRAUCH_FORMAT_GEPRUEFT wird erst dann auf true gesetzt.
 */
export const VERBRAUCH_FORMAT_GEPRUEFT = false;

export const VERBRAUCH_LEER =
  "Noch keine Angaben. Sie erscheinen nach dem ersten Entwicklungsauftrag mit deinem Claude-Code-Token.";

export const VERBRAUCH_FORMAT_HINWEIS =
  "Hinweis: Das Datenformat ist noch nicht an einem echten Token geprüft. Die Werte können abweichen.";

type Art = KontingentLimit["art"];

const ARTEN: Record<Art, { titel: string; erklaerung: string }> = {
  sitzung: { titel: "Sitzungslimit", erklaerung: "Fenster von 5 Stunden" },
  woche: { titel: "Wochenlimit", erklaerung: "alle Modelle, Fenster von 7 Tagen" },
  fable: { titel: "Fable limit", erklaerung: "modellbezogenes Wochenlimit des größten Modells" },
  opus: { titel: "Opus-Wochenlimit", erklaerung: "modellbezogenes Wochenlimit" },
  sonnet: { titel: "Sonnet-Wochenlimit", erklaerung: "modellbezogenes Wochenlimit" },
};

/** Diese drei zeigt die Seite immer, sobald es überhaupt Angaben gibt (fehlt eine, steht "keine Angabe" da). */
export const STANDARD_ARTEN: readonly Art[] = ["sitzung", "woche", "fable"];
const ALLE_ARTEN: readonly Art[] = ["sitzung", "woche", "opus", "sonnet", "fable"];

const STATUS_TEXT = { ok: "im Rahmen", warnung: "Warnung, das Limit ist bald erreicht", erreicht: "Limit erreicht" } as const;

export type Zuruecksetzen = {
  /** "heute, 15:00 Uhr", "morgen, 09:00 Uhr" oder "Samstag, 24.10.2026, 09:00 Uhr" in der Zeitzone des Geräts */
  absolut: string;
  /** "in 2 Stunden und 5 Minuten" oder, wenn der Zeitpunkt vorbei ist, "vor 3 Minuten" */
  relativ: string;
  /** der Zeitpunkt liegt in der Vergangenheit: der Wert stammt aus dem alten Fenster */
  vorbei: boolean;
};

export type VerbrauchZeile = {
  art: Art;
  titel: string;
  erklaerung: string;
  /** false: für diese Art hat der Kern nichts gemeldet */
  gemeldet: boolean;
  prozent: number | null;
  /** "42 %" oder "keine Angabe" */
  prozentText: string;
  /** Füllung des Balkens, 0 bis 100 (über 100 Prozent bleibt der Balken voll) */
  balken: number;
  status: KontingentLimit["status"] | null;
  statusText: string | null;
  zuruecksetzen: Zuruecksetzen | null;
};

export type VerbrauchAnzeige = {
  /** true: es gibt keine verwertbaren Angaben, die Seite zeigt den Leerzustand */
  leer: boolean;
  zeilen: VerbrauchZeile[];
  /** wann der Kern den Stand zuletzt gespeichert hat, z. B. "heute, 11:59 Uhr" */
  stand: string | null;
  formatGeprueft: boolean;
};

const zweistellig = (n: number) => String(n).padStart(2, "0");

function mehrzahl(n: number, einzahl: string, vielzahl: string): string {
  return `${n} ${n === 1 ? einzahl : vielzahl}`;
}

/** Dauer im Dativ für "in …" und "vor …", höchstens zwei Einheiten: "3 Tagen und 4 Stunden", "2 Stunden und 5 Minuten". */
export function dauerText(ms: number): string {
  const minuten = Math.max(0, Math.floor(ms / 60_000));
  if (minuten < 1) return "weniger als einer Minute";
  const tage = Math.floor(minuten / 1440);
  const stunden = Math.floor((minuten % 1440) / 60);
  const rest = minuten % 60;
  const teile =
    tage > 0
      ? [mehrzahl(tage, "Tag", "Tagen"), stunden > 0 ? mehrzahl(stunden, "Stunde", "Stunden") : ""]
      : stunden > 0
        ? [mehrzahl(stunden, "Stunde", "Stunden"), rest > 0 ? mehrzahl(rest, "Minute", "Minuten") : ""]
        : [mehrzahl(rest, "Minute", "Minuten")];
  return teile.filter(Boolean).join(" und ");
}

type Teile = { y: number; m: number; d: number; wochentag: string; stunde: string; minute: string };

function teileIn(datum: Date, zone: string | undefined): Teile {
  const f = new Intl.DateTimeFormat("de-DE", {
    timeZone: zone,
    weekday: "long",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const p: Record<string, string> = {};
  for (const t of f.formatToParts(datum)) p[t.type] = t.value;
  return { y: Number(p.year), m: Number(p.month), d: Number(p.day), wochentag: p.weekday, stunde: p.hour, minute: p.minute };
}

/**
 * Zeitpunkt (ISO 8601) für Menschen in der Zeitzone des Geräts (zone nur für Tests). null bei ungültiger Eingabe.
 * "heute, 15:00 Uhr" und "morgen, 09:00 Uhr" sind kurz, weiter weg steht Wochentag und Datum.
 */
export function zeitpunktText(iso: string, jetzt: number, zone?: string): { absolut: string; relativ: string; vorbei: boolean } | null {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return null;
  const t = teileIn(new Date(ms), zone);
  const heute = teileIn(new Date(jetzt), zone);
  const tage = Math.round((Date.UTC(t.y, t.m - 1, t.d) - Date.UTC(heute.y, heute.m - 1, heute.d)) / 86_400_000);
  const uhr = `${t.stunde}:${t.minute} Uhr`;
  const absolut =
    tage === 0
      ? `heute, ${uhr}`
      : tage === 1
        ? `morgen, ${uhr}`
        : tage === -1
          ? `gestern, ${uhr}`
          : `${t.wochentag}, ${zweistellig(t.d)}.${zweistellig(t.m)}.${t.y}, ${uhr}`;
  const diff = ms - jetzt;
  const vorbei = diff <= 0;
  return { absolut, relativ: vorbei ? `vor ${dauerText(-diff)}` : `in ${dauerText(diff)}`, vorbei };
}

/** Zahl mit höchstens einer Nachkommastelle und deutschem Komma. */
const zahlText = (n: number): string => String(Math.round(n * 10) / 10).replace(".", ",");

function zeile(art: Art, l: KontingentLimit | undefined, jetzt: number, zone: string | undefined): VerbrauchZeile {
  const info = ARTEN[art];
  const p = l?.prozent;
  const prozent = typeof p === "number" && Number.isFinite(p) && p >= 0 ? p : null;
  const status = l?.status ?? null;
  return {
    art,
    titel: info.titel,
    erklaerung: info.erklaerung,
    gemeldet: l !== undefined,
    prozent,
    prozentText: prozent === null ? "keine Angabe" : `${zahlText(prozent)} %`,
    balken: prozent === null ? (status === "erreicht" ? 100 : 0) : Math.min(100, prozent),
    status,
    statusText: status ? STATUS_TEXT[status] : null,
    zuruecksetzen: l?.endet ? zeitpunktText(l.endet, jetzt, zone) : null,
  };
}

/**
 * Anzeigemodell aus dem Stand des Kerns. Leer, wenn es keinen Stand gibt, der Zustand unbekannt ist oder kein Limit
 * etwas Verwertbares trägt. Sonst die drei Standardarten (auch ohne Angabe) und dazu jede weitere Art, die der Kern
 * gemeldet hat, in fester Reihenfolge.
 */
export function verbrauchAnzeige(k: Kontingent | null, jetzt: number, zone?: string): VerbrauchAnzeige {
  const limits = k && k.zustand === "bekannt" ? k.limits.filter((l) => l.prozent !== undefined || l.endet !== undefined || l.status !== undefined) : [];
  if (limits.length === 0) return { leer: true, zeilen: [], stand: null, formatGeprueft: VERBRAUCH_FORMAT_GEPRUEFT };
  const nachArt = new Map(limits.map((l) => [l.art, l]));
  const arten = ALLE_ARTEN.filter((a) => STANDARD_ARTEN.includes(a) || nachArt.has(a));
  const stand = k?.stand ? (zeitpunktText(k.stand, jetzt, zone)?.absolut ?? null) : null;
  return {
    leer: false,
    zeilen: arten.map((a) => zeile(a, nachArt.get(a), jetzt, zone)),
    stand,
    formatGeprueft: VERBRAUCH_FORMAT_GEPRUEFT,
  };
}
