"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { CanvasItem } from "@/components/Canvas";
import { gespraechsStartSetzen } from "@/components/kommando/ansicht";
import { istDarstellung, darstellungSetzen } from "@/components/kommando/darstellung";
import { LoginGate } from "@/components/LoginGate";
import { bueroBeispiel } from "@/components/vorschauBuero";
import { WamoAnsicht } from "@/components/WamoAnsicht";
import { bueroAttrappeSetzen } from "@/lib/bueroApi";
import { bildAnzeigen, liveBildLoeschen } from "@/lib/liveBild";
import { stilleText } from "@/lib/mikrofon";
import type { Vorgang, VorgangSchritt } from "@/lib/nachrichten";
import { bereiteText, schluesselHinweis } from "@/lib/textEingabe";
import type { WamoAktionen } from "@/lib/useWamo";
import {
  HISTORY,
  entferneMemo,
  historyRef,
  initialState,
  LIVE_BILD_AUS,
  levelsRef,
  logEintrag,
  nimmeTextAuf,
  resetWamo,
  wamo,
  zeigeMemo,
  zeigeToast,
  type LevelSample,
  type Presence,
  type WamoState,
} from "@/lib/wamo";

/**
 * NUR ENTWICKLUNG: Vorschau der Oberfläche ohne Supabase und ohne Voice-Core.
 * page.tsx lädt dieses Modul ausschließlich bei process.env.NODE_ENV === "development".
 *
 *   /?vorschau=1                    Hauptansicht mit Beispieldaten (WAMO spricht)
 *   /?vorschau=1&zustand=listening  Zustand (offline, connecting, idle, listening, thinking, speaking)
 *   /?vorschau=1&leer=1             leeres Canvas, keine Werkzeuge, kein Verlauf
 *   /?vorschau=1&freigabe=1         mit Freigabe-Karte (freigabe=pc: kritische PC-Aktion mit Ziel und Frist)
 *   /?vorschau=1&handelt=1          ein Werkzeug läuft gerade
 *   /?vorschau=1&auftrag=1          Entwicklungsauftrag wartet (&entwicklung=aus: gesperrt)
 *   /?vorschau=1&pcsteuerung=an     PC-Steuerung dieser Sitzung an
 *   /?vorschau=1&pcauto=1           Autostart der PC-Steuerung (simuliert, ändert die Geräte-Einstellung nicht)
 *   /?vorschau=1&fehler=1           offline mit Fehlermeldung
 *   /?vorschau=1&fehler=mikrofon    Mikrofon liefert keinen Ton (Warnung mit Gerätewahl)
 *   /?vorschau=1&rolle=mitglied     Profil als Mitglied
 *   /?vorschau=1&pc=getrennt        PC-Host getrennt (auch gesperrt, aus)
 *   /?vorschau=anmeldung            Anmeldeformular
 *
 * Kommandozentrale (Darstellung und Beispieldaten):
 *   /?vorschau=1&ansicht=fokus      Darstellung: normal, fokus, zentrale, nacht (gilt nur jetzt, wird nicht gespeichert)
 *   /?vorschau=1&viele=1            drei Freigaben und ein Entwicklungsauftrag (rote Zahl am Reiter Aufträge)
 *   /?vorschau=1&entwicklung=laeuft Entwicklungsauftrag läuft seit 30 s (Zähler der Zentrale)
 *   /?vorschau=1&gemerkt=gemeinsam  zusätzlich ein gemeinsamer Gedächtniseintrag
 *   /?vorschau=1&sitzung=45         Gespräch läuft seit 45 Minuten (Sitzungsfortschritt)
 *   /?vorschau=1&text=aus           Texteingabe gesperrt (ohne Sendefunktion, so ist der heutige Stand)
 *   /?vorschau=1&code=1             zusätzlich ein Code-Element im Canvas (Kopierknopf mit Bestätigung "Kopiert")
 *   /?vorschau=1&vorgaenge=1        Tafel "Vorgänge" mit fünf Beispielen (läuft mit Schritten und Kriterien, wartet auf Freigabe,
 *                                   fertig, fehlgeschlagen, abgebrochen); der Reiter "Vorgänge" ist am Handy der vierte
 *
 * Einstellungen (Adresse mit Hash, zum Beispiel /?vorschau=1&rolle=admin#einstellungen/verbrauch):
 *   &kontingent=beispiel            Kontingent mit drei Limits (sonst leer: noch keine Angaben)
 *   &kontingent=alt                 Kontingent, dessen Fenster schon vorbei ist
 *   &livebild=laeuft                Live-Bild des Laptops (Attrappenbild, kein echter Bildschirm); auch wartet, pausiert, zu
 *                                   (geschlossen); &monitore=1 für einen einzigen Monitor; setzt PC-Steuerung an
 *   &zugang=beide                   Claude-Schlüssel und Claude-Code-Token hinterlegt (sonst nichts hinterlegt)
 *
 * Büro-Karte (Reiter "Büro", Beispieldaten statt Gerätedienst, siehe components/vorschauBuero.ts):
 *   &buero=beispiel                 vierzehn Geräte; auch stoerung, alles, leer, offline (Gerätedienst nicht erreichbar), fehler
 */

