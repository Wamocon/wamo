"use client";

import type { MouseEvent, ReactNode } from "react";
import { hashFuer, type AbschnittId } from "@/lib/einstellungenAbschnitte";
import { einstellungenOeffnen, useEinstellungenZiel } from "@/lib/einstellungenHash";

/** Kennung des Zahnrads in der Kopfzeile: beim Schließen der Einstellungen kehrt der Fokus dorthin zurück. */
export const EINSTELLUNGEN_KNOPF_ID = "ez-knopf";

/** Normaler Klick öffnet die Seite ohne Neuladen; Strg, Umschalt, Mittelklick und "Link kopieren" nutzen die Adresse. */
function beiKlick(e: MouseEvent<HTMLAnchorElement>, abschnitt: AbschnittId | null) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  einstellungenOeffnen(abschnitt);
}

/** Link zur Einstellungsseite (optional zu einem Abschnitt), zum Beispiel "Einstellungen öffnen" in der Seitenleiste. */
export function EinstellungenLink({
  abschnitt = null,
  className,
  children,
}: {
  abschnitt?: AbschnittId | null;
  className?: string;
  children: ReactNode;
}) {
  return (
    <a href={hashFuer(abschnitt)} className={className} onClick={(e) => beiKlick(e, abschnitt)}>
      {children}
    </a>
  );
}

/** Zahnrad in der Kopfzeile. Markiert die Seite mit aria-current, solange die Einstellungen offen sind. */
export function EinstellungenKnopf() {
  const offen = useEinstellungenZiel().offen;
  return (
    <a
      id={EINSTELLUNGEN_KNOPF_ID}
      href={hashFuer(null)}
      className="topbar-btn einstellungen-btn"
      aria-label="Einstellungen"
      title="Einstellungen"
      aria-current={offen ? "page" : undefined}
      onClick={(e) => beiKlick(e, null)}
    >
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="10" cy="10" r="2.6" />
        <path d="M10 2.2l1.2 2 2.3-.5.9 2.2 2.2.9-.5 2.3 2 1.2-2 1.2.5 2.3-2.2.9-.9 2.2-2.3-.5-1.2 2-1.2-2-2.3.5-.9-2.2-2.2-.9.5-2.3-2-1.2 2-1.2-.5-2.3 2.2-.9.9-2.2 2.3.5z" />
      </svg>
    </a>
  );
}
