/**
 * Reine Hilfen für geteilte Arbeit und Hinweise (ohne Abhängigkeiten, damit `npm test` sie direkt prüfen kann):
 * Prüfung der Antworten des Kerns (nie ungeprüft casten), Prüfung von Dateien vor dem Hochladen, Anzeigeformate.
 * Fremde Texte (Titel, Namen, Nachrichten) werden hier schon bereinigt: keine Steuer- und Bidi-Zeichen, gekürzt.
 */

export type Recht = "lesen" | "bearbeiten";
export type TeilArt = "canvas" | "auftrag" | "datei";

export type Teilung = {
  id: string;
  art: TeilArt;
  recht: Recht;
  titel: string;
  objekt: string;
  person: string;
  name: string;
  hinweis: string | null;
  erstellt_at: string | null;
};

export type Profil = { user_id: string; name: string };

export type DateiEintrag = {
  id: string;
  name: string;
  groesse: number;
  herkunft: string;
  erstellt_at: string | null;
  fassungen: number;
};

export type Hinweis = { id: string; art: string; titel: string; text: string; erstellt_at: string; gelesen: boolean };

export type CanvasAntwort = {
  item: Record<string, unknown>;
  recht: "besitzer" | Recht;
  besitzer: string | null;
  version: number;
};

export type Ereignis = { ereignis: string; at: string | null; durch: string | null };

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
/** Gleiche Liste wie im Kern (voice-core/teilen.py, DATEITYPEN). */
export const DATEI_ACCEPT =
  ".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.csv,.md,.json,.zip,.docx,.xlsx,.pptx,.odt,.ods,.odp";
const ENDUNGEN = new Set(DATEI_ACCEPT.split(","));

// Steuerzeichen und Bidi-Steuerzeichen, die Text optisch umdrehen können (wie lib/nachrichten.ts)
const STEUER = /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁦-⁩﻿]/g;

/** Fremden Text bereinigen: eine Zeile, ohne Steuerzeichen, gekürzt. */
export function text(wert: unknown, max = 200): string {
  if (typeof wert !== "string") return "";
  const t = wert.replace(/[\r\n\t]+/g, " ").replace(STEUER, "").replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const datum = (v: unknown): string | null => (typeof v === "string" && v.length <= 40 ? v : null);

/** UUID der Datenbank? (nur solche Canvas-Elemente lassen sich teilen) */
export function istUuid(wert: unknown): wert is string {
  return typeof wert === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(wert);
}

/** Größe lesbar: 1,2 MB, 340 KB. */
export function groesseText(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Datei vor dem Hochladen prüfen; liefert einen Fehlertext oder null. */
export function dateiPruefen(datei: { name: string; size: number }): string | null {
  if (datei.size <= 0) return "Die Datei ist leer.";
  if (datei.size > MAX_UPLOAD_BYTES) return "Die Datei ist größer als 25 MB.";
  const punkt = datei.name.lastIndexOf(".");
  if (punkt < 0 || !ENDUNGEN.has(datei.name.slice(punkt).toLowerCase())) {
    return "Dieser Dateityp lässt sich nicht teilen (PDF, Bilder, Text, Office).";
  }
  return null;
}

export function alsTeilungen(roh: unknown): Teilung[] {
  if (!Array.isArray(roh)) return [];
  return roh.flatMap((v): Teilung[] => {
    if (!isObj(v) || !istUuid(v.id) || typeof v.objekt !== "string") return [];
    const art = v.art === "canvas" || v.art === "auftrag" || v.art === "datei" ? v.art : null;
    if (!art) return [];
    return [
      {
        id: v.id,
        art,
        recht: v.recht === "bearbeiten" ? "bearbeiten" : "lesen",
        titel: text(v.titel, 120) || "Ohne Titel",
        objekt: v.objekt.slice(0, 100),
        person: istUuid(v.person) ? v.person : "",
        name: text(v.name, 60) || "Unbekannt",
        hinweis: text(v.hinweis, 300) || null,
        erstellt_at: datum(v.erstellt_at),
      },
    ];
  });
}

export function alsProfile(roh: unknown): Profil[] {
  if (!Array.isArray(roh)) return [];
  return roh.flatMap((v) => (isObj(v) && istUuid(v.user_id) ? [{ user_id: v.user_id, name: text(v.name, 60) || "?" }] : []));
}

export function alsDateien(roh: unknown): DateiEintrag[] {
  if (!Array.isArray(roh)) return [];
  return roh.flatMap((v): DateiEintrag[] =>
    isObj(v) && istUuid(v.id)
      ? [
          {
            id: v.id,
            name: text(v.name, 200) || "Datei",
            groesse: typeof v.groesse === "number" ? v.groesse : 0,
            herkunft: typeof v.herkunft === "string" ? v.herkunft : "upload",
            erstellt_at: datum(v.erstellt_at),
            fassungen: typeof v.fassungen === "number" ? v.fassungen : 1,
          },
        ]
      : [],
  );
}

export function alsHinweise(roh: unknown): Hinweis[] {
  if (!Array.isArray(roh)) return [];
  return roh.flatMap((v): Hinweis[] =>
    isObj(v) && istUuid(v.id)
      ? [
          {
            id: v.id,
            art: typeof v.art === "string" ? v.art.slice(0, 40) : "system",
            titel: text(v.titel, 200) || "Hinweis",
            text: text(v.text, 600),
            erstellt_at: datum(v.erstellt_at) ?? "",
            gelesen: v.gelesen === true,
          },
        ]
      : [],
  );
}

export function alsCanvasAntwort(roh: unknown): CanvasAntwort | null {
  if (!isObj(roh) || !isObj(roh.item)) return null;
  const recht = roh.recht === "besitzer" || roh.recht === "bearbeiten" || roh.recht === "lesen" ? roh.recht : null;
  if (!recht) return null;
  return {
    item: roh.item,
    recht,
    besitzer: text(roh.besitzer, 60) || null,
    version: typeof roh.version === "number" && roh.version >= 1 ? roh.version : 1,
  };
}

export function alsEreignisse(roh: unknown): Ereignis[] {
  if (!Array.isArray(roh)) return [];
  return roh.flatMap((v) =>
    isObj(v) && typeof v.ereignis === "string"
      ? [{ ereignis: v.ereignis.slice(0, 30), at: datum(v.at), durch: text(v.durch, 60) || null }]
      : [],
  );
}
