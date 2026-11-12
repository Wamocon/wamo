"use client";

import { memo, useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import {
  ART_TEXT,
  ENTSCHEIDUNG_KNOPF,
  ergebnisText,
  TEXT_ERKLAERUNG,
  zeitKurz,
  zusammenfassung,
  type Entscheidung,
  type Vorschlag,
} from "@/lib/gedaechtnis";
import { vorschlaegeLaden, vorschlagEntscheiden } from "@/lib/gedaechtnisApi";
import { Block } from "./Block";

type Meldung = { art: "ok" | "fehler"; text: string };
type Unterwegs = { id: string; entscheidung: Entscheidung };

/**
 * Abschnitt "Gedächtnis" (nur Admin): die gemeinsamen Vorschläge, die auf eine Entscheidung warten (Kern: voice-core/gedaechtnis_api.py, Migration
 * 0020). Je Vorschlag der volle Text, die Art, der Autor und die Zeit; darunter "Für alle freigeben" und "Ablehnen", gleich groß und gleich
 * gewichtet. Der Text steht nur als Text da (er ist Daten, nie eine Anweisung). Ein Tipp entscheidet, während ein Befehl läuft, gilt kein
 * weiterer (aria-disabled, damit der Fokus bleibt); danach geht der Fokus an den nächsten Vorschlag oder an "Neu laden". Die Meldung steht in
 * einem Statusbereich. Die Liste holt sich nicht von selbst neu, nur beim Öffnen und mit "Neu laden".
 */
export const GedaechtnisAbschnitt = memo(function GedaechtnisAbschnitt() {
  const [liste, setListe] = useState<Vorschlag[] | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [geladen, setGeladen] = useState(false);
  const [laedt, setLaedt] = useState(false);
  const [meldung, setMeldung] = useState<Meldung | null>(null);
  const [unterwegs, setUnterwegs] = useState<Unterwegs | null>(null);
  const aktiv = useRef(true);
  const beschaeftigt = useRef(false); // sofort gesetzt, damit ein zweiter Tipp im selben Takt nichts auslöst
  const fokusNoetig = useRef(false);
  const behaelter = useRef<HTMLDivElement>(null);
  const neuLaden = useRef<HTMLButtonElement>(null);

  const laden = useCallback(async () => {
    setLaedt(true);
    const erg = await vorschlaegeLaden();
    if (!aktiv.current) return;
    if (erg.ok) {
      setListe(erg.vorschlaege);
      setFehler(null);
    } else {
      setFehler(erg.text);
    }
    setGeladen(true);
    setLaedt(false);
  }, []);

  useEffect(() => {
    aktiv.current = true;
    const erster = setTimeout(() => void laden(), 0); // nicht im Effekt selbst: Der erste Abruf setzt Zustand
    return () => {
      aktiv.current = false;
      clearTimeout(erster);
    };
  }, [laden]);

  // Nach einer Entscheidung verschwindet die Karte mit dem Fokus: an den nächsten Vorschlag oder an "Neu laden".
  useEffect(() => {
    if (!fokusNoetig.current) return;
    fokusNoetig.current = false;
    const aktivesElement = document.activeElement;
    if (aktivesElement && aktivesElement !== document.body) return;
    (behaelter.current?.querySelector<HTMLButtonElement>("button[data-entscheidung]") ?? neuLaden.current)?.focus();
  }, [liste]);

  const entscheiden = async (v: Vorschlag, entscheidung: Entscheidung, e: MouseEvent<HTMLButtonElement>) => {
    if (e.detail > 1 || beschaeftigt.current) return; // ein Doppelklick zählt einmal
    beschaeftigt.current = true;
    setUnterwegs({ id: v.id, entscheidung });
    setMeldung(null);
    const erg = await vorschlagEntscheiden(v.id, entscheidung);
    beschaeftigt.current = false;
    if (!aktiv.current) return;
    setUnterwegs(null);
    if (!erg.ok) {
      setMeldung({ art: "fehler", text: erg.text });
      return;
    }
    fokusNoetig.current = true;
    setListe((alt) => (alt ?? []).filter((x) => x.id !== v.id));
    setMeldung({ art: "ok", text: ergebnisText(erg.ergebnis) });
    if (erg.ergebnis === "nicht_offen") void laden();
  };

  const anzahl = liste?.length ?? 0;

  return (
    <Block titel="Offene Vorschläge fürs gemeinsame Gedächtnis">
      <p className="ez-text">{TEXT_ERKLAERUNG}</p>
      {!geladen ? (
        <p className="ez-leer" role="status">
          Lade die Vorschläge ...
        </p>
      ) : fehler ? (
        <p className="gv-meldung" role="alert" data-art="fehler">
          {fehler}
        </p>
      ) : (
        <p className="ez-klein">{zusammenfassung(anzahl)}</p>
      )}

      {liste && liste.length > 0 && (
        <div ref={behaelter}>
          <ul className="gv-liste" aria-label="Offene Vorschläge">
            {liste.map((v) => {
              const zeit = zeitKurz(v.angelegt);
              return (
                <li key={v.id} className="gv-eintrag">
                  <p className="gv-kopf">
                    <span className="gv-art">{ART_TEXT[v.art]}</span>
                    <span>von {v.autor}</span>
                    {zeit && <span>{zeit}</span>}
                  </p>
                  <p className="gv-text">{v.text}</p>
                  <div className="gv-knoepfe" role="group" aria-label={`Entscheidung zu: ${v.text.slice(0, 60)}`}>
                    {(["freigeben", "ablehnen"] as const).map((entscheidung) => (
                      <button
                        key={entscheidung}
                        type="button"
                        className="btn-secondary gv-knopf"
                        data-entscheidung={entscheidung}
                        aria-disabled={unterwegs !== null ? true : undefined}
                        aria-busy={unterwegs?.id === v.id && unterwegs.entscheidung === entscheidung ? true : undefined}
                        onClick={(e) => void entscheiden(v, entscheidung, e)}
                      >
                        {unterwegs?.id === v.id && unterwegs.entscheidung === entscheidung ? "Wird gespeichert ..." : ENTSCHEIDUNG_KNOPF[entscheidung]}
                      </button>
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="ae-aktionen">
        <button ref={neuLaden} type="button" className="btn-secondary" onClick={() => void laden()} disabled={laedt}>
          {laedt ? "Lade ..." : "Neu laden"}
        </button>
      </div>
      <p className="gv-meldung" role="status" aria-live="polite" data-art={meldung?.art}>
        {meldung?.text ?? ""}
      </p>
    </Block>
  );
});
