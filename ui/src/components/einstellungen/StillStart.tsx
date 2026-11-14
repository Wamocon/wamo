"use client";

import { memo, useId } from "react";
import { stillStartSetzen } from "@/lib/einstellungen";
import { LEERLAUF_MS } from "@/lib/stillStart";
import { useStillStartErlaubt } from "@/lib/useStillStart";

const MINUTEN = Math.round(LEERLAUF_MS / 60_000);

/**
 * Geräteschalter "Still starten beim Tippen" (Abschnitt Sprache und Ton): Standard an, gilt für diese Person auf diesem Gerät (kein
 * Server-Zustand). Auf Laborplätzen gibt es ihn nicht. Der Hinweis nennt die Grenze: Der Kern löst je Profil die laufende Sitzung ab, und die
 * Oberfläche erfährt vor dem Start nichts von einer Sitzung auf einem anderen Gerät. Wer das nicht will, schaltet es auf dem jeweiligen Gerät aus.
 */
export const StillStartEinstellung = memo(function StillStartEinstellung() {
  const id = useId();
  const { schalter, labor, userId } = useStillStartErlaubt();
  const gesperrt = labor || userId === null;
  return (
    <>
      <label className="pc-auto" htmlFor={`${id}-still`}>
        <input
          id={`${id}-still`}
          type="checkbox"
          checked={schalter && !labor}
          disabled={gesperrt}
          aria-describedby={`${id}-hinweis`}
          onChange={(e) => stillStartSetzen(userId, e.target.checked)}
        />
        <span>Still starten beim Tippen (dieses Gerät)</span>
      </label>
      <div id={`${id}-hinweis`}>
        {labor ? (
          <p className="ez-klein">Auf Laborplätzen nicht verfügbar. Dort startet ein Gespräch nur der Betreuer.</p>
        ) : (
          <>
            <p className="ez-klein">
              Der erste Tastendruck im Eingabefeld startet W.A.I.S. ohne Mikrofon. Die Antworten kommen als Text, das Mikrofon öffnet sich erst, wenn du
              es einschaltest. Nach {MINUTEN} Minuten ohne Eingabe endet die Sitzung wieder; solange die PC-Steuerung an ist oder etwas auf deine
              Entscheidung wartet, bleibt sie offen.
            </p>
            <p className="ez-klein">
              Ein stiller Start löst eine laufende Sitzung auf einem anderen Gerät ab, denn der Kern führt je Profil nur eine Sitzung, und
              dieses Gerät kann vorher nicht erkennen, ob anderswo eine läuft. Schalte es dort aus, wo du das nicht willst.
            </p>
          </>
        )}
      </div>
    </>
  );
});
