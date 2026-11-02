"use client";

import { alsStatus, type Status } from "@/lib/aenderungen";
import { KernFehler, kernJson } from "@/lib/kernApi";

/**
 * Abfrage der Karte "Änderungen" (voice-core/release_api.py: GET /wamo/api/release/status, nur Rolle admin). Liest nur: Die Karte ändert nichts am
 * Release-Dienst. Wirft nie; ein Fehler kommt als Text zurück, den die Karte anzeigt.
 */
export type StatusErgebnis = { ok: true; status: Status } | { ok: false; text: string };

export async function statusLaden(): Promise<StatusErgebnis> {
  try {
    const status = alsStatus(await kernJson("/release/status"));
    if (status) return { ok: true, status };
    return { ok: false, text: "Unerwartete Antwort des Kerns." };
  } catch (e) {
    if (e instanceof KernFehler) {
      if (e.status === 403) return { ok: false, text: "Die Änderungen sind nur für Admins mit frischem zweiten Faktor." };
      if (e.status === 429) return { ok: false, text: "Zu viele Abfragen, bitte einen Moment warten." };
      return { ok: false, text: e.message };
    }
    return { ok: false, text: "Kern nicht erreichbar." };
  }
}
