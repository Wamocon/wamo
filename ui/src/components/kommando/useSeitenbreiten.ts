"use client";

import { useState, useSyncExternalStore, type CSSProperties } from "react";
import {
  erzeugeBreitenSpeicher,
  grenzenFuer,
  LINKS_STANDARD,
  passend,
  RECHTS_STANDARD,
  STANDARD_BREITEN,
  type Grenzen,
  type Seite,
} from "./seitenbreite";

/** Was ein Griff braucht (Seitengriff.tsx): Seite, wirksame Breite und Grenzen, dazu die Wege zurück in den Rahmen. */
export type GriffEigenschaften = {
  seite: Seite;
  wert: number;
  grenzen: Grenzen;
  /** fest = false: Vorschau beim Ziehen (nichts wird gespeichert); fest = true: endgültig, wird gespeichert */
  onAendern: (px: number, fest: boolean) => void;
  onZiehen: (an: boolean) => void;
  onStandard: () => void;
};

function lokalerSpeicher(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // gesperrte Website-Daten
  }
}

function abonnierenFenster(h: () => void) {
  window.addEventListener("resize", h);
  return () => window.removeEventListener("resize", h);
}

/** Breite des Fensters ohne Scrollleiste; die Kommandozentrale füllt sie ganz aus. */
const fensterBreite = () => document.documentElement.clientWidth;

/**
 * Breiten der Seitenleisten für den Rahmen. Der Server und der erste Zeichenlauf des Browsers nehmen immer die Standardbreiten
 * (useSyncExternalStore mit Serverwert), die gespeicherte Wahl gilt erst nach dem Laden, noch vor dem ersten Bild und ohne
 * Hydrationsfehler. `stil` setzt die Variablen --kz-links und --kz-rechts nur, wo die Breite vom Standard abweicht; sonst gilt
 * kommando.css. `zieht` nennt die Seite, die gerade gezogen wird (für Cursor und Textauswahl im CSS).
 */
export function useSeitenbreiten() {
  const [speicher] = useState(() => erzeugeBreitenSpeicher(lokalerSpeicher()));
  const gewaehlt = useSyncExternalStore(speicher.abonnieren, speicher.lesen, () => STANDARD_BREITEN);
  const gesamt = useSyncExternalStore(abonnierenFenster, fensterBreite, () => 0);
  const [zieht, setZieht] = useState<Seite | null>(null);

  const breiten = passend(gewaehlt, gesamt);
  const stil: Record<string, string> = {};
  if (breiten.links !== LINKS_STANDARD) stil["--kz-links"] = `${breiten.links}px`;
  if (breiten.rechts !== RECHTS_STANDARD) stil["--kz-rechts"] = `${breiten.rechts}px`;

  const griff = (seite: Seite): GriffEigenschaften => ({
    seite,
    wert: breiten[seite],
    grenzen: grenzenFuer(seite, breiten[seite === "links" ? "rechts" : "links"], gesamt),
    onAendern: (px, fest) => (fest ? speicher.festlegen(seite, px) : speicher.setzen(seite, px)),
    onZiehen: (an) => setZieht(an ? seite : null),
    onStandard: () => speicher.standardSetzen(seite),
  });

  return { stil: stil as CSSProperties, zieht, links: griff("links"), rechts: griff("rechts") };
}
