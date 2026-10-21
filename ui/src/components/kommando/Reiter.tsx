"use client";

import { useRef, type KeyboardEvent } from "react";

export type ReiterDef<T extends string> = {
  id: T;
  label: string;
  /** rote Zahl hinter dem Namen (nur bei mehr als 0 sichtbar) */
  zahl?: number;
  /** Wort für die Zahl im Screenreader, z. B. "offen" */
  zahlWort?: string;
};

/**
 * Reiterleiste nach dem WAI-ARIA-Muster: ein Tabstopp (der aktive Reiter), Pfeil links und rechts,
 * Pos1 und Ende wechseln und aktivieren sofort. Die Tafeln (role="tabpanel") liegen im Rahmen.
 * `praefix` hält die Kennungen der zwei Leisten (Seitenleiste, Handy) getrennt.
 */
export function Reiterleiste<T extends string>({
  name,
  reiter,
  aktiv,
  onWahl,
  praefix,
  klasse,
}: {
  name: string;
  reiter: readonly ReiterDef<T>[];
  aktiv: T;
  onWahl: (id: T) => void;
  praefix: string;
  klasse: string;
}) {
  const knoepfe = useRef<Partial<Record<T, HTMLButtonElement | null>>>({});

  const taste = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = reiter.findIndex((r) => r.id === aktiv);
    const n = reiter.length;
    const ziel =
      e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
    if (ziel < 0) return;
    e.preventDefault();
    onWahl(reiter[ziel].id);
    knoepfe.current[reiter[ziel].id]?.focus();
  };

  return (
    <div role="tablist" aria-label={name} className={klasse} onKeyDown={taste}>
      {reiter.map((r) => (
        <button
          key={r.id}
          ref={(el) => {
            knoepfe.current[r.id] = el;
          }}
          type="button"
          role="tab"
          id={`${praefix}-${r.id}`}
          aria-selected={r.id === aktiv}
          aria-controls={`kz-p-${r.id}`}
          tabIndex={r.id === aktiv ? 0 : -1}
          className="kz-reiter"
          onClick={() => onWahl(r.id)}
        >
          {r.label}
          {r.zahl ? (
            <>
              <span className="kz-zahl num" aria-hidden="true">
                {r.zahl}
              </span>
              <span className="sr-only">
                , {r.zahl} {r.zahlWort ?? ""}
              </span>
            </>
          ) : null}
        </button>
      ))}
    </div>
  );
}