/** ISO-Zeitpunkt in UTC (YYYY-MM-DDTHH:MM:SSZ) relativ zu jetzt, wie ihn der Kern schickt. */
const iso = (versatzMs: number) => new Date(Date.now() + versatzMs).toISOString().replace(/\.\d{3}Z$/, "Z");
const MIN = 60_000;
const STUNDE = 60 * MIN;

/** Beispiel für die Servernachricht kontingent; ob das Format stimmt, ist an keinem echten Token geprüft. */
function beispielKontingent(art: string | null): WamoState["kontingent"] {
  if (art === "beispiel") {
    return {
      zustand: "bekannt",
      stand: iso(-90_000),
      limits: [
        { art: "sitzung", prozent: 42, endet: iso(3 * STUNDE + 5 * MIN), status: "ok" },
        { art: "woche", prozent: 13.4, endet: iso(3 * 24 * STUNDE + 22 * STUNDE) },
        { art: "fable", prozent: 104, endet: iso(5 * 24 * STUNDE), status: "erreicht" },
      ],
    };
  }
  if (art === "alt") {
    return { zustand: "bekannt", stand: iso(-6 * STUNDE), limits: [{ art: "sitzung", prozent: 90, endet: iso(-45 * MIN), status: "warnung" }] };
  }
  return null;
}

const ZUSTAENDE: Presence[] = ["offline", "connecting", "idle", "listening", "thinking", "speaking"];

const TABELLE: CanvasItem = {
  id: "vorschau-tabelle",
  kind: "tabelle",
  title: "Offene Testfälle im Projekt Atlas",
  columns: ["Testfall", "Bereich", "Status", "Verantwortlich", "Aufwand (h)"],
  rows: [
    ["TF-101 Anmeldung mit zweitem Faktor", "Zugang", "offen", "Niko", "4,5"],
    ["TF-114 Rechnungsexport als PDF", "Abrechnung", "in Arbeit", "Sabine", "12,0"],
    ["TF-127 Schnittstelle Lagerbestand", "Logistik", "kritisch", "Jonas", "7,25"],
    ["TF-131 Barrierefreiheit Formulare", "Oberfläche", "offen", "Mira", "3,0"],
  ],
};

const DOKUMENT: CanvasItem = {
  id: "vorschau-dokument",
  kind: "dokument",
  title: "Kurzbericht Testlauf",
  text: "## Ergebnis\nDrei von vier Testfällen sind **bestanden**.\n- Zugang stabil\n- Export braucht `pdf-engine` 2.1",
};

