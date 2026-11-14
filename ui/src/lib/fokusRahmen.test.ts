// Test für den Fokusrahmen (app/globals.css): Ziele, die nur das Programm fokussiert (tabIndex -1: Überschriften der Einstellungen, der
// Einlernansicht und des Kerns, Meldungen), zeigen nach Maus oder Skript keinen Rahmen; mit der Tastatur bleibt er (:focus-visible).
// Kein Stylesheet gibt einem Element über ein bloßes :focus einen Rahmen, sonst sähen Mausnutzer ihn nach jedem Fokuswechsel.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const ohneKommentare = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "");
const globals = ohneKommentare(readFileSync(new URL("../app/globals.css", import.meta.url), "utf8"));

/** Alle Regeln einer Datei als Paare aus Selektorliste und Inhalt (verschachtelte @media-Blöcke werden mit durchsucht). */
function regeln(text: string): { selektoren: string[]; inhalt: string }[] {
  return [...text.matchAll(/([^{}@;]+)\{([^{}]*)\}/g)].map((m) => ({
    selektoren: m[1].split(",").map((s) => s.trim()),
    inhalt: m[2],
  }));
}

function stylesheets(ordner: URL = new URL("../", import.meta.url)): { pfad: string; text: string }[] {
  const treffer: { pfad: string; text: string }[] = [];
  for (const eintrag of readdirSync(ordner, { withFileTypes: true })) {
    const unter = new URL(eintrag.name + (eintrag.isDirectory() ? "/" : ""), ordner);
    if (eintrag.isDirectory()) treffer.push(...stylesheets(unter));
    else if (eintrag.name.endsWith(".css")) treffer.push({ pfad: fileURLToPath(unter), text: ohneKommentare(readFileSync(unter, "utf8")) });
  }
  return treffer;
}

test("programmatisch fokussierte Ziele (tabindex -1) zeigen ohne Tastatur keinen Rahmen", () => {
  const regel = regeln(globals).find((r) => r.selektoren.includes('[tabindex="-1"]:focus:not(:focus-visible)'));
  assert.ok(regel, 'globals.css braucht [tabindex="-1"]:focus:not(:focus-visible)');
  assert.match(regel.inhalt, /outline\s*:\s*none/);
});

test("mit der Tastatur bleibt der Rahmen: :focus-visible setzt ihn weiterhin", () => {
  const regel = regeln(globals).find((r) => r.selektoren.includes(":focus-visible"));
  assert.ok(regel);
  assert.match(regel.inhalt, /outline\s*:\s*2px solid/);
});

test("kein Stylesheet zeichnet einen Rahmen über ein bloßes :focus", () => {
  const verstoesse: string[] = [];
  for (const { pfad, text } of stylesheets()) {
    for (const r of regeln(text)) {
      const bloss = r.selektoren.filter((s) => /:focus(?![-\w])/.test(s) && !/:not\(:focus-visible\)/.test(s));
      if (bloss.length && /outline\s*:\s*(?!none)/.test(r.inhalt)) verstoesse.push(`${pfad.split("src")[1]}: ${bloss.join(", ")}`);
    }
  }
  assert.deepEqual(verstoesse, []);
});
