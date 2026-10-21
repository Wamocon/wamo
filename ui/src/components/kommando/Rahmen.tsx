"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";
import { offeneEntscheidungen, useEffektiveDarstellung } from "./ansicht";
import { DarstellungsWahl } from "./DarstellungsWahl";
import { RahmenKontext } from "./rahmenKontext";
import { Reiterleiste, type ReiterDef } from "./Reiter";

type Rechts = "auftraege" | "vorgaenge" | "erledigt";
type Mobil = "kern" | "auftraege" | "vorgaenge" | "canvas" | "hinweise";

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
  /** Verlauf und Ansage für Screenreader */
  anhang: ReactNode;
};

const RECHTS: readonly ReiterDef<Rechts>[] = [
  { id: "auftraege", label: "Aufträge", zahlWort: "offen" },
  { id: "vorgaenge", label: "Vorgänge" },
  { id: "erledigt", label: "Erledigt" },
];

/**
 * Rahmen der Kommandozentrale. Ab 1100 px drei Spalten: links Hinweise, in der Mitte der Kern mit
 * Bedienung und Canvas, rechts Reiter (Aufträge, Vorgänge, Erledigt). Darunter eine Spalte mit
 * Reiterleiste (Kern, Aufträge, Vorgänge, Canvas, Hinweise). Welche Tafel sichtbar ist, entscheidet
 * das CSS über data-rechts und data-mobil; jede Tafel steht genau einmal im DOM.
 *
 * Neue Freigaben oder Aufträge schalten beide Reiterleisten sofort auf "Aufträge" (noch im selben
 * Render, damit die Karte sichtbar entsteht und ihre Fokusführung greift). Nacht erzwingt dunkel
 * über data-theme auf html (das Theme-Gerüst in globals.css kennt es bereits) und stellt es wieder her.
 */
export function KommandoRahmen(inhalt: RahmenInhalt) {
  const { gewaehlt, effektiv } = useEffektiveDarstellung();
  const offen = useSelect(wamo, offeneEntscheidungen);
  const [rechts, setRechts] = useState<Rechts>("auftraege");
  const [mobil, setMobil] = useState<Mobil>("kern");
  const [gesehen, setGesehen] = useState(offen);

  // Zustand beim Rendern anpassen (React-Muster): kein Zwischenbild mit versteckter Karte
  if (offen !== gesehen) {
    setGesehen(offen);
    if (offen > gesehen) {
      setRechts("auftraege");
      setMobil("auftraege");
    }
  }
  const mobilEff: Mobil = effektiv === "normal" || effektiv === "nacht" ? mobil : "kern";

  useEffect(() => {
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
    // erst nach dem Umschalten scrollen; die Mitte ist der scrollende Bereich
    requestAnimationFrame(() => {
      const kurz = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      document.getElementById("kz-p-canvas")?.scrollIntoView({ behavior: kurz ? "auto" : "smooth", block: "start" });
    });
  }, []);
  const angebot = useMemo(() => ({ zeigeCanvas }), [zeigeCanvas]);

  const rechtsReiter = useMemo(() => RECHTS.map((r) => (r.id === "auftraege" ? { ...r, zahl: offen } : r)), [offen]);
  const mobilReiter = useMemo<readonly ReiterDef<Mobil>[]>(
    () => [
      { id: "kern", label: "Kern" },
      { id: "auftraege", label: "Aufträge", zahl: offen, zahlWort: "offen" },
      { id: "vorgaenge", label: "Vorgänge" },
      { id: "canvas", label: "Canvas" },
      { id: "hinweise", label: "Hinweise" },
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
        <main className="kz-main">
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

          <div id="kz-p-hinweise" className="kz-links" data-p="hinweise" role="tabpanel" aria-label="Hinweise">
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
            <section id="kz-p-canvas" className="kz-canvas" data-p="canvas" role="tabpanel" aria-label="Canvas">
              {inhalt.canvas}
            </section>
          </div>

          <div className="kz-rechts">
            <Reiterleiste
              name="Aufträge und Vorgänge"
              reiter={rechtsReiter}
              aktiv={rechts}
              onWahl={setRechts}
              praefix="kz-tab-d"
              klasse="kz-reiterleiste kz-reiter-seite"
            />
            <div id="kz-p-auftraege" className="kz-tafel" data-p="auftraege" role="tabpanel" aria-label="Aufträge">
              {inhalt.auftraege}
            </div>
            <div id="kz-p-vorgaenge" className="kz-tafel" data-p="vorgaenge" role="tabpanel" aria-label="Vorgänge">
              {inhalt.vorgaenge}
            </div>
            <div id="kz-p-erledigt" className="kz-tafel" data-p="erledigt" role="tabpanel" aria-label="Erledigt">
              {inhalt.erledigt}
            </div>
          </div>
        </main>
        {inhalt.anhang}
      </div>
    </RahmenKontext.Provider>
  );
}
