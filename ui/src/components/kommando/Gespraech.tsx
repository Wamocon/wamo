"use client";

import { memo } from "react";
import { VoiceLine } from "@/components/VoiceLine";
import { menschZeile, wamoZeile } from "@/lib/gespraech";
import { useSelect } from "@/lib/store";
import { historyRef, istOnline, wamo } from "@/lib/wamo";

/**
 * Block "Gespräch" ganz oben in der linken Spalte: Überschrift mit dem Abzeichen online oder offline, darunter die
 * Stimmlinie mit Legende und die letzten Zeilen von Person und WAMO (lib/gespraech.ts). Die Zeilen sind bewusst keine
 * Live-Region: Ton und Verlauf (role="log") tragen den Inhalt, ein Vorlesen im Worttakt gäbe es doppelt.
 * Der Block abonniert nur Zeichenketten und Wahrheitswerte, die Stimmlinie zeichnet sich selbst aus historyRef.
 */
export const Gespraech = memo(function Gespraech() {
  const online = useSelect(wamo, istOnline);
  const mensch = useSelect(wamo, (s) => menschZeile(s.caption, s.log));
  const wamoText = useSelect(wamo, (s) => wamoZeile(s.caption, s.log));

  return (
    <section className="kz-gespraech" aria-labelledby="kz-h-gespraech">
      <h2 className="kz-gespraech-kopf">
        <span id="kz-h-gespraech">Gespräch</span>
        <span className="hud-badge" data-state={online ? "an" : "aus"}>
          {online ? "Online" : "Offline"}
        </span>
      </h2>
      <VoiceLine history={historyRef} />
      <p className="legend" aria-hidden="true">
        <span className="legend-user">Du</span>
        <span className="legend-bot">WAMO</span>
      </p>
      <div className="kz-zeilen">
        <p className="kz-zeile-mensch" data-leer={mensch ? undefined : "true"}>
          <span className="kz-wer">Du</span>
          {mensch || "Noch nichts gesagt."}
        </p>
        {wamoText && (
          <p className="kz-zeile-wamo">
            <span className="kz-wer">WAMO</span>
            <span className="kz-text">{wamoText}</span>
          </p>
        )}
      </div>
    </section>
  );
});
