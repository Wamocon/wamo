// Tests für die Prüfungen rund um geteilte Arbeit (lib/teilenHilfe.ts) und die Teilen-Angaben in Canvas-Nachrichten
// (lib/nachrichten.ts). Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage } from "./nachrichten.ts";
import {
  alsCanvasAntwort,
  alsHinweise,
  alsTeilungen,
  dateiPruefen,
  groesseText,
  istUuid,
  MAX_UPLOAD_BYTES,
  text,
} from "./teilenHilfe.ts";

const UUID = "11111111-2222-4333-8444-555555555555";

test("istUuid nimmt nur kanonische UUIDs", () => {
  assert.equal(istUuid(UUID), true);
  assert.equal(istUuid(UUID.toUpperCase()), true);
  for (const v of ["dev-abc", "", null, 5, `${UUID}x`, "../x"]) assert.equal(istUuid(v), false);
});

test("groesseText", () => {
  assert.equal(groesseText(1), "1 KB");
  assert.equal(groesseText(340 * 1024), "340 KB");
  assert.equal(groesseText(1.25 * 1024 * 1024), "1,3 MB");
  assert.equal(groesseText(-1), "");
});

test("dateiPruefen wie der Kern: leer, zu groß, Typ", () => {
  assert.equal(dateiPruefen({ name: "a.pdf", size: 10 }), null);
  assert.match(dateiPruefen({ name: "a.pdf", size: 0 }) ?? "", /leer/);
  assert.match(dateiPruefen({ name: "a.pdf", size: MAX_UPLOAD_BYTES + 1 }) ?? "", /25 MB/);
  assert.match(dateiPruefen({ name: "seite.html", size: 10 }) ?? "", /Dateityp/);
  assert.match(dateiPruefen({ name: "ohne", size: 10 }) ?? "", /Dateityp/);
  assert.equal(dateiPruefen({ name: "Tabelle.XLSX", size: 10 }), null);
});

test("text entfernt Steuer- und Bidi-Zeichen und kürzt", () => {
  assert.equal(text("a‮b\u0000c\nd"), "abc d");
  assert.equal(text("x".repeat(10), 5), "xxxx…");
  assert.equal(text(42), "");
});

test("alsTeilungen prüft und bereinigt fremde Angaben", () => {
  const liste = alsTeilungen([
    { id: UUID, art: "canvas", recht: "bearbeiten", titel: "Plan‮", objekt: UUID, name: "Anna", hinweis: "" },
    { id: "kaputt", art: "canvas", objekt: UUID },
    { id: UUID, art: "unbekannt", objekt: UUID },
    "kein Objekt",
  ]);
  assert.equal(liste.length, 1);
  assert.equal(liste[0].titel, "Plan");
  assert.equal(liste[0].hinweis, null);
  assert.deepEqual(alsTeilungen({ kein: "Array" }), []);
});

test("alsHinweise und alsCanvasAntwort ohne ungeprüfte Casts", () => {
  assert.deepEqual(alsHinweise(null), []);
  const h = alsHinweise([{ id: UUID, art: "teilung_erhalten", titel: "Anna hat …", gelesen: false }]);
  assert.equal(h[0].text, "");
  assert.equal(alsCanvasAntwort({ item: {}, recht: "admin" }), null);
  assert.equal(alsCanvasAntwort({ item: { kind: "dokument" }, recht: "lesen", version: 0 })?.version, 1);
});

test("Canvas-Nachricht behält nur geprüfte Teilen-Angaben", () => {
  const msg = parseServerMessage({
    type: "canvas",
    item: {
      id: "dev-1",
      kind: "dokument",
      title: "Entwicklung: x",
      text: "t",
      teilbar_id: UUID,
      teilbar_projekt: "rezepte-app",
      geteilt: { von: "Anna‮", recht: "lesen" },
    },
  });
  assert.ok(msg && msg.type === "canvas");
  assert.deepEqual(msg.item.teilbar, { id: UUID, projekt: "rezepte-app" });
  assert.deepEqual(msg.item.geteilt, { von: "Anna", recht: "lesen" });
  const boese = parseServerMessage({
    type: "canvas",
    item: { kind: "dokument", title: "x", text: "t", teilbar_id: "../x", teilbar_projekt: "A B", geteilt: { recht: "admin" } },
  });
  assert.ok(boese && boese.type === "canvas");
  assert.equal(boese.item.teilbar, undefined);
  assert.equal(boese.item.geteilt, undefined);
});
