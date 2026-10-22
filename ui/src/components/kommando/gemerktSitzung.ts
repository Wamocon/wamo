"use client";

import { useSyncExternalStore } from "react";
import { wamo, type Memo } from "@/lib/wamo";
import { gemerktAbgleichen, gemerktEntfernen } from "./gemerktListe";

/**
 * Modul-Store für die Gedächtnis-Karte: sammelt die "Gemerkt"-Einträge der Sitzung (Logik in gemerktListe.ts).
 * Hängt dauerhaft am Store von WAMO (ein Abo für die ganze Seite), damit nichts verloren geht, wenn die Karte
 * gerade nicht eingehängt ist. Mit dem Ende des Gesprächs (offline) wird die Liste geleert.
 */

let liste: readonly Memo[] = [];
const hoerer = new Set<() => void>();
let abo: (() => void) | null = null;

function setze(neu: readonly Memo[]) {
  if (neu === liste) return;
  liste = neu;
  hoerer.forEach((h) => h());
}

function beiStore() {
  const s = wamo.get();
  if (s.presence === "offline") setze(liste.length ? [] : liste);
  else setze(gemerktAbgleichen(liste, s.memos));
}

function abonnieren(h: () => void) {
  hoerer.add(h);
  abo ??= wamo.subscribe(beiStore);
  beiStore();
  return () => {
    hoerer.delete(h);
  };
}

/** Einträge dieser Sitzung, neueste zuerst. */
export function useGemerktSitzung(): readonly Memo[] {
  return useSyncExternalStore(abonnieren, () => liste, () => liste);
}

/** Nach "Rückgängig": Eintrag aus der Karte nehmen, sofern er auch aus dem Store verschwunden ist. */
export function gemerktZurueckgenommen(m: Memo) {
  if (!wamo.get().memos.some((x) => x.key === m.key)) setze(gemerktEntfernen(liste, m.key));
}
