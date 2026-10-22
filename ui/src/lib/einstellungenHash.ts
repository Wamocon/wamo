"use client";

import { useMemo, useSyncExternalStore } from "react";
import { hashFuer, parseHash, type AbschnittId, type HashZiel } from "@/lib/einstellungenAbschnitte";
import {
  basisNachErsetzen,
  nachRueckschrittNochOffen,
  schliessenSchritt,
  tiefe,
  waehlenSchritt,
  zurListeSchritt,
} from "@/lib/einstellungenVerlauf";

/**
 * Die Adresse (Hash) als Zustand der Einstellungsseite. Der Hash ist die einzige Quelle: Öffnen, Abschnitt wählen und
 * Schließen schreiben ihn, die Oberfläche liest ihn. So gehen Links, "Zurück" des Browsers und ein Neuladen ohne
 * zweiten Zustand. Geschrieben wird über die History-API (Next.js hält die Adresse damit in Takt, die Seite lädt nicht
 * neu und ein laufendes Gespräch bleibt unberührt). Der Eintrag im Verlauf trägt bewusst keinen eigenen State (null):
 * Next.js lädt die Seite neu, wenn "Zurück" auf einen fremden State trifft, bei null tut es nichts. Welche Einträge die
 * Seite selbst angelegt hat und wohin "Zurück" und "Zum Gespräch" führen, steht in lib/einstellungenVerlauf.ts.
 * pushState und replaceState lösen kein Ereignis aus, deshalb benachrichtigt dieses Modul seine Hörer selbst; hashchange
 * und popstate decken Zurück, Vor und das Ändern der Adresse von Hand ab.
 */

const hoerer = new Set<() => void>();
/** Tiefe des Eintrags, bei dem die Seite begann (siehe einstellungenVerlauf.ts); null, solange sie geschlossen ist. */
let basis: number | null = null;
/** "Zum Gespräch" ging mit history.go zurück und liegt dann noch auf einem offenen Eintrag der Basis: dort ersetzen. */
let schliesstGerade = false;
let sicherheitsUhr: ReturnType<typeof setTimeout> | null = null;
/** Das Element, das die Seite geöffnet hat (Link, Zahnrad): beim Schließen kehrt der Fokus dorthin zurück. */
let oeffner: HTMLElement | null = null;

/** Einmaliger Rückruf, sobald die Seite zu ist (zum Beispiel: danach das Canvas zeigen). */
let danach: (() => void) | null = null;

const lesen = (): string => (typeof window === "undefined" ? "" : window.location.hash);
function melden() {
  hoerer.forEach((h) => h());
  if (danach && !parseHash(lesen()).offen) {
    const rueckruf = danach;
    danach = null;
    rueckruf();
  }
}

// ---------- Breite: zwei Spalten oder Liste (dieselbe Grenze wie in einstellungen.css) ----------
const ZWEI_SPALTEN = "(min-width: 860px)";
let breite: MediaQueryList | null = null;
function breiteListe(): MediaQueryList {
  breite ??= window.matchMedia(ZWEI_SPALTEN);
  return breite;
}
const zweispaltigJetzt = (): boolean => breiteListe().matches;

function ersetze(hash: string) {
  window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
}

/** Den offenen Eintrag durch einen anderen offenen ersetzen; die Basis folgt, wenn es der Eintrag der Basis ist. */
function ersetzeOffen(hash: string) {
  const zwei = zweispaltigJetzt();
  basis = basisNachErsetzen(basis, tiefe(parseHash(lesen()), zwei), tiefe(parseHash(hash), zwei));
  ersetze(hash);
}

function beiAenderung() {
  if (sicherheitsUhr) {
    clearTimeout(sicherheitsUhr);
    sicherheitsUhr = null;
  }
  const ziel = parseHash(lesen());
  if (!ziel.offen) {
    basis = null;
    schliesstGerade = false;
  } else if (schliesstGerade) {
    // zurückgegangen bis zur Basis: die Seite begann hier (Link oder Neuladen), also den Eintrag ersetzen
    schliesstGerade = false;
    basis = null;
    ersetze("");
  } else {
    basis ??= tiefe(ziel, zweispaltigJetzt()); // Adresse von Hand geändert oder Vor in einen offenen Eintrag
  }
  melden();
}

let gestartet = false;
function starte() {
  if (gestartet || typeof window === "undefined") return;
  gestartet = true;
  window.addEventListener("hashchange", beiAenderung);
  window.addEventListener("popstate", beiAenderung);
  if (parseHash(lesen()).offen) basis = tiefe(parseHash(lesen()), zweispaltigJetzt()); // Link oder Neuladen
}

