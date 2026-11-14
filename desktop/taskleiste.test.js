// Tests für die Absicherung des Taskleistenknopfs (taskleiste.js). Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { ANZEIGENAME, APP_ID, NACHLAUF_MS, appDetails, taskleisteAbsichern } = require("./taskleiste");

const paket = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8"));
const EXE = "C:\\Users\\Max Muster\\AppData\\Local\\Programs\\wamo-desktop\\WAMO.exe";
const ICO_ASAR = "C:\\Users\\Max Muster\\AppData\\Local\\Programs\\wamo-desktop\\resources\\app.asar\\assets\\wamo.ico";

/** Falsches BrowserWindow: merkt sich setAppDetails und setIcon in der Reihenfolge der Aufrufe. */
function fenster({ wirft = false } = {}) {
  const f = new EventEmitter();
  f.aufrufe = [];
  f.zerstoert = false;
  f.isDestroyed = () => f.zerstoert;
  f.setAppDetails = (details) => {
    if (wirft) throw new Error("kaputt");
    f.aufrufe.push(["setAppDetails", details]);
  };
  f.setIcon = (symbol) => f.aufrufe.push(["setIcon", symbol]);
  return f;
}

/** Falscher Zeitgeber: hält die Rückrufe fest, bis der Test sie auslöst. */
function zeitgeber() {
  const offen = [];
  const warte = (fn, ms) => {
    const t = { fn, ms, unref() { t.unrefd = true; return t; } };
    offen.push(t);
    return t;
  };
  return { offen, warte };
}

const installiert = { plattform: "win32", istPaketiert: true, execPath: EXE, appDir: "C:\\egal\\app.asar", symbolDatei: ICO_ASAR };

test("Die AppUserModelId ist die appId aus package.json und dieselbe wie in main.js", () => {
  assert.equal(APP_ID, paket.build.appId);
  const main = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
  assert.ok(main.includes(`app.setAppUserModelId("${APP_ID}")`));
  assert.equal(ANZEIGENAME, "W.A.I.S.");
});

test("Installiert: Symbol und Befehl aus der EXE (nie aus der asar), Befehl in Anführungszeichen", () => {
  assert.deepEqual(appDetails(installiert), {
    appId: "de.wamocon.wamo",
    appIconPath: EXE,
    appIconIndex: 0,
    relaunchCommand: `"${EXE}"`,
    relaunchDisplayName: "W.A.I.S.",
  });
});

test("Entwickler: Befehl wie beim Autostart (electron.exe mit App-Ordner), Symbol aus der .ico-Datei", () => {
  const d = appDetails({ istPaketiert: false, execPath: "D:\\w\\electron.exe", appDir: "D:\\w\\desktop", symbolDatei: "D:\\w\\desktop\\assets\\wamo.ico" });
  assert.equal(d.appIconPath, "D:\\w\\desktop\\assets\\wamo.ico");
  assert.equal(d.relaunchCommand, '"D:\\w\\electron.exe" "D:\\w\\desktop"');
  // relaunchCommand und relaunchDisplayName wirken nur zusammen (Electron 44): beide gesetzt, keiner leer
  assert.ok(d.relaunchCommand.length > 2 && d.relaunchDisplayName.length > 0);
});

test("Windows: nach ready-to-show sofort und nach 3 s noch einmal Knopf und Symbol neu setzen", () => {
  const f = fenster();
  const z = zeitgeber();
  assert.equal(taskleisteAbsichern(f, { ...installiert, warte: z.warte }), true);
  assert.deepEqual(f.aufrufe, [], "vor ready-to-show passiert nichts");
  f.emit("ready-to-show");
  assert.deepEqual(f.aufrufe, [["setAppDetails", appDetails(installiert)], ["setIcon", ICO_ASAR]]);
  assert.equal(z.offen.length, 1);
  assert.equal(z.offen[0].ms, NACHLAUF_MS);
  assert.equal(NACHLAUF_MS, 3000);
  assert.equal(z.offen[0].unrefd, true, "der Zeitgeber hält die App nicht am Leben");
  z.offen[0].fn();
  assert.equal(f.aufrufe.length, 4);
  assert.deepEqual(f.aufrufe.slice(2).map((a) => a[0]), ["setAppDetails", "setIcon"]);
  f.emit("ready-to-show"); // nur einmal angehängt
  assert.equal(f.aufrufe.length, 4);
  assert.equal(z.offen.length, 1);
});

test("Andere Plattformen und fehlendes Fenster: nichts angehängt", () => {
  const f = fenster();
  assert.equal(taskleisteAbsichern(f, { ...installiert, plattform: "linux" }), false);
  assert.equal(f.listenerCount("ready-to-show"), 0);
  assert.equal(taskleisteAbsichern(null, installiert), false);
});

test("Zerstörtes Fenster oder Fehler von Electron: kein Absturz, nur ein Logeintrag", () => {
  const f = fenster();
  const z = zeitgeber();
  taskleisteAbsichern(f, { ...installiert, warte: z.warte });
  f.emit("ready-to-show");
  f.zerstoert = true;
  assert.doesNotThrow(() => z.offen[0].fn());
  assert.equal(f.aufrufe.length, 2);

  const kaputt = fenster({ wirft: true });
  const log = [];
  const z2 = zeitgeber();
  taskleisteAbsichern(kaputt, { ...installiert, warte: z2.warte, log: (t) => log.push(t) });
  assert.doesNotThrow(() => kaputt.emit("ready-to-show"));
  assert.doesNotThrow(() => z2.offen[0].fn());
  assert.equal(log.length, 2);
  assert.match(log[0], /kaputt/);
});

test("main.js hängt die Absicherung an das Hauptfenster, Autostart und Einzelinstanz bleiben unverändert", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
  assert.match(main, /taskleisteAbsichern\(mainWin, \{/);
  assert.match(main, /appDir: __dirname,/);
  assert.match(main, /symbolDatei: iconPath\(\),/);
  assert.ok(main.includes("return app.isPackaged ? {} : { path: process.execPath, args: [__dirname] };"));
  assert.ok(main.includes("if (!app.requestSingleInstanceLock()) {"));
});
