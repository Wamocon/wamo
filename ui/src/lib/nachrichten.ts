import type { CanvasItem } from "@/components/Canvas";

/**
 * RTVI-Servernachrichten des Voice-Core (kommen über onServerMessage in useWamo.ts an).
 * Die Daten stammen aus dem Netz: jede Nachricht wird hier geprüft, unbekannte oder
 * unvollständige Nachrichten ergeben null und werden ignoriert. Zusatzfelder, die der Kern
 * vielleicht später schickt (id für "Rückgängig", Latenz, Cloud oder im Haus), sind optional.
 */

export type Rolle = "admin" | "mitglied";

export type Profil = { name: string; rolle: Rolle };

export type Betrieb = {
  modus: "lokal" | "buero";
  modelle: { llm?: string; stt?: string; tts?: string };
  /** verbindet: Kern baut die Verbindung zum PC-Host gerade auf (Sitzungsbeginn) */
  pc: "verbindet" | "verbunden" | "getrennt" | "aus" | "gesperrt";
  /** optional: wohin die Stimme geht */
  ort?: "cloud" | "haus";
  /** optional: Antwortzeit des PC-Hosts in Millisekunden */
  pcLatenzMs?: number;
  /** PC-Steuerung dieser Sitzung (Schalter nur für admin); fehlt = aus */
  pcSteuerung?: "an" | "aus";
  /** Entwicklungsaufträge erlaubt? "aus" = gesperrt */
  entwicklung?: "an" | "aus";
  /** optional: Rechnername des gesteuerten PCs (Büro: der PC am anderen Ende des Tunnels) */
  pcName?: string;
  /** Einschalten abgelehnt, weil eine andere Sitzung den PC gerade steuert */
  pcBelegt?: boolean;
  /** wirksamer Modus dieser Sitzung (WAMO Boss): nur_haus gilt ohne eigenen API-Schlüssel, bei Zwang des Admins und
   *  bei der Wahl der Person; sonst wählt der Boss unter allen erlaubten Modellen */
  modellModus?: ModellModus;
  /** warum dieser Modus gilt (festgelegt: Admin, wahl: Person, keiner: kein eigener Schlüssel, ...) */
  modellQuelle?: ModellQuelle;
  /** warum der Boss nicht das beste Modell nehmen konnte (zu langsam, nicht erreichbar) */
  modellHinweis?: string;
};

/** Modellwahl je Profil: Automatisch (der Boss wählt) oder Nur Hausmodelle */
export type ModellModus = "automatisch" | "nur_haus";
export type ModellQuelle = "profil" | "zuordnung" | "festgelegt" | "wahl" | "keiner" | "fehler" | "betreiber";
const MODELL_MODI = ["automatisch", "nur_haus"] as const;
const MODELL_QUELLEN = ["profil", "zuordnung", "festgelegt", "wahl", "keiner", "fehler", "betreiber"] as const;

export type Gemerkt = {
  bereich: "persoenlich" | "gemeinsam";
  status: "aktiv" | "vorgeschlagen";
  text: string;
  /** optional: Kennung des Eintrags, nur dann gibt es "Rückgängig" */
  id?: string;
};

/**
 * Modellzugang des Profils (voice-core/modellzugang.py): eigener Claude-Schlüssel oder nur Hausmodelle.
 * Enthält nie den Schlüssel, nur ob einer hinterlegt ist und seine letzten vier Zeichen.
 */
export type Modellzugang = {
  aktion: "status" | "hinterlegen" | "loeschen" | "modus";
  /** fehlt, wenn der Kern den Stand gerade nicht lesen konnte (dann steht nur fehler da) */
  art?: "claude" | "haus";
  /** profil: eigener Schlüssel, zuordnung: Schlüssel des Kerns für genau dieses Profil, festgelegt: vom Admin,
   *  wahl: von der Person gewählt (nur Hausmodelle) */
  quelle?: "profil" | "zuordnung" | "festgelegt" | "wahl" | "keiner" | "fehler";
  /** eigener API-Schlüssel (Gespräch) hinterlegt */
  hinterlegt: boolean;
  ende?: string;
  geprueft?: string;
  /** ein Admin hat nur Hausmodelle festgelegt: die Wahl der Person ist gesperrt */
  festgelegt: boolean;
  modell?: string;
  /** Wahl der Person, unabhängig vom Zwang des Admins; fehlt, wenn der Kern sie nicht gemeldet hat */
  modus?: ModellModus;
  /** Claude-Code-Token (nur Entwicklungsaufträge, aus `claude setup-token`) hinterlegt; nur die letzten vier Zeichen */
  codeHinterlegt: boolean;
  codeEnde?: string;
  codeGesetzt?: string;
  wirksam?: string;
  fehler?: string;
};

