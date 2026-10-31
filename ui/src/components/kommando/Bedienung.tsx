"use client";

import { memo, useState, type FormEvent } from "react";
import { VoiceLine } from "@/components/VoiceLine";
import { useSelect } from "@/lib/store";
import { eingabeHinweis, MAX_TEXT } from "@/lib/textEingabe";
import { historyRef, istOnline, laeuftWerkzeug, wamo } from "@/lib/wamo";
import { useEffektiveDarstellung } from "./ansicht";
import { darstellungSetzen, type Darstellung } from "./darstellung";
import { useRahmen } from "./rahmenKontext";

/** Text an WAMO schicken; true, wenn die Nachricht gesendet wurde. Fehlt die Funktion, bleibt die Eingabe gesperrt. */
export type TextSenden = (text: string) => boolean;

/**
 * Eingabepille: Mikrofon (stumm schalten, wie bisher mit Strg+M), Texteingabe und Senden.
 * Die Texteingabe ist nur aktiv, wenn die Oberfläche eine Sendefunktion bekommt (Prop senden), ein Gespräch läuft und
 * der Kern Text annimmt; sonst bleibt das Feld gesperrt und der Platzhalter sagt ehrlich warum. Ein getipptes Ja
 * ersetzt keine Freigabe: dazu steht unter dem Feld ein Hinweis, solange eine Karte wartet.
 */
const Eingabepille = memo(function Eingabepille({ onMikro, senden }: { onMikro: () => void; senden?: TextSenden }) {
  const online = useSelect(wamo, istOnline);
  const micOn = useSelect(wamo, (s) => s.micOn);
  const textMoeglich = useSelect(wamo, (s) => s.textMoeglich);
  const hinweis = useSelect(wamo, (s) =>
    eingabeHinweis({
      entscheidungen: s.approvals.length + s.auftraege.length,
      werkzeugLaeuft: istOnline(s) && laeuftWerkzeug(s),
    }),
  );
  const [text, setText] = useState("");
  const aktiv = Boolean(senden) && online && textMoeglich;
  const platzhalter = aktiv
    ? "Frag WAMO etwas …"
    : online && senden
      ? "Dieser Kern nimmt keinen Text an"
      : "Sprich einfach mit WAMO";

  const abschicken = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !senden || !aktiv) return;
    if (senden(t)) setText("");
  };

  return (
    <div className="kz-eingabe">
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
          placeholder={platzhalter}
          value={text}
          maxLength={MAX_TEXT}
          autoComplete="off"
          autoCorrect="off"
          enterKeyHint="send"
          aria-describedby={hinweis ? "kz-pille-hinweis" : undefined}
          disabled={!aktiv}
          onChange={(e) => setText(e.target.value)}
        />
        <button type="submit" className="kz-pille-senden" aria-label="Senden" disabled={!aktiv || !text.trim()}>
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M4 10h11M10.5 5l5 5-5 5" />
          </svg>
        </button>
      </form>
      <p id="kz-pille-hinweis" className="kz-pille-hinweis" hidden={!hinweis}>
        {hinweis}
      </p>
    </div>
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
  const textMoeglich = useSelect(wamo, (s) => s.textMoeglich);
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
            <button type="button" className="kz-chip" disabled={!online || !textMoeglich} onClick={() => senden(b)}>
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
