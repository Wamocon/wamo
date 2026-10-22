// Tests für die Hilfen des Modellzugangs ohne Gespräch (lib/modellzugangHilfe.ts).
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { fehlerStand, fehlerText, standAusAntwort } from "./modellzugangHilfe.ts";

test("Antwort des Kerns: gültiger Stand wird übernommen, alles andere abgelehnt", () => {
  const stand = standAusAntwort({
    type: "modellzugang",
    aktion: "status",
    art: "haus",
    quelle: "keiner",
    hinterlegt: false,
    festgelegt: false,
    code_hinterlegt: true,
    code_ende: "ab12",
    modus: "automatisch",
  });
  assert.equal(stand?.type, "modellzugang");
  assert.equal(stand?.codeHinterlegt, true);
  assert.equal(stand?.codeEnde, "ab12");
  assert.equal(stand?.modus, "automatisch");

  assert.equal(standAusAntwort(null), null);
  assert.equal(standAusAntwort("text"), null);
  assert.equal(standAusAntwort({ type: "activity" }), null);
  assert.equal(standAusAntwort({ detail: "nicht angemeldet" }), null);
});

test("Fehlertexte sind konkret und nennen nie einen Schlüssel", () => {
  const faelle: [number, string, RegExp][] = [
    [0, "Kern nicht erreichbar", /nicht erreichbar/],
    [0, "Zeitüberschreitung", /nicht rechtzeitig/],
    [401, "x", /Anmeldung ist abgelaufen/],
    [403, "x", /zweiten Faktor|zweiter Faktor|zweitem Faktor/],
    [413, "x", /zu lang/],
    [429, "x", /Zu viele Versuche/],
    [500, "x", /Problem/],
    [503, "x", /Problem/],
    [400, "Ungültige Eingabe", /abgelehnt: Ungültige Eingabe/],
    [404, "", /abgelehnt \(404\)/],
    [404, "Fehler 404", /abgelehnt \(404\)/], // Standardtext von kernApi.ts ist keine Auskunft
  ];
  for (const [status, text, erwartet] of faelle) {
    const t = fehlerText(status, text);
    assert.match(t, erwartet);
    assert.ok(!/[–—]/.test(t), t);
    assert.ok(!/sk-ant-/.test(t), t);
  }
});

test("Fehlerstand trägt nur die Aktion und den Fehler", () => {
  const s = fehlerStand("hinterlegen", "Der Kern ist nicht erreichbar.");
  assert.deepEqual(s, {
    type: "modellzugang",
    aktion: "hinterlegen",
    hinterlegt: false,
    festgelegt: false,
    codeHinterlegt: false,
    fehler: "Der Kern ist nicht erreichbar.",
  });
  assert.equal(s.art, undefined);
});
