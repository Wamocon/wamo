// Die Schrift wird selbst ausgeliefert: kein Zugriff auf Google beim Bau und im Betrieb. Geprüft werden die Dateien (Prüfsumme, Lizenz, Herkunft) und
// dass im Quelltext der Oberfläche nichts mehr auf Google-Schriften zeigt.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const FONTS = join(SRC, "app", "fonts");
const SUMMEN: Record<string, string> = {
  "roboto-condensed-latin-400-normal.woff2": "543c03fff71d1b39590af102e0852c2dbce46f4dbe4faaebafbef6edc82e78f5",
  "roboto-condensed-latin-700-normal.woff2": "ab74f0c2d7ec37e44e017b9586675dd00c519591207dc3bbc8e003c5628dc9b7",
};

function quelldateien(ordner: string): string[] {
  const aus: string[] = [];
  for (const name of readdirSync(ordner)) {
    const voll = join(ordner, name);
    if (statSync(voll).isDirectory()) aus.push(...quelldateien(voll));
    else if (/\.(ts|tsx|css|mjs|js)$/.test(name) && !name.endsWith(".test.ts")) aus.push(voll);
  }
  return aus;
}

test("die Schriftdateien sind echte WOFF2-Dateien mit der festgehaltenen Prüfsumme", () => {
  for (const [name, summe] of Object.entries(SUMMEN)) {
    const inhalt = readFileSync(join(FONTS, name));
    assert.equal(inhalt.subarray(0, 4).toString("latin1"), "wOF2", name);
    assert.ok(inhalt.length > 5000 && inhalt.length < 200_000, `${name}: ${inhalt.length} Bytes`);
    assert.equal(createHash("sha256").update(inhalt).digest("hex"), summe, name);
  }
  assert.deepEqual(readdirSync(FONTS).filter((n) => n.endsWith(".woff2")).sort(), Object.keys(SUMMEN).sort());   // keine weitere Schrift unbemerkt
});

test("Lizenz und Herkunft liegen bei den Dateien und nennen dieselben Prüfsummen", () => {
  const lizenz = readFileSync(join(FONTS, "LICENSE-RobotoCondensed-OFL.txt"), "utf8");
  assert.match(lizenz, /SIL Open Font License, Version 1\.1/);
  const herkunft = readFileSync(join(FONTS, "HERKUNFT.md"), "utf8");
  for (const [name, summe] of Object.entries(SUMMEN)) {
    assert.ok(herkunft.includes(name) && herkunft.includes(summe), name);
  }
  assert.match(herkunft, /@fontsource\/roboto-condensed/);
});

test("layout.tsx nutzt next/font/local mit genau diesen Dateien und behält die Variable", () => {
  const layout = readFileSync(join(SRC, "app", "layout.tsx"), "utf8");
  assert.match(layout, /from "next\/font\/local"/);
  assert.doesNotMatch(layout, /next\/font\/google/);
  assert.match(layout, /variable: "--font-condensed"/);
  for (const name of Object.keys(SUMMEN)) assert.ok(layout.includes(`./fonts/${name}`), name);
  const css = readFileSync(join(SRC, "app", "globals.css"), "utf8");
  assert.match(css, /var\(--font-condensed/);   // die Schriftreihenfolge der Oberfläche bleibt: Arial Narrow, Liberation Sans Narrow, Roboto Condensed
});

test("im Quelltext der Oberfläche verweist nichts auf Google-Schriften", () => {
  for (const datei of quelldateien(SRC)) {
    const text = readFileSync(datei, "utf8");
    assert.doesNotMatch(text, /next\/font\/google|fonts\.googleapis\.com|fonts\.gstatic\.com/, datei);
  }
});
