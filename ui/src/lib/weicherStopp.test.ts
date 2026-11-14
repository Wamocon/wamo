// Tests für den weichen Stopp bei Unterbrechung (lib/weicherStopp.ts).
// Ausführen im Ordner ui/:  npm test   (node:test über tsx, ohne Browser)
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage } from "./nachrichten.ts";
import { AUSBLENDEN_MS, GEDAEMPFT, LEISE_PEGEL, SPAETESTENS_MS, sollAusblenden, verlauf, type SoftStop } from "./weicherStopp.ts";

const scharf = (armedAt: number, fading = false): SoftStop => ({ armedAt, fading });

test("ohne leiser_stopp wird nie ausgeblendet", () => {
  assert.equal(sollAusblenden({ armedAt: null, fading: false }, 0, 10_000), false);
});

test("in der nächsten Wortpause wird ausgeblendet", () => {
  assert.equal(sollAusblenden(scharf(1000), LEISE_PEGEL / 2, 1010), true);
});

test("solange W.A.I.S. laut spricht, wird bis zur Frist gewartet", () => {
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

test("der Verlauf ist linear und bleibt zwischen Anfang und Ziel", () => {
  assert.equal(verlauf(1, 0, AUSBLENDEN_MS, 0), 1);
  assert.equal(verlauf(1, 0, 100, 50), 0.5);
  assert.equal(verlauf(1, GEDAEMPFT, 90, 10_000), GEDAEMPFT);
  assert.equal(verlauf(0.3, 1, 250, -5), 0.3, "vor dem Start bleibt es beim Anfangswert");
});

test("ein ungültiger Verlauf springt zum Ziel statt hängen zu bleiben", () => {
  assert.equal(verlauf(Number.NaN, 0, 100, 10), 0);
  assert.equal(verlauf(1, 0, 0, 10), 0);
});

test("leiser_stopp kennt sofort, stimme_leiser braucht einen Wahrheitswert", () => {
  assert.deepEqual(parseServerMessage({ type: "leiser_stopp" }), { type: "leiser_stopp", sofort: false });
  assert.deepEqual(parseServerMessage({ type: "leiser_stopp", sofort: true }), { type: "leiser_stopp", sofort: true });
  assert.deepEqual(parseServerMessage({ type: "stimme_leiser", an: true }), { type: "stimme_leiser", an: true });
  assert.equal(parseServerMessage({ type: "stimme_leiser", an: "ja" }), null);
  assert.equal(parseServerMessage({ type: "stimme_leiser" }), null);
});
