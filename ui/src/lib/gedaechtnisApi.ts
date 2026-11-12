"use client";

import { fehlerText, pruefeErgebnis, pruefeVorschlaege, type Entscheidung, type Ergebnis, type Vorschlag } from "@/lib/gedaechtnis";
import { KernFehler, kernJson } from "@/lib/kernApi";

/**
 * Abruf der Karte "Gedächtnis" beim Kern (voice-core/gedaechtnis_api.py, nur Rolle admin): die offenen gemeinsamen Vorschläge lesen und einen
 * freigeben oder ablehnen. Beide Antworten werden streng geprüft (lib/gedaechtnis.ts). Wirft nie: Ein Fehler kommt als ruhiger Satz zurück,
 * den die Karte anzeigt. Der Kern entscheidet in der Datenbank noch einmal, wer ein Admin ist.
 */
export type VorschlaegeErgebnis = { ok: true; vorschlaege: Vorschlag[] } | { ok: false; text: string };
export type EntscheidungErgebnis = { ok: true; ergebnis: Ergebnis } | { ok: false; text: string };

function alsFehler(e: unknown): { ok: false; text: string } {
  return { ok: false, text: e instanceof KernFehler ? fehlerText(e.status, e.message) : fehlerText(0, "") };
}

export async function vorschlaegeLaden(): Promise<VorschlaegeErgebnis> {
  try {
    const liste = pruefeVorschlaege(await kernJson("/gedaechtnis/vorschlaege"));
    return liste ? { ok: true, vorschlaege: liste } : { ok: false, text: "Unerwartete Antwort des Kerns." };
  } catch (e) {
    return alsFehler(e);
  }
}

export async function vorschlagEntscheiden(id: string, entscheidung: Entscheidung): Promise<EntscheidungErgebnis> {
  try {
    const ergebnis = pruefeErgebnis(await kernJson("/gedaechtnis/entscheiden", "POST", { id, entscheidung }));
    return ergebnis ? { ok: true, ergebnis } : { ok: false, text: "Unerwartete Antwort des Kerns." };
  } catch (e) {
    return alsFehler(e);
  }
}
