// Tests für die Nachfrage beim Beenden oder Neuladen, solange ein Entwicklungsauftrag läuft (entladen.js). Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { BEENDEN, FRAGE, entladenBehandeln } = require("./entladen");

/** Falsches Ereignis von will-prevent-unload: merkt sich, ob preventDefault gerufen wurde (das erlaubt das Entladen). */
function ereignis() {
  return {
    erlaubt: false,
    preventDefault() {
      this.erlaubt = true;
    },
  };
}

/** Falscher Dialog: antwortet mit einem festen Index und merkt sich die Aufrufe. */
function dialog(antwort) {
  const aufrufe = [];
  return {
    aufrufe,
    showMessageBoxSync(eltern, optionen) {
      aufrufe.push({ eltern, optionen });
      if (antwort instanceof Error) throw antwort;
      return antwort;
    },
  };
}

test("die Frage ist deutsch, mit echten Umlauten, und Abbrechen ist die sichere Vorgabe", () => {
  assert.equal(FRAGE.message, "Ein Entwicklungsauftrag läuft noch. Trotzdem beenden?");
  assert.deepEqual(FRAGE.buttons, ["Abbrechen", "Beenden"]);
  assert.equal(FRAGE.defaultId, 0, "Eingabetaste bricht ab");
  assert.equal(FRAGE.cancelId, 0, "Esc und Schließen brechen ab");
  assert.equal(BEENDEN, 1);
  assert.equal(FRAGE.buttons[BEENDEN], "Beenden");
  assert.doesNotMatch(JSON.stringify(FRAGE), /laeuft|Entwicklungsauftraege/, "keine Ersatzschreibung für Umlaute");
  assert.doesNotMatch(JSON.stringify(FRAGE), /[–—]/, "keine Gedankenstriche");
});

test("Beenden erlaubt das Entladen (preventDefault)", () => {
  const e = ereignis();
  const d = dialog(BEENDEN);
  assert.equal(entladenBehandeln(e, d, { fenster: 1 }), true);
  assert.equal(e.erlaubt, true);
  assert.equal(d.aufrufe.length, 1);
  assert.deepEqual(d.aufrufe[0].eltern, { fenster: 1 });
  assert.equal(d.aufrufe[0].optionen.message, FRAGE.message);
});

test("Abbrechen lässt das Entladen angehalten: nichts wird aufgerufen", () => {
  const e = ereignis();
  assert.equal(entladenBehandeln(e, dialog(0), null), false);
  assert.equal(e.erlaubt, false);
});

test("jede andere Antwort und ein Fehler des Dialogs lassen es angehalten (im Zweifel nichts beenden)", () => {
  for (const antwort of [undefined, null, 2, -1, "1", true, new Error("Fenster weg")]) {
    const e = ereignis();
    assert.equal(entladenBehandeln(e, dialog(antwort), null), false, String(antwort));
    assert.equal(e.erlaubt, false, String(antwort));
  }
});

test("ein zerstörtes Elternfenster wird nicht übergeben, die Frage erscheint trotzdem", () => {
  const d = dialog(0);
  entladenBehandeln(ereignis(), d, { isDestroyed: () => true });
  assert.equal(d.aufrufe[0].eltern, undefined);
  const d2 = dialog(0);
  const fenster = { isDestroyed: () => false };
  entladenBehandeln(ereignis(), d2, fenster);
  assert.equal(d2.aufrufe[0].eltern, fenster);
});

test("die Optionen des Dialogs sind eine Kopie: ein Aufrufer kann die Vorgabe nicht verändern", () => {
  const d = dialog(0);
  entladenBehandeln(ereignis(), d, null);
  d.aufrufe[0].optionen.buttons.push("Immer");
  assert.deepEqual(FRAGE.buttons, ["Abbrechen", "Beenden"]);
  assert.throws(() => {
    FRAGE.buttons.push("Immer");
  });
});

test("main.js hört auf will-prevent-unload des Hauptfensters und braucht dafür weder IPC noch Änderungen an Preload und Fuses", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
  assert.match(main, /require\("\.\/entladen"\)/);
  assert.match(main, /wc\.on\("will-prevent-unload",\s*\(event\)\s*=>\s*entladenBehandeln\(event,\s*dialog,\s*mainWin\)\)/);
  const preload = fs.readFileSync(path.join(__dirname, "preload.js"), "utf8");
  assert.doesNotMatch(preload, /entlad|prevent-unload/i, "kein neuer Kanal in der Brücke");
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname, "entladen.js"), "utf8"), /ipcMain|ipcRenderer|contextBridge/);
});