const CODE: CanvasItem = {
  id: "vorschau-code",
  kind: "code",
  title: "Abfrage mit JOIN",
  language: "sql",
  text: "select t.id, t.titel, p.name\nfrom testfaelle t\njoin projekte p on p.id = t.projekt_id\nwhere t.status = 'offen';",
};

/** Beispielvorgänge für die Tafel "Vorgänge" (Vorschau mit &vorgaenge=1). Der laufende verweist auf das Dokument im Canvas. */
function beispielVorgaenge(): Vorgang[] {
  const jetzt = Date.now();
  const basis = { restzeitS: null, runde: null, maxRunden: null, freigabeId: null, canvasId: null, beendetMs: null };
  const schritt = (nr: number, titel: string, status: VorgangSchritt["status"], fortschritt: number, detail: string | null = null): VorgangSchritt => ({
    nr,
    titel,
    status,
    fortschritt,
    detail,
  });
  return [
    {
      ...basis,
      id: "vorschau-v1",
      titel: "Umsetzung: PDF-Export im Projekt Atlas",
      art: "entwicklung",
      status: "laeuft",
      gestartetMs: jetzt - 4 * 60_000,
      gesamt: 0.52,
      restzeitS: 142,
      runde: 2,
      maxRunden: 3,
      canvasId: "vorschau-dokument",
      schritte: [
        schritt(1, "Tests schreiben", "fertig", 1),
        schritt(2, "Umlaute in Dateinamen beheben", "laeuft", 0.55, "Setzt die Ersetzung in der Exportfunktion um"),
        schritt(3, "Selbsttest und Lint", "offen", 0),
        schritt(4, "Pull Request vorbereiten", "offen", 0),
      ],
      kriterien: [
        { text: "Selbsttest bestanden", erfuellt: true, hinweis: null },
        { text: "Keine neuen Lint-Fehler", erfuellt: false, hinweis: "2 Funde in export.py" },
        { text: "Pull Request geöffnet", erfuellt: null, hinweis: null },
      ],
    },
    {
      ...basis,
      id: "vorschau-v2",
      titel: "Paket pdf-engine aktualisieren",
      art: "aufgabe",
      status: "wartet_freigabe",
      gestartetMs: jetzt - 90_000,
      gesamt: 0.6,
      freigabeId: "1",
      schritte: [
        schritt(1, "Abhängigkeiten lesen", "fertig", 1),
        schritt(2, "Freigabe: npm install", "wartet_freigabe", 0, "npm install pdf-engine@2.1"),
        schritt(3, "Tests ausführen", "offen", 0),
      ],
      kriterien: [],
    },
    {
      ...basis,
      id: "vorschau-v3",
      titel: "Statusbericht Projekt Atlas",
      art: "aufgabe",
      status: "fertig",
      gestartetMs: jetzt - 20 * 60_000,
      beendetMs: jetzt - 20 * 60_000 + 135_000,
      gesamt: 1,
      canvasId: "vorschau-tabelle",
      schritte: [schritt(1, "Testfälle lesen", "fertig", 1), schritt(2, "Tabelle bauen", "fertig", 1)],
      kriterien: [{ text: "Alle offenen Testfälle enthalten", erfuellt: true, hinweis: null }],
    },
    {
      ...basis,
      id: "vorschau-v4",
      titel: "Mails mit dem Kalender abgleichen",
      art: "aufgabe",
      status: "fehler",
      gestartetMs: jetzt - 45 * 60_000,
      beendetMs: jetzt - 44 * 60_000,
      gesamt: 0.33,
      schritte: [schritt(1, "Kalender lesen", "fertig", 1), schritt(2, "Postfach öffnen", "fehler", 0.2, "Postfach nicht erreichbar"), schritt(3, "Abgleichen", "uebersprungen", 0)],
      kriterien: [],
    },
    {
      ...basis,
      id: "vorschau-v5",
      titel: "Dokument auf dem Drucker im Büro ausgeben",
      art: "geraet",
      status: "abgebrochen",
      gestartetMs: jetzt - 3 * 60 * 60_000,
      beendetMs: jetzt - 3 * 60 * 60_000 + 20_000,
      gesamt: 0,
      schritte: [],
      kriterien: [],
    },
  ];
}

