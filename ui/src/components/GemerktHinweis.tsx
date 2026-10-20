"use client";

import { memo } from "react";
import { useSelect } from "@/lib/store";
import { halteMemo, wamo, weiterMemo, type Memo } from "@/lib/wamo";

function titel(m: Memo) {
  if (m.bereich === "gemeinsam") {
    return m.status === "aktiv" ? "Gemerkt · gemeinsam" : "Für alle vorgeschlagen, wartet auf Freigabe";
  }
  return m.status === "aktiv" ? "Gemerkt · persönlich" : "Vorgeschlagen · persönlich";
}

function ScopeIcon({ bereich }: { bereich: Memo["bereich"] }) {
  return bereich === "gemeinsam" ? (
    <svg className="memo-icon memo-icon-shared" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="2" y="2" width="12" height="12" rx="2" />
    </svg>
  ) : (
    <svg className="memo-icon" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="5.2" r="2.8" />
      <path d="M2.8 14.5c.6-3 2.7-4.6 5.2-4.6s4.6 1.6 5.2 4.6" />
    </svg>
  );
}

/**
 * Hinweis "Gemerkt" unter dem Untertitel (Servernachricht "gemerkt"): 8 s sichtbar, Pause bei
 * Hover und Fokus. Gemeinsame Einträge betreffen Kollegen und fallen deshalb deutlicher auf.
 * "Rückgängig" gibt es nur, wenn der Kern eine Kennung mitschickt.
 * Die Live-Region bleibt immer im DOM, damit Screenreader neue Hinweise zuverlässig ansagen.
 */
export const GemerktHinweis = memo(function GemerktHinweis({ onVergessen }: { onVergessen: (m: Memo) => void }) {
  const memos = useSelect(wamo, (s) => s.memos);
  return (
    <div className="memos" role="status" aria-live="polite">
      {memos.map((m) => (
        <div
          key={m.key}
          className="memo"
          data-scope={m.bereich}
          onMouseEnter={() => halteMemo(m.key)}
          onMouseLeave={() => weiterMemo(m.key)}
          onFocus={() => halteMemo(m.key)}
          onBlur={() => weiterMemo(m.key)}
        >
          <ScopeIcon bereich={m.bereich} />
          <div className="memo-body">
            <b>{titel(m)}</b>
            {m.text && <span className="memo-text">{m.text}</span>}
            {m.bereich === "gemeinsam" && m.status === "aktiv" && (
              <span className="memo-hint">für alle bei WAMOCON sichtbar</span>
            )}
          </div>
          {m.id && (
            <button type="button" className="memo-undo" onClick={() => onVergessen(m)}>
              Rückgängig
            </button>
          )}
        </div>
      ))}
    </div>
  );
});
