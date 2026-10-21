// Tests für die Gedächtnis-Karte (kommando/gemerktListe.ts): Aufnehmen, Aktualisieren, Zurücknehmen.
// Ausführen im Ordner ui/:  npm test   (node:test, Node lädt TypeScript selbst)
import assert from "node:assert/strict";
import { test } from "node:test";
import { GEMERKT_MAX, gemerktAbgleichen, gemerktEntfernen } from "./gemerktListe.ts";

type Memo = Parameters<typeof gemerktEntfernen>[0][number];

const memo = (key: number, text: string, extra: Partial<Memo> = {}): Memo => ({
  key,
  text,
  bereich: "persoenlich",
  status: "aktiv",
  paused: false,
  ...extra,
});

test("neue Einträge kommen nach vorn", () => {
  const liste = gemerktAbgleichen([memo(1, "a")], [memo(1, "a"), memo(2, "b")]);
  assert.deepEqual(liste.map((m) => m.key), [2, 1]);
});

test("ohne Änderung bleibt dieselbe Liste (kein Neurendern)", () => {
  const alt = [memo(1, "a")];
  assert.equal(gemerktAbgleichen(alt, [memo(1, "a")]), alt);
  assert.equal(gemerktAbgleichen(alt, []), alt); // abgelaufene Store-Einträge bleiben in der Karte
});

test("gleiche Kennung wird aktualisiert (vorgeschlagen zu aktiv)", () => {
  const alt = [memo(1, "Testlauf", { bereich: "gemeinsam", status: "vorgeschlagen" })];
  const neu = gemerktAbgleichen(alt, [memo(1, "Testlauf", { bereich: "gemeinsam", status: "aktiv" })]);
  assert.equal(neu[0].status, "aktiv");
  assert.equal(neu.length, 1);
});

test("derselbe Text im selben Bereich steht nur einmal", () => {
  const liste = gemerktAbgleichen([memo(1, "a")], [memo(7, "a")]);
  assert.equal(liste.length, 1);
  const andererBereich = gemerktAbgleichen([memo(1, "a")], [memo(7, "a", { bereich: "gemeinsam" })]);
  assert.equal(andererBereich.length, 2);
});

test("höchstens so viele Einträge wie vorgesehen, die ältesten fallen heraus", () => {
  let liste: readonly Memo[] = [];
  for (let i = 1; i <= GEMERKT_MAX + 3; i++) liste = gemerktAbgleichen(liste, [memo(i, `Text ${i}`)]);
  assert.equal(liste.length, GEMERKT_MAX);
  assert.equal(liste[0].key, GEMERKT_MAX + 3);
  assert.equal(liste.some((m) => m.key === 1), false);
});

test("zurückgenommene Einträge verschwinden aus der Liste", () => {
  const alt = [memo(1, "a"), memo(2, "b")];
  assert.deepEqual(gemerktEntfernen(alt, 1).map((m) => m.key), [2]);
  assert.equal(gemerktEntfernen(alt, 99), alt);
});
