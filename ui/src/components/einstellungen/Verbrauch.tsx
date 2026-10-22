"use client";

import { memo, useMemo } from "react";
import { useJetzt } from "@/components/kommando/useJetzt";
import { useSelect } from "@/lib/store";
import { VERBRAUCH_FORMAT_HINWEIS, VERBRAUCH_LEER, verbrauchAnzeige, type VerbrauchZeile } from "@/lib/verbrauch";
import { wamo } from "@/lib/wamo";
import { Block } from "./Block";

/**
 * Abschnitt "Verbrauch": Kontingent des Max-Abos aus der Servernachricht "kontingent" (lib/nachrichten.ts, im Store als
 * kontingent). Sitzungslimit (5 Stunden), Wochenlimit und Fable limit als Balken mit Prozent und dem Zeitpunkt, an dem
 * das Fenster sich zurücksetzt. Der Balken ist nur die Anschauung: Prozent, Status und Zeit stehen als Text daneben, die
 * Seite verlässt sich nie auf Farbe.
 *
 * FORMAT UNGEPRÜFT: Das Datenformat des Kerns ist noch an keinem echten Token bestätigt (lib/verbrauch.ts). Deshalb zeigt
 * die Seite ausdrücklich einen Hinweis, sobald sie Werte zeigt, und ehrlich "keine Angabe", wo etwas fehlt.
 */

function Limit({ z }: { z: VerbrauchZeile }) {
  return (
    <li className="ez-limit" data-status={z.status ?? undefined} data-gemeldet={z.gemeldet || undefined}>
      <div className="ez-limit-kopf">
        <h4 className="ez-limit-titel">{z.titel}</h4>
        <span className="ez-limit-art">{z.erklaerung}</span>
      </div>
      <div className="ez-bahn" aria-hidden="true">
        <span style={{ width: `${z.balken}%` }} />
      </div>
      <dl className="ez-daten ez-limit-daten">
        <div>
          <dt>Genutzt</dt>
          <dd className="num">
            {z.prozentText}
            {z.statusText && <span className="ez-limit-status">, {z.statusText}</span>}
          </dd>
        </div>
        <div>
          <dt>Setzt sich zurück</dt>
          <dd>
            {z.zuruecksetzen ? (
              z.zuruecksetzen.vorbei ? (
                <>
                  {z.zuruecksetzen.absolut} ({z.zuruecksetzen.relativ}). Das Fenster ist schon zurückgesetzt, der Wert ist veraltet.
                </>
              ) : (
                <>
                  {z.zuruecksetzen.absolut} ({z.zuruecksetzen.relativ})
                </>
              )
            ) : (
              "keine Angabe"
            )}
          </dd>
        </div>
      </dl>
    </li>
  );
}

export const VerbrauchAbschnitt = memo(function VerbrauchAbschnitt() {
  const kontingent = useSelect(wamo, (s) => s.kontingent);
  // Die Zeiten "in 3 Stunden" ändern sich: halbminütlich neu rechnen, nur diese kleine Komponente rendert dafür
  const jetzt = useJetzt(30_000).datum;
  const anzeige = useMemo(() => verbrauchAnzeige(kontingent, jetzt), [kontingent, jetzt]);

  return (
    <Block titel="Kontingent des Claude-Abos">
      <p className="ez-text">
        So viel von deinem Abo haben Entwicklungsaufträge mit deinem Claude-Code-Token schon verbraucht. WAMO liest die Werte
        aus den Antworten von Claude Code und fragt dafür nichts zusätzlich an.
      </p>
      {anzeige.leer ? (
        <p className="ez-leer" role="status">
          {VERBRAUCH_LEER}
        </p>
      ) : (
        <>
          <ul className="ez-limits">
            {anzeige.zeilen.map((z) => (
              <Limit key={z.art} z={z} />
            ))}
          </ul>
          {anzeige.stand && <p className="ez-klein">Stand vom Kern: {anzeige.stand}.</p>}
          {!anzeige.formatGeprueft && <p className="ez-klein">{VERBRAUCH_FORMAT_HINWEIS}</p>}
        </>
      )}
    </Block>
  );
});
