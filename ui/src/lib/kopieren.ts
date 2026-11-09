/**
 * Bestätigung am Kopierknopf des Canvas (components/Canvas.tsx, CodeView): Die Knopfbeschriftung bleibt "Kopieren", daneben
 * erscheint kurz ein Chip in einer Live-Region (aria-live="polite"). Rein, damit sich Text und Dauer ohne Browser prüfen lassen.
 */
export type KopierStatus = "" | "kopiert" | "fehler";

/** So lange steht die Bestätigung da. */
export const KOPIERT_MS = 1500;

export function kopierChipText(status: KopierStatus): string {
  if (status === "kopiert") return "Kopiert";
  if (status === "fehler") return "Nicht kopiert"; // zum Beispiel Zwischenablage nicht erlaubt
  return "";
}
