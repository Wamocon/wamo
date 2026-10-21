// Tests für die Berechtigungen der Desktop-App (berechtigungen.js). Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  anfrageErlaubt,
  herkunft,
  installieren,
  mikrofonStatusWert,
  oberflaechenZeile,
  ratenGrenze,
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
  assert.ok(oberflaechenZeile("error", `[WAMO] Mikrofon ${"x".repeat(1000)}`, jetzt).length < 340);
});

test("Oberflächen-Zeilen: nur [WAMO] Mikrofon, Unicode-Steuer- und Zeilentrenner raus", () => {
  const jetzt = new Date("2026-10-21T08:00:00Z");
  assert.equal(oberflaechenZeile("info", "[WAMO] etwas anderes", jetzt), null, "enge Kennung");
  const boese = "[WAMO] Mikrofon a\u0085b\u2028c\u2029d\u202ee\u200bf\u001bg";
  const zeile = oberflaechenZeile("warning", boese, jetzt);
  assert.equal(zeile, "2026-10-21T08:00:00.000Z warning [WAMO] Mikrofon a b c d e f g");
  assert.doesNotMatch(zeile, /[\p{Cc}\p{Cf}\u2028\u2029]/u);
});

test("Rate: höchstens max Zeilen je Zeitraum, danach wieder frei", () => {
  let t = 0;
  const erlaubt = ratenGrenze(3, 1000, () => t);
  assert.deepEqual([erlaubt(), erlaubt(), erlaubt(), erlaubt(), erlaubt()], [true, true, true, false, false]);
  t = 999;
  assert.equal(erlaubt(), false);
  t = 1000;
  assert.equal(erlaubt(), true);
});

test("Windows-Mikrofonstatus: nur feste Werte, alles andere ist unknown", () => {
  for (const wert of ["granted", "denied", "restricted", "not-determined"]) assert.equal(mikrofonStatusWert(wert), wert);
  for (const roh of ["", "unknown", "GRANTED", undefined, null, 1, {}, "granted; evil"]) {
    assert.equal(mikrofonStatusWert(roh), "unknown");
  }
});

test("Unterframes und Ausnahmen führen nie zu erlaubt", () => {
  assert.equal(anfrageErlaubt("media", { mediaTypes: ["audio"], isMainFrame: false }, true), false);
  assert.equal(pruefungErlaubt("media", { mediaType: "audio", isMainFrame: false }, true), false);
  assert.equal(anfrageErlaubt("media", { mediaTypes: ["audio"], isMainFrame: true }, true), true);
  let anfrage;
  let pruefung;
  installieren(
    { setPermissionRequestHandler: (fn) => (anfrage = fn), setPermissionCheckHandler: (fn) => (pruefung = fn) },
    () => {
      throw new Error("kaputt");
    },
    () => {
      throw new Error("Protokoll kaputt");
    },
  );
  let antwort = "keine";
  anfrage({}, "media", (ok) => (antwort = ok), { mediaTypes: ["audio"], requestingUrl: "http://ui" });
  assert.equal(antwort, false, "der Aufruf wird beantwortet, und zwar mit nein");
  assert.equal(pruefung({}, "media", "http://ui", { mediaType: "audio" }), false);
  // Protokoll wirft, Oberfläche ist echt: trotzdem erlaubt und beantwortet
  installieren(
    { setPermissionRequestHandler: (fn) => (anfrage = fn), setPermissionCheckHandler: (fn) => (pruefung = fn) },
    () => true,
    () => {
      throw new Error("Protokoll kaputt");
    },
  );
  anfrage({}, "media", (ok) => (antwort = ok), { mediaTypes: ["audio"] });
  assert.equal(antwort, true);
  assert.equal(pruefung({}, "media", "x", { mediaType: "audio" }), true);
});

test("installieren: beide Handler laufen über dieselbe Entscheidung und werden protokolliert", () => {
  const zeilen = [];
  let anfrage;
  let pruefung;
  const ses = {
    setPermissionRequestHandler: (fn) => (anfrage = fn),
    setPermissionCheckHandler: (fn) => (pruefung = fn),
  };
  const wc = {};
  installieren(ses, (w, url) => w === wc && url === "http://ui", (...a) => zeilen.push(a));

  let antwort;
  anfrage(wc, "media", (ok) => (antwort = ok), { mediaTypes: ["audio"], requestingUrl: "http://ui" });
  assert.equal(antwort, true);
  anfrage(wc, "media", (ok) => (antwort = ok), { mediaTypes: ["audio", "video"], requestingUrl: "http://ui" });
  assert.equal(antwort, false);
  anfrage({}, "media", (ok) => (antwort = ok), { mediaTypes: ["audio"], requestingUrl: "http://ui" });
  assert.equal(antwort, false, "anderes Fenster");
  assert.equal(pruefung(wc, "media", "http://ui", { mediaType: "audio" }), true);
  assert.equal(pruefung(wc, "media", "http://ui", { mediaType: "video" }), false);
  assert.equal(pruefung(wc, "media", "http://fremd", { mediaType: "audio" }), false, "fremder Origin");
  assert.deepEqual(
    zeilen.map(([art, , , eigene, erlaubt]) => [art, eigene, erlaubt]),
    [
      ["anfrage", true, true],
      ["anfrage", true, false],
      ["anfrage", false, false],
      ["pruefung", true, true],
      ["pruefung", true, false],
      ["pruefung", false, false],
    ],
  );
});
