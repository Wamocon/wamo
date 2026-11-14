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
  "unbounded-latin-500-normal.woff2": "be65bb9b1a340d1647024392fe3fbf3c21e595eda0395da3138932a01e6a183a",
  "unbounded-latin-700-normal.woff2": "477fa23002517a32f232316d859e7b672a044f20073babec8ed3dbcfdb8e041a",
  "unbounded-latin-800-normal.woff2": "fe6d4ea7ed9647abf1e100a703786338d7898d56d0bedcdfdbc325e8fb02c834",
  "jetbrains-mono-latin-400-normal.woff2": "14425ba9c695763c1547f48a206b7aa60350a33ae23de09f0407877f3fcd89eb",
  "jetbrains-mono-latin-500-normal.woff2": "cb182feeed4d798ff6961d3c79f7026279448fca0676438aaecb21f3fc39553a",
  "jetbrains-mono-latin-600-normal.woff2": "400c6bfda18d5d14acad1c15d6dcb9f8e13c015e7286317e0b9a482539bef147",
  "jetbrains-mono-latin-700-normal.woff2": "d0d4e818808f2a0ba39b2b09d1989366f63494e295f003c7ef436697378507e8",
  "atkinson-hyperlegible-latin-400-normal.woff2": "d64ba838ef5472bba248620ec4fd8b5aa7cf0db2908e0bb230600caf279ba7bc",
  "atkinson-hyperlegible-latin-700-normal.woff2": "140e2bd25a7315c8a062508391426b0d8c3297400c947b8d847be28f73a199f0",
};
// Je Schriftfamilie: Lizenzdatei, npm-Paket der Herkunft und die CSS-Variable aus layout.tsx
const FAMILIEN = [
  { lizenz: "LICENSE-Unbounded-OFL.txt", paket: "@fontsource/unbounded", variable: "--font-display" },
  { lizenz: "LICENSE-JetBrainsMono-OFL.txt", paket: "@fontsource/jetbrains-mono", variable: "--font-hud" },
  { lizenz: "LICENSE-AtkinsonHyperlegible-OFL.txt", paket: "@fontsource/atkinson-hyperlegible", variable: "--font-body" },
] as const;

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
  const herkunft = readFileSync(join(FONTS, "HERKUNFT.md"), "utf8");
  for (const f of FAMILIEN) {
    assert.match(readFileSync(join(FONTS, f.lizenz), "utf8"), /SIL Open Font License, Version 1\.1/, f.lizenz);
    assert.ok(herkunft.includes(f.paket) && herkunft.includes(f.lizenz), f.paket);
  }
  for (const [name, summe] of Object.entries(SUMMEN)) {
    assert.ok(herkunft.includes(name) && herkunft.includes(summe), name);
  }
});

test("layout.tsx nutzt next/font/local mit genau diesen Dateien und setzt die drei Schriftvariablen", () => {
  const layout = readFileSync(join(SRC, "app", "layout.tsx"), "utf8");
  assert.match(layout, /from "next\/font\/local"/);
  assert.doesNotMatch(layout, /next\/font\/google/);
  for (const name of Object.keys(SUMMEN)) assert.ok(layout.includes(`./fonts/${name}`), name);
  const css = readFileSync(join(SRC, "app", "globals.css"), "utf8");
  for (const f of FAMILIEN) {
    assert.ok(layout.includes(`variable: "${f.variable}"`), f.variable);
    assert.ok(css.includes(`var(${f.variable}`), `globals.css nutzt ${f.variable}`);   // Marke und Zahlen, HUD-Beschriftungen, Fließtext
  }
});

test("im Quelltext der Oberfläche verweist nichts auf Google-Schriften", () => {
  for (const datei of quelldateien(SRC)) {
    const text = readFileSync(datei, "utf8");
    assert.doesNotMatch(text, /next\/font\/google|fonts\.googleapis\.com|fonts\.gstatic\.com/, datei);
  }
});
