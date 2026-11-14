// Test für die Zeilen im Block "Gespräch" (lib/gespraech.ts): die letzte Zeile der Person und die letzte Zeile von WAMO,
// jeweils zuerst aus dem laufenden Zug, sonst aus dem Verlauf, und eine unterbrochene Antwort nur mit den gesprochenen Wörtern.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { menschZeile, wamoZeile } from "./gespraech.ts";

const leer = { userDone: "", userLive: "" };
const kein = { text: "", spoken: 0, interrupted: false };

test("die Person: der laufende Beitrag zuerst, fertige und vorläufige Teile zusammen", () => {
  assert.equal(menschZeile({ userDone: "Zeig mir", userLive: "die Testfälle" }, []), "Zeig mir die Testfälle");
  assert.equal(menschZeile({ userDone: "  Hallo ", userLive: "" }, []), "Hallo");
});

test("die Person: ohne laufenden Beitrag gilt der letzte fertige Beitrag aus dem Verlauf", () => {
  const log = [
    { wer: "nutzer" as const, text: "erste Frage" },
    { wer: "wamo" as const, text: "erste Antwort" },
    { wer: "nutzer" as const, text: "zweite Frage" },
    { wer: "wamo" as const, text: "zweite Antwort" },
  ];
  assert.equal(menschZeile(leer, log), "zweite Frage");
  assert.equal(menschZeile(leer, []), "");
});

test("WAMO: der laufende Zug zuerst, sonst die letzte Antwort aus dem Verlauf", () => {
  const log = [{ wer: "wamo" as const, text: "alte Antwort" }];
  assert.equal(wamoZeile({ text: "Hier ist die Übersicht.", spoken: 2, interrupted: false }, log), "Hier ist die Übersicht.");
  assert.equal(wamoZeile(kein, log), "alte Antwort");
  assert.equal(wamoZeile(kein, []), "");
});

test("WAMO: nach einer Unterbrechung zählen nur die gesprochenen Wörter", () => {
  const c = { text: "Vier Testfälle sind offen und einer davon ist kritisch.", spoken: 4, interrupted: true };
  assert.equal(wamoZeile(c, []), "Vier Testfälle sind offen");
});

test("WAMO: eine Unterbrechung vor dem ersten Wort fällt auf den Verlauf zurück", () => {
  const log = [{ wer: "wamo" as const, text: "vorige Antwort" }];
  assert.equal(wamoZeile({ text: "Noch nichts gesagt", spoken: 0, interrupted: true }, log), "vorige Antwort");
});
