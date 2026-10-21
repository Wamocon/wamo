// Tests für die Zeit-Hilfen der Kommandozentrale (kommando/zeit.ts).
// Ausführen im Ordner ui/:  npm test   (node:test, Node lädt TypeScript selbst)
import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDauer, maxSitzungMinuten, sitzungsProzent, sitzungsText, uhrzeit } from "./zeit.ts";

test("Uhrzeit ist zweistellig", () => {
  assert.equal(uhrzeit(new Date(2026, 9, 21, 7, 5)), "07:05");
  assert.equal(uhrzeit(new Date(2026, 9, 21, 23, 59)), "23:59");
});

test("Dauer als HH:MM:SS", () => {
  assert.equal(formatDauer(0), "00:00:00");
  assert.equal(formatDauer(30_000), "00:00:30");
  assert.equal(formatDauer(3_725_000), "01:02:05");
  assert.equal(formatDauer(-5000), "00:00:00");
});

test("Höchstdauer: nur ganze Minuten von 1 bis 1440, sonst 120", () => {
  assert.equal(maxSitzungMinuten(undefined), 120);
  assert.equal(maxSitzungMinuten("45"), 45);
  assert.equal(maxSitzungMinuten(" 90 "), 90);
  assert.equal(maxSitzungMinuten("0"), 120);
  assert.equal(maxSitzungMinuten("1441"), 120);
  assert.equal(maxSitzungMinuten("abc"), 120);
  assert.equal(maxSitzungMinuten("12.5"), 120);
});

test("Sitzungsfortschritt in Prozent, begrenzt", () => {
  const min = 60_000;
  assert.equal(sitzungsProzent(null, 1000, 120), 0);
  assert.equal(sitzungsProzent(0, 60 * min, 120), 50);
  assert.equal(sitzungsProzent(0, 500 * min, 120), 100);
  assert.equal(sitzungsProzent(10 * min, 0, 120), 0); // Start in der Zukunft
  assert.equal(sitzungsProzent(0, 10 * min, 0), 0); // ungültige Höchstdauer
});

test("Sitzungstext für Screenreader", () => {
  assert.equal(sitzungsText(0, 60_000, 120), "1 Minute von 120");
  assert.equal(sitzungsText(0, 12 * 60_000, 120), "12 Minuten von 120");
  assert.equal(sitzungsText(null, 5, 120), "0 Minuten von 120");
});