/** Attrappenbild für die Vorschau des Live-Bilds: gezeichnetes Fenster, nichts vom echten Bildschirm. */
function attrappenBild(): string {
  const c = document.createElement("canvas");
  c.width = 960;
  c.height = 540;
  const g = c.getContext("2d");
  if (!g) return "";
  g.fillStyle = "#d9d9d9";
  g.fillRect(0, 0, 960, 540);
  g.fillStyle = "#ffffff";
  g.fillRect(60, 50, 560, 400);
  g.fillStyle = "#000000";
  g.fillRect(60, 50, 560, 34);
  g.fillStyle = "#f40e0e";
  g.fillRect(60, 80, 560, 4);
  g.fillStyle = "#6b6b6b";
  for (let i = 0; i < 9; i++) g.fillRect(84, 112 + i * 34, 300 + ((i * 53) % 190), 12);
  g.fillStyle = "#f3f3f3";
  g.fillRect(660, 90, 240, 300);
  g.fillStyle = "#000000";
  g.font = "bold 26px Arial";
  g.fillText("Attrappe", 690, 140);
  g.fillText("Beispielfenster", 690, 176);
  g.fillStyle = "#f40e0e";
  g.fillRect(0, 500, 960, 40);
  return c.toDataURL("image/jpeg", 0.8).replace(/^data:image\/jpeg;base64,/, "");
}

const KEINE_AKTIONEN: WamoAktionen = {
  connect: async () => {},
  disconnect: async () => {},
  decide: (a) => {
    wamo.set((s) => ({ ...s, approvals: s.approvals.filter((x) => x.id !== a.id) }));
    return true;
  },
  toggleMic: () => wamo.set((s) => ({ ...s, micOn: !s.micOn })),
  vergessen: (m) => entferneMemo(m.key),
  entscheideAuftrag: (a) => {
    wamo.set((s) => ({ ...s, auftraege: s.auftraege.filter((x) => x.job !== a.job) }));
    return true;
  },
  pcSteuerung: (an) => {
    wamo.set((s) => (s.betrieb ? { ...s, betrieb: { ...s.betrieb, pcSteuerung: an ? "an" : "aus" } } : s));
    return true;
  },
  liveBild: {
    an: (monitor) => {
      wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, geoeffnet: true, zustand: "wartet", monitor } }));
      return true;
    },
    aus: () => {
      liveBildLoeschen();
      wamo.set((s) => ({ ...s, liveBild: { ...LIVE_BILD_AUS, monitor: s.liveBild.monitor } }));
      return true;
    },
    monitor: (monitor) => {
      wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, monitor } }));
      return true;
    },
    anfordern: (monitor) => {
      wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, geoeffnet: true, zustand: "wartet", monitor } }));
      return true;
    },
  },
  schliesseFehler: () => wamo.set((s) => ({ ...s, error: null })),
  mikrofone: async () => [
    { id: "vorschau-1", label: "Onboard MIC (Intel Smart Sound)" },
    { id: "vorschau-2", label: "Headset-Mikrofon" },
  ],
  mikrofonWaehlen: () => true,
  // Texteingabe simulieren: gleiche Vorbereitung wie im Gespräch (Schlüssel entfernt), der Text erscheint als Nutzerzeile
  textSenden: (roh) => {
    const t = bereiteText(roh);
    if (!t) return false;
    if (t.geschwaerzt) wamo.set((s) => ({ ...s, error: { art: "hinweis", text: schluesselHinweis(t.senden) } }));
    if (t.senden) wamo.set((s) => nimmeTextAuf(s, t.text));
    return true;
  },
};

function param(name: string) {
  return new URLSearchParams(window.location.search).get(name);
}

