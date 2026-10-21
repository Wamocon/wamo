"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { CanvasItem } from "@/components/Canvas";
import { gespraechsStartSetzen } from "@/components/kommando/ansicht";
import { istDarstellung, darstellungSetzen } from "@/components/kommando/darstellung";
import { LoginGate } from "@/components/LoginGate";
import { WamoAnsicht } from "@/components/WamoAnsicht";
import type { WamoAktionen } from "@/lib/useWamo";
import {
  HISTORY,
  historyRef,
  initialState,
  levelsRef,
  logEintrag,
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
 */

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

const KEINE_AKTIONEN: WamoAktionen & { textSenden?: (text: string) => boolean } = {
  connect: async () => {},
  disconnect: async () => {},
  decide: (a) => {
    wamo.set((s) => ({ ...s, approvals: s.approvals.filter((x) => x.id !== a.id) }));
    return true;
  },
  toggleMic: () => wamo.set((s) => ({ ...s, micOn: !s.micOn })),
  vergessen: () => {},
  entscheideAuftrag: (a) => {
    wamo.set((s) => ({ ...s, auftraege: s.auftraege.filter((x) => x.job !== a.job) }));
    return true;
  },
  pcSteuerung: (an) => {
    wamo.set((s) => (s.betrieb ? { ...s, betrieb: { ...s.betrieb, pcSteuerung: an ? "an" : "aus" } } : s));
    return true;
  },
  schliesseFehler: () => wamo.set((s) => ({ ...s, error: null })),
  // Texteingabe simulieren: der Text erscheint als Nutzerzeile der Untertitel
  textSenden: (text) => {
    wamo.set((s) => ({ ...s, caption: { ...s.caption, userDone: text, userLive: "", userLogged: false } }));
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
  const pc = param("pc");
  const online = presence !== "offline" && presence !== "connecting" && !fehler;
  const botText = "Hier ist die Übersicht. Vier Testfälle sind offen, einer davon ist kritisch.";
  const state: WamoState = {
    ...initialState,
    presence: fehler ? "offline" : presence,
    error: fehler
      ? { art: "verbindung", text: "Voice-Core unter http://localhost:7860 nicht erreichbar. Läuft bot.py?" }
      : null,
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
        : param("freigabe") === "1"
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
            },
          ]
        : [],
    canvas: leer ? [] : [DOKUMENT, TABELLE],
    profil: { name: "Niko", rolle: param("rolle") === "mitglied" ? "mitglied" : "admin" },
    betrieb: !online
      ? null
      : {
          modus: "buero",
          modelle: { llm: "Claude Haiku 5.5", stt: "Soniox", tts: "Soniox Nina" },
          pc: pc === "getrennt" || pc === "gesperrt" || pc === "aus" ? pc : "verbunden",
          ort: "cloud",
          pcLatenzMs: 12,
          pcSteuerung: param("pcsteuerung") === "an" ? "an" : "aus",
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
    let tick = 0;
    const id = setInterval(() => {
      tick++;
      const s = probe(HISTORY * 0.35 + tick * 0.35);
      levelsRef.current = { ...s };
      h.samples.push(s);
      if (h.samples.length > HISTORY) h.samples.shift();
      h.seq++;
    }, 50);
    return () => {
      clearInterval(id);
      if (autoTimer) clearTimeout(autoTimer);
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
