// Texte der Freigabekarte je Job (components/Freigaben.tsx, lib/useWamo.ts). Der Job "labor" ist die Freigabe schreibender Aktionen im Testlabor
// (Ticket anlegen, ändern, verschieben, kommentieren, Mail senden; voice-core/labor_freigaben.py). Die Karte zeigt jede Zeile dessen, was ausgeführt
// wird, "Freigeben" und "Ablehnen" sind gleichgewichtig. Der Kern sendet höchstens 1900 UTF-16-Einheiten Text je Karte: die Oberfläche kürzt ein
// Feld bei 2000 (lib/nachrichten.ts) und würde sonst weniger zeigen, als geschieht.

export const LABOR_JOB = "labor";
export const PC_JOB = "pc";
/** Freigabe einer Bürogeräte-Aktion (Drucken, Scannen, Steuern nach Fremdinhalt; voice-core/geraete_werkzeuge.py, JOB). */
export const GERAET_JOB = "geraet";

export type KarteTexte = {
  /** Überschrift über dem Grund der Karte (Eyebrow) und Text der Ansage. */
  ueberschrift: string;
  /** Hinweis unter dem Grund, falls es einen gibt. */
  hinweis?: string;
  /** Beschriftung der Zielzeile. */
  zielLabel: string;
};

export function karteTexte(job: string): KarteTexte {
  if (job === LABOR_JOB) return { ueberschrift: "Aktion freigeben", hinweis: "Du entscheidest, nicht WAMO.", zielLabel: "Ziel:" };
  if (job === PC_JOB) return { ueberschrift: "PC-Aktion freigeben", zielLabel: "Ziel (Fenster und Programm):" };
  if (job === GERAET_JOB) return { ueberschrift: "Bürogerät freigeben", hinweis: "Du entscheidest, nicht WAMO.", zielLabel: "Gerät:" };
  return { ueberschrift: "Freigabe nötig", zielLabel: "Ziel (Fenster und Programm):" };
}

/** Meldung, wenn die Frist einer Freigabe vor dem Klick vorbei ist: bei Labor und Bürogeräten ist nichts passiert, nur bei einer PC-Aktion "am PC". */
export function abgelaufenHinweis(job: string): string {
  return job === LABOR_JOB || job === GERAET_JOB
    ? "Die Freigabe ist abgelaufen; es ist nichts passiert."
    : "Die Freigabe ist abgelaufen; am PC ist nichts passiert.";
}
