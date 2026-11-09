"use client";

import { useCallback, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";
import { offeneEntscheidungen, useBreit, useEffektiveDarstellung } from "./ansicht";
import { DarstellungsWahl } from "./DarstellungsWahl";
import { RahmenKontext } from "./rahmenKontext";
import { Reiterleiste, type ReiterDef } from "./Reiter";

type Rechts = "auftraege" | "vorgaenge" | "erledigt" | "buero";
type Mobil = "kern" | "auftraege" | "vorgaenge" | "canvas" | "hinweise" | "buero";

/** Alle Bausteine kommen von WamoAnsicht; der Rahmen legt sie nur an und verwaltet die Reiter. */
export type RahmenInhalt = {
  kopf: ReactNode;
  /** Fehler und kurze Hinweise: immer sichtbar, auch am Handy auf jedem Reiter */
  meldungen: ReactNode;
  kernbereich: ReactNode;
  /** Gespräch starten oder beenden; am Handy unten fixiert, auf jedem Reiter erreichbar */
  aktionen: ReactNode;
  bedienung: ReactNode;
  canvas: ReactNode;
  hinweise: ReactNode;
  auftraege: ReactNode;
  vorgaenge: ReactNode;
  erledigt: ReactNode;
  /** Büro-Karte (kommando/BueroKarte.tsx); wird nur eingehängt, solange der Reiter "Büro" sichtbar ist, dann ruht der Abruf */
  buero?: ReactNode;
  /** Verlauf und Ansage für Screenreader */
  anhang: ReactNode;
  /** Einstellungsseite; nur gesetzt, solange sie offen ist. Die Kommandozentrale bleibt dabei eingehängt und wird nur verborgen. */
  einstellungen?: ReactNode;
};

const RECHTS: readonly ReiterDef<Rechts>[] = [
  { id: "auftraege", label: "Aufträge", zahlWort: "offen" },
  { id: "vorgaenge", label: "Vorgänge" },
  { id: "erledigt", label: "Erledigt" },
  { id: "buero", label: "Büro" },
];

/** Rolle der Tafeln: tabpanel nur mit sichtbarem Reiter (labelledby), sonst ein benannter Bereich. */
const tafel = (name: string, reiter: string | null) =>
  reiter ? ({ role: "tabpanel", "aria-labelledby": reiter } as const) : ({ role: "region", "aria-label": name } as const);

/**
 * Rahmen der Kommandozentrale. Ab 1100 px drei Spalten: links Hinweise, in der Mitte der Kern mit
 * Bedienung und Canvas, rechts Reiter (Aufträge, Vorgänge, Erledigt). Darunter eine Spalte mit
 * Reiterleiste (Kern, Aufträge, Vorgänge, Canvas, Hinweise). Welche Tafel sichtbar ist, entscheidet
 * das CSS über data-rechts und data-mobil; jede Tafel steht genau einmal im DOM.
 *
 * Neue Freigaben oder Aufträge (eine Kennung, die vorher fehlte) schalten beide Reiterleisten sofort auf
 * "Aufträge", noch im selben Render, damit die Karte sichtbar entsteht und ihre Fokusführung greift.
 * Rollen: Tafeln sind nur dort role="tabpanel", wo die zugehörige Reiterleiste sichtbar ist (useBreit);
 * sonst Bereiche (region) mit Namen. Nacht erzwingt dunkel über data-theme auf html (das Theme-Gerüst
 * in globals.css kennt es bereits) und stellt den vorigen Wert wieder her.
 */
export function KommandoRahmen(inhalt: RahmenInhalt) {
  const { gewaehlt, effektiv } = useEffektiveDarstellung();
  const breit = useBreit();
  const offen = useSelect(wamo, offeneEntscheidungen);
  // Kennungen aller offenen Karten: auch eine Karte, die eine andere ersetzt, zählt als neu
  const kennungen = useSelect(wamo, (s) =>
    [...s.approvals.map((a) => `${a.job}:${a.id}`), ...s.auftraege.map((a) => `auftrag:${a.job}`)].join("|"),
  );
  const [rechts, setRechts] = useState<Rechts>("auftraege");
  const [mobil, setMobil] = useState<Mobil>(() => (offen > 0 ? "auftraege" : "kern"));
  const [gesehen, setGesehen] = useState(kennungen);

  // Zustand beim Rendern anpassen (React-Muster): kein Zwischenbild mit versteckter Karte
  if (kennungen !== gesehen) {
    setGesehen(kennungen);
    const alt = new Set(gesehen.split("|"));
    if (kennungen.split("|").some((k) => k !== "" && !alt.has(k))) {
      setRechts("auftraege");
      setMobil("auftraege");
    }
  }
  const mobilEff: Mobil = effektiv === "normal" || effektiv === "nacht" ? mobil : "kern";
  // Die Büro-Karte läuft nur, solange ihr Reiter wirklich zu sehen ist (nicht in Fokus und Zentrale, nicht unter den Einstellungen)
  const bueroAktiv = !inhalt.einstellungen && (breit ? effektiv !== "fokus" && effektiv !== "zentrale" && rechts === "buero" : mobilEff === "buero");

  useLayoutEffect(() => {
    if (effektiv !== "nacht") return;
    const el = document.documentElement;
    const vorher = el.getAttribute("data-theme");
    el.setAttribute("data-theme", "dark");
    return () => {
      if (vorher === null) el.removeAttribute("data-theme");
      else el.setAttribute("data-theme", vorher);
    };
  }, [effektiv]);

  const zeigeCanvas = useCallback(() => {
    setMobil("canvas");
    // erst nach dem Umschalten scrollen und den Fokus setzen; die Mitte ist der scrollende Bereich
    requestAnimationFrame(() => {
      const kurz = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const canvas = document.getElementById("kz-p-canvas");
      canvas?.focus({ preventScroll: true });
      canvas?.scrollIntoView({ behavior: kurz ? "auto" : "smooth", block: "start" });
    });
  }, []);
  const angebot = useMemo(() => ({ zeigeCanvas }), [zeigeCanvas]);

  const rechtsReiter = useMemo(() => RECHTS.map((r) => (r.id === "auftraege" ? { ...r, zahl: offen } : r)), [offen]);
  const mobilReiter = useMemo<readonly ReiterDef<Mobil>[]>(
    () => [
      { id: "kern", label: "Kern", ohneTafel: true },
      { id: "auftraege", label: "Aufträge", zahl: offen, zahlWort: "offen" },
      { id: "vorgaenge", label: "Vorgänge" },
      { id: "canvas", label: "Canvas" },
      { id: "hinweise", label: "Hinweise" },
      { id: "buero", label: "Büro" },
    ],
    [offen],
  );

  return (
    <RahmenKontext.Provider value={angebot}>
      <div
        className="shell kz"
        data-ansicht={effektiv}
        data-gewaehlt={gewaehlt}
        data-rechts={rechts}
        data-mobil={mobilEff}
      >
        {inhalt.kopf}
        <main className="kz-main" hidden={Boolean(inhalt.einstellungen)}>
          <div className="kz-sterne" aria-hidden="true" />
          {effektiv !== "fokus" && effektiv !== "zentrale" && (
            <Reiterleiste
              name="Bereiche"
              reiter={mobilReiter}
              aktiv={mobil}
              onWahl={setMobil}
              praefix="kz-tab-m"
              klasse="kz-reiterleiste kz-reiter-mobil"
            />
          )}

          <div id="kz-p-hinweise" className="kz-links" data-p="hinweise" {...tafel("Hinweise", breit ? null : "kz-tab-m-hinweise")}>
            {inhalt.hinweise}
          </div>

          <div className="kz-mitte">
            <div className="kz-kopf" data-p="kern">
              {effektiv === "zentrale" && (
                <p className="kz-titel">
                  Kommandozentrale
                  <span className="kz-seiten" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                </p>
              )}
              <DarstellungsWahl />
            </div>
            <div className="kz-meldungen">{inhalt.meldungen}</div>
            {inhalt.kernbereich}
            <div className="kz-aktionen">{inhalt.aktionen}</div>
            {inhalt.bedienung}
            <section
              id="kz-p-canvas"
              className="kz-canvas"
              data-p="canvas"
              tabIndex={-1}
              {...tafel("Canvas", breit ? null : "kz-tab-m-canvas")}
            >
              {inhalt.canvas}
            </section>
          </div>

          <div className="kz-rechts">
            <Reiterleiste
              name="Aufträge, Vorgänge, Erledigt und Büro"
              reiter={rechtsReiter}
              aktiv={rechts}
              onWahl={setRechts}
              praefix="kz-tab-d"
              klasse="kz-reiterleiste kz-reiter-seite"
            />
            <div
              id="kz-p-auftraege"
              className="kz-tafel"
              data-p="auftraege"
              {...tafel("Aufträge", breit ? "kz-tab-d-auftraege" : "kz-tab-m-auftraege")}
            >
              <h2 className="sr-only">Aufträge und Freigaben</h2>
              {inhalt.auftraege}
            </div>
            <div
              id="kz-p-vorgaenge"
              className="kz-tafel"
              data-p="vorgaenge"
              {...tafel("Vorgänge", breit ? "kz-tab-d-vorgaenge" : "kz-tab-m-vorgaenge")}
            >
              {inhalt.vorgaenge}
            </div>
            <div
              id="kz-p-erledigt"
              className="kz-tafel"
              data-p="erledigt"
              {...tafel("Erledigt", breit ? "kz-tab-d-erledigt" : null)}
            >
              {inhalt.erledigt}
            </div>
            <div id="kz-p-buero" className="kz-tafel" data-p="buero" {...tafel("Büro", breit ? "kz-tab-d-buero" : "kz-tab-m-buero")}>
              {bueroAktiv ? inhalt.buero : null}
            </div>
          </div>
        </main>
        {inhalt.einstellungen}
        {inhalt.anhang}
      </div>
    </RahmenKontext.Provider>
  );
}
