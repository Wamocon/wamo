"use client";

import { useStillStart } from "@/lib/einstellungen";
import { useNutzer } from "@/lib/nutzer";
import { istLaborKonto } from "@/lib/stillStart";

/**
 * Darf das Tippen auf diesem Gerät für diese Person von selbst eine Sitzung starten? Nur mit eingeschaltetem Schalter (je Gerät und
 * Person, Standard aus) und nie für ein Laborkonto. Die Entscheidung im Moment des Tastendrucks trifft zusätzlich useWamo.stillStarten
 * (verdeckter Tab, Übernahme, Ratenbegrenzung); das hier steuert, was die Oberfläche anbietet.
 */
export function useStillStartErlaubt(): { erlaubt: boolean; schalter: boolean; labor: boolean; userId: string | null } {
  const nutzer = useNutzer();
  const schalter = useStillStart(nutzer.id);
  const labor = istLaborKonto(nutzer.email);
  return { erlaubt: schalter && !labor, schalter, labor, userId: nutzer.id };
}
