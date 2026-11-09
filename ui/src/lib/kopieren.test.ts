// Tests für die Bestätigung nach dem Kopieren im Canvas (lib/kopieren.ts). Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { KOPIERT_MS, kopierChipText } from "./kopieren.ts";

test("die Bestätigung steht 1,5 Sekunden da", () => {
  assert.equal(KOPIERT_MS, 1500);
});

test("kopierChipText: Kopiert oder Nicht kopiert, im Ruhezustand leer (die Live-Region bleibt im DOM)", () => {
  assert.equal(kopierChipText("kopiert"), "Kopiert");
  assert.equal(kopierChipText("fehler"), "Nicht kopiert");
  assert.equal(kopierChipText(""), "");
});
