// Tests der Drosselung einer Anzeige (lib/drossel.ts): wie lange ein neuer Zwischenstand noch warten muss.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { drosselWartezeit } from "./drossel.ts";

test("ein Zwischenstand wartet den Rest des Abstands ab, nie länger", () => {
  assert.equal(drosselWartezeit(0, 250), 250);
  assert.equal(drosselWartezeit(100, 250), 150);
  assert.equal(drosselWartezeit(249, 250), 1);
});

test("ist der Abstand um, gilt der Stand sofort (0)", () => {
  assert.equal(drosselWartezeit(250, 250), 0);
  assert.equal(drosselWartezeit(10_000, 250), 0);
});

test("eine rückwärts gelaufene Uhr oder ein ungültiger Wert bringt nie eine Wartezeit über dem Abstand", () => {
  assert.equal(drosselWartezeit(-500, 250), 250);
  assert.equal(drosselWartezeit(Number.NaN, 250), 0);
  assert.equal(drosselWartezeit(100, Number.NaN), 0);
  assert.equal(drosselWartezeit(100, -5), 0);
});
