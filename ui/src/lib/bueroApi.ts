"use client";

import { pruefeBuero, type BueroStand } from "@/components/kommando/bueroKarteModell";
import { KernFehler, kernJson } from "@/lib/kernApi";

/**
 * Abruf der Büro-Karte beim Kern: GET /wamo/api/buero/status (voice-core/buero_api.py). Nur lesen, jede Rolle.
 * Die Antwort wird streng geprüft (bueroKarteModell.pruefeBuero); was nicht passt, gilt als nicht lesbar (502).
 * Der Kern erlaubt höchstens 6 Abrufe je Minute und Profil, die Karte ruft alle 30 Sekunden (bueroKarteModell.ABRUF_MS).
 */

type Holer = () => Promise<unknown>;
let attrappe: Holer | null = null;

/** NUR ENTWICKLUNG (Vorschau, ?vorschau=1&buero=...): ersetzt den Abruf beim Kern durch Beispieldaten. */
export function bueroAttrappeSetzen(holer: Holer | null): void {
  attrappe = holer;
}

export async function bueroLaden(): Promise<BueroStand> {
  const stand = pruefeBuero(await (attrappe ? attrappe() : kernJson("/buero/status")));
  if (!stand) throw new KernFehler(502, "Antwort der Büro-Karte nicht lesbar");
  return stand;
}
