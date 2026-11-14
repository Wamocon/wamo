/**
 * Geführte Anleitung für die beiden Schlüsseltypen in der Karte Modellzugang (components/ModellZugang.tsx).
 * WAMO erzeugt nichts selbst: Ein API-Schlüssel und ein Claude-Code-Token entstehen nur mit der eigenen Anmeldung der
 * Person. Die Anleitung führt Schritt für Schritt dorthin und endet mit "Fertig, eintragen". Als Daten, damit die
 * Texte sich testen lassen (schluesselhilfe.test.ts) und nicht in der Komponente verstreut stehen.
 */

export type Schritt = {
  text: string;
  /** Teil des Textes, der als Link oder Befehl hervorgehoben wird */
  link?: { wort: string; url: string };
  befehl?: string;
};

export type Anleitung = {
  ueberschrift: string;
  schritte: Schritt[];
  /** Satz unter den Schritten: wofür der Schlüssel gilt */
  zweck: string;
};

export const CONSOLE_URL = "https://console.anthropic.com";

export const API_ANLEITUNG: Anleitung = {
  ueberschrift: "So erzeugst du einen API-Schlüssel",
  schritte: [
    { text: "Öffne die Anthropic-Konsole und melde dich mit deinem Konto an.", link: { wort: "console.anthropic.com", url: CONSOLE_URL } },
    { text: "Lege unter API-Schlüssel einen neuen Schlüssel an, Name zum Beispiel W.A.I.S." },
    { text: "Setze unter Limits ein monatliches Ausgabenlimit, damit nichts unbemerkt Kosten verursacht (empfohlen: 100 US-Dollar)." },
    { text: "Kopiere den Schlüssel. Er beginnt mit sk-ant-api und wird nur einmal angezeigt." },
  ],
  zweck: "Mit dem Schlüssel läuft das Gespräch auf Claude Haiku, bezahlt nach Verbrauch von deinem Guthaben.",
};

export const TOKEN_ANLEITUNG: Anleitung = {
  ueberschrift: "So erzeugst du den Claude-Code-Token",
  schritte: [
    { text: "Öffne auf deinem Laptop ein Terminal. Claude Code muss dort installiert und mit deinem Claude-Konto angemeldet sein." },
    { text: "Gib diesen Befehl ein und bestätige im Browser die Anmeldung:", befehl: "claude setup-token" },
    { text: "Kopiere den ausgegebenen Token. Er beginnt mit sk-ant-oat und wird nur einmal angezeigt." },
  ],
  zweck:
    "Der Token gilt nur für Entwicklungsaufträge mit Claude Code und läuft über das Kontingent deines Tarifs, nicht für das Gespräch. " +
    "W.A.I.S. prüft nur das Format, damit dein Kontingent nicht verbraucht wird.",
};

export type SchluesselArt = "api" | "code";

/**
 * Welche Art Schlüssel ein eingefügter Wert ist, allein am Anfang (nie wird der Wert weitergegeben):
 * sk-ant-oat… ist ein Claude-Code-Token, sk-ant-… sonst ein API-Schlüssel, alles andere null.
 */
export function erkenneSchluesselart(wert: string): SchluesselArt | null {
  const w = wert.trim();
  if (/^sk-ant-oat/i.test(w)) return "code";
  if (/^sk-ant-/i.test(w)) return "api";
  return null;
}

/** Hinweis, wenn ein Wert im falschen Feld steht (ohne den Wert zu nennen). null: passt oder nicht erkennbar. */
export function falschesFeld(feld: SchluesselArt, wert: string): { richtig: SchluesselArt; text: string; knopf: string } | null {
  const erkannt = erkenneSchluesselart(wert);
  if (!erkannt || erkannt === feld) return null;
  return erkannt === "code"
    ? {
        richtig: "code",
        text: "Das ist ein Claude-Code-Token, kein API-Schlüssel. Er gehört in das Feld Claude-Code-Token.",
        knopf: "Als Claude-Code-Token hinterlegen",
      }
    : {
        richtig: "api",
        text: "Das ist ein API-Schlüssel, kein Claude-Code-Token. Er gehört in das Feld Claude-API-Schlüssel.",
        knopf: "Als API-Schlüssel hinterlegen",
      };
}

/** Satz, der ausdrücklich sagt, dass WAMO nichts selbst erzeugt. */
export const NICHT_AUTOMATISCH =
  "W.A.I.S. kann das nicht für dich erledigen: Schlüssel und Token entstehen nur mit deiner eigenen Anmeldung.";
