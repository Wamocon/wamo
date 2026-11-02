"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { abgeschlossenText, ansicht, type Status } from "@/lib/aenderungen";
import { statusLaden } from "@/lib/aenderungenApi";
import { Block, Daten } from "./Block";

/**
 * Abschnitt "Änderungen" (nur Admin): der Stand der Selbstüberarbeitung (Konzept docs/selbstueberarbeitung.md, Schritt 1.5). Die Karte liest den
 * Stand des Release-Dienstes über den Kern (GET /wamo/api/release/status) und fragt alle 10 Sekunden neu, solange der Abschnitt offen und der Tab
 * sichtbar ist. Sie ändert nichts: Anfragen vorschlagen kommt mit den Werkzeugen des Kerns (Schritt 3.4), Freigeben geht allein über den
 * Freigabe-Dienst des Ankers mit frischem zweiten Faktor, sobald der Release-Dienst Diff und Versionsvorschlag liefert.
 *
 * Jede Lage steht als Text da; Form und Farbe sind nur Beigabe (die Seite verlässt sich nie auf Farbe allein).
 */

const INTERVALL_MS = 10_000;

const LAGE_KENNZEICHEN = {
  nicht_erreichbar: "Nicht erreichbar",
  stoerung: "Störung",
  gesperrt: "Gesperrt",
  wartet: "Wartet auf dich",
  arbeit: "In Arbeit",
  bereit: "Bereit",
} as const;

const ZUSTAND_TEXT = { fertig: "fertig", aktuell: "jetzt", offen: "offen" } as const;

export const AenderungenAbschnitt = memo(function AenderungenAbschnitt() {
  const [status, setStatus] = useState<Status | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [geladen, setGeladen] = useState(false);
  const [laedt, setLaedt] = useState(false);
  const aktiv = useRef(true);

  const laden = useCallback(async () => {
    setLaedt(true);
    const erg = await statusLaden();
    if (!aktiv.current) return;
    if (erg.ok) {
      setStatus(erg.status);
      setFehler(null);
    } else {
      setStatus(null);
      setFehler(erg.text);
    }
    setGeladen(true);
    setLaedt(false);
  }, []);

  useEffect(() => {
    aktiv.current = true;
    void laden();
    const takt = setInterval(() => {
      if (document.visibilityState === "visible") void laden();
    }, INTERVALL_MS);
    return () => {
      aktiv.current = false;
      clearInterval(takt);
    };
  }, [laden]);

  const a = useMemo(() => ansicht(status, fehler), [status, fehler]);
  const zuletzt = status?.verfuegbar ? status.abgeschlossen : [];

  return (
    <>
      <Block titel="Lage">
        {!geladen ? (
          <p className="ez-leer" role="status">
            Lade den Stand ...
          </p>
        ) : (
          <div className="ae-lage" data-lage={a.lage} role="status">
            <span className="ae-lage-marke">{LAGE_KENNZEICHEN[a.lage]}</span>
            <p className="ez-text">{a.lageText}</p>
          </div>
        )}
        {a.hinweise.map((h) => (
          <p key={h} className="ez-klein">
            {h}
          </p>
        ))}
        <div className="ae-aktionen">
          <button type="button" className="btn-secondary" onClick={() => void laden()} disabled={laedt}>
            {laedt ? "Lade ..." : "Neu laden"}
          </button>
          {status?.verfuegbar && status.zeit && <span className="ez-klein">Stand vom Kern: {status.zeit}</span>}
        </div>
      </Block>

      {a.anfrage && (
        <Block titel={`Offene Änderung ${a.anfrage.kurz}`}>
          <Daten
            zeilen={[
              { name: "Quelle", wert: a.anfrage.quelleText },
              ...(a.anfrage.versionText ? [{ name: "Version", wert: a.anfrage.versionText }] : []),
              ...(a.anfrage.fristText ? [{ name: "Frist bis", wert: a.anfrage.fristText }] : []),
            ]}
          />
          {a.anfrage.schritte.length > 0 && (
            <ol className="ae-weg" aria-label="Weg der Änderung">
              {a.anfrage.schritte.map((s) => (
                <li key={s.name} data-zustand={s.zustand} aria-current={s.zustand === "aktuell" ? "step" : undefined}>
                  <span className="ae-marke" aria-hidden="true" />
                  <span className="ae-titel">{s.titel}</span>
                  <span className="ae-zustand">{ZUSTAND_TEXT[s.zustand]}</span>
                </li>
              ))}
            </ol>
          )}
        </Block>
      )}

      {a.zeilen.length > 0 && (
        <Block titel="Dienste">
          <Daten zeilen={a.zeilen} />
        </Block>
      )}

      {zuletzt.length > 0 && (
        <Block titel="Zuletzt abgeschlossen">
          <ul className="ae-zuletzt">
            {zuletzt.map((e) => (
              <li key={e.id} className="ez-klein">
                {abgeschlossenText(e)}
              </li>
            ))}
          </ul>
        </Block>
      )}
    </>
  );
});
