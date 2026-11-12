"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { bueroSteuern } from "@/lib/bueroApi";
import { KernFehler } from "@/lib/kernApi";
import { AKTION_GESENDET, AKTION_KNOPF, brauchtBestaetigung, schaltFehlerText, type Aktion, type BueroGeraet } from "./bueroKarteModell";
import { bueroEpoche, bueroNachSchalten } from "./useBueroStand";

/** So lange gilt der erste Tipp bei "Ausschalten" einer Steckdose, danach muss man neu anfangen. */
const FRAGE_MS = 6000;
/** So kurz nach der Rückfrage zählt ein weiterer Tipp nicht (Doppelklick, Tastatur-Wiederholung bei gehaltener Taste). */
const SPERRE_MS = 400;
/** So lange nach dem letzten Tipp bringt die Karte den Fokus zurück, wenn der Knopf verschwunden ist (der Zustand wechselte). */
const FOKUS_MS = 15_000;

/** Aktuelle Zeit in Millisekunden (als Funktion, damit sie in Ereignisbehandlern steht und nicht beim Rendern gelesen wird). */
const jetzt = (): number => Date.now();

/** Befehle, die gerade unterwegs sind (Gerät und Aktion): gilt über das Wechseln und Schließen der Karte hinaus, damit nichts doppelt geht. */
const unterwegs = new Set<string>();

type Meldung = { art: "ok" | "fehler" | "frage"; text: string };

/**
 * Knöpfe zum Schalten in der Detailkarte (nur Admins, der Kern liefert die Liste "aktionen" nur ihnen): Ein Fernseher im Standby bietet
 * "Einschalten", ein laufender "Ausschalten", Lauter, Leiser, Stumm und Ton an; eine Steckdose das Gegenteil ihres Zustands. Ein Tipp
 * genügt, nur "Ausschalten" einer Steckdose verlangt einen zweiten (sie kappt, was daran hängt): Die Rückfrage steht als Satz in der
 * Statusmeldung, gilt 6 Sekunden, ein zu schneller zweiter Tipp zählt nicht, Escape bricht sie ab. Während ein Befehl läuft, sind die Knöpfe
 * aria-disabled (nicht disabled: der Fokus bleibt, wo er ist). Das Ergebnis steht in der Meldung (ruhiger Satz des Kerns, nie Gerätetexte).
 * Danach holt die Karte den Stand bald neu (useBueroStand.bueroNachSchalten); verschwindet dabei der Knopf, an dem der Fokus lag, geht er an
 * denselben oder den ersten Knopf.
 */
export function BueroSchalter({ geraet }: { geraet: BueroGeraet }) {
  const aktionen = geraet.aktionen ?? [];
  const [laeuft, setLaeuft] = useState<Aktion | null>(null);
  const [frage, setFrage] = useState<Aktion | null>(null);
  const [meldung, setMeldung] = useState<Meldung | null>(null);
  const frist = useRef<ReturnType<typeof setTimeout> | null>(null);
  const frageSeit = useRef(0);
  const gruppe = useRef<HTMLDivElement>(null);
  const bedient = useRef<{ zeit: number; aktion: Aktion } | null>(null);

  const fristLoeschen = () => {
    if (frist.current !== null) clearTimeout(frist.current);
    frist.current = null;
  };
  useEffect(() => fristLoeschen, []);

  // Wechselt der Zustand und mit ihm die Knopfliste, kann der Knopf mit dem Fokus verschwinden: dann den Fokus zurückholen.
  const liste = aktionen.join(",");
  useEffect(() => {
    const el = gruppe.current;
    const zuletzt = bedient.current;
    if (!el || !zuletzt || Date.now() - zuletzt.zeit > FOKUS_MS) return;
    const aktiv = document.activeElement;
    if (aktiv && aktiv !== document.body) return; // der Fokus ist woanders (bewusst) oder noch da
    (el.querySelector<HTMLButtonElement>(`[data-aktion="${zuletzt.aktion}"]`) ?? el.querySelector<HTMLButtonElement>("button"))?.focus();
  }, [liste]);

  if (aktionen.length === 0) return null;

  const abbrechen = (text: string) => {
    fristLoeschen();
    setFrage(null);
    setMeldung({ art: "ok", text });
  };

  const klick = async (aktion: Aktion, e: MouseEvent<HTMLButtonElement>) => {
    const schluessel = `${geraet.id}:${aktion}`;
    if (e.detail > 1 || laeuft !== null || unterwegs.has(schluessel)) return; // ein Doppelklick zählt einmal
    bedient.current = { zeit: jetzt(), aktion };
    if (brauchtBestaetigung(geraet, aktion)) {
      if (frage !== aktion) {
        frageSeit.current = jetzt();
        setFrage(aktion);
        setMeldung({
          art: "frage",
          text: `${geraet.name} wirklich ausschalten? Tippe innerhalb von ${FRAGE_MS / 1000} Sekunden noch einmal auf „${AKTION_KNOPF[aktion]}“.`,
        });
        fristLoeschen();
        frist.current = setTimeout(() => {
          setFrage(null);
          setMeldung((alt) => (alt?.art === "frage" ? { art: "ok", text: "Die Rückfrage ist abgelaufen, es ist nichts passiert." } : alt));
        }, FRAGE_MS);
        return;
      }
      if (jetzt() - frageSeit.current < SPERRE_MS) return; // zu schnell: kein bewusster zweiter Tipp
    }
    fristLoeschen();
    setFrage(null);
    setLaeuft(aktion);
    setMeldung(null);
    unterwegs.add(schluessel);
    const epoche = bueroEpoche();
    try {
      await bueroSteuern(geraet.id, aktion);
      setMeldung({ art: "ok", text: AKTION_GESENDET[aktion] });
      bueroNachSchalten(aktion, epoche);
    } catch (fehler) {
      const status = fehler instanceof KernFehler ? fehler.status : 0;
      const text = fehler instanceof KernFehler ? fehler.message : "";
      setMeldung({ art: "fehler", text: schaltFehlerText(status, text) });
      if (status === 0) bueroNachSchalten(aktion, epoche); // unklar, ob der Befehl ankam: der Stand zeigt es bald
    } finally {
      unterwegs.delete(schluessel);
      setLaeuft(null);
    }
  };

  const taste = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape" && frage !== null) {
      e.stopPropagation(); // nur die Rückfrage abbrechen, die Karte bleibt offen
      abbrechen("Abgebrochen, es ist nichts passiert.");
    }
  };

  return (
    <div ref={gruppe} className="bk-schalter" role="group" aria-label={`${geraet.name} schalten`} aria-live="off" onKeyDown={taste}>
      <span className="bk-etikett">Schalten</span>
      <div className="bk-schalter-reihe">
        {aktionen.map((a) => (
          <button
            key={a}
            type="button"
            className="bk-schalt"
            data-aktion={a}
            data-frage={frage === a ? "ja" : undefined}
            aria-disabled={laeuft !== null ? true : undefined}
            aria-busy={laeuft === a ? true : undefined}
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
