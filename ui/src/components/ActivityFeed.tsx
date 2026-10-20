"use client";

import { memo } from "react";
import type { Activity, ActivityStatus } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";

/**
 * Was WAMO gerade tut: Zeitleiste der Werkzeugaufrufe (vom Voice-Core gemeldet).
 * Statusform zusätzlich zur Farbe: läuft (grauer Kreis, roter Bogen), erledigt (Häkchen),
 * nicht ausgeführt (Kreis mit Schrägstrich), wartet auf Freigabe (Kreis mit Pause), Fehler (rotes Quadrat mit "!").
 * Die Dauer steht erst am Ende da; während des Laufs zählt kein Timer (kein Neurendern).
 * Schritte früherer Gesprächszüge sind eingeklappt.
 */

export const LABEL: Record<string, string> = {
  webseite_oeffnen: "öffnet Webseite",
  programm_oeffnen: "startet Programm",
  oeffnen_mit: "öffnet",
  fenster_auflisten: "schaut, was offen ist",
  fenster_fokussieren: "holt Fenster nach vorne",
  tastatur_tippen: "tippt",
  tasten_druecken: "drückt",
  maus_scrollen: "scrollt",
  bildschirm_ansehen: "schaut auf den Bildschirm",
  auf_bildschirm_klicken: "klickt",
  powershell_lesen: "liest PC-Infos",
  websuche: "sucht im Web",
  canvas_zeigen: "legt aufs Canvas",
  merken: "merkt sich etwas",
  aktuelle_zeit: "schaut auf die Uhr",
  entwicklung_vorbereiten: "plant Entwicklung",
  entwicklung_starten: "startet Claude Code",
  entwicklung_status: "prüft Entwicklung",
  dark_mode: "schaltet Dark Mode",
  system_info: "liest Systeminfos",
};

const STATUS_TEXT: Record<ActivityStatus, string> = {
  laeuft: "läuft",
  fertig: "erledigt",
  abgelehnt: "nicht ausgeführt",
  fehler: "Fehler",
  wartet: "wartet auf Freigabe",
};

const MAX_AKTUELL = 6;

function dauer(a: Activity) {
  if (a.startedAt === undefined || a.endedAt === undefined) return "";
  return `${((a.endedAt - a.startedAt) / 1000).toFixed(1).replace(".", ",")} s`;
}

function StatusMark({ status }: { status: ActivityStatus }) {
  switch (status) {
    case "laeuft":
      return (
        <svg className="mark mark-run" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6" />
          <path className="arc" d="M8 2a6 6 0 0 1 6 6" />
        </svg>
      );
    case "fertig":
      return (
        <svg className="mark mark-done" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M3 8.5l3.2 3.2L13 4.8" />
        </svg>
      );
    case "abgelehnt":
      return (
        <svg className="mark mark-skip" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6" />
          <path d="M4 12L12 4" />
        </svg>
      );
    case "wartet":
      return (
        <svg className="mark mark-wait" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6" />
          <path d="M6.2 5.5v5M9.8 5.5v5" />
        </svg>
      );
    case "fehler":
      return (
        <svg className="mark mark-error" viewBox="0 0 16 16" aria-hidden="true">
          <rect x="1.5" y="1.5" width="13" height="13" rx="2" />
          <path d="M8 4.5v4.6M8 11.2v.4" />
        </svg>
      );
  }
}

function Zeile({ a }: { a: Activity }) {
  const teilfehler = a.status === "abgelehnt" || a.status === "fehler" || a.status === "wartet";
  return (
    <li data-status={a.status}>
      <StatusMark status={a.status} />
      <span className="tl-what">
        {LABEL[a.name] ?? a.name}
        {a.detail && (
          <span className="tl-detail">
            {" "}
            {teilfehler ? `${STATUS_TEXT[a.status]}: ${a.detail}` : a.detail}
          </span>
        )}
      </span>
      <time className="num">{dauer(a)}</time>
      <span className="sr-only">{STATUS_TEXT[a.status]}</span>
    </li>
  );
}

/** "wartet" nur, solange noch eine PC-Freigabe offen ist; sonst ist die Aktion nicht ausgeführt worden. */
function mitFreigabeStand(a: Activity, pcOffen: boolean): Activity {
  return a.status === "wartet" && !pcOffen ? { ...a, status: "abgelehnt", detail: "Freigabe nicht mehr offen" } : a;
}

export const ActivityTimeline = memo(function ActivityTimeline() {
  const roh = useSelect(wamo, (s) => s.activities);
  const turn = useSelect(wamo, (s) => s.turn);
  const pcOffen = useSelect(wamo, (s) => s.approvals.some((a) => a.job === "pc"));
  const items = roh.map((a) => mitFreigabeStand(a, pcOffen));
  if (!items.length) return null;
  // aktueller Zug (und alles, was noch läuft) oben, frühere Schritte eingeklappt
  const aktuell = items.filter((a) => a.turn === turn || a.status === "laeuft").slice(0, MAX_AKTUELL);
  const frueher = items.filter((a) => !aktuell.includes(a));

  return (
    <section className="timeline" aria-labelledby="tl-h">
      <h2 id="tl-h" className="eyebrow">
        Werkzeuge
      </h2>
      {aktuell.length > 0 && (
        <ol>
          {aktuell.map((a) => (
            <Zeile key={a.id} a={a} />
          ))}
        </ol>
      )}
      {frueher.length > 0 && (
        <details className="tl-older">
          <summary>{frueher.length === 1 ? "1 früherer Schritt" : `${frueher.length} frühere Schritte`}</summary>
          <ol>
            {frueher.map((a) => (
              <Zeile key={a.id} a={a} />
            ))}
          </ol>
        </details>
      )}
    </section>
  );
});
