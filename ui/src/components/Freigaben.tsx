"use client";

import { memo, useEffect, useRef } from "react";
import { useSelect } from "@/lib/store";
import type { Auftrag } from "@/lib/nachrichten";
import { wamo, type Approval } from "@/lib/wamo";

type Entscheide = (a: Approval, ja: boolean) => boolean;

// Nach einer Entscheidung den Fokus nicht verlieren: nach dem Entfernen der Karte (also nach dem
// Commit) zur nächsten Karte oder zur Hauptschaltfläche. Gilt für Freigaben und Aufträge gemeinsam.
let fokusNachEntscheidung = false;

function fokusWeitergeben() {
  if (!fokusNachEntscheidung) return;
  fokusNachEntscheidung = false;
  const naechste = document.querySelector<HTMLElement>(".approval h3");
  (naechste ?? document.querySelector<HTMLElement>(".actions .wake"))?.focus();
}

/**
 * Freigabe-Karte: immer ganz oben in der Präsenzspalte. Sicherheit: kein Tastenkürzel, Esc lehnt
 * nicht ab, keine Freigabe per Sprache. "Freigeben" und "Ablehnen" sind gleichgewichtig gestaltet,
 * damit nichts zum schnellen Bestätigen verleitet. Texte kommen bereinigt und gekürzt aus
 * lib/nachrichten.ts (keine Steuer- oder Bidi-Zeichen). Der Fokus springt auf die Überschrift (nicht
 * auf "Freigeben") und nur, wenn er gerade auf dem body steht. Angesagt wird einmal per role="alert".
 */
function ApprovalCard({ a, onDecide }: { a: Approval; onDecide: (a: Approval, ja: boolean) => void }) {
  const head = useRef<HTMLHeadingElement>(null);
  const id = `ap-${a.job}-${a.id}`.replace(/[^a-zA-Z0-9_-]/g, "_");

  useEffect(() => {
    if (document.activeElement === document.body) head.current?.focus();
  }, []);

  return (
    <article className="approval" aria-labelledby={id} data-approval={id}>
      <p className="sr-only" role="alert">
        Freigabe nötig: {a.grund}
      </p>
      <p className="eyebrow">
        Freigabe nötig{a.projekt && <> · {a.projekt}</>}
      </p>
      <h3 id={id} ref={head} tabIndex={-1}>
        {a.grund}
      </h3>
      {a.von && <p className="approval-von">angefordert von {a.von}</p>}
      <pre className="approval-detail" tabIndex={0} role="region" aria-label="Was genau ausgeführt wird">
        <code>{a.detail}</code>
      </pre>
      <div className="approval-actions">
        <button type="button" className="btn-decide" onClick={() => onDecide(a, true)}>
          Freigeben
        </button>
        <button type="button" className="btn-decide" onClick={() => onDecide(a, false)}>
          Ablehnen
        </button>
      </div>
    </article>
  );
}

export const Freigaben = memo(function Freigaben({ onDecide }: { onDecide: Entscheide }) {
  const approvals = useSelect(wamo, (s) => s.approvals);
  useEffect(fokusWeitergeben, [approvals]);

  const entscheide = (a: Approval, ja: boolean) => {
    fokusNachEntscheidung = true;
    if (!onDecide(a, ja)) fokusNachEntscheidung = false;
  };

  if (!approvals.length) return null;
  return (
    <section className="approvals" aria-label="Freigaben">
      {approvals.map((a) => (
        <ApprovalCard key={`${a.job}-${a.id}`} a={a} onDecide={entscheide} />
      ))}
    </section>
  );
});

/**
 * Auftragskarte für Entwicklungsaufträge (Servernachricht "auftrag"): Projekt, Auftragsart und der
 * vollständige, bereinigte Auftragstext. "Starten" und "Verwerfen" sind gleichgewichtig; die Karte
 * verschwindet erst, wenn die Entscheidung gesendet wurde. Meldet der Kern betrieb.entwicklung "aus",
 * ist Starten gesperrt.
 */
function AuftragCard({
  a,
  gesperrt,
  onDecide,
}: {
  a: Auftrag;
  gesperrt: boolean;
  onDecide: (a: Auftrag, ja: boolean) => void;
}) {
  const head = useRef<HTMLHeadingElement>(null);
  const id = `auftrag-${a.job}`.replace(/[^a-zA-Z0-9_-]/g, "_");

  useEffect(() => {
    if (document.activeElement === document.body) head.current?.focus();
  }, []);

  return (
    <article className="approval auftrag" aria-labelledby={id}>
      <p className="sr-only" role="alert">
        Entwicklungsauftrag wartet: {a.art || "Auftrag"}
      </p>
      <p className="eyebrow">Entwicklungsauftrag</p>
      <h3 id={id} ref={head} tabIndex={-1}>
        {a.art || "Auftrag"}
      </h3>
      <dl className="auftrag-meta">
        <div>
          <dt>Projekt</dt>
          <dd>{a.projekt || "unbekannt"}</dd>
        </div>
        <div>
          <dt>Auftragsart</dt>
          <dd>{a.art || "unbekannt"}</dd>
        </div>
      </dl>
      <div className="auftrag-text" tabIndex={0} role="region" aria-label="Auftragstext">
        {a.auftrag}
      </div>
      {gesperrt && <p className="notice">Entwicklungsaufträge sind gesperrt.</p>}
      <div className="approval-actions">
        <button type="button" className="btn-decide" disabled={gesperrt} onClick={() => onDecide(a, true)}>
          Starten
        </button>
        <button type="button" className="btn-decide" onClick={() => onDecide(a, false)}>
          Verwerfen
        </button>
      </div>
    </article>
  );
}

export const Auftraege = memo(function Auftraege({
  onDecide,
}: {
  onDecide: (a: Auftrag, ja: boolean) => boolean;
}) {
  const auftraege = useSelect(wamo, (s) => s.auftraege);
  const gesperrt = useSelect(wamo, (s) => s.betrieb?.entwicklung === "aus");
  useEffect(fokusWeitergeben, [auftraege]);

  const entscheide = (a: Auftrag, ja: boolean) => {
    fokusNachEntscheidung = true;
    if (!onDecide(a, ja)) fokusNachEntscheidung = false;
  };

  if (!auftraege.length) return null;
  return (
    <section className="approvals" aria-label="Entwicklungsaufträge">
      {auftraege.map((a) => (
        <AuftragCard key={a.job} a={a} gesperrt={gesperrt} onDecide={entscheide} />
      ))}
    </section>
  );
});
