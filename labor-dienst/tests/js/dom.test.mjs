import test from "node:test";
import assert from "node:assert/strict";

// Ein winziges Dokument: Es erfasst, was die Oberfläche tut, und wirft, wenn jemand HTML setzen will.
class Knoten {
  constructor(tag) {
    this.tag = tag;
    this.attribute = new Map();
    this.kinder = [];
    this.text = "";
    this.ereignisse = [];
  }
  setAttribute(name, wert) { this.attribute.set(name, String(wert)); }
  append(...k) { this.kinder.push(...k); }
  replaceChildren(...k) { this.kinder = [...k]; }
  addEventListener(art, fn) { this.ereignisse.push([art, fn]); }
  set textContent(wert) { this.text = String(wert); this.kinder = []; }
  get textContent() { return this.text; }
  set innerHTML(_) { throw new Error("innerHTML ist verboten"); }
  set outerHTML(_) { throw new Error("outerHTML ist verboten"); }
}
globalThis.document = { createElement: (tag) => new Knoten(tag), createTextNode: (text) => ({ textknoten: String(text) }) };
const { el, ersetzeKinder, leeren } = await import("../../wamo_labor_dienst/web/dom.js");

test("el setzt Text nur als Text, nie als HTML", () => {
  const n = el("p", { text: "<img src=x onerror=alert(1)>" });
  assert.equal(n.tag, "p");
  assert.equal(n.textContent, "<img src=x onerror=alert(1)>");
  assert.deepEqual(n.kinder, []);
});

test("el hängt Kinder an: Text als Textknoten, Knoten als Knoten, Leeres nicht", () => {
  const kind = el("span", { text: "k" });
  const n = el("div", {}, "Hallo <b>x</b>", kind, null, undefined, false, 7);
  assert.deepEqual(n.kinder, [{ textknoten: "Hallo <b>x</b>" }, kind, { textknoten: "7" }]);
});

test("el setzt Klasse und erlaubte Attribute über setAttribute", () => {
  const n = el("button", { klasse: "knopf gross", attr: { type: "button", "aria-label": "Schließen", "data-nummer": "LAB-1", tabindex: 0, disabled: "" } });
  assert.equal(n.attribute.get("class"), "knopf gross");
  assert.equal(n.attribute.get("type"), "button");
  assert.equal(n.attribute.get("aria-label"), "Schließen");
  assert.equal(n.attribute.get("data-nummer"), "LAB-1");
  assert.equal(n.attribute.get("tabindex"), "0");
});

test("el lehnt Ereignisattribute, Stil, Adressen und fremde Attribute ab", () => {
  for (const name of ["onclick", "onerror", "ONLOAD", "style", "srcdoc", "href", "src", "action", "formaction", "xlink:href", "innerHTML", "is", "autofocus-x", ""]) {
    assert.throws(() => el("div", { attr: { [name]: "x" } }), TypeError, name);
  }
});

test("el nimmt Ereignisse nur als Funktionen über addEventListener", () => {
  const fn = () => {};
  const n = el("button", { ereignisse: { click: fn } });
  assert.deepEqual(n.ereignisse, [["click", fn]]);
  assert.throws(() => el("button", { ereignisse: { click: "alert(1)" } }), TypeError);
});

test("leeren und ersetzeKinder arbeiten ohne HTML", () => {
  const n = el("div", {}, "a", "b");
  ersetzeKinder(n, "neu", el("i", { text: "x" }));
  assert.equal(n.kinder.length, 2);
  assert.deepEqual(n.kinder[0], { textknoten: "neu" });
  leeren(n);
  assert.deepEqual(n.kinder, []);
});

const { verweis } = await import("../../wamo_labor_dienst/web/dom.js");

test("verweis baut nur den Link auf den Datenschutzhinweis, in neuem Tab ohne Opener und Referrer", () => {
  const a = verweis("Hinweis für den Besucher anzeigen", "/datenschutz");
  assert.equal(a.tag, "a");
  assert.equal(a.textContent, "Hinweis für den Besucher anzeigen");
  assert.equal(a.attribute.get("href"), "/datenschutz");
  assert.equal(a.attribute.get("target"), "_blank");
  assert.equal(a.attribute.get("rel"), "noopener noreferrer");
});

test("verweis lehnt jede andere Adresse ab", () => {
  for (const pfad of ["javascript:alert(1)", "https://fremd.example/", "//fremd.example", "/board", "/datenschutz/", "/Datenschutz", "datenschutz", "", null, undefined]) {
    assert.throws(() => verweis("x", pfad), TypeError, String(pfad));
  }
});
