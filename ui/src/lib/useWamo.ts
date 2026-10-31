"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import { PipecatClient, RTVIError } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport, WavMediaManager } from "@pipecat-ai/small-webrtc-transport";
import { betriebsartFuerStart, KERN, kernUrl, type Betriebsart, type BetriebsartBruecke } from "@/lib/betriebsart";
import { pcAutoLesen } from "@/lib/einstellungen";
import type { GeraetBruecke } from "@/lib/geraet";
import { bildAnzeigen, liveBildLoeschen } from "@/lib/liveBild";
import { parseServerMessage, type Auftrag, type Markierung, type ModellModus } from "@/lib/nachrichten";
import type { SchluesselArt } from "@/lib/schluesselhilfe";
import { overlayFarben, type OverlayFarben } from "@/lib/stimmfarben";
import { textAbschicken, unterstuetztText } from "@/lib/textEingabe";
import { erstelleFrist, fristMeldung, sollVerlaengern } from "@/lib/verbindungsfrist";
import { anmeldungAbgelaufen, maxStundenHier, merkeAbmeldeGrund } from "@/lib/anmeldung";
import {
  anzeigePegel,
  fehlerName,
  geraeteListe,
  gewaehltesMikrofon,
  keineSpurText,
  kernFehltText,
  kernStilleText,
  merkeMikrofon,
  messen,
  mikrofonFehlerText,
  neueStille,
  pruefeStille,
  sendeSpurErsetzen,
  SPUR_ENDE_MS,
  SPUR_MS,
  spurEndeText,
  STILLE_DBFS,
  startGeraet,
  stilleText,
  tonLog,
  windowsGesperrt,
  windowsGesperrtText,
  type Mikrofon,
} from "@/lib/mikrofon";
import { getSupabase } from "@/lib/supabase";
import {
  AUSBLENDEN_MS,
  DAEMPFEN_MAX_MS,
  DAEMPFEN_MS,
  ENTDAEMPFEN_MS,
  GEDAEMPFT,
  sollAusblenden,
  verlauf,
  type SoftStop,
} from "@/lib/weicherStopp";
import {
  HISTORY,
  LIVE_BILD_AUS,
  applyServerMessage,
  beginneNutzerZug,
  entferneAblauf,
  entferneMemo,
  gesprochen,
  historyRef,
  istOnline,
  leereCaption,
  levelsRef,
  logEintrag,
  nimmeTextAuf,
  overlayZustand,
  reduceBotOutput,
  reduceBotTranscript,
  resetWamo,
  wamo,
  wartetAufEntscheidung,
  zeigeToast,
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
      /** Lichtrahmen ums Zielfenster und Welle am Klickpunkt (nur Zahlen und Gerätekennung); die Desktop-App zeichnet nur auf
       *  dem Laptop, der handelt. Ältere Desktop-Versionen kennen das nicht. */
      markierung?: (markierung: Markierung) => void;
      /** Betriebsart Büro/Unterwegs, gehalten vom Hauptprogramm (lib/betriebsart.ts) */
      betriebsart?: BetriebsartBruecke;
      /** Rechnername dieses PCs (Autostart der PC-Steuerung nur, wenn der Kern genau diesen PC steuert) */
      rechnername?: () => Promise<unknown>;
      /** Windows-Datenschutz für das Mikrofon: "granted", "denied", "restricted" oder "not-determined" (lib/mikrofon.ts) */
      mikrofonStatus?: () => Promise<unknown>;
      /** Einrichtung des Laptops bei der Anmeldung (nur Büro; lib/geraet.ts) */
      geraet?: GeraetBruecke;
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
  /** Live-Bild des eigenen Laptops (nur admin, voice-core/bildstrom.py): Ansicht öffnen, schließen, Monitor wählen;
   *  true, wenn gesendet. Optional, damit die Vorschau ohne Kern auskommt. */
  liveBild?: LiveBildAktionen;
  /** Text an WAMO schicken wie eine gesprochene Bitte (nur mit Verbindung, höchstens eine Sendung zugleich, Schlüssel
   *  werden vorher entfernt); true, wenn die Eingabe angenommen wurde (das Feld wird geleert). Optional, damit die
   *  Vorschau ohne Kern auskommt. Ersetzt nie eine Freigabe: die gibt nur der Klick auf die Karte (decide). */
  textSenden?: (text: string) => boolean;
  /** Fehlermeldung schließen */
  schliesseFehler: () => void;
  /** angeschlossene Mikrofone (ohne die Windows-Aliase), leer ohne Verbindung */
  mikrofone: () => Promise<Mikrofon[]>;
  /** Mikrofon wechseln (gilt sofort und für die nächsten Gespräche auf diesem Gerät); true, wenn gewechselt */
  mikrofonWaehlen: (id: string) => boolean;
  /** Modellzugang des Profils abfragen, eigenen Claude-Schlüssel hinterlegen oder löschen; true, wenn gesendet.
   *  Optional, damit die Vorschau ohne Kern auskommt. */
  modellzugang?: (anfrage: ModellzugangAnfrage) => boolean;
};

export type LiveBildAktionen = {
  an: (monitor: number) => boolean;
  aus: () => boolean;
  monitor: (monitor: number) => boolean;
};

