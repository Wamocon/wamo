// Test für die Farben der Entscheidungs-Schaltflächen (app/globals.css, .btn-decide): Der gesperrte Zustand ("Wird gestartet …") bleibt
// lesbar (WCAG 2.1 AA, 4,5:1 für Text) und ändert nichts mit Transparenz, und der Hover-Wechsel gilt nur für aktive Knöpfe.
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

/** Variable eines Themas aus dem ersten Block des Selektors. */
function variable(thema: "hell" | "dunkel", name: string): string {
  const kopf = thema === "hell" ? ":root {" : ':root[data-theme="dark"] {';
  const i = css.indexOf(kopf);
  assert.ok(i >= 0, kopf);
  const block = css.slice(i, css.indexOf("}", i));
  const treffer = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block);
  assert.ok(treffer, `${name} im Thema ${thema}`);
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

test("gesperrte Schaltfläche: keine Transparenz, sondern eine Textfarbe mit mindestens 4,5:1 in beiden Themen", () => {
  const gesperrt = regel(".btn-decide:disabled");
  assert.doesNotMatch(gesperrt, /opacity/, "Transparenz drückt den Kontrast unter 4,5:1");
  assert.match(gesperrt, /color:\s*var\(--ink-soft\)/);
  for (const thema of ["hell", "dunkel"] as const) {
    const verhaeltnis = kontrast(variable(thema, "--ink-soft"), variable(thema, "--bg"));
    assert.ok(verhaeltnis >= 4.5, `${thema}: ${verhaeltnis.toFixed(2)}:1`);
  }
});

test("der Rand der gesperrten Schaltfläche bleibt erkennbar (3:1 für Bedienelemente)", () => {
  assert.match(regel(".btn-decide:disabled"), /border-color:\s*var\(--rule-strong\)/);
  for (const thema of ["hell", "dunkel"] as const) {
    assert.ok(kontrast(variable(thema, "--rule-strong"), variable(thema, "--bg")) >= 3, thema);
  }
});

test("Hover kehrt die Farben nur bei einer aktiven Schaltfläche um", () => {
  assert.ok(css.includes(".btn-decide:hover:not(:disabled) {"), "hover braucht :not(:disabled)");
  assert.doesNotMatch(css, /\.btn-decide:hover \{/, "kein Hover-Wechsel mehr auf gesperrten Knöpfen");
});
