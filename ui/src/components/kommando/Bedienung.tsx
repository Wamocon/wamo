"use client";

import { memo, useState, type FormEvent } from "react";
import { VoiceLine } from "@/components/VoiceLine";
import { useSelect } from "@/lib/store";
import { historyRef, istOnline, wamo } from "@/lib/wamo";
import { useEffektiveDarstellung } from "./ansicht";
import { darstellungSetzen, type Darstellung } from "./darstellung";
import { useRahmen } from "./rahmenKontext";

/** Text an WAMO schicken; true, wenn die Nachricht gesendet wurde. Fehlt die Funktion, bleibt die Eingabe gesperrt. */
export type TextSenden = (text: string) => boolean;

const MAX_TEXT = 500;

/**
 * Eingabepille: Mikrofon (stumm schalten, wie bisher mit Strg+M), Texteingabe und Senden.
 * Die Texteingabe wird erst aktiv, wenn die Oberfläche eine Sendefunktion bekommt (Prop senden) und
 * ein Gespräch läuft; bis dahin bleibt das Feld gesperrt und der Platzhalter verweist auf die Sprache.
 */
const Eingabepille = memo(function Eingabepille({ onMikro, senden }: { onMikro: () => void; senden?: TextSenden }) {
  const online = useSelect(wamo, istOnline);
  const micOn = useSelect(wamo, (s) => s.micOn);
  const [text, setText] = useState("");
  const aktiv = Boolean(senden) && online;

  const abschicken = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !senden || !online) return;
    if (senden(t)) setText("");
  };

  return (
    <form className="kz-pille" onSubmit={abschicken} aria-label="Eingabe">
      <button
        type="button"
        className="kz-pille-mikro"
        aria-pressed={!micOn}
        aria-label="Mikrofon stumm schalten"
        aria-keyshortcuts="Control+M"
        title="Mikrofon stumm schalten (Strg+M)"
        disabled={!online}
        onClick={onMikro}
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <rect x="7" y="2.5" width="6" height="10" rx="3" />
          <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5" />
          {!micOn && <path className="mic-off" d="M3 3l14 14" />}
        </svg>
      </button>
      <input
        type="text"
        className="kz-pille-feld"
        aria-label="Nachricht an WAMO"
        placeholder={aktiv ? "Frag WAMO etwas …" : "Sprich einfach mit WAMO"}
        value={text}
        maxLength={MAX_TEXT}
        autoComplete="off"
        disabled={!aktiv}
        onChange={(e) => setText(e.target.value)}
      />
      <button type="submit" className="kz-pille-senden" aria-label="Senden" disabled={!aktiv || !text.trim()}>
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M4 10h11M10.5 5l5 5-5 5" />
        </svg>
      </button>
    </form>
  );
});

/** Schnellaktionen. Befehle an WAMO erscheinen erst mit Sendefunktion; die übrigen wirken lokal. */
const BEFEHLE = ["Überblick", "Tagesplan", "Offene Aufträge"];

const Schnellaktionen = memo(function Schnellaktionen({
  verlaufOffen,
  onVerlauf,
  senden,
}: {
  verlaufOffen: boolean;
  onVerlauf: () => void;
  senden?: TextSenden;
}) {
  const online = useSelect(wamo, istOnline);
  const { gewaehlt } = useEffektiveDarstellung();
  const { zeigeCanvas } = useRahmen();
  const umschalten = (d: Darstellung) => {
    darstellungSetzen(gewaehlt === d ? "normal" : d);
    // Fokus blendet diese Chips aus: der Tastaturfokus geht auf den aktiven Schalter der Darstellungswahl
    if (d === "fokus") {
      requestAnimationFrame(() => document.querySelector<HTMLElement>('.kz-wahl [aria-checked="true"]')?.focus());
    }
  };

  return (
    <ul className="kz-chips" aria-label="Schnellaktionen">
      {senden &&
        BEFEHLE.map((b) => (
          <li key={b}>
            <button type="button" className="kz-chip" disabled={!online} onClick={() => senden(b)}>
              {b}
            </button>
          </li>
        ))}
      <li>
        <button
          type="button"
          className="kz-chip"
          aria-expanded={verlaufOffen}
          aria-controls="verlauf"
          onClick={onVerlauf}
        >
          Verlauf
        </button>
      </li>
      <li>
        <button type="button" className="kz-chip" onClick={zeigeCanvas}>
          Canvas
        </button>
      </li>
      <li>
        <button type="button" className="kz-chip" aria-pressed={gewaehlt === "fokus"} onClick={() => umschalten("fokus")}>
          Fokus
        </button>
      </li>
      <li>
        <button type="button" className="kz-chip" aria-pressed={gewaehlt === "nacht"} onClick={() => umschalten("nacht")}>
          Nacht
        </button>
      </li>
    </ul>
  );
});

/** Untere Hälfte der Bühne: Eingabepille, Schnellaktionen und Stimmlinie. Fokus und Zentrale blenden sie aus. */
export const Bedienung = memo(function Bedienung({
  onMikro,
  senden,
  verlaufOffen,
  onVerlauf,
}: {
  onMikro: () => void;
  senden?: TextSenden;
  verlaufOffen: boolean;
  onVerlauf: () => void;
}) {
  return (
    <div className="kz-bedienung" data-p="kern">
      <Eingabepille onMikro={onMikro} senden={senden} />
      <Schnellaktionen verlaufOffen={verlaufOffen} onVerlauf={onVerlauf} senden={senden} />
      <div className="voice">
        <VoiceLine history={historyRef} />
        <p className="legend" aria-hidden="true">
          <span className="legend-user">Du</span>
          <span className="legend-bot">WAMO</span>
        </p>
      </div>
    </div>
  );
});
