"use client";

import { memo, useEffect, useLayoutEffect, useRef } from "react";
import { useSelect } from "@/lib/store";
import { abnahmeEintraege } from "@/lib/auftragAbnahme";
import { kartenAnsicht, repoAnzeige } from "@/lib/auftragStart";
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
 * Freigabe-Karte: immer ganz oben in der Präsenzspalte. Für Entwicklungsaufträge und für kritische PC-Aktionen
 * (job "pc": PowerShell, Senden in Mail und Messenger, lange Suchanfragen), dann mit Zielanwendung und Frist. Sicherheit: kein Tastenkürzel, Esc lehnt
 * nicht ab, keine Freigabe per Sprache. "Freigeben" und "Ablehnen" sind gleichgewichtig gestaltet,
 * damit nichts zum schnellen Bestätigen verleitet. Texte kommen bereinigt und gekürzt aus
 * lib/nachrichten.ts (keine Steuer- oder Bidi-Zeichen). Der Fokus springt auf die Überschrift (nicht
 * auf "Freigeben") und nur, wenn er gerade auf dem body steht. Angesagt wird einmal per role="alert".
 */
function ApprovalCard({ a, onDecide }: { a: Approval; onDecide: (a: Approval, ja: boolean) => void }) {
  const head = useRef<HTMLHeadingElement>(null);
  const karte = useRef<HTMLElement>(null);
  const id = `ap-${a.job}-${a.id}`.replace(/[^a-zA-Z0-9_-]/g, "_");

  useEffect(() => {
    if (document.activeElement === document.body) head.current?.focus();
  }, []);

  // Verschwindet die Karte ohne Klick (Frist abgelaufen), während der Fokus in ihr liegt: Fokus weitergeben
  useLayoutEffect(() => {
    const el = karte.current;
    return () => {
      if (el && el.contains(document.activeElement)) fokusNachEntscheidung = true;
    };
  }, []);

  return (
    <article className="approval" aria-labelledby={id} data-approval={id} ref={karte}>
      <p className="sr-only" role="alert">
        {a.job === "pc" ? "PC-Aktion freigeben" : "Freigabe nötig"}: {a.grund}
        {a.ziel && `, Ziel: ${a.ziel}`}
      </p>
      <p className="eyebrow">
        {a.job === "pc" ? "PC-Aktion freigeben" : "Freigabe nötig"}
        {a.projekt && <> · {a.projekt}</>}
      </p>
      <h3 id={id} ref={head} tabIndex={-1}>
        {a.grund}
      </h3>
      {a.von && <p className="approval-von">angefordert von {a.von}</p>}
      {a.ziel && (
        <p className="approval-ziel">
          <span className="approval-ziel-label">Ziel (Fenster und Programm):</span> {a.ziel}
        </p>
      )}
      <pre className="approval-detail" tabIndex={0} role="region" aria-label="Was genau ausgeführt wird">
        <code>{a.detail}</code>
      </pre>
      {a.anlass && <p className="approval-anlass">Anlass laut WAMO (nur Hinweis): {a.anlass}</p>}
      {a.ablauf !== undefined && (
        <p className="approval-frist">
          Gilt bis{" "}
          <time dateTime={new Date(a.ablauf).toISOString()}>
            {new Date(a.ablauf).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </time>{" "}
          Uhr, danach verfällt sie.
        </p>
      )}
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
  useEffect(fokusWeitergeben); // auch wenn die letzte Karte selbst verfiel (nach dem Aufräumen der Karte)

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
 * Auftragskarte für Entwicklungsaufträge (Servernachricht "auftrag"): Projekt, Auftragsart, der
 * vollständige, bereinigte Auftragstext und, falls vorhanden, die Abnahmekriterien (Befehle in Festbreite,
 * alles als Text, nie als Markup: wer startet, bestätigt auch diese Befehle). "Starten" und "Verwerfen" sind gleichgewichtig; die Karte
 * verschwindet bei "Verwerfen" sofort, bei "Starten" erst mit der Quittung des Kerns (lib/auftragStart.ts): bis dahin
 * wartet sie, bei einem Grund bleibt sie mit dem Grund stehen, nach 15 Sekunden ohne Antwort zeigt sie einen neutralen
 * Hinweis. Meldet der Kern betrieb.entwicklung "aus", ist Starten gesperrt.
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
  const karte = useRef<HTMLElement>(null);
  const id = `auftrag-${a.job}`.replace(/[^a-zA-Z0-9_-]/g, "_");
  const abnahme = abnahmeEintraege(a.abnahme);
  const ansicht = kartenAnsicht(a, gesperrt);
  const repo = repoAnzeige(a);
  const phase = a.stand?.phase;

  useEffect(() => {
    if (document.activeElement === document.body) head.current?.focus();
  }, []);

  // Nach "Starten" ist die Schaltfläche gesperrt, bis die Quittung kommt: den Fokus auf die Überschrift der Karte legen
  useEffect(() => {
    if (phase === "wartet") head.current?.focus();
  }, [phase]);

  // Verschwindet die Karte nach der Quittung (ok), während der Fokus in ihr liegt: Fokus weitergeben
  useLayoutEffect(() => {
    const el = karte.current;
    return () => {
      if (el && el.contains(document.activeElement)) fokusNachEntscheidung = true;
    };
  }, []);

  return (
    <article className="approval auftrag" aria-labelledby={id} ref={karte}>
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
        {repo && (
          <div>
            <dt>Repo</dt>
            <dd>{repo}</dd>
          </div>
        )}
      </dl>
      <div className="auftrag-text" tabIndex={0} role="region" aria-label="Auftragstext">
        {a.auftrag}
      </div>
      {abnahme.length > 0 && (
        <section className="auftrag-abnahme" aria-labelledby={`${id}-abnahme`}>
          <h4 id={`${id}-abnahme`}>Abnahme</h4>
          <p className="auftrag-abnahme-hinweis">
            WAMO prüft nach jeder Runde.
            {abnahme.some((k) => k.art === "befehl") && " Mit Starten bestätigst du auch die Befehle."}
          </p>
          <ul>
            {abnahme.map((k, i) => (
              <li key={i} className={k.art === "befehl" ? "abnahme-befehl" : undefined}>
                {k.art === "befehl" ? (
                  <>
                    <span className="abnahme-art">Befehl</span> <code>{k.text}</code>
                  </>
                ) : (
                  k.text
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {gesperrt && <p className="notice">Entwicklungsaufträge sind gesperrt.</p>}
      {ansicht.grund && (
        <p className="notice" role="alert">
          Nicht gestartet: {ansicht.grund}
        </p>
      )}
      {ansicht.hinweis && (
        <p className="notice" role="status">
          {ansicht.hinweis}
        </p>
      )}
      <div className="approval-actions">
        <button type="button" className="btn-decide" disabled={ansicht.startGesperrt} onClick={() => onDecide(a, true)}>
          {ansicht.startText}
        </button>
        <button type="button" className="btn-decide" disabled={ansicht.verwerfenGesperrt} onClick={() => onDecide(a, false)}>
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
    // Starten lässt die Karte stehen, bis die Quittung des Kerns kommt: nur Verwerfen entfernt sie sofort und gibt den Fokus weiter
    fokusNachEntscheidung = !ja;
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
