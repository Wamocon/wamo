"use client";

import { memo } from "react";
import { useStillStartErlaubt } from "@/lib/useStillStart";
import { useSelect } from "@/lib/store";
import { HINWEIS_ANDERES_FENSTER } from "@/lib/stillStart";
import { wamo } from "@/lib/wamo";

/**
 * Hinweis nach einer Übernahme: Eine neuere Sitzung dieses Profils (anderes Fenster oder Gerät) hat die Sitzung hier abgelöst. Das Tippen
 * startet hier dann nicht von selbst neu (kein Hin und Her), erst der Klick auf "Hier übernehmen" startet wieder, still wie beim Tippen.
 * Nur sichtbar, wenn der Schalter "Still starten beim Tippen" an ist und keine Sitzung läuft.
 */
export const UebernehmenHinweis = memo(function UebernehmenHinweis({ onUebernehmen }: { onUebernehmen: () => void }) {
  const { erlaubt } = useStillStartErlaubt();
  const sichtbar = useSelect(wamo, (s) => s.stillStart.uebernommen && s.presence === "offline");
  if (!erlaubt || !sichtbar) return null;
  return (
    <div className="error" role="status" data-art="hinweis">
      <svg className="sym sym-warn" viewBox="0 0 16 16" aria-hidden="true">
        <rect x="1" y="1" width="14" height="14" rx="2" />
        <path d="M8 4v5M8 11.2v.6" />
      </svg>
      <div className="error-body">
        <p>{HINWEIS_ANDERES_FENSTER}</p>
        <div className="error-actions">
          <button type="button" className="btn-secondary btn-small" onClick={onUebernehmen}>
            Hier übernehmen
          </button>
        </div>
      </div>
    </div>
  );
});

/**
 * Hauptknöpfe einer still gestarteten Sitzung (Start beim Tippen): "Gespräch starten" macht daraus ein Gespräch mit Mikrofon und Stimme,
 * in derselben Sitzung (keine neue, also keine Verdrängung), "Beenden" trennt.
 */
export const StillAktionen = memo(function StillAktionen({ onSprechen, onBeenden }: { onSprechen: () => void; onBeenden: () => void }) {
  return (
    <>
      <button type="button" className="btn-primary wake" onClick={onSprechen}>
        Gespräch starten
      </button>
      <button type="button" className="btn-secondary" onClick={onBeenden}>
        Beenden
      </button>
    </>
  );
});
