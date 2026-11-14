// Test für das WMC-Logo in beiden Themen (app/globals.css, public/): Im Dunkelmodus steht das Logo ohne weiße Platte in
// Weiß und Rot (wmc-logo-dunkel.png, erzeugt mit scripts/wamo_bildmarken_erzeugen.py), im Hellmodus wie bisher schwarz und
// rot auf weißer Platte. Sichtbar ist je Thema genau eine Fassung.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

const ohneKommentare = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "");
const globals = ohneKommentare(readFileSync(new URL("../app/globals.css", import.meta.url), "utf8"));

function regeln(text: string): { selektoren: string[]; inhalt: string }[] {
  return [...text.matchAll(/([^{}@;]+)\{([^{}]*)\}/g)].map((m) => ({
    selektoren: m[1].split(",").map((s) => s.trim().replace(/\s+/g, " ")),
    inhalt: m[2],
  }));
}

const regel = (selektor: string) => regeln(globals).find((r) => r.selektoren.includes(selektor));
const wert = (inhalt: string, name: string) => inhalt.match(new RegExp(`${name}\\s*:\\s*([^;]+);`))?.[1].trim();

test("beide Logodateien liegen in public", () => {
  assert.ok(existsSync(new URL("../../public/wmc-logo.png", import.meta.url)));
  assert.ok(existsSync(new URL("../../public/wmc-logo-dunkel.png", import.meta.url)));
});

test("im Dunkelmodus ist die Platte transparent, im Hellmodus weiß", () => {
  const dunkel = regel(":root");
  const hell = regel(':root[data-theme="light"]');
  assert.ok(dunkel && hell);
  assert.equal(wert(dunkel.inhalt, "--logo-plate"), "transparent");
  assert.equal(wert(hell.inhalt, "--logo-plate"), "#ffffff");
});

test("je Thema ist genau eine Fassung sichtbar", () => {
  const dunkelAus = regel(':root[data-theme="light"] .logo-dunkel');
  const hellAus = regel(':root:not([data-theme="light"]) .logo-hell');
  assert.ok(dunkelAus, "im Hellmodus versteckt globals.css die dunkle Fassung");
  assert.ok(hellAus, "im Dunkelmodus versteckt globals.css die helle Fassung");
  assert.match(dunkelAus.inhalt, /display\s*:\s*none/);
  assert.match(hellAus.inhalt, /display\s*:\s*none/);
});
