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

test("repo der Startkarte: owner/name wird übernommen, alles andere und Fehlendes ergibt kein repo und keinen Fehler", () => {
  assert.equal(auftrag({ repo: "acme/testrepo" }).repo, "acme/testrepo");
  assert.equal(auftrag({ repo: "Acme/Test-Repo.js_x" }).repo, "Acme/Test-Repo.js_x");
  assert.equal(auftrag().repo, undefined);
  for (const roh of [null, 5, true, {}, [], "", "   ", "kein-repo", "a/b/c", "../x", "a/b c", "https://github.com/a/b", "x".repeat(300)]) {
    assert.equal(auftrag({ repo: roh }).repo, undefined, JSON.stringify(roh));
  }
});

test("auftrag_quittung: ok, oder nicht ok mit bereinigtem Grund", () => {
  const ok = parseServerMessage({ type: "auftrag_quittung", job: "abc123", ok: true });
  assert.deepEqual(ok, { type: "auftrag_quittung", job: "abc123", ok: true, grund: undefined });
  const nein = parseServerMessage({ type: "auftrag_quittung", job: "abc123", ok: false, grund: "Der Kanal ist geschlossen.\n‮" });
  assert.ok(nein && nein.type === "auftrag_quittung");
  assert.equal(nein.ok, false);
  assert.equal(nein.grund, "Der Kanal ist geschlossen.");
  const lang = parseServerMessage({ type: "auftrag_quittung", job: "a", ok: false, grund: "x".repeat(5000) });
  assert.ok(lang && lang.type === "auftrag_quittung" && (lang.grund ?? "").length < 330);
});

test("auftrag_quittung: ungültige Formen werden ignoriert", () => {
  for (const roh of [
    { type: "auftrag_quittung" },
    { type: "auftrag_quittung", job: "", ok: true },
    { type: "auftrag_quittung", job: 5, ok: true },
    { type: "auftrag_quittung", job: "a", ok: "ja" },
    { type: "auftrag_quittung", job: "a" },
  ]) {
    assert.equal(parseServerMessage(roh), null, JSON.stringify(roh));
  }
});

test("entwicklung_grund: ein Zeichen außerhalb der Grundebene wird nie in der Mitte getrennt", () => {
  const BASIS = { type: "betrieb", modus: "buero", modelle: {}, pc: "aus", entwicklung: "aus" };
  // 119 einfache Zeichen, dann ein Emoji (zwei UTF-16-Einheiten): die Grenze von 120 Einheiten fällt in die Mitte des Zeichens
  const m = parseServerMessage({ ...BASIS, entwicklung_grund: `${"x".repeat(119)}😀 Rest` });
  assert.ok(m && m.type === "betrieb");
  const grund = m.entwicklungGrund ?? "";
  assert.ok(grund.length <= 120, String(grund.length));
  assert.doesNotMatch(grund, /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u, "kein einzelnes Ersatzzeichen");
  assert.equal(grund, "x".repeat(119));
  const kurz = parseServerMessage({ ...BASIS, entwicklung_grund: "Schalter aus 😀" });
  assert.ok(kurz && kurz.type === "betrieb");
  assert.equal(kurz.entwicklungGrund, "Schalter aus 😀", "ein kurzer Text bleibt ganz");
});

test("entwicklung_grund läuft durch die Bereinigung: Zeilenumbrüche, Steuer- und Bidi-Zeichen werden zu Leerzeichen", () => {
  const BASIS = { type: "betrieb", modus: "buero", modelle: {}, pc: "aus", entwicklung: "aus" };
  const m = parseServerMessage({ ...BASIS, entwicklung_grund: "Der Schalter\nist\taus‮ ​ ja\u0007" });
  assert.ok(m && m.type === "betrieb");
  assert.equal(m.entwicklungGrund, "Der Schalter ist aus ja");
});

test("auftrag_quittung: ein langer Grund wird nicht in der Mitte eines Zeichens getrennt", () => {
  const m = parseServerMessage({ type: "auftrag_quittung", job: "a", ok: false, grund: `${"y".repeat(299)}😀 Rest` });
  assert.ok(m && m.type === "auftrag_quittung");
  assert.doesNotMatch(m.grund ?? "", /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u);
});

test("stand gehört nicht zur Nachricht des Kerns: ein Feld stand aus dem Netz kommt nie in die Karte", () => {
  // Der Zustand der Startkarte entsteht allein in der Oberfläche (lib/auftragStart.ts), der Kern kann ihn nicht vorgeben
  const m = auftrag({ stand: { phase: "abgelehnt", grund: "vom Kern eingeschmuggelt" } });
  assert.equal("stand" in m, false);
  assert.equal((m as Record<string, unknown>).stand, undefined);
});

test("betrieb: entwicklung_grund ist optional, bereinigt und höchstens 120 Zeichen", () => {
  const BASIS = { type: "betrieb", modus: "buero", modelle: {}, pc: "aus", entwicklung: "aus" };
  const ohne = parseServerMessage(BASIS);
  assert.ok(ohne && ohne.type === "betrieb");
  assert.equal(ohne.entwicklungGrund, undefined);
  const mit = parseServerMessage({ ...BASIS, entwicklung_grund: "Der Schalter für Entwicklungsaufträge im Büro ist aus\u0000" });
  assert.ok(mit && mit.type === "betrieb");
  assert.equal(mit.entwicklungGrund, "Der Schalter für Entwicklungsaufträge im Büro ist aus");
  const lang = parseServerMessage({ ...BASIS, entwicklung_grund: "y".repeat(500) });
  assert.ok(lang && lang.type === "betrieb");
  assert.equal(lang.entwicklungGrund?.length, 120);
  for (const roh of [null, 7, "", "   ", {}, []]) {
    const m = parseServerMessage({ ...BASIS, entwicklung_grund: roh });
    assert.ok(m && m.type === "betrieb");
    assert.equal(m.entwicklungGrund, undefined, JSON.stringify(roh));
  }
});
