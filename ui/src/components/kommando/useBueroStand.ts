"use client";

import { useEffect, useState } from "react";
import { bueroLaden } from "@/lib/bueroApi";
import { KernFehler } from "@/lib/kernApi";
import {
  abrufAbstand,
  naechsterAbruf,
  TAKT_NEU,
  taktErfolg,
  taktFehler,
  taktNeuBeginnen,
  type Aktion,
  type BueroStand,
  type Takt,
} from "./bueroKarteModell";

export type BueroZustand = {
  stand: BueroStand | null;
  /** HTTP-Status des letzten fehlgeschlagenen Abrufs (0 = nicht erreichbar), null wenn der letzte Abruf gelang */
  fehler: number | null;
  /** mindestens ein Abruf ist zurück (mit Ergebnis oder Fehler) */
  geladen: boolean;
};

type Ergebnis = { stand: BueroStand } | { fehler: number };

// Der Reiter hängt die Karte nur ein, solange er sichtbar ist. Damit ein Reiterwechsel keinen neuen Abruf auslöst (der Kern
// lässt 6 je Minute zu), hält das Modul den letzten Stand (höchstens 5 Minuten), den Takt (letzter Versuch, Fehler in Folge)
// und den laufenden Abruf. Alles gehört dem angemeldeten Profil und wird beim Abmelden vergessen (bueroStandVergessen).
const MERKEN_MS = 5 * 60_000;
let gemerkt: { stand: BueroStand; geholt: number } | null = null;
let takt: Takt = TAKT_NEU;
let laufend: Promise<Ergebnis> | null = null;
let epoche = 0; // zählt die Abmeldungen: ein Abruf aus früherer Zeit trägt nichts mehr ein
const hoerer = new Set<(e: Ergebnis) => void>(); // Instanzen der Karte, die ein Ergebnis auch außerhalb ihres Taktes bekommen
let nachSchalten: ReturnType<typeof setTimeout>[] = [];

function nachSchaltenLoeschen(): void {
  for (const t of nachSchalten) clearTimeout(t);
  nachSchalten = [];
}

function anwenden(alt: BueroZustand, ergebnis: Ergebnis): BueroZustand {
  return "stand" in ergebnis ? { stand: ergebnis.stand, fehler: null, geladen: true } : { ...alt, fehler: ergebnis.fehler, geladen: true };
}

/**
 * Nach einem Schaltvorgang den Stand bald neu holen, damit die Karte den neuen Zustand zeigt (nach 2,5 Sekunden; ein eingeschalteter
 * Fernseher braucht länger, dafür kommt nach 10 Sekunden noch ein Abruf). Mehrere Schaltvorgänge hintereinander setzen die Zeiten neu, es
 * bleibt bei höchstens zwei Abrufen, und der Kern erlaubt 6 je Minute (der Takt ruft nur alle 30 Sekunden).
 */
export function bueroNachSchalten(aktion: Aktion): void {
  nachSchaltenLoeschen();
  const meine = epoche;
  nachSchalten = (aktion === "an" ? [2500, 10_000] : [2500]).map((ms) =>
    setTimeout(() => {
      if (meine !== epoche) return;
      void abrufen().then((e) => hoerer.forEach((h) => h(e)));
    }, ms),
  );
}

function frisch(): { stand: BueroStand; geholt: number } | null {
  return gemerkt && Date.now() - gemerkt.geholt < MERKEN_MS ? gemerkt : null;
}

async function holeUndMerke(meine: number): Promise<Ergebnis> {
  try {
    const stand = await bueroLaden();
    if (meine === epoche) {
      const jetzt = Date.now();
      gemerkt = { stand, geholt: jetzt };
      takt = taktErfolg(jetzt);
    }
    return { stand };
  } catch (e) {
    const fehler = e instanceof KernFehler ? e.status : 0;
    if (meine === epoche) takt = taktFehler(takt, Date.now(), fehler);
    return { fehler };
  }
}

