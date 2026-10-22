"use client";

import { memo, useEffect, useRef, useState } from "react";
import { gewaehlterModus, MODI, modusErklaerung, nachbar, wirksamText } from "@/lib/modellwahl";
import type { ModellModus } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import type { ModellzugangAnfrage } from "@/lib/useWamo";
import { istOnline, wamo } from "@/lib/wamo";
import styles from "./ModellZugang.module.css";

const ANTWORT_MS = 10_000;

/**
 * Umschalter "Modelle: Automatisch | Nur Hausmodelle" in der Karte Modellzugang. Die Wahl gehört der Person und
 * gilt ab der nächsten Sitzung (der Satz darunter sagt es und nennt Abweichungen zur laufenden Sitzung). Ein Zwang
 * des Admins sperrt den Umschalter. Radiogruppe nach WAI-ARIA: ein Tabstopp, Pfeiltasten wählen, Home und Ende
 * springen. Der Zustand kommt allein vom Kern zurück (modellzugang.modus), nicht aus dem Klick.
 */
export const ModellWahl = memo(function ModellWahl({
  onAnfrage,
}: {
  onAnfrage?: (anfrage: ModellzugangAnfrage) => boolean;
}) {
  const online = useSelect(wamo, istOnline);
  const zugang = useSelect(wamo, (s) => s.modellzugang);
  const betrieb = useSelect(wamo, (s) => s.betrieb);
  const [wartet, setWartet] = useState<ModellModus | null>(null);
  const [fehler, setFehler] = useState("");
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null);
  const knoepfe = useRef<(HTMLButtonElement | null)[]>([]);

  // Antwort des Kerns beendet das Warten; ein Fehler steht danach sichtbar da
  useEffect(() => {
    if (!zugang || zugang.aktion !== "modus") return;
    if (uhr.current) clearTimeout(uhr.current);
    setWartet(null);
    setFehler(zugang.fehler ?? "");
  }, [zugang]);

  useEffect(() => () => {
    if (uhr.current) clearTimeout(uhr.current);
  }, []);

  if (!onAnfrage) return null;

  const gesperrt = zugang?.festgelegt === true;
  const aktiv = gewaehlterModus(zugang);
  const bedienbar = online && !gesperrt && wartet === null;

  const waehle = (modus: ModellModus) => {
    if (!bedienbar || modus === aktiv) return;
    if (!onAnfrage({ aktion: "modus", modus })) {
      setFehler("Nicht gesendet: keine Verbindung zum Kern.");
      return;
    }
    setFehler("");
    setWartet(modus);
    uhr.current = setTimeout(() => {
      setWartet(null);
      setFehler("Keine Antwort vom Kern. Bitte später noch einmal versuchen.");
    }, ANTWORT_MS);
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
      <div className="betriebsart-wahl" role="radiogroup" aria-labelledby="modellwahl-label" aria-describedby="modellwahl-text">
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
            disabled={!online || gesperrt}
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
        {wartet ? "Wird gespeichert …" : online ? wirksamText(zugang, betrieb) : "Verbinde dich mit WAMO, um die Wahl zu ändern."}
      </p>
      {fehler && (
        <p className={styles.meldung} role="alert" data-fehler>
          {fehler}
        </p>
      )}
    </div>
  );
});