/** Der Schlüssel geht nur in dieser einen Nachricht an den eigenen Kern und wird im Browser nicht gespeichert.
 *  schluesselart: api (Anthropic-API-Schlüssel, Gespräch) oder code (Claude-Code-Token, Entwicklungsaufträge);
 *  ohne Angabe gilt api. modus: die Wahl der Person, gilt ab der nächsten Sitzung. */
export type { SchluesselArt };
export type ModellzugangAnfrage =
  | { aktion: "status" }
  | { aktion: "loeschen"; schluesselart?: SchluesselArt }
  | { aktion: "hinterlegen"; schluessel: string; workspace?: string; schluesselart?: SchluesselArt }
  | { aktion: "modus"; modus: ModellModus };

/** Nachrichten an den Voice-Core: feste Liste statt freier Typen. */
type ClientMessage =
  | { type: "freigabe"; data: { job: string; id: string; entscheidung: "ja" | "nein" } }
  | { type: "vergessen"; data: { id: string } }
  | { type: "auftrag_start"; data: { job: string; entscheidung: "ja" | "nein" } }
  | { type: "pc_steuerung"; data: { an: boolean } }
  | { type: "bildstrom"; data: { aktion: "an" | "aus" | "monitor" | "ack"; monitor?: number; seq?: number } }
  | { type: "modellzugang"; data: ModellzugangAnfrage }
  | { type: "kontingent"; data: Record<string, never> };

function senden(client: PipecatClient, msg: ClientMessage) {
  client.sendClientMessage(msg.type, msg.data);
}

/** Zählt die Lautstärkeverläufe: ein neuer macht den laufenden ungültig (Dämpfen, Entdämpfen, Ausblenden). */
let rampeNr = 0;

