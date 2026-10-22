"use client";

import { useSyncExternalStore } from "react";

/**
 * Darstellung der Oberfläche, je Gerät dauerhaft (localStorage, jeder Zugriff in try/catch):
 *   normal   drei Spalten (Hinweise, Kern, Aufträge)
 *   fokus    nur Kern und Untertitel
 *   zentrale große Kommandozentrale mit Statuszeilen und Zähler
 *   nacht    erzwungen dunkel, Kern gedämpft
 * Ohne nutzbaren Speicher (privates Fenster) gilt die Wahl bis zum Neuladen. Nie Geheimnisse hier ablegen.
 */

export type Darstellung = "normal" | "fokus" | "zentrale" | "nacht";

export const DARSTELLUNGEN: readonly Darstellung[] = ["normal", "fokus", "zentrale", "nacht"];

export const DARSTELLUNG_TEXT: Record<Darstellung, string> = {
  normal: "Normal",
  fokus: "Fokus",
  zentrale: "Zentrale",
  nacht: "Nacht",
};

export const DARSTELLUNG_SCHLUESSEL = "wamo.darstellung";

export function istDarstellung(v: unknown): v is Darstellung {
  return typeof v === "string" && (DARSTELLUNGEN as readonly string[]).includes(v);
}

/** Gespeicherte Wahl lesen; bei fehlendem, ungültigem oder gesperrtem Speicher gilt "normal". */
export function darstellungLesen(sp: Pick<Storage, "getItem"> | null): Darstellung {
  try {
    const v = sp?.getItem(DARSTELLUNG_SCHLUESSEL);
    return istDarstellung(v) ? v : "normal";
  } catch {
    return "normal";
  }
}

/** Wahl speichern; false, wenn der Speicher nicht nutzbar ist. */
export function darstellungSchreiben(sp: Pick<Storage, "setItem"> | null, d: Darstellung): boolean {
  try {
    if (!sp) return false;
    sp.setItem(DARSTELLUNG_SCHLUESSEL, d);
    return true;
  } catch {
    return false;
  }
}

function speicher(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // gesperrte Website-Daten
  }
}

let aktuell: Darstellung | null = null; // null = noch nicht gelesen
const hoerer = new Set<() => void>();

function uebernehmen(d: Darstellung) {
  if (d === aktuell) return;
  aktuell = d;
  hoerer.forEach((h) => h());
}

/** Darstellung wählen. merken = false (Vorschau): gilt nur jetzt, ohne den Speicher zu ändern. */
export function darstellungSetzen(d: Darstellung, merken = true) {
  if (merken) darstellungSchreiben(speicher(), d);
  uebernehmen(d);
}

function lesen(): Darstellung {
  aktuell ??= darstellungLesen(speicher());
  return aktuell;
}

function abonnieren(h: () => void) {
  hoerer.add(h);
  // Änderung in einem anderen Fenster derselben App
  const onStorage = (e: StorageEvent) => {
    if (e.key === DARSTELLUNG_SCHLUESSEL) uebernehmen(darstellungLesen(speicher()));
  };
  window.addEventListener("storage", onStorage);
  return () => {
    hoerer.delete(h);
    window.removeEventListener("storage", onStorage);
  };
}

/** Gewählte Darstellung dieses Geräts (Server: normal). */
export function useDarstellung(): Darstellung {
  return useSyncExternalStore(abonnieren, lesen, () => "normal");
}