export function Vorschau({ modus }: { modus: string }) {
  if (modus === "anmeldung") return <LoginGate vorschau>{() => null}</LoginGate>;
  return <Hauptvorschau />;
}

function beispiel(): { state: WamoState; presence: Presence } {
  const z = param("zustand") as Presence | null;
  const presence: Presence = z && ZUSTAENDE.includes(z) ? z : "speaking";
  const leer = param("leer") === "1";
  const fehler = param("fehler") === "1";
  const micStill = param("fehler") === "mikrofon";
  const pc = param("pc");
  const online = presence !== "offline" && presence !== "connecting" && !fehler;
  const botText = "Hier ist die Übersicht. Vier Testfälle sind offen, einer davon ist kritisch.";
  const state: WamoState = {
    ...initialState,
    presence: fehler ? "offline" : presence,
    error: fehler
      ? { art: "verbindung", text: "Voice-Core unter http://localhost:7860 nicht erreichbar. Läuft bot.py?" }
      : micStill
        ? { art: "mikrofon", text: stilleText("Mikrofon (DisplayLink Audio)") }
        : null,
    mikrofon: online ? "Onboard MIC (Intel Smart Sound)" : null,
    textMoeglich: online,
    // Beispielstand, damit die Karte Modellzugang in der Vorschau keinen echten Kern abfragt
    modellzugang:
      param("zugang") === "beide"
        ? {
            aktion: "status",
            art: "claude",
            quelle: "profil",
            hinterlegt: true,
            ende: "ab12",
            geprueft: iso(-26 * STUNDE),
            festgelegt: false,
            modell: "Claude Haiku 5.5",
            codeHinterlegt: true,
            codeEnde: "cd34",
            codeGesetzt: iso(-5 * 24 * STUNDE),
            modus: "automatisch",
          }
        : { aktion: "status", art: "haus", quelle: "keiner", hinterlegt: false, festgelegt: false, codeHinterlegt: false, modus: "automatisch" },
    kontingent: beispielKontingent(param("kontingent")),
    caption:
      leer || !online
        ? initialState.caption
        : {
            userDone: "Zeig mir die offenen Testfälle im Projekt Atlas als Tabelle.",
            userLive: presence === "listening" ? "und sortier sie nach" : "",
            userLogged: presence !== "listening",
            text: presence === "speaking" ? botText : "",
            spoken: presence === "speaking" ? 7 : 0,
            turnEnd: false,
            interrupted: false,
            segs: [],
          },
    log: leer
      ? []
      : [
          logEintrag({ wer: "nutzer", text: "Was steht heute im Projekt Atlas an?" }),
          logEintrag({ wer: "wamo", text: "Zwei Abstimmungen und der Testlauf um 14 Uhr.", werkzeuge: ["websuche"] }),
          logEintrag({ wer: "nutzer", text: "Zeig mir die offenen Testfälle im Projekt Atlas als Tabelle." }),
        ],
    turn: 2,
    activities: leer
      ? []
      : [
          param("entwicklung") === "laeuft"
            ? { id: "a5", name: "entwicklung_starten", detail: "atlas, Fehler beheben", status: "laeuft", turn: 2, startedAt: performance.now() - 30_000 }
            : param("handelt") === "1"
            ? { id: "a4", name: "webseite_oeffnen", detail: "atlas.wamocon.local", status: "laeuft", turn: 2, startedAt: 0 }
            : { id: "a4", name: "canvas_zeigen", detail: "Tabelle", status: "fertig", turn: 2, startedAt: 0, endedAt: 310 },
          { id: "a3", name: "websuche", detail: "pdf-engine Changelog", status: "fertig", turn: 2, startedAt: 0, endedAt: 1240 },
          { id: "a2", name: "programm_oeffnen", detail: "PC nicht erreichbar", status: "abgelehnt", turn: 2 },
          { id: "a1", name: "aktuelle_zeit", detail: "", status: "fertig", turn: 1, startedAt: 0, endedAt: 80 },
        ],
    approvals:
      param("viele") === "1"
        ? [
            { job: "vorschau", projekt: "atlas", id: "1", grund: "Paket installieren", detail: "npm install pdf-engine@2.1", von: "Niko" },
            { job: "vorschau", projekt: "atlas", id: "2", grund: "Datei löschen", detail: "rm exporte/alt.pdf", von: "Niko" },
            {
              job: "pc",
              projekt: "WMC-PC",
              id: "0123456789abcdef",
              grund: "Nachricht tippen und senden",
              detail: "Bin in fünf Minuten da⏎",
              ziel: "Chat | Microsoft Teams (ms-teams.exe)",
              von: "Niko",
              ablauf: Date.now() + 5 * 60 * 1000,
            },
          ]
        : param("freigabe") === "pc"
        ? [
            {
              job: "pc",
              projekt: "WMC-PC",
              id: "0123456789abcdef",
              grund: "Nachricht tippen und senden",
              detail: "Bin in fünf Minuten da⏎",
              ziel: "Chat | Microsoft Teams (ms-teams.exe)",
              von: "Niko",
              ablauf: Date.now() + 5 * 60 * 1000,
            },
          ]
        : param("freigabe") === "1" || param("vorgaenge") === "1"
        ? [
            {
              job: "vorschau",
              projekt: "atlas",
              id: "1",
              grund: "Paket installieren",
              detail: "npm install pdf-engine@2.1",
              von: "Niko",
            },
          ]
        : [],
    auftraege:
      param("auftrag") === "1" || param("viele") === "1"
        ? [
            {
              job: "dev-42",
              projekt: "atlas",
              art: "Fehler beheben",
              auftrag:
                "Behebe den Fehler im PDF-Export (TF-114): Umlaute in Dateinamen werden ersetzt.\nSchreibe einen Test und öffne einen Pull Request, nichts direkt auf main.",
              abnahme: ["Der Test für Umlaute in Dateinamen läuft grün", "befehl: pytest -q tests/test_export.py"],
            },
          ]
        : [],
    vorgaenge: !leer && param("vorgaenge") === "1" ? beispielVorgaenge() : [],
    canvas: leer ? [] : param("code") === "1" ? [DOKUMENT, TABELLE, CODE] : [DOKUMENT, TABELLE],
    profil: { name: "Niko", rolle: param("rolle") === "mitglied" ? "mitglied" : "admin" },
    betrieb: !online
      ? null
      : {
          modus: "buero",
          modelle: { llm: "Claude Haiku 5.5", stt: "Soniox", tts: "Soniox Nina" },
          pc: pc === "getrennt" || pc === "gesperrt" || pc === "aus" ? pc : "verbunden",
          ort: "cloud",
          pcLatenzMs: 12,
          pcSteuerung: param("pcsteuerung") === "an" || param("livebild") ? "an" : "aus",
          entwicklung: param("entwicklung") === "aus" ? "aus" : "an",
        },
    micOn: true,
  };
  return { state, presence };
}