/** Lautstärke von WAMOs Stimme in dauerMs linear auf ziel, ausgehend vom aktuellen Wert. */
function rampe(audio: HTMLAudioElement | null, ziel: number, dauerMs: number) {
  const nr = ++rampeNr;
  if (!audio) return;
  const von = audio.volume;
  const start = performance.now();
  const step = () => {
    if (nr !== rampeNr) return;
    const v = verlauf(von, ziel, dauerMs, performance.now() - start);
    audio.volume = v;
    if (v !== ziel) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** WAMOs Stimme in 140 ms ausblenden (weicher Stopp bei Unterbrechung), auch aus einer Dämpfung heraus. */
function fadeOutAudio(audio: HTMLAudioElement | null, state: SoftStop) {
  state.fading = true;
  if (!audio) return;
  rampe(audio, 0, AUSBLENDEN_MS);
  // Sicherheitsnetz: nie dauerhaft stumm bleiben
  setTimeout(() => {
    if (state.fading) restoreAudio(audio, state);
  }, 3000);
}

function restoreAudio(audio: HTMLAudioElement | null, state: SoftStop) {
  state.armedAt = null;
  state.fading = false;
  rampeNr++; // laufenden Verlauf beenden
  if (audio) audio.volume = 1;
}

/** Vorsorgliches Dämpfen (an) und Zurücknehmen (aus), solange kein Stopp läuft. */
function stimmeLeiser(audio: HTMLAudioElement | null, an: boolean, state: SoftStop) {
  if (state.fading) return;
  rampe(audio, an ? GEDAEMPFT : 1, an ? DAEMPFEN_MS : ENTDAEMPFEN_MS);
  if (!an) return;
  // Sicherheitsnetz: bleibt die Entwarnung aus (Kern weg, Nachricht verloren), nie dauerhaft leise bleiben
  const meine = rampeNr;
  setTimeout(() => {
    if (rampeNr === meine && !state.fading) rampe(audio, 1, ENTDAEMPFEN_MS);
  }, DAEMPFEN_MAX_MS);
}

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

/** Nach einer Unterbrechung den ungesagten Rest 2 s durchgestrichen zeigen, dann entfernen (Sprechen und Tippen). */
function planeSchnitt(timer: { current: ReturnType<typeof setTimeout> | null }) {
  if (timer.current) clearTimeout(timer.current);
  timer.current = setTimeout(() => {
    setCaption((c) => (c.interrupted ? { ...c, text: gesprochen(c), interrupted: false } : c));
  }, 2000);
}

/** Nutzertext einarbeiten (endgültig oder Zwischenstand); ein schon geloggter Beitrag beginnt neu. */
function userText(text: string, final: boolean) {
  setCaption((c) => {
    const base = c.userLogged ? { ...c, userDone: "", userLive: "", userLogged: false } : c;
    if (!final) return { ...base, userLive: text };
    return { ...base, userDone: `${base.userDone} ${text}`.trim(), userLive: "" };
  });
}

/** Der Kern verlangt eine neue Anmeldung (zu alt): lokal abmelden, die Anmeldemaske zeigt den Grund. */
function neuAnmelden(grund: string) {
  merkeAbmeldeGrund(grund);
  void getSupabase()?.auth.signOut({ scope: "local" }).catch(() => undefined);
}

/** Statuscode und Begründung aus einem Startfehler lesen. Teils lehnt client-js mit der Response selbst ab; beim
 * Start (StartBotError) trägt der RTVIError Status und den Text des Kerns (detail), aber nicht dessen code. */
async function startFehler(e: unknown): Promise<{ status?: number; detail?: string; name?: string; code?: string }> {
  if (typeof Response !== "undefined" && e instanceof Response) {
    let detail: string | undefined;
    let code: string | undefined;
    try {
      const body = (await e.clone().json()) as { detail?: unknown; code?: unknown };
      if (typeof body?.detail === "string") detail = body.detail.slice(0, 200);
      if (typeof body?.code === "string") code = body.code.slice(0, 40);
    } catch {
      // kein JSON: dann ohne Begründung
    }
    return { status: e.status, detail, code };
  }
  if (e instanceof RTVIError) return { status: e.status, detail: e.message, name: e.name };
  const name = (e as { name?: string } | null)?.name;
  return { name, status: (e as { status?: number } | null)?.status };
}

function fehlerText(
  status: number | undefined,
  detail: string | undefined,
  name: string | undefined,
  art: Betriebsart,
): Fehler {
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
      return art === "buero"
        ? {
            art: "verbindung",
            text: `Voice-Core auf Sokrates-2 (${kernUrl(art)}) nicht erreichbar. Läuft wamo-core? Ohne Büro-LAN die Betriebsart Unterwegs wählen.`,
          }
        : { art: "verbindung", text: `Voice-Core unter ${kernUrl(art)} nicht erreichbar. Läuft bot.py?` };
  }
}

/**
 * Nach dem Verbinden auf das auf diesem Gerät gewählte Mikrofon wechseln, wenn es angeschlossen ist und nicht schon
 * läuft. Ohne Wahl oder ohne das Gerät bleibt das Standardgerät von Windows.
 */
async function gewuenschtesMikrofon(client: PipecatClient, spur: MediaStreamTrack | undefined, aktuell: () => boolean) {
  const wunsch = gewaehltesMikrofon();
  if (!wunsch || !navigator.mediaDevices?.enumerateDevices) return;
  try {
    const geraete = await navigator.mediaDevices.enumerateDevices();
    const id = startGeraet(geraeteListe(geraete), wunsch);
    if (!id || !aktuell()) return;
    const laeuft = spur?.getSettings?.() ?? {};
    const ziel = geraete.find((g) => g.deviceId === id);
    // "default" ist nur ein Alias: dasselbe Gerät, wenn die groupId übereinstimmt
    const schon = laeuft.deviceId === id || (laeuft.deviceId === "default" && !!ziel && laeuft.groupId === ziel.groupId);
    if (!schon) client.updateMic(id);
  } catch {
    // Geräteliste nicht lesbar: dann bleibt das Standardgerät
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
  // Ebenso den eigenen Mikrofonpegel (onLocalAudioLevel kommt mit dem WavMediaManager nie): für Stimmlinie, Rand und
  // den Stille-Wächter (lib/mikrofon.ts)
  const micMeter = useRef<{
    ctx: AudioContext;
    analyser: AnalyserNode;
    buf: Float32Array<ArrayBuffer>;
    track: MediaStreamTrack;
    /** schon einmal Ton über der Stille-Schwelle gemessen (für die positive Logzeile) */
    ton: boolean;
  } | null>(null);
  const stille = useRef(neueStille());
  // Fehler beim Öffnen des Mikrofons (onDeviceError): Name wie NotAllowedError, für eine passende Meldung
  const geraetFehler = useRef<string | null>(null);
  // Kern meldet nur Stille (Servernachricht "mikrofon")
  const kernStille = useRef(false);
  // Gerät gewechselt, während eine Mikrofon-Warnung steht: sie verschwindet, sobald das neue Gerät Ton liefert, und
  // nennt nach STILLE_MS das neue Gerät, wenn auch dieses still bleibt
  const warnungNachWechsel = useRef(false);
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
  // Rechnername dieses Geräts (nur Desktop-App), Vergleich mit betrieb.pcName im Büro
  const geraet = useRef<string | null>(null);
  // Grund, mit dem der Kern die Sitzung beendet hat (sitzung_beendet, z. B. neue Sitzung dieses Profils): beim
  // folgenden Trennen bleibt diese Meldung stehen statt "Verbindung getrennt"
  const kernEnde = useRef<string | null>(null);
  // Läuft gerade das Senden einer getippten Nachricht? Höchstens eine Sendung zugleich.
  const textLaeuft = useRef(false);
  // Verbindungs-Watchdog (lib/verbindungsfrist.ts): ohne Betriebsmeldung nach 20 s Abbruch mit Meldung
  const fristRef = useRef<ReturnType<typeof erstelleFrist> | null>(null);

  /**
   * Gemeinsamer Abbau für "Gespräch beenden", Verbindungsabbruch und Verlassen der Seite:
   * Generation weiterzählen, Client trennen, Pegel nullen, AudioContext schließen, Wiedergabe
   * stoppen. Offene Freigaben verfallen (sie gehen nur über die laufende Verbindung).
   */
  const teardown = useCallback((grund: "nutzer" | "getrennt" | "unmount" | "frist", meldung?: Fehler) => {
    genRef.current += 1;
    fristRef.current?.stoppe();
    textLaeuft.current = false;
    pcAuto.current = { aktiv: false, gesendet: false, manuellAus: false, wartet: false };
    const client = clientRef.current;
    clientRef.current = null;
    authHeaders.current = null;
    tokenAblauf.current = null;
    if (client) void client.disconnect().catch(() => undefined);
    const meter = botMeter.current;
    botMeter.current = null;
    if (meter) void meter.ctx.close().catch(() => undefined);
    const mic = micMeter.current;
    micMeter.current = null;
    if (mic) void mic.ctx.close().catch(() => undefined);
    stille.current = neueStille();
    geraetFehler.current = null;
    kernStille.current = false;
    warnungNachWechsel.current = false;
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
    const vomKern = kernEnde.current;
    kernEnde.current = null;
    liveBildLoeschen(); // kein Bild bleibt nach dem Gespräch stehen
    if (grund === "unmount") return;
    wamo.set((s) => {
      const verfallen = s.approvals.length > 0 || s.auftraege.length > 0;
      const war = s.presence !== "offline";
      let error = s.error;
      if (grund === "getrennt" && war && vomKern !== null) {
        error = { art: "hinweis", text: vomKern ? `Sitzung beendet: ${vomKern}` : "Sitzung vom Voice-Core beendet." };
      } else if (grund === "getrennt" && war) {
        error = {
          art: "verbindung",
          text: verfallen
            ? "Verbindung getrennt. Verbindung weg, Freigabe verfällt (auch offene Entwicklungsaufträge)."
            : "Verbindung getrennt.",
        };
      } else if (grund === "frist" && meldung) {
        error = meldung;
      } else if (grund === "nutzer" && verfallen) {
        error = { art: "hinweis", text: "Gespräch beendet. Offene Freigaben und Aufträge sind verfallen." };
      }
      return {
        ...s,
        presence: "offline",
        betrieb: null, // Betriebsdaten gelten nur für eine laufende Verbindung
        liveBild: LIVE_BILD_AUS,
        modellzugang: null, // wird beim nächsten Verbinden neu abgefragt
        approvals: [],
        auftraege: [],
        micOn: true,
        textMoeglich: false,
        mikrofon: null,
        error,
        ansage: grund === "frist" ? "Keine Antwort vom Kern" : !war ? s.ansage : grund === "getrennt" ? "Verbindung getrennt" : "Gespräch beendet",
      };
    });
  }, []);

  // Watchdog: Kommt nach dem Start der Verbindung binnen 20 s keine Betriebsmeldung, ist der Aufbau hängen geblieben.
  // Solange der Browser die Person noch nach dem Mikrofon fragt, wird weiter gewartet (kein hängender Aufbau).
  useEffect(() => {
    const frist = erstelleFrist({
      gen: () => genRef.current,
      fertig: () => wamo.get().betrieb !== null,
      verlaengern: async (n) => {
        try {
          // Safari (iPhone) kennt die Abfrage teils nicht oder antwortet nie: höchstens 2 s warten
          const status = await Promise.race([
            navigator.permissions.query({ name: "microphone" as PermissionName }),
            new Promise<null>((r) => setTimeout(() => r(null), 2000)),
          ]);
          return sollVerlaengern(status?.state, n);
        } catch {
          // Zustand nicht lesbar: einmal um 20 s verlängern (die Person liest vielleicht noch die Mikrofonfrage), dann gilt die Frist
          return n < 1;
        }
      },
      beiAblauf: () => teardown("frist", fristMeldung(wamo.get().kern)),
    });
    fristRef.current = frist;
    // Offline oder Betriebsmeldung da: keine Frist mehr nötig (deckt auch Abbrüche, die nicht über teardown laufen)
    const abo = wamo.subscribe(() => {
      const s = wamo.get();
      if (s.presence === "offline" || s.betrieb !== null) frist.stoppe();
    });
    return () => {
      abo();
      frist.stoppe();
      fristRef.current = null;
    };
  }, [teardown]);

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
      const mic = micMeter.current;
      if (mic) {
        mic.analyser.getFloatTimeDomainData(mic.buf);
        const { rms, spitzeDb } = messen(mic.buf);
        lv.user = anzeigePegel(rms);
        if (!mic.ton && spitzeDb > STILLE_DBFS) {
          mic.ton = true; // einmal je Spur: Beleg im Protokoll der Desktop-App (logs/oberflaeche.log)
          console.info("[WAMO]", tonLog(mic.track.label, spitzeDb));
        }
        // Nur prüfen, solange gemessen werden kann (AudioContext läuft) und das Mikrofon nicht stumm geschaltet ist
        const st = wamo.get();
        const aktiv = istOnline(st) && st.micOn && mic.ctx.state === "running";
        const ergebnis = pruefeStille(stille.current, spitzeDb, performance.now(), aktiv);
        const tonNachWechsel = warnungNachWechsel.current && aktiv && spitzeDb > STILLE_DBFS;
        if (ergebnis === "still") {
          warnungNachWechsel.current = false;
          console.warn("[WAMO] Mikrofon liefert seit 3 s nur Stille:", mic.track.label || "unbekanntes Gerät", mic.track.readyState);
          setFehler({ art: "mikrofon", text: stilleText(mic.track.label) });
          // Sperrt Windows das Mikrofon für Desktop-Apps, kommt nur Stille an, ohne Fehler: dann den Grund nennen
          void window.wamoDesktop
            ?.mikrofonStatus?.()
            .then((status) => {
              if (windowsGesperrt(status) && wamo.get().error?.art === "mikrofon") {
                setFehler({ art: "mikrofon", text: windowsGesperrtText() });
              }
            })
            .catch(() => undefined);
        } else if (ergebnis === "wieder_da" || tonNachWechsel) {
          warnungNachWechsel.current = false;
          if (!kernStille.current && wamo.get().error?.art === "mikrofon") setFehler(null);
        }
      }
      // Weicher Stopp nach "leiser_stopp": in der nächsten Wortpause, spätestens nach 600 ms ausblenden.
      // Hier im Takt mit dem eigenen Pegel, weil SmallWebRTC keinen Pegel der Gegenseite meldet.
      if (sollAusblenden(softStop.current, lv.bot, performance.now())) {
        fadeOutAudio(audioRef.current, softStop.current);
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
      // Welcher Kern: Betriebsart dieses Geräts direkt vor dem Verbinden (Desktop: aus dem Hauptprogramm)
      const art = await betriebsartFuerStart();
      if (!aktuell()) return;
      const kern = kernUrl(art);
      wamo.set((s) => ({ ...s, kern: art }));
      geraet.current = null;
      try {
        const name = await window.wamoDesktop?.rechnername?.();
        if (typeof name === "string" && /^[A-Za-z0-9._-]{1,63}$/.test(name)) geraet.current = name;
      } catch {
        // ältere Desktop-App oder Browser: kein Rechnername, also kein Autostart im Büro
      }
      if (!aktuell()) return;
      if (!kern) {
        wamo.set((s) => ({
          ...s,
          presence: "offline",
          error: { art: "dienst", text: `Betriebsart ${KERN[art].name} ist auf diesem Gerät nicht eingerichtet.` },
        }));
        return;
      }
      const restoreVolume = () => restoreAudio(audioRef.current, softStop.current);
      // Eigene Mikrofonspur (Start und jeder Gerätewechsel): Pegel messen und auf die laufende Verbindung legen
      const eigeneSpur = (track: MediaStreamTrack) => {
        const alt = micMeter.current;
        const bekannt = alt?.track === track; // Mikrofon aus und wieder an meldet dieselbe Spur noch einmal
        if (alt) void alt.ctx.close().catch(() => undefined);
        // Steht schon eine Mikrofon-Warnung, verschwindet sie erst, wenn das neue Gerät wirklich Ton liefert
        stille.current = neueStille();
        warnungNachWechsel.current = wamo.get().error?.art === "mikrofon";
        geraetFehler.current = null;
        const ctx = new AudioContext();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 2048;
        ctx.createMediaStreamSource(new MediaStream([track])).connect(analyser);
        void ctx.resume().catch(() => undefined);
        micMeter.current = { ctx, analyser, buf: new Float32Array(analyser.fftSize), track, ton: false };
        wamo.set((s) => ({ ...s, mikrofon: track.label ? track.label.slice(0, 120) : null }));
        if (!bekannt) {
          console.info("[WAMO] Mikrofon geöffnet:", track.label || "unbekanntes Gerät");
          // Endet die Spur von außen (Gerät abgezogen, Windows entzieht es) und folgt keine neue, sagt es die Oberfläche
          track.addEventListener("ended", () => {
            setTimeout(() => {
              if (!aktuell() || micMeter.current?.track !== track || !istOnline(wamo.get())) return;
              console.warn("[WAMO] Mikrofonspur beendet:", track.label || "unbekanntes Gerät");
              setFehler({ art: "mikrofon", text: spurEndeText(track.label) });
            }, SPUR_ENDE_MS);
          });
        }
        void sendeSpurErsetzen(clientRef.current?.transport, track)
          .then((r) => {
            if (r === "ersetzt") console.info("[WAMO] Mikrofonspur der Verbindung ersetzt:", track.label);
          })
          .catch(() => {
            if (aktuell()) {
              setFehler({ art: "mikrofon", text: "Das neue Mikrofon ließ sich nicht verbinden. Bitte das Gespräch neu starten." });
            }
          });
      };
      const client = new PipecatClient({
        // WavMediaManager statt des Standards DailyMediaManager: Mikrofon rein lokal per getUserMedia.
        // Der Daily-Manager lädt zur Laufzeit Code vom Daily-CDN; das blockt die CSP, und fremden Code
        // aus dem Netz wollen wir in WAMO nicht ausführen.
        transport: new SmallWebRTCTransport({ mediaManager: new WavMediaManager() }),
        enableMic: true,
        enableCam: false,
        callbacks: {
          onBotReady: (bereit) => {
            if (!aktuell()) return;
            wamo.set((s) => ({
              ...s,
              presence: "idle",
              error: null,
              ansage: "Verbunden",
              textMoeglich: unterstuetztText(bereit?.version),
            }));
            // Kam bis dahin keine eigene Mikrofonspur (Öffnen schlug lautlos fehl), hört WAMO nichts: nicht schweigen
            setTimeout(() => {
              const st = wamo.get();
              if (aktuell() && istOnline(st) && st.micOn && !micMeter.current && st.error?.art !== "mikrofon") {
                console.warn("[WAMO] Mikrofon: keine Spur nach dem Verbinden");
                setFehler({ art: "mikrofon", text: keineSpurText() });
              }
            }, SPUR_MS);
          },
          onDisconnected: () => {
            if (aktuell()) teardown("getrennt");
          },
          onUserStartedSpeaking: () => {
            if (!aktuell()) return;
            if (wamo.get().presence === "speaking") {
              // Unterbrechung: cut für Stimmlinie und Rand, cutAt für den Kern (geht nicht verloren)
              levelsRef.current.cut = true;
              levelsRef.current.cutAt = performance.now();
              planeSchnitt(cutTimer);
            }
            wamo.set(beginneNutzerZug);
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
            // "mhm" nicht anzeigen. Getippten Text (chat) zeigt textSenden selbst, schon von Schlüsseln befreit: ein Echo
            // des Kerns würde ihn doppelt zeigen.
            if (d.input_type === "backchannel" || d.input_type === "chat" || !d.text) return;
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
          onDeviceError: (fehler) => {
            // getUserMedia fehlgeschlagen (gesperrt, kein Gerät, belegt). Ohne diese Meldung lief der Start sonst in
            // ein irreführendes "Voice-Core nicht erreichbar".
            if (!aktuell() || !fehler.devices?.includes("mic")) return;
            const name = fehlerName(fehler) ?? "unbekannt";
            geraetFehler.current = name;
            console.warn("[WAMO] Mikrofon lässt sich nicht öffnen:", name);
            setFehler({ art: "mikrofon", text: mikrofonFehlerText(name) });
          },
          onTrackStarted: (track, participant) => {
            if (!aktuell() || track.kind !== "audio") return;
            if (participant?.local) {
              eigeneSpur(track);
              return;
            }
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
            if (msg.type === "leiser_stopp") {
              softStop.current.armedAt = performance.now();
              // sofort: der Kern hat die Grenze schon gewählt, die Stimme verstummt jetzt (keine Wortpause abwarten)
              if (msg.sofort) fadeOutAudio(audioRef.current, softStop.current);
            } else if (msg.type === "stimme_leiser") stimmeLeiser(audioRef.current, msg.an, softStop.current);
            else if (msg.type === "mikrofon") {
              kernStille.current = msg.zustand !== "ok";
              const st = wamo.get();
              if (msg.zustand === "fehlt" && st.micOn) {
                // Die Spur ist abgerissen: wichtiger als eine Stille-Meldung der Oberfläche
                setFehler({ art: "mikrofon", text: kernFehltText(msg.sekunden) });
              } else if (msg.zustand === "still" && st.micOn && st.error?.art !== "mikrofon") {
                setFehler({ art: "mikrofon", text: kernStilleText(msg.sekunden) });
              } else if (msg.zustand === "ok" && !stille.current.gemeldet && st.error?.art === "mikrofon") {
                setFehler(null);
              }
            } else if (msg.type === "bild") {
              // Nur zeichnen, wenn die Person die Ansicht geöffnet hat; erst nach dem Zeichnen bestätigen (Rückstau im Kern)
              const st = wamo.get();
              if (st.liveBild.geoeffnet && st.liveBild.zustand !== "aus") {
                const seq = msg.seq;
                void bildAnzeigen(msg.jpeg).then((gezeichnet) => {
                  if (!gezeichnet) return;
                  const ende = Date.now() + msg.restS * 1000;
                  wamo.set((x) => {
                    const l = x.liveBild;
                    if (!l.geoeffnet || (l.zustand !== "wartet" && l.zustand !== "laeuft")) {
                      liveBildLoeschen(); // ein spätes Bild nach Pause, Sperre oder Ende bleibt nie stehen
                      return x;
                    }
                    if (l.zustand === "laeuft" && l.monitore === msg.monitore && l.monitor === msg.monitor && Math.abs((l.endeMs ?? 0) - ende) < 3000) {
                      return x; // kein Render je Bild, wenn sich nichts Sichtbares ändert
                    }
                    return { ...x, liveBild: { ...l, zustand: "laeuft", monitor: msg.monitor, monitore: msg.monitore, endeMs: ende } };
                  });
                  const c = clientRef.current;
                  if (c) {
                    try {
                      senden(c, { type: "bildstrom", data: { aktion: "ack", seq } });
                    } catch {
                      /* Verbindung weg: der Kern beendet die Ansicht selbst */
                    }
                  }
                });
              }
            } else if (msg.type === "markierung") {
              // Gesperrter Bildschirm oder offene Freigabe: kein Rahmen. Der Rest (Gerätekennung, Vollbild, Rand aus) prüft die App.
              const st = wamo.get();
              if (st.betrieb?.pc !== "gesperrt" && !wartetAufEntscheidung(st)) {
                const { type: _typ, ...daten } = msg;
                void _typ;
                window.wamoDesktop?.markierung?.(daten);
              }
            } else {
              if (msg.type === "sitzung_beendet") kernEnde.current = msg.grund;
              applyServerMessage(msg);
              if (msg.type === "sitzung_beendet" && msg.anmelden) neuAnmelden(msg.grund || "Bitte neu anmelden.");
            }
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
      fristRef.current?.starte(gen);

      try {
        // Der Voice-Core lässt nur aktive WAMO-Profile mit zweitem Faktor herein (voice-core/auth_gate.py).
        // Der Transport schickt diese Header auch beim WebRTC-Offer und den ICE-Updates mit.
        const supabase = getSupabase();
        const { data } = supabase ? await supabase.auth.getSession() : { data: { session: null } };
        if (!aktuell()) return;
        const session = data.session;
        if (session && anmeldungAbgelaufen(session.access_token, Date.now(), maxStundenHier())) {
          // zu alt: gar nicht erst verbinden, sondern abmelden (die Anmeldemaske nennt den Grund)
          clientRef.current = null;
          wamo.set((s) => ({ ...s, presence: "offline" }));
          neuAnmelden("Deine Anmeldung ist abgelaufen. Bitte neu anmelden.");
          return;
        }
        const headers = new Headers(session ? { Authorization: `Bearer ${session.access_token}` } : {});
        authHeaders.current = headers;
        tokenAblauf.current = session?.expires_at ? session.expires_at * 1000 : null;
        await client.startBotAndConnect({ endpoint: `${kern}/start`, headers });
        // inzwischen abgebrochen: die gerade entstandene Sitzung sofort wieder schließen
        if (!aktuell()) void client.disconnect().catch(() => undefined);
        else void gewuenschtesMikrofon(client, micMeter.current?.track, aktuell);
      } catch (e) {
        if (!aktuell()) {
          void client.disconnect().catch(() => undefined);
          return; // vom Nutzer abgebrochen: keine Meldung
        }
        const { status, detail, name, code } = await startFehler(e);
        if (!aktuell()) return;
        // Der Kern meldet code anmeldung_zu_alt; client-js reicht beim Start nur den Text (detail) weiter
        if (status === 401 && (code === "anmeldung_zu_alt" || /Anmeldung älter als/.test(detail ?? ""))) {
          // Erneuern hilft nicht (gemessen ab Passwort und Code): abmelden statt erneut versuchen
          clientRef.current = null;
          authHeaders.current = null;
          tokenAblauf.current = null;
          void client.disconnect().catch(() => undefined);
          wamo.set((s) => ({ ...s, presence: "offline" }));
          neuAnmelden("Deine Anmeldung ist zu alt. Bitte neu anmelden.");
          return;
        }
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
        // Ließ sich das Mikrofon nicht öffnen, ist das der Grund (der Start bricht dann im Transport ab)
        const mic = geraetFehler.current;
        const error = mic ? { art: "mikrofon" as const, text: mikrofonFehlerText(mic) } : fehlerText(status, detail, name, art);
        wamo.set((s) => ({ ...s, presence: "offline", error }));
      }
    };
    await starte(false);
  }, [teardown]);

  // Freigabe geht nur über die eigene WebRTC-Verbindung zum Voice-Core, nie per Sprache.
  // Die Karte verschwindet erst, wenn das Senden geklappt hat.
  const decide = useCallback((a: Approval, ja: boolean) => {
    const client = clientRef.current;
    if (a.ablauf !== undefined && Date.now() > a.ablauf) {
      // abgelaufen: nicht mehr senden (der Kern würde ablehnen), Karte weg und klar sagen
      entferneAblauf(a);
      wamo.set((s) => ({
        ...s,
        approvals: s.approvals.filter((x) => !(x.job === a.job && x.id === a.id)),
        ansage: `Freigabe abgelaufen: ${a.grund}`,
      }));
      setFehler({ art: "hinweis", text: "Die Freigabe ist abgelaufen; am PC ist nichts passiert." });
      return false;
    }
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
    entferneAblauf(a);
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

  // Live-Bild: nur senden; den Zustand meldet der Kern (Nachricht bildstrom), Bilder zeichnet lib/liveBild.ts
  const liveBild = useMemo<LiveBildAktionen>(() => {
    const sende = (data: { aktion: "an" | "aus" | "monitor"; monitor?: number }) => {
      const client = clientRef.current;
      const s = wamo.get();
      if (!client || !istOnline(s) || s.profil?.rolle !== "admin") return false;
      try {
        senden(client, { type: "bildstrom", data });
        return true;
      } catch {
        setFehler({ art: "sonst", text: "Das Live-Bild konnte nicht umgeschaltet werden." });
        return false;
      }
    };
    return {
      an: (monitor) => {
        wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, geoeffnet: true, zustand: "wartet", monitor } }));
        if (sende({ aktion: "an", monitor })) return true;
        wamo.set((s) => ({ ...s, liveBild: LIVE_BILD_AUS }));
        return false;
      },
      aus: () => {
        liveBildLoeschen();
        wamo.set((s) => ({ ...s, liveBild: { ...LIVE_BILD_AUS, monitor: s.liveBild.monitor } }));
        return sende({ aktion: "aus" });
      },
      monitor: (monitor) => {
        wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, monitor } }));
        return sende({ aktion: "monitor", monitor });
      },
    };
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

  const modellzugang = useCallback((anfrage: ModellzugangAnfrage) => {
    const client = clientRef.current;
    if (!client || !istOnline(wamo.get())) return false;
    try {
      senden(client, { type: "modellzugang", data: anfrage });
    } catch {
      return false;
    }
    return true;
  }, []);

  // Texteingabe: eine getippte Nachricht geht wie eine gesprochene Bitte an WAMO (RTVI send-text, der Kern antwortet mit
  // Stimme). Der Text wird nie geloggt und nie auf Vorrat gehalten; ein Schlüssel wird vor dem Senden entfernt
  // (lib/textEingabe.ts). Mit "run_immediately" unterbricht die Nachricht eine laufende Antwort, wie ein Einwurf.
  // Eine Freigabe ersetzt sie nie: Freigaben und Aufträge gehen nur über decide und entscheideAuftrag.
  const textSenden = useCallback((roh: string): boolean => {
    const gen = genRef.current;
    return textAbschicken(roh, {
      bereit: () => {
        const st = wamo.get();
        if (!clientRef.current || !istOnline(st)) return "offline";
        return st.textMoeglich ? "ok" : "kein_text";
      },
      besetzt: () => textLaeuft.current,
      setzeBesetzt: (b) => {
        if (genRef.current === gen) textLaeuft.current = b;
      },
      // höchstens 10 s warten, sonst bleibt das Feld nicht für immer gesperrt
      senden: (text) =>
        clientRef.current
          ? Promise.race([
              clientRef.current.sendText(text, { run_immediately: true, audio_response: true }),
              new Promise<never>((_, nein) => setTimeout(() => nein(new Error("Zeitüberschreitung")), 10_000)),
            ])
          : Promise.reject(new Error("keine Verbindung")),
      hinweis: (text) => setFehler({ art: "hinweis", text }),
      fehler: (art, text) => setFehler({ art, text }),
      angenommen: (text) => {
        if (wamo.get().presence === "speaking") {
          // Unterbrechung durch Tippen: Stimmlinie und Rand zeigen sie wie bei einem gesprochenen Einwurf
          levelsRef.current.cut = true;
          levelsRef.current.cutAt = performance.now();
          planeSchnitt(cutTimer);
        }
        wamo.set((s) => nimmeTextAuf(s, text));
      },
      aktuell: () => genRef.current === gen,
    });
  }, []);

  // Kontingent des Max-Abos (Einstellungen, Abschnitt Verbrauch): den gespeicherten Stand einmal je Verbindung beim
  // Kern abfragen. Er antwortet mit der Servernachricht kontingent; ohne Daten ist der Zustand "unbekannt".
  useEffect(() => {
    let abgefragt: unknown = null;
    const pruefe = () => {
      const s = wamo.get();
      if (!istOnline(s)) {
        abgefragt = null;
        return;
      }
      // erst nach der Betriebsmeldung: sie sendet der Kern, sobald der Client bereit ist
      const client = clientRef.current;
      if (!s.betrieb || !client || abgefragt === client) return;
      abgefragt = client;
      try {
        senden(client, { type: "kontingent", data: {} });
      } catch {
        // die Anzeige ist eine Zugabe: ohne Antwort bleibt es beim Leerzustand
      }
    };
    pruefe();
    return wamo.subscribe(pruefe);
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
      // Büro: Der Kern steuert den PC am Tunnel. Automatisch nur, wenn das genau dieser PC ist (Desktop-App
      // meldet ihren Rechnernamen); vom Laptop oder aus dem Browser schaltet nur der Mensch selbst ein.
      if (b.modus === "buero" && (!b.pcName || !geraet.current || b.pcName.toLowerCase() !== geraet.current.toLowerCase())) {
        return;
      }
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

  // Mikrofone für die Auswahl in der Mikrofon-Warnung (Bezeichnungen gibt es erst nach der Freigabe)
  const mikrofone = useCallback(async (): Promise<Mikrofon[]> => {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    try {
      return geraeteListe(await navigator.mediaDevices.enumerateDevices());
    } catch {
      return [];
    }
  }, []);

  // Gerät wechseln: der Medienmanager öffnet das neue Gerät, onTrackStarted legt die Spur auf die Verbindung
  const mikrofonWaehlen = useCallback((id: string) => {
    if (!id || id.length > 200) return false;
    merkeMikrofon(id);
    const client = clientRef.current;
    if (!client || !istOnline(wamo.get())) return true; // gilt ab dem nächsten Gespräch
    try {
      client.updateMic(id);
    } catch {
      setFehler({ art: "mikrofon", text: "Das Mikrofon ließ sich nicht wechseln. Bitte das Gespräch neu starten." });
      return false;
    }
    zeigeToast("Mikrofon gewechselt");
    return true;
  }, []);

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
    () => ({
      connect,
      disconnect,
      decide,
      entscheideAuftrag,
      pcSteuerung,
      toggleMic,
      vergessen,
      schliesseFehler,
      mikrofone,
      mikrofonWaehlen,
      modellzugang,
      liveBild,
      textSenden,
    }),
    [
      connect,
      disconnect,
      decide,
      entscheideAuftrag,
      pcSteuerung,
      toggleMic,
      vergessen,
      schliesseFehler,
      mikrofone,
      mikrofonWaehlen,
      modellzugang,
      liveBild,
      textSenden,
    ],
  );
}
