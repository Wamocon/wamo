// Tests für die Servernachricht "modellzugang" (lib/nachrichten.ts): nur geprüfte Felder, nie ein Schlüssel.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage } from "./nachrichten.ts";

const STAND = {
  type: "modellzugang",
  aktion: "hinterlegen",
  art: "claude",
  quelle: "profil",
  hinterlegt: true,
  ende: "Ab12",
  geprueft: "2026-10-21T09:30+00:00",
  festgelegt: false,
  modell: "Claude Haiku 5.5",
  stt: "soniox",
  tts: "soniox",
  wirksam: "ab der nächsten Sitzung",
};

test("gültiger Stand wird übernommen", () => {
  const m = parseServerMessage(STAND);
  assert.ok(m && m.type === "modellzugang");
  assert.equal(m.art, "claude");
  assert.equal(m.ende, "Ab12");
  assert.equal(m.geprueft, "2026-10-21T09:30");
  assert.equal(m.hinterlegt, true);
});

test("fremde Felder und ein mitgeschickter Schlüssel landen nicht im Stand", () => {
  const schluessel = "sk-ant-api03-GEHEIM-0123456789abcdefghij";
  const m = parseServerMessage({ ...STAND, schluessel, claude_schluessel: schluessel, ende: schluessel });
  assert.ok(m && m.type === "modellzugang");
  assert.equal(m.ende, undefined); // nur genau vier Zeichen
  assert.ok(!JSON.stringify(m).includes("sk-ant-"));
});

test("unbekannte Aktion oder Art", () => {
  assert.equal(parseServerMessage({ ...STAND, aktion: "ausgeben" }), null);
  const m = parseServerMessage({ ...STAND, art: "anderes", quelle: "irgendwo" });
  assert.ok(m && m.type === "modellzugang");
  assert.equal(m.art, undefined);
  assert.equal(m.quelle, undefined);
});

test("nur Fehler (Datenbank weg)", () => {
  const m = parseServerMessage({ type: "modellzugang", aktion: "loeschen", fehler: "Datenbank gerade nicht erreichbar" });
  assert.ok(m && m.type === "modellzugang");
  assert.equal(m.art, undefined);
  assert.equal(m.hinterlegt, false);
  assert.equal(m.fehler, "Datenbank gerade nicht erreichbar");
});
