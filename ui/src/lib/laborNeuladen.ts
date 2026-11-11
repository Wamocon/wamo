// Testlabor: Beendet der Betreuer einen Durchlauf, schickt der Kern "labor_durchlauf_ende" und beendet die Sitzung. Untertitel und Canvas
// liegen im Speicher der Seite; die Seite lädt sich deshalb selbst neu, damit nichts zum nächsten Teilnehmer wandert (Befund 9).
// Eine Sperre von zehn Sekunden verhindert eine Schleife, falls die Nachricht kurz hintereinander mehrfach käme. Der Marker liegt im
// sessionStorage (überlebt das Neuladen im selben Tab). Ist der Speicher nicht benutzbar, wird trotzdem neu geladen: ein Durchlauf endet
// höchstens einmal je Sitzung, und Neuladen ist die sichere Richtung.

export const NEULADEN_VERZOEGERUNG_MS = 1500; // der Hinweis "Sitzung beendet" bleibt kurz sichtbar
export const NEULADEN_SPERRE_MS = 10_000;
const MARKER = "wamo.labor.neuladen";

export type NeuladenSpeicher = { getItem(k: string): string | null; setItem(k: string, v: string): void };

export type NeuladenOptionen = {
  neuladen: () => void;
  jetzt?: () => number;
  /** null: kein Speicher (nur der Test setzt es); fehlt das Feld, gilt sessionStorage, falls benutzbar */
  speicher?: NeuladenSpeicher | null;
  verzoegern?: (fn: () => void, ms: number) => unknown;
};

function standardSpeicher(): NeuladenSpeicher | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

/** Plant das Neuladen. true: geplant. false: gesperrt, weil es vor weniger als zehn Sekunden schon eines gab. */
export function neuladenPlanen(opt: NeuladenOptionen): boolean {
  const jetzt = (opt.jetzt ?? Date.now)();
  const speicher = opt.speicher === undefined ? standardSpeicher() : opt.speicher;
  try {
    const letztes = Number(speicher?.getItem(MARKER));
    // ein unlesbarer Marker oder eine zurückgestellte Uhr (Marker in der Zukunft) sperrt nie
    if (Number.isFinite(letztes) && letztes > 0 && letztes <= jetzt && jetzt - letztes < NEULADEN_SPERRE_MS) return false;
  } catch {
    // Speicher gesperrt: weiter, neu laden ist die sichere Richtung
  }
  try {
    speicher?.setItem(MARKER, String(jetzt));
  } catch {
    // ohne Marker gibt es keine Sperre, das Neuladen läuft trotzdem
  }
  (opt.verzoegern ?? ((fn: () => void, ms: number) => setTimeout(fn, ms)))(opt.neuladen, NEULADEN_VERZOEGERUNG_MS);
  return true;
}
