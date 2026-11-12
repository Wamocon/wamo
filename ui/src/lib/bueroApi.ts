"use client";

import { pruefeBuero, type Aktion, type BueroStand } from "@/components/kommando/bueroKarteModell";
import { KernFehler, kernJson } from "@/lib/kernApi";

/**
 * Abruf der Büro-Karte beim Kern: GET /wamo/api/buero/status (voice-core/buero_api.py), jede Rolle. Schalten: POST /wamo/api/buero/steuern,
 * nur Admins, nur die sechs Aktionen aus bueroKarteModell.AKTIONEN (der Kern prüft alles noch einmal).
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

/** Ein Gerät schalten. Wirft KernFehler mit dem ruhigen Satz des Kerns (zum Beispiel 409 "Das geht mit diesem Gerät nicht"). */
export async function bueroSteuern(geraet: string, aktion: Aktion): Promise<void> {
  if (attrappe) return; // NUR ENTWICKLUNG (Vorschau): nichts geht ins Netz
  const antwort = await kernJson("/buero/steuern", "POST", { geraet, aktion });
  if (!antwort || typeof antwort !== "object" || (antwort as { ok?: unknown }).ok !== true) {
    throw new KernFehler(502, "Die Antwort beim Schalten war nicht lesbar");
  }
}
