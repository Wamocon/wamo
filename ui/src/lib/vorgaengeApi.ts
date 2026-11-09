"use client";

import { kernJson } from "@/lib/kernApi";
import type { Vorgang } from "@/lib/nachrichten";
import { createStore } from "@/lib/store";
import { vorgaengeAusAntwort } from "@/lib/vorgaenge";

/**
 * Verlauf der Tafel "Vorgänge": die letzten Vorgänge des eigenen Profils aus der Datenbank (voice-core/vorgaenge_api.py,
 * GET /wamo/api/vorgaenge, Migration 0017). Die Tafel fragt einmal beim Start. Der Verlauf liegt in einem eigenen Store, damit
 * die Tafel und ihr Leerzustand (kommando/Seitenleisten.tsx) ihn lesen, ohne ihn zweimal zu laden. Was die laufende Sitzung
 * schon kennt, gilt aus der Sitzung (lib/vorgaenge.ts, tafelListen). Fehler (Kern ohne Schnittstelle, Datenbank nicht
 * eingerichtet, kein Netz) bleiben still: dann ist der Verlauf leer.
 */
export const vorgangVerlauf = createStore<Vorgang[]>([]);

let runde = 0; // gegen Überholen und gegen Antworten nach dem Abmelden: nur die Antwort der letzten Anfrage zählt

export async function verlaufLaden(): Promise<void> {
  const meine = ++runde;
  try {
    const liste = vorgaengeAusAntwort(await kernJson("/vorgaenge"));
    if (meine === runde) vorgangVerlauf.set(() => liste);
  } catch {
    // still leer: die Tafel zeigt die Vorgänge der Sitzung auch ohne Verlauf
  }
}

/** Beim Abmelden und Aushängen der Tafel: eine noch laufende Abfrage darf nichts mehr eintragen, der Verlauf gehörte dem Profil. */
export function verlaufLeeren(): void {
  runde++;
  vorgangVerlauf.set((alt) => (alt.length ? [] : alt));
}
