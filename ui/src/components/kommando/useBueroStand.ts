"use client";

import { useEffect, useState } from "react";
import { bueroLaden } from "@/lib/bueroApi";
import { KernFehler } from "@/lib/kernApi";
import { naechsterAbruf, type BueroStand } from "./bueroKarteModell";

export type BueroZustand = {
  stand: BueroStand | null;
  /** HTTP-Status des letzten fehlgeschlagenen Abrufs (0 = nicht erreichbar), null wenn der letzte Abruf gelang */
  fehler: number | null;
  /** mindestens ein Abruf ist zurück (mit Ergebnis oder Fehler) */
  geladen: boolean;
};

// Der Reiter hängt die Karte nur ein, solange er sichtbar ist. Damit ein Reiterwechsel keinen neuen Abruf auslöst
// (der Kern lässt 6 je Minute zu), merkt sich das Modul den letzten Stand, höchstens 5 Minuten lang.
const MERKEN_MS = 5 * 60_000;
let gemerkt: { stand: BueroStand; geholt: number } | null = null;

function frisch(): { stand: BueroStand; geholt: number } | null {
  return gemerkt && Date.now() - gemerkt.geholt < MERKEN_MS ? gemerkt : null;
}

/**
 * Hält den Stand der Büro-Karte: Abruf beim Einhängen und danach alle 30 Sekunden, solange die Seite sichtbar ist
 * (document.visibilityState). Ist sie verborgen, ruht der Abruf; beim Zurückkehren gilt er sofort, wenn der Stand älter als
 * 30 Sekunden ist. Ein Fehler hält den Takt ein (auch dann 30 Sekunden Ruhe) und lässt den letzten Stand stehen.
 */
export function useBueroStand(): BueroZustand {
  const [zustand, setZustand] = useState<BueroZustand>(() => {
    const m = frisch();
    return { stand: m?.stand ?? null, fehler: null, geladen: m !== null };
  });

  useEffect(() => {
    let beendet = false;
    let laeuft = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let letzter: number | null = frisch()?.geholt ?? null;

    const planen = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (beendet || laeuft) return;
      const warte = naechsterAbruf(letzter, Date.now(), document.visibilityState === "visible");
      if (warte !== null) timer = setTimeout(holen, warte);
    };

    const holen = async () => {
      timer = null;
      if (beendet || laeuft) return;
      laeuft = true;
      try {
        const stand = await bueroLaden();
        letzter = Date.now();
        gemerkt = { stand, geholt: letzter };
        if (!beendet) setZustand({ stand, fehler: null, geladen: true });
      } catch (e) {
        letzter = Date.now();
        const status = e instanceof KernFehler ? e.status : 0;
        if (!beendet) setZustand((alt) => ({ ...alt, fehler: status, geladen: true }));
      } finally {
        laeuft = false;
        planen();
      }
    };

    document.addEventListener("visibilitychange", planen);
    planen();
    return () => {
      beendet = true;
      document.removeEventListener("visibilitychange", planen);
      if (timer !== null) clearTimeout(timer);
    };
  }, []);

  return zustand;
}
