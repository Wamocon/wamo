// Tests für die Servernachrichten zur Stimmerkennung (lib/nachrichten.ts): "stimme" (Stand und Ergebnis der letzten Äußerung),
// "stimmabdruck" (Fortschritt des Einlernens) und "stimmabdruck_einwilligung" (Text vom Kern): feste Felder, Wertebereiche,
// bereinigte Texte, alles Unvollständige oder Fremde wird abgelehnt.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage } from "./nachrichten.ts";

test("stimme mit status: Stand der Stimmerkennung, jeder der fünf Werte und jeder Modus", () => {
  for (const status of ["aus", "fehlt", "laedt", "kaputt", "bereit"]) {
    for (const modus of ["aus", "beobachten", "streng"]) {
      assert.deepEqual(parseServerMessage({ type: "stimme", status, modus }), { type: "stimme_status", status, modus });
    }
  }
});

test("stimme mit status: ohne modus bleibt der Modus leer, ein Wert außerhalb der Liste lehnt die Nachricht ab", () => {
  assert.deepEqual(parseServerMessage({ type: "stimme", status: "bereit" }), { type: "stimme_status", status: "bereit", modus: null });
  assert.deepEqual(parseServerMessage({ type: "stimme", status: "bereit", modus: null }), { type: "stimme_status", status: "bereit", modus: null });
  assert.equal(parseServerMessage({ type: "stimme", status: "bereit", modus: "locker" }), null);
  assert.equal(parseServerMessage({ type: "stimme", status: "fertig", modus: "streng" }), null);
  assert.equal(parseServerMessage({ type: "stimme", status: 1, modus: "streng" }), null);
});

test("stimme mit ergebnis: eigen, fremd und unklar mit dem Wert zwischen 0 und 1", () => {
  assert.deepEqual(parseServerMessage({ type: "stimme", ergebnis: "eigen", wert: 0.78, modus: "streng" }), {
    type: "stimme_ergebnis",
    ergebnis: "eigen",
    wert: 0.78,
    modus: "streng",
  });
  assert.equal(parseServerMessage({ type: "stimme", ergebnis: "fremd", wert: 0.31, modus: "beobachten" })?.type, "stimme_ergebnis");
  assert.equal(parseServerMessage({ type: "stimme", ergebnis: "unklar", wert: 0.55, modus: "beobachten" })?.type, "stimme_ergebnis");
});

test("stimme mit ergebnis: der Wert wird auf 0 bis 1 begrenzt, fehlt er, bleibt er leer, ein Unsinnswert lehnt ab", () => {
  const wert = (w: unknown) => {
    const m = parseServerMessage({ type: "stimme", ergebnis: "eigen", wert: w, modus: "streng" });
    return m && m.type === "stimme_ergebnis" ? m.wert : "abgelehnt";
  };
  assert.equal(wert(1.7), 1);
  assert.equal(wert(-0.4), 0);
  assert.equal(wert(null), null);
  assert.equal(wert(undefined), null);
  assert.equal(wert("0.7"), "abgelehnt");
  assert.equal(wert(Number.NaN), "abgelehnt");
  assert.equal(wert(Number.POSITIVE_INFINITY), "abgelehnt");
  assert.equal(parseServerMessage({ type: "stimme", ergebnis: "nikolaj", wert: 0.7, modus: "streng" }), null);
  assert.equal(parseServerMessage({ type: "stimme", ergebnis: "eigen", wert: 0.7, modus: "locker" }), null);
});

test("stimme mit ergebnis: das freiwillige Feld ignoriert (strenger Modus, sicher fremde Äußerung verworfen) wird übernommen", () => {
  assert.deepEqual(parseServerMessage({ type: "stimme", ergebnis: "fremd", wert: 0.12, modus: "streng", ignoriert: true }), {
    type: "stimme_ergebnis",
    ergebnis: "fremd",
    wert: 0.12,
    modus: "streng",
    ignoriert: true,
  });
  const m = parseServerMessage({ type: "stimme", ergebnis: "fremd", wert: 0.3, modus: "streng", ignoriert: false });
  assert.equal(m?.type === "stimme_ergebnis" ? m.ignoriert : "abgelehnt", false);
  // ohne das Feld bleibt die Nachricht, wie sie war (kein erfundenes ignoriert)
  const ohne = parseServerMessage({ type: "stimme", ergebnis: "fremd", wert: 0.3, modus: "streng" });
  assert.ok(ohne && ohne.type === "stimme_ergebnis" && !("ignoriert" in ohne));
  // null zählt wie fehlend, alles andere als ein Wahrheitswert lehnt die Nachricht ab
  const leer = parseServerMessage({ type: "stimme", ergebnis: "fremd", wert: 0.3, modus: "streng", ignoriert: null });
  assert.ok(leer && leer.type === "stimme_ergebnis" && !("ignoriert" in leer));
  for (const unsinn of ["ja", 1, 0, {}, []]) {
    assert.equal(parseServerMessage({ type: "stimme", ergebnis: "fremd", wert: 0.3, modus: "streng", ignoriert: unsinn }), null);
  }
});

