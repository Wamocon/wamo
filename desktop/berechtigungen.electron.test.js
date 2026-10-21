// Integrationstest der Berechtigungen gegen ein echtes Electron (das der App, mit künstlichem Mikrofon): Kommen
// mediaTypes und mediaType in dieser Electron-Version so an, dass genau Audio für die eigene Oberfläche durchgeht?
// Fehlt electron.exe (npm install nicht gelaufen), wird der Test übersprungen. Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");

const exe = path.join(
  __dirname,
  "node_modules",
  "electron",
  "dist",
  process.platform === "win32" ? "electron.exe" : "electron",
);

test("echtes Electron: Audio nur für das Hauptfenster mit eigenem Origin, nie Video", { skip: !fs.existsSync(exe) }, () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "wamo-berechtigung-"));
  const env = { ...process.env, WAMO_PROBE_USERDATA: userData };
  delete env.ELECTRON_RUN_AS_NODE; // VS Code setzt das in seinen Terminals; dann liefe Electron nur als Node
  try {
    const r = spawnSync(exe, [path.join(__dirname, "berechtigungen.probe.js")], {
      env,
      encoding: "utf8",
      timeout: 90000,
      windowsHide: true,
    });
    const zeile = (r.stdout ?? "").split(/\r?\n/).find((l) => l.startsWith("PROBE "));
    assert.ok(zeile, `keine Ausgabe der Probe (Status ${r.status}): ${(r.stderr ?? "").slice(0, 400)}`);
    const p = JSON.parse(zeile.slice(6));

    // eigene Oberfläche: Audio ja, alles mit Video nein
    assert.deepEqual(p.eigene.audio, { ok: true, spuren: ["audio"] });
    assert.equal(p.eigene.video.ok, false);
    assert.equal(p.eigene.video.fehler, "NotAllowedError");
    assert.equal(p.eigene.beides.ok, false);
    assert.equal(p.eigene.mikrofon, "granted");
    assert.notEqual(p.eigene.kamera, "granted");
    // Unterframe der eigenen Oberfläche (Origin gleich, aber kein Hauptframe): nichts
    assert.equal(p.eigene.rahmen.ok, false);
    assert.equal(p.eigene.rahmen.fehler, "NotAllowedError");

    // fremder Origin oder nicht das Hauptfenster: nichts
    // (auch eine weitere Session, etwa eine Partition, bekommt die Handler)
    for (const fremd of [p.fremderOrigin, p.fremdesFenster, p.fremdePartition]) {
      assert.equal(fremd.audio.ok, false);
      assert.equal(fremd.audio.fehler, "NotAllowedError");
      assert.equal(fremd.video.ok, false);
      assert.notEqual(fremd.mikrofon, "granted");
    }
  } finally {
    fs.rmSync(userData, { recursive: true, force: true });
  }
});
