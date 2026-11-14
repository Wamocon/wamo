// Tests der Aufgabenanzeige (lib/aufgabenanzeige.ts): Werkzeugschritte werden zu einer Aufgabe gebündelt, mit Zustand und Dauer.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  AUFGABE_LEER,
  AUFGABE_MAX_SCHRITTE,
  aufgabeAus,
  aufgabeBeenden,
  aufgabeDauer,
  aufgabeZustand,
  istOffen,
  naechsteAufgabe,
} from "./aufgabenanzeige.ts";
import type { Activity } from "./nachrichten.ts";

const schritt = (id: string, status: Activity["status"], turn = 1): Activity => ({ id, name: "websuche", detail: "", status, turn });

test("der erste Schritt beginnt eine Aufgabe und merkt sich Beginn und Zug", () => {
  const m = naechsteAufgabe(AUFGABE_LEER, schritt("a", "laeuft", 3), 1000);
  assert.equal(m.start, 1000);
  assert.equal(m.zug, 3);
  assert.deepEqual(m.schritte.map((s) => [s.id, s.status, s.startedAt, s.endedAt]), [["a", "laeuft", 1000, 1000]]);
});

test("dieselbe Kennung ist ein Statuswechsel: der Beginn bleibt, das Ende rückt nach", () => {
  let m = naechsteAufgabe(AUFGABE_LEER, schritt("a", "laeuft"), 1000);
  m = naechsteAufgabe(m, schritt("a", "fertig"), 3500);
  assert.equal(m.schritte.length, 1);
  assert.deepEqual([m.schritte[0].status, m.schritte[0].startedAt, m.schritte[0].endedAt], ["fertig", 1000, 3500]);
});

test("weitere Schritte desselben Zugs hängen sich an, der Beginn des Auftrags bleibt", () => {
  let m = naechsteAufgabe(AUFGABE_LEER, schritt("a", "fertig"), 1000);
  m = naechsteAufgabe(m, schritt("b", "laeuft"), 2000);
  assert.equal(m.start, 1000);
  assert.deepEqual(m.schritte.map((s) => s.id), ["a", "b"]);
});

test("nach einer neuen Bitte (neuer Zug) beginnt der nächste Schritt eine neue Aufgabe", () => {
  let m = naechsteAufgabe(AUFGABE_LEER, schritt("a", "fertig", 1), 1000);
  m = naechsteAufgabe(m, schritt("b", "laeuft", 2), 9000);
  assert.equal(m.start, 9000);
  assert.equal(m.zug, 2);
  assert.deepEqual(m.schritte.map((s) => s.id), ["b"]);
});

test("ein neuer Zug beginnt eine neue Aufgabe, auch wenn ein Schritt des alten Zugs noch offen ist", () => {
  let m = naechsteAufgabe(AUFGABE_LEER, schritt("a", "laeuft", 1), 1000);
  m = naechsteAufgabe(m, schritt("b", "laeuft", 2), 2000);
  assert.equal(m.start, 2000);
  assert.equal(m.zug, 2);
  assert.deepEqual(m.schritte.map((s) => s.id), ["b"]);
  // eine späte Meldung zum alten Schritt gehört nicht in die neue Aufgabe
  m = naechsteAufgabe(m, schritt("a", "fertig", 1), 2500);
  assert.deepEqual(m.schritte.map((s) => s.id), ["b"]);
  // weitere Schritte des neuen Zugs hängen sich an
  m = naechsteAufgabe(m, schritt("b", "fertig", 2), 2600);
  m = naechsteAufgabe(m, schritt("c", "laeuft", 2), 2700);
  assert.deepEqual(m.schritte.map((s) => s.id), ["b", "c"]);
});

test("eine Aufgabe zeigt höchstens die letzten 20 Schritte, der Beginn bleibt", () => {
  let m = AUFGABE_LEER;
  for (let i = 0; i < 25; i++) m = naechsteAufgabe(m, schritt(`s${i}`, "fertig"), 1000 + i);
  assert.equal(AUFGABE_MAX_SCHRITTE, 20);
  assert.equal(m.schritte.length, 20);
  assert.equal(m.schritte[0].id, "s5");
  assert.equal(m.schritte[19].id, "s24");
  assert.equal(m.start, 1000);
});

