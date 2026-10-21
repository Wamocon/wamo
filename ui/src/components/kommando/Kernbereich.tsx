"use client";

import { memo } from "react";
import { LABEL } from "@/components/ActivityFeed";
import { LiveCaption } from "@/components/LiveCaption";
import { useSelect } from "@/lib/store";
import {
  istOnline,
  laufendesWerkzeug,
  levelsRef,
  wamo,
  wartetAufEntscheidung,
  type Presence,
} from "@/lib/wamo";
import { useEffektiveDarstellung } from "./ansicht";
import { PraesenzKern } from "./PraesenzKern";
import { ZentraleStatus } from "./ZentraleStatus";

const PRESENCE_TEXT: Record<Presence, string> = {
  offline: "schläft",
  connecting: "wacht auf",
  idle: "ist da",
  listening: "hört zu",
  thinking: "denkt nach",
  speaking: "spricht",
};

/** Was WAMO gerade tut, in Worten: Freigabe wartet, Werkzeug läuft oder Gesprächszustand. */
function useZustand() {
  const presence = useSelect(wamo, (s) => s.presence);
  const werkzeug = useSelect(wamo, (s) => (istOnline(s) ? laufendesWerkzeug(s) : null));
  const freigabe = useSelect(wamo, wartetAufEntscheidung);
  const text = freigabe
    ? "wartet auf deine Entscheidung"
    : werkzeug
      ? `handelt: ${LABEL[werkzeug] ?? werkzeug}`
      : PRESENCE_TEXT[presence];
  return { presence, text, kennung: freigabe ? "freigabe" : werkzeug ? "handelt" : presence };
}

/**
 * Namensschild als Pille: Kleinstzeile mit dem Zustand über dem Namen. Im DOM steht der Name zuerst,
 * damit Screenreader "WAMO hört zu" lesen; die Reihenfolge dreht nur das CSS.
 */
function Namensschild({ presence, text, kennung }: { presence: Presence; text: string; kennung: string }) {
  return (
    <p className="kz-schild" data-presence={presence} data-zustand={kennung}>
      <span className="kz-schild-name">WAMO</span>
      <span className="kz-schild-status">{text}</span>
    </p>
  );
}

/**
 * Obere Hälfte der Bühne: Kern, Namensschild (in der Zentrale großer Name mit Statuszeilen und Zähler)
 * und die Live-Untertitel. Der Hinweissatz kommt aus LiveCaption (Sag einfach, was du brauchst).
 */
export const Kernbereich = memo(function Kernbereich() {
  const { effektiv } = useEffektiveDarstellung();
  const { presence, text, kennung } = useZustand();
  const zentrale = effektiv === "zentrale";
  return (
    <section id="kz-p-kern" className="kz-kernbereich" data-p="kern" data-presence={presence} aria-label="Gesprächszustand">
      <div className="kz-kernwrap">
        <PraesenzKern levels={levelsRef} dichte={zentrale ? 1.5 : 1} />
      </div>
      {zentrale ? (
        <ZentraleStatus presence={presence} zustand={text} />
      ) : (
        <Namensschild presence={presence} text={text} kennung={kennung} />
      )}
      <LiveCaption />
    </section>
  );
});
