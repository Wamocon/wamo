import { createStore } from "./store.ts"; // relativ, damit node:test diese Datei laden kann (canvasWahl.test.ts)

/**
 * Wunsch aus der Tafel "Vorgänge" (Knopf "Ergebnis ansehen", components/kommando/Vorgaenge.tsx): dieses Canvas-Element soll
 * angezeigt werden. Das Canvas (components/Canvas.tsx) hält seine Auswahl selbst; es liest diesen Wunsch und wählt das Element.
 * Der Zähler n läuft bei jedem Wunsch hoch, auch bei derselben Kennung: so greift ein zweiter Klick, nachdem die Person
 * inzwischen ein anderes Element gewählt hat.
 */
export type CanvasWunsch = { id: string | null; n: number };

export const canvasWunsch = createStore<CanvasWunsch>({ id: null, n: 0 });

export function canvasWaehlen(id: string): void {
  canvasWunsch.set((alt) => ({ id, n: alt.n + 1 }));
}

/** Stelle des Elements mit dieser Kennung im Canvas, sonst -1. */
export function canvasIndex(items: readonly { id?: string | null }[], id: string): number {
  if (!id) return -1;
  return items.findIndex((it) => it.id === id);
}
