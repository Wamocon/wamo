// Tests für den Vollbild-Wächter des Stimmen-Rands (vollbild.js). Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { spawn } = require("node:child_process");
const { test } = require("node:test");
const {
  ABFRAGE_MS,
  AUFGEBEN_NACH,
  Entpreller,
  NEUSTART_MS,
  NEUSTART_MAX_MS,
  VOLLBILD_WERTE,
  kodiert,
  powershellPfad,
  starteVollbildWaechter,
  waechterSkript,
} = require("./vollbild");

function falschesKind() {
  const kind = new EventEmitter();
  kind.stdout = new EventEmitter();
  kind.stdout.setEncoding = () => {};
  kind.stderr = new EventEmitter();
  kind.stderr.setEncoding = () => {};
  kind.kill = () => {
    kind.getoetet = true;
  };
  return kind;
}

/** Wächter mit falschen Kindern, falscher Uhr und falschen Timern. */
function aufbau({ jetzt = [0] } = {}) {
  const kinder = [];
  const timer = [];
  const wechsel = [];
  const logs = [];
  const w = starteVollbildWaechter({
    pid: 4242,
    onWechsel: (an) => wechsel.push(an),
    spawnFn: (programm, argv, optionen) => {
      assert.equal(programm, powershellPfad());
      assert.ok(argv.includes("-EncodedCommand") && argv.includes("-NonInteractive"));
      assert.equal(optionen.windowsHide, true);
      const k = falschesKind();
      kinder.push(k);
      return k;
    },
    setTimeoutFn: (fn, ms) => {
      timer.push({ fn, ms });
      return timer.length;
    },
    clearTimeoutFn: () => {},
    jetztFn: () => jetzt[0],
    log: (text) => logs.push(text),
  });
  return { w, kinder, timer, wechsel, logs, jetzt };
}

test("Vollbild, Direct3D und Präsentation zählen, Wert 7 nicht", () => {
  assert.deepEqual([...VOLLBILD_WERTE].sort(), [2, 3, 4]);
  assert.equal(VOLLBILD_WERTE.has(7), false);
  assert.equal(ABFRAGE_MS, 1500);
  assert.equal(NEUSTART_MS, 5000);
});

test("Skript fragt SHQueryUserNotificationState ab und endet mit der App (Handle, nicht PID-Abfrage)", () => {
  const skript = waechterSkript(4242);
  assert.match(skript, /SHQueryUserNotificationState/);
  assert.match(skript, /GetProcessById\(4242\)/);
  assert.match(skript, /WaitForExit\(1500\)/);
  assert.equal(Buffer.from(kodiert(skript), "base64").toString("utf16le"), skript);
  for (const pid of [0, -1, 1.5, "4242; calc", null]) assert.throws(() => waechterSkript(pid));
});

test("PowerShell mit vollem Pfad aus dem Windows-Ordner", () => {
  assert.match(powershellPfad({ SystemRoot: "C:\\Windows" }), /^C:\\Windows\\System32\\WindowsPowerShell\\v1\.0\\powershell\.exe$/);
});

test("erst nach zwei gleichen Werten wird umgeschaltet", () => {
  const wechsel = [];
  const e = new Entpreller((an) => wechsel.push(an));
  e.wert("5");
  e.wert("2"); // einzelner Ausreißer: noch nichts
  assert.deepEqual(wechsel, []);
  e.wert("2");
  assert.deepEqual(wechsel, [true]);
  e.wert("3"); // 2 und 3 sind beide Vollbild: kein neuer Wechsel
  e.wert("5");
  assert.deepEqual(wechsel, [true]);
  e.wert("5");
  assert.deepEqual(wechsel, [true, false]);
  e.wert("7");
  e.wert("7"); // Store-App: Rand bleibt
  assert.deepEqual(wechsel, [true, false]);
});

test("Unsinn in der Ausgabe wird ignoriert", () => {
  const wechsel = [];
  const e = new Entpreller((an) => wechsel.push(an));
  for (const z of ["", "abc", "2.5", "99", "-1", "2"]) e.wert(z);
  assert.deepEqual(wechsel, []); // "2" allein reicht nicht
  e.wert(" 2 \r");
  assert.deepEqual(wechsel, [true]);
});

test("Wächter liest Zeilen, startet nach Absturz nach 5 s neu und endet mit stop", () => {
  const { w, kinder, timer, wechsel } = aufbau();
  kinder[0].stdout.emit("data", "5\r\n2\r\n2");
  assert.deepEqual(wechsel, []); // letzte Zeile ist noch unvollständig
  kinder[0].stdout.emit("data", "\r\n");
  assert.deepEqual(wechsel, [true]);
  assert.equal(w.vollbild, true);
  kinder[0].emit("exit", 1);
  assert.equal(timer.length, 1);
  assert.equal(timer[0].ms, 5000);
  timer[0].fn();
  assert.equal(kinder.length, 2);
  w.stop();
  assert.equal(kinder[1].getoetet, true);
  kinder[1].emit("exit", 1); // Ende nach stop: kein Neustart
  kinder[1].stdout.emit("data", "5\n5\n"); // späte Ausgabe nach stop: keine Meldung mehr
  assert.equal(timer.length, 1);
  assert.deepEqual(wechsel, [true]);
});

