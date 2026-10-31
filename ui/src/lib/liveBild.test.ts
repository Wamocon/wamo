// Tests für das Live-Bild in der Oberfläche: Prüfung der Servernachrichten, Zustand ohne Bild im Store, kein Zeichnen ohne
// Ansicht. Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { bildAnzeigen, liveBildLoeschen, restZeit, zeichnerSetzen } from "./liveBild.ts";
import { LIVE_BILD_B64_MAX, parseServerMessage } from "./nachrichten.ts";
import { applyServerMessage, initialState, LIVE_BILD_AUS, resetWamo, wamo } from "./wamo.ts";

const JPEG = "/9j/" + "A".repeat(60);
const frame = (extra: Record<string, unknown> = {}) => ({
  type: "bild",
  seq: 3,
  monitor: 1,
  monitore: 2,
  breite: 800,
  hoehe: 450,
  jpeg: JPEG,
  rest_s: 600,
  ...extra,
});

test("ein gültiges Bild wird mit festen Feldern übernommen", () => {
  const m = parseServerMessage(frame({ extra: "x", titel: "Kundenliste" }));
  assert.deepEqual(m, { type: "bild", seq: 3, monitor: 1, monitore: 2, breite: 800, hoehe: 450, jpeg: JPEG, restS: 600 });
  assert.ok(!JSON.stringify(m).includes("Kundenliste"));
});

test("ungültige Bilder werden verworfen", () => {
  const schlecht = [
    frame({ jpeg: "A".repeat(60) }), // kein JPEG-Kopf
    frame({ jpeg: "/9j/" + "A".repeat(LIVE_BILD_B64_MAX) }), // zu groß
    frame({ jpeg: "/9j/ nicht base64!" }),
    frame({ jpeg: 5 }),
    frame({ seq: 0 }),
    frame({ seq: 1.5 }),
    frame({ monitor: 0 }),
    frame({ monitor: 17 }),
    frame({ breite: 5000 }),
    frame({ hoehe: "450" }),
    frame({ rest_s: -1 }),
    frame({ rest_s: undefined }),
  ];
  for (const s of schlecht) assert.equal(parseServerMessage(s), null, JSON.stringify(s).slice(0, 80));
});

test("Zustandsmeldungen: nur bekannte Zustände, Texte bereinigt", () => {
  const m = parseServerMessage({ type: "bildstrom", zustand: "pausiert", grund: "vollbild", text: "Das Bild‮ ruht", monitor: 2, monitore: 3, rest_s: 90 });
  assert.deepEqual(m, { type: "bildstrom", zustand: "pausiert", grund: "vollbild", text: "Das Bild ruht", monitor: 2, monitore: 3, restS: 90 });
  assert.equal(parseServerMessage({ type: "bildstrom", zustand: "an" }), null);
  const roh = parseServerMessage({ type: "bildstrom", zustand: "aus", grund: "../x", monitor: 99 });
  assert.ok(roh && roh.type === "bildstrom" && roh.grund === undefined && roh.monitor === undefined);
});

test("ohne Öffnen durch die Person ändert eine Zustandsmeldung nichts", () => {
  resetWamo();
  applyServerMessage({ type: "bildstrom", zustand: "laeuft", monitor: 1, monitore: 2, restS: 100 });
  assert.deepEqual(wamo.get().liveBild, LIVE_BILD_AUS);
});

test("geöffnet: Zustand, Restzeit und Ende; ein Bild landet nie im Store", () => {
  resetWamo();
  wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, geoeffnet: true, zustand: "wartet" } }));
  applyServerMessage({ type: "bildstrom", zustand: "laeuft", monitor: 1, monitore: 2, restS: 100 });
  assert.equal(wamo.get().liveBild.zustand, "laeuft");
  assert.equal(wamo.get().liveBild.monitore, 2);
  assert.ok((wamo.get().liveBild.endeMs ?? 0) > Date.now());
  const vorher = JSON.stringify(wamo.get());
  applyServerMessage({ type: "bild", seq: 1, monitor: 1, monitore: 2, breite: 8, hoehe: 8, jpeg: JPEG, restS: 99 });
  assert.equal(JSON.stringify(wamo.get()), vorher);
  assert.ok(!JSON.stringify(wamo.get()).includes(JPEG));
  applyServerMessage({ type: "bildstrom", zustand: "aus", grund: "ende", text: "Gesperrt." });
  const l = wamo.get().liveBild;
  assert.equal(l.geoeffnet, false);
  assert.equal(l.zustand, "aus");
  assert.equal(l.text, "Gesperrt.");
});

test("Zurücksetzen löscht die Ansicht", () => {
  wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, geoeffnet: true, zustand: "laeuft" } }));
  resetWamo();
  assert.deepEqual(wamo.get().liveBild, initialState.liveBild);
});

test("ohne angemeldete Ansicht wird nie gezeichnet", async () => {
  assert.equal(await bildAnzeigen(JPEG), false);
});

test("Ansicht abgemeldet: kein Zeichnen mehr; Löschen ruft das Leeren der Fläche", async () => {
  let geleert = 0;
  const abmelden = zeichnerSetzen(() => assert.fail("darf nicht zeichnen"), () => (geleert += 1));
  liveBildLoeschen();
  assert.equal(geleert, 1);
  abmelden();
  assert.equal(await bildAnzeigen(JPEG), false);
  liveBildLoeschen();
  assert.equal(geleert, 1);
});

test("Restzeit als m:ss, nie negativ", () => {
  assert.equal(restZeit(0), "0:00");
  assert.equal(restZeit(65), "1:05");
  assert.equal(restZeit(900), "15:00");
  assert.equal(restZeit(-4), "0:00");
});
