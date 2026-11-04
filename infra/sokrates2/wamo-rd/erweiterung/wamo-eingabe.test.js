// Tests der reinen Teile von wamo-eingabe.js (ohne Browser): node --test wamo-eingabe.test.js (in diesem Ordner)
const test = require("node:test");
const assert = require("node:assert/strict");
const e = require("./wamo-eingabe.js");

test("Zeichen werden zu Tastencodes", () => {
  assert.equal(e.keysymFuer("a"), 0x61);
  assert.equal(e.keysymFuer("A"), 0x41);
  assert.equal(e.keysymFuer("@"), 0x40);
  assert.equal(e.keysymFuer("ä"), 0xe4);
  assert.equal(e.keysymFuer("ß"), 0xdf);
  assert.equal(e.keysymFuer("€"), 0x01000000 + 0x20ac);
  assert.equal(e.keysymFuer("\n"), e.TASTEN.Enter);
  assert.equal(e.keysymFuer("\t"), e.TASTEN.Tab);
  assert.equal(e.keysymFuer("\u0007"), null); // Steuerzeichen ohne Taste
  assert.equal(e.keysymFuer("\u007f"), null);
});

test("Unterschied: nur der Schwanz ändert sich", () => {
  assert.deepEqual(e.unterschied("", "abc"), { loeschen: 0, text: "abc" });
  assert.deepEqual(e.unterschied("abc", "abcd"), { loeschen: 0, text: "d" });
  assert.deepEqual(e.unterschied("abc", "ab"), { loeschen: 1, text: "" });
  assert.deepEqual(e.unterschied("hallo", "halte"), { loeschen: 2, text: "te" });
  assert.deepEqual(e.unterschied("a😀", "a"), { loeschen: 1, text: "" }); // ein Emoji ist ein Zeichen
  assert.deepEqual(e.unterschied("abc", "abc"), { loeschen: 0, text: "" });
});

function falschesClient() {
  const ereignisse = [];
  return { ereignisse, sendKeyEvent: (gedrueckt, code) => ereignisse.push([gedrueckt, code]) };
}

test("Taste: drücken und loslassen, Umschalter davor und danach in umgekehrter Reihenfolge", () => {
  const c = falschesClient();
  e.taste(c, 0x63, [e.TASTEN.Control, e.TASTEN.Alt]);
  assert.deepEqual(c.ereignisse, [
    [1, e.TASTEN.Control], [1, e.TASTEN.Alt], [1, 0x63], [0, 0x63], [0, e.TASTEN.Alt], [0, e.TASTEN.Control],
  ]);
});

test("Text: Zeichen für Zeichen, Zeichen ohne Taste fallen weg", () => {
  const c = falschesClient();
  e.text(c, "hi\u0007!", []);
  assert.deepEqual(c.ereignisse, [[1, 0x68], [0, 0x68], [1, 0x69], [0, 0x69], [1, 0x21], [0, 0x21]]);
});
