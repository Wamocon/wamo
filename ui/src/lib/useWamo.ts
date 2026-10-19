"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import { PipecatClient, RTVIError } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport, WavMediaManager } from "@pipecat-ai/small-webrtc-transport";
import { pcAutoLesen } from "@/lib/einstellungen";
import { parseServerMessage, type Auftrag } from "@/lib/nachrichten";
import { overlayFarben, type OverlayFarben } from "@/lib/stimmfarben";
import { getSupabase } from "@/lib/supabase";
import {
  HISTORY,
  applyServerMessage,
  entferneMemo,
  gesprochen,
  historyRef,
  istOnline,
  leereCaption,
  levelsRef,
  logEintrag,
  overlayZustand,
  reduceBotOutput,
  reduceBotTranscript,
  resetWamo,
  wamo,
  zeigeToast,
  ZUG_LEER,
  type Approval,
  type Caption,
  type Fehler,
  type LevelSample,
  type Memo,
  type Presence,
} from "@/lib/wamo";

export type { Activity, Betrieb, Gemerkt, Profil } from "@/lib/nachrichten";
export type { Approval, LevelHistory, LevelSample, Presence } from "@/lib/wamo";

declare global {
  interface Window {
    /** Nur in der Desktop-App (desktop/preload.js) vorhanden. */
    wamoDesktop?: {
      isDesktop: boolean;
      levels: (sample: LevelSample & { zustand: string }) => void;
      /** Farben des Stimmen-Rands einmalig übergeben (ältere Desktop-Versionen kennen das nicht). */
      farben?: (farben: OverlayFarben) => void;
    };
  }
}

/** Was die Oberfläche auslösen kann; der Zustand selbst liegt im Store (lib/wamo.ts). */
export type WamoAktionen = {
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
  /** true, wenn die Entscheidung gesendet wurde (sonst bleibt die Karte stehen) */
  decide: (a: Approval, ja: boolean) => boolean;
  toggleMic: () => void;
  vergessen: (memo: Memo) => void;
  /** Entwicklungsauftrag starten oder verwerfen; true, wenn gesendet */
  entscheideAuftrag: (a: Auftrag, ja: boolean) => boolean;
  /** PC-Steuerung dieser Sitzung an- oder ausschalten (nur admin); true, wenn gesendet */
  pcSteuerung: (an: boolean) => boolean;
  /** Fehlermeldung schließen */
  schliesseFehler: () => void;
};

/** Nachrichten an den Voice-Core: feste Liste statt freier Typen. */
type ClientMessage =
  | { type: "freigabe"; data: { job: string; id: string; entscheidung: "ja" | "nein" } }
  | { type: "vergessen"; data: { id: string } }
  | { type: "auftrag_start"; data: { job: string; entscheidung: "ja" | "nein" } }
  | { type: "pc_steuerung"; data: { an: boolean } };

function senden(client: PipecatClient, msg: ClientMessage) {
  client.sendClientMessage(msg.type, msg.data);
}

type SoftStop = { armedAt: number | null; fading: boolean };

