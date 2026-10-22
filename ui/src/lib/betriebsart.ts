"use client";

import { useSyncExternalStore } from "react";
import { NUR_BUERO_BUILD, serverBrowser } from "@/lib/geraet";

/**
 * Betriebsart, je Gerät dauerhaft:
 *   unterwegs  Voice-Core auf diesem PC (NEXT_PUBLIC_VOICE_CORE_URL, Standard http://localhost:7860)
 *   buero      Voice-Core auf Sokrates-2 (NEXT_PUBLIC_WAMO_KERN_BUERO, nur https)
 *
 * In der Desktop-App hält das Hauptprogramm die Einstellung (desktop/main.js, userData/einstellungen.json), weil es
 * in "unterwegs" den lokalen Kern startet; die Oberfläche liest und setzt sie über window.wamoDesktop.betriebsart.
 * Im Browser liegt sie in localStorage. Die Adressen stehen zur Build-Zeit fest (auch in der CSP); über die
 * Brücke kommt nur der Name der Betriebsart, nie eine Adresse.
 * Standard bleibt "unterwegs" (NEXT_PUBLIC_WAMO_BETRIEBSART=buero ändert den Standard im Browser).
 *
 * Im Build für Sokrates-2 (NEXT_PUBLIC_WAMO_NUR_BUERO=ja, lib/geraet.ts) gibt es keinen lokalen Kern: Ein Browser
 * darauf hat nur die Betriebsart Büro (Standard und einzige Wahl), "unterwegs" ist dort nicht eingerichtet.
 */

export type Betriebsart = "buero" | "unterwegs";

export const BETRIEBSARTEN: readonly Betriebsart[] = ["buero", "unterwegs"];

/** Was die Desktop-App über preload.js anbietet (ältere Versionen kennen das nicht). */
export type BetriebsartBruecke = {
  lesen: () => Promise<unknown>;
  setzen: (art: Betriebsart) => Promise<unknown>;
  /** Änderung aus dem Tray-Menü; liefert eine Abmeldefunktion */
  abonnieren: (rueckruf: (art: unknown) => void) => () => void;
};

/** Nur https, ohne Zugangsdaten in der Adresse; sonst ist Büro nicht verfügbar. */
function sichereHerkunft(url: string | undefined): string {
  if (!url) return "";
  try {
    const u = new URL(url);
    return u.protocol === "https:" && !u.username && !u.password ? u.origin : "";
  } catch {
    return "";
  }
}

// Der Build für Sokrates-2 kennt keinen lokalen Kern (auch nicht in der CSP, next.config.ts)
const URL_UNTERWEGS = NUR_BUERO_BUILD ? "" : (process.env.NEXT_PUBLIC_VOICE_CORE_URL ?? "http://localhost:7860");
const URL_BUERO = sichereHerkunft(process.env.NEXT_PUBLIC_WAMO_KERN_BUERO);

export const BUERO_VERFUEGBAR = URL_BUERO !== "";

export const KERN: Record<Betriebsart, { name: string; ort: string; url: string }> = {
  buero: { name: "Büro", ort: "Sokrates-2", url: URL_BUERO },
  unterwegs: { name: "Unterwegs", ort: "dieser PC", url: URL_UNTERWEGS },
};

const STANDARD: Betriebsart =
  (process.env.NEXT_PUBLIC_WAMO_BETRIEBSART === "buero" || NUR_BUERO_BUILD) && BUERO_VERFUEGBAR ? "buero" : "unterwegs";
const SPEICHER = "wamo.betriebsart";

export function istBetriebsart(v: unknown): v is Betriebsart {
  return v === "buero" || v === "unterwegs";
}

/** Gültige Betriebsart; Büro ohne eingerichtete Adresse fällt auf Unterwegs zurück. Ein Browser auf dem Server hat
 * nur Büro (ist es nicht eingerichtet, bleibt Unterwegs und die Meldung "nicht eingerichtet" zeigt den Fehler). */
function pruefe(v: unknown): Betriebsart | null {
  if (!istBetriebsart(v)) return null;
  if (serverBrowser()) return BUERO_VERFUEGBAR ? "buero" : "unterwegs";
  return v === "buero" && !BUERO_VERFUEGBAR ? "unterwegs" : v;
}

function bruecke(): BetriebsartBruecke | undefined {
  if (typeof window === "undefined") return undefined;
  const b = window.wamoDesktop?.betriebsart;
  return b && typeof b.lesen === "function" && typeof b.setzen === "function" ? b : undefined;
}

function ausSpeicher(): Betriebsart {
  try {
    return pruefe(window.localStorage.getItem(SPEICHER)) ?? STANDARD;
  } catch {
    return STANDARD;
  }
}

let aktuell: Betriebsart | null = null; // null = noch nicht gelesen
const hoerer = new Set<() => void>();

function uebernehmen(v: unknown) {
  const art = pruefe(v);
  if (!art || art === aktuell) return;
  aktuell = art;
  hoerer.forEach((h) => h());
}

let gestartet = false;
function starte() {
  if (gestartet || typeof window === "undefined") return;
  gestartet = true;
  const b = bruecke();
  if (!b) {
    aktuell = ausSpeicher();
    window.addEventListener("storage", (e) => {
      if (e.key === SPEICHER) uebernehmen(ausSpeicher());
    });
    return;
  }
  aktuell = STANDARD; // bis die Desktop-App antwortet
  b.abonnieren(uebernehmen);
  void b.lesen().then(uebernehmen, () => undefined);
}

/** Betriebsart, mit der das nächste Gespräch startet. */
export function betriebsartAktuell(): Betriebsart {
  starte();
  return aktuell ?? STANDARD;
}

/** Betriebsart direkt vor dem Verbinden: in der Desktop-App frisch aus dem Hauptprogramm (höchstens 2 s). */
export async function betriebsartFuerStart(): Promise<Betriebsart> {
  starte();
  const b = bruecke();
  if (b) {
    const zeit = new Promise<null>((r) => setTimeout(() => r(null), 2000));
    try {
      const wert = await Promise.race([b.lesen(), zeit]);
      if (wert !== null) uebernehmen(wert);
    } catch {
      // Hauptprogramm antwortet nicht: zuletzt bekannte Einstellung
    }
  }
  return betriebsartAktuell();
}

/** Setzt die Betriebsart dieses Geräts. Desktop: das Hauptprogramm bestätigt (und startet ggf. den lokalen Kern). */
export async function betriebsartSetzen(art: Betriebsart): Promise<Betriebsart> {
  starte();
  const ziel = pruefe(art) ?? STANDARD;
  const b = bruecke();
  if (b) {
    uebernehmen(await b.setzen(ziel));
    return betriebsartAktuell();
  }
  try {
    window.localStorage.setItem(SPEICHER, ziel);
  } catch {
    // kein Speicher: gilt bis zum Neuladen
  }
  uebernehmen(ziel);
  return ziel;
}

function abonnieren(h: () => void) {
  starte();
  hoerer.add(h);
  return () => {
    hoerer.delete(h);
  };
}

/** Aktuelle Betriebsart für die Oberfläche (Server: Standard). */
export function useBetriebsart(): Betriebsart {
  return useSyncExternalStore(abonnieren, betriebsartAktuell, () => STANDARD);
}

/** Adresse des Kerns für eine Betriebsart. */
export function kernUrl(art: Betriebsart): string {
  return KERN[art].url;
}
