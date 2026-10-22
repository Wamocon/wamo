import { parseServerMessage, type Modellzugang } from "./nachrichten.ts";

/**
 * Reine Hilfen für den Modellzugang ohne laufendes Gespräch (lib/modellzugangApi.ts): Antwort des Kerns prüfen und
 * Fehler in einen konkreten Satz übersetzen. Ohne Netz und ohne Browser, damit sie sich testen lassen.
 * Kein Satz enthält je einen Schlüssel; die Antwort des Kerns enthält nie einen.
 */

export type Antwort = Modellzugang & { type: "modellzugang" };

/**
 * So lange wartet die Karte auf die Antwort zu Schlüssel oder Modus: länger als die Zeitgrenze des Aufrufs am Kern
 * (20 s, lib/kernApi.ts), damit dessen konkrete Meldung ("Zeitüberschreitung", "nicht erreichbar") vor dem
 * allgemeinen Satz der Karte ankommt. Die Statusabfrage über den Datenkanal wartet kürzer.
 */
export const ANTWORT_MS = 25_000;
export const STATUS_MS = 15_000;

/** Die Antwort des Kerns als geprüfter Stand, oder null, wenn sie keine gültige Nachricht "modellzugang" ist. */
export function standAusAntwort(daten: unknown): Antwort | null {
  const m = parseServerMessage(daten);
  return m && m.type === "modellzugang" ? m : null;
}

/** Konkrete Meldung zu einem Fehler beim Aufruf des Kerns (Status 0 steht für Netz und Zeitgrenze). */
export function fehlerText(status: number, text: string): string {
  if (status === 0) {
    return text === "Zeitüberschreitung"
      ? "Der Kern hat nicht rechtzeitig geantwortet (Zeitüberschreitung). Bitte noch einmal versuchen."
      : "Der Kern ist nicht erreichbar. Bitte später noch einmal versuchen oder ein Gespräch starten.";
  }
  if (status === 401) return "Die Anmeldung ist abgelaufen oder ungültig. Bitte neu anmelden.";
  if (status === 403) return "Dieses Profil darf das hier nicht ändern (Anmeldung mit zweitem Faktor nötig).";
  if (status === 413) return "Die Eingabe ist zu lang für einen Schlüssel.";
  if (status === 429) return "Zu viele Versuche. Bitte in ein paar Minuten noch einmal versuchen.";
  if (status >= 500) return "Der Kern hat gerade ein Problem. Bitte später noch einmal versuchen.";
  // kernApi.ts setzt ohne Text des Kerns "Fehler <Status>"; das ist keine Auskunft, nur der Status zählt
  const konkret = text && !/^Fehler \d+$/.test(text);
  return konkret ? `Der Kern hat die Anfrage abgelehnt: ${text}` : `Der Kern hat die Anfrage abgelehnt (${status}).`;
}

/** Stand, der nur einen Fehler trägt (der Store behält den letzten bekannten Stand und hängt den Fehler an). */
export function fehlerStand(aktion: Modellzugang["aktion"], fehler: string): Antwort {
  return { type: "modellzugang", aktion, hinterlegt: false, festgelegt: false, codeHinterlegt: false, fehler };
}
