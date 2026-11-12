"use client";

import { useEffect, useRef, useState, type MouseEvent } from "react";
import { bueroSteuern } from "@/lib/bueroApi";
import { KernFehler } from "@/lib/kernApi";
import { AKTION_GESENDET, AKTION_KNOPF, brauchtBestaetigung, schaltFehlerText, type Aktion, type BueroGeraet } from "./bueroKarteModell";
import { bueroNachSchalten } from "./useBueroStand";

/** So lange gilt der erste Tipp bei "Ausschalten" einer Steckdose, danach muss man neu anfangen. */
const FRAGE_MS = 4000;

type Meldung = { art: "ok" | "fehler"; text: string };

/**
 * Knöpfe zum Schalten in der Detailkarte (nur Admins, der Kern liefert die Liste "aktionen" nur ihnen): Ein Fernseher im Standby bietet
 * "Einschalten", ein laufender "Ausschalten", Lauter, Leiser, Stumm und Ton an; eine Steckdose das Gegenteil ihres Zustands. Ein Tipp
 * genügt, nur "Ausschalten" einer Steckdose verlangt einen zweiten (sie kappt, was daran hängt). Während ein Befehl läuft, sind alle
 * Knöpfe aus, und ein Doppelklick zählt einmal. Das Ergebnis steht in einer Meldung unter den Knöpfen (ruhiger Satz des Kerns, nie
 * Gerätetexte). Danach holt die Karte den Stand bald neu (useBueroStand.bueroNachSchalten).
 */
export function BueroSchalter({ geraet }: { geraet: BueroGeraet }) {
  const aktionen = geraet.aktionen ?? [];
  const [laeuft, setLaeuft] = useState<Aktion | null>(null);
  const [frage, setFrage] = useState<Aktion | null>(null);
  const [meldung, setMeldung] = useState<Meldung | null>(null);
  const frist = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fristLoeschen = () => {
    if (frist.current !== null) clearTimeout(frist.current);
    frist.current = null;
  };
  useEffect(() => fristLoeschen, []);

  if (aktionen.length === 0) return null;

  const klick = async (aktion: Aktion, e: MouseEvent<HTMLButtonElement>) => {
    if (e.detail > 1 || laeuft !== null) return; // ein Doppelklick zählt einmal
    if (brauchtBestaetigung(geraet, aktion) && frage !== aktion) {
      setFrage(aktion);
      setMeldung(null);
      fristLoeschen();
      frist.current = setTimeout(() => setFrage(null), FRAGE_MS);
      return;
    }
    fristLoeschen();
    setFrage(null);
    setLaeuft(aktion);
    setMeldung(null);
    try {
      await bueroSteuern(geraet.id, aktion);
      setMeldung({ art: "ok", text: AKTION_GESENDET[aktion] });
      bueroNachSchalten(aktion);
    } catch (fehler) {
      const status = fehler instanceof KernFehler ? fehler.status : 0;
      const text = fehler instanceof KernFehler ? fehler.message : "";
      setMeldung({ art: "fehler", text: schaltFehlerText(status, text) });
    } finally {
      setLaeuft(null);
    }
  };

  return (
    <div className="bk-schalter" role="group" aria-label={`${geraet.name} schalten`}>
      <span className="bk-etikett">Schalten</span>
      <div className="bk-schalter-reihe">
        {aktionen.map((a) => (
          <button
            key={a}
            type="button"
            className="bk-schalt"
            data-aktion={a}
            data-frage={frage === a ? "ja" : undefined}
            disabled={laeuft !== null}
            aria-busy={laeuft === a}
            onClick={(e) => void klick(a, e)}
          >
            {laeuft === a ? "Wird gesendet …" : frage === a ? "Sicher? Noch einmal tippen" : AKTION_KNOPF[a]}
          </button>
        ))}
      </div>
      <p className="bk-schalt-meldung" role="status" aria-live="polite" data-art={meldung?.art}>
        {meldung?.text ?? ""}
      </p>
    </div>
  );
}