/** WAMOs Stimme in 140 ms ausblenden (weicher Stopp bei Unterbrechung). */
function fadeOutAudio(audio: HTMLAudioElement | null, state: SoftStop) {
  state.fading = true;
  if (!audio) return;
  const start = performance.now();
  const step = () => {
    const t = Math.min(1, (performance.now() - start) / 140);
    audio.volume = 1 - t;
    if (t < 1 && state.fading) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  // Sicherheitsnetz: nie dauerhaft stumm bleiben
  setTimeout(() => {
    if (state.fading) restoreAudio(audio, state);
  }, 3000);
}

function restoreAudio(audio: HTMLAudioElement | null, state: SoftStop) {
  state.armedAt = null;
  state.fading = false;
  if (audio) audio.volume = 1;
}

const VOICE_CORE_URL = process.env.NEXT_PUBLIC_VOICE_CORE_URL ?? "http://localhost:7860";

function setPresence(p: Presence) {
  wamo.set((s) => (s.presence === p ? s : { ...s, presence: p }));
}

function setCaption(update: (c: Caption) => Caption) {
  wamo.set((s) => {
    const caption = update(s.caption);
    return caption === s.caption ? s : { ...s, caption };
  });
}

function setFehler(error: Fehler | null) {
  wamo.set((s) => ({ ...s, error }));
}

/** Nutzertext einarbeiten (endgültig oder Zwischenstand); ein schon geloggter Beitrag beginnt neu. */
function userText(text: string, final: boolean) {
  setCaption((c) => {
    const base = c.userLogged ? { ...c, userDone: "", userLive: "", userLogged: false } : c;
    if (!final) return { ...base, userLive: text };
    return { ...base, userDone: `${base.userDone} ${text}`.trim(), userLive: "" };
  });
}

/** Statuscode und Begründung aus einem Startfehler lesen. client-js lehnt bei HTTP-Fehlern mit der
 * Response selbst ab; RTVIError (z. B. StartBotError) trägt nur den Status. */
async function startFehler(e: unknown): Promise<{ status?: number; detail?: string; name?: string }> {
  if (typeof Response !== "undefined" && e instanceof Response) {
    let detail: string | undefined;
    try {
      const body = (await e.clone().json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail.slice(0, 200);
    } catch {
      // kein JSON: dann ohne Begründung
    }
    return { status: e.status, detail };
  }
  if (e instanceof RTVIError) return { status: e.status, detail: e.message, name: e.name };
  const name = (e as { name?: string } | null)?.name;
  return { name, status: (e as { status?: number } | null)?.status };
}

function fehlerText(status: number | undefined, detail: string | undefined, name: string | undefined): Fehler {
  const mitGrund = (text: string) => (detail ? `${text} (${detail})` : text);
  if (name === "NotAllowedError") {
    return { art: "mikrofon", text: "Mikrofon gesperrt: bitte in den Windows-Einstellungen für diese App freigeben." };
  }
  switch (status) {
    case 401:
      return { art: "anmeldung", text: mitGrund("Der Voice-Core hat die Anmeldung abgelehnt. Bitte ab- und wieder anmelden.") };
    case 403:
      return { art: "anmeldung", text: mitGrund("Kein Zugang: kein aktives WAMO-Profil oder zweiter Faktor fehlt.") };
    case 503:
      return { art: "dienst", text: mitGrund("Profilprüfung nicht möglich, Datenbank nicht erreichbar.") };
    default:
      return { art: "verbindung", text: `Voice-Core unter ${VOICE_CORE_URL} nicht erreichbar. Läuft bot.py?` };
  }
}

const TOKEN_REST_MS = 60_000; // so lange vor Ablauf gilt ein Token als "läuft ab"

export function useWamo(): WamoAktionen {
  const clientRef = useRef<PipecatClient | null>(null);
  // Generation der Verbindung: jeder Start und jedes Trennen zählt hoch. Rückrufe und ein spät
  // fertiger Start einer alten Generation wirken nicht mehr (kein Zombie nach "Abbrechen").
  const genRef = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // WAMOs Pegel messen wir selbst: SmallWebRTC meldet onRemoteAudioLevel nie (kein Player).
  const botMeter = useRef<{ ctx: AudioContext; analyser: AnalyserNode; buf: Float32Array<ArrayBuffer> } | null>(null);
  const softStop = useRef<SoftStop>({ armedAt: null, fading: false });
  // Neue RTVI-Ereignisse (bot-output, user-input) haben Vorrang; ältere Kerne schicken nur Transkripte
  const seen = useRef({ botOutput: false, userInput: false });
  const cutTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Authorization-Header der laufenden Sitzung: wird bei TOKEN_REFRESHED aktualisiert
  const authHeaders = useRef<Headers | null>(null);
  const tokenAblauf = useRef<number | null>(null);
  // Autostart der PC-Steuerung (Geräte-Einstellung): je Verbindung höchstens einmal senden;
  // aktiv = Einstellung beim Verbindungsstart, manuellAus = Nutzer hat in dieser Sitzung ausgeschaltet
  const pcAuto = useRef({ aktiv: false, gesendet: false, manuellAus: false, wartet: false });

  /**
   * Gemeinsamer Abbau für "Gespräch beenden", Verbindungsabbruch und Verlassen der Seite:
   * Generation weiterzählen, Client trennen, Pegel nullen, AudioContext schließen, Wiedergabe
   * stoppen. Offene Freigaben verfallen (sie gehen nur über die laufende Verbindung).
   */
  const teardown = useCallback((grund: "nutzer" | "getrennt" | "unmount") => {
    genRef.current += 1;
    pcAuto.current = { aktiv: false, gesendet: false, manuellAus: false, wartet: false };
    const client = clientRef.current;
    clientRef.current = null;
    authHeaders.current = null;
    tokenAblauf.current = null;
    if (client) void client.disconnect().catch(() => undefined);
    const meter = botMeter.current;
    botMeter.current = null;
    if (meter) void meter.ctx.close().catch(() => undefined);
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.srcObject = null;
    }
    restoreAudio(audioRef.current, softStop.current);
    if (cutTimer.current) clearTimeout(cutTimer.current);
    cutTimer.current = null;
    const lv = levelsRef.current;
    lv.user = 0;
    lv.bot = 0;
    lv.cut = false;
    if (grund === "unmount") return;
    wamo.set((s) => {
      const verfallen = s.approvals.length > 0 || s.auftraege.length > 0;
      const war = s.presence !== "offline";
      let error = s.error;
      if (grund === "getrennt" && war) {
        error = {
          art: "verbindung",
          text: verfallen
            ? "Verbindung getrennt. Verbindung weg, Freigabe verfällt (auch offene Entwicklungsaufträge)."
            : "Verbindung getrennt.",
        };
      } else if (grund === "nutzer" && verfallen) {
        error = { art: "hinweis", text: "Gespräch beendet. Offene Freigaben und Aufträge sind verfallen." };
      }
      return {
        ...s,
        presence: "offline",
        betrieb: null, // Betriebsdaten gelten nur für eine laufende Verbindung
        approvals: [],
        auftraege: [],
        micOn: true,
        error,
        ansage: !war ? s.ansage : grund === "getrennt" ? "Verbindung getrennt" : "Gespräch beendet",
      };
    });
  }, []);

  // Desktop-App: Farben des Stimmen-Rands einmalig übergeben (Quelle: CSS-Variablen)
  useEffect(() => {
    window.wamoDesktop?.farben?.(overlayFarben());
  }, []);

  // 20 Hz Abtastung der Pegel für Stimmlinie, Kern und Rand. Bewusst ohne React-State.
  // An die Desktop-App geht nur etwas, solange es etwas zu zeigen gibt (sonst einmal Ruhe melden).
  useEffect(() => {
    let ruheGemeldet = false;
    let letzterZustand = "";
    const id = setInterval(() => {
      const meter = botMeter.current;
      const lv = levelsRef.current;
      if (meter) {
        meter.analyser.getFloatTimeDomainData(meter.buf);
        let sum = 0;
        for (const v of meter.buf) sum += v * v;
        // RMS ungefähr auf die Skala des Mikrofonpegels bringen (Sprache ~0,1 bis 0,3)
        lv.bot = Math.min(1, Math.sqrt(sum / meter.buf.length) * 2);
      }
      const s = { ...lv };
      lv.cut = false; // cutAt bleibt für den Kern stehen
      const zustand = overlayZustand(wamo.get());
      const ruhe = (zustand === "offline" || zustand === "idle") && s.user < 0.003 && s.bot < 0.003 && !s.cut;
      // jeder Zustandswechsel geht raus (die Desktop-App schaltet daran z. B. den Energiesparschutz)
      if (!ruhe || !ruheGemeldet || zustand !== letzterZustand) window.wamoDesktop?.levels({ ...s, zustand });
      ruheGemeldet = ruhe;
      letzterZustand = zustand;
      const h = historyRef.current;
      h.samples.push(s);
      if (h.samples.length > HISTORY) h.samples.shift();
      h.seq++;
    }, 50);
    return () => clearInterval(id);
  }, []);

  // Screen Wake Lock im Browser-Modus während eines Gesprächs (Electron hat powerSaveBlocker)
  useEffect(() => {
    if (window.wamoDesktop || !("wakeLock" in navigator)) return;
    let lock: WakeLockSentinel | null = null;
    let pending = false;
    let wanted = false;
    const release = () => {
      const l = lock;
      lock = null;
      if (l) void l.release().catch(() => undefined);
    };
    const request = async () => {
      if (!wanted || lock || pending || document.visibilityState !== "visible") return;
      pending = true;
      try {
        const l = await navigator.wakeLock.request("screen");
        // inzwischen beendet? dann sofort wieder freigeben (kein hängender Wake Lock)
        if (!wanted) {
          void l.release().catch(() => undefined);
          return;
        }
        lock = l;
        l.addEventListener("release", () => {
          if (lock === l) lock = null;
        });
      } catch {
        // z. B. Energiesparmodus: dann eben ohne Wake Lock
      } finally {
        pending = false;
      }
    };
    const sync = () => {
      const s = wamo.get();
      const on = istOnline(s) || s.presence === "connecting";
      if (on === wanted) return;
      wanted = on;
      if (on) void request();
      else release();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void request();
    };
    const unsubscribe = wamo.subscribe(sync);
    document.addEventListener("visibilitychange", onVisible);
    sync();
    return () => {
      unsubscribe();
      document.removeEventListener("visibilitychange", onVisible);
      wanted = false;
      release();
    };
  }, []);

  // Token der laufenden Sitzung aktuell halten: supabase-js erneuert es vor Ablauf; der Transport
  // nutzt dasselbe Headers-Objekt für Offer und ICE-Updates, also dort nachziehen.
  useEffect(() => {
    const supabase = getSupabase();
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      if (event !== "TOKEN_REFRESHED" || !session || !authHeaders.current) return;
      authHeaders.current.set("Authorization", `Bearer ${session.access_token}`);
      tokenAblauf.current = session.expires_at ? session.expires_at * 1000 : null;
    });
    return () => data.subscription.unsubscribe();
  }, []);

  // Ablauf erkennen: läuft das Token einer laufenden Sitzung ab, ohne dass es erneuert wurde,
  // verständlich melden (das Gespräch selbst läuft weiter, neue Anfragen würden abgelehnt).
  useEffect(() => {
    const id = setInterval(() => {
      const ablauf = tokenAblauf.current;
      if (!ablauf || !istOnline(wamo.get())) return;
      if (Date.now() > ablauf - TOKEN_REST_MS / 2) {
        tokenAblauf.current = null; // nur einmal melden
        setFehler({
          art: "hinweis",
          text: "Die Anmeldung dieser Sitzung ist abgelaufen. Das Gespräch läuft weiter; bei Störungen bitte neu verbinden.",
        });
      }
    }, 15_000);
    return () => clearInterval(id);
  }, []);

  const disconnect = useCallback(async () => {
    teardown("nutzer");
  }, [teardown]);

  const connect = useCallback(async () => {
    // erneut = zweiter Versuch nach erneuerter Sitzung (nur einmal, nach 401)
    const starte = async (erneut: boolean): Promise<void> => {
      teardown("unmount"); // Reste eines früheren Versuchs abbauen, ohne Meldung
      // Geräte-Einstellung beim Start festhalten: ein Umschalten mitten im Gespräch wirkt erst beim nächsten
      pcAuto.current.aktiv = pcAutoLesen();
      const gen = genRef.current;
      const aktuell = () => genRef.current === gen;
      seen.current = { botOutput: false, userInput: false };
      wamo.set((s) => ({ ...s, error: null, presence: "connecting", caption: leereCaption, micOn: true }));
      const fadeOut = () => fadeOutAudio(audioRef.current, softStop.current);
      const restoreVolume = () => restoreAudio(audioRef.current, softStop.current);
      const client = new PipecatClient({
        // WavMediaManager statt des Standards DailyMediaManager: Mikrofon rein lokal per getUserMedia.
        // Der Daily-Manager lädt zur Laufzeit Code vom Daily-CDN; das blockt die CSP, und fremden Code
        // aus dem Netz wollen wir in WAMO nicht ausführen.
        transport: new SmallWebRTCTransport({ mediaManager: new WavMediaManager() }),
        enableMic: true,
        enableCam: false,
        callbacks: {
          onBotReady: () => {
            if (aktuell()) wamo.set((s) => ({ ...s, presence: "idle", error: null, ansage: "Verbunden" }));
          },
          onDisconnected: () => {
            if (aktuell()) teardown("getrennt");
          },
          onUserStartedSpeaking: () => {
            if (!aktuell()) return;
            const speaking = wamo.get().presence === "speaking";
            if (speaking) {
              // Unterbrechung: cut für Stimmlinie und Rand, cutAt für den Kern (geht nicht verloren)
              levelsRef.current.cut = true;
              levelsRef.current.cutAt = performance.now();
              if (cutTimer.current) clearTimeout(cutTimer.current);
              // den ungesagten Rest 2 s durchgestrichen zeigen, dann entfernen
              cutTimer.current = setTimeout(() => {
                setCaption((c) => (c.interrupted ? { ...c, text: gesprochen(c), interrupted: false } : c));
              }, 2000);
            }
            wamo.set((s) => {
              let caption = s.caption;
              if (speaking) caption = { ...caption, interrupted: true };
              else if (caption.turnEnd) caption = { ...caption, ...ZUG_LEER };
              if (caption.userLogged) caption = { ...caption, userDone: "", userLive: "", userLogged: false };
              return { ...s, presence: "listening", caption, turn: s.turn + 1 };
            });
          },
          onUserStoppedSpeaking: () => {
            if (aktuell()) setPresence("thinking");
          },
          onBotStartedSpeaking: () => {
            if (!aktuell()) return;
            restoreVolume(); // neue Antwort nach einem weichen Stopp wieder hörbar
            wamo.set((s) => {
              const c = s.caption;
              // fertigen Nutzerbeitrag in den Verlauf (nur ganze Züge, nie Zwischenstände)
              const userFertig = `${c.userDone} ${c.userLive}`.trim();
              const log =
                userFertig && !c.userLogged ? [...s.log, logEintrag({ wer: "nutzer", text: userFertig })] : s.log;
              return {
                ...s,
                presence: "speaking",
                log: log.slice(-200),
                caption: userFertig ? { ...c, userLogged: true } : c,
              };
            });
          },
          onBotStoppedSpeaking: () => {
            if (!aktuell()) return;
            wamo.set((s) => {
              const c = s.caption;
              const text = c.interrupted ? gesprochen(c) : c.text;
              const werkzeuge = s.activities.filter((a) => a.turn === s.turn).map((a) => a.name);
              const log = text
                ? [...s.log, logEintrag({ wer: "wamo", text, unterbrochen: c.interrupted, werkzeuge })]
                : s.log;
              return { ...s, presence: "idle", log: log.slice(-200), caption: { ...c, turnEnd: true } };
            });
          },
          onUserInput: (d) => {
            if (!aktuell()) return;
            seen.current.userInput = true;
            if (d.input_type === "backchannel" || !d.text) return; // "mhm" nicht anzeigen
            userText(d.text, d.final);
          },
          onUserTranscript: (d) => {
            if (!aktuell() || seen.current.userInput) return;
            if (d.text) userText(d.text, d.final);
          },
          onBotOutput: (d) => {
            if (!aktuell()) return;
            seen.current.botOutput = true;
            setCaption((c) => reduceBotOutput(c, d));
          },
          onBotTranscript: (d) => {
            if (!aktuell() || seen.current.botOutput) return;
            setCaption((c) => reduceBotTranscript(c, d.text));
          },
          onLocalAudioLevel: (level) => {
            if (aktuell()) levelsRef.current.user = level;
          },
          onRemoteAudioLevel: (level) => {
            if (!aktuell()) return;
            levelsRef.current.bot = level;
            // Weicher Stopp: in der nächsten Wortpause (oder spätestens nach 600 ms) ausblenden
            const armed = softStop.current.armedAt;
            if (armed && !softStop.current.fading && (level < 0.015 || performance.now() - armed > 600)) {
              fadeOut();
            }
          },
          onTrackStarted: (track, participant) => {
            if (!aktuell() || participant?.local || track.kind !== "audio") return;
            if (!audioRef.current) audioRef.current = new Audio();
            const stream = new MediaStream([track]);
            audioRef.current.srcObject = stream;
            audioRef.current.play().catch(() => {
              setFehler({ art: "hinweis", text: "Die Wiedergabe wurde vom Browser blockiert. Bitte einmal in die Seite klicken." });
            });
            // Pegel für Kern, Stimmlinie und den Rand der Desktop-App
            const alt = botMeter.current;
            if (alt) void alt.ctx.close().catch(() => undefined);
            const ctx = new AudioContext();
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 512;
            ctx.createMediaStreamSource(stream).connect(analyser);
            void ctx.resume().catch(() => undefined);
            botMeter.current = { ctx, analyser, buf: new Float32Array(analyser.fftSize) };
          },
          onServerMessage: (data) => {
            if (!aktuell()) return;
            const msg = parseServerMessage(data);
            if (!msg) return; // unbekannte oder unvollständige Nachricht: ignorieren
            if (msg.type === "leiser_stopp") softStop.current.armedAt = performance.now();
            else applyServerMessage(msg);
          },
          onError: (msg) => {
            if (!aktuell()) return;
            // data ist meist ein Objekt ({ message } / { error }); String() ergab "[object Object]".
            const data = (msg as { data?: unknown }).data as
              | string
              | { message?: string; error?: string }
              | undefined;
            const text =
              typeof data === "string" ? data : (data?.message ?? data?.error ?? JSON.stringify(data ?? ""));
            setFehler({ text: (text || "Verbindungsfehler").slice(0, 300), art: "sonst" });
          },
        },
      });
      clientRef.current = client;

      try {
        // Der Voice-Core lässt nur aktive WAMO-Profile mit zweitem Faktor herein (voice-core/auth_gate.py).
        // Der Transport schickt diese Header auch beim WebRTC-Offer und den ICE-Updates mit.
        const supabase = getSupabase();
        const { data } = supabase ? await supabase.auth.getSession() : { data: { session: null } };
        if (!aktuell()) return;
        const session = data.session;
        const headers = new Headers(session ? { Authorization: `Bearer ${session.access_token}` } : {});
        authHeaders.current = headers;
        tokenAblauf.current = session?.expires_at ? session.expires_at * 1000 : null;
        await client.startBotAndConnect({ endpoint: `${VOICE_CORE_URL}/start`, headers });
        // inzwischen abgebrochen: die gerade entstandene Sitzung sofort wieder schließen
        if (!aktuell()) void client.disconnect().catch(() => undefined);
      } catch (e) {
        if (!aktuell()) {
          void client.disconnect().catch(() => undefined);
          return; // vom Nutzer abgebrochen: keine Meldung
        }
        const { status, detail, name } = await startFehler(e);
        if (!aktuell()) return;
        if (status === 401 && !erneut) {
          // einmal die Sitzung erneuern und mit frischem Token und neuem Client wiederholen
          const supabase = getSupabase();
          const { error } = supabase ? await supabase.auth.refreshSession() : { error: true };
          if (!error && aktuell()) return starte(true);
        }
        if (!aktuell()) return;
        clientRef.current = null;
        authHeaders.current = null;
        tokenAblauf.current = null;
        void client.disconnect().catch(() => undefined);
        wamo.set((s) => ({ ...s, presence: "offline", error: fehlerText(status, detail, name) }));
      }
    };
    await starte(false);
  }, [teardown]);

  // Freigabe geht nur über die eigene WebRTC-Verbindung zum Voice-Core, nie per Sprache.
  // Die Karte verschwindet erst, wenn das Senden geklappt hat.
  const decide = useCallback((a: Approval, ja: boolean) => {
    const client = clientRef.current;
    if (!client || !istOnline(wamo.get())) {
      setFehler({ art: "verbindung", text: "Keine Verbindung: Die Freigabe konnte nicht gesendet werden." });
      return false;
    }
    try {
      senden(client, { type: "freigabe", data: { job: a.job, id: a.id, entscheidung: ja ? "ja" : "nein" } });
    } catch {
      setFehler({ art: "sonst", text: "Die Entscheidung konnte nicht gesendet werden. Bitte noch einmal versuchen." });
      return false;
    }
    wamo.set((s) => ({
      ...s,
      approvals: s.approvals.filter((x) => !(x.job === a.job && x.id === a.id)),
      ansage: ja ? `Freigegeben: ${a.grund}` : `Abgelehnt: ${a.grund}`,
    }));
    return true;
  }, []);

  // Entwicklungsauftrag: Karte erst nach erfolgreichem Senden entfernen
  const entscheideAuftrag = useCallback((a: Auftrag, ja: boolean) => {
    const client = clientRef.current;
    if (!client || !istOnline(wamo.get())) {
      setFehler({ art: "verbindung", text: "Keine Verbindung: Die Entscheidung zum Auftrag konnte nicht gesendet werden." });
      return false;
    }
    try {
      senden(client, { type: "auftrag_start", data: { job: a.job, entscheidung: ja ? "ja" : "nein" } });
    } catch {
      setFehler({ art: "sonst", text: "Die Entscheidung zum Auftrag konnte nicht gesendet werden. Bitte noch einmal versuchen." });
      return false;
    }
    wamo.set((s) => ({
      ...s,
      auftraege: s.auftraege.filter((x) => x.job !== a.job),
      ansage: ja ? `Auftrag gestartet: ${a.art || a.projekt}` : `Auftrag verworfen: ${a.art || a.projekt}`,
    }));
    return true;
  }, []);

  // PC-Steuerung: nur senden; den Zustand meldet der Kern im Feld betrieb.pc_steuerung zurück
  const pcSteuerung = useCallback((an: boolean) => {
    const client = clientRef.current;
    const s = wamo.get();
    if (!client || !istOnline(s) || s.profil?.rolle !== "admin") return false;
    try {
      senden(client, { type: "pc_steuerung", data: { an } });
    } catch {
      setFehler({ art: "sonst", text: "PC-Steuerung konnte nicht umgeschaltet werden." });
      return false;
    }
    // manuell ausgeschaltet: in dieser Sitzung nicht wieder automatisch einschalten
    if (!an) {
      pcAuto.current.manuellAus = true;
      pcAuto.current.wartet = false;
    }
    return true;
  }, []);

  // Autostart der PC-Steuerung: Einstellung an, Profil admin, PC-Host verbunden und Steuerung aus
  // -> einmal je Verbindung einschalten. Den Hinweis gibt es erst, wenn der Kern "an" bestätigt.
  useEffect(() => {
    const pruefe = () => {
      const s = wamo.get();
      const st = pcAuto.current;
      const b = s.betrieb;
      if (st.wartet && b?.pcSteuerung === "an") {
        st.wartet = false;
        zeigeToast("PC-Steuerung automatisch eingeschaltet");
        return;
      }
      if (!st.aktiv || st.gesendet || st.manuellAus || !b || !istOnline(s)) return;
      if (s.profil?.rolle !== "admin" || b.pc !== "verbunden" || b.pcSteuerung !== "aus") return;
      const client = clientRef.current;
      if (!client) return;
      st.gesendet = true; // auch bei einem Fehler nicht erneut versuchen (kein Dauerfeuer)
      try {
        senden(client, { type: "pc_steuerung", data: { an: true } });
        st.wartet = true;
      } catch {
        setFehler({ art: "sonst", text: "PC-Steuerung konnte nicht automatisch eingeschaltet werden." });
      }
    };
    return wamo.subscribe(pruefe);
  }, []);

  const toggleMic = useCallback(() => {
    const client = clientRef.current;
    if (!client || !istOnline(wamo.get())) return;
    const on = !wamo.get().micOn;
    try {
      client.enableMic(on);
    } catch {
      return;
    }
    wamo.set((s) => ({ ...s, micOn: on, ansage: on ? "Mikrofon an" : "Mikrofon aus" }));
  }, []);

  // "Rückgängig" beim Hinweis "Gemerkt": nur mit Kennung vom Kern, Hinweis erst nach dem Senden weg
  const vergessen = useCallback((memo: Memo) => {
    if (memo.id) {
      const client = clientRef.current;
      try {
        if (!client) throw new Error("keine Verbindung");
        senden(client, { type: "vergessen", data: { id: memo.id } });
      } catch {
        setFehler({ art: "sonst", text: "Rückgängig nicht möglich: keine Verbindung zum Voice-Core." });
        return;
      }
    }
    entferneMemo(memo.key);
    wamo.set((s) => ({ ...s, ansage: "Eintrag zurückgenommen" }));
  }, []);

  const schliesseFehler = useCallback(() => setFehler(null), []);

  // Strg+M schaltet das Mikrofon (nicht in Eingabefeldern); ohne Modifikator kein Auslösen aus Versehen
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key !== "m" && e.key !== "M") || !e.ctrlKey || e.altKey || e.metaKey || e.shiftKey || e.repeat) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      toggleMic();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleMic]);

  // Beim Verlassen (Abmelden): Verbindung trennen und Zustand leeren
  useEffect(
    () => () => {
      teardown("unmount");
      resetWamo();
    },
    [teardown],
  );

  return useMemo(
    () => ({ connect, disconnect, decide, entscheideAuftrag, pcSteuerung, toggleMic, vergessen, schliesseFehler }),
    [connect, disconnect, decide, entscheideAuftrag, pcSteuerung, toggleMic, vergessen, schliesseFehler],
  );
}
