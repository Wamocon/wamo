"use client";

import { memo, useId, useMemo, useState } from "react";
import { canvasIndex, canvasWaehlen } from "@/lib/canvasWahl";
import type { Vorgang, VorgangSchritt } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import { useGedrosselt } from "@/lib/useGedrosselt";
import {
  SCHRITT_DROSSEL_MS,
  artText,
  balkenTon,
  endeText,
  freigabeKartenSelektor,
  fortschrittText,
  kriteriumMarke,
  kriteriumText,
  markeArt,
  prozent,
  rundenText,
  schrittAenderung,
  schrittFortschrittText,
  schrittGleich,
  schrittStatusText,
  statusText,
  tafelListen,
  zeitZeile,
  type MarkenArt,
} from "@/lib/vorgaenge";
import { vorgangVerlauf } from "@/lib/vorgaengeApi";
import { wamo, zeigeToast } from "@/lib/wamo";
import { useRahmen } from "./rahmenKontext";

/**
 * Tafel "Vorgänge" (rechte Seite, Reiter "Vorgänge"): was WAMO gerade abarbeitet. Je Vorgang eine Karte mit Titel, Status als Wort,
 * Gesamtbalken (Prozent, Restzeit oder "Fertig in mm:ss", Runde), nummerierten Schritten mit eigenem Balken, Prüfkriterien und, wo
 * es passt, den Knöpfen "Zur Freigabe" und "Ergebnis ansehen". Beendete Vorgänge stehen eingeklappt unter "Zuletzt beendet", dazu
 * der Verlauf aus der Datenbank (lib/vorgaengeApi.ts, geholt von WamoAnsicht). Der Rahmen hängt die Tafel nur ein, solange ihr Reiter
 * zu sehen ist (kommando/tafeln.ts), im Verborgenen rendert sie also nie. Daten kommen als Servernachricht "vorgang" in den Store (lib/wamo.ts); die
 * Bildschirmleser-Ansage kommt von dort (ansage), hier steht keine zweite.
 *
 * Barrierefreiheit: Jeder Balken ist ein role="progressbar" mit aria-valuenow und aria-valuetext. Der Zustand steht immer als Wort
 * da, Form und Farbe sind nur Beigabe (Ring mit Bogen, Haken, Pause, "!", durchgestrichen). Bewegung (der drehende Bogen und das
 * Wachsen der Balken) schaltet prefers-reduced-motion ab (vorgaenge.css).
 */

function Marke({ art }: { art: MarkenArt }) {
  return (
    <svg className={`vg-marke vg-marke-${art}`} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      {art === "laeuft" && (
        <>
          <circle cx="8" cy="8" r="6" />
          <path className="vg-bogen" d="M8 2a6 6 0 0 1 6 6" />
        </>
      )}
      {art === "fertig" && (
        <>
          <circle cx="8" cy="8" r="6.6" />
          <path d="M4.8 8.4l2.3 2.3 4.2-4.7" />
        </>
      )}
      {art === "wartet" && (
        <>
          <circle cx="8" cy="8" r="6" />
          <path d="M6.2 5.5v5M9.8 5.5v5" />
        </>
      )}
      {art === "fehler" && (
        <>
          <rect x="1.5" y="1.5" width="13" height="13" rx="2" />
          <path d="M8 4.5v4.6M8 11.2v.4" />
        </>
      )}
      {art === "offen" && <circle cx="8" cy="8" r="6" />}
      {art === "aus" && (
        <>
          <circle cx="8" cy="8" r="6" />
          <path d="M4 12L12 4" />
        </>
      )}
    </svg>
  );
}

type BalkenProps = {
  anteil: number;
  ton: "aktiv" | "still";
  /** aria-valuetext: was ein Screenreader statt der bloßen Zahl vorliest */
  text: string;
  /** Kennung des Elements, das den Balken benennt (aria-labelledby): bei der Karte ihr Titel, beim Schritt sein Titel */
  benanntNach: string;
  klein?: boolean;
};

function Balken({ anteil, ton, text, benanntNach, klein }: BalkenProps) {
  const p = prozent(anteil);
  return (
    <div
      className="vg-balken"
      data-ton={ton}
      data-klein={klein ? "" : undefined}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={p}
      aria-valuetext={text}
      aria-labelledby={benanntNach}
    >
      <span className="vg-fuell" style={{ transform: `translateX(${p - 100}%)` }} />
    </div>
  );
}

/**
 * Eine Zeile der Schrittliste. Bis zu 60 je Karte: Sie rendert nur neu, wenn sich etwas ändert, das man sieht (schrittGleich),
 * denn jede Nachricht des Kerns bringt neue Schrittobjekte mit gleichem Inhalt.
 */