test("stimme: genau eine der beiden Formen, sonst wird die Nachricht abgelehnt", () => {
  assert.equal(parseServerMessage({ type: "stimme", modus: "streng" }), null);
  assert.equal(parseServerMessage({ type: "stimme", status: "bereit", ergebnis: "eigen", wert: 0.7, modus: "streng" }), null);
  assert.equal(parseServerMessage({ type: "stimme" }), null);
});

const ABDRUCK = ["start", "aufnahme", "rechnet", "fertig", "fehler", "abgebrochen", "geloescht"];

test("stimmabdruck: alle sieben Zustände werden übernommen, sekunden, ziel und text sind freiwillig", () => {
  for (const status of ABDRUCK) assert.deepEqual(parseServerMessage({ type: "stimmabdruck", status }), { type: "stimmabdruck", status });
  assert.deepEqual(parseServerMessage({ type: "stimmabdruck", status: "aufnahme", sekunden: 8.4, ziel: 20 }), {
    type: "stimmabdruck",
    status: "aufnahme",
    sekunden: 8.4,
    ziel: 20,
  });
  assert.deepEqual(parseServerMessage({ type: "stimmabdruck", status: "fehler", text: "Zu wenig Sprache." }), {
    type: "stimmabdruck",
    status: "fehler",
    text: "Zu wenig Sprache.",
  });
});

test("stimmabdruck: null zählt wie nicht gesendet, ein Zustand außerhalb der Liste lehnt ab", () => {
  assert.deepEqual(parseServerMessage({ type: "stimmabdruck", status: "fertig", sekunden: null, ziel: null, text: null }), {
    type: "stimmabdruck",
    status: "fertig",
  });
  assert.equal(parseServerMessage({ type: "stimmabdruck", status: "läuft" }), null);
  assert.equal(parseServerMessage({ type: "stimmabdruck" }), null);
});

test("stimmabdruck: sekunden und ziel bleiben in festen Grenzen, Unsinn lehnt die Nachricht ab", () => {
  const m = (extra: Record<string, unknown>) => parseServerMessage({ type: "stimmabdruck", status: "aufnahme", ...extra });
  assert.equal((m({ sekunden: -3 }) as { sekunden: number }).sekunden, 0);
  assert.equal((m({ sekunden: 99999 }) as { sekunden: number }).sekunden, 3600);
  assert.equal((m({ ziel: 0 }) as { ziel: number }).ziel, 1);
  assert.equal((m({ ziel: 5000 }) as { ziel: number }).ziel, 600);
  assert.equal(m({ sekunden: "8" }), null);
  assert.equal(m({ ziel: Number.NaN }), null);
  assert.equal(m({ sekunden: Number.POSITIVE_INFINITY }), null);
  assert.equal(m({ text: 42 }), null);
});

test("stimmabdruck: der Text wird von Steuerzeichen befreit, auf eine Zeile gebracht und gekürzt; ein leerer Text entfällt", () => {
  const text = (t: unknown) => (parseServerMessage({ type: "stimmabdruck", status: "fehler", text: t }) as { text?: string }).text;
  assert.equal(text("Zu\u202Ewenig\nSprache.\u0000"), "Zuwenig Sprache.");
  assert.equal(text("   "), undefined);
  assert.ok((text("x".repeat(2000)) ?? "").length <= 420);
});

const EINWILLIGUNG = {
  type: "stimmabdruck_einwilligung",
  version: "stimme-1",
  text: "W.A.I.S. speichert aus deiner Stimme einen Abdruck.\nDu kannst ihn jederzeit löschen.",
};

test("stimmabdruck_einwilligung: Version und Text vom Kern, Zeilenumbrüche bleiben", () => {
  assert.deepEqual(parseServerMessage(EINWILLIGUNG), EINWILLIGUNG);
});

test("stimmabdruck_einwilligung: ohne gültige Version oder ohne Text, mit zu langem Text oder mit fremden Zeichen wird abgelehnt", () => {
  assert.equal(parseServerMessage({ ...EINWILLIGUNG, version: "" }), null);
  assert.equal(parseServerMessage({ ...EINWILLIGUNG, version: "stimme 1" }), null);
  assert.equal(parseServerMessage({ ...EINWILLIGUNG, version: "x".repeat(41) }), null);
  assert.equal(parseServerMessage({ ...EINWILLIGUNG, version: 1 }), null);
  assert.equal(parseServerMessage({ ...EINWILLIGUNG, text: "  " }), null);
  assert.equal(parseServerMessage({ ...EINWILLIGUNG, text: 5 }), null);
  // ein gekürzter Einwilligungstext wäre keiner mehr: lieber ablehnen als abschneiden
  assert.equal(parseServerMessage({ ...EINWILLIGUNG, text: "x".repeat(4001) }), null);
  assert.ok(parseServerMessage({ ...EINWILLIGUNG, text: "x".repeat(4000) }));
});

test("stimmabdruck_einwilligung: Steuer- und Bidi-Zeichen fliegen aus dem Text", () => {
  const m = parseServerMessage({ ...EINWILLIGUNG, text: "Ein\u202Eabdruck\u0007 wird gespeichert." });
  assert.ok(m && m.type === "stimmabdruck_einwilligung");
  assert.equal(m.text, "Einabdruck wird gespeichert.");
});
