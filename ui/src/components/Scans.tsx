"use client";

import { memo, useCallback, useEffect, useState } from "react";
import {
  ablageHerunterladen,
  ablageLaden,
  ablageLoeschen,
  groesseText,
  type AblageEintrag,
} from "@/lib/geraeteApi";

const NEU_LADEN_MS = 30_000;

function datum(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/**
 * Eigene Scans und vom NAS abgerufene Dateien (Ablage auf Sokrates-2, je Person). Lädt beim Öffnen, alle 30 s und
 * wenn `neu` sich ändert (z. B. nach der Servernachricht "scan"). Herunterladen und Löschen nur für eigene Einträge;
 * Namen sind Daten und erscheinen nur als Text.
 */
export const Scans = memo(function Scans({ neu = 0 }: { neu?: number }) {
  const [eintraege, setEintraege] = useState<AblageEintrag[]>([]);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laedt, setLaedt] = useState(false);

  const laden = useCallback(async () => {
    setLaedt(true);
    try {
      setEintraege(await ablageLaden());
      setFehler(null);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Ablage nicht erreichbar");
    } finally {
      setLaedt(false);
    }
  }, []);

  useEffect(() => {
    void laden();
    const t = setInterval(() => void laden(), NEU_LADEN_MS);
    return () => clearInterval(t);
  }, [laden, neu]);

  const holen = async (e: AblageEintrag) => {
    try {
      await ablageHerunterladen(e);
    } catch (err) {
      setFehler(err instanceof Error ? err.message : "Herunterladen fehlgeschlagen");
    }
  };

  const loeschen = async (e: AblageEintrag) => {
    try {
      if (await ablageLoeschen(e)) setEintraege((alt) => alt.filter((x) => x.id !== e.id));
    } catch (err) {
      setFehler(err instanceof Error ? err.message : "Löschen fehlgeschlagen");
    }
  };

  return (
    <section className="scans" aria-label="Scans und Dateien">
      <p className="eyebrow">Scans und Dateien</p>
      {fehler && <p role="alert">{fehler}</p>}
      {!fehler && !eintraege.length && <p>{laedt ? "Lädt …" : "Noch keine Scans. Sag zum Beispiel „Scann das Blatt auf dem Glas“."}</p>}
      {eintraege.length > 0 && (
        <ul>
          {eintraege.map((e) => (
            <li key={e.id}>
              <span>
                <b>{e.name}</b> · {e.art === "scan" ? "Scan" : "vom NAS"} · {groesseText(e.groesse)} · {datum(e.erstellt)}
              </span>
              <span>
                <button type="button" className="btn-secondary btn-small" onClick={() => void holen(e)}>
                  Herunterladen
                </button>{" "}
                <button type="button" className="btn-secondary btn-small" onClick={() => void loeschen(e)}>
                  Löschen
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p>
        <small>Scans bleiben 30 Tage, Dateien vom NAS 24 Stunden. Nur du siehst deine Ablage.</small>
      </p>
    </section>
  );
});
