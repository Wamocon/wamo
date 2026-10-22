// Tests für den Browser-Betrieb auf dem Server (lib/geraet.ts).
// Ausführen im Ordner ui/:  npm test   (node:test, ohne Browser)
import assert from "node:assert/strict";
import { test } from "node:test";
import { istServerBrowser, nurBueroBuild } from "./geraet.ts";

test("nur der Wert ja schaltet den Build für Sokrates-2 ein", () => {
  assert.equal(nurBueroBuild("ja"), true);
  assert.equal(nurBueroBuild(" JA "), true);
  for (const wert of [undefined, "", "nein", "1", "true", "an"]) assert.equal(nurBueroBuild(wert), false);
});

test("Server-Browser: Build für Sokrates-2 ohne Desktop-App", () => {
  assert.equal(istServerBrowser(true, false), true);
});

test("die Desktop-App bleibt auch in diesem Build eine Desktop-App", () => {
  assert.equal(istServerBrowser(true, true), false);
});

test("ohne Build für Sokrates-2 gilt nie der Server-Browser", () => {
  assert.equal(istServerBrowser(false, false), false);
  assert.equal(istServerBrowser(false, true), false);
});
