"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { alsHinweise, kernJson, type Hinweis } from "@/lib/kernApi";
import { zeigeToast } from "@/lib/wamo";

/**
 * Hinweise an das eigene Profil (voice-core/hinweise_api.py, Tabelle hinweise): geteilte Arbeit, Änderungen an
 * eigenen Geräten und für Admins neue oder wartende Geräte. Höchstens drei Karten oben in der Präsenzspalte, darunter
 * die Zahl der weiteren. Abfrage beim Start, jede Minute (nur bei sichtbarem Fenster) und wenn das Fenster wieder
 * sichtbar wird; neue Hinweise werden angesagt. Ist der Kern nicht erreichbar, bleibt der Bereich still leer.
 * Quittierte Hinweise verschwinden sofort und kommen auch durch eine ältere, später eintreffende Antwort nicht zurück.
 */

const INTERVALL_MS = 60_000;
const MAX_KARTEN = 3;

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
  const runde = useRef(0); // gegen Überholen: nur die Antwort der letzten Anfrage zählt
  const quittiert = useRef(new Set<string>());
  const bekannt = useRef<Set<string> | null>(null); // null: noch nie geladen (nichts ansagen)

  const laden = useCallback(async () => {
    const meine = ++runde.current;
    try {
      const daten = (await kernJson("/hinweise")) as { hinweise?: unknown };
      if (meine !== runde.current) return;
      const offen = alsHinweise(daten?.hinweise).filter((h) => !h.gelesen && !quittiert.current.has(h.id));
      const neu = bekannt.current ? offen.filter((h) => !bekannt.current?.has(h.id)) : [];
      bekannt.current = new Set(offen.map((h) => h.id));
      if (neu.length === 1) zeigeToast(`Neuer Hinweis: ${neu[0].titel}`);
      else if (neu.length > 1) zeigeToast(`${neu.length} neue Hinweise`);
      setHinweise(offen);
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

  const gelesen = async (ids: string[]) => {
    ids.forEach((id) => quittiert.current.add(id));
    runde.current++; // eine gerade laufende Abfrage darf sie nicht zurückholen
    setHinweise((h) => h.filter((x) => !ids.includes(x.id)));
    const fehlgeschlagen = await Promise.allSettled(
      ids.map((id) => kernJson(`/hinweise/${encodeURIComponent(id)}/gelesen`, "POST")),
    );
    if (fehlgeschlagen.some((r) => r.status === "rejected")) {
      ids.forEach((id) => quittiert.current.delete(id)); // nicht quittiert: beim nächsten Laden wieder da
      void laden();
    }
  };

  if (!hinweise.length) return null;
  const sichtbar = hinweise.slice(0, MAX_KARTEN);
  const weitere = hinweise.length - sichtbar.length;
  return (
    <section className="hinweise" aria-label="Hinweise">
      {sichtbar.map((h) => (
        <article key={h.id} className="hinweis-karte" data-art={h.art}>
          <p className="eyebrow">{ART_TEXT[h.art] ?? "Hinweis"}</p>
          <p className="hinweis-titel">{h.titel}</p>
          {h.text && <p className="hinweis-text">{h.text}</p>}
          <button
            type="button"
            className="error-close"
            aria-label={`Gelesen: ${h.titel}`}
            onClick={() => void gelesen([h.id])}
          >
            Gelesen
          </button>
        </article>
      ))}
      <div className="hinweise-fuss">
        {weitere > 0 && <span className="hinweise-weitere">und {weitere} weitere</span>}
        {sichtbar.length > 1 && (
          <button type="button" className="error-close" onClick={() => void gelesen(sichtbar.map((h) => h.id))}>
            Diese {sichtbar.length} als gelesen markieren
          </button>
        )}
      </div>
    </section>
  );
});
