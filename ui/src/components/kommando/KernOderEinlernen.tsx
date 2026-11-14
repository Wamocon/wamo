"use client";

import { memo, useEffect, useRef } from "react";
import { useSelect } from "@/lib/store";
import type { StimmabdruckAktionen } from "@/lib/useWamo";
import { wamo } from "@/lib/wamo";
import { fokusNachEinlernen } from "./einlernFokus";
import { Kernbereich } from "./Kernbereich";
import { StimmeEinlernen } from "./StimmeEinlernen";

/**
 * Die obere Hälfte der Bühne: der Kern, oder, solange die Einlernansicht offen ist, die Stimme einlernen (wie beim Einlernen am Handy,
 * ohne zwischen Seiten zu wechseln). Ohne stimmabdruck (Laborkonten, Vorschau ohne Kern) gibt es die Ansicht nie, es bleibt beim Kern.
 * Schließt die Ansicht und geht der Fokus dabei verloren (er lag in ihr), kommt er auf den Knopf, der sie geöffnet hat, sonst auf die
 * Überschrift des Kerns (kommando/einlernFokus.ts). Steht er woanders, etwa im Eingabefeld, bleibt er dort.
 */
export const KernOderEinlernen = memo(function KernOderEinlernen({ stimmabdruck }: { stimmabdruck?: StimmabdruckAktionen }) {
  const einlernen = useSelect(wamo, (s) => s.abdruck !== null) && stimmabdruck !== undefined;
  const vorher = useRef(einlernen);
  useEffect(() => {
    const war = vorher.current;
    vorher.current = einlernen;
    if (!war || einlernen) return;
    const aktiv = document.activeElement;
    if (aktiv && aktiv !== document.body) return;
    fokusNachEinlernen();
  }, [einlernen]);
  return stimmabdruck && einlernen ? <StimmeEinlernen aktionen={stimmabdruck} /> : <Kernbereich />;
});