/**
 * Kontingent des Max-Abos (voice-core/kontingent.py, aus rate_limit_event von Claude Code): sitzung = 5-Stunden-Fenster,
 * woche = Wochenlimit (alle Modelle), fable = Wochenlimit des größten Modells (in Claude Code "Fable limit"), opus und
 * sonnet = ältere modellbezogene Wochenlimits. Jedes Feld außer art kann fehlen. Ohne Daten ist der Zustand "unbekannt".
 */
export const KONTINGENT_ARTEN = ["sitzung", "woche", "opus", "sonnet", "fable"] as const;
export type KontingentLimit = {
  art: (typeof KONTINGENT_ARTEN)[number];
  /** genutzter Anteil in Prozent (0 bis 1000; über 100 kommt vor), eine Nachkommastelle */
  prozent?: number;
  /** Ende des Fensters, ISO 8601 in UTC (YYYY-MM-DDTHH:MM:SSZ) */
  endet?: string;
  status?: "ok" | "warnung" | "erreicht";
};
export type Kontingent = {
  zustand: "unbekannt" | "bekannt";
  /** wann der Kern den Stand zuletzt gespeichert hat (ISO 8601, UTC) */
  stand?: string;
  limits: KontingentLimit[];
};

/** wartet: kritische PC-Aktion, die auf eine Freigabe in der Oberfläche wartet */
export type ActivityStatus = "laeuft" | "fertig" | "abgelehnt" | "fehler" | "wartet";

/** Ein Werkzeugaufruf von WAMO für die Aktivitäts-Zeitleiste (activity.py im Voice-Core). */
export type Activity = {
  id: string;
  name: string;
  detail: string;
  status: ActivityStatus;
  /** im Browser gesetzt: Beginn und Ende (performance.now), Gesprächszug */
  startedAt?: number;
  endedAt?: number;
  turn?: number;
};

/** Ein Punkt zum Freigeben. ziel: Zielanwendung (PC-Aktionen, z. B. Fenster und Programm); gueltigS: Frist in s. */
export type FreigabePunkt = {
  id: string;
  grund: string;
  detail: string;
  von?: string;
  ziel?: string;
  /** Begründung des Modells (nur Hinweis, nicht das, was ausgeführt wird) */
  anlass?: string;
  gueltigS?: number;
};

/** Entwicklungsauftrag, den das Profil vor dem Start bestätigen muss (Servernachricht "auftrag"). */
export type Auftrag = { job: string; projekt: string; art: string; auftrag: string };

/** Lichtrahmen am Laptop (host/wamo_host.py, voice-core/activity.py): nur ganze Zahlen in Bildschirmpixeln, nie Titel. */
export type Rechteck = { x: number; y: number; b: number; h: number };
export type Punkt = { x: number; y: number };
export const MARKIERUNG_ARTEN = ["fokus", "klick", "text", "tasten", "scrollen"] as const;
export type Markierung = {
  art: (typeof MARKIERUNG_ARTEN)[number];
  /** Kennung des Laptops, der handelt (Tunnel je Gerät); null beim lokalen Kern */
  geraet: string | null;
  /** Rechnername des gesteuerten PCs, wenn bekannt */
  rechner: string | null;
  fenster: Rechteck;
  monitor: Rechteck;
  /** nur bei Klicks: Punkt der Welle */
  punkt?: Punkt;
};

export type ServerMessage =
  | { type: "canvas"; item: CanvasItem }
  | ({ type: "markierung" } & Markierung)
  | ({ type: "aktivitaet" } & Pick<Activity, "id" | "name" | "detail" | "status">)
  | { type: "sprecher"; index: number }
  | { type: "leiser_stopp"; sofort?: boolean }
  | { type: "stimme_leiser"; an: boolean }
  | { type: "freigaben"; job: string; projekt: string; items: FreigabePunkt[] }
  | ({ type: "profil" } & Profil)
  | ({ type: "betrieb" } & Betrieb)
  | ({ type: "gemerkt" } & Gemerkt)
  | ({ type: "auftrag" } & Auftrag)
  | { type: "sitzung_beendet"; grund: string; anmelden?: boolean }
  | ({ type: "modellzugang" } & Modellzugang)
  | ({ type: "kontingent" } & Kontingent)
  /** Kern: still = seit `sekunden` nur digitale Stille vom Mikrofon, ok = wieder Ton (voice-core/audio_monitor.py) */
  | { type: "mikrofon"; zustand: "still" | "fehlt" | "ok"; sekunden: number };

