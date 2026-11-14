import assert from "node:assert/strict";
import test from "node:test";
import { ANZEIGENAME, LANGFORM, LANGFORM_DEUTSCH } from "./anzeigename.ts";

test("der Anzeigename wird mit Schlusspunkt geschrieben", () => {
  assert.equal(ANZEIGENAME, "W.A.I.S.");
});

test("die Langform nutzt den typografischen Apostroph, nicht den geraden", () => {
  assert.equal(LANGFORM, "WAMOCON’s Artificial Intelligence System");
  assert.ok(!LANGFORM.includes("'"));
});

test("die deutsche Fassung nennt das künstliche intelligente System", () => {
  assert.equal(LANGFORM_DEUTSCH, "WAMOCONs künstliches intelligentes System");
});
