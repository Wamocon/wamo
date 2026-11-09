// Tests für die Servernachricht "auftrag" (lib/nachrichten.ts): die Startkarte zeigt die Abnahmekriterien,
// damit die Person Befehle bestätigt, die sie sieht. Feste Felder, Längengrenzen, bereinigte Texte.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { abnahmeEintraege } from "./auftragAbnahme.ts";
import { AUFTRAG_ABNAHME_MAX, AUFTRAG_ABNAHME_ZEICHEN, parseServerMessage } from "./nachrichten.ts";

const GUT = { type: "auftrag", job: "abc123", projekt: "rezepte-app", art: "umsetzung", auftrag: "Dokumentiere die Rezepte-App" };

function auftrag(extra: Record<string, unknown> = {}) {
  const m = parseServerMessage({ ...GUT, ...extra });
  assert.ok(m && m.type === "auftrag", JSON.stringify(extra));
  return m;
}

test("ohne abnahme bleibt die Karte wie bisher und die Liste ist leer", () => {
  const m = auftrag();
  assert.deepEqual(m.abnahme, []);
  assert.equal(m.job, "abc123");
  assert.equal(m.projekt, "rezepte-app");
  assert.equal(m.art, "umsetzung");
});

test("die Kriterien kommen in der Reihenfolge des Kerns mit", () => {
  const m = auftrag({ abnahme: ["Die README erklärt die Installation", "befehl: pytest -q"] });
  assert.deepEqual(m.abnahme, ["Die README erklärt die Installation", "befehl: pytest -q"]);
});

test("höchstens fünf Kriterien, je höchstens 300 Zeichen", () => {
  assert.equal(AUFTRAG_ABNAHME_MAX, 5);
  assert.equal(AUFTRAG_ABNAHME_ZEICHEN, 300);
  const m = auftrag({ abnahme: Array.from({ length: 9 }, (_, i) => `Kriterium ${i + 1}`) });
  assert.equal(m.abnahme.length, 5);
  assert.equal(m.abnahme[4], "Kriterium 5");
  const lang = auftrag({ abnahme: ["x".repeat(5000)] });
  assert.ok(lang.abnahme[0].startsWith("x".repeat(300)));
  assert.ok(lang.abnahme[0].length < 330, "gekürzt mit Hinweis, nie 5000 Zeichen");
  assert.ok(lang.abnahme[0].endsWith("(gekürzt)"));
});

test("absurd lange Listen werden nicht durchlaufen und liefern trotzdem höchstens fünf Einträge", () => {
  const riesig = Array.from({ length: 200_000 }, (_, i) => `K${i}`);
  assert.equal(auftrag({ abnahme: riesig }).abnahme.length, 5);
  // nicht-Texte vorneweg zählen nicht als Kriterium
  assert.deepEqual(auftrag({ abnahme: [1, null, {}, ["x"], "A", "B"] }).abnahme, ["A", "B"]);
});

test("falsche Formen ergeben eine leere Liste, nie einen Fehler", () => {
  for (const roh of [null, "text", 5, true, {}, { 0: "x" }, [], [""], ["   "], [null]]) {
    assert.deepEqual(auftrag({ abnahme: roh }).abnahme, [], JSON.stringify(roh));
  }
});

test("Steuer-, Bidi- und Nullbreitenzeichen und Zeilenumbrüche werden entfernt", () => {
  const m = auftrag({ abnahme: ["befehl:‮ pytest\u0000 -q\n", "Zeile eins\nZeile zwei​"] });
  assert.equal(m.abnahme[0], "befehl: pytest -q");
  assert.equal(m.abnahme[1], "Zeile eins Zeile zwei");
  for (const k of m.abnahme) assert.doesNotMatch(k, /[\u0000-\u0008\u000A-\u001F​-‏‪-‮]/);
});

test("HTML bleibt Text und wird nicht verändert (die Anzeige setzt nie Markup ein)", () => {
  const m = auftrag({ abnahme: ['Die Seite zeigt <b onclick="x()">fett</b> & <script>alert(1)</script>'] });
  assert.equal(m.abnahme[0], 'Die Seite zeigt <b onclick="x()">fett</b> & <script>alert(1)</script>');
});

test("ein Auftrag ohne Kennung oder Text ist weiter ungültig", () => {
  assert.equal(parseServerMessage({ ...GUT, job: "" }), null);
  assert.equal(parseServerMessage({ ...GUT, auftrag: "  " }), null);
  assert.equal(parseServerMessage({ ...GUT, abnahme: ["x"], job: 5 }), null);
});

test("Anzeige: Befehle werden als Befehl erkannt, alles andere ist eine Aussage", () => {
  assert.deepEqual(abnahmeEintraege(["befehl: pytest -q", "Die README erklärt die Installation"]), [
    { art: "befehl", text: "pytest -q" },
    { art: "aussage", text: "Die README erklärt die Installation" },
  ]);
  assert.deepEqual(abnahmeEintraege(["  BEFEHL :  npm test "]), [{ art: "befehl", text: "npm test" }]);
  assert.deepEqual(abnahmeEintraege([]), []);
});

test("Anzeige: ein Eintrag, der nur 'befehl:' heißt, bleibt sichtbar", () => {
  assert.deepEqual(abnahmeEintraege(["befehl:"]), [{ art: "aussage", text: "befehl:" }]);
});

test("Anzeige: HTML in einem Befehl bleibt unverändert als Text (React setzt es nie als Markup ein)", () => {
  assert.deepEqual(abnahmeEintraege(["befehl: grep -q <img src=x onerror=alert(1)> a.txt"]), [
    { art: "befehl", text: "grep -q <img src=x onerror=alert(1)> a.txt" },
  ]);
});