function Hauptvorschau() {
  const router = useRouter();
  const [{ state, presence }] = useState(beispiel);

  useEffect(() => {
    resetWamo(state);
    // Autostart simulieren: der Kern meldet nach kurzer Zeit "an", dazu der Hinweis
    const autoTimer =
      param("pcauto") === "1" && state.betrieb
        ? setTimeout(() => {
            wamo.set((s) => (s.betrieb ? { ...s, betrieb: { ...s.betrieb, pcSteuerung: "an" } } : s));
            zeigeToast("PC-Steuerung automatisch eingeschaltet");
          }, 1200)
        : null;
    if (param("leer") !== "1" && state.presence !== "offline") {
      zeigeMemo({ bereich: "persoenlich", status: "aktiv", text: "Niko mag kurze Antworten.", id: "m1" });
      if (param("gemerkt") === "gemeinsam") {
        zeigeMemo({ bereich: "gemeinsam", status: "vorgeschlagen", text: "Der Testlauf für Atlas beginnt freitags um 14 Uhr.", id: "m2" });
      }
    }
    // Kommandozentrale: Darstellung nur für diese Ansicht setzen (nicht im Speicher des Geräts ablegen)
    const ansicht = param("ansicht");
    if (istDarstellung(ansicht)) darstellungSetzen(ansicht, false);
    const minuten = Number(param("sitzung"));
    if (Number.isFinite(minuten) && minuten > 0 && state.presence !== "offline" && state.presence !== "connecting") {
      gespraechsStartSetzen(Date.now() - minuten * 60_000);
    }
    // Künstliche Pegel: erst spricht das Profil, dann WAMO, dazwischen eine Unterbrechung
    const welle = (t: number, f: number) => Math.max(0, Math.sin(t * f) * 0.5 + Math.sin(t * f * 2.7) * 0.3);
    const probe = (t: number): LevelSample => ({
      user: presence === "listening" ? 0.08 + 0.1 * welle(t, 0.9) : 0,
      bot: presence === "speaking" ? 0.06 + 0.12 * welle(t, 0.7) : 0,
      cut: false,
      speaker: 0,
    });
    const h = historyRef.current;
    for (let i = 0; i < HISTORY; i++) {
      const t = i * 0.35;
      h.samples.push(
        i < 90
          ? { user: 0.05 + 0.1 * welle(t, 0.9), bot: 0, cut: false, speaker: 0 }
          : i < 150
            ? { user: 0, bot: 0.05 + 0.11 * welle(t, 0.7), cut: false, speaker: 0 }
            : i < 175
              ? { user: 0.06 + 0.08 * welle(t, 1.1), bot: i < 160 ? 0.04 : 0, cut: i === 150, speaker: 0 }
              : probe(t),
      );
    }
    h.seq++;
    // Live-Bild: Zustand setzen, bei "laeuft" ein Attrappenbild zeichnen (nach dem Einhängen der Ansicht)
    const lb = param("livebild");
    let lbTimer: ReturnType<typeof setTimeout> | null = null;
    if (lb && state.betrieb) {
      const monitore = Number(param("monitore")) === 1 ? 1 : 3;
      if (lb !== "zu") {
        wamo.set((s) => ({
          ...s,
          liveBild: {
            geoeffnet: true,
            zustand: lb === "wartet" ? "wartet" : lb === "pausiert" ? "pausiert" : "laeuft",
            grund: lb === "pausiert" ? "vollbild" : lb === "wartet" ? "schalter_aus" : undefined,
            monitor: 1,
            monitore,
            endeMs: Date.now() + 14 * 60_000 + 12_000,
          },
        }));
        if (lb === "laeuft") lbTimer = setTimeout(() => void bildAnzeigen(attrappenBild()), 300);
      }
    }
    let tick = 0;
    const id = setInterval(() => {
      tick++;
      const s = probe(HISTORY * 0.35 + tick * 0.35);
      levelsRef.current = { ...s };
      h.samples.push(s);
      if (h.samples.length > HISTORY) h.samples.shift();
      h.seq++;
    }, 50);
    // Büro-Karte: Beispieldaten statt Kern (&buero=beispiel, stoerung, alles, leer, offline, fehler)
    bueroAttrappeSetzen(bueroBeispiel(param("buero")));
    return () => {
      clearInterval(id);
      if (autoTimer) clearTimeout(autoTimer);
      if (lbTimer) clearTimeout(lbTimer);
      bueroAttrappeSetzen(null);
      resetWamo();
    };
  }, [state, presence]);

  const sitzung = useMemo(() => ({ signOut: () => router.push("/"), email: "niko@example.com" }), [router]);
  const aktionen = useMemo(
    () => (param("text") === "aus" ? { ...KEINE_AKTIONEN, textSenden: undefined } : KEINE_AKTIONEN),
    [],
  );
  return <WamoAnsicht aktionen={aktionen} sitzung={sitzung} />;
}
