"use client";

import { memo, useEffect, useRef } from "react";
import { LABEL } from "@/components/ActivityFeed";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";

const uhr = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });

/**
 * Gesprächsverlauf: je fertigem Zug ein Eintrag (Profil oder WAMO) mit Uhrzeit, Werkzeuge
 * eingerückt. role="log" (polite) bekommt nur fertige Züge, nie gestreamten Text.
 * Ab 1600 px eigene Spalte, darunter eine Schublade (Schaltfläche "Verlauf" in der Kopfzeile).
 */
export const Verlauf = memo(function Verlauf({ offen, onSchliessen }: { offen: boolean; onSchliessen: () => void }) {
  const log = useSelect(wamo, (s) => s.log);
  const who = useSelect(wamo, (s) => s.profil?.name.split(/\s+/)[0] ?? "Du");
  const liste = useRef<HTMLOListElement>(null);

  const letzteId = log.at(-1)?.id;

  // Neuester Eintrag sichtbar halten; nur die eigene Liste scrollen, nie die Seite.
  // Abhängig von der letzten id (nicht der Länge): bei voller Liste (200) wechselt nur die id.
  useEffect(() => {
    const el = liste.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [letzteId]);

  useEffect(() => {
    if (!offen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onSchliessen();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [offen, onSchliessen]);

  return (
    <aside id="verlauf" className="verlauf" data-offen={offen || undefined} aria-labelledby="verlauf-h">
      <div className="verlauf-head">
        <h2 id="verlauf-h" className="eyebrow">
          Verlauf
        </h2>
        <button type="button" className="verlauf-close" onClick={onSchliessen} tabIndex={offen ? 0 : -1}>
          Schließen
        </button>
      </div>
      <ol ref={liste} className="verlauf-list" role="log" aria-live="polite" aria-relevant="additions">
        {log.length === 0 && <li className="verlauf-leer">Noch kein Gespräch.</li>}
        {log.map((e) => (
          <li key={e.id} className="verlauf-eintrag" data-wer={e.wer}>
            <p className="verlauf-meta">
              <b>{e.wer === "wamo" ? "W.A.I.S." : who}</b>{" "}
              <time className="num" dateTime={new Date(e.zeit).toISOString()}>
                {uhr.format(e.zeit)}
              </time>
              {e.unterbrochen && <span className="verlauf-cut"> unterbrochen</span>}
            </p>
            <p className="verlauf-text">{e.text}</p>
            {e.werkzeuge && e.werkzeuge.length > 0 && (
              <ul className="verlauf-tools">
                {e.werkzeuge.map((w, i) => (
                  <li key={i}>{LABEL[w] ?? w}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </aside>
  );
});
