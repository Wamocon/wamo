"use client";

import { useEffect, useRef, useState } from "react";
import { drosselWartezeit } from "./drossel";

/** Wie sich ein neuer Wert gegenüber dem Gezeigten verhält: nichts Sichtbares, ein Zwischenstand (darf warten) oder mehr (sofort). */
export type Aenderung = "gleich" | "zwischenstand" | "sonst";

/**
 * Zeigt einen Wert gedrosselt: Ein Zwischenstand erscheint höchstens alle `intervallMs` Millisekunden, aber immer der neueste
 * (nach dem Abstand folgt der letzte Stand, es geht keiner am Ende verloren). Alles andere ("sonst") erscheint im selben
 * Render, ohne Verzögerung und ohne Zwischenbild. Sieht ein neuer Wert gleich aus ("gleich"), bleibt der alte Wert mit seiner
 * Referenz stehen, damit memo-Komponenten darunter nicht neu rendern. `bewerte` muss eine stabile Funktion sein (Modulebene).
 */
export function useGedrosselt<T>(wert: T, bewerte: (gezeigt: T, neu: T) => Aenderung, intervallMs: number): T {
  const [gezeigt, setGezeigt] = useState(wert);
  const stand = useRef<{ seit: number; timer: ReturnType<typeof setTimeout> | null; neuester: T }>({ seit: 0, timer: null, neuester: wert });
  const art = bewerte(gezeigt, wert);

  // Alles außer einem Zwischenstand gilt sofort: den Wert noch im Render übernehmen (Zustand beim Rendern anpassen)
  if (art === "sonst") setGezeigt(wert);

  useEffect(() => {
    const s = stand.current;
    s.neuester = wert;
    if (art !== "zwischenstand") {
      // nichts mehr zu warten: ein offener Zwischenstand wäre veraltet
      if (s.timer !== null) clearTimeout(s.timer);
      s.timer = null;
      return;
    }
    if (s.timer !== null) return; // ein Zeitgeber läuft schon und nimmt den neuesten Stand mit
    s.timer = setTimeout(() => {
      s.timer = null;
      s.seit = Date.now();
      setGezeigt(s.neuester);
    }, drosselWartezeit(Date.now() - s.seit, intervallMs));
  }, [wert, art, intervallMs]);

  useEffect(
    () => () => {
      const s = stand.current;
      if (s.timer !== null) clearTimeout(s.timer);
      s.timer = null;
    },
    [],
  );

  return art === "sonst" ? wert : gezeigt;
}
