import { istLaborKonto } from "./stillStart.ts";

/**
 * Platz-Ansicht der Laborkonten (Testlabor): Besucher bedienen WAMO an einem Platz mit einem Laborkonto (labor-NN, dazu das Schaufenster
 * labor-schau, scripts/wamo_labor.py). Sie sehen eine schlanke Oberfläche: keine Büro-Karte, kein Verlauf, keine Vorgänge, keine
 * Admin-Hinweise, keine Geräteeinrichtung, keinen Modellzugang, keine Stimmerkennung (kein Modul, kein Einlernen), von den Einstellungen nur das Mikrofon, und "Abmelden" erst nach einer
 * Rückfrage. Das ist reine Darstellung: Der Kern sperrt weiter selbst (Büro-Karte, Werkzeuge, Verlauf), die Oberfläche entscheidet nichts,
 * was Sicherheit wäre. Ob eine Mail ein Laborkonto ist, sagt allein istLaborKonto (lib/stillStart.ts); hier steht keine zweite Regel dafür.
 * Ohne React und ohne Browser, damit sich die Logik mit node:test prüfen lässt (laborPlatz.test.ts).
 */

export type LaborAnsicht = Readonly<{
  /** Laborkonto (Platz oder Schaufenster) */
  labor: boolean;
  /** Platz 1 bis 16; das Schaufenster und jedes normale Konto haben keinen */
  platz: number | null;
  /** Überschrift der Kopfzeile; null: die gewohnte Wortmarke "WAMO" */
  kopfTitel: string | null;
  bueroKarte: boolean;
  /** Verlauf (Schublade, Knöpfe und Abruf) */
  verlauf: boolean;
  /** Tafel Vorgänge samt Zeitleiste der Werkzeuge */
  vorgaenge: boolean;
  /** Hinweise an Admins (Geräte, neue Anfragen) samt Zähler */
  adminHinweise: boolean;
  /** Einrichtung des eigenen Laptops (Desktop-App) samt Zeile in den Einstellungen */
  geraeteEinrichtung: boolean;
  /** Stand und Abruf des Modellzugangs */
  modellzugang: boolean;
  /** Stimmerkennung: Modul in der Kopfzeile, Einlernansicht und Abschnitt Stimme in den Einstellungen */
  stimme: boolean;
  /** alle Abschnitte oder nur der mit der Mikrofonwahl */
  einstellungen: "alle" | "mikrofon";
  /** "Abmelden" fragt erst nach */
  abmeldenFragen: boolean;
}>;

const VOLL: LaborAnsicht = Object.freeze({
  labor: false,
  platz: null,
  kopfTitel: null,
  bueroKarte: true,
  verlauf: true,
  vorgaenge: true,
  adminHinweise: true,
  geraeteEinrichtung: true,
  modellzugang: true,
  stimme: true,
  einstellungen: "alle",
  abmeldenFragen: false,
});

/** Platz 1 bis 16 aus der Kennung labor-NN; das Schaufenster (labor-schau) und alles, was kein Laborkonto ist, hat keine Nummer. */
export function laborPlatzNummer(email: string | null | undefined): number | null {
  if (!istLaborKonto(email)) return null;
  const nummer = /^labor-(\d\d)@/.exec((email as string).trim().toLowerCase());
  return nummer ? Number(nummer[1]) : null;
}

/** Welche Teile der Oberfläche gelten für dieses Konto? Ein normales Konto (auch ohne E-Mail) behält die volle Ansicht. */
export function laborAnsicht(email: string | null | undefined): LaborAnsicht {
  if (!istLaborKonto(email)) return VOLL;
  const platz = laborPlatzNummer(email);
  return Object.freeze({
    labor: true,
    platz,
    kopfTitel: platz === null ? "W.A.I.S. Testlabor" : `W.A.I.S. Testlabor, Platz ${platz}`,
    bueroKarte: false,
    verlauf: false,
    vorgaenge: false,
    adminHinweise: false,
    geraeteEinrichtung: false,
    modellzugang: false,
    stimme: false,
    einstellungen: "mikrofon",
    abmeldenFragen: true,
  });
}
