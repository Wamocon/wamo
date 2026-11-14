"use client";

import { memo } from "react";
import { LABEL } from "@/components/ActivityFeed";
import { useSelect } from "@/lib/store";
import { istOnline, wamo, wartetAufEntscheidung, type Presence } from "@/lib/wamo";
import { useGespraechsStart } from "./ansicht";
import { useJetzt } from "./useJetzt";
import { formatDauer } from "./zeit";

/**
 * Zentrale: großer Name, Statuszeilen mit Häkchen und ein Zähler. Der Zähler läuft für den laufenden
 * Entwicklungsauftrag (Werkzeuge entwicklung_*), sonst für die Gesprächsdauer. Die Zeilen leitet die
 * Komponente aus dem vorhandenen Zustand ab (Untertitel, Gesprächszustand, laufendes Werkzeug).
 */

type Zeile = { text: string; art: "fertig" | "laeuft" | "wartet" | "ruhig" };

function Marke({ art }: { art: Zeile["art"] }) {
  switch (art) {
    case "fertig":
      return (
        <svg className="kz-marke kz-marke-fertig" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M3 8.6l3.3 3.3L13 4.9" />
        </svg>
      );
    case "laeuft":
      return (
        <svg className="kz-marke kz-marke-laeuft" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="5.5" />
          <path className="arc" d="M8 2.5a5.5 5.5 0 0 1 5.5 5.5" />
        </svg>
      );
    case "wartet":
      return (
        <svg className="kz-marke kz-marke-wartet" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="5.5" />
          <path d="M6.3 5.8v4.4M9.7 5.8v4.4" />
        </svg>
      );
    default:
      return (
        <svg className="kz-marke kz-marke-ruhig" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="5.5" />
        </svg>
      );
  }
}

export const ZentraleStatus = memo(function ZentraleStatus({ presence, zustand }: { presence: Presence; zustand: string }) {
  const online = useSelect(wamo, istOnline);
  const freigabe = useSelect(wamo, wartetAufEntscheidung);
  const werkzeug = useSelect(wamo, (s) => s.activities.find((a) => a.status === "laeuft")?.name ?? null);
  const entwicklungSeit = useSelect(
    wamo,
    (s) => s.activities.find((a) => a.status === "laeuft" && a.name.startsWith("entwicklung_"))?.startedAt ?? null,
  );
  const gehoert = useSelect(wamo, (s) => Boolean(s.caption.userDone || s.caption.userLive));
  const abgeschlossen = useSelect(wamo, (s) => Boolean(s.caption.userDone) && !s.caption.userLive);
  const start = useGespraechsStart();
  const takt = useJetzt(1000);

  const antwort = presence === "thinking" || presence === "speaking";
  const zeilen: Zeile[] = [];
  if (!online) {
    zeilen.push({ text: presence === "connecting" ? "Verbindung wird aufgebaut" : "Nicht verbunden", art: "ruhig" });
  } else {
    if (gehoert || antwort) zeilen.push({ text: "Stimme erkannt", art: "fertig" });
    if (abgeschlossen || antwort) zeilen.push({ text: "Anfrage empfangen", art: "fertig" });
    if (freigabe) zeilen.push({ text: "Freigabe wartet auf dich", art: "wartet" });
    else if (werkzeug) zeilen.push({ text: `W.A.I.S. ${LABEL[werkzeug] ?? werkzeug}`, art: "laeuft" });
    else if (presence === "thinking") zeilen.push({ text: "W.A.I.S. denkt nach", art: "laeuft" });
    else if (presence === "speaking") zeilen.push({ text: "W.A.I.S. antwortet", art: "laeuft" });
    if (!zeilen.length) zeilen.push({ text: "Bereit für deine Anfrage", art: "ruhig" });
  }

  // Zähler: Entwicklungsauftrag (startedAt ist performance.now) oder Gesprächsdauer
  const dauer =
    entwicklungSeit !== null ? takt.perf - entwicklungSeit : online && start !== null ? takt.datum - start : 0;
  const beschriftung = entwicklungSeit !== null ? "Dauer Entwicklungsauftrag" : online ? "Gesprächsdauer" : "Bereit";

  return (
    <div className="kz-zentrale" data-presence={presence} aria-label={`W.A.I.S. ${zustand}`} role="group">
      <p className="kz-gross">W.A.I.S.</p>
      <ul className="kz-haken" aria-label="Status">
        {zeilen.map((z) => (
          <li key={z.text} data-art={z.art}>
            <span>{z.text}</span>
            <Marke art={z.art} />
          </li>
        ))}
      </ul>
      <p className="kz-zaehler-label">{beschriftung}</p>
      <p className="kz-zaehler num" role="timer" aria-label={beschriftung} data-aktiv={dauer > 0 || undefined}>
        {formatDauer(dauer)}
      </p>
    </div>
  );
});
