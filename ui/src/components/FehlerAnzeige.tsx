"use client";

import { memo } from "react";
import { MikrofonWahl } from "@/components/MikrofonWahl";
import { stilleAusblenden, type Mikrofon } from "@/lib/mikrofon";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";

const OHNE_MIKROFON_HILFE =
  "Schließt die Warnung und lässt sie für dieses Mikrofon aus, bis es wieder Ton liefert. Du kannst WAMO weiter tippen.";

/** Fehler in Tinte mit rotem Symbol und sicherer nächster Aktion. */
export const FehlerAnzeige = memo(function FehlerAnzeige({
  onRetry,
  onAbmelden,
  onSchliessen,
  onMikrofone,
  onMikrofonWaehlen,
}: {
  onRetry: () => void;
  onAbmelden: () => void;
  onSchliessen: () => void;
  onMikrofone: () => Promise<Mikrofon[]>;
  onMikrofonWaehlen: (id: string) => boolean;
}) {
  const error = useSelect(wamo, (s) => s.error);
  const offline = useSelect(wamo, (s) => s.presence === "offline");
  if (!error) return null;
  const hinweis = error.art === "hinweis";
  const retry = offline && !hinweis && error.art !== "anmeldung";
  return (
    <div className="error" role={hinweis ? "status" : "alert"} data-art={error.art}>
      <svg className="sym sym-warn" viewBox="0 0 16 16" aria-hidden="true">
        <rect x="1" y="1" width="14" height="14" rx="2" />
        <path d="M8 4v5M8 11.2v.6" />
      </svg>
      <div className="error-body">
        <p>{error.text}</p>
        <div className="error-actions">
          {retry && (
            <button type="button" className="btn-secondary btn-small" onClick={onRetry}>
              Erneut verbinden
            </button>
          )}
          {error.art === "anmeldung" && (
            <button type="button" className="btn-secondary btn-small" onClick={onAbmelden}>
              Abmelden
            </button>
          )}
          {error.art === "mikrofon" && (
            <>
              <button
                type="button"
                className="btn-secondary btn-small"
                title={OHNE_MIKROFON_HILFE}
                onClick={() => {
                  stilleAusblenden(wamo.get().mikrofon); // dieselbe Bezeichnung, nach der der Wächter in useWamo.ts fragt
                  onSchliessen();
                }}
              >
                Ohne Mikrofon weiter
              </button>
              <MikrofonWahl laden={onMikrofone} waehlen={onMikrofonWaehlen} />
            </>
          )}
          <button type="button" className="error-close" onClick={onSchliessen}>
            Schließen
          </button>
        </div>
      </div>
    </div>
  );
});
