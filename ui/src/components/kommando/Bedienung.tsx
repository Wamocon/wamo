"use client";

import { memo, useEffect, useRef, useState, type FormEvent } from "react";
import { useLiveBildErlaubt } from "@/components/BildschirmAnsicht";
import { useSelect } from "@/lib/store";
import { TEXT_STILL_BEREIT, TEXT_VERBINDET, ZeilenHalter } from "@/lib/stillStart";
import { eingabeHinweis, MAX_TEXT } from "@/lib/textEingabe";
import { useStillStartErlaubt } from "@/lib/useStillStart";
import { istOnline, laeuftWerkzeug, wamo } from "@/lib/wamo";
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
const Eingabepille = memo(function Eingabepille({
  onMikro,
  senden,
  stillStarten,
  eingabeMelden,
}: {
  onMikro: () => void;
  senden?: TextSenden;
  stillStarten?: () => boolean;
  eingabeMelden?: () => void;
}) {
  const online = useSelect(wamo, istOnline);
  const micOn = useSelect(wamo, (s) => s.micOn);
  const textMoeglich = useSelect(wamo, (s) => s.textMoeglich);
  // Start beim Tippen (lib/stillStart.ts): nur mit eingeschaltetem Schalter ist das Feld auch ohne Sitzung aktiv
  const { erlaubt: stillErlaubt } = useStillStartErlaubt();
  const presence = useSelect(wamo, (s) => s.presence);
  const still = useSelect(wamo, (s) => s.stillStart.still);
  const perTippen = useSelect(wamo, (s) => s.stillStart.perTippen);
  const [halter] = useState(() => new ZeilenHalter());
  const [wartet, setWartet] = useState(false);
  const stillOffen = Boolean(stillStarten) && stillErlaubt && (presence === "offline" || (presence === "connecting" && perTippen));
  const hinweis = useSelect(wamo, (s) =>
    eingabeHinweis({
      entscheidungen: s.approvals.length + s.auftraege.length,
      werkzeugLaeuft: istOnline(s) && laeuftWerkzeug(s),
      kernOhneText: Boolean(senden) && istOnline(s) && !s.textMoeglich,
      stillWartet: wartet,
      stillBereit: istOnline(s) && s.stillStart.still,
    }),
  );
  const [text, setText] = useState("");
  const feld = useRef<HTMLInputElement>(null);
  const bereit = Boolean(senden) && online && textMoeglich;
  const aktiv = bereit || (Boolean(senden) && stillOffen);
  const platzhalter = bereit
    ? "Frag WAMO etwas …"
    : stillOffen
      ? presence === "connecting"
        ? "Verbinde …"
        : "Tippe, um WAMO still zu starten"
      : online && senden
        ? "Kein Text möglich, sprich mit WAMO"
        : "Sprich einfach mit WAMO";

  // Die erste Zeile wartet, bis die Sitzung steht, und geht genau einmal hinaus (ZeilenHalter). Sie bleibt bis dahin im Feld:
  // Scheitert die Verbindung, ist nichts verloren und nichts wird nachträglich gesendet.
  useEffect(() => {
    if (!wartet) return;
    if (presence === "offline") {
      halter.verwerfen();
      setWartet(false);
    } else if (online && textMoeglich) {
      const zeile = halter.nehmen();
      setWartet(false);
      if (zeile !== null && senden?.(zeile)) {
        setText("");
        feld.current?.focus();
      }
    }
  }, [wartet, presence, online, textMoeglich, halter, senden]);

  const abschicken = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !senden || !aktiv || wartet) return;
    if (!bereit) {
      // noch keine Sitzung: starten (falls das Tippen es nicht schon getan hat) und die Zeile halten
      if (presence === "offline" && !stillStarten?.()) return;
      if (halter.halten(t)) setWartet(true);
      return;
    }
    if (senden(t)) {
      setText("");
      feld.current?.focus(); // der Fokus bleibt im Feld, auch wenn der Senden-Knopf sich sperrt
    }
  };

  // Der erste Tastendruck startet still eine Sitzung (nur mit Schalter; alles Weitere prüft useWamo.stillStarten)
  const aendern = (wert: string) => {
    setText(wert);
    eingabeMelden?.();
    if (wert.trim() && presence === "offline" && stillOffen) stillStarten?.();
  };

  return (
    <div className="kz-eingabe">
      <form className="kz-pille" onSubmit={abschicken} aria-label="Eingabe">
        <button
          type="button"
          className="kz-pille-mikro"
          aria-pressed={still ? undefined : !micOn}
          aria-label={still ? "Mikrofon einschalten und sprechen" : "Mikrofon stumm schalten"}
          aria-keyshortcuts="Control+M"
          title={still ? "Mikrofon einschalten und sprechen (Strg+M)" : "Mikrofon stumm schalten (Strg+M)"}
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
          ref={feld}
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
          aria-busy={wartet || undefined}
          readOnly={wartet}
          disabled={!aktiv}
          onChange={(e) => aendern(e.target.value)}
        />
        <button type="submit" className="kz-pille-senden" aria-label="Senden" disabled={!aktiv || !text.trim() || wartet}>
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M4 10h11M10.5 5l5 5-5 5" />
          </svg>
        </button>
      </form>
      {/* Die Zustände der stillen Sitzung kündigt schon die Statusansage (Ansage) an: hier nur sichtbar und über aria-describedby, nicht doppelt */}
      <p
        id="kz-pille-hinweis"
        className="kz-pille-hinweis"
        role={hinweis === TEXT_VERBINDET || hinweis === TEXT_STILL_BEREIT ? undefined : "status"}
        hidden={!hinweis}
      >
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
  verlaufOffen?: boolean;
  /** ohne diese Funktion gibt es den Knopf "Verlauf" nicht (Laborkonten, lib/laborPlatz.ts) */
  onVerlauf?: () => void;
  senden?: TextSenden;
}) {
  const online = useSelect(wamo, istOnline);
  const textMoeglich = useSelect(wamo, (s) => s.textMoeglich);
  const { gewaehlt } = useEffektiveDarstellung();
  const { zeigeCanvas } = useRahmen();
  const liveBild = useLiveBildErlaubt();
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
      {onVerlauf && (
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
      )}
      <li>
        <button type="button" className="kz-chip" onClick={zeigeCanvas}>
          Canvas
        </button>
      </li>
      {liveBild && (
        <li>
          <button type="button" className="kz-chip" onClick={zeigeCanvas}>
            Live-Bild
          </button>
        </li>
      )}
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

/** Untere Hälfte der Bühne: Eingabepille und Schnellaktionen. Fokus und Zentrale blenden sie aus. Die Stimmlinie steht im Block Gespräch (Gespraech.tsx). */
export const Bedienung = memo(function Bedienung({
  onMikro,
  senden,
  stillStarten,
  eingabeMelden,
  verlaufOffen,
  onVerlauf,
}: {
  onMikro: () => void;
  senden?: TextSenden;
  /** Start beim Tippen (nur mit Geräteschalter, lib/stillStart.ts); ohne diese Funktionen bleibt das Feld ohne Sitzung gesperrt */
  stillStarten?: () => boolean;
  eingabeMelden?: () => void;
  verlaufOffen?: boolean;
  onVerlauf?: () => void;
}) {
  return (
    <div className="kz-bedienung" data-p="kern">
      <Eingabepille onMikro={onMikro} senden={senden} stillStarten={stillStarten} eingabeMelden={eingabeMelden} />
      <Schnellaktionen verlaufOffen={verlaufOffen} onVerlauf={onVerlauf} senden={senden} />
    </div>
  );
});