test("Startfehler führt zum Neustart statt zum Absturz", () => {
  const timer = [];
  starteVollbildWaechter({
    pid: 4242,
    onWechsel: () => {},
    spawnFn: () => {
      throw new Error("ENOENT");
    },
    setTimeoutFn: (fn, ms) => timer.push({ fn, ms }),
    clearTimeoutFn: () => {},
    log: () => {},
  });
  assert.equal(timer.length, 1);
  assert.equal(timer[0].ms, 5000);
});

test("Dauerfehler: wachsende Pause, Diagnose ins Log, dann aufgeben und den Rand wieder zeigen", () => {
  const { kinder, timer, wechsel, logs, jetzt } = aufbau();
  kinder[0].stdout.emit("data", "3\n3\n"); // Vollbild, Rand aus
  assert.deepEqual(wechsel, [true]);
  for (let i = 0; i < AUFGEBEN_NACH; i++) {
    const k = kinder[kinder.length - 1];
    k.stderr.emit("data", "Add-Type : Die Sprache ist eingeschränkt");
    jetzt[0] += 1000; // jeweils nach einer Sekunde wieder tot
    k.emit("exit", 1);
    if (i < AUFGEBEN_NACH - 1) timer[timer.length - 1].fn();
  }
  // nach dem letzten Fehlschlag wird nicht mehr geplant, sondern aufgegeben
  assert.deepEqual(timer.map((t) => t.ms), [5000, 10000, 20000, 40000, 60000].slice(0, AUFGEBEN_NACH - 1));
  assert.ok(timer.every((t) => t.ms <= NEUSTART_MAX_MS));
  assert.equal(kinder.length, AUFGEBEN_NACH);
  assert.deepEqual(wechsel, [true, false]); // aufgegeben: Rand nicht für immer weg
  assert.ok(logs.some((l) => l.includes("Code 1") && l.includes("eingeschränkt")));
  assert.ok(logs.some((l) => l.includes("aufgegeben")));
});

test("gültige Ausgabe setzt die Fehlerzählung zurück", () => {
  const { kinder, timer } = aufbau();
  kinder[0].emit("exit", 1);
  timer[0].fn();
  kinder[1].stdout.emit("data", "5\n");
  kinder[1].emit("exit", 1);
  assert.equal(timer[1].ms, 5000); // wieder die kurze Pause
});

test("spätes error nach exit startet keinen zweiten Wächter, zweites error wirft nicht", () => {
  const { kinder, timer } = aufbau();
  kinder[0].emit("exit", 1);
  timer[0].fn(); // Neustart: Kind 1 läuft
  kinder[0].emit("error", new Error("spät")); // altes Kind meldet noch etwas
  kinder[0].emit("error", new Error("noch später"));
  assert.equal(timer.length, 1);
  assert.equal(kinder.length, 2);
});

test("Fehler im Rückruf beendet die Auswertung nicht", () => {
  const kinder = [];
  let aufrufe = 0;
  starteVollbildWaechter({
    pid: 4242,
    onWechsel: () => {
      aufrufe += 1;
      throw new Error("Fenster schon zu");
    },
    spawnFn: () => {
      const k = falschesKind();
      kinder.push(k);
      return k;
    },
    setTimeoutFn: () => 1,
    clearTimeoutFn: () => {},
    log: () => {},
  });
  kinder[0].stdout.emit("data", "2\n2\n5\n5\n");
  assert.equal(aufrufe, 2);
});

test("viele Zeilen auf einmal: vollständige Zeilen zählen, nur der Rest ist begrenzt", () => {
  const { kinder, wechsel } = aufbau();
  kinder[0].stdout.emit("data", "5\n".repeat(3000) + "2\n2\n");
  assert.deepEqual(wechsel, [true]);
});

test("echter Wächter liefert einen Zustand und endet mit seinem Elternprozess (nur Windows)", { skip: process.platform !== "win32" }, async () => {
  // kurzlebiger Elternprozess: der Wächter muss kurz nach dessen Ende selbst aufhören
  const eltern = spawn(process.execPath, ["-e", "setTimeout(() => {}, 4000)"], { stdio: "ignore" });
  const kind = spawn(powershellPfad(), ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand",
    kodiert(waechterSkript(eltern.pid))], { windowsHide: true, stdio: ["ignore", "pipe", "ignore"] });
  try {
    const zeile = await new Promise((resolve, reject) => {
      const frist = setTimeout(() => reject(new Error("keine Ausgabe nach 20 s")), 20000);
      let puffer = "";
      kind.stdout.setEncoding("utf8");
      kind.stdout.on("data", (d) => {
        puffer += d;
        const i = puffer.indexOf("\n");
        if (i >= 0) {
          clearTimeout(frist);
          resolve(puffer.slice(0, i).trim());
        }
      });
    });
    const wert = Number(zeile);
    assert.ok(Number.isInteger(wert) && wert >= 0 && wert <= 7, `Wert ${zeile}`);
    eltern.kill();
    const code = await new Promise((resolve, reject) => {
      const frist = setTimeout(() => reject(new Error("Wächter lief nach dem Ende der App weiter")), 8000);
      kind.once("exit", (c) => {
        clearTimeout(frist);
        resolve(c);
      });
    });
    assert.equal(code, 0);
  } finally {
    eltern.kill();
    kind.kill();
  }
});
