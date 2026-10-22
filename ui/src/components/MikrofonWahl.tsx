"use client";

import { useEffect, useState } from "react";
import type { Mikrofon } from "@/lib/mikrofon";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";

/**
 * Liste der angeschlossenen Mikrofone. Lädt beim Erscheinen und noch einmal, wenn Geräte kommen oder gehen
 * (devicechange). null, solange die erste Antwort aussteht. Eine Antwort, die erst nach dem Verschwinden der
 * Komponente eintrifft, wird verworfen.
 */
export function useMikrofonListe(laden: () => Promise<Mikrofon[]>): Mikrofon[] | null {
  const [liste, setListe] = useState<Mikrofon[] | null>(null);
  useEffect(() => {
    let weg = false;
    const holen = () => {
      void laden().then((l) => {
        if (!weg) setListe(l);
      });
    };
    holen();
    const geraete = typeof navigator === "undefined" ? undefined : navigator.mediaDevices;
    geraete?.addEventListener?.("devicechange", holen);
    return () => {
      weg = true;
      geraete?.removeEventListener?.("devicechange", holen);
    };
  }, [laden]);
  return liste;
}

/** Gerätewahl in der Mikrofon-Warnung: Liste beim Erscheinen laden, Wechsel gilt sofort und auf diesem Gerät. */
export function MikrofonWahl({ laden, waehlen }: { laden: () => Promise<Mikrofon[]>; waehlen: (id: string) => boolean }) {
  const liste = useMikrofonListe(laden);
  const aktuell = useSelect(wamo, (s) => s.mikrofon);
  if (!liste || liste.length === 0) return null;
  return (
    <label className="mic-wahl">
      <span>Anderes Mikrofon:</span>
      <select defaultValue="" onChange={(e) => e.target.value && waehlen(e.target.value)}>
        <option value="" disabled>
          {aktuell ? `jetzt: ${aktuell}` : "wählen …"}
        </option>
        {liste.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label}
          </option>
        ))}
      </select>
    </label>
  );
}
