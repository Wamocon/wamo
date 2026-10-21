"use client";

import { useSyncExternalStore } from "react";
import { serverBrowser } from "@/lib/geraet";

/**
 * Gerätebezogene Einstellungen (nur dieser Browser bzw. diese Desktop-App), gespeichert in
 * localStorage. Lesen und Schreiben sind abgesichert: ohne Speicher (privates Fenster, gesperrte
 * Website-Daten) gilt der Wert nur bis zum Neuladen. Nie Geheimnisse hier ablegen.
 *
 * "PC-Steuerung beim Start automatisch einschalten": Standard aus, wirkt nur für Profile mit Rolle
 * admin (useWamo prüft das zusätzlich), Schalter in WamoAnsicht neben "PC-Steuerung".
 */

const PC_AUTO = "wamo.pcSteuerungAuto";
let pcAutoSpeicher = false; // Rückfall, wenn localStorage nicht verfügbar ist
const hoerer = new Set<() => void>();

export function pcAutoLesen(): boolean {
  if (typeof window === "undefined") return false;
  if (serverBrowser()) return false; // PC-Steuerung nur in der Desktop-App (lib/geraet.ts)
  try {
    return window.localStorage.getItem(PC_AUTO) === "an";
  } catch {
    return pcAutoSpeicher;
  }
}

export function pcAutoSetzen(an: boolean) {
  pcAutoSpeicher = an;
  try {
    if (an) window.localStorage.setItem(PC_AUTO, "an");
    else window.localStorage.removeItem(PC_AUTO);
  } catch {
    // kein Speicher: Wert bleibt bis zum Neuladen im Rückfall
  }
  hoerer.forEach((h) => h());
}

function pcAutoAbonnieren(h: () => void) {
  hoerer.add(h);
  // Änderung in einem anderen Fenster derselben App
  const onStorage = (e: StorageEvent) => {
    if (e.key === PC_AUTO) h();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    hoerer.delete(h);
    window.removeEventListener("storage", onStorage);
  };
}

/** Aktueller Wert der Einstellung für die Oberfläche (Server: immer aus). */
export function usePcAuto(): boolean {
  return useSyncExternalStore(pcAutoAbonnieren, pcAutoLesen, () => false);
}
