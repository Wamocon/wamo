// Tests für den weichen Stopp bei Unterbrechung (lib/weicherStopp.ts).
// Ausführen im Ordner ui/:  npm test   (node:test über tsx, ohne Browser)
import assert from "node:assert/strict";
import { test } from "node:test";
import { LEISE_PEGEL, SPAETESTENS_MS, sollAusblenden, type SoftStop } from "./weicherStopp.ts";

const scharf = (armedAt: number, fading = false): SoftStop => ({ armedAt, fading });

test("ohne leiser_stopp wird nie ausgeblendet", () => {
  assert.equal(sollAusblenden({ armedAt: null, fading: false }, 0, 10_000), false);
});

test("in der nächsten Wortpause wird ausgeblendet", () => {
  assert.equal(sollAusblenden(scharf(1000), LEISE_PEGEL / 2, 1010), true);
});

test("solange WAMO laut spricht, wird bis zur Frist gewartet", () => {
  assert.equal(sollAusblenden(scharf(1000), 0.2, 1000 + SPAETESTENS_MS), false);
  assert.equal(sollAusblenden(scharf(1000), 0.2, 1000 + SPAETESTENS_MS + 1), true);
});

test("ein laufendes Ausblenden startet kein zweites", () => {
  assert.equal(sollAusblenden(scharf(1000, true), 0, 5000), false);
});

test("Zeitpunkt 0 gilt als scharf (kein Falsy-Fehler)", () => {
  assert.equal(sollAusblenden(scharf(0), 0.2, SPAETESTENS_MS + 1), true);
});

test("ungültiger Pegel blendet erst nach der Frist aus", () => {
  assert.equal(sollAusblenden(scharf(1000), Number.NaN, 1100), false);
  assert.equal(sollAusblenden(scharf(1000), Number.NaN, 1000 + SPAETESTENS_MS + 1), true);
});
