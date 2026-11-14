// Test für die Ringe des Kerns (lib/kernRinge.ts): Skalenring, Pegelbogen und die Wahl, wessen Stimme der Bogen zeigt.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BOGEN_ANTEIL,
  BOGEN_RADIUS,
  bogenStrich,
  glaetten,
  pegelAuswahl,
  skalaPfad,
  UMFANG,
} from "./kernRinge.ts";

test("die Skala hat 120 Striche, jeder zehnte ist lang, jeder fünfte mittel, sonst kurz", () => {
  const pfad = skalaPfad();
  const striche = pfad.split("M").filter(Boolean);
  assert.equal(striche.length, 120);
  const laenge = (s: string) => {
    const [a, b] = s.split("L");
    const [x1, y1] = a.trim().split(" ").map(Number);
    const [x2, y2] = b.trim().split(" ").map(Number);
    return Math.hypot(x2 - x1, y2 - y1);
  };
  assert.ok(Math.abs(laenge(striche[0]) - 10) < 0.2, "Strich 0 ist lang");
  assert.ok(Math.abs(laenge(striche[5]) - 6) < 0.2, "Strich 5 ist mittel");
  assert.ok(Math.abs(laenge(striche[1]) - 3) < 0.2, "Strich 1 ist kurz");
});

test("der Umfang des Pegelbogens ist der Kreisumfang auf dem Segmentring", () => {
  assert.ok(Math.abs(UMFANG - 2 * Math.PI * BOGEN_RADIUS) < 1e-9);
});

test("ohne Ton ist der Bogen leer, bei voller Stimme füllt er den festen Anteil des Rings", () => {
  assert.equal(bogenStrich(0), `0.0 ${UMFANG.toFixed(1)}`);
  const voll = bogenStrich(1);
  assert.equal(voll, `${(UMFANG * BOGEN_ANTEIL).toFixed(1)} ${UMFANG.toFixed(1)}`);
  assert.equal(bogenStrich(2), voll, "über 1 wird gekappt");
  assert.equal(bogenStrich(-1), bogenStrich(0), "unter 0 wird gekappt");
});

test("der Pegel wächst schnell und fällt langsam", () => {
  const hoch = glaetten(0, 1);
  const runter = glaetten(1, 0);
  assert.ok(hoch > 0.3 && hoch < 0.4, `Anstieg ${hoch}`);
  assert.ok(1 - runter > 0.05 && 1 - runter < 0.1, `Abfall ${1 - runter}`);
});

test("der lautere Sprecher bestimmt den Bogen: WAMO, Mensch oder eine weitere Stimme", () => {
  const botLaut = pegelAuswahl({ user: 0, bot: 0.1, speaker: 0 }, false);
  assert.equal(botLaut.wer, "bot");
  assert.ok(Math.abs(botLaut.ziel - 0.32) < 1e-9);
  assert.equal(pegelAuswahl({ user: 0.2, bot: 0.05, speaker: 0 }, false).wer, "user");
  assert.equal(pegelAuswahl({ user: 0.2, bot: 0.05, speaker: 1 }, false).wer, "fremd");
  assert.equal(pegelAuswahl({ user: 0.1, bot: 0.1, speaker: 1 }, false).wer, "bot", "bei Gleichstand gewinnt WAMO");
});

test("der Pegel wird auf 1 gekappt und rechnet mit dem Faktor 3,2", () => {
  assert.equal(pegelAuswahl({ user: 0.9, bot: 0, speaker: 0 }, false).ziel, 1);
  assert.ok(Math.abs(pegelAuswahl({ user: 0.1, bot: 0, speaker: 0 }, false).ziel - 0.32) < 1e-9);
});

test("bei reduzierter Bewegung gibt es nur drei Stufen statt Zittern", () => {
  const stufen = new Set([0.02, 0.1, 0.17, 0.2, 0.3, 0.5, 0.9].map((u) => pegelAuswahl({ user: u, bot: 0, speaker: 0 }, true).ziel));
  for (const s of stufen) assert.ok(s === 0 || s === 0.5 || s === 1, `Stufe ${s}`);
  assert.equal(pegelAuswahl({ user: 0, bot: 0, speaker: 0 }, true).ziel, 0);
});
