"use client";

import { memo } from "react";
import { useSelect } from "@/lib/store";
import type { StimmabdruckAktionen } from "@/lib/useWamo";
import { wamo } from "@/lib/wamo";
import { Kernbereich } from "./Kernbereich";
import { StimmeEinlernen } from "./StimmeEinlernen";

/**
 * Die obere Hälfte der Bühne: der Kern, oder, solange die Einlernansicht offen ist, die Stimme einlernen (wie beim Einlernen am Handy,
 * ohne zwischen Seiten zu wechseln). Ohne stimmabdruck (Laborkonten, Vorschau ohne Kern) gibt es die Ansicht nie, es bleibt beim Kern.
 */
export const KernOderEinlernen = memo(function KernOderEinlernen({ stimmabdruck }: { stimmabdruck?: StimmabdruckAktionen }) {
  const einlernen = useSelect(wamo, (s) => s.abdruck !== null);
  return stimmabdruck && einlernen ? <StimmeEinlernen aktionen={stimmabdruck} /> : <Kernbereich />;
});
