"use client";

import { memo } from "react";
import { useEffektiveDarstellung } from "@/components/kommando/ansicht";
import { themaWaehlen } from "@/lib/thema";
import { useThema } from "@/lib/useThema";

/**
 * Umschalter Hell und Dunkel in der Kopfzeile. Ein Knopf mit aria-pressed (gedrückt heißt: Hellmodus an), der Name
 * bleibt "Hellmodus", das Symbol zeigt Sonne (hell) oder Mond (dunkel). Die Wahl gilt je Gerät (lib/thema.ts).
 * In der Darstellung Nacht bleibt die Oberfläche dunkel: der Knopf ist dann gesperrt und sagt warum.
 */
export const ThemaSchalter = memo(function ThemaSchalter() {
  const thema = useThema();
  const nacht = useEffektiveDarstellung().effektiv === "nacht";
  const hell = thema === "hell";
  return (
    <button
      type="button"
      className="topbar-btn thema-btn"
      aria-pressed={hell}
      aria-label="Hellmodus"
      title={nacht ? "In der Darstellung Nacht bleibt die Oberfläche dunkel" : hell ? "Hellmodus ausschalten" : "Hellmodus einschalten"}
      disabled={nacht}
      onClick={() => themaWaehlen(hell ? "dunkel" : "hell")}
    >
      {hell ? (
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="10" cy="10" r="3.4" />
          <path d="M10 2.2v2M10 15.8v2M2.2 10h2M15.8 10h2M4.5 4.5l1.4 1.4M14.1 14.1l1.4 1.4M4.5 15.5l1.4-1.4M14.1 5.9l1.4-1.4" />
        </svg>
      ) : (
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M16.2 12.4A6.8 6.8 0 0 1 7.6 3.8a6.8 6.8 0 1 0 8.6 8.6z" />
        </svg>
      )}
    </button>
  );
});
