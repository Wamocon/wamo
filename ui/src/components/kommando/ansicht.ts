"use client";

import { useSyncExternalStore } from "react";
import { useSelect } from "@/lib/store";
import { istOnline, wamo, type WamoState } from "@/lib/wamo";
import { useDarstellung, type Darstellung } from "./darstellung";

/** Anzahl offener Entscheidungen: Freigaben und Entwicklungsaufträge. */
export const offeneEntscheidungen = (s: WamoState) => s.approvals.length + s.auftraege.length;

/**
 * Darstellung, die gerade gilt. Wartet etwas auf eine Entscheidung, gilt immer "normal":
 * Freigaben dürfen weder im Fokus noch in der Zentrale verborgen sein (sie müssen sichtbar
 * und für Screenreader angesagt werden). Die gespeicherte Wahl bleibt dabei unverändert,
 * danach gilt sie wieder.
 */
export function useEffektiveDarstellung(): { gewaehlt: Darstellung; effektiv: Darstellung; pausiert: boolean } {
  const gewaehlt = useDarstellung();
  const offen = useSelect(wamo, offeneEntscheidungen) > 0;
  const pausiert = offen && (gewaehlt === "fokus" || gewaehlt === "zentrale");
  return { gewaehlt, effektiv: pausiert ? "normal" : gewaehlt, pausiert };
}

// ---------- Beginn des Gesprächs (für Sitzungsfortschritt und Zähler) ----------
let start: number | null = null;
const hoerer = new Set<() => void>();
let abo: (() => void) | null = null;

function pruefe() {
  const s = wamo.get();
  const neu = istOnline(s) ? (start ?? Date.now()) : s.presence === "offline" ? null : start;
  if (neu !== start) {
    start = neu;
    hoerer.forEach((h) => h());
  }
}

function abonnieren(h: () => void) {
  hoerer.add(h);
  abo ??= wamo.subscribe(pruefe);
  pruefe();
  return () => {
    hoerer.delete(h);
    if (!hoerer.size && abo) {
      abo();
      abo = null;
    }
  };
}

/** Zeitpunkt (Date.now), seit dem das Gespräch läuft; null, solange keines läuft. */
export function useGespraechsStart(): number | null {
  return useSyncExternalStore(abonnieren, () => start, () => null);
}

/** Nur für die Vorschau: Beginn setzen, damit Fortschritt und Zähler etwas zeigen. */
export function gespraechsStartSetzen(ms: number | null) {
  start = ms;
  hoerer.forEach((h) => h());
}
