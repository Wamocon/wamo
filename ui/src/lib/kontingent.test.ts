// Tests für die Servernachricht "kontingent" (lib/nachrichten.ts): nur geprüfte Felder, Wertebereiche, Zustand unbekannt.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage } from "./nachrichten.ts";

const BEKANNT = {
  type: "kontingent",
  zustand: "bekannt",
  stand: "2026-10-22T11:59:00Z",
  limits: [
    { art: "sitzung", prozent: 42, endet: "2026-10-22T15:00:00Z", status: "ok" },
    { art: "woche", prozent: 13.04, endet: "2026-10-26T09:00:00Z" },
    { art: "fable", prozent: 7, endet: "2026-10-27T09:00:00Z" },
  ],
};

test("gültiges Kontingent wird übernommen, Prozent auf eine Nachkommastelle", () => {
  const m = parseServerMessage(BEKANNT);
  assert.ok(m && m.type === "kontingent");
  assert.equal(m.zustand, "bekannt");
  assert.equal(m.stand, "2026-10-22T11:59:00Z");
  assert.deepEqual(m.limits.map((l) => l.art), ["sitzung", "woche", "fable"]);
  assert.equal(m.limits[1].prozent, 13);
  assert.equal(m.limits[0].status, "ok");
  assert.equal(m.limits[2].status, undefined);
});

test("ohne Daten ist der Zustand unbekannt", () => {
  const leer = parseServerMessage({ type: "kontingent", zustand: "unbekannt", stand: null, limits: [] });
  assert.ok(leer && leer.type === "kontingent");
  assert.equal(leer.zustand, "unbekannt");
  assert.deepEqual(leer.limits, []);
  assert.equal(leer.stand, undefined);
  // bekannt ohne gültige Limits bleibt unbekannt, auch ein mitgeschickter Stand zählt dann nicht
  for (const limits of [[], null, "alles", [{ art: "geheim", prozent: 5 }], [{ art: "woche" }]]) {
    const m = parseServerMessage({ ...BEKANNT, limits });
    assert.ok(m && m.type === "kontingent");
    assert.equal(m.zustand, "unbekannt");
    assert.equal(m.stand, undefined);
    assert.deepEqual(m.limits, []);
  }
});

test("unbekannter Zustand und fremde Felder", () => {
  assert.equal(parseServerMessage({ ...BEKANNT, zustand: "vielleicht" }), null);
  assert.equal(parseServerMessage({ type: "kontingent", limits: [] }), null);
  const m = parseServerMessage({
    ...BEKANNT,
    token: "sk-ant-oat01-GEHEIM",
    limits: [{ art: "sitzung", prozent: 5, geheim: "sk-ant-oat01-GEHEIM", status: "ok" }],
  });
  assert.ok(m && m.type === "kontingent");
  assert.ok(!JSON.stringify(m).includes("sk-ant-"));
  assert.deepEqual(Object.keys(m.limits[0]).sort(), ["art", "prozent", "status"]);
});

test("Wertebereiche, Zeiten und Doppelte", () => {
  const m = parseServerMessage({
    ...BEKANNT,
    stand: "gestern",
    limits: [
      { art: "sitzung", prozent: -1, endet: "morgen", status: "kaputt" }, // nichts Gültiges: fällt ganz weg
      { art: "woche", prozent: 5000, endet: "2026-10-26T09:00:00Z" }, // Prozent außerhalb, Zeit gültig
      { art: "woche", prozent: 10 }, // doppelt: die erste zählt
      { art: "opus", prozent: "viel" },
      { art: "sonnet", prozent: Number.NaN, status: "warnung" },
      { art: "fable", prozent: 99.96, status: "erreicht" },
      "text",
      null,
    ],
  });
  assert.ok(m && m.type === "kontingent");
  assert.equal(m.stand, undefined); // kein ISO-Stand
  assert.deepEqual(m.limits, [
    { art: "woche", endet: "2026-10-26T09:00:00Z" },
    { art: "sonnet", status: "warnung" },
    { art: "fable", prozent: 100, status: "erreicht" },
  ]);
});
