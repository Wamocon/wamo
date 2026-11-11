// Tests für die Fristen der Startkarten (lib/auftragFrist.ts): ein Zeitgeber je Auftrag, der bei der Quittung, beim Ersetzen und
// beim Trennen verschwindet. Mit einem falschen Planer, ohne echte Zeit.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { QUITTUNG_FRIST_MS } from "./auftragStart.ts";
import { alleFristenLoeschen, fristLoeschen, fristStarten, offeneFristen } from "./auftragFrist.ts";

/** Falscher Planer: merkt sich Zeitgeber, "vergeht" auf Zuruf. */
function planer() {
  let n = 0;
  const offen = new Map<number, { f: () => void; ms: number }>();
  return {
    offen,
    setze: (f: () => void, ms: number) => {
      offen.set(++n, { f, ms });
      return n;
    },
    loesche: (id: unknown) => void offen.delete(id as number),
    ablaufen: () => {
      for (const [id, t] of [...offen]) {
        offen.delete(id);
        t.f();
      }
    },
  };
}

test("die Frist läuft etwas länger als die 15 Sekunden der Karte und ruft einmal zurück", () => {
  const p = planer();
  alleFristenLoeschen(p);
  let gerufen = 0;
  fristStarten("j1", () => gerufen++, p);
  assert.equal(p.offen.size, 1);
  assert.equal([...p.offen.values()][0].ms, QUITTUNG_FRIST_MS + 100);
  assert.equal(offeneFristen(), 1);
  p.ablaufen();
  assert.equal(gerufen, 1);
  assert.equal(offeneFristen(), 0, "abgelaufen: der Eintrag ist weg");
});

test("eine Quittung löscht den Zeitgeber: nichts läuft später noch ab", () => {
  const p = planer();
  alleFristenLoeschen(p);
  let gerufen = 0;
  fristStarten("j1", () => gerufen++, p);
  fristLoeschen("j1", p);
  assert.equal(p.offen.size, 0, "der Zeitgeber ist wirklich gelöscht, nicht nur vergessen");
  p.ablaufen();
  assert.equal(gerufen, 0);
  fristLoeschen("j1", p); // ein zweites Löschen ist harmlos
  fristLoeschen("unbekannt", p);
});

test("ein neuer Start derselben Karte ersetzt die alte Frist, es gibt höchstens eine je Auftrag", () => {
  const p = planer();
  alleFristenLoeschen(p);
  const gerufen: string[] = [];
  fristStarten("j1", () => gerufen.push("alt"), p);
  fristStarten("j1", () => gerufen.push("neu"), p);
  assert.equal(p.offen.size, 1);
  p.ablaufen();
  assert.deepEqual(gerufen, ["neu"]);
});

test("Fristen verschiedener Aufträge stören sich nicht, Trennen löscht alle", () => {
  const p = planer();
  alleFristenLoeschen(p);
  const gerufen: string[] = [];
  fristStarten("j1", () => gerufen.push("j1"), p);
  fristStarten("j2", () => gerufen.push("j2"), p);
  fristLoeschen("j1", p);
  assert.equal(p.offen.size, 1);
  fristStarten("j3", () => gerufen.push("j3"), p);
  alleFristenLoeschen(p);
  assert.equal(p.offen.size, 0);
  assert.equal(offeneFristen(), 0);
  p.ablaufen();
  assert.deepEqual(gerufen, []);
});

test("ohne Planer gilt der echte: setTimeout, und Löschen räumt ihn auf", async () => {
  alleFristenLoeschen();
  let gerufen = 0;
  fristStarten("echt", () => gerufen++);
  assert.equal(offeneFristen(), 1);
  fristLoeschen("echt");
  assert.equal(offeneFristen(), 0);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(gerufen, 0);
});
