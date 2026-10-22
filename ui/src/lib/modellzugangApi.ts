"use client";

import { useCallback } from "react";
import { KernFehler, kernJson } from "@/lib/kernApi";
import { fehlerStand, fehlerText, standAusAntwort, STATUS_MS } from "@/lib/modellzugangHilfe";
import type { ModellzugangAnfrage } from "@/lib/useWamo";
import { applyServerMessage, istOnline, wamo, wamoEpoche } from "@/lib/wamo";

/**
 * Modellzugang ohne laufendes Gespräch: dieselben Aktionen wie über den Datenkanal (Stand lesen, Modus wählen,
 * Schlüssel hinterlegen oder löschen), aber über /wamo/api/modellzugang am Kern (voice-core/modellzugang_api.py), mit
 * dem Anmelde-Token der Sitzung. Die Antwort wird wie eine Nachricht des Kerns geprüft und in den Speicher gelegt, die
 * Karte reagiert also gleich wie bei einer Antwort über den Datenkanal. Ein Fehler wird zu einem konkreten Satz
 * (lib/modellzugangHilfe.ts). Der Schlüssel steht nur in der Anfrage; er wird weder gespeichert noch in einer Meldung
 * oder im Zustand abgelegt. Eine Antwort, die erst nach dem Abmelden eintrifft, wird verworfen (der Stand gehörte
 * dem vorigen Profil).
 */
export async function modellzugangOhneGespraech(anfrage: ModellzugangAnfrage): Promise<void> {
  const epoche = wamoEpoche();
  let nachricht;
  try {
    const daten =
      anfrage.aktion === "status" ? await kernJson("/modellzugang") : await kernJson("/modellzugang", "POST", anfrage);
    nachricht = standAusAntwort(daten) ?? fehlerStand(anfrage.aktion, "Unerwartete Antwort des Kerns.");
  } catch (e) {
    const f = e instanceof KernFehler ? e : new KernFehler(0, "");
    nachricht = fehlerStand(anfrage.aktion, fehlerText(f.status, f.message));
  }
  if (epoche === wamoEpoche()) applyServerMessage(nachricht);
}

/**
 * Sendefunktion für die Karte: mit Sitzung über den Datenkanal (onAnfrage), sonst oder wenn der Datenkanal nicht
 * annimmt über die HTTP-Schnittstelle. Die Antwort kommt in beiden Fällen als neuer Stand im Speicher an. Für die
 * Statusabfrage über den Datenkanal gilt eine Zeitgrenze: Bleibt die Antwort aus, steht ein Fehlerstand da, damit die
 * Karte "Erneut versuchen" anbieten kann, statt ewig zu laden.
 */
export function useModellzugangSenden(onAnfrage?: (anfrage: ModellzugangAnfrage) => boolean) {
  return useCallback(
    (anfrage: ModellzugangAnfrage) => {
      if (onAnfrage && istOnline(wamo.get()) && onAnfrage(anfrage)) {
        if (anfrage.aktion === "status") {
          const epoche = wamoEpoche();
          setTimeout(() => {
            if (epoche === wamoEpoche() && wamo.get().modellzugang === null) {
              applyServerMessage(fehlerStand("status", "Keine Antwort vom Kern. Bitte noch einmal versuchen."));
            }
          }, STATUS_MS);
        }
        return;
      }
      void modellzugangOhneGespraech(anfrage);
    },
    [onAnfrage],
  );
}
