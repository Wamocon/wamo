// Tests für die Auswahl im Canvas (lib/canvasWahl.ts): Stelle eines Elements nach Kennung und der Wunsch "zeige dieses Ergebnis".
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { canvasIndex, canvasWunsch, canvasWaehlen } from "./canvasWahl.ts";

test("canvasIndex findet das Element mit der Kennung, sonst -1", () => {
  const items = [{ id: "a" }, { id: null }, {}, { id: "dev-42" }];
  assert.equal(canvasIndex(items, "dev-42"), 3);
  assert.equal(canvasIndex(items, "a"), 0);
  assert.equal(canvasIndex(items, "fehlt"), -1);
  assert.equal(canvasIndex(items, ""), -1); // leere Kennung trifft nie ein Element ohne Kennung
  assert.equal(canvasIndex([], "a"), -1);
});

test("canvasWaehlen setzt den Wunsch; derselbe Wunsch zweimal zählt als neuer Wunsch (der Zähler läuft hoch)", () => {
  const vorher = canvasWunsch.get();
  canvasWaehlen("dev-42");
  const eins = canvasWunsch.get();
  assert.equal(eins.id, "dev-42");
  assert.equal(eins.n, vorher.n + 1);
  canvasWaehlen("dev-42");
  const zwei = canvasWunsch.get();
  assert.equal(zwei.id, "dev-42");
  assert.equal(zwei.n, eins.n + 1);
});
