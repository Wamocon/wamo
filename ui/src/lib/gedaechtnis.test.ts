// Tests der reinen Logik der Karte "Gedächtnis" (lib/gedaechtnis.ts): strenge Prüfung der Antwort des Kerns, Säuberung der Texte, Sätze.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ART_TEXT,
  ENTSCHEIDUNG_KNOPF,
  ergebnisText,
  fehlerText,
  MAX_TEXT,
  MAX_VORSCHLAEGE,
  pruefeErgebnis,
  pruefeVorschlaege,
  sauber,
  TEXT_ERKLAERUNG,
  TEXT_LEER,
  zeitKurz,
  zusammenfassung,
} from "./gedaechtnis.ts";

const ID1 = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e";
const ID2 = "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const roh = (ueber: Record<string, unknown> = {}) => ({ id: ID1, art: "fact", text: "W.A.I.S. kann Fernseher schalten.", autor: "Anna", angelegt: "2026-11-12T06:58:48Z", ...ueber });

test("gültige Antwort wird übernommen", () => {
  const liste = pruefeVorschlaege({ vorschlaege: [roh(), roh({ id: ID2, art: "lesson", autor: "Niko" })], anzahl: 2 });
  assert.deepEqual(liste, [
    { id: ID1, art: "fact", text: "W.A.I.S. kann Fernseher schalten.", autor: "Anna", angelegt: "2026-11-12T06:58:48Z" },
    { id: ID2, art: "lesson", text: "W.A.I.S. kann Fernseher schalten.", autor: "Niko", angelegt: "2026-11-12T06:58:48Z" },
  ]);
});

test("keine Antwort oder falsche Form ergibt null, eine leere Liste ist gültig", () => {
  for (const r of [null, undefined, "x", 5, [], {}, { vorschlaege: "x" }, { vorschlaege: {} }]) assert.equal(pruefeVorschlaege(r), null, JSON.stringify(r));
  assert.deepEqual(pruefeVorschlaege({ vorschlaege: [] }), []);
});

test("unbrauchbare Einträge fallen einzeln weg, die übrigen bleiben", () => {
  const liste = pruefeVorschlaege({
    vorschlaege: [null, "x", 5, roh({ id: "kaputt" }), roh({ id: 5 }), roh({ text: "   " }), roh({ text: 5 }), roh({ id: ID2 })],
  });
  assert.deepEqual(liste?.map((v) => v.id), [ID2]);
});

test("doppelte Kennungen zählen einmal, höchstens 50 Einträge werden gelesen", () => {
  assert.equal(pruefeVorschlaege({ vorschlaege: [roh(), roh(), roh()] })?.length, 1);
  const viele = Array.from({ length: 80 }, (_, i) => roh({ id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}` }));
  assert.equal(pruefeVorschlaege({ vorschlaege: viele })?.length, MAX_VORSCHLAEGE);
});

test("Art, Autor und Zeit: Unbekanntes wird Fakt, unbekannter Autor und leere Zeit", () => {
  const v = pruefeVorschlaege({ vorschlaege: [roh({ art: "irgendwas", autor: 5, angelegt: "gestern" })] })![0];
  assert.equal(v.art, "fact");
  assert.equal(v.autor, "unbekannt");
  assert.equal(v.angelegt, "");
  for (const art of Object.keys(ART_TEXT)) assert.equal(pruefeVorschlaege({ vorschlaege: [roh({ art })] })![0].art, art);
});

test("Texte werden gesäubert und begrenzt (Vorschlagstexte sind Daten)", () => {
  const umlenkung = String.fromCharCode(0x202e);
  const nul = String.fromCharCode(0);
  const v = pruefeVorschlaege({ vorschlaege: [roh({ text: `Zeile eins\nZeile\tzwei${umlenkung}gedreht${nul}   Ende`, autor: `Evil${umlenkung}\nName${"x".repeat(200)}` })] })![0];
  assert.equal(v.text, "Zeile eins Zeile zwei gedreht Ende");
  assert.ok(v.autor.length <= 60 && !v.autor.includes(umlenkung) && !v.autor.includes("\n"));
  assert.equal(pruefeVorschlaege({ vorschlaege: [roh({ text: "y".repeat(5000) })] })![0].text.length, MAX_TEXT);
});

test("fremde Felder werden nie übernommen", () => {
  const v = pruefeVorschlaege({ vorschlaege: [roh({ adresse: "192.168.178.136", geheim: "x", user_id: "y" })] })![0];
  assert.deepEqual(Object.keys(v).sort(), ["angelegt", "art", "autor", "id", "text"]);
});

test("Ergebnis einer Entscheidung: nur die drei bekannten", () => {
  for (const e of ["freigegeben", "abgelehnt", "nicht_offen"]) assert.equal(pruefeErgebnis({ ok: true, ergebnis: e }), e);
  for (const r of [null, {}, { ok: false, ergebnis: "freigegeben" }, { ok: true }, { ok: true, ergebnis: "aktiv" }, { ok: "true", ergebnis: "abgelehnt" }, "freigegeben"]) {
    assert.equal(pruefeErgebnis(r), null, JSON.stringify(r));
  }
});

test("Sätze: jeder Ausgang hat einen Satz, ohne Gedankenstrich", () => {
  const gedankenstrich = new RegExp("[" + String.fromCharCode(0x2013, 0x2014) + "]");
  for (const e of ["freigegeben", "abgelehnt", "nicht_offen"] as const) {
    assert.ok(ergebnisText(e).endsWith("."), e);
    assert.doesNotMatch(ergebnisText(e), gedankenstrich);
  }
  for (const t of [TEXT_ERKLAERUNG, TEXT_LEER, ENTSCHEIDUNG_KNOPF.freigeben, ENTSCHEIDUNG_KNOPF.ablehnen]) {
    assert.doesNotMatch(t, gedankenstrich);
    assert.ok(!t.includes(" - "));
  }
  assert.equal(zusammenfassung(0), TEXT_LEER);
  assert.equal(zusammenfassung(1), "1 Vorschlag wartet auf dich.");
  assert.equal(zusammenfassung(3), "3 Vorschläge warten auf dich.");
});

test("Fehlersätze: 401, 403, 429 und keine Verbindung sind fest, sonst gilt der gesäuberte Satz des Kerns", () => {
  assert.equal(fehlerText(403, "egal"), "Vorschläge prüfen dürfen nur Admins.");
  assert.equal(fehlerText(401, "egal"), "Bitte melde dich neu an.");
  assert.match(fehlerText(429, "egal"), /Zu viele Aufrufe/);
  assert.match(fehlerText(0, "Kern nicht erreichbar"), /nicht erreichbar/);
  assert.equal(fehlerText(503, "Die Vorschläge sind gerade nicht verfügbar"), "Die Vorschläge sind gerade nicht verfügbar");
  assert.match(fehlerText(500, ""), /nicht geklappt/);
  assert.ok(fehlerText(500, "x".repeat(500)).length <= 160);
});

test("Zeit: nur das feste Format, sonst leer", () => {
  assert.match(zeitKurz("2026-11-12T06:58:48Z", "UTC"), /^12\.11\.2026, 06:58$/);
  for (const z of ["", "gestern", "2026-11-12", "2026-13-45T99:99:99Z"]) assert.equal(zeitKurz(z, "UTC"), "", z);
  assert.equal(sauber(5, 10), "");
});
