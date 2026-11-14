// Test für die Farben der Entscheidungs-Schaltflächen (app/globals.css, .btn-decide): Der gesperrte Zustand ("Wird gestartet …") bleibt
// lesbar (WCAG 2.1 AA, 4,5:1 für Text) und ändert nichts mit Transparenz, und der Hover-Wechsel gilt nur für aktive Knöpfe.
// Dazu die Grundfarbe der Oberfläche: das Marken-Rot muss als Schrift und als Fläche lesbar bleiben.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

function regel(selektor: string): string {
  const i = css.indexOf(`${selektor} {`);
  assert.ok(i >= 0, `${selektor} fehlt in globals.css`);
  return css.slice(i, css.indexOf("}", i));
}

/** Variable aus dem Block :root (die Oberfläche hat nur ein Thema: dunkel). */
function variable(name: string): string {
  const i = css.indexOf(":root {");
  assert.ok(i >= 0, ":root");
  const block = css.slice(i, css.indexOf("\n}", i));
  const treffer = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block);
  assert.ok(treffer, name);
  return treffer[1];
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

test("gesperrte Schaltfläche: keine Transparenz, sondern eine Textfarbe mit mindestens 4,5:1", () => {
  const gesperrt = regel(".btn-decide:disabled");
  assert.doesNotMatch(gesperrt, /opacity/, "Transparenz drückt den Kontrast unter 4,5:1");
  assert.match(gesperrt, /color:\s*var\(--ink-soft\)/);
  const verhaeltnis = kontrast(variable("--ink-soft"), variable("--bg"));
  assert.ok(verhaeltnis >= 4.5, `${verhaeltnis.toFixed(2)}:1`);
});

test("der Rand der gesperrten Schaltfläche bleibt erkennbar (3:1 für Bedienelemente)", () => {
  assert.match(regel(".btn-decide:disabled"), /border-color:\s*var\(--kontur\)/);
  assert.ok(kontrast(variable("--kontur"), variable("--bg")) >= 3);
});

test("das Marken-Rot trägt Schrift: Rot auf dem Hintergrund und dunkle Schrift auf Rot je mindestens 4,5:1", () => {
  assert.equal(variable("--core").toLowerCase(), "#f40e0e");
  assert.ok(kontrast(variable("--core"), variable("--bg")) >= 4.5);
  assert.ok(kontrast(variable("--on-accent"), variable("--core")) >= 4.5);
});

test("Hover kehrt die Farben nur bei einer aktiven Schaltfläche um", () => {
  assert.ok(css.includes(".btn-decide:hover:not(:disabled) {"), "hover braucht :not(:disabled)");
  assert.doesNotMatch(css, /\.btn-decide:hover \{/, "kein Hover-Wechsel mehr auf gesperrten Knöpfen");
});
