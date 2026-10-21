// Tests für die Berechtigungen der Desktop-App (berechtigungen.js). Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  anfrageErlaubt,
  herkunft,
  oberflaechenZeile,
  protokollieren,
  protokollZeile,
  pruefungErlaubt,
} = require("./berechtigungen");

test("Mikrofon-Anfrage der eigenen Oberfläche: nur Audio", () => {
  assert.equal(anfrageErlaubt("media", { mediaTypes: ["audio"] }, true), true);
  assert.equal(anfrageErlaubt("media", { mediaTypes: ["audio", "video"] }, true), false);
  assert.equal(anfrageErlaubt("media", { mediaTypes: ["video"] }, true), false);
  assert.equal(anfrageErlaubt("media", { mediaTypes: [] }, true), false);
  assert.equal(anfrageErlaubt("media", {}, true), false);
  assert.equal(anfrageErlaubt("media", undefined, true), false);
});

test("fremde Seiten bekommen nichts, auch kein Audio", () => {
  assert.equal(anfrageErlaubt("media", { mediaTypes: ["audio"] }, false), false);
  assert.equal(pruefungErlaubt("media", { mediaType: "audio" }, false), false);
  assert.equal(anfrageErlaubt("clipboard-sanitized-write", {}, false), false);
});

test("andere Berechtigungen: nur Schreiben in die Zwischenablage", () => {
  assert.equal(anfrageErlaubt("clipboard-sanitized-write", {}, true), true);
  for (const p of ["notifications", "geolocation", "clipboard-read", "display-capture", "openExternal"]) {
    assert.equal(anfrageErlaubt(p, {}, true), false, p);
    assert.equal(pruefungErlaubt(p, {}, true), false, p);
  }
});

test("Prüfung: Audio ja, Video und unbekannt nein", () => {
  assert.equal(pruefungErlaubt("media", { mediaType: "audio" }, true), true);
  assert.equal(pruefungErlaubt("media", { mediaType: "video" }, true), false);
  assert.equal(pruefungErlaubt("media", { mediaType: "unknown" }, true), false);
  assert.equal(pruefungErlaubt("media", undefined, true), false);
});

test("Protokoll: ohne Schalter nur abgelehnte Mikrofon-Anfragen", () => {
  assert.equal(protokollieren("anfrage", "media", false, false), true);
  assert.equal(protokollieren("anfrage", "media", true, false), false);
  assert.equal(protokollieren("pruefung", "media", false, false), false);
  assert.equal(protokollieren("pruefung", "media", true, true), true);
});

test("Protokollzeile nennt nur die Herkunft, keine Pfade", () => {
  const z = JSON.parse(
    protokollZeile("anfrage", "media", { mediaTypes: ["audio"], requestingUrl: "http://127.0.0.1:3000/geheim?x=1" }, true, true,
      new Date("2026-10-21T08:00:00Z")),
  );
  assert.deepEqual(z, {
    zeit: "2026-10-21T08:00:00.000Z",
    art: "anfrage",
    berechtigung: "media",
    herkunft: "http://127.0.0.1:3000",
    medien: ["audio"],
    eigeneUi: true,
    erlaubt: true,
  });
  assert.deepEqual(JSON.parse(protokollZeile("pruefung", "media", { mediaType: "unknown", securityOrigin: "x" }, false, false)).medien, ["unknown"]);
  assert.equal(herkunft("kein url"), "");
});

test("Oberflächen-Meldungen: nur mit Kennung, einzeilig, gekürzt", () => {
  const jetzt = new Date("2026-10-21T08:00:00Z");
  assert.equal(oberflaechenZeile("warning", "[WAMO] Mikrofon still\nzweite Zeile", jetzt),
    "2026-10-21T08:00:00.000Z warning [WAMO] Mikrofon still zweite Zeile");
  assert.equal(oberflaechenZeile("warning", "irgendwas anderes", jetzt), null);
  assert.equal(oberflaechenZeile("debug", "[WAMO] x", jetzt), null);
  assert.ok(oberflaechenZeile("error", `[WAMO] ${"x".repeat(1000)}`, jetzt).length < 340);
});