const SchrittZeile = memo(
  function SchrittZeile({ s }: { s: VorgangSchritt }) {
    const titelId = `${useId()}-titel`; // der Balken heißt wie sein Schritt, über Karten hinweg eindeutig
    return (
      <li data-status={s.status} aria-current={s.status === "laeuft" ? "step" : undefined}>
        <span className="vg-nr num">{s.nr}</span>
        <Marke art={markeArt(s.status)} />
        <span id={titelId} className="vg-titel">
          {s.titel}
        </span>
        <span className="vg-zustand" aria-hidden="true">
          {schrittStatusText[s.status]}
        </span>
        <Balken anteil={s.fortschritt} ton={s.status === "laeuft" || s.status === "fertig" ? "aktiv" : "still"} klein text={schrittFortschrittText(s)} benanntNach={titelId} />
        {s.detail && <span className="vg-detail">{s.detail}</span>}
      </li>
    );
  },
  (alt, neu) => schrittGleich(alt.s, neu.s),
);

/**
 * Schrittliste. Ein reiner Zwischenstand der Prozent erscheint höchstens alle SCHRITT_DROSSEL_MS (der neueste, am Ende geht keiner
 * verloren); der Endwert 100 Prozent, ein anderer Status und jeder neue Schritt erscheinen sofort (lib/vorgaenge.ts, schrittAenderung).
 */
function Schritte({ schritte }: { schritte: VorgangSchritt[] }) {
  const gezeigt = useGedrosselt(schritte, schrittAenderung, SCHRITT_DROSSEL_MS);
  if (!gezeigt.length) return null;
  return (
    <ol className="vg-schritte" aria-label="Schritte">
      {gezeigt.map((s) => (
        <SchrittZeile key={s.nr} s={s} />
      ))}
    </ol>
  );
}

function Kriterien({ v }: { v: Vorgang }) {
  if (!v.kriterien.length) return null;
  return (
    <ul className="vg-kriterien" aria-label="Prüfkriterien">
      {v.kriterien.map((k, i) => (
        <li key={i} data-erfuellt={k.erfuellt === null ? "offen" : String(k.erfuellt)}>
          <Marke art={kriteriumMarke(k)} />
          <span className="vg-titel">{k.text}</span>
          <span className="vg-zustand">{kriteriumText(k)}</span>
          {k.hinweis && <span className="vg-detail">{k.hinweis}</span>}
        </li>
      ))}
    </ul>
  );
}

/** Scrollt und fokussiert; bei reduzierter Bewegung ohne weiches Scrollen. */
function inDenBlick(ziel: HTMLElement | null, fokus: HTMLElement | null) {
  const kurz = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  fokus?.focus({ preventScroll: true });
  ziel?.scrollIntoView({ behavior: kurz ? "auto" : "smooth", block: "center" });
}

/**
 * Inhalt eines Vorgangs ohne Kopf: Gesamtbalken, Schritte, Kriterien und Knöpfe. Steht in der aktiven Karte und aufgeklappt unter
 * "Zuletzt beendet".
 */
const VorgangInhalt = memo(function VorgangInhalt({ v, titelId }: { v: Vorgang; titelId: string }) {
  const { zeigeCanvas, zeigeAuftraege } = useRahmen();
  // Das Ergebnis lässt sich nur ansehen, solange das Element im Canvas liegt (ein Vorgang aus dem Verlauf hat meist keins mehr)
  const canvasId = v.canvasId;
  const ergebnisDa = useSelect(wamo, (s) => canvasId !== null && canvasIndex(s.canvas, canvasId) >= 0);
  const wartet = v.status === "wartet_freigabe";
  const runde = rundenText(v);
  const zeit = zeitZeile(v);

  // Die Karte steht in der Tafel "Aufträge" (Reiter, am Handy eigener Reiter): erst dorthin wechseln, dann nach dem Umschalten
  // zur Karte scrollen und die Überschrift fokussieren. Ohne eigene Karte (Frist abgelaufen) springt der Fokus nie auf die Karte
  // eines anderen Vorgangs, sondern auf die Überschrift der Tafel (Rahmen.tsx), dazu die Ansage "Freigabekarte nicht gefunden".
  const zurFreigabe = () => {
    zeigeAuftraege();
    requestAnimationFrame(() => {
      const selektor = freigabeKartenSelektor(v.freigabeId);
      const karte = selektor ? document.querySelector<HTMLElement>(selektor) : null;
      if (karte) {
        inDenBlick(karte, karte.querySelector<HTMLElement>("h3"));
        return;
      }
      inDenBlick(document.getElementById("kz-p-auftraege"), document.getElementById("kz-h-auftraege"));
      zeigeToast("Freigabekarte nicht gefunden");
    });
  };
  const ergebnisAnsehen = () => {
    if (canvasId === null) return;
    canvasWaehlen(canvasId);
    zeigeCanvas();
  };

  return (
    <>
      <div className="vg-gesamt">
        <Balken anteil={v.gesamt} ton={balkenTon(v.status)} text={fortschrittText(v)} benanntNach={titelId} />
        <p className="vg-gesamt-zeile" aria-hidden="true">
          <span className="vg-prozent num">
            {prozent(v.gesamt)}
            <span className="vg-pz">&nbsp;%</span>
          </span>
          {runde && <span className="vg-runde">{runde}</span>}
          {zeit && <span className="vg-zeit">{zeit}</span>}
        </p>
      </div>
      <Schritte schritte={v.schritte} />
      <Kriterien v={v} />
      {(wartet || ergebnisDa) && (
        <div className="vg-aktionen">
          {wartet && (
            <button type="button" className="btn-primary btn-small" onClick={zurFreigabe} aria-label={`Zur Freigabe: ${v.titel}`}>
              Zur Freigabe
            </button>
          )}
          {ergebnisDa && (
            <button type="button" className="btn-secondary btn-small" onClick={ergebnisAnsehen} aria-label={`Ergebnis ansehen: ${v.titel}`}>
              Ergebnis ansehen
            </button>
          )}
        </div>
      )}
    </>
  );
});

