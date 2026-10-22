// Tests für die geführte Anleitung zu den Schlüsseln (lib/schluesselhilfe.ts).
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  API_ANLEITUNG,
  CONSOLE_URL,
  erkenneSchluesselart,
  falschesFeld,
  NICHT_AUTOMATISCH,
  TOKEN_ANLEITUNG,
} from "./schluesselhilfe.ts";

// Platzhalter, die nur wie Schlüssel beginnen (keine echten Werte)
const TOKEN = "sk-ant-oat01-" + "x".repeat(40);
const API = "sk-ant-api03-" + "y".repeat(40);

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

test("Schlüsselart wird nur am Anfang erkannt", () => {
  assert.equal(erkenneSchluesselart(TOKEN), "code");
  assert.equal(erkenneSchluesselart(`  ${TOKEN}  `), "code");
  assert.equal(erkenneSchluesselart(API), "api");
  assert.equal(erkenneSchluesselart("SK-ANT-API03-abc"), "api");
  assert.equal(erkenneSchluesselart("sk-proj-abc"), null);
  assert.equal(erkenneSchluesselart(""), null);
});

test("falsches Feld: sagt, welches Feld richtig ist, und nennt den Wert nie", () => {
  const imApiFeld = falschesFeld("api", TOKEN);
  assert.equal(imApiFeld?.richtig, "code");
  assert.match(imApiFeld?.text ?? "", /Claude-Code-Token, kein API-Schlüssel/);
  assert.match(imApiFeld?.knopf ?? "", /Claude-Code-Token hinterlegen/);

  const imTokenFeld = falschesFeld("code", API);
  assert.equal(imTokenFeld?.richtig, "api");
  assert.match(imTokenFeld?.text ?? "", /API-Schlüssel, kein Claude-Code-Token/);

  for (const h of [imApiFeld, imTokenFeld]) {
    for (const t of [h?.text ?? "", h?.knopf ?? ""]) {
      assert.ok(!t.includes("xxxx") && !t.includes("yyyy"), t);
      assert.ok(!/[–—]/.test(t), t);
    }
  }
});

test("falsches Feld: passender oder unbekannter Wert gibt keinen Hinweis", () => {
  assert.equal(falschesFeld("api", API), null);
  assert.equal(falschesFeld("code", TOKEN), null);
  assert.equal(falschesFeld("api", "irgendwas"), null);
  assert.equal(falschesFeld("code", ""), null);
});
