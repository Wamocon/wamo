import type { Betrieb, ModellModus, Modellzugang } from "@/lib/nachrichten";

/**
 * Modellwahl je Profil (WAMO Boss): Automatisch oder Nur Hausmodelle. Reine Funktionen für Texte und Zustände der
 * Karte Modellzugang und der Statusleiste, damit sie sich ohne Oberfläche testen lassen (modellwahl.test.ts).
 */

export const MODI: readonly { id: ModellModus; name: string }[] = [
  { id: "automatisch", name: "Automatisch" },
  { id: "nur_haus", name: "Nur Hausmodelle" },
];

export function modusName(modus: ModellModus | undefined): string {
  return MODI.find((m) => m.id === modus)?.name ?? "unbekannt";
}

/** Was die Person gewählt hat; ein Zwang des Admins zählt als Nur Hausmodelle. Ohne Meldung des Kerns: Automatisch. */
export function gewaehlterModus(z: Modellzugang | null): ModellModus {
  if (z?.festgelegt) return "nur_haus";
  return z?.modus ?? "automatisch";
}

export function modusErklaerung(modus: ModellModus, hatApiSchluessel: boolean): string {
  if (modus === "nur_haus") {
    return (
      "Gespräch, Lernen, Bildschirm und Aufgaben laufen nur auf den Modellen im Haus. Es geht nichts an Anthropic, " +
      "und Entwicklungsaufträge mit Claude Code sind aus. Hören und Stimme laufen vorerst weiter über Soniox, bis die " +
      "Hausstimmen aktiv sind."
    );
  }
  return hatApiSchluessel
    ? "W.A.I.S. wählt für jede Aufgabe das beste erlaubte Modell: im Gespräch Claude Haiku mit deinem Schlüssel, andere " +
        "Aufgaben bevorzugt im Haus."
    : "W.A.I.S. wählt für jede Aufgabe das beste Modell. Ohne eigenen API-Schlüssel sind das die Hausmodelle.";
}

/**
 * Satz unter dem Umschalter. Die Wahl gilt immer erst ab der nächsten Sitzung; weicht sie von dem ab, was in der
 * laufenden Sitzung gilt, sagt der Satz es ausdrücklich.
 */
export function wirksamText(z: Modellzugang | null, betrieb: Betrieb | null): string {
  if (z?.festgelegt) return "Ein Admin hat dein Profil auf Hausmodelle festgelegt. Die Wahl ist gesperrt.";
  const gewaehlt = gewaehlterModus(z);
  const laeuft = betrieb?.modellModus;
  if (laeuft && laeuft !== gewaehlt) {
    return `Gewählt: ${modusName(gewaehlt)}. Dieses Gespräch läuft noch im Modus ${modusName(laeuft)}; die Wahl gilt ab der nächsten Sitzung.`;
  }
  return "Die Wahl gilt ab der nächsten Sitzung.";
}

/** Kurzform für die Statusleiste: "Automatisch", "Nur Hausmodelle" oder mit Grund ("vom Admin festgelegt"). */
export function modusKurz(betrieb: Betrieb | null): { text: string; grund?: string } | null {
  const modus = betrieb?.modellModus;
  if (!modus) return null;
  if (modus === "automatisch") return { text: modusName(modus) };
  const grund =
    betrieb.modellQuelle === "festgelegt"
      ? "vom Admin festgelegt"
      : betrieb.modellQuelle === "wahl"
        ? "von dir gewählt"
        : betrieb.modellQuelle === "keiner"
          ? "kein eigener Schlüssel"
          : betrieb.modellQuelle === "fehler"
            ? "Zugang nicht lesbar"
            : undefined;
  return { text: modusName(modus), grund };
}

/** Nächster Eintrag bei Pfeiltasten in der Radiogruppe (mit Umlauf). */
export function nachbar(index: number, taste: string, anzahl: number): number | null {
  if (taste === "ArrowRight" || taste === "ArrowDown") return (index + 1) % anzahl;
  if (taste === "ArrowLeft" || taste === "ArrowUp") return (index - 1 + anzahl) % anzahl;
  if (taste === "Home") return 0;
  if (taste === "End") return anzahl - 1;
  return null;
}
