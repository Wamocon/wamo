// Anzeige der Abnahmekriterien auf der Startkarte eines Entwicklungsauftrags (Servernachricht "auftrag", Feld "abnahme").
// Die Person bestätigt mit dem Klick auf "Starten" auch die Befehle, die WAMO nach jeder Runde ausführt, also muss sie sie sehen.
// Nur Zerlegung von Text: Die Komponente setzt ihn als Text ein (nie als Markup).

export type AbnahmeEintrag = { art: "befehl" | "aussage"; text: string };

// Wie im Kern (voice-core/abnahme.py): Präfix "befehl:" ohne Rücksicht auf Groß- und Kleinschreibung und Leerraum
const BEFEHL = /^\s*befehl\s*:\s*(\S[\s\S]*)$/i;

export function abnahmeEintraege(abnahme: readonly string[]): AbnahmeEintrag[] {
  return abnahme.map((roh) => {
    const treffer = BEFEHL.exec(roh);
    return treffer ? { art: "befehl", text: treffer[1].trim() } : { art: "aussage", text: roh.trim() };
  });
}
