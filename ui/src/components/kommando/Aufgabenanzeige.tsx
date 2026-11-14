"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { LABEL } from "@/components/ActivityFeed";
import {
  aufgabeBeenden,
  aufgabeDauer,
  aufgabeZustand,
  istOffen,
  type Aufgabe,
  type AufgabeZustand,
} from "@/lib/aufgabenanzeige";
import type { ActivityStatus } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import { istOnline, wamo } from "@/lib/wamo";

/**
 * Aufgabenanzeige neben dem Kern: Schrittnummer groß, Dauer als laufende Uhr, jeder Werkzeugschritt als Balken
 * (erledigt = rot gefüllt, läuft = Lauflicht, wartet = pulsiert, Fehler = gestrichen), darunter der Fortschritt.
 * Alle Werte sind echt: Schritte und Zeiten kommen aus den Aktivitätsmeldungen des Kerns (lib/aufgabenanzeige.ts).
 */

const ZUSTAND_TEXT: Record<AufgabeZustand, string> = {
  laeuft: "Läuft",
  wartet: "Wartet auf Freigabe",
  fehler: "Mit Problem beendet",
  fertig: "Erledigt",
};

const MARKE: Record<ActivityStatus, string> = {
  laeuft: "",
  wartet: "!",
  fertig: "✓",
  abgelehnt: "✕",
  fehler: "✕",
};

const STATUS_WORT: Record<ActivityStatus, string> = {
  laeuft: "läuft",
  wartet: "wartet auf Freigabe",
  fertig: "erledigt",
  abgelehnt: "abgelehnt",
  fehler: "Fehler",
};

/** Dauer als Stoppuhr: unter einer Minute "3.2s", darüber "1:04.5". */
export function dauerText(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

function zwei(n: number): string {
  return String(n).padStart(2, "0");
}

/** Laufende Dauer: zählt nur, solange ein Schritt offen ist, also nur mit laufender Sitzung (eigener Takt, die Seite rendert nicht mit). */
function Dauer({ aufgabe }: { aufgabe: Aufgabe }) {
  // Startwert 0 statt der Uhr: gleich beim ersten Rendern auf dem Server und im Browser
  const [jetzt, setJetzt] = useState(0);
  const laeuft = aufgabe.schritte.some(istOffen); // abgeschlossen: Dauer bis zum letzten Schritt, ohne Uhr
  useEffect(() => {
    if (!laeuft) return;
    const takt = () => setJetzt(performance.now());
    const erster = setTimeout(takt, 0);
    const id = setInterval(takt, 100);
    return () => {
      clearTimeout(erster);
      clearInterval(id);
    };
  }, [laeuft]);
  return (
    <span className="az-uhr-wert" role="timer">
      {dauerText(aufgabeDauer(aufgabe, jetzt))}
    </span>
  );
}

export const Aufgabenanzeige = memo(function Aufgabenanzeige() {
  const roh = useSelect(wamo, (s) => s.aufgabe);
  const online = useSelect(wamo, istOnline);
  // Ohne laufende Sitzung läuft nichts: offene Schritte gelten als abgebrochen, die Uhr steht (useWamo schließt sie beim Trennen ohnehin)
  const aufgabe = useMemo(() => (online ? roh : aufgabeBeenden(roh)), [roh, online]);
  const schritte = aufgabe.schritte;
  if (!schritte.length) return null;
  const zustand = aufgabeZustand(aufgabe);
  const erledigt = schritte.filter((s) => s.status === "fertig").length;
  const offen = schritte.findIndex(istOffen);
  const aktuell = offen >= 0 ? offen + 1 : schritte.length; // offener Schritt, sonst der letzte

  return (
    <section className="az" data-state={zustand} aria-label="Aktuelle Aufgabe">
      <header className="az-kopf">
        <div className="az-titel">
          <span className="hud-label">Aufgabe</span>
          <span className="hud-badge" data-state={zustand} role="status">
            {zustand === "fertig" ? "✓ " : "● "}
            {ZUSTAND_TEXT[zustand]}
          </span>
        </div>
        <div className="az-uhr">
          <span className="hud-label">Dauer</span>
          <Dauer aufgabe={aufgabe} />
        </div>
      </header>

      <p
        className="az-schritt"
        aria-label={`Schritt ${aktuell} von ${schritte.length}`}
      >
        <span className="az-schritt-wort">Schritt</span>
        <span className="az-schritt-zahl">{zwei(aktuell)}</span>
        <span className="az-schritt-von">/ {zwei(schritte.length)}</span>
      </p>

      <ol className="az-schritte">
        {schritte.map((s, i) => (
          <li key={s.id} className="az-zeile" data-status={s.status}>
            <span className="az-idx" aria-hidden="true">
              {zwei(i + 1)}
            </span>
            <span className="az-balken">
              <span className="az-fuellung" />
              <span className="az-text">
                {LABEL[s.name] ?? s.name}
                {s.detail && <span className="az-detail"> {s.detail}</span>}
              </span>
            </span>
            <span className="az-marke">
              <span aria-hidden="true">{MARKE[s.status]}</span>
              <span className="sr-only">{STATUS_WORT[s.status]}</span>
            </span>
          </li>
        ))}
      </ol>

      <div className="az-fortschritt">
        <span className="hud-label">Fortschritt</span>
        <span className="az-fortschritt-wert">
          {erledigt}/{schritte.length}
        </span>
        <span className="az-fortschritt-bahn" aria-hidden="true">
          <span style={{ width: `${(erledigt / schritte.length) * 100}%` }} />
        </span>
      </div>
    </section>
  );
});
