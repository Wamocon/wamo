"use client";

import { useEffect } from "react";
import { auftragLaeuft } from "@/lib/auftragStart";
import { wamo } from "@/lib/wamo";

/**
 * Neuladen oder Schließen, solange ein Entwicklungsauftrag läuft: Ein Auftrag endet mit der Sitzung. Der Browser fragt nach
 * (beforeunload), die Desktop-App zeigt dafür selbst eine Frage (desktop/entladen.js, Ereignis will-prevent-unload). Ohne laufenden
 * Auftrag geschieht nichts. Gelesen wird der Zustand erst beim Ereignis, der Hook rendert deshalb nie neu.
 */
export function useNeuladenWarnung(): void {
  useEffect(() => {
    const warnen = (e: BeforeUnloadEvent) => {
      const s = wamo.get();
      if (!auftragLaeuft(s.vorgaenge, s.auftraege)) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warnen);
    return () => window.removeEventListener("beforeunload", warnen);
  }, []);
}
