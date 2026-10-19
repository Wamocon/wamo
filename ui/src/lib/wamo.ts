import type { BotOutputData } from "@pipecat-ai/client-js";
import type { CanvasItem } from "@/components/Canvas";
import type { Activity, Auftrag, Betrieb, Gemerkt, Profil, ServerMessage } from "@/lib/nachrichten";
import { createStore } from "@/lib/store";

/**
 * Zustand der Hauptansicht als Store (lib/store.ts). Die Pegel für Kern, Stimmlinie und
 * Stimmen-Rand liegen bewusst NICHT im Store, sondern in levelsRef und historyRef: sie ändern
 * sich 20-mal pro Sekunde und werden pro Bild gelesen, ohne React-Render.
 */

export type Presence = "offline" | "connecting" | "idle" | "listening" | "thinking" | "speaking";

/** Ein Abtastwert: user = Mikrofon, bot = WAMO. cut markiert eine Unterbrechung (für Stimmlinie
 * und Rand), cutAt bleibt stehen, damit der Kern sie nicht verpasst. */
export type LevelSample = { user: number; bot: number; cut: boolean; speaker: number; cutAt?: number };

/** Verlauf der Stimmlinie; seq zählt jeden neuen Abtastwert (VoiceLine zeichnet nur dann neu). */
export type LevelHistory = { samples: LevelSample[]; seq: number };

/** Aktion eines Entwicklungsauftrags, die das angemeldete Profil per Klick freigeben muss. */
export type Approval = { job: string; projekt: string; id: string; grund: string; detail: string; von?: string };

/** Live-Untertitel: Nutzerzeile und der aktuelle WAMO-Zug (Karaoke über spoken). */
export type Caption = {
  userDone: string; // endgültige Teile des aktuellen Nutzerbeitrags
  userLive: string; // Zwischenergebnis (kursiv grau)
  userLogged: boolean; // schon in den Verlauf übernommen
  text: string; // WAMO-Zug, gesprochene und noch kommende Wörter
  spoken: number; // Anzahl schon gesprochener Wörter
  turnEnd: boolean;
  interrupted: boolean; // Rest wird kurz durchgestrichen gezeigt
};

export type LogEintrag = {
  id: number;
  wer: "nutzer" | "wamo";
  text: string;
  zeit: number; // Date.now()
  unterbrochen?: boolean;
  werkzeuge?: string[];
};

export type Memo = Gemerkt & { key: number; paused: boolean };

/** Meldung in der Präsenzspalte. art steuert die sichere nächste Aktion (Erneut verbinden, Abmelden). */
export type Fehler = {
  text: string;
  art: "verbindung" | "dienst" | "anmeldung" | "mikrofon" | "sonst" | "hinweis";
};

export type WamoState = {
  presence: Presence;
  error: Fehler | null;
  caption: Caption;
  log: LogEintrag[];
  activities: Activity[];
  turn: number;
  approvals: Approval[];
  /** Entwicklungsaufträge, die auf "Starten" oder "Verwerfen" warten */
  auftraege: Auftrag[];
  canvas: CanvasItem[];
  betrieb: Betrieb | null;
  profil: Profil | null;
  memos: Memo[];
  micOn: boolean;
  /** Text für role="status": nur bedeutsame Wechsel (verbunden, getrennt, Mikro, PC) */
  ansage: string;
};

export const HISTORY = 240; // ~12 s bei 20 Hz
export const CANVAS_MAX_ELEMENTE = 20;

export const leereCaption: Caption = {
  userDone: "",
  userLive: "",
  userLogged: false,
  text: "",
  spoken: 0,
  turnEnd: false,
  interrupted: false,
};

export const initialState: WamoState = {
  presence: "offline",
  error: null,
  caption: leereCaption,
  log: [],
  activities: [],
  turn: 0,
  approvals: [],
  auftraege: [],
  canvas: [],
  betrieb: null,
  profil: null,
  memos: [],
  micOn: true,
  ansage: "",
};

export const wamo = createStore<WamoState>(initialState);

export const levelsRef: { current: LevelSample } = {
  current: { user: 0, bot: 0, cut: false, speaker: 0 },
};
export const historyRef: { current: LevelHistory } = { current: { samples: [], seq: 0 } };

// ---------- abgeleitete Werte (Selektoren liefern Primitive) ----------
export const istOnline = (s: WamoState) => s.presence !== "offline" && s.presence !== "connecting";
export const laeuftWerkzeug = (s: WamoState) => s.activities.some((a) => a.status === "laeuft");
export const laufendesWerkzeug = (s: WamoState) => s.activities.find((a) => a.status === "laeuft")?.name ?? null;

