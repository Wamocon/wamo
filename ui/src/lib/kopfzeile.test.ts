// Test für die Rangfolge der Statusmodule in der Kopfzeile (components/kommando/kopfzeile.css): Die Module stehen in einer Zeile, was nicht
// passt, bricht in eine abgeschnittene zweite Zeile um (ganze Module fallen weg, nie ein Wort), und die Reihenfolge (order) ist die
// Rangfolge: Büro, Modelle, PC, Mikro und Stimme stehen vor allem, was bei 1440 px nicht zwingend sichtbar sein muss.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../components/kommando/kopfzeile.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

const regel = (selektor: string): string => {
  const i = css.indexOf(`${selektor} {`);
  assert.ok(i >= 0, `${selektor} fehlt in kopfzeile.css`);
  return css.slice(i, css.indexOf("}", i));
};

const rang = (feld: string): number => {
  const treffer = /order:\s*(\d+)/.exec(regel(`.status-items [data-feld="${feld}"]`));
  assert.ok(treffer, `${feld} hat keine Rangfolge (order)`);
  return Number(treffer[1]);
};

test("die Module stehen in einer Zeile mit Umbruch, die zweite Zeile ist abgeschnitten", () => {
  const zeile = regel(".status-items");
  assert.match(zeile, /flex-wrap:\s*wrap/);
  assert.match(zeile, /overflow:\s*hidden/);
  assert.match(zeile, /height:\s*\d+px/, "feste Höhe einer Zeile, sonst bliebe die zweite Zeile sichtbar");
});

test("kein Modul wird mitten im Wort abgeschnitten (kein text-overflow in den Modulen)", () => {
  for (const selektor of [".status-items", ".status-items > div", ".status-items dd"]) {
    assert.doesNotMatch(regel(selektor), /text-overflow/, `${selektor} kürzt mit Auslassungspunkten`);
  }
});

test("Rangfolge: Büro, Modelle, PC, Mikro, Stimme vor PC-Steuerung, Entwicklung und Netz", () => {
  const reihe = ["betriebsart", "modelle", "pc", "mikro", "stimme", "pcsteuerung", "entwicklung", "netz"];
  const raenge = reihe.map(rang);
  assert.deepEqual(raenge, [...raenge].sort((a, b) => a - b), "die Rangfolge steigt in dieser Reihenfolge");
  assert.equal(new Set(raenge).size, raenge.length, "jedes Modul hat einen eigenen Rang");
});

test("das Menü ist unter 1600 px da, damit nichts verloren geht, was die Zeile abschneidet", () => {
  assert.match(css, /@media \(max-width: 1599px\)\s*\{\s*\.menu-btn \{ display: inline-flex; \}/);
});
