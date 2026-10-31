// Tests für Pfadauflösung und Betriebsarten (installation.js). Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const { test } = require("node:test");
const { BUERO_UI_URL, LOKALE_UI_URL, adressen, loeseAuf, wirksameBetriebsart } = require("./installation");

const installiert = () =>
  loeseAuf({
    istPaketiert: true,
    resourcesPath: "C:\\Users\\x\\AppData\\Local\\Programs\\WAMO\\resources",
    verzeichnis: "C:\\Users\\x\\AppData\\Local\\Programs\\WAMO\\resources\\app.asar",
    userData: "C:\\Users\\x\\AppData\\Roaming\\wamo-desktop",
  });

const entwickler = (existiert = () => true) =>
  loeseAuf({ istPaketiert: false, resourcesPath: "C:\\egal", verzeichnis: "D:\\Projekt\\wamo\\desktop", userData: "C:\\egal", existiert });

test("Entwickler: alles kommt aus dem Repository über desktop/", () => {
  const p = entwickler();
  assert.equal(p.modus, "entwickler");
  assert.equal(p.repo, path.resolve("D:\\Projekt\\wamo"));
  assert.equal(p.ressourcen, p.repo);
  assert.equal(p.hostDir, path.join(p.repo, "host"));
  assert.equal(p.uiDir, path.join(p.repo, "ui"));
  assert.equal(p.logDir, path.join("D:\\Projekt\\wamo\\desktop", "logs"));
  assert.equal(p.caDatei, path.join(p.repo, "infra", "sokrates2", "wamo-ca.crt"));
  assert.equal(p.lokalerKernMoeglich, true);
  assert.equal(p.hostUmgebungVerwalten, false);
  assert.equal(p.uvDatei, null);
});

test("Entwickler ohne voice-core/bot.py: kein lokaler Kern möglich", () => {
  assert.equal(entwickler(() => false).lokalerKernMoeglich, false);
});

test("Installiert: Host, Skripte und CA unter dem Ressourcenordner, Logs in userData, kein Repo", () => {
  const p = installiert();
  assert.equal(p.modus, "installiert");
  assert.equal(p.repo, null);
  assert.equal(p.ressourcen, "C:\\Users\\x\\AppData\\Local\\Programs\\WAMO\\resources");
  assert.equal(p.hostDir, path.join(p.ressourcen, "host"));
  assert.equal(p.caDatei, path.join(p.ressourcen, "wamo-ca.crt"));
  assert.equal(p.uvDatei, path.join(p.ressourcen, "uv", "uv.exe"));
  assert.equal(p.logDir, path.join("C:\\Users\\x\\AppData\\Roaming\\wamo-desktop", "logs"));
  assert.equal(p.uiDir, null);
  assert.equal(p.voiceCoreDir, null);
  assert.equal(p.lokalerKernMoeglich, false);
  assert.equal(p.hostUmgebungVerwalten, true);
});

test("Installiert: ohne Ressourcenordner oder userData wird nicht geraten", () => {
  assert.throws(() => loeseAuf({ istPaketiert: true, userData: "C:\\u" }), /resourcesPath/);
  assert.throws(() => loeseAuf({ istPaketiert: true, resourcesPath: "C:\\r" }), /userData/);
});

test("Adressen: Entwickler lokal, installiert die Büro-Oberfläche", () => {
  assert.deepEqual(adressen(entwickler(), {}), { uiUrl: LOKALE_UI_URL, kernUrl: "http://localhost:7860" });
  assert.equal(adressen(installiert(), {}).uiUrl, BUERO_UI_URL);
  assert.equal(BUERO_UI_URL, "https://192.168.178.136:55445");
});

test("Adressen: installiert sind sie fest, die Umgebung ändert nichts", () => {
  const a = adressen(installiert(), { WAMO_UI_URL: "https://boese.example", WAMO_VOICE_CORE_URL: "http://boese.example" });
  assert.deepEqual(a, { uiUrl: "https://192.168.178.136:55445", kernUrl: "https://192.168.178.136:55444" });
});

test("Adressen: im Entwicklermodus überschreibt die Umgebung, aber nur mit http(s)", () => {
  assert.equal(adressen(entwickler(), { WAMO_UI_URL: "http://localhost:3999" }).uiUrl, "http://localhost:3999");
  assert.throws(() => adressen(entwickler(), { WAMO_UI_URL: "file:///C:/x.html" }), /http/);
  assert.throws(() => adressen(entwickler(), { WAMO_UI_URL: "data:text/html,x" }), /http/);
  assert.throws(() => adressen(entwickler(), { WAMO_UI_URL: "kaputt" }), /gültige/);
});

test("Betriebsart: ohne lokalen Kern immer Büro, im Entwicklermodus gilt der Wunsch (Standard Unterwegs)", () => {
  const inst = installiert();
  assert.equal(wirksameBetriebsart("unterwegs", inst), "buero");
  assert.equal(wirksameBetriebsart(undefined, inst), "buero");
  const dev = entwickler();
  assert.equal(wirksameBetriebsart("buero", dev), "buero");
  assert.equal(wirksameBetriebsart("unterwegs", dev), "unterwegs");
  assert.equal(wirksameBetriebsart("quatsch", dev), "unterwegs");
  assert.equal(wirksameBetriebsart("unterwegs", entwickler(() => false)), "buero");
});