/** Zustand für den Stimmen-Rand der Desktop-App (main.js prüft die Werte). */
/** Wartet etwas auf eine Entscheidung des Profils (Freigabe oder Auftrag)? */
export const wartetAufEntscheidung = (s: WamoState) => s.approvals.length > 0 || s.auftraege.length > 0;

export function overlayZustand(s: WamoState) {
  if (wartetAufEntscheidung(s)) return "approval";
  if (laeuftWerkzeug(s) && istOnline(s)) return "acting";
  return s.presence === "connecting" ? "idle" : s.presence;
}

// ---------- Untertitel ----------
const words = (s: string) => (s.trim() ? s.trim().split(/\s+/).length : 0);

/** Bot-Ausgabe einarbeiten: Sätze kommen vorab (grau), Wörter im Moment des Sprechens (Tinte). */
export function reduceBotOutput(c: Caption, d: BotOutputData): Caption {
  const kind = d.text_type ?? d.aggregated_by;
  if (!d.text || kind === "backchannel") return c;
  const base = c.turnEnd || c.interrupted ? { ...c, text: "", spoken: 0, turnEnd: false, interrupted: false } : c;
  if (d.spoken_progress) {
    // RTVI 2: exakter Fortschritt im Satz
    const { accumulated_text: done, remaining_text: rest } = d.spoken_progress;
    return { ...base, text: `${done} ${rest}`.trim(), spoken: words(done) };
  }
  if (kind === "word") {
    // Wort wird jetzt gesprochen
    return base.spoken < words(base.text)
      ? { ...base, spoken: base.spoken + 1 }
      : { ...base, text: `${base.text} ${d.text}`.trim(), spoken: base.spoken + 1 };
  }
  const text = `${base.text} ${d.text}`.trim(); // Satz kommt vorab: grau als Vorschau
  return { ...base, text, spoken: d.will_be_spoken === false ? words(text) : base.spoken };
}

/** Rückfall für Kerne ohne bot-output (nur LLM-Text): alles gilt sofort als gesprochen. */
export function reduceBotTranscript(c: Caption, text: string): Caption {
  if (!text) return c;
  const base = c.turnEnd || c.interrupted ? { ...c, text: "", spoken: 0, turnEnd: false, interrupted: false } : c;
  const next = `${base.text} ${text}`.trim();
  return { ...base, text: next, spoken: words(next) };
}

/** Nur die gesprochenen Wörter eines Zugs (nach einer Unterbrechung). */
export const gesprochen = (c: Caption) =>
  c.text.trim() ? c.text.trim().split(/\s+/).slice(0, c.spoken).join(" ") : "";

let logId = 0;
export function logEintrag(e: Omit<LogEintrag, "id" | "zeit">): LogEintrag {
  logId += 1;
  return { ...e, id: logId, zeit: Date.now() };
}

// ---------- Hinweis "Gemerkt": 8 s sichtbar, Pause bei Hover und Fokus ----------
const MEMO_MS = 8000;
const memoTimer = new Map<number, { timer: ReturnType<typeof setTimeout>; ende: number }>();
const memoRest = new Map<number, number>();
let memoKey = 0;

export function entferneMemo(key: number) {
  const t = memoTimer.get(key);
  if (t) clearTimeout(t.timer);
  memoTimer.delete(key);
  memoRest.delete(key);
  wamo.set((s) => ({ ...s, memos: s.memos.filter((m) => m.key !== key) }));
}

function starteMemoTimer(key: number, ms: number) {
  const timer = setTimeout(() => entferneMemo(key), ms);
  memoTimer.set(key, { timer, ende: performance.now() + ms });
}

export function zeigeMemo(g: Gemerkt) {
  memoKey += 1;
  const key = memoKey;
  wamo.set((s) => {
    const memos = [{ ...g, key, paused: false }, ...s.memos];
    // höchstens drei gleichzeitig; ältere gehen sofort
    for (const alt of memos.slice(3)) {
      const t = memoTimer.get(alt.key);
      if (t) clearTimeout(t.timer);
      memoTimer.delete(alt.key);
    }
    return { ...s, memos: memos.slice(0, 3) };
  });
  starteMemoTimer(key, MEMO_MS);
}

export function halteMemo(key: number) {
  const t = memoTimer.get(key);
  if (!t) return;
  clearTimeout(t.timer);
  memoTimer.delete(key);
  memoRest.set(key, Math.max(1500, t.ende - performance.now()));
  wamo.set((s) => ({ ...s, memos: s.memos.map((m) => (m.key === key ? { ...m, paused: true } : m)) }));
}

export function weiterMemo(key: number) {
  const rest = memoRest.get(key);
  if (rest === undefined) return;
  memoRest.delete(key);
  starteMemoTimer(key, rest);
  wamo.set((s) => ({ ...s, memos: s.memos.map((m) => (m.key === key ? { ...m, paused: false } : m)) }));
}

