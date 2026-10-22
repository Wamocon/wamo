// Tests für die Modellwahl je Profil (lib/modellwahl.ts) und die Felder dazu in lib/nachrichten.ts.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { gewaehlterModus, modusErklaerung, modusKurz, modusName, nachbar, wirksamText } from "./modellwahl.ts";
import { parseServerMessage, type Betrieb, type Modellzugang } from "./nachrichten.ts";

const STAND: Modellzugang = { aktion: "status", hinterlegt: false, festgelegt: false, codeHinterlegt: false };
const BETRIEB: Betrieb = { modus: "buero", modelle: { llm: "gemma4-31b (LiteLLM)" }, pc: "aus" };

test("Namen der beiden Modi", () => {
  assert.equal(modusName("automatisch"), "Automatisch");
  assert.equal(modusName("nur_haus"), "Nur Hausmodelle");
  assert.equal(modusName(undefined), "unbekannt");
});

test("gewählter Modus: Zwang des Admins gilt als Nur Hausmodelle", () => {
  assert.equal(gewaehlterModus(null), "automatisch");
  assert.equal(gewaehlterModus(STAND), "automatisch");
  assert.equal(gewaehlterModus({ ...STAND, modus: "nur_haus" }), "nur_haus");
  assert.equal(gewaehlterModus({ ...STAND, modus: "automatisch", festgelegt: true }), "nur_haus");
});

test("die Wahl gilt ab der nächsten Sitzung, und das steht ausdrücklich da", () => {
  assert.match(wirksamText(STAND, null), /ab der nächsten Sitzung/);
  const text = wirksamText({ ...STAND, modus: "nur_haus" }, { ...BETRIEB, modellModus: "automatisch" });
  assert.match(text, /Gewählt: Nur Hausmodelle/);
  assert.match(text, /läuft noch im Modus Automatisch/);
  assert.match(text, /ab der nächsten Sitzung/);
  // gleiche Wahl wie in der Sitzung: kein Widerspruch zu melden
  assert.doesNotMatch(wirksamText({ ...STAND, modus: "nur_haus" }, { ...BETRIEB, modellModus: "nur_haus" }), /läuft noch/);
});

test("gesperrt, wenn ein Admin festgelegt hat", () => {
  assert.match(wirksamText({ ...STAND, festgelegt: true }, null), /gesperrt/);
});

test("Erklärungen ohne Gedankenstrich und mit dem Hinweis auf Soniox", () => {
  const texte = [modusErklaerung("nur_haus", true), modusErklaerung("automatisch", true), modusErklaerung("automatisch", false)];
  for (const t of texte) assert.ok(!/[–—]/.test(t), t);
  assert.match(texte[0], /Soniox/);
  assert.match(texte[0], /nichts an Anthropic/);
  assert.match(texte[1], /Claude Haiku/);
  assert.doesNotMatch(texte[2], /Claude/);
});

test("Kurzform für die Statusleiste", () => {
  assert.equal(modusKurz(null), null);
  assert.equal(modusKurz(BETRIEB), null); // alter Kern ohne Meldung
  assert.deepEqual(modusKurz({ ...BETRIEB, modellModus: "automatisch" }), { text: "Automatisch" });
  assert.deepEqual(modusKurz({ ...BETRIEB, modellModus: "nur_haus", modellQuelle: "festgelegt" }), {
    text: "Nur Hausmodelle",
    grund: "vom Admin festgelegt",
  });
  assert.equal(modusKurz({ ...BETRIEB, modellModus: "nur_haus", modellQuelle: "wahl" })?.grund, "von dir gewählt");
  assert.equal(modusKurz({ ...BETRIEB, modellModus: "nur_haus", modellQuelle: "keiner" })?.grund, "kein eigener Schlüssel");
});

test("Pfeiltasten wandern mit Umlauf", () => {
  assert.equal(nachbar(0, "ArrowRight", 2), 1);
  assert.equal(nachbar(1, "ArrowRight", 2), 0);
  assert.equal(nachbar(0, "ArrowLeft", 2), 1);
  assert.equal(nachbar(1, "ArrowUp", 2), 0);
  assert.equal(nachbar(1, "Home", 2), 0);
  assert.equal(nachbar(0, "End", 2), 1);
  assert.equal(nachbar(0, "a", 2), null);
});

test("Servernachricht betrieb: Modus, Quelle und Hinweis werden geprüft übernommen", () => {
  const roh = {
    type: "betrieb", modus: "buero", modelle: { llm: "gemma4-31b (Rückfall)" }, pc: "aus",
    modell_modus: "nur_haus", modell_quelle: "wahl", modell_hinweis: "Claude Haiku 5.5: nicht erreichbar",
  };
  const m = parseServerMessage(roh);
  assert.ok(m && m.type === "betrieb");
  assert.equal(m.modellModus, "nur_haus");
  assert.equal(m.modellQuelle, "wahl");
  assert.equal(m.modellHinweis, "Claude Haiku 5.5: nicht erreichbar");
  const schlecht = parseServerMessage({ ...roh, modell_modus: "alles", modell_quelle: "irgendwer", modell_hinweis: 5 });
  assert.ok(schlecht && schlecht.type === "betrieb");
  assert.equal(schlecht.modellModus, undefined);
  assert.equal(schlecht.modellQuelle, undefined);
  assert.equal(schlecht.modellHinweis, undefined);
});

test("Servernachricht modellzugang: Wahl und Claude-Code-Token ohne Geheimnis", () => {
  const token = "sk-ant-oat01-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99";
  const m = parseServerMessage({
    type: "modellzugang", aktion: "modus", art: "haus", quelle: "wahl", hinterlegt: true, ende: "Ab12",
    festgelegt: false, modell: "Hausmodelle", modus: "nur_haus", code_hinterlegt: true, code_ende: "Zz99",
    code_gesetzt: "2026-10-22T08:30+00:00", claude_code_token: token, code_token: token,
  });
  assert.ok(m && m.type === "modellzugang");
  assert.equal(m.aktion, "modus");
  assert.equal(m.quelle, "wahl");
  assert.equal(m.modus, "nur_haus");
  assert.equal(m.codeHinterlegt, true);
  assert.equal(m.codeEnde, "Zz99");
  assert.equal(m.codeGesetzt, "2026-10-22T08:30+00:00");
  assert.ok(!JSON.stringify(m).includes("sk-ant-"));
  // ein mitgeschickter Token als "Ende" wird verworfen (nur genau vier Zeichen)
  const ende = parseServerMessage({ type: "modellzugang", aktion: "status", code_hinterlegt: true, code_ende: token });
  assert.ok(ende && ende.type === "modellzugang");
  assert.equal(ende.codeEnde, undefined);
  assert.ok(!JSON.stringify(ende).includes("sk-ant-"));
  const egal = parseServerMessage({ type: "modellzugang", aktion: "modus", modus: "egal" });
  assert.ok(egal && egal.type === "modellzugang");
  assert.equal(egal.modus, undefined);
});
