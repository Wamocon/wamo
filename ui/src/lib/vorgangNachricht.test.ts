// Tests für die Servernachricht "vorgang" (lib/nachrichten.ts): feste Felder, Wertebereiche, Grenzen, bereinigte Texte.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage, VORGANG_MAX_KRITERIEN, VORGANG_MAX_SCHRITTE } from "./nachrichten.ts";

const GUT = {
  id: "7d3e9c1a-2b4f-4e6a-8c0d-1f2a3b4c5d6e",
  titel: "Umsetzung: rezepte-app",
  art: "entwicklung",
  status: "laeuft",
  gestartet_ms: 1_700_000_000_000,
  beendet_ms: null,
  gesamt: 0.4,
  restzeit_s: 90,
  schritte: [
    { nr: 1, titel: "Tests schreiben", status: "fertig", fortschritt: 1, detail: null },
    { nr: 2, titel: "Funktion umsetzen", status: "laeuft", fortschritt: 0.5, detail: "Setzt die Funktion um" },
    { nr: 3, titel: "Freigabe: git push", status: "wartet_freigabe", fortschritt: 0, detail: null },
  ],
  kriterien: [
    { text: "Selbsttest bestanden", erfuellt: true, hinweis: null },
    { text: "Keine Freigabe offen", erfuellt: false, hinweis: "1 offen" },
    { text: "Lauf ohne Fehler beendet", erfuellt: null, hinweis: null },
  ],
  runde: 2,
  max_runden: 3,
  freigabe_id: "abcdef0123456789",
  ergebnis: { canvas_id: "dev-abc" },
};
const nachricht = (extra: Record<string, unknown> = {}) => ({ type: "vorgang", vorgang: { ...GUT, ...extra } });

test("gültiger Vorgang wird mit festen Feldern in camelCase übernommen", () => {
  const m = parseServerMessage(nachricht());
  assert.ok(m && m.type === "vorgang");
  const v = m.vorgang;
  assert.equal(v.id, GUT.id);
  assert.equal(v.titel, "Umsetzung: rezepte-app");
  assert.equal(v.art, "entwicklung");
  assert.equal(v.status, "laeuft");
  assert.equal(v.gestartetMs, 1_700_000_000_000);
  assert.equal(v.beendetMs, null);
  assert.equal(v.gesamt, 0.4);
  assert.equal(v.restzeitS, 90);
  assert.equal(v.runde, 2);
  assert.equal(v.maxRunden, 3);
  assert.equal(v.freigabeId, "abcdef0123456789");
  assert.equal(v.canvasId, "dev-abc");
  assert.deepEqual(v.schritte[1], { nr: 2, titel: "Funktion umsetzen", status: "laeuft", fortschritt: 0.5, detail: "Setzt die Funktion um" });
  assert.equal(v.schritte[2].status, "wartet_freigabe");
  assert.deepEqual(v.kriterien.map((k) => k.erfuellt), [true, false, null]);
  assert.equal(v.kriterien[1].hinweis, "1 offen");
});

test("ohne Kennung, Titel oder gültigen Status ist die Nachricht ungültig", () => {
  for (const kaputt of [{ id: "" }, { id: 5 }, { titel: "" }, { titel: "   " }, { titel: 5 }, { status: "kaputt" }, { status: null }]) {
    assert.equal(parseServerMessage(nachricht(kaputt)), null, JSON.stringify(kaputt));
  }
  for (const roh of [{ type: "vorgang" }, { type: "vorgang", vorgang: null }, { type: "vorgang", vorgang: "x" }, { type: "vorgang", vorgang: [] }]) {
    assert.equal(parseServerMessage(roh), null);
  }
});

test("alle sieben Status und vier Arten sind bekannt, eine fremde Art wird sonstiges", () => {
  for (const status of ["geplant", "laeuft", "wartet_freigabe", "pruefen", "fertig", "fehler", "abgebrochen"]) {
    const m = parseServerMessage(nachricht({ status }));
    assert.ok(m && m.type === "vorgang" && m.vorgang.status === status, status);
  }
  for (const art of ["aufgabe", "entwicklung", "geraet", "sonstiges"]) {
    const m = parseServerMessage(nachricht({ art }));
    assert.ok(m && m.type === "vorgang" && m.vorgang.art === art, art);
  }
  const fremd = parseServerMessage(nachricht({ art: "geheim" }));
  assert.ok(fremd && fremd.type === "vorgang");
  assert.equal(fremd.vorgang.art, "sonstiges");
});

