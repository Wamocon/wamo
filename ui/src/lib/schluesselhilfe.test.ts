// Tests für die geführte Anleitung zu den Schlüsseln (lib/schluesselhilfe.ts).
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { API_ANLEITUNG, CONSOLE_URL, NICHT_AUTOMATISCH, TOKEN_ANLEITUNG } from "./schluesselhilfe.ts";

const alleTexte = [API_ANLEITUNG, TOKEN_ANLEITUNG].flatMap((a) => [a.ueberschrift, a.zweck, ...a.schritte.map((s) => s.text)]);

test("API-Anleitung: Konsole, Ausgabenlimit, nur einmal sichtbar", () => {
  const text = API_ANLEITUNG.schritte.map((s) => s.text).join(" ");
  assert.equal(API_ANLEITUNG.schritte[0].link?.url, CONSOLE_URL);
  assert.equal(new URL(CONSOLE_URL).protocol, "https:");
  assert.equal(API_ANLEITUNG.schritte[0].link?.wort, "console.anthropic.com");
  assert.match(text, /Ausgabenlimit/);
  assert.match(text, /nur einmal/);
  assert.ok(API_ANLEITUNG.schritte.length >= 3 && API_ANLEITUNG.schritte.length <= 5);
});

test("Token-Anleitung: setup-token im Terminal", () => {
  assert.equal(TOKEN_ANLEITUNG.schritte[1].befehl, "claude setup-token");
  assert.match(TOKEN_ANLEITUNG.zweck, /nur für Entwicklungsaufträge/);
  assert.match(TOKEN_ANLEITUNG.zweck, /nur das Format/);
});

test("nichts wird automatisch erzeugt", () => {
  assert.match(NICHT_AUTOMATISCH, /eigenen Anmeldung/);
});

test("Texte: keine Gedankenstriche, kein echter Schlüssel", () => {
  for (const t of [...alleTexte, NICHT_AUTOMATISCH]) {
    assert.ok(!/[–—]/.test(t), t);
    assert.ok(!/sk-ant-[A-Za-z0-9_-]{20,}/.test(t), t);
  }
});