type Roh = Record<string, unknown>;

const isObj = (v: unknown): v is Roh => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const oneOf = <T extends string>(v: unknown, werte: readonly T[]): v is T =>
  isStr(v) && (werte as readonly string[]).includes(v);
const optStr = (v: unknown, max = 80) => (isStr(v) && v.trim() ? v.trim().slice(0, max) : undefined);

const CANVAS_KINDS = ["tabelle", "code", "dokument", "mermaid"] as const;

// Grenzen wie host/windows_control.py (Windows rechnet Koordinaten mit 16 Bit), voice-core/activity.py und desktop/lichtrahmen.js
export const MARK_POS_MIN = -32768;
export const MARK_POS_MAX = 32767;
export const MARK_GROESSE_MAX = 32768;
const ganz = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);
const pos = (v: unknown): v is number => ganz(v) && v >= MARK_POS_MIN && v <= MARK_POS_MAX;
const GERAET_KENNUNG = /^pc-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const RECHNERNAME = /^[A-Za-z0-9._-]{1,63}$/;

function rechteck(v: unknown): Rechteck | null {
  if (!isObj(v) || !pos(v.x) || !pos(v.y)) return null;
  const { b, h } = v;
  if (!ganz(b) || !ganz(h) || b < 1 || h < 1 || b > MARK_GROESSE_MAX || h > MARK_GROESSE_MAX) return null;
  return { x: v.x, y: v.y, b, h };
}

/** Markierung des Lichtrahmens prüfen: feste Felder, ganze Zahlen mit Obergrenzen, sonst null. */
function markierung(m: Roh): Markierung | null {
  if (!oneOf(m.art, MARKIERUNG_ARTEN)) return null;
  const fenster = rechteck(m.fenster);
  const monitor = rechteck(m.monitor);
  if (!fenster || !monitor) return null;
  const geraet = m.geraet ?? null;
  if (geraet !== null && !(isStr(geraet) && geraet.length <= 23 && GERAET_KENNUNG.test(geraet))) return null;
  const rechner = m.rechner ?? null;
  if (rechner !== null && !(isStr(rechner) && RECHNERNAME.test(rechner))) return null;
  const ergebnis: Markierung = { art: m.art, geraet, rechner, fenster, monitor };
  if (m.art === "klick" && isObj(m.punkt) && pos(m.punkt.x) && pos(m.punkt.y)) {
    ergebnis.punkt = { x: m.punkt.x, y: m.punkt.y };
  }
  return ergebnis;
}

/** Grenzen gegen übergroße Canvas-Inhalte (Speicher, Darstellung). */
export const CANVAS_MAX_ZEILEN = 500;
export const CANVAS_MAX_SPALTEN = 30;
export const CANVAS_MAX_TEXT = 200_000;

// Steuerzeichen (außer Zeilenumbruch und Tab) und Bidi-Steuerzeichen, die Text optisch umdrehen können
const STEUER = /[\u0000-\u0008\u000B-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2066-\u2069\uFEFF]/g;

/** Text bereinigen und kürzen; gekürzte Texte enden sichtbar mit "… (gekürzt)". */
export function sauber(text: string, max: number, zeilen = true): string {
  let t = text.replace(STEUER, "");
  if (!zeilen) t = t.replace(/[\r\n\t]+/g, " ");
  return t.length > max ? `${t.slice(0, max)} … (gekürzt)` : t;
}

const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

