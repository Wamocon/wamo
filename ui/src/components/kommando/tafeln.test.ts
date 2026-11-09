// Tests, wann eine rechte Tafel im Bild steht (kommando/tafeln.ts). Nur dann hängt der Rahmen ihren Inhalt ein.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { tafelSichtbar, type TafelLage } from "./tafeln.ts";

const lage = (extra: Partial<TafelLage> = {}): TafelLage => ({
  einstellungen: false,
  breit: true,
  effektiv: "normal",
  rechts: "auftraege",
  mobilEff: "kern",
  ...extra,
});

test("breit: nur der gewählte rechte Reiter ist sichtbar", () => {
  assert.equal(tafelSichtbar(lage({ rechts: "vorgaenge" }), "vorgaenge"), true);
  assert.equal(tafelSichtbar(lage({ rechts: "vorgaenge" }), "buero"), false);
  assert.equal(tafelSichtbar(lage({ rechts: "buero" }), "buero"), true);
  assert.equal(tafelSichtbar(lage({ rechts: "buero" }), "vorgaenge"), false);
  assert.equal(tafelSichtbar(lage({ rechts: "erledigt" }), "vorgaenge"), false);
});

test("breit: in Fokus und Zentrale ist die rechte Spalte verborgen, in Nacht nicht", () => {
  for (const effektiv of ["fokus", "zentrale"] as const) {
    assert.equal(tafelSichtbar(lage({ effektiv, rechts: "vorgaenge" }), "vorgaenge"), false, effektiv);
    assert.equal(tafelSichtbar(lage({ effektiv, rechts: "buero" }), "buero"), false, effektiv);
  }
  assert.equal(tafelSichtbar(lage({ effektiv: "nacht", rechts: "vorgaenge" }), "vorgaenge"), true);
});

test("schmal: es zählt der wirksame mobile Reiter, nicht der rechte", () => {
  assert.equal(tafelSichtbar(lage({ breit: false, rechts: "vorgaenge", mobilEff: "kern" }), "vorgaenge"), false);
  assert.equal(tafelSichtbar(lage({ breit: false, rechts: "auftraege", mobilEff: "vorgaenge" }), "vorgaenge"), true);
  assert.equal(tafelSichtbar(lage({ breit: false, mobilEff: "buero" }), "buero"), true);
  assert.equal(tafelSichtbar(lage({ breit: false, mobilEff: "buero" }), "vorgaenge"), false);
  assert.equal(tafelSichtbar(lage({ breit: false, mobilEff: "canvas" }), "buero"), false);
});

test("unter den Einstellungen ist nie eine Tafel sichtbar", () => {
  assert.equal(tafelSichtbar(lage({ einstellungen: true, rechts: "vorgaenge" }), "vorgaenge"), false);
  assert.equal(tafelSichtbar(lage({ einstellungen: true, breit: false, mobilEff: "buero" }), "buero"), false);
});
