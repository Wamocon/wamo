// Test für die Farben der Oberfläche (app/globals.css) in beiden Themen, Dunkel (Standard, Block :root) und Hell (Block
// :root[data-theme="light"]): Schrift mindestens 4,5:1 (WCAG 2.1 AA), Ränder und Symbole von Bedienelementen mindestens 3:1,
// das Marken-Rot #F40E0E bleibt in beiden Themen gleich und kleine rote Schrift nimmt den eigenen Ton --core-schrift (Rot auf
// Weiß hat nur 4,27:1). Dazu der gesperrte Zustand der Entscheidungs-Schaltflächen (.btn-decide:disabled): lesbar, ohne
// Transparenz, und der Hover-Wechsel gilt nur für aktive Knöpfe.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
// Die Knöpfe (.btn-decide) sind Bausteine in kommando/hud.css, die Farbrollen stehen in globals.css
const baustein = readFileSync(new URL("../components/kommando/hud.css", import.meta.url), "utf8");

type Thema = "dunkel" | "hell";
const THEMEN: readonly Thema[] = ["dunkel", "hell"];
const KOPF: Record<Thema, string> = { dunkel: ":root {", hell: ':root[data-theme="light"] {' };

function regel(selektor: string): string {
  const quelle = css.includes(`${selektor} {`) ? css : baustein;
  const i = quelle.indexOf(`${selektor} {`);
  assert.ok(i >= 0, `${selektor} fehlt in globals.css und hud.css`);
  return quelle.slice(i, quelle.indexOf("}", i));
}

/** Inhalt des Blocks eines Themas (bis zur schließenden Klammer in der ersten Spalte). */
function block(thema: Thema): string {
  const i = css.indexOf(`\n${KOPF[thema]}`);
  assert.ok(i >= 0, `Block ${KOPF[thema]} fehlt in globals.css`);
  return css.slice(i, css.indexOf("\n}", i));
}

/** Farbwert (#RRGGBB) einer Variablen im Thema; was der Hellmodus nicht ändert, gilt aus dem dunklen Block. */
function variable(thema: Thema, name: string): string {
  for (const t of thema === "hell" ? (["hell", "dunkel"] as const) : (["dunkel"] as const)) {
    const treffer = new RegExp(`\\n\\s*${name}:\\s*(#[0-9a-fA-F]{6})\\b`).exec(block(t));
    if (treffer) return treffer[1].toLowerCase();
  }
  assert.fail(`${name} ist im Thema ${thema} nicht als #RRGGBB gesetzt`);
}

function leuchtdichte(hex: string): number {
  const k = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * k[0] + 0.7152 * k[1] + 0.0722 * k[2];
}

function kontrast(a: string, b: string): number {
  const [hell, dunkel] = [leuchtdichte(a), leuchtdichte(b)].sort((x, y) => y - x);
  return (hell + 0.05) / (dunkel + 0.05);
}

const mindestens = (wert: number, grenze: number, was: string) =>
  assert.ok(wert >= grenze, `${was}: ${wert.toFixed(2)}:1, verlangt ${grenze}:1`);

test("beide Themen sind da: Dunkel als Standard in :root, Hell im Block data-theme=light, je mit color-scheme", () => {
  assert.match(block("dunkel"), /color-scheme:\s*dark/);
  assert.match(block("hell"), /color-scheme:\s*light/);
  assert.match(block("hell"), /--sterne:\s*0/, "im Hellmodus gibt es keine Sterne");
  assert.ok(css.indexOf(KOPF.dunkel) < css.indexOf(KOPF.hell), "der Hellmodus kommt nach :root und übersteuert es");
});

for (const thema of THEMEN) {
  test(`${thema}: gesperrte Schaltfläche ohne Transparenz, Schrift --ink-soft mit mindestens 4,5:1`, () => {
    const gesperrt = regel(".btn-decide:disabled");
    assert.doesNotMatch(gesperrt, /opacity/, "Transparenz drückt den Kontrast unter 4,5:1");
    assert.match(gesperrt, /color:\s*var\(--ink-soft\)/);
    for (const grund of ["--bg", "--surface"]) {
      mindestens(kontrast(variable(thema, "--ink-soft"), variable(thema, grund)), 4.5, `--ink-soft auf ${grund}`);
    }
  });

  test(`${thema}: --ink mindestens 7:1, --ink-faint mindestens 4,5:1, der Rand gesperrter Bedienelemente (--kontur) mindestens 3:1`, () => {
    assert.match(regel(".btn-decide:disabled"), /border-color:\s*var\(--kontur\)/);
    for (const grund of ["--bg", "--surface"]) {
      mindestens(kontrast(variable(thema, "--ink"), variable(thema, grund)), 7, `--ink auf ${grund}`);
      mindestens(kontrast(variable(thema, "--kontur"), variable(thema, grund)), 3, `--kontur auf ${grund}`);
      mindestens(kontrast(variable(thema, "--ink-faint"), variable(thema, grund)), 4.5, `--ink-faint auf ${grund} (Beiwerk, auch Hinweise)`);
    }
  });

  test(`${thema}: das Marken-Rot bleibt #F40E0E, trägt Flächen und große Schrift, dunkle Schrift darauf mindestens 4,5:1`, () => {
    assert.equal(variable(thema, "--core"), "#f40e0e");
    assert.equal(variable(thema, "--accent"), "#f40e0e");
    mindestens(kontrast(variable(thema, "--on-accent"), variable(thema, "--core")), 4.5, "--on-accent auf Rot");
    for (const grund of ["--bg", "--surface"]) {
      mindestens(kontrast(variable(thema, "--core"), variable(thema, grund)), 3, `Rot als Fläche oder große Schrift auf ${grund}`);
    }
  });

  test(`${thema}: kleine rote, gelbe und rosa Schrift (--core-schrift, --warn-schrift, --fehler-schrift) mindestens 4,5:1`, () => {
    for (const name of ["--core-schrift", "--warn-schrift", "--fehler-schrift"]) {
      for (const grund of ["--bg", "--surface"]) {
        mindestens(kontrast(variable(thema, name), variable(thema, grund)), 4.5, `${name} auf ${grund}`);
      }
    }
  });

  test(`${thema}: Fokusring und die Stimmfarben (Balken, Legende) mindestens 3:1 auf dem Grund`, () => {
    for (const name of ["--focus", "--voice-wamo", "--voice-user", "--voice-other"]) {
      mindestens(kontrast(variable(thema, name), variable(thema, "--bg")), 3, `${name} auf --bg`);
    }
  });
}

