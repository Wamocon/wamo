"use client";

import { memo, useEffect, useRef, useState } from "react";
import { ANTWORT_MS } from "@/lib/modellzugangHilfe";
import { gewaehlterModus, MODI, modusErklaerung, nachbar, wirksamText } from "@/lib/modellwahl";
import type { ModellModus } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import type { ModellzugangAnfrage } from "@/lib/useWamo";
import { wamo } from "@/lib/wamo";
import styles from "./ModellZugang.module.css";

/**
 * Umschalter "Modelle: Automatisch | Nur Hausmodelle" in der Karte Modellzugang. Die Wahl gehört der Person und
 * gilt ab der nächsten Sitzung (der Satz darunter sagt es und nennt Abweichungen zur laufenden Sitzung). Ein Zwang
 * des Admins sperrt den Umschalter. Radiogruppe nach WAI-ARIA: ein Tabstopp, Pfeiltasten wählen, Home und Ende
 * springen. Der Zustand kommt allein vom Kern zurück (modellzugang.modus), nicht aus dem Klick. Die Wahl geht mit
 * Sitzung über den Datenkanal, sonst über die HTTP-Schnittstelle des Kerns (senden aus lib/modellzugangApi.ts).
 */
export const ModellWahl = memo(function ModellWahl({ senden }: { senden: (anfrage: ModellzugangAnfrage) => void }) {
  const zugang = useSelect(wamo, (s) => s.modellzugang);
  const betrieb = useSelect(wamo, (s) => s.betrieb);
  const [wartet, setWartet] = useState<ModellModus | null>(null);
  const [fehler, setFehler] = useState("");
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null);
  const knoepfe = useRef<(HTMLButtonElement | null)[]>([]);

  // Antwort des Kerns auf die eigene Wahl beendet das Warten; ein Fehler steht danach sichtbar da. Abgeleiteter Zustand
  // während des Renderns statt in einem Effekt: so erscheint nach dem Neumounten kein alter Fehler wieder.
  const [gesehen, setGesehen] = useState(zugang);
  if (zugang !== gesehen) {
    setGesehen(zugang);
    if (zugang && zugang.aktion === "modus" && wartet !== null) {
      setWartet(null);
      setFehler(zugang.fehler ?? "");
    }
  }

  useEffect(() => {
    if (wartet === null && uhr.current) {
      clearTimeout(uhr.current);
      uhr.current = null;
    }
  }, [wartet]);

  useEffect(() => () => {
    if (uhr.current) clearTimeout(uhr.current);
  }, []);

  const gesperrt = zugang?.festgelegt === true;
  const aktiv = gewaehlterModus(zugang);
  const bedienbar = !gesperrt && wartet === null;

  const waehle = (modus: ModellModus) => {
    if (!bedienbar || modus === aktiv) return;
    setFehler("");
    setWartet(modus);
    if (uhr.current) clearTimeout(uhr.current);
    uhr.current = setTimeout(() => {
      setWartet(null);
      setFehler("Keine Antwort vom Kern. Bitte später noch einmal versuchen.");
    }, ANTWORT_MS);
    senden({ aktion: "modus", modus });
  };

  const taste = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const ziel = nachbar(index, e.key, MODI.length);
    if (ziel === null) return;
    e.preventDefault();
    knoepfe.current[ziel]?.focus();
    waehle(MODI[ziel].id);
  };

  const angezeigt = wartet ?? aktiv;
  return (
    <div className={styles.wahl}>
      <p id="modellwahl-label" className={styles.wahlLabel}>
        Modelle
      </p>
      <div className={`betriebsart-wahl ${styles.wahlGruppe}`} role="radiogroup" aria-labelledby="modellwahl-label" aria-describedby="modellwahl-text">
        {MODI.map((m, i) => (
          <button
            key={m.id}
            ref={(el) => {
              knoepfe.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={m.id === angezeigt}
            data-an={m.id === angezeigt || undefined}
            tabIndex={m.id === angezeigt ? 0 : -1}
            disabled={gesperrt}
            onClick={() => waehle(m.id)}
            onKeyDown={(e) => taste(e, i)}
          >
            {m.name}
          </button>
        ))}
      </div>
      <p id="modellwahl-text" className={styles.klein}>
        {modusErklaerung(angezeigt, Boolean(zugang?.hinterlegt) || zugang?.quelle === "zuordnung")}
      </p>
      <p className={styles.klein} role="status">
        {wartet ? "Wird gespeichert …" : wirksamText(zugang, betrieb)}
      </p>
      {fehler && (
        <p className={styles.meldung} role="alert" data-fehler>
          {fehler}
        </p>
      )}
    </div>
  );
});