/** Limits des Kontingents prüfen: nur bekannte Arten, Zahlen im Wertebereich, ISO-Zeiten; Doppelte zählen einmal. */
function kontingentLimits(roh: unknown): KontingentLimit[] {
  if (!Array.isArray(roh)) return [];
  const gefunden = new Map<string, KontingentLimit>();
  for (const e of roh.slice(0, 10)) {
    if (!isObj(e) || !oneOf(e.art, KONTINGENT_ARTEN) || gefunden.has(e.art)) continue;
    const l: KontingentLimit = { art: e.art };
    if (typeof e.prozent === "number" && Number.isFinite(e.prozent) && e.prozent >= 0 && e.prozent <= 1000) {
      l.prozent = Math.round(e.prozent * 10) / 10;
    }
    if (isStr(e.endet) && ISO_UTC.test(e.endet)) l.endet = e.endet;
    if (oneOf(e.status, ["ok", "warnung", "erreicht"] as const)) l.status = e.status;
    if (Object.keys(l).length > 1) gefunden.set(e.art, l);
  }
  return KONTINGENT_ARTEN.flatMap((a) => gefunden.get(a) ?? []);
}

const AKTIVITAET_STATUS = ["laeuft", "fertig", "abgelehnt", "fehler", "wartet"] as const;

const zelle = (v: unknown) => (v === null || v === undefined ? "" : String(v));

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PROJEKT = /^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$/;

/** Angaben zum Teilen (voice-core/dev_agent.py: teilbar_id, teilbar_projekt; teilen_werkzeuge.py: geteilt). */
function canvasZusatz(v: Record<string, unknown>): Pick<CanvasItem, "teilbar" | "geteilt"> {
  const zusatz: Pick<CanvasItem, "teilbar" | "geteilt"> = {};
  if (isStr(v.teilbar_id) && UUID.test(v.teilbar_id)) {
    zusatz.teilbar = {
      id: v.teilbar_id,
      projekt: isStr(v.teilbar_projekt) && PROJEKT.test(v.teilbar_projekt) ? v.teilbar_projekt : undefined,
    };
  }
  const geteilt = v.geteilt;
  if (isObj(geteilt) && (geteilt.recht === "lesen" || geteilt.recht === "bearbeiten")) {
    zusatz.geteilt = { von: isStr(geteilt.von) ? sauber(geteilt.von, 60, false) : null, recht: geteilt.recht };
  }
  return zusatz;
}

function canvasItem(v: unknown): CanvasItem | null {
  const item = canvasGrund(v);
  return item && isObj(v) ? { ...item, ...canvasZusatz(v) } : item;
}

function canvasGrund(v: unknown): CanvasItem | null {
  if (!isObj(v) || !oneOf(v.kind, CANVAS_KINDS) || !isStr(v.title)) return null;
  const id = isStr(v.id) ? v.id.slice(0, 100) : null;
  const title = sauber(v.title, 200, false);
  switch (v.kind) {
    case "tabelle":
      if (!Array.isArray(v.columns) || !Array.isArray(v.rows)) return null;
      {
        const rows = v.rows.filter(Array.isArray) as unknown[][];
        const gekuerzt = rows.length > CANVAS_MAX_ZEILEN;
        return {
          id,
          kind: "tabelle",
          title: gekuerzt ? `${title} (erste ${CANVAS_MAX_ZEILEN} Zeilen)` : title,
          columns: v.columns.slice(0, CANVAS_MAX_SPALTEN).map((c) => sauber(zelle(c), 200, false)),
          rows: rows
            .slice(0, CANVAS_MAX_ZEILEN)
            .map((row) => row.slice(0, CANVAS_MAX_SPALTEN).map((c) => sauber(zelle(c), 2000))),
        };
      }
    case "code":
      if (!isStr(v.text)) return null;
      return {
        id,
        kind: "code",
        title,
        text: sauber(v.text, CANVAS_MAX_TEXT),
        language: isStr(v.language) ? sauber(v.language, 30, false) : undefined,
      };
    case "dokument":
      return isStr(v.text) ? { id, kind: v.kind, title, text: sauber(v.text, CANVAS_MAX_TEXT) } : null;
    case "mermaid":
      // Diagrammtext nicht kürzen (sonst ungültig): zu groß heißt ablehnen
      return isStr(v.text) && v.text.length <= CANVAS_MAX_TEXT ? { id, kind: v.kind, title, text: v.text.replace(STEUER, "") } : null;
  }
}

