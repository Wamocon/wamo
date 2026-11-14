"use client";

import { useEffect, useRef } from "react";
import { useSelect } from "@/lib/store";
import { wamo, zeigeToast } from "@/lib/wamo";

/**
 * Kurzer Hinweis "Fremde Stimme ignoriert": Im strengen Modus verwirft der Kern eine sicher fremde Äußerung (Nachricht stimme mit
 * ignoriert), sie erreicht das Sprachmodell nicht. Der Hinweis nimmt den gemeinsamen Toast der Seite (sichtbar, mit role="status" für
 * Screenreader, verschwindet von selbst). Jede verworfene Äußerung meldet sich neu, auch zwei gleiche hintereinander, deshalb hängt er
 * am Zähler und nicht am Zustand. Die Komponente rendert nichts und hängt nur bei Konten ein, die die Stimmerkennung sehen (nicht bei
 * Laborkonten, lib/laborPlatz.ts); das Modul "Stimme" in der Kopfzeile zeigt dazu "Fremd · ignoriert".
 */
export function StimmeIgnoriertHinweis() {
  const zaehler = useSelect(wamo, (s) => s.stimme.ignoriertZaehler);
  const gesehen = useRef(zaehler); // beim Einhängen gilt der aktuelle Stand als bekannt: nichts wird nachträglich gemeldet
  useEffect(() => {
    if (zaehler > gesehen.current) zeigeToast("Fremde Stimme ignoriert");
    gesehen.current = zaehler;
  }, [zaehler]);
  return null;
}
