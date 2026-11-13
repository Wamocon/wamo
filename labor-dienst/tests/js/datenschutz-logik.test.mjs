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

import { einwilligungKoerper, neuerDurchlaufFreigabe, versionVeraltet } from "../../wamo_labor_dienst/web/datenschutz-logik.js";

test("neuerDurchlaufFreigabe verlangt die gesetzte Checkbox und die eingetippte Platznummer", () => {
  assert.equal(neuerDurchlaufFreigabe(true, "3", 3), true);
  assert.equal(neuerDurchlaufFreigabe(true, " 16 ", 16), true);
  assert.equal(neuerDurchlaufFreigabe(false, "3", 3), false, "ohne Häkchen kein Neuer Durchlauf");
  assert.equal(neuerDurchlaufFreigabe(true, "4", 3), false, "falsche Platznummer");
  assert.equal(neuerDurchlaufFreigabe(true, "", 3), false);
  for (const kein of [undefined, null, "true", 1, "on", {}]) assert.equal(neuerDurchlaufFreigabe(kein, "3", 3), false, String(kein));
});

test("versionVeraltet meldet, wenn der Dienst schon eine höhere Version erfasst hat", () => {
  assert.equal(versionVeraltet(0), false);
  assert.equal(versionVeraltet(1), false);
  assert.equal(versionVeraltet(2), true);
  assert.equal(versionVeraltet(null), false);
  assert.equal(versionVeraltet("9"), false);
});

test("einwilligungKoerper sendet genau die Version der Konstante", () => {
  assert.deepEqual(einwilligungKoerper(), { einwilligung_version: DATENSCHUTZ_VERSION });
  assert.notEqual(einwilligungKoerper(), einwilligungKoerper(), "jedes Mal ein neues Objekt");
});
