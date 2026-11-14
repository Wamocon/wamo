"use client";

import { memo, useEffect, useId, useRef, useState } from "react";
import { einstellungenSchliessen } from "@/lib/einstellungenHash";
import {
  ergebnisText,
  kannEinlernen,
  kannLoeschen,
  modusText,
  stimmeStatusText,
} from "@/lib/stimme";
import { useSelect } from "@/lib/store";
import type { StimmabdruckAktionen } from "@/lib/useWamo";
import { abdruckAnsichtOeffnen, istOnline, wamo } from "@/lib/wamo";
import { Block, Daten } from "./Block";

/**
 * Abschnitt "Stimme" der Einstellungen (nicht in der Laborsicht, lib/laborPlatz.ts): Stand der Stimmerkennung, Einlernen oder Neu einlernen
 * (öffnet die Einlernansicht in der Mitte und schließt die Einstellungen, damit man sie sieht) und Abdruck löschen mit Rückfrage.
 * Einlernen und Löschen brauchen eine laufende Sitzung, denn der Kern prüft und speichert. Gelöscht wird erst nach einem ausdrücklichen Ja,
 * der Fokus der Rückfrage liegt auf "Behalten". Die Bestätigung kommt vom Kern (Ansage "Stimmabdruck gelöscht", Status "Noch nicht eingelernt").
 */
export const StimmeAbschnitt = memo(function StimmeAbschnitt({ aktionen }: { aktionen: StimmabdruckAktionen | undefined }) {
  const stand = useSelect(wamo, (s) => s.stimme);
  const online = useSelect(wamo, istOnline);
  const [frage, setFrage] = useState(false);
  const [nachricht, setNachricht] = useState<{ art: "status" | "alert"; text: string } | null>(null);
  const frageId = useId();
  const behalten = useRef<HTMLButtonElement>(null);
  const loeschKnopf = useRef<HTMLButtonElement>(null);
  const einlernKnopf = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (frage) behalten.current?.focus();
  }, [frage]);

  const einlernenOk = kannEinlernen(stand, online);
  const loeschenOk = kannLoeschen(stand, online) && aktionen !== undefined;

  const einlernen = () => {
    if (!abdruckAnsichtOeffnen()) return;
    einstellungenSchliessen();
  };
  const frageSchliessen = () => {
    setFrage(false);
    loeschKnopf.current?.focus(); // der Fokus bleibt bei dem, was die Frage geöffnet hat
  };
  const loeschen = () => {
    setFrage(false);
    einlernKnopf.current?.focus(); // der Abdruck-löschen-Knopf verschwindet gleich, wenn der Kern bestätigt
    const ging = aktionen?.loeschen() ?? false;
    setNachricht(
      ging
        ? { art: "status", text: "Löschen angefragt. Der Kern bestätigt es gleich." }
        : { art: "alert", text: "Das Löschen ging nicht hinaus. Prüfe die Verbindung und versuche es noch einmal." },
    );
  };

  return (
    <Block titel="Stimmerkennung">
      <Daten
        zeilen={[
          { name: "Status", wert: stimmeStatusText(stand) },
          { name: "Betriebsart", wert: modusText(stand.modus) },
          { name: "Letzte Äußerung", wert: ergebnisText(stand) },
        ]}
      />
      <p className="ez-text">
        WAMO erkennt an deinem Stimmabdruck, ob du sprichst. Dafür liest du einmal einen kurzen Text vor. Gespeichert wird nur der Abdruck,
        keine Aufnahme, und du kannst ihn jederzeit löschen.
      </p>
      {!online ? (
        <p className="ez-klein">Starte ein Gespräch, dann lässt sich die Stimme einlernen oder der Abdruck löschen.</p>
      ) : stand.status === "aus" ? (
        <p className="ez-klein">Die Stimmerkennung ist in dieser Sitzung nicht eingeschaltet.</p>
      ) : stand.status === "laedt" ? (
        <p className="ez-klein">Die Stimmerkennung wird gerade geladen. Einlernen geht gleich.</p>
      ) : null}
      <div className="ez-tasten">
        <button type="button" ref={einlernKnopf} className="btn-primary" disabled={!einlernenOk} onClick={einlernen}>
          {stand.status === "bereit" ? "Neu einlernen" : "Stimme einlernen"}
        </button>
        {loeschenOk && (
          <button
            type="button"
            ref={loeschKnopf}
            className="btn-secondary"
            aria-expanded={frage}
            onClick={() => {
              setNachricht(null);
              setFrage(true);
            }}
          >
            Abdruck löschen
          </button>
        )}
      </div>
      {frage && (
        <div className="ez-rueckfrage" role="group" aria-labelledby={frageId}>
          <p id={frageId}>Abdruck wirklich löschen? Danach erkennt WAMO deine Stimme nicht mehr, bis du sie neu einlernst.</p>
          <div className="ez-tasten">
            <button type="button" ref={behalten} className="btn-secondary" onClick={frageSchliessen}>
              Behalten
            </button>
            <button type="button" className="btn-secondary ez-gefahr" onClick={loeschen}>
              Ja, Abdruck löschen
            </button>
          </div>
        </div>
      )}
      {nachricht && (
        <p className="ez-klein" role={nachricht.art === "alert" ? "alert" : "status"}>
          {nachricht.text}
        </p>
      )}
    </Block>
  );
});