/** Prüft eine Servernachricht und gibt sie typisiert zurück; alles Unbekannte wird null. */
export function parseServerMessage(data: unknown): ServerMessage | null {
  if (!isObj(data) || !isStr(data.type)) return null;
  const m = data;
  switch (m.type) {
    case "canvas": {
      const item = canvasItem(m.item);
      return item ? { type: "canvas", item } : null;
    }
    case "markierung": {
      const mk = markierung(m);
      return mk ? { type: "markierung", ...mk } : null;
    }
    case "aktivitaet":
      if (!isStr(m.id) || !m.id || !isStr(m.name) || !m.name || !oneOf(m.status, AKTIVITAET_STATUS)) return null;
      return {
        type: "aktivitaet",
        id: m.id.slice(0, 100),
        name: sauber(m.name, 60, false),
        detail: isStr(m.detail) ? sauber(m.detail, 200, false) : "",
        status: m.status,
      };
    case "sprecher":
      return typeof m.index === "number" && Number.isFinite(m.index)
        ? { type: "sprecher", index: Math.max(0, Math.min(7, Math.trunc(m.index))) }
        : null;
    case "leiser_stopp":
      return { type: "leiser_stopp", sofort: m.sofort === true };
    case "stimme_leiser":
      return typeof m.an === "boolean" ? { type: "stimme_leiser", an: m.an } : null;
    case "freigaben": {
      if (!isStr(m.job) || !Array.isArray(m.items)) return null;
      const items = m.items
        .filter((it): it is Roh => isObj(it) && isStr(it.id) && isStr(it.grund) && isStr(it.detail))
        .map((it) => ({
          id: (it.id as string).slice(0, 100),
          grund: sauber(it.grund as string, 200, false),
          detail: sauber(it.detail as string, 2000),
          // erst bereinigen, dann kürzen: so erscheint "(gekürzt)", wenn etwas fehlt
          von: isStr(it.von) && it.von.trim() ? sauber(it.von.trim(), 60, false) : undefined,
          ziel: isStr(it.ziel) && it.ziel.trim() ? sauber(it.ziel.trim(), 200, false) : undefined,
          anlass: isStr(it.anlass) && it.anlass.trim() ? sauber(it.anlass.trim(), 200, false) : undefined,
          gueltigS:
            typeof it.gueltig_s === "number" && Number.isInteger(it.gueltig_s) && it.gueltig_s > 0 && it.gueltig_s <= 3600
              ? it.gueltig_s
              : undefined,
        }));
      return {
        type: "freigaben",
        job: m.job.slice(0, 100),
        projekt: isStr(m.projekt) ? sauber(m.projekt, 80, false) : "",
        items,
      };
    }
    case "profil":
      if (!isStr(m.name) || !m.name.trim() || !oneOf(m.rolle, ["admin", "mitglied"] as const)) return null;
      return { type: "profil", name: sauber(m.name.trim(), 60, false), rolle: m.rolle };
    case "betrieb": {
      if (!oneOf(m.modus, ["lokal", "buero"] as const)) return null;
      const modelle = isObj(m.modelle) ? m.modelle : {};
      const latenz = typeof m.pc_latenz_ms === "number" && Number.isFinite(m.pc_latenz_ms) ? m.pc_latenz_ms : undefined;
      return {
        type: "betrieb",
        modus: m.modus,
        modelle: { llm: optStr(modelle.llm), stt: optStr(modelle.stt), tts: optStr(modelle.tts) },
        pc: oneOf(m.pc, ["verbindet", "verbunden", "getrennt", "aus", "gesperrt"] as const) ? m.pc : "aus",
        ort: oneOf(m.ort, ["cloud", "haus"] as const) ? m.ort : undefined,
        pcLatenzMs: latenz !== undefined ? Math.max(0, Math.round(latenz)) : undefined,
        pcSteuerung: oneOf(m.pc_steuerung, ["an", "aus"] as const) ? m.pc_steuerung : undefined,
        entwicklung: oneOf(m.entwicklung, ["an", "aus"] as const) ? m.entwicklung : undefined,
        pcName: isStr(m.pc_name) && /^[A-Za-z0-9._-]{1,63}$/.test(m.pc_name) ? m.pc_name : undefined,
        pcBelegt: m.pc_belegt === true ? true : undefined,
        modellModus: oneOf(m.modell_modus, MODELL_MODI) ? m.modell_modus : undefined,
        modellQuelle: oneOf(m.modell_quelle, MODELL_QUELLEN) ? m.modell_quelle : undefined,
        modellHinweis: optStr(m.modell_hinweis, 200),
      };
    }
    case "gemerkt":
      if (
        !oneOf(m.bereich, ["persoenlich", "gemeinsam"] as const) ||
        !oneOf(m.status, ["aktiv", "vorgeschlagen"] as const)
      )
        return null;
      return {
        type: "gemerkt",
        bereich: m.bereich,
        status: m.status,
        text: isStr(m.text) ? sauber(m.text, 280, false) : "",
        id: optStr(m.id, 64),
      };
    case "auftrag":
      // Auftragstext vollständig zeigen (großzügige Grenze), aber ohne Steuer- und Bidi-Zeichen
      if (!isStr(m.job) || !m.job || !isStr(m.auftrag) || !m.auftrag.trim()) return null;
      return {
        type: "auftrag",
        job: m.job.slice(0, 100),
        projekt: isStr(m.projekt) ? sauber(m.projekt, 80, false) : "",
        art: isStr(m.art) ? sauber(m.art, 60, false) : "",
        auftrag: sauber(m.auftrag, 20_000),
      };
    case "sitzung_beendet":
      // Kern beendet die Sitzung selbst (Profil gesperrt, Rolle geändert, Höchstdauer erreicht)
      // anmelden: der Kern verlangt eine neue Anmeldung (Anmeldung zu alt); die Oberfläche meldet dann ab
      return {
        type: "sitzung_beendet",
        grund: isStr(m.grund) ? sauber(m.grund, 200, false) : "",
        anmelden: m.anmelden === true ? true : undefined,
      };
    case "modellzugang": {
      if (!oneOf(m.aktion, ["status", "hinterlegen", "loeschen", "modus"] as const)) return null;
      // Minutengenau, mit Zeitzone (der Kern liefert UTC mit +00:00): ohne sie läse der Browser den Wert als Ortszeit
      const zeit = (v: unknown) => {
        const t = isStr(v) ? /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?/.exec(v) : null;
        return t ? t[1] + (t[2] ?? "") : undefined;
      };
      const vier = (v: unknown) => (isStr(v) && /^[A-Za-z0-9_-]{4}$/.test(v) ? v : undefined);
      return {
        type: "modellzugang",
        aktion: m.aktion,
        art: oneOf(m.art, ["claude", "haus"] as const) ? m.art : undefined,
        quelle: oneOf(m.quelle, ["profil", "zuordnung", "festgelegt", "wahl", "keiner", "fehler"] as const)
          ? m.quelle
          : undefined,
        hinterlegt: m.hinterlegt === true,
        // nur die letzten vier Zeichen; alles andere wird verworfen
        ende: vier(m.ende),
        geprueft: zeit(m.geprueft),
        festgelegt: m.festgelegt === true,
        modell: isStr(m.modell) ? sauber(m.modell, 60, false) : undefined,
        modus: oneOf(m.modus, MODELL_MODI) ? m.modus : undefined,
        codeHinterlegt: m.code_hinterlegt === true,
        codeEnde: vier(m.code_ende),
        codeGesetzt: zeit(m.code_gesetzt),
        wirksam: isStr(m.wirksam) ? sauber(m.wirksam, 60, false) : undefined,
        // Absicherung: auch wenn der Kern je einen Schlüssel in einen Fehlertext schriebe, zeigt die Oberfläche ihn nicht
        fehler:
          isStr(m.fehler) && m.fehler.trim()
            ? sauber(m.fehler.trim(), 200, false).replace(/sk-ant-[A-Za-z0-9_-]*/gi, "[Schlüssel entfernt]")
            : undefined,
      };
    }
    case "kontingent": {
      if (!oneOf(m.zustand, ["unbekannt", "bekannt"] as const)) return null;
      const limits = m.zustand === "bekannt" ? kontingentLimits(m.limits) : [];
      return {
        type: "kontingent",
        zustand: limits.length > 0 ? "bekannt" : "unbekannt", // ohne gültige Limits bleibt es unbekannt
        stand: limits.length > 0 && isStr(m.stand) && ISO_UTC.test(m.stand) ? m.stand : undefined,
        limits,
      };
    }
    case "mikrofon": {
      if (!oneOf(m.zustand, ["still", "fehlt", "ok"] as const)) return null;
      const s = typeof m.sekunden === "number" && Number.isFinite(m.sekunden) ? Math.round(m.sekunden) : 10;
      return { type: "mikrofon", zustand: m.zustand, sekunden: Math.max(1, Math.min(3600, s)) };
    }
    default:
      return null;
  }
}