/** Aktiver Vorgang: volle Karte. Die Karte rendert nur neu, wenn sich ihr Vorgang ändert (die übrigen behalten ihre Referenz). */
const VorgangKarte = memo(function VorgangKarte({ v }: { v: Vorgang }) {
  const id = useId();
  const titelId = `${id}-titel`;
  return (
    <article className="vg-karte" data-status={v.status} aria-labelledby={titelId}>
      <header className="vg-kopf">
        <span className="vg-art">{artText[v.art]}</span>
        <span className="vg-status" data-status={v.status}>
          <Marke art={markeArt(v.status)} />
          {statusText[v.status]}
        </span>
      </header>
      <h3 id={titelId} className="vg-name">
        {v.titel}
      </h3>
      <VorgangInhalt v={v} titelId={titelId} />
    </article>
  );
});

/**
 * Beendeter Vorgang: eine Zeile mit Titel und "Fertig in mm:ss" (oder Status), aufklappbar bis zu Schritten und Kriterien. Der
 * Inhalt (Balken, bis zu 60 Schritte, Kriterien) entsteht erst nach dem Aufklappen: bis zu 25 geschlossene Zeilen kosten so nur
 * ihre Kopfzeile. Der Abschnitt "Zuletzt beendet" selbst bleibt wie bisher aufgeklappt, seine Zeilen zu.
 */
const BeendeterVorgang = memo(function BeendeterVorgang({ v }: { v: Vorgang }) {
  const id = useId();
  const titelId = `${id}-titel`;
  const [offen, setOffen] = useState(false);
  return (
    <details className="vg-ende" data-status={v.status} onToggle={(e) => setOffen(e.currentTarget.open)}>
      <summary>
        <Marke art={markeArt(v.status)} />
        <span className="vg-ende-wer">
          <span id={titelId} className="vg-ende-titel">
            {v.titel}
          </span>
          <span className="vg-ende-text">
            {artText[v.art]}, {endeText(v)}
          </span>
        </span>
      </summary>
      {offen && (
        <div className="vg-ende-inhalt">
          <VorgangInhalt v={v} titelId={titelId} />
        </div>
      )}
    </details>
  );
});

/**
 * Tafel mit aktiven Vorgängen und dem einklappbaren Abschnitt "Zuletzt beendet". Ohne Vorgang zeigt sie nichts (den Leerzustand
 * zeigt VorgaengeLeer in Seitenleisten.tsx).
 */
export const VorgaengeTafel = memo(function VorgaengeTafel() {
  const sitzung = useSelect(wamo, (s) => s.vorgaenge);
  const verlauf = useSelect(vorgangVerlauf, (v) => v);
  const { aktiv, beendet } = useMemo(() => tafelListen(sitzung, verlauf), [sitzung, verlauf]);
  if (!aktiv.length && !beendet.length) return null;
  return (
    <section className="vg-tafel" aria-labelledby="vg-h">
      <h2 id="vg-h" className="sr-only">
        Vorgänge
      </h2>
      {aktiv.length > 0 ? (
        <ul className="vg-liste">
          {aktiv.map((v) => (
            <li key={v.id}>
              <VorgangKarte v={v} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="kz-leer">Gerade läuft kein Vorgang.</p>
      )}
      {beendet.length > 0 && (
        <details className="vg-alt" open>
          <summary>
            Zuletzt beendet <span className="num">({beendet.length})</span>
          </summary>
          <ul className="vg-liste">
            {beendet.map((v) => (
              <li key={v.id}>
                <BeendeterVorgang v={v} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
});
