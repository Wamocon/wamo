// Test für das Farbschema (lib/thema.ts): Dunkel ist der Standard, die Wahl liegt je Gerät im localStorage (jeder Zugriff mit
// try/catch, die Seite läuft auch ohne Speicher), und das kleine Skript im Kopf der Seite setzt data-theme vor dem ersten
// Zeichnen, sodass nichts aufblitzt.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import {
  attributWert,
  INIT_SKRIPT,
  istThema,
  LEISTENFARBE,
  THEMA_SCHLUESSEL,
  themaAusAttribut,
  themaLesen,
  themaSpeichern,
} from "./thema.ts";

const speicherMit = (wert: string | null) => ({ getItem: () => wert });
const speicherDefekt = {
  getItem: () => {
    throw new Error("Zugriff verweigert");
  },
  setItem: () => {
    throw new Error("Zugriff verweigert");
  },
};

test("nur dunkel und hell sind gültige Themen", () => {
  assert.equal(istThema("dunkel"), true);
  assert.equal(istThema("hell"), true);
  for (const w of ["", "light", "dark", null, undefined, 1]) assert.equal(istThema(w), false);
});

test("Dunkel bleibt der Standard: ohne Wert, bei Unsinn und bei defektem Speicher", () => {
  assert.equal(themaLesen(speicherMit(null)), "dunkel");
  assert.equal(themaLesen(speicherMit("blau")), "dunkel");
  assert.equal(themaLesen(speicherDefekt), "dunkel");
  assert.equal(themaLesen(null), "dunkel");
});

test("eine gespeicherte Wahl wird gelesen", () => {
  assert.equal(themaLesen(speicherMit("hell")), "hell");
  assert.equal(themaLesen(speicherMit("dunkel")), "dunkel");
});

test("Speichern legt die Wahl unter dem festen Schlüssel ab und meldet ehrlich, wenn es nicht ging", () => {
  const abgelegt: Record<string, string> = {};
  assert.equal(themaSpeichern("hell", { setItem: (k, v) => void (abgelegt[k] = v) }), true);
  assert.deepEqual(abgelegt, { [THEMA_SCHLUESSEL]: "hell" });
  assert.equal(themaSpeichern("hell", speicherDefekt), false);
  assert.equal(themaSpeichern("hell", null), false);
});

test("das Attribut data-theme heißt light oder dark und wird zurückgelesen", () => {
  assert.equal(attributWert("hell"), "light");
  assert.equal(attributWert("dunkel"), "dark");
  assert.equal(themaAusAttribut("light"), "hell");
  assert.equal(themaAusAttribut("dark"), "dunkel");
  assert.equal(themaAusAttribut(null), "dunkel");
  assert.equal(themaAusAttribut("irgendwas"), "dunkel");
});

/** Das Skript im Kopf der Seite in einer Attrappe der Seite laufen lassen (mit zwei Leisten-Metas, wie nach einer Hydration). */
function skriptLaufen(wert: string | null | "wirft") {
  const gesetzt: Record<string, string> = {};
  const metas: Record<string, string>[] = [{}, {}];
  const meta = metas[0];
  runInNewContext(INIT_SKRIPT, {
    document: {
      documentElement: { setAttribute: (k: string, v: string) => void (gesetzt[k] = v) },
      querySelectorAll: (sel: string) =>
        sel === 'meta[name="theme-color"]' ? metas.map((m) => ({ setAttribute: (k: string, v: string) => void (m[k] = v) })) : [],
    },
    localStorage: {
      getItem: () => {
        if (wert === "wirft") throw new Error("gesperrt");
        return wert;
      },
    },
  });
  return { gesetzt, meta, metas };
}

test("das Kopf-Skript setzt vor dem ersten Zeichnen light bei gespeichertem hell, sonst dark", () => {
  assert.equal(skriptLaufen("hell").gesetzt["data-theme"], "light");
  assert.equal(skriptLaufen("dunkel").gesetzt["data-theme"], "dark");
  assert.equal(skriptLaufen(null).gesetzt["data-theme"], "dark");
  assert.equal(skriptLaufen("blau").gesetzt["data-theme"], "dark");
});

test("das Kopf-Skript bricht bei gesperrtem Speicher nicht ab und bleibt dunkel", () => {
  assert.equal(skriptLaufen("wirft").gesetzt["data-theme"], "dark");
});

test("das Kopf-Skript stellt auch die Farbe der Browserleiste ein", () => {
  assert.equal(skriptLaufen("hell").meta.content, LEISTENFARBE.hell);
  assert.equal(skriptLaufen(null).meta.content, LEISTENFARBE.dunkel);
});

test("das Kopf-Skript stellt jedes meta theme-color ein, nicht nur das erste", () => {
  assert.deepEqual(skriptLaufen("hell").metas.map((m) => m.content), [LEISTENFARBE.hell, LEISTENFARBE.hell]);
  assert.deepEqual(skriptLaufen("dunkel").metas.map((m) => m.content), [LEISTENFARBE.dunkel, LEISTENFARBE.dunkel]);
});
