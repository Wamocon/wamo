import test from "node:test";
import assert from "node:assert/strict";
import { betreuerTokenGueltig, formatZeit, fragmentLesen, kurzeId, platzTokenGueltig, ruecknahmeMs } from "../../wamo_labor_dienst/web/gemeinsam.js";
import { betreuerToken, platzToken, uuid } from "./werte.mjs";

test("fragmentLesen liest den Wert des genannten Schlüssels", () => {
  assert.equal(fragmentLesen("#t=abc", "t"), "abc");
  assert.equal(fragmentLesen("t=abc", "t"), "abc");
  assert.equal(fragmentLesen("#t=abc&x=1", "t"), "abc");
  assert.equal(fragmentLesen("#x=1&t=abc", "t"), "abc");
  assert.equal(fragmentLesen("#b=a=b+c/d.e~f-g_h", "b"), "a=b+c/d.e~f-g_h", "Zeichen des Tokens, auch = + und /, bleiben erhalten");
});

test("fragmentLesen liefert null, wenn nichts Brauchbares da ist", () => {
  for (const eingabe of ["", "#", "#t=", "#t", "#x=1", "#tt=abc", "#xt=abc", "#T=abc", "?t=abc", undefined, null, 5, {}]) {
    assert.equal(fragmentLesen(eingabe, "t"), null, String(eingabe));
  }
});

test("fragmentLesen liest nie aus einer Abfrage", () => {
  assert.equal(fragmentLesen("?t=abc", "t"), null);
  assert.equal(fragmentLesen("#x=1?t=abc", "t"), null);
});

test("platzTokenGueltig verlangt genau 32 Kleinbuchstaben und Ziffern des Hexalphabets", () => {
  assert.equal(platzTokenGueltig(platzToken()), true);
  assert.equal(platzTokenGueltig(platzToken(3)), true);
  for (const falsch of [platzToken().toUpperCase(), platzToken().slice(1), platzToken() + "0", platzToken().replace(/^./, "g"), "", " " + platzToken(), platzToken() + "\n", null, undefined, 7]) {
    assert.equal(platzTokenGueltig(falsch), false, String(falsch));
  }
});

test("betreuerTokenGueltig verlangt 43 bis 200 Zeichen aus dem erlaubten Alphabet", () => {
  assert.equal(betreuerTokenGueltig(betreuerToken(43)), true);
  assert.equal(betreuerTokenGueltig(betreuerToken(200)), true);
  assert.equal(betreuerTokenGueltig(betreuerToken(42)), false);
  assert.equal(betreuerTokenGueltig(betreuerToken(201)), false);
  assert.equal(betreuerTokenGueltig(betreuerToken(50) + " "), false);
  assert.equal(betreuerTokenGueltig(betreuerToken(50) + "ä"), false);
  assert.equal(betreuerTokenGueltig("Bearer " + betreuerToken(50)), false);
  assert.equal(betreuerTokenGueltig(betreuerToken(44) + "._~+/=-"), true);
  assert.equal(betreuerTokenGueltig(null), false);
});

test("ruecknahmeMs folgt Retry-After, sonst einer wachsenden Pause mit Obergrenze", () => {
  assert.equal(ruecknahmeMs("1", 1), 1000);
  assert.equal(ruecknahmeMs("7", 3), 7000);
  assert.equal(ruecknahmeMs(" 3 ", 1), 3000);
  assert.equal(ruecknahmeMs("0", 1), 1000, "mindestens eine Sekunde");
  assert.equal(ruecknahmeMs("999", 1), 120000, "höchstens zwei Minuten");
  assert.equal(ruecknahmeMs(null, 1), 3000);
  assert.equal(ruecknahmeMs(null, 2), 6000);
  assert.equal(ruecknahmeMs(null, 3), 12000);
  assert.equal(ruecknahmeMs(null, 4), 24000);
  assert.equal(ruecknahmeMs(null, 5), 48000);
  assert.equal(ruecknahmeMs(null, 6), 60000);
  assert.equal(ruecknahmeMs(null, 99), 60000);
  for (const kaputt of ["abc", "-5", "1.5", "", "Wed, 21 Oct 2026 07:28:00 GMT", undefined]) {
    assert.equal(ruecknahmeMs(kaputt, 2), 6000, String(kaputt));
  }
  assert.equal(ruecknahmeMs(null, 0), 3000, "auch ohne Fehlerfolge nie unter der Grundpause");
});

test("formatZeit zeigt die Zeit in der gewünschten Zeitzone", () => {
  assert.equal(formatZeit("2026-11-28T09:30:00Z", "Europe/Berlin"), "28.11.2026, 10:30");
  assert.equal(formatZeit("2026-07-01T09:30:00Z", "Europe/Berlin"), "01.07.2026, 11:30");
  assert.equal(formatZeit("2026-11-28T09:30:00Z", "UTC"), "28.11.2026, 09:30");
  for (const kaputt of ["kaputt", "", null, undefined, 5]) assert.equal(formatZeit(kaputt), "");
});

test("kurzeId zeigt die ersten acht Zeichen", () => {
  assert.equal(kurzeId(uuid()), uuid().slice(0, 8));
  assert.equal(kurzeId(null), "");
  assert.equal(kurzeId(5), "");
});
