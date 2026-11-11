// Tests für das Testlabor in der Oberfläche (Befund 9, Zugang labor):
//  * Die Servernachricht "labor_durchlauf_ende" wird gelesen und löst genau ein Neuladen der Seite aus (Untertitel und Canvas liegen im
//    Speicher der Seite und dürfen nicht zum nächsten Teilnehmer wandern).
//  * Die Zugangsquelle "labor" (Server-Schlüssel für Laborprofile) kennt die Oberfläche nicht: sie wird ausgeblendet, der Rest der
//    Nachricht bleibt heil.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { NEULADEN_SPERRE_MS, NEULADEN_VERZOEGERUNG_MS, neuladenPlanen } from "./laborNeuladen.ts";
import { parseServerMessage } from "./nachrichten.ts";

// ---------- Nachricht ----------

test("labor_durchlauf_ende wird gelesen, überflüssige Felder fallen weg", () => {
  const m = parseServerMessage({ type: "labor_durchlauf_ende", durchlauf_id: "d-12", text: "<b>Ende</b>" });
  assert.deepEqual(m, { type: "labor_durchlauf_ende" });
});

test("labor_durchlauf_ende ohne weitere Felder", () => {
  assert.deepEqual(parseServerMessage({ type: "labor_durchlauf_ende" }), { type: "labor_durchlauf_ende" });
});

test("ein anderer Typ mit ähnlichem Namen bleibt unbekannt", () => {
  assert.equal(parseServerMessage({ type: "labor_durchlauf" }), null);
  assert.equal(parseServerMessage({ type: "LABOR_DURCHLAUF_ENDE" }), null);
});

// ---------- Quelle labor ----------

test("betrieb mit modell_quelle labor: die Quelle wird ausgeblendet, der Rest bleibt", () => {
  const m = parseServerMessage({
    type: "betrieb",
    modus: "buero",
    modelle: { llm: "Haiku" },
    pc: "aus",
    modell_modus: "automatisch",
    modell_quelle: "labor",
  });
  assert.ok(m && m.type === "betrieb");
  assert.equal(m.modellQuelle, undefined);
  assert.equal(m.modellModus, "automatisch");
  assert.equal(m.pc, "aus");
});

test("modellzugang mit quelle labor: ausgeblendet, kein Absturz", () => {
  const m = parseServerMessage({ type: "modellzugang", aktion: "status", art: "claude", quelle: "labor", hinterlegt: false });
  assert.ok(m && m.type === "modellzugang");
  assert.equal(m.quelle, undefined);
  assert.equal(m.art, "claude");
  assert.equal(m.hinterlegt, false);
});

test("bekannte Quellen gelten weiter", () => {
  for (const q of ["profil", "zuordnung", "festgelegt", "wahl", "keiner", "fehler"]) {
    const m = parseServerMessage({ type: "modellzugang", aktion: "status", quelle: q });
    assert.ok(m && m.type === "modellzugang");
    assert.equal(m.quelle, q);
  }
});

// ---------- Neuladen ----------

function umgebung(start = 1_000_000) {
  const speicher = new Map<string, string>();
  const protokoll = { neuladen: 0, geplant: [] as number[] };
  const zeit = { jetzt: start };
  return {
    protokoll,
    zeit,
    optionen: {
      neuladen: () => {
        protokoll.neuladen += 1;
      },
      jetzt: () => zeit.jetzt,
      speicher: {
        getItem: (k: string) => speicher.get(k) ?? null,
        setItem: (k: string, v: string) => void speicher.set(k, v),
      },
      verzoegern: (fn: () => void, ms: number) => {
        protokoll.geplant.push(ms);
        fn();
        return 0;
      },
    },
  };
}

test("das Neuladen kommt nach kurzer Verzögerung genau einmal", () => {
  const u = umgebung();
  assert.equal(neuladenPlanen(u.optionen), true);
  assert.equal(u.protokoll.neuladen, 1);
  assert.deepEqual(u.protokoll.geplant, [NEULADEN_VERZOEGERUNG_MS]);
  assert.ok(NEULADEN_VERZOEGERUNG_MS >= 500 && NEULADEN_VERZOEGERUNG_MS <= 3000);
});

test("eine zweite Nachricht in kurzer Folge lädt nicht erneut (keine Schleife)", () => {
  const u = umgebung();
  assert.equal(neuladenPlanen(u.optionen), true);
  u.zeit.jetzt += NEULADEN_SPERRE_MS - 1;
  assert.equal(neuladenPlanen(u.optionen), false);
  assert.equal(u.protokoll.neuladen, 1);
});

test("nach der Sperrzeit lädt der nächste Durchlauf wieder neu", () => {
  const u = umgebung();
  neuladenPlanen(u.optionen);
  u.zeit.jetzt += NEULADEN_SPERRE_MS + 1;
  assert.equal(neuladenPlanen(u.optionen), true);
  assert.equal(u.protokoll.neuladen, 2);
});

test("ohne Speicher (privates Fenster, gesperrt) wird trotzdem neu geladen", () => {
  const u = umgebung();
  const kaputt = {
    getItem: () => {
      throw new Error("gesperrt");
    },
    setItem: () => {
      throw new Error("gesperrt");
    },
  };
  assert.equal(neuladenPlanen({ ...u.optionen, speicher: kaputt }), true);
  assert.equal(neuladenPlanen({ ...u.optionen, speicher: null }), true);
  assert.equal(u.protokoll.neuladen, 2);
});

test("ein kaputter Marker im Speicher sperrt nicht dauerhaft", () => {
  const u = umgebung();
  u.optionen.speicher.setItem("wamo.labor.neuladen", "kein-Zahlenwert");
  assert.equal(neuladenPlanen(u.optionen), true);
  u.optionen.speicher.setItem("wamo.labor.neuladen", String(u.zeit.jetzt + 10 * NEULADEN_SPERRE_MS)); // Uhr zurückgestellt
  assert.equal(neuladenPlanen(u.optionen), true);
});
