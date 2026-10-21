// Tests für die Wahl der Darstellung (kommando/darstellung.ts): gültige Werte, gesperrter Speicher.
// Ausführen im Ordner ui/:  npm test   (node:test, Node lädt TypeScript selbst)
import assert from "node:assert/strict";
import { test } from "node:test";
import { DARSTELLUNG_SCHLUESSEL, darstellungLesen, darstellungSchreiben, istDarstellung } from "./darstellung.ts";

function speicher(start: Record<string, string> = {}) {
  const daten = new Map(Object.entries(start));
  return {
    getItem: (k: string) => daten.get(k) ?? null,
    setItem: (k: string, v: string) => void daten.set(k, v),
  };
}

const gesperrt = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("SecurityError");
  },
};

test("nur bekannte Darstellungen gelten", () => {
  for (const d of ["normal", "fokus", "zentrale", "nacht"]) assert.equal(istDarstellung(d), true);
  for (const d of ["", "Normal", "brevier", null, undefined, 3]) assert.equal(istDarstellung(d), false);
});

test("lesen: gespeicherter Wert, sonst normal", () => {
  assert.equal(darstellungLesen(speicher({ [DARSTELLUNG_SCHLUESSEL]: "nacht" })), "nacht");
  assert.equal(darstellungLesen(speicher({ [DARSTELLUNG_SCHLUESSEL]: "unsinn" })), "normal");
  assert.equal(darstellungLesen(speicher()), "normal");
  assert.equal(darstellungLesen(null), "normal");
});

test("gesperrter Speicher wirft nicht", () => {
  assert.equal(darstellungLesen(gesperrt), "normal");
  assert.equal(darstellungSchreiben(gesperrt, "fokus"), false);
  assert.equal(darstellungSchreiben(null, "fokus"), false);
});

test("schreiben und wieder lesen", () => {
  const sp = speicher();
  assert.equal(darstellungSchreiben(sp, "zentrale"), true);
  assert.equal(darstellungLesen(sp), "zentrale");
});
