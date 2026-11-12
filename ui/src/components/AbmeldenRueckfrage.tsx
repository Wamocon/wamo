"use client";

import { useEffect, useId, useRef, type KeyboardEvent, type SyntheticEvent } from "react";

/**
 * Rückfrage vor dem Abmelden (Laborkonten, lib/laborPlatz.ts): Ein Besucher am Laborplatz soll nicht versehentlich aus dem Platz
 * herausfallen, denn ohne die Zugangsdaten des Platzes kommt er nicht wieder hinein.
 *
 * Ein modales <dialog> (showModal): Der Rest der Seite ist solange inaktiv, der Fokus bleibt im Fenster. Er beginnt bei "Angemeldet
 * bleiben", der harmlosen Antwort. Escape und "Angemeldet bleiben" brechen ab; "Ja, abmelden" ist ein eigener, deutlicher Schritt.
 * Der Aufrufer (Kopfzeile) bringt den Fokus nach dem Abbruch zum Profil-Chip zurück, sobald das Fenster aus dem Dokument ist, denn
 * solange es offen ist, lässt der Browser auf der übrigen Seite keinen Fokus zu. Escape gilt nur für den Dialog, nicht zugleich für
 * die Einstellungsseite oder den Verlauf dahinter.
 */
export function AbmeldenRueckfrage({ onBestaetigt, onAbbruch }: { onBestaetigt: () => void; onAbbruch: () => void }) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const bleiben = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const d = dialog.current;
    if (d && !d.open) {
      // Ohne showModal (sehr alte Browser, Testumgebungen) öffnet das Attribut das Fenster, nur eben nicht modal
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    }
    bleiben.current?.focus();
  }, []);

  const abbrechen = (e: SyntheticEvent) => {
    e.preventDefault(); // der Dialog verschwindet mit dem Element, nicht durch den Browser
    onAbbruch();
  };
  const beiTaste = (e: KeyboardEvent<HTMLDialogElement>) => {
    if (e.key !== "Escape") return;
    e.stopPropagation();
    abbrechen(e);
  };

  return (
    <dialog
      ref={dialog}
      className="ab-dialog"
      aria-labelledby={`${id}-titel`}
      aria-describedby={`${id}-text`}
      onKeyDown={beiTaste}
      onCancel={abbrechen}
    >
      <div className="ab-form">
        <h2 id={`${id}-titel`} className="ab-titel">
          Wirklich abmelden?
        </h2>
        <p id={`${id}-text`} className="ab-text">
          Ein laufendes Gespräch endet. Danach musst du dich an diesem Platz neu anmelden.
        </p>
        <div className="ab-knoepfe">
          <button ref={bleiben} type="button" className="btn-primary" onClick={onAbbruch}>
            Angemeldet bleiben
          </button>
          <button type="button" className="btn-secondary" onClick={onBestaetigt}>
            Ja, abmelden
          </button>
        </div>
      </div>
    </dialog>
  );
}
