"use client";

import { memo, useRef, type KeyboardEvent } from "react";
import { useEffektiveDarstellung } from "./ansicht";
import { DARSTELLUNGEN, DARSTELLUNG_TEXT, darstellungSetzen, type Darstellung } from "./darstellung";

/**
 * Umschalter der Darstellung oben mittig: Normal, Fokus, Zentrale, Nacht als Segment-Pille (radiogroup).
 * Pfeiltasten wählen und aktivieren. Die Wahl bleibt je Gerät erhalten (darstellung.ts). Wartet eine
 * Freigabe, pausieren Fokus und Zentrale (ansicht.ts); der Umschalter sagt das in einer Zeile.
 */
export const DarstellungsWahl = memo(function DarstellungsWahl() {
  const { gewaehlt, pausiert } = useEffektiveDarstellung();
  const knoepfe = useRef<Partial<Record<Darstellung, HTMLButtonElement | null>>>({});

  const taste = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = DARSTELLUNGEN.indexOf(gewaehlt);
    const n = DARSTELLUNGEN.length;
    const ziel =
      e.key === "ArrowRight" || e.key === "ArrowDown"
        ? (i + 1) % n
        : e.key === "ArrowLeft" || e.key === "ArrowUp"
          ? (i - 1 + n) % n
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? n - 1
              : -1;
    if (ziel < 0) return;
    e.preventDefault();
    darstellungSetzen(DARSTELLUNGEN[ziel]);
    knoepfe.current[DARSTELLUNGEN[ziel]]?.focus();
  };

  return (
    <div className="kz-wahl-wrap">
      <div className="kz-wahl" role="radiogroup" aria-label="Darstellung" onKeyDown={taste}>
        {DARSTELLUNGEN.map((d) => (
          <button
            key={d}
            ref={(el) => {
              knoepfe.current[d] = el;
            }}
            type="button"
            role="radio"
            aria-checked={d === gewaehlt}
            tabIndex={d === gewaehlt ? 0 : -1}
            onClick={() => darstellungSetzen(d)}
          >
            {DARSTELLUNG_TEXT[d]}
          </button>
        ))}
      </div>
      {pausiert && (
        <p className="kz-wahl-hinweis" role="status">
          {DARSTELLUNG_TEXT[gewaehlt]} pausiert, solange etwas auf deine Entscheidung wartet.
        </p>
      )}
    </div>
  );
});
