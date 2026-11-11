// Entscheidungslogik der Startkarte eines Entwicklungsauftrags (Servernachricht "auftrag", Antwort "auftrag_quittung").
// Reine Funktionen ohne Uhr und ohne React: Die Uhrzeit kommt von außen, der Zustand steht in Auftrag.stand.
//
// Ablauf: Starten sendet die Entscheidung, die Karte bleibt und wartet. Der Kern antwortet mit einer Quittung:
//   ok         der Auftrag ist angenommen, die Karte verschwindet ("gestartet" erst jetzt)
//   nicht ok   mit einem Satz als Grund; die Karte bleibt stehen, Starten geht erneut
// Kommt nach 15 Sekunden nichts (ein Kern ohne Quittung, eine hängende Verbindung), zeigt die Karte einen neutralen Hinweis:
// Der Auftrag kann trotzdem laufen, die Person sieht unter Vorgänge nach. Eine spätere Quittung gilt wie jede andere.
import type { Auftrag, Vorgang } from "./nachrichten.ts";

/** So lange wartet die Karte auf die Quittung des Kerns, bevor sie den neutralen Hinweis zeigt. */
export const QUITTUNG_FRIST_MS = 15_000;

export type AuftragStand =
  | { phase: "wartet"; seitMs: number }
  | { phase: "ohneQuittung"; seitMs: number }
  | { phase: "abgelehnt"; grund: string };

export type Quittung = { job: string; ok: boolean; grund?: string };

const ALLGEMEINER_GRUND = "Der Auftrag ist nicht gestartet.";
export const HINWEIS_OHNE_QUITTUNG =
  "Noch keine Rückmeldung vom Kern. Der Auftrag kann trotzdem laufen, schau unter Vorgänge nach.";

const bezeichnung = (a: Auftrag) => a.art || a.projekt || "Auftrag";

/** Klick auf Starten: die Karte wartet auf die Quittung. Ein zweiter Klick während des Wartens ändert nichts. */
export function auftraegeNachStart(auftraege: Auftrag[], job: string, jetztMs: number): Auftrag[] {
  const i = auftraege.findIndex((a) => a.job === job);
  if (i < 0 || auftraege[i].stand?.phase === "wartet") return auftraege;
  return auftraege.map((a, k) => (k === i ? { ...a, stand: { phase: "wartet", seitMs: jetztMs } } : a));
}

/**
 * Quittung des Kerns. ok: die Karte verschwindet und die Ansage sagt "gestartet". Nicht ok: die Karte bleibt mit dem Grund.
 * Eine unbekannte Kennung ändert nichts (dieselbe Liste, keine Ansage).
 */
export function auftraegeNachQuittung(
  auftraege: Auftrag[],
  q: Quittung,
): { auftraege: Auftrag[]; ansage: string | null } {
  const karte = auftraege.find((a) => a.job === q.job);
  if (!karte) return { auftraege, ansage: null };
  if (q.ok) {
    return { auftraege: auftraege.filter((a) => a.job !== q.job), ansage: `Auftrag gestartet: ${bezeichnung(karte)}` };
  }
  const grund = q.grund?.trim() || ALLGEMEINER_GRUND;
  // Keine Ansage: den Grund sagt die Karte selbst an (role="alert" in Freigaben.tsx), zweimal wäre er doppelt zu hören
  return {
    auftraege: auftraege.map((a) => (a.job === q.job ? { ...a, stand: { phase: "abgelehnt", grund } } : a)),
    ansage: null,
  };
}

/** Nach der Frist ohne Quittung: wartende Karten zeigen den neutralen Hinweis. Ohne Änderung kommt dieselbe Liste zurück. */
export function auftraegeNachFrist(auftraege: Auftrag[], jetztMs: number): Auftrag[] {
  let geaendert = false;
  const neu = auftraege.map((a) => {
    const s = a.stand;
    if (s?.phase === "wartet" && jetztMs - s.seitMs >= QUITTUNG_FRIST_MS) {
      geaendert = true;
      const stand: AuftragStand = { phase: "ohneQuittung", seitMs: s.seitMs };
      return { ...a, stand };
    }
    return a;
  });
  return geaendert ? neu : auftraege;
}

