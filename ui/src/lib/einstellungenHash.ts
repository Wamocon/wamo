"use client";

import { useMemo, useSyncExternalStore } from "react";
import { hashFuer, parseHash, type AbschnittId, type HashZiel } from "@/lib/einstellungenAbschnitte";

/**
 * Die Adresse (Hash) als Zustand der Einstellungsseite. Der Hash ist die einzige Quelle: Öffnen, Abschnitt wählen und
 * Schließen schreiben ihn, die Oberfläche liest ihn. So gehen Links, "Zurück" des Browsers und ein Neuladen ohne
 * zweiten Zustand. Geschrieben wird über die History-API (Next.js hält die Adresse damit in Takt, die Seite lädt nicht
 * neu und ein laufendes Gespräch bleibt unberührt); pushState und replaceState lösen kein Ereignis aus, deshalb
 * benachrichtigt dieses Modul seine Hörer selbst. hashchange und popstate decken Zurück, Vor und das Ändern der Adresse
 * von Hand ab.
 */

const hoerer = new Set<() => void>();

function lesen(): string {
  return typeof window === "undefined" ? "" : window.location.hash;
}

function melden() {
  hoerer.forEach((h) => h());
}

function abonnieren(h: () => void) {
  hoerer.add(h);
  window.addEventListener("hashchange", h);
  window.addEventListener("popstate", h);
  return () => {
    hoerer.delete(h);
    window.removeEventListener("hashchange", h);
    window.removeEventListener("popstate", h);
  };
}

/** Ziel aus der Adresse (Server: geschlossen). */
export function useEinstellungenZiel(): HashZiel {
  const hash = useSyncExternalStore(abonnieren, lesen, () => "");
  return useMemo(() => parseHash(hash), [hash]);
}

function schreibe(hash: string, ersetzen: boolean) {
  const url = `${window.location.pathname}${window.location.search}${hash}`;
  if (ersetzen) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
  melden();
}

/** Seite öffnen (neuer Eintrag im Verlauf, damit "Zurück" zum Gespräch führt). */
export function einstellungenOeffnen(abschnitt: AbschnittId | null = null) {
  schreibe(hashFuer(abschnitt), false);
}

/**
 * Abschnitt wählen. Am Handy führt die Liste in den Abschnitt: ein neuer Eintrag, damit "Zurück" zur Liste geht.
 * Zweispaltig ersetzt der Wechsel den Eintrag, damit "Zurück" nicht erst durch alle besuchten Abschnitte läuft.
 */
export function abschnittWaehlen(abschnitt: AbschnittId | null, ersetzen: boolean) {
  schreibe(hashFuer(abschnitt), ersetzen);
}

/** Seite schließen: Hash entfernen, ohne einen weiteren Eintrag anzulegen. */
export function einstellungenSchliessen() {
  schreibe("", true);
}

// ---------- Breite: zwei Spalten oder Liste ----------
const ZWEI_SPALTEN = "(min-width: 860px)";

function abonnierenBreite(h: () => void) {
  const m = window.matchMedia(ZWEI_SPALTEN);
  m.addEventListener("change", h);
  return () => m.removeEventListener("change", h);
}

/** Gilt gerade das zweispaltige Layout? (Dieselbe Grenze wie in einstellungen.css; Server: ja.) */
export function useZweispaltig(): boolean {
  return useSyncExternalStore(abonnierenBreite, () => window.matchMedia(ZWEI_SPALTEN).matches, () => true);
}
