"use client";

import { memo, useCallback, useEffect, useState } from "react";
import { kernJson, type Hinweis } from "@/lib/kernApi";
import { sauber } from "@/lib/nachrichten";

/**
 * Hinweise an das eigene Profil (voice-core/hinweise_api.py, Tabelle hinweise): geteilte Arbeit, Änderungen an
 * eigenen Geräten und für Admins neue oder wartende Geräte. Karten oben in der Präsenzspalte, jede mit "Gelesen".
 * Abfrage beim Start, jede Minute (nur bei sichtbarem Fenster) und wenn das Fenster wieder sichtbar wird. Ist der
 * Kern nicht erreichbar, bleibt der Bereich still leer. Titel und Text sind reiner Text.
 */

const INTERVALL_MS = 60_000;
const MAX_KARTEN = 5;

const ART_TEXT: Record<string, string> = {
  geraet_gebunden: "Gerät",
  geraet_beantragt: "Gerät wartet",
  geraet_konflikt: "Gerät",
  geraet_gesperrt: "Gerät gesperrt",
  geraet_entsperrt: "Gerät",
  geraet_widerrufen: "Gerät widerrufen",
  teilung_erhalten: "Geteilt",
  teilung_geaendert: "Geteilt",
  teilung_widerrufen: "Geteilt",
  scan_fertig: "Scan",
};

export const Hinweise = memo(function Hinweise() {
  const [hinweise, setHinweise] = useState<Hinweis[]>([]);

  const laden = useCallback(async () => {
    try {
      const daten = await kernJson<{ hinweise: Hinweis[] }>("/hinweise");
      setHinweise(daten.hinweise.filter((h) => !h.gelesen));
    } catch {
      // Kern ohne Schnittstelle (älterer Stand) oder gerade nicht erreichbar: nichts anzeigen
    }
  }, []);

  useEffect(() => {
    void laden();
    const uhr = setInterval(() => {
      if (document.visibilityState === "visible") void laden();
    }, INTERVALL_MS);
    const sichtbar = () => {
      if (document.visibilityState === "visible") void laden();
    };
    document.addEventListener("visibilitychange", sichtbar);
    return () => {
      clearInterval(uhr);
      document.removeEventListener("visibilitychange", sichtbar);
    };
  }, [laden]);

  const gelesen = async (id: string | null) => {
    setHinweise((h) => (id ? h.filter((x) => x.id !== id) : []));
    try {
      await kernJson(id ? `/hinweise/${encodeURIComponent(id)}/gelesen` : "/hinweise/gelesen", "POST");
    } catch {
      void laden(); // nicht quittiert: beim nächsten Laden wieder da
    }
  };

  if (!hinweise.length) return null;
  return (
    <section className="hinweise" aria-label="Hinweise">
      {hinweise.slice(0, MAX_KARTEN).map((h) => (
        <article key={h.id} className="hinweis-karte" data-art={h.art}>
          <p className="eyebrow">{ART_TEXT[h.art] ?? "Hinweis"}</p>
          <p className="hinweis-titel">{sauber(h.titel, 200, false)}</p>
          {h.text && <p className="hinweis-text">{sauber(h.text, 600, false)}</p>}
          <button type="button" className="error-close" onClick={() => void gelesen(h.id)}>
            Gelesen
          </button>
        </article>
      ))}
      {hinweise.length > 1 && (
        <button type="button" className="error-close hinweise-alle" onClick={() => void gelesen(null)}>
          Alle {hinweise.length} als gelesen markieren
        </button>
      )}
    </section>
  );
});