for (const thema of THEMEN) {
  test(`${thema}: der Rand der Bedienelemente (--steuer-rand, also --kontur) hat mindestens 3:1 auf allen Gründen`, () => {
    for (const grund of ["--bg", "--surface", "--panel-solid"]) {
      mindestens(kontrast(variable(thema, "--kontur"), variable(thema, grund)), 3, `--kontur auf ${grund}`);
    }
  });
}

test("Bedienelemente nehmen die eigene Rolle --steuer-rand (= --kontur), der Hellmodus erbt sie und setzt keine eigene", () => {
  assert.match(block("dunkel"), /--steuer-rand:\s*var\(--kontur\)/, "Bedienelemente nehmen --kontur");
  assert.doesNotMatch(block("hell"), /--steuer-rand:/, "der Hellmodus erbt sie und setzt keine eigene");
  // --rule-strong bleibt den kräftigeren Trennlinien und Marken, nie ein Rand von Bedienelementen (heute 1,9:1 im Dunkelmodus)
  assert.notEqual(variable("dunkel", "--rule-strong"), variable("dunkel", "--kontur"));
});

/** Alle Stylesheets unter src/ (rekursiv), samt Pfad. */
function stylesheets(ordner: URL = new URL("../", import.meta.url)): { pfad: string; text: string }[] {
  const treffer: { pfad: string; text: string }[] = [];
  for (const eintrag of readdirSync(ordner, { withFileTypes: true })) {
    const unter = new URL(eintrag.name + (eintrag.isDirectory() ? "/" : ""), ordner);
    if (eintrag.isDirectory()) treffer.push(...stylesheets(unter));
    else if (eintrag.name.endsWith(".css")) treffer.push({ pfad: fileURLToPath(unter), text: readFileSync(unter, "utf8") });
  }
  return treffer;
}

/** Selektoren, die ein Bedienelement meinen: Knöpfe, Eingabefelder, Auswahllisten, Schalter, Chips, Umschalter. */
const BEDIENELEMENT =
  /(^|[\s>+~(])(button|input|select|textarea)|\.btn-|\.topbar-btn|\.kz-chip|\.kz-pille(?![-\w])|\.kz-wahl(?![-\w])|\.pc-switch(?![-\w])|\.betriebsart-wahl|\.lb-code-feld|\.ez-feld select|\.hud-knopf|\.hud-feld/;
const SCHWACHER_RAND = /border(?:-[a-z]+)?\s*:[^;]*var\(--(?:rule|rule-strong|kante)\)/;

test("kein Bedienelement in irgendeinem Stylesheet zieht seinen Rand aus --rule, --rule-strong oder --kante", () => {
  const verstoesse: string[] = [];
  for (const { pfad, text } of stylesheets()) {
    const ohneKommentare = text.replace(/\/\*[\s\S]*?\*\//g, "");
    for (const m of ohneKommentare.matchAll(/([^{}@;]+)\{([^{}]*)\}/g)) {
      const selektoren = m[1].split(",").map((x) => x.trim());
      if (!selektoren.some((x) => BEDIENELEMENT.test(x))) continue;
      if (SCHWACHER_RAND.test(m[2])) verstoesse.push(`${pfad.split("src")[1]}: ${m[1].trim().slice(0, 70)}`);
    }
  }
  assert.deepEqual(verstoesse, [], "Bedienelemente brauchen --steuer-rand (3:1), Glas- und Kartenkanten dürfen --kante nehmen");
});

test("hell: das Rot für kleine Schrift ist dunkler als das Marken-Rot, bleibt aber rot", () => {
  const rot = variable("hell", "--core-schrift");
  assert.notEqual(rot, "#f40e0e");
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(rot.slice(i, i + 2), 16));
  assert.ok(r > 150 && g < 40 && b < 40, `${rot} ist kein Rot mehr`);
  assert.ok(leuchtdichte(rot) < leuchtdichte("#f40e0e"));
});

test("dunkel: die kleine rote Schrift ist nur einen Hauch heller als das Marken-Rot (das Marken-Rot selbst hat auf Karten 4,4:1)", () => {
  const rot = variable("dunkel", "--core-schrift");
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(rot.slice(i, i + 2), 16));
  assert.ok(r >= 0xf4 && g < 80 && b < 80, `${rot} ist kein Marken-Rot mehr`);
  mindestens(kontrast(rot, variable("dunkel", "--surface")), 4.5, "Rot auf --surface");
});

test("Hover kehrt die Farben nur bei einer aktiven Schaltfläche um", () => {
  assert.ok(baustein.includes(".btn-decide:hover:not(:disabled) {"), "hover braucht :not(:disabled)");
  assert.doesNotMatch(baustein, /\.btn-decide:hover \{/, "kein Hover-Wechsel mehr auf gesperrten Knöpfen");
});
