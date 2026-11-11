// Fristen der Startkarten (lib/auftragStart.ts): ein Zeitgeber je Auftrag, der nach 15 Sekunden ohne Quittung den neutralen Hinweis
// auslöst. Er wird bei der Quittung, beim Ersetzen durch einen neuen Start und beim Trennen gelöscht, damit nichts später noch an eine
// Karte schreibt, die es nicht mehr gibt. Der Planer ist austauschbar (Tests), ohne Angabe gilt setTimeout.
import { QUITTUNG_FRIST_MS } from "./auftragStart.ts";

export type Planer = {
  setze: (f: () => void, ms: number) => unknown;
  loesche: (id: unknown) => void;
};

const ECHT: Planer = {
  setze: (f, ms) => setTimeout(f, ms),
  loesche: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
};

const offen = new Map<string, unknown>();

/** Startet die Frist dieses Auftrags (eine vorhandene wird ersetzt). Nach Ablauf (etwas länger als die Frist der Karte) ruft sie beiAblauf. */
export function fristStarten(job: string, beiAblauf: () => void, planer: Planer = ECHT): void {
  fristLoeschen(job, planer);
  offen.set(
    job,
    planer.setze(() => {
      offen.delete(job);
      beiAblauf();
    }, QUITTUNG_FRIST_MS + 100),
  );
}

/** Löscht die Frist dieses Auftrags (Quittung eingetroffen). Ein zweites Löschen oder eine unbekannte Kennung ist harmlos. */
export function fristLoeschen(job: string, planer: Planer = ECHT): void {
  if (!offen.has(job)) return;
  planer.loesche(offen.get(job));
  offen.delete(job);
}

/** Löscht alle Fristen (Verbindung getrennt, die Karten sind weg). */
export function alleFristenLoeschen(planer: Planer = ECHT): void {
  for (const id of offen.values()) planer.loesche(id);
  offen.clear();
}

/** Zahl der laufenden Fristen (für Tests). */
export function offeneFristen(): number {
  return offen.size;
}
