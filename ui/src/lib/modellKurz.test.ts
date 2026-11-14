// Test für die Kurzform der Modellnamen in der Kopfzeile (lib/modellKurz.ts): ganze Wörter, nie ein Schnitt mitten im Wort.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { modellKurzname } from "./modellKurz.ts";

test("kurze Namen bleiben, wie sie sind", () => {
  assert.equal(modellKurzname("Haiku 5.5"), "Haiku 5.5");
  assert.equal(modellKurzname("Hermes"), "Hermes");
});

test("die Herstellerangabe Claude entfällt, die Familie und die Version bleiben", () => {
  assert.equal(modellKurzname("Claude Haiku 5.5"), "Haiku 5.5");
  assert.equal(modellKurzname("claude opus 4.7"), "opus 4.7");
});

test("zu lange Namen verlieren ganze Wörter von hinten, nie Buchstaben", () => {
  assert.equal(modellKurzname("Claude Sonnet 5.5 Vorschau Langform", 14), "Sonnet 5.5");
  assert.equal(modellKurzname("Qwen3-235B-A22B-Instruct-2507", 14), "Qwen3-235B");
});

test("ein einzelnes Wort über der Grenze bleibt ganz", () => {
  assert.equal(modellKurzname("Superlangermodellname", 8), "Superlangermodellname");
});

test("Leerraum und leere Angaben ergeben einen leeren Text, der Aufrufer setzt seinen Ersatz", () => {
  assert.equal(modellKurzname("   "), "");
  assert.equal(modellKurzname(null), "");
  assert.equal(modellKurzname(undefined), "");
});

test("endet die Kürzung an einem Trennzeichen, bleibt keines hängen", () => {
  assert.equal(modellKurzname("Alpha - Beta Gamma Delta Epsilon", 8), "Alpha");
});
