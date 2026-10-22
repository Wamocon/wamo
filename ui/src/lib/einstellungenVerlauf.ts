import type { HashZiel } from "./einstellungenAbschnitte.ts";

/**
 * Wie die Einstellungsseite mit dem Verlauf des Browsers umgeht (Rechenkern zu lib/einstellungenHash.ts, ohne Browser,
 * damit er sich mit node:test prüfen lässt). Ziel: "Zurück" des Browsers geht dorthin, wo man hergekommen ist, und
 * "Zum Gespräch" hinterlässt keine doppelten Einträge.
 *
 * Tiefe der Adresse: 0 = geschlossen, 1 = Seite offen (zweispaltig auch mit Abschnitt, denn dort ersetzt ein Wechsel den
 * Eintrag), 2 = Abschnitt am Handy (die Liste führt mit einem neuen Eintrag hinein). Basis: die Tiefe des Eintrags, bei
 * dem die Seite begann: 0, wenn sie per Klick aus dem Gespräch geöffnet wurde (die Einträge darüber hat die Seite selbst
 * angelegt), sonst die Tiefe der Adresse beim Laden (Link, Neuladen). Nur Einträge über der Basis dürfen mit "Zurück"
 * wieder entfernt werden; die Basis selbst ist nie ein Eintrag, den die Seite angelegt hat.
 */

export function tiefe(ziel: HashZiel, zweispaltig: boolean): 0 | 1 | 2 {
  if (!ziel.offen) return 0;
  return !zweispaltig && ziel.abschnitt !== null ? 2 : 1;
}

export type Schritt = { art: "zurueck"; schritte: number } | { art: "ersetzen" };

/** "Zum Gespräch", Escape: alle Einträge über der Basis zurückgehen; liegt nichts darüber, den Eintrag selbst ersetzen. */
export function schliessenSchritt(aktuelleTiefe: number, basis: number): Schritt {
  const darueber = aktuelleTiefe - basis;
  return darueber > 0 ? { art: "zurueck", schritte: darueber } : { art: "ersetzen" };
}

/** Nach "Zurück" um `schritte` Einträge: ist die Seite dort noch offen (Basis über 0), schließt sie danach per Ersetzen. */
export const nachRueckschrittNochOffen = (basis: number): boolean => basis > 0;

/**
 * Ersetzt die Seite den Eintrag, bei dem sie begann (Basis), ändert sich dessen Tiefe: aus dem Abschnitt, mit dem ein
 * Link sie öffnete, wird die Liste. Die Basis folgt, sonst gälte sie später für einen Eintrag, den es so nicht mehr gibt.
 */
export function basisNachErsetzen(basis: number | null, tiefeVorher: number, tiefeNachher: number): number | null {
  return basis !== null && tiefeVorher === basis ? tiefeNachher : basis;
}

/** Am Handy aus einem Abschnitt zur Liste: ein Eintrag zurück, wenn die Liste darunter von der Seite selbst stammt. */
export function zurListeSchritt(aktuelleTiefe: number, basis: number): Schritt {
  return aktuelleTiefe === 2 && basis < 2 ? { art: "zurueck", schritte: 1 } : { art: "ersetzen" };
}

/** Abschnitt wählen: am Handy aus der Liste ein neuer Eintrag, sonst ersetzen; derselbe Hash bewirkt nichts. */
export function waehlenSchritt(o: { zweispaltig: boolean; aktuell: string; ziel: string; vonListe: boolean }): "nichts" | "neu" | "ersetzen" {
  if (o.aktuell === o.ziel) return "nichts";
  return !o.zweispaltig && o.vonListe ? "neu" : "ersetzen";
}