test("Zahlen werden auf ihren Bereich gebracht, Unsinn wird zu null oder 0", () => {
  const g = (extra: Record<string, unknown>) => {
    const m = parseServerMessage(nachricht(extra));
    assert.ok(m && m.type === "vorgang");
    return m.vorgang;
  };
  assert.equal(g({ gesamt: 5 }).gesamt, 1);
  assert.equal(g({ gesamt: -1 }).gesamt, 0);
  assert.equal(g({ gesamt: "viel" }).gesamt, 0);
  assert.equal(g({ gesamt: null }).gesamt, 0);
  assert.equal(g({ gesamt: 0.33349 }).gesamt, 0.333);
  assert.equal(g({ restzeit_s: -3 }).restzeitS, null);
  assert.equal(g({ restzeit_s: 1.5 }).restzeitS, null);
  assert.equal(g({ restzeit_s: "bald" }).restzeitS, null);
  assert.equal(g({ restzeit_s: null }).restzeitS, null);
  assert.equal(g({ restzeit_s: 0 }).restzeitS, 0);
  assert.equal(g({ gestartet_ms: "gestern" }).gestartetMs, 0);
  assert.equal(g({ gestartet_ms: -5 }).gestartetMs, 0);
  assert.equal(g({ beendet_ms: 1_700_000_100_000 }).beendetMs, 1_700_000_100_000);
  assert.equal(g({ beendet_ms: "x" }).beendetMs, null);
  assert.equal(g({ runde: 0 }).runde, null);
  assert.equal(g({ runde: 100 }).runde, null);
  assert.equal(g({ runde: 1.5 }).runde, null);
  assert.equal(g({ max_runden: "drei" }).maxRunden, null);
});

test("Texte sind ohne Steuer- und Bidi-Zeichen, einzeilig und gekürzt", () => {
  const m = parseServerMessage(
    nachricht({
      titel: "Titel\u0000 ‮mit\nUmbruch " + "x".repeat(400),
      schritte: [{ nr: 1, titel: "Schritt\u0007\r\n" + "y".repeat(400), status: "offen", fortschritt: 0, detail: "d".repeat(500) }],
      kriterien: [{ text: "k".repeat(400), erfuellt: null, hinweis: "h".repeat(500) }],
    }),
  );
  assert.ok(m && m.type === "vorgang");
  const v = m.vorgang;
  assert.ok(!/[\u0000-\u001F‮]/.test(v.titel));
  assert.ok(v.titel.length <= 200 + " … (gekürzt)".length);
  assert.ok(!/[\u0000-\u001F]/.test(v.schritte[0].titel));
  assert.ok(v.schritte[0].titel.length <= 160 + " … (gekürzt)".length);
  assert.ok((v.schritte[0].detail ?? "").length <= 200 + " … (gekürzt)".length);
  assert.ok(v.kriterien[0].text.length <= 160 + " … (gekürzt)".length);
});

test("Schritte und Kriterien: Grenzen, ungültige Einträge fallen weg, fremder Status wird offen", () => {
  const viele = Array.from({ length: VORGANG_MAX_SCHRITTE + 20 }, (_, i) => ({ titel: `s${i}`, status: "offen", fortschritt: 0 }));
  const m = parseServerMessage(
    nachricht({
      schritte: [...viele, 5, null, { titel: "" }, { status: "fertig" }],
      kriterien: Array.from({ length: VORGANG_MAX_KRITERIEN + 5 }, (_, i) => ({ text: `k${i}`, erfuellt: "ja" })),
    }),
  );
  assert.ok(m && m.type === "vorgang");
  assert.equal(m.vorgang.schritte.length, VORGANG_MAX_SCHRITTE);
  assert.deepEqual(m.vorgang.schritte.slice(0, 3).map((s) => s.nr), [1, 2, 3]); // die Nummer ist die Stelle in der Liste
  assert.equal(m.vorgang.kriterien.length, VORGANG_MAX_KRITERIEN);
  assert.equal(m.vorgang.kriterien[0].erfuellt, null); // nur true und false zählen
  const klein = parseServerMessage(nachricht({ schritte: [{ titel: "A", status: "kaputt", fortschritt: 9 }, "x", { titel: "B", status: "fertig", fortschritt: -1 }] }));
  assert.ok(klein && klein.type === "vorgang");
  assert.deepEqual(klein.vorgang.schritte, [
    { nr: 1, titel: "A", status: "offen", fortschritt: 1, detail: null },
    { nr: 2, titel: "B", status: "fertig", fortschritt: 0, detail: null },
  ]);
  const ohne = parseServerMessage(nachricht({ schritte: "nein", kriterien: { a: 1 } }));
  assert.ok(ohne && ohne.type === "vorgang");
  assert.deepEqual(ohne.vorgang.schritte, []);
  assert.deepEqual(ohne.vorgang.kriterien, []);
});

test("Freigabe-Kennung und Canvas-Kennung nur als kurze Texte, sonst null", () => {
  const g = (extra: Record<string, unknown>) => {
    const m = parseServerMessage(nachricht(extra));
    assert.ok(m && m.type === "vorgang");
    return m.vorgang;
  };
  assert.equal(g({ freigabe_id: null }).freigabeId, null);
  assert.equal(g({ freigabe_id: 5 }).freigabeId, null);
  assert.equal(g({ freigabe_id: "" }).freigabeId, null);
  assert.equal(g({ freigabe_id: "a".repeat(300) }).freigabeId?.length, 100);
  assert.equal(g({ ergebnis: null }).canvasId, null);
  assert.equal(g({ ergebnis: "x" }).canvasId, null);
  assert.equal(g({ ergebnis: { canvas_id: 7 } }).canvasId, null);
  assert.equal(g({ ergebnis: { canvas_id: "" } }).canvasId, null);
});