test("Sitzungsende: offene Schritte gelten als abgebrochen, die Aufgabe läuft nicht mehr und die Uhr steht", () => {
  let m = naechsteAufgabe(AUFGABE_LEER, schritt("a", "fertig"), 1000);
  m = naechsteAufgabe(m, schritt("b", "laeuft"), 2000);
  m = naechsteAufgabe(m, schritt("c", "wartet"), 2500);
  const b = aufgabeBeenden(m, 4000);
  assert.deepEqual(b.schritte.map((s) => [s.id, s.status, s.endedAt]), [["a", "fertig", 1000], ["b", "fehler", 4000], ["c", "abgelehnt", 4000]]);
  assert.equal(b.schritte.some(istOffen), false);
  assert.equal(aufgabeZustand(b), "fehler");
  assert.equal(aufgabeDauer(b, 99000), 3000);
  assert.equal(b.beendet, true);
  // ohne Zeitpunkt bleibt der letzte Statuswechsel das Ende
  assert.deepEqual(aufgabeBeenden(m).schritte.map((s) => s.endedAt), [1000, 2000, 2500]);
  // eine leere Aufgabe bleibt leer
  assert.equal(aufgabeBeenden(AUFGABE_LEER), AUFGABE_LEER);
});

test("nach dem Sitzungsende beginnt der nächste Schritt eine neue Aufgabe, auch bei gleicher Kennung und gleichem Zug", () => {
  let m = naechsteAufgabe(AUFGABE_LEER, schritt("1", "laeuft", 4), 1000);
  m = aufgabeBeenden(m, 2000);
  m = naechsteAufgabe(m, schritt("1", "laeuft", 4), 9000);
  assert.deepEqual(m.schritte.map((s) => [s.id, s.status, s.startedAt]), [["1", "laeuft", 9000]]);
  assert.equal(m.start, 9000);
  assert.equal(m.beendet, undefined);
});

test("offen sind nur laufende und wartende Schritte", () => {
  assert.equal(istOffen(schritt("a", "laeuft")), true);
  assert.equal(istOffen(schritt("a", "wartet")), true);
  for (const s of ["fertig", "fehler", "abgelehnt"] as const) assert.equal(istOffen(schritt("a", s)), false, s);
});

test("Gesamtzustand: wartet vor läuft vor Fehler vor erledigt", () => {
  const aus = (...status: Activity["status"][]) => aufgabeZustand({ start: 0, zug: 1, schritte: status.map((s, i) => schritt(`s${i}`, s)) });
  assert.equal(aus("fertig", "laeuft", "wartet"), "wartet");
  assert.equal(aus("fehler", "laeuft"), "laeuft");
  assert.equal(aus("fertig", "abgelehnt"), "fehler");
  assert.equal(aus("fertig", "fehler"), "fehler");
  assert.equal(aus("fertig", "fertig"), "fertig");
});

test("Dauer: läuft noch etwas, zählt sie bis jetzt, sonst bis zum letzten Statuswechsel", () => {
  let m = naechsteAufgabe(AUFGABE_LEER, schritt("a", "laeuft"), 1000);
  assert.equal(aufgabeDauer(m, 4200), 3200);
  m = naechsteAufgabe(m, schritt("a", "fertig"), 3500);
  assert.equal(aufgabeDauer(m, 99000), 2500);
  assert.equal(aufgabeDauer(AUFGABE_LEER, 5000), 0);
  assert.equal(aufgabeDauer(m, 0), 2500);   // die Uhr darf nie rückwärts laufen
});

test("aus einer fertigen Liste (neueste zuerst, wie im Store) entsteht dieselbe Aufgabe", () => {
  const liste: Activity[] = [
    { ...schritt("c", "laeuft", 2), startedAt: 5000 },
    { ...schritt("b", "fertig", 2), startedAt: 3000, endedAt: 4000 },
    { ...schritt("a", "fertig", 1), startedAt: 1000, endedAt: 2000 },
  ];
  const m = aufgabeAus(liste);
  assert.equal(m.zug, 2);
  assert.equal(m.start, 3000);
  assert.deepEqual(m.schritte.map((s) => [s.id, s.startedAt, s.endedAt]), [["b", 3000, 4000], ["c", 5000, 5000]]);
  assert.deepEqual(aufgabeAus([]), AUFGABE_LEER);
});