/** Es läuft nie zwei Mal derselbe Abruf: Wer währenddessen fragt (zweite Instanz, Wechsel des Reiters), bekommt dasselbe Ergebnis. */
function abrufen(): Promise<Ergebnis> {
  if (laufend) return laufend;
  const lauf: Promise<Ergebnis> = holeUndMerke(epoche).finally(() => {
    if (laufend === lauf) laufend = null;
  });
  laufend = lauf;
  return lauf;
}

/** Einhängen der Karte und Sichtbarwerden der Seite: ein früherer Stopp (401, 403) und das Wachstum nach Fehlern entfallen. */
function taktErneuern(): void {
  takt = taktNeuBeginnen(takt);
}

/** Beim Abmelden: Stand, Takt und laufenden Abruf des bisherigen Profils verwerfen. */
export function bueroStandVergessen(): void {
  nachSchaltenLoeschen();
  epoche++;
  gemerkt = null;
  takt = TAKT_NEU;
  laufend = null;
}

/**
 * Lässt den Stand der Büro-Karte so lange gelten, wie die Hauptansicht lebt, und vergisst ihn beim Aushängen (Abmelden). Steht
 * in WamoAnsicht, die immer eingehängt ist; die Karte selbst hängt nur bei sichtbarem Reiter ein und darf ihn nicht verwerfen.
 */
export function useBueroStandHalten(): void {
  useEffect(() => bueroStandVergessen, []);
}

/**
 * Hält den Stand der Büro-Karte: Abruf beim Einhängen und danach alle 30 Sekunden, solange die Seite sichtbar ist
 * (document.visibilityState). Ist sie verborgen, ruht der Abruf; beim Zurückkehren gilt er sofort, wenn der letzte Versuch
 * länger als 30 Sekunden her ist. Fehler lassen den letzten Stand stehen. 503 und "nicht erreichbar" warten mit wachsendem
 * Abstand (30, 60, 120 Sekunden bis höchstens 300), 401 und 403 hören auf, bis die Seite wieder sichtbar wird (Abstände:
 * bueroKarteModell.ts, abrufAbstand). Ein laufender Abruf wird mit jeder Instanz geteilt.
 */
export function useBueroStand(): BueroZustand {
  const [zustand, setZustand] = useState<BueroZustand>(() => {
    const m = frisch();
    // Ein Fehler vom letzten Besuch bleibt sichtbar, bis der nächste Abruf antwortet (bei wachsendem Abstand kann das dauern)
    return { stand: m?.stand ?? null, fehler: takt.fehler, geladen: m !== null || takt.fehler !== null };
  });

  useEffect(() => {
    let beendet = false;
    let wartet = false; // diese Instanz wartet auf einen Abruf
    let timer: ReturnType<typeof setTimeout> | null = null;
    const sichtbar = () => document.visibilityState === "visible";

    const planen = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (beendet || wartet) return;
      const warte = naechsterAbruf(takt.letzter, Date.now(), sichtbar(), abrufAbstand(takt));
      if (warte !== null) timer = setTimeout(holen, warte);
    };

    const holen = async () => {
      timer = null;
      if (beendet || wartet) return;
      wartet = true;
      const ergebnis = await abrufen();
      wartet = false;
      if (beendet) return;
      setZustand((alt) => anwenden(alt, ergebnis));
      planen();
    };

    const frischer = (ergebnis: Ergebnis) => {
      if (!beendet) setZustand((alt) => anwenden(alt, ergebnis));
    };
    hoerer.add(frischer);

    const beiSichtbarkeit = () => {
      if (sichtbar()) taktErneuern();
      planen();
    };

    taktErneuern();
    document.addEventListener("visibilitychange", beiSichtbarkeit);
    planen();
    return () => {
      beendet = true;
      hoerer.delete(frischer);
      document.removeEventListener("visibilitychange", beiSichtbarkeit);
      if (timer !== null) clearTimeout(timer);
    };
  }, []);

  return zustand;
}
