import test from "node:test";
import assert from "node:assert/strict";
import { BESTAETIGUNGSTEXT, DATENSCHUTZ_VERSION } from "../../wamo_labor_dienst/web/datenschutz-logik.js";

test("die Version des Datenschutzhinweises ist die Fassung 1", () => {
  assert.equal(DATENSCHUTZ_VERSION, 1);
  assert.ok(Number.isInteger(DATENSCHUTZ_VERSION) && DATENSCHUTZ_VERSION >= 1);
});

test("der Bestätigungstext nennt die Version aus der Konstante", () => {
  assert.equal(BESTAETIGUNGSTEXT, `Der Besucher hat den Datenschutzhinweis (Version ${DATENSCHUTZ_VERSION}) gelesen und bestätigt.`);
  assert.equal(BESTAETIGUNGSTEXT, "Der Besucher hat den Datenschutzhinweis (Version 1) gelesen und bestätigt.");
});