function abonnieren(h: () => void) {
  starte();
  hoerer.add(h);
  return () => {
    hoerer.delete(h);
  };
}

/** Ziel aus der Adresse (Server: geschlossen). */
export function useEinstellungenZiel(): HashZiel {
  const hash = useSyncExternalStore(abonnieren, lesen, () => "");
  return useMemo(() => parseHash(hash), [hash]);
}

/** Seite öffnen. Aus dem Gespräch: neuer Eintrag im Verlauf, damit "Zurück" zum Gespräch führt. Schon offen: nur wechseln. */
export function einstellungenOeffnen(abschnitt: AbschnittId | null = null) {
  starte();
  const hash = hashFuer(abschnitt);
  if (parseHash(lesen()).offen) {
    if (lesen() !== hash) {
      ersetzeOffen(hash);
      melden();
    }
    return;
  }
  const a = typeof document === "undefined" ? null : document.activeElement;
  oeffner = a instanceof HTMLElement && a !== document.body ? a : null;
  basis = 0;
  window.history.pushState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
  melden();
}

/**
 * Abschnitt wählen. Am Handy führt die Liste mit einem neuen Eintrag in den Abschnitt, damit "Zurück" zur Liste geht;
 * zweispaltig und beim Wechsel zwischen Abschnitten ersetzt der Wechsel den Eintrag, damit "Zurück" nicht durch alle
 * besuchten Abschnitte läuft.
 */
export function abschnittWaehlen(abschnitt: AbschnittId) {
  starte();
  const ziel = hashFuer(abschnitt);
  const aktuell = lesen();
  const vonListe = parseHash(aktuell).abschnitt === null;
  const art = waehlenSchritt({ zweispaltig: zweispaltigJetzt(), aktuell, ziel, vonListe });
  if (art === "nichts") return;
  if (art === "neu") window.history.pushState(null, "", `${window.location.pathname}${window.location.search}${ziel}`);
  else ersetzeOffen(ziel);
  melden();
}

/** Am Handy aus einem Abschnitt zurück zur Liste: ein Schritt zurück, wenn die Liste davor von der Seite stammt. */
export function zurListe() {
  starte();
  const schritt = zurListeSchritt(tiefe(parseHash(lesen()), zweispaltigJetzt()), basis ?? 0);
  if (schritt.art === "zurueck") {
    window.history.go(-schritt.schritte);
    return;
  }
  ersetzeOffen(hashFuer(null));
  melden();
}

/**
 * Seite schließen: Einträge, die sie angelegt hat, per "Zurück" entfernen; sonst den Eintrag ersetzen. `nachher` läuft
 * einmal, sobald die Seite wirklich zu ist.
 */
export function einstellungenSchliessen(nachher?: () => void) {
  starte();
  if (nachher) danach = nachher;
  const aktuelleTiefe = tiefe(parseHash(lesen()), zweispaltigJetzt());
  const schritt = schliessenSchritt(aktuelleTiefe, basis ?? aktuelleTiefe);
  if (schritt.art === "zurueck") {
    schliesstGerade = nachRueckschrittNochOffen(basis ?? 0);
    window.history.go(-schritt.schritte);
    // Kommt kein popstate (nichts zum Zurückgehen), wird nach kurzer Zeit ersetzt, damit die Seite nie hängen bleibt
    sicherheitsUhr = setTimeout(() => {
      sicherheitsUhr = null;
      schliesstGerade = false;
      if (parseHash(lesen()).offen) {
        basis = null;
        ersetze("");
        melden();
      }
    }, 600);
    return;
  }
  basis = null;
  ersetze("");
  melden();
}

/** Element, das die Seite geöffnet hat, solange es noch da und sichtbar ist (einmalig); sonst null. */
export function oeffnerFuerFokus(): HTMLElement | null {
  const el = oeffner;
  oeffner = null;
  return el && el.isConnected && el.getClientRects().length > 0 ? el : null;
}

function abonnierenBreite(h: () => void) {
  const m = breiteListe();
  m.addEventListener("change", h);
  return () => m.removeEventListener("change", h);
}

/** Gilt gerade das zweispaltige Layout? (Server: ja.) */
export function useZweispaltig(): boolean {
  return useSyncExternalStore(abonnierenBreite, zweispaltigJetzt, () => true);
}
