"use client";

import { memo } from "react";
import { useSelect } from "@/lib/store";
import { istOnline, wamo } from "@/lib/wamo";

/**
 * Live-Untertitel (Karaoke): der aktuelle WAMO-Satz groß, gesprochene Wörter in Tinte, kommende
 * grau. Nach einer Unterbrechung bleibt der ungesagte Rest 2 s durchgestrichen stehen. Am Zugende
 * blendet der Untertitel nach 4 s auf 60 % ab. Darüber die Nutzerzeile mit dem Profilnamen,
 * Zwischenstände kursiv. Nur diese Komponente rendert im Worttakt neu.
 * Der WAMO-Text ist aria-hidden: Ton und Verlauf (role="log") tragen den Inhalt.
 */
export const LiveCaption = memo(function LiveCaption() {
  const c = useSelect(wamo, (s) => s.caption);
  const who = useSelect(wamo, (s) => s.profil?.name.split(/\s+/)[0] ?? "Du");
  const online = useSelect(wamo, istOnline);
  const user = `${c.userDone} ${c.userLive}`.trim();
  let n = 0;

  return (
    <div className="caption" data-ended={c.turnEnd || undefined}>
      {user ? (
        <p className="caption-user" data-final={!c.userLive || undefined}>
          <b>{who}:</b> {c.userDone}
          {c.userLive && <i> {c.userLive}</i>}
        </p>
      ) : (
        !c.text && (
          <p className="caption-hint">
            {online ? "Sag einfach, was du brauchst." : "Tippe auf „Gespräch starten“."}
          </p>
        )
      )}
      {c.text && (
        <p className="caption-bot" aria-hidden="true" data-cut={c.interrupted || undefined}>
          <span className="caption-inner">
            {c.text.split(/(\s+)/).map((part, i) =>
              /^\s+$/.test(part) ? part : (
                <span key={i} data-spoken={n++ < c.spoken || undefined}>
                  {part}
                </span>
              ),
            )}
          </span>
        </p>
      )}
    </div>
  );
});
