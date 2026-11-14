"use client";

import { memo, useEffect, useId, useRef, useState } from "react";
import { useEinstellungenZiel } from "@/lib/einstellungenHash";
import { useSelect } from "@/lib/store";
import {
  abdruckBadge,
  abdruckProzent,
  abdruckStatusText,
  VORLESETEXT,
  type Abdruck,
} from "@/lib/stimme";
import type { StimmabdruckAktionen } from "@/lib/useWamo";
import { abdruckAnsichtOeffnen, wamo } from "@/lib/wamo";

/**
 * Stimme einlernen: steht in der Mitte statt des Kerns, solange die Ansicht offen ist (kommando/KernOderEinlernen.tsx).
 * Zuerst die Einwilligung: der Text kommt vom Kern, ohne ihn gibt es weder Haken noch Start, und der Start geht mit der Version dieses Textes
 * hinaus (lib/useWamo.ts prüft sie noch einmal). Der Haken gilt nur für diese Version; einen ungültigen Text meldet die Ansicht, statt
 * weiter auf ihn zu warten. Danach führt der Kern: Vorlesetext, Balken mit den aufgenommenen Sekunden reiner Sprache
 * (Pausen füllen ihn nicht), Statuszeile und bis zum Ergebnis Abbrechen, danach Schließen. Gespeichert wird nur der Abdruck, keine Aufnahme.
 * Der Fokus geht auf die Überschrift, sobald die Ansicht zu sehen ist (nach dem Schließen der Einstellungen) und nach dem Start (die Knöpfe der
 * Einwilligung verschwinden); die Statuszeile ist eine höfliche Live-Region (nur Phasenwechsel, nicht jede Sekunde).
 */
export const StimmeEinlernen = memo(function StimmeEinlernen({ aktionen }: { aktionen: StimmabdruckAktionen }) {
  const abdruck = useSelect(wamo, (s) => s.abdruck);
  const oeffnen = abdruck !== null;
  const inEinwilligung = abdruck?.phase === "einwilligung";
  const einstellungenOffen = useEinstellungenZiel().offen;
  const titel = useRef<HTMLHeadingElement>(null);
  const titelId = useId();
  useEffect(() => {
    if (oeffnen && !einstellungenOffen) titel.current?.focus();
  }, [oeffnen, einstellungenOffen, inEinwilligung]);
  if (!abdruck) return null;
  const badge = abdruckBadge(abdruck);

  return (
    <section className="kz-kernbereich kz-einlernen" data-p="kern" aria-labelledby={titelId}>
      <div className="kz-einlernen-kopf">
        <span className="hud-label">Stimmabdruck</span>
        <span className="hud-badge" data-state={badge.an === "ja" ? "laeuft" : badge.an}>
          {badge.text}
        </span>
      </div>
      <h2 id={titelId} ref={titel} tabIndex={-1}>
        Stimme einlernen
      </h2>
      {abdruck.phase === "einwilligung" ? (
        <Einwilligung aktionen={aktionen} />
      ) : (
        <Aufnahme abdruck={abdruck} aktionen={aktionen} />
      )}
    </section>
  );
});

function Einwilligung({ aktionen }: { aktionen: StimmabdruckAktionen }) {
  const einwilligung = useSelect(wamo, (s) => s.einwilligung);
  const abgelehnt = useSelect(wamo, (s) => s.einwilligungAbgelehnt);
  // Der Haken gilt nur für die Version, der er gegeben wurde: schickt der Kern einen neuen Text, ist er wieder leer.
  // (Gebunden über den Wert statt über key, damit der Fokus beim Wechsel nicht verloren geht.)
  const [okFuer, setOkFuer] = useState<string | null>(null);
  const [gescheitert, setGescheitert] = useState(false);
  const textId = useId();
  const ok = einwilligung !== null && okFuer === einwilligung.version;
  const bereit = ok && einwilligung !== null;

  const starten = () => {
    if (!bereit) return;
    setGescheitert(!aktionen.start(einwilligung.version));
  };

  return (
    <>
      <p className="kz-einlernen-hinweis">
        Bevor WAMO deine Stimme einlernt, braucht es dein Einverständnis. Gespeichert wird nur der Abdruck, keine Aufnahme.
      </p>
      {einwilligung ? (
        <div className="kz-einlernen-text kz-einlernen-einwilligung" id={textId}>
          {einwilligung.text}
        </div>
      ) : abgelehnt ? (
        <p className="kz-einlernen-fehler" role="alert">
          Der Einwilligungstext vom Kern ist ungültig und wird nicht angezeigt. Ohne ihn lässt sich das Einlernen nicht starten.
          Versuche es in einem neuen Gespräch noch einmal.
        </p>
      ) : (
        <p className="kz-einlernen-hinweis" role="status">
          Der Einwilligungstext wird vom Kern geladen. Ohne ihn lässt sich das Einlernen nicht starten.
        </p>
      )}
      <label className="kz-einlernen-haken">
        <input
          type="checkbox"
          checked={ok}
          disabled={!einwilligung}
          aria-describedby={einwilligung ? textId : undefined}
          onChange={(e) => setOkFuer(e.target.checked && einwilligung ? einwilligung.version : null)}
        />
        <span>Ich habe den Text gelesen und willige ein.</span>
      </label>
      {gescheitert && (
        <p className="kz-einlernen-fehler" role="alert">
          Das Einlernen wurde nicht gestartet. Prüfe die Verbindung und versuche es noch einmal.
        </p>
      )}
      <div className="kz-einlernen-tasten">
        <button type="button" className="btn-primary" disabled={!bereit} onClick={starten}>
          Einlernen starten
        </button>
        <button type="button" className="btn-secondary" onClick={() => aktionen.abbrechen()}>
          Abbrechen
        </button>
      </div>
    </>
  );
}

function Aufnahme({ abdruck, aktionen }: { abdruck: Abdruck; aktionen: StimmabdruckAktionen }) {
  const fertig = abdruck.phase === "fertig" || abdruck.phase === "fehler";
  const sekunden = Math.floor(abdruck.sekunden);
  return (
    <>
      <p className="kz-einlernen-hinweis">
        Lies den Text in normaler Lautstärke vor oder erzähl einfach frei, bis der Balken voll ist. Am besten ohne andere Stimmen im
        Raum. Gespeichert wird nur der Abdruck, keine Aufnahme.
      </p>
      <blockquote className="kz-einlernen-text" lang="de">
        {VORLESETEXT}
      </blockquote>
      <div
        className="kz-einlernen-balken"
        role="progressbar"
        aria-label="Aufgenommene Sprache in Sekunden"
        aria-valuemin={0}
        aria-valuemax={abdruck.ziel}
        aria-valuenow={sekunden}
        aria-valuetext={`${sekunden} von ${Math.round(abdruck.ziel)} Sekunden`}
      >
        <span style={{ width: `${abdruckProzent(abdruck)}%` }} />
      </div>
      <p className="kz-einlernen-status" aria-live="polite" data-phase={abdruck.phase}>
        {abdruckStatusText(abdruck)}
      </p>
      <div className="kz-einlernen-tasten">
        {abdruck.phase === "fehler" && (
          <button type="button" className="btn-primary" onClick={() => abdruckAnsichtOeffnen()}>
            Noch einmal
          </button>
        )}
        <button type="button" className={abdruck.phase === "fertig" ? "btn-primary" : "btn-secondary"} onClick={() => aktionen.abbrechen()}>
          {fertig ? "Schließen" : "Abbrechen"}
        </button>
      </div>
    </>
  );
}