/** Nach dem Senden liegt der Auftrag vielleicht schon beim Kern: Der Kern ignoriert "nein" bei einem gestarteten Auftrag, und es gibt
 *  keinen Abbruchweg außer dem Auflegen. Darum geht Verwerfen in diesen Phasen nicht, und die Karte sagt das. */
export const HINWEIS_NICHT_VERWERFBAR =
  "Der Auftrag ist gesendet und lässt sich nicht mehr verwerfen. Zum Beenden das Gespräch auflegen.";

export type KartenAnsicht = {
  /** Starten ist gesperrt: der Kern meldet Entwicklung aus, oder die Antwort auf den Klick steht noch aus */
  startGesperrt: boolean;
  /** Verwerfen ist gesperrt: der Auftrag ist gesendet und kann beim Kern schon laufen */
  verwerfenGesperrt: boolean;
  startText: string;
  /** Hinweis zum Stand: gesendet und nicht mehr verwerfbar, nach 15 Sekunden ohne Quittung zusätzlich der neutrale Hinweis */
  hinweis: string | null;
  /** Grund des Kerns, warum der Auftrag nicht gestartet ist */
  grund: string | null;
};

/** Was die Karte zeigt. entwicklungAus: der Kern meldet betrieb.entwicklung "aus". Eine Karte ohne Stand ist die bisherige. */
export function kartenAnsicht(a: Auftrag, entwicklungAus: boolean): KartenAnsicht {
  const s = a.stand;
  switch (s?.phase) {
    case "wartet":
      return { startGesperrt: true, verwerfenGesperrt: true, startText: "Wird gestartet …", hinweis: HINWEIS_NICHT_VERWERFBAR, grund: null };
    case "ohneQuittung":
      return {
        startGesperrt: entwicklungAus,
        verwerfenGesperrt: true,
        startText: "Noch einmal senden",
        hinweis: `${HINWEIS_OHNE_QUITTUNG} ${HINWEIS_NICHT_VERWERFBAR}`,
        grund: null,
      };
    case "abgelehnt":
      return { startGesperrt: entwicklungAus, verwerfenGesperrt: false, startText: "Erneut starten", hinweis: null, grund: s.grund };
    default:
      return { startGesperrt: entwicklungAus, verwerfenGesperrt: false, startText: "Starten", hinweis: null, grund: null };
  }
}

/** Darf diese Karte verworfen werden? Nein, wenn sie unbekannt ist oder der Auftrag gesendet wurde (wartet, ohne Quittung). */
export function auftragVerwerfbar(auftraege: readonly Auftrag[], job: string): boolean {
  const karte = auftraege.find((a) => a.job === job);
  return !!karte && !kartenAnsicht(karte, false).verwerfenGesperrt;
}

/** Darf für diese Karte gesendet werden? Nein, wenn sie unbekannt ist oder die Antwort auf den ersten Klick noch aussteht. */
export function auftragStartbar(auftraege: readonly Auftrag[], job: string): boolean {
  const karte = auftraege.find((a) => a.job === job);
  return !!karte && karte.stand?.phase !== "wartet";
}

/** Das Repo der Karte als Text, oder null (ein Kern ohne Feld repo, Recherche ohne Repo). */
export function repoAnzeige(a: Pick<Auftrag, "repo">): string | null {
  return typeof a.repo === "string" && a.repo.trim() ? a.repo.trim() : null;
}

// Ohne "geplant": Der Kern legt den Vorgang schon mit der Karte an (voice-core/vorgang_stream.py), dann ist noch nichts gestartet.
const LAUFENDE_VORGAENGE: readonly Vorgang["status"][] = ["laeuft", "wartet_freigabe", "pruefen"];

/**
 * Läuft gerade ein Auftrag, der beim Neuladen verloren ginge? Ja, wenn ein Entwicklungsvorgang läuft oder eine Karte gesendet
 * wurde und noch keine Antwort hat. Eine Karte, die nur wartet (Vorgang geplant), verliert beim Neuladen nichts.
 */
export function auftragLaeuft(vorgaenge: readonly Pick<Vorgang, "art" | "status">[], auftraege: readonly Auftrag[]): boolean {
  return (
    vorgaenge.some((v) => v.art === "entwicklung" && LAUFENDE_VORGAENGE.includes(v.status)) ||
    auftraege.some((a) => a.stand?.phase === "wartet" || a.stand?.phase === "ohneQuittung")
  );
}
