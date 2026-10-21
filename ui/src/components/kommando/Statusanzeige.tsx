"use client";

import { memo } from "react";
import { KERN, useBetriebsart, type Betriebsart } from "@/lib/betriebsart";
import { useSelect } from "@/lib/store";
import { istOnline, wamo } from "@/lib/wamo";
import { useGespraechsStart } from "./ansicht";
import { useJetzt } from "./useJetzt";
import { maxSitzungMinuten, sitzungsProzent, sitzungsText, uhrzeit } from "./zeit";

// Build-Konstante: längste Sitzung in Minuten (Voice-Core: WAMO_MAX_SITZUNG_MIN, Standard 120)
const MAX_MIN = maxSitzungMinuten(process.env.NEXT_PUBLIC_WAMO_MAX_SITZUNG_MIN);

/**
 * Rechter Teil der Kopfzeile: Sitzungsfortschritt als dünner Balken mit Prozentzahl (Gesprächsdauer
 * im Verhältnis zur längsten Sitzung), Abzeichen ONLINE mit Häkchen und die Uhrzeit. Der Kern (Büro:
 * Sokrates-2, Unterwegs: dieser PC) steht als Kleinstzeile daneben.
 */
export const Statusanzeige = memo(function Statusanzeige() {
  const presence = useSelect(wamo, (s) => s.presence);
  const online = useSelect(wamo, istOnline);
  const modus = useSelect(wamo, (s) => s.betrieb?.modus ?? null);
  const gewaehlt = useBetriebsart();
  const start = useGespraechsStart();
  const takt = useJetzt(10_000);

  const art: Betriebsart = modus ? (modus === "lokal" ? "unterwegs" : "buero") : gewaehlt;
  const prozent = sitzungsProzent(start, takt.datum, MAX_MIN);
  const zustand = online ? "online" : presence === "connecting" ? "verbindet" : "getrennt";
  const zustandText = online ? "ONLINE" : presence === "connecting" ? "VERBINDET" : "GETRENNT";

  return (
    <div className="kz-status">
      {online && start !== null && (
        <div
          className="kz-meter"
          role="progressbar"
          aria-label="Sitzungsdauer"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={prozent}
          aria-valuetext={sitzungsText(start, takt.datum, MAX_MIN)}
        >
          <span className="kz-meter-text" aria-hidden="true">
            Sitzung <b className="num">{prozent} %</b>
          </span>
          <span className="kz-meter-bahn" aria-hidden="true">
            <span style={{ width: `${prozent}%` }} />
          </span>
        </div>
      )}
      <span className="kz-badge" data-zustand={zustand}>
        {zustand === "online" ? (
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3 8.6l3.3 3.3L13 4.9" />
          </svg>
        ) : (
          <span className="kz-badge-punkt" aria-hidden="true" />
        )}
        {zustandText}
      </span>
      <span className="kz-kernort">Kern {KERN[art].ort}</span>
      <time className="kz-uhr num" dateTime={new Date(takt.datum).toISOString()}>
        {uhrzeit(new Date(takt.datum))}
      </time>
    </div>
  );
});
