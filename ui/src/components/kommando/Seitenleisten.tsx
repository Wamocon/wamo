"use client";

import { memo, useEffect, useState, type ReactNode } from "react";
import { LABEL } from "@/components/ActivityFeed";
import { useSelect } from "@/lib/store";
import { wamo, type Memo } from "@/lib/wamo";
import { offeneEntscheidungen } from "./ansicht";

/** Freundlicher Hinweis, wenn ein Bereich noch leer ist. */
function Leer({ children }: { children: ReactNode }) {
  return <p className="kz-leer">{children}</p>;
}

export const AuftraegeLeer = memo(function AuftraegeLeer() {
  const offen = useSelect(wamo, offeneEntscheidungen);
  if (offen) return null;
  return <Leer>Nichts wartet auf dich. Freigaben und Entwicklungsaufträge erscheinen hier.</Leer>;
});

export const VorgaengeLeer = memo(function VorgaengeLeer() {
  const anzahl = useSelect(wamo, (s) => s.activities.length);
  if (anzahl) return null;
  return <Leer>Noch keine Vorgänge. Sobald WAMO ein Werkzeug nutzt, siehst du es hier.</Leer>;
});

const sekunden = (ms: number) => `${(ms / 1000).toFixed(1).replace(".", ",")} s`;

/** Erledigtes: fertige Werkzeugschritte der Sitzung (neueste oben) mit Dauer. */
export const ErledigtListe = memo(function ErledigtListe() {
  const alle = useSelect(wamo, (s) => s.activities);
  const fertig = alle.filter((a) => a.status === "fertig");
  if (!fertig.length) return <Leer>Noch nichts erledigt. Fertige Schritte sammeln sich hier.</Leer>;
  return (
    <section className="timeline" aria-labelledby="kz-erledigt-h">
      <h2 id="kz-erledigt-h" className="sr-only">
        Erledigte Schritte
      </h2>
      <ol>
        {fertig.map((a) => (
          <li key={a.id} data-status="fertig">
            <svg className="mark mark-done" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3 8.5l3.2 3.2L13 4.8" />
            </svg>
            <span className="tl-what">
              {LABEL[a.name] ?? a.name}
              {a.detail && <span className="tl-detail"> {a.detail}</span>}
            </span>
            <time className="num">
              {a.startedAt !== undefined && a.endedAt !== undefined ? sekunden(a.endedAt - a.startedAt) : ""}
            </time>
          </li>
        ))}
      </ol>
    </section>
  );
});

/** Merkt sich die Einträge, die in dieser Sitzung als "Gemerkt" erschienen sind (neueste zuerst, höchstens 8). */
function useGemerktSitzung(): Memo[] {
  const [liste, setListe] = useState<Memo[]>([]);
  useEffect(() => {
    const merke = () => {
      const aktuell = wamo.get().memos;
      setListe((alt) => {
        const neu = aktuell.filter((m) => !alt.some((a) => a.key === m.key));
        return neu.length ? [...neu, ...alt].slice(0, 8) : alt;
      });
    };
    merke();
    return wamo.subscribe(merke);
  }, []);
  return liste;
}

function Eintrag({ m }: { m: Memo }) {
  return (
    <li className="kz-eintrag" data-scope={m.bereich}>
      <span className="kz-eintrag-art">
        {m.bereich === "gemeinsam" ? "für alle" : "persönlich"}
        {m.status === "vorgeschlagen" && " · vorgeschlagen"}
      </span>
      <span>{m.text}</span>
    </li>
  );
}

/**
 * Linke Seite: Gedächtnis (was in dieser Sitzung gemerkt wurde), geteilte Arbeit (gemeinsame Einträge,
 * Canvas, Entwicklung) und Geräte-Status. Alles aus vorhandenen Daten; ohne Inhalt steht ein freundlicher Text.
 * Der Geräte-Block (Status, PC-Steuerung, Betriebsart) kommt von außen, weil er Schalter der Hauptansicht enthält.
 */
export const Hinweise = memo(function Hinweise({ geraete }: { geraete: ReactNode }) {
  const gemerkt = useGemerktSitzung();
  const persoenlich = gemerkt.filter((m) => m.bereich === "persoenlich");
  const gemeinsam = gemerkt.filter((m) => m.bereich === "gemeinsam");
  const canvas = useSelect(wamo, (s) => s.canvas.length);
  const entwicklung = useSelect(wamo, (s) => s.betrieb?.entwicklung ?? null);
  const rolle = useSelect(wamo, (s) => s.profil?.rolle ?? null);

  return (
    <>
      <section className="kz-karte" aria-labelledby="kz-h-gedaechtnis">
        <h2 id="kz-h-gedaechtnis" className="kz-karte-titel">
          Gedächtnis
        </h2>
        {persoenlich.length ? (
          <ul className="kz-eintraege">
            {persoenlich.map((m) => (
              <Eintrag key={m.key} m={m} />
            ))}
          </ul>
        ) : (
          <Leer>Noch nichts gemerkt. Sag „Merk dir …“, dann steht es hier.</Leer>
        )}
      </section>

      <section className="kz-karte" aria-labelledby="kz-h-geteilt">
        <h2 id="kz-h-geteilt" className="kz-karte-titel">
          Geteilte Arbeit
        </h2>
        {gemeinsam.length ? (
          <ul className="kz-eintraege">
            {gemeinsam.map((m) => (
              <Eintrag key={m.key} m={m} />
            ))}
          </ul>
        ) : (
          <Leer>Gemeinsame Einträge für das Team erscheinen hier, sobald es welche gibt.</Leer>
        )}
        <dl className="kz-daten">
          <div>
            <dt>Rolle</dt>
            <dd>{rolle === "admin" ? "Admin" : rolle === "mitglied" ? "Mitglied" : "unbekannt"}</dd>
          </div>
          <div>
            <dt>Canvas</dt>
            <dd className="num">{canvas ? `${canvas} ${canvas === 1 ? "Ergebnis" : "Ergebnisse"}` : "leer"}</dd>
          </div>
          <div>
            <dt>Entwicklung</dt>
            <dd>{entwicklung === "aus" ? "gesperrt" : entwicklung === "an" ? "freigegeben" : "unbekannt"}</dd>
          </div>
        </dl>
      </section>

      <section className="kz-karte" aria-labelledby="kz-h-geraete">
        <h2 id="kz-h-geraete" className="kz-karte-titel">
          Geräte
        </h2>
        {geraete}
      </section>
    </>
  );
});
