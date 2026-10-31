// Tests für die Markierung des Lichtrahmens in den Servernachrichten (nachrichten.ts). Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage } from "./nachrichten.ts";

const FENSTER = { x: 100, y: 50, b: 800, h: 600 };
const MONITOR = { x: 0, y: 0, b: 1920, h: 1080 };
const gut = (extra: Record<string, unknown> = {}) => ({
  type: "markierung",
  art: "klick",
  geraet: "pc-wmc-laptop",
  rechner: "WMC-PC",
  fenster: FENSTER,
  monitor: MONITOR,
  ...extra,
});

test("gültige Markierung wird mit festen Feldern übernommen", () => {
  const m = parseServerMessage(gut({ punkt: { x: 300, y: 200 } }));
  assert.deepEqual(m, {
    type: "markierung",
    art: "klick",
    geraet: "pc-wmc-laptop",
    rechner: "WMC-PC",
    fenster: FENSTER,
    monitor: MONITOR,
    punkt: { x: 300, y: 200 },
  });
});

test("Zusatzfelder (Titel, Text) wandern nie weiter", () => {
  const m = parseServerMessage(gut({ titel: "Kundenliste", text: "Passwort", fenster: { ...FENSTER, titel: "x" } }));
  assert.ok(m);
  assert.ok(!JSON.stringify(m).includes("Kundenliste"));
  assert.ok(!JSON.stringify(m).includes("Passwort"));
  assert.ok(!JSON.stringify(m).includes('"titel"'));
});

test("Zahlen müssen ganz sein und innerhalb der Grenzen liegen", () => {
  const schlecht = [
    { ...FENSTER, x: 1.5 },
    { ...FENSTER, x: "1" },
    { ...FENSTER, b: 0 },
    { ...FENSTER, h: -1 },
    { ...FENSTER, x: 40000 },
    { ...FENSTER, y: -40000 },
    { ...FENSTER, b: 40000 },
    { ...FENSTER, b: Number.NaN },
    { x: 1, y: 2, b: 3 },
    null,
    "text",
  ];
  for (const f of schlecht) {
    assert.equal(parseServerMessage(gut({ fenster: f })), null, JSON.stringify(f));
    assert.equal(parseServerMessage(gut({ monitor: f })), null, JSON.stringify(f));
  }
});

test("unbekannte Art, ungültige Kennung oder Rechnername ergeben keine Nachricht", () => {
  for (const kaputt of [
    { art: "loeschen" },
    { geraet: "PC-1" },
    { geraet: "pc-" },
    { geraet: 5 },
    { geraet: `pc-${"a".repeat(30)}` },
    { rechner: "böse; name" },
    { rechner: "" },
  ]) {
    assert.equal(parseServerMessage(gut(kaputt)), null, JSON.stringify(kaputt));
  }
});

test("ohne Kennung (lokaler Kern) ist null erlaubt", () => {
  const m = parseServerMessage(gut({ geraet: null, rechner: undefined }));
  assert.ok(m && m.type === "markierung");
  assert.equal(m.geraet, null);
  assert.equal(m.rechner, null);
});

test("der Punkt gilt nur bei Klicks und nur mit ganzen Zahlen", () => {
  const ohne = parseServerMessage(gut({ art: "text", punkt: { x: 1, y: 2 } }));
  assert.ok(ohne && ohne.type === "markierung");
  assert.equal(ohne.punkt, undefined);
  const kaputt = parseServerMessage(gut({ punkt: { x: 1.5, y: 2 } }));
  assert.ok(kaputt && kaputt.type === "markierung");
  assert.equal(kaputt.punkt, undefined);
});
