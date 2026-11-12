"use client";

import { useEffect } from "react";
import { kernJson } from "@/lib/kernApi";
import type { Vorgang } from "@/lib/nachrichten";
import { createStore } from "@/lib/store";
import { vorgaengeAusAntwort } from "@/lib/vorgaenge";

/**
 * Verlauf der Tafel "Vorgänge": die letzten Vorgänge des eigenen Profils aus der Datenbank (voice-core/vorgaenge_api.py,
 * GET /wamo/api/vorgaenge, Migration 0017). Die Hauptansicht fragt einmal beim Start (useVerlaufHalten), nicht erst die Tafel:
 * Die Tafel hängt nur ein, solange ihr Reiter sichtbar ist. Der Verlauf liegt in einem eigenen Store, damit die Tafel und ihr
 * Leerzustand (kommando/Seitenleisten.tsx) ihn lesen, ohne ihn zweimal zu laden. Was die laufende Sitzung
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

/** Beim Abmelden und Aushängen der Hauptansicht: eine noch laufende Abfrage darf nichts mehr eintragen, der Verlauf gehörte dem Profil. */
export function verlaufLeeren(): void {
  runde++;
  vorgangVerlauf.set((alt) => (alt.length ? [] : alt));
}

/**
 * Hält den Verlauf für die Dauer der Hauptansicht: einmal laden beim Einhängen, beim Aushängen (Abmelden) freigeben. Steht in
 * WamoAnsicht, die immer eingehängt ist, auch wenn der Reiter "Vorgänge" gerade nicht zu sehen ist.
 * aktiv = false (Laborkonten, lib/laborPlatz.ts): gar kein Abruf. Der Hook wird trotzdem in jedem Render aufgerufen (Regeln der Hooks).
 */
export function useVerlaufHalten(aktiv = true): void {
  useEffect(() => {
    if (!aktiv) return;
    void verlaufLaden();
    return verlaufLeeren;
  }, [aktiv]);
}