/** Alle Timer stoppen und den Zustand zurücksetzen (Abmelden, Vorschau). */
export function resetWamo(state: WamoState = initialState) {
  for (const t of memoTimer.values()) clearTimeout(t.timer);
  memoTimer.clear();
  memoRest.clear();
  levelsRef.current = { user: 0, bot: 0, cut: false, speaker: 0 };
  historyRef.current = { samples: [], seq: 0 };
  wamo.set(() => state);
}

// ---------- Servernachrichten in den Store übernehmen ----------
const PC_ANSAGE: Record<Betrieb["pc"], string> = {
  verbunden: "PC verbunden",
  getrennt: "PC nicht erreichbar",
  aus: "PC-Steuerung aus",
  gesperrt: "Bildschirm gesperrt",
};

export function applyServerMessage(msg: ServerMessage) {
  switch (msg.type) {
    case "sprecher":
      // Wer spricht (0 = Hauptsprecher, 1.. = weitere Stimmen): Ring im Kern und Licht am Rand
      levelsRef.current.speaker = msg.index;
      return;
    case "canvas": {
      // canvas_zeigen im Voice-Core: gleiche id = Aktualisierung (z. B. Fortschritt eines Auftrags)
      const item = msg.item;
      wamo.set((s) => {
        const i = item.id ? s.canvas.findIndex((it) => it.id === item.id) : -1;
        if (i >= 0) {
          const canvas = [...s.canvas];
          canvas[i] = item;
          return { ...s, canvas };
        }
        // höchstens 20 Elemente, das älteste fällt heraus
        return {
          ...s,
          canvas: [...s.canvas, item].slice(-CANVAS_MAX_ELEMENTE),
          ansage: `Neues Ergebnis auf dem Canvas: ${item.title}`,
        };
      });
      return;
    }
    case "aktivitaet": {
      const jetzt = performance.now();
      wamo.set((s) => {
        const alt = s.activities.find((a) => a.id === msg.id);
        const fertig = msg.status !== "laeuft";
        const a: Activity = {
          id: msg.id,
          name: msg.name,
          detail: msg.detail,
          status: msg.status,
          turn: alt?.turn ?? s.turn,
          startedAt: alt?.startedAt ?? (fertig ? undefined : jetzt),
          endedAt: fertig ? (alt?.endedAt ?? jetzt) : undefined,
        };
        // gleiche id = Statuswechsel; neueste oben, Platz für den aktuellen Zug und frühere Schritte
        return { ...s, activities: [a, ...s.activities.filter((x) => x.id !== a.id)].slice(0, 12) };
      });
      return;
    }
    case "freigaben": {
      const { job, projekt } = msg;
      const fresh = msg.items.map((it) => ({ ...it, job, projekt }));
      wamo.set((s) => ({
        ...s,
        approvals: [...s.approvals.filter((a) => !fresh.some((f) => f.job === a.job && f.id === a.id)), ...fresh],
      }));
      return;
    }
    case "profil":
      wamo.set((s) => ({ ...s, profil: { name: msg.name, rolle: msg.rolle } }));
      return;
    case "betrieb": {
      const betrieb: Betrieb = {
        modus: msg.modus,
        modelle: msg.modelle,
        pc: msg.pc,
        ort: msg.ort,
        pcLatenzMs: msg.pcLatenzMs,
        pcSteuerung: msg.pcSteuerung,
        entwicklung: msg.entwicklung,
      };
      wamo.set((s) => ({
        ...s,
        betrieb,
        ansage: s.betrieb && s.betrieb.pc !== betrieb.pc ? PC_ANSAGE[betrieb.pc] : s.ansage,
      }));
      return;
    }
    case "gemerkt":
      zeigeMemo({ bereich: msg.bereich, status: msg.status, text: msg.text, id: msg.id });
      return;
    case "auftrag": {
      const a: Auftrag = { job: msg.job, projekt: msg.projekt, art: msg.art, auftrag: msg.auftrag };
      wamo.set((s) => ({
        ...s,
        auftraege: [...s.auftraege.filter((x) => x.job !== a.job), a],
        ansage: `Entwicklungsauftrag wartet: ${a.art || "Auftrag"} für ${a.projekt || "ein Projekt"}`,
      }));
      return;
    }
    case "sitzung_beendet":
      wamo.set((s) => ({
        ...s,
        error: { art: "hinweis", text: `Sitzung beendet${msg.grund ? `: ${msg.grund}` : "."}` },
        ansage: "Sitzung vom Voice-Core beendet",
      }));
      return;
    case "leiser_stopp":
      return; // behandelt useWamo (weicher Stopp der Wiedergabe)
    default: {
      const unbekannt: never = msg; // neue Nachrichtentypen müssen hier behandelt werden
      void unbekannt;
    }
  }
}
