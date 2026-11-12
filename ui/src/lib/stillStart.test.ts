// Tests für den stillen Start beim ersten Tippen (lib/stillStart.ts, KP-1A): Schalter je Gerät und Person, Sperre für Laborkonten,
// Startentscheidung, Leerlaufregel (nur für per Tippen gestartete Sitzungen, nie bei PC-Steuerung, Karte, Auftrag), Halten der ersten Zeile.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  GRUND_UEBERNOMMEN,
  HINWEIS_ANDERES_FENSTER,
  istLaborKonto,
  istUebernommen,
  LEERLAUF_MS,
  leerlaufBlockiert,
  leerlaufPruefen,
  schalterLesen,
  schalterSchluessel,
  schalterSchreiben,
  STARTS_FENSTER_MS,
  STARTS_MAX,
  startEntscheidung,
  startNotieren,
  STILL_AUS,
  ZeilenHalter,
  type LeerlaufLage,
  type StartLage,
} from "./stillStart.ts";

const NUTZER = "0b7f3c1e-2d4a-4c55-9a10-1a2b3c4d5e6f";
const ANDERER = "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d";

/** Einfacher Speicher wie localStorage; wirft, wenn gesperrt. */
function speicher(gesperrt = false) {
  const m = new Map<string, string>();
  return {
    m,
    getItem(k: string) {
      if (gesperrt) throw new Error("gesperrt");
      return m.get(k) ?? null;
    },
    setItem(k: string, v: string) {
      if (gesperrt) throw new Error("gesperrt");
      m.set(k, v);
    },
    removeItem(k: string) {
      if (gesperrt) throw new Error("gesperrt");
      m.delete(k);
    },
  };
}

// ---------- Schalter je Gerät und Person ----------

test("Standard ist an, solange nichts gewählt wurde; ohne Speicher und ohne Kennung bleibt er aus", () => {
  assert.equal(schalterLesen(speicher(), NUTZER), true);
  assert.equal(schalterLesen(null, NUTZER), false);
  assert.equal(schalterLesen(speicher(), null), false);
  assert.equal(schalterLesen(speicher(), ""), false);
});

test("der Schlüssel gehört zur Person: zwei Personen am selben Gerät teilen den Schalter nicht", () => {
  const s = speicher();
  assert.equal(schalterSchreiben(s, NUTZER, false), true);
  assert.equal(schalterLesen(s, NUTZER), false);
  assert.equal(schalterLesen(s, ANDERER), true);
  assert.notEqual(schalterSchluessel(NUTZER), schalterSchluessel(ANDERER));
  assert.match(schalterSchluessel(NUTZER)!, /^wamo\.stillStart\./);
});

test("ohne brauchbare Kennung gibt es keinen Schlüssel, nichts wird geschrieben und der Schalter bleibt aus", () => {
  const s = speicher();
  for (const k of [null, undefined, "", "  ", "a/b", "x".repeat(65), "id mit leerzeichen"]) {
    assert.equal(schalterSchluessel(k), null, String(k));
    assert.equal(schalterSchreiben(s, k, true), false);
  }
  assert.equal(s.m.size, 0);
});

test("Ausschalten speichert die ausdrückliche Wahl 'aus', sie bleibt auch nach dem Neuladen aus", () => {
  const s = speicher();
  schalterSchreiben(s, NUTZER, true);
  schalterSchreiben(s, NUTZER, false);
  assert.equal(s.m.get(schalterSchluessel(NUTZER)!), "aus");
  assert.equal(schalterLesen(s, NUTZER), false);
  schalterSchreiben(s, NUTZER, true);
  assert.equal(schalterLesen(s, NUTZER), true);
});

test("gesperrter Speicher (strenger Datenschutzmodus): Lesen ergibt aus, Schreiben meldet false, nichts wirft", () => {
  const s = speicher(true);
  assert.equal(schalterLesen(s, NUTZER), false);
  assert.equal(schalterSchreiben(s, NUTZER, true), false);
});

test("nur die genauen Werte 'an' und 'aus' gelten; jeder andere Wert ist aus (kein 'true', kein '1')", () => {
  const s = speicher();
  for (const v of ["true", "1", "ja", "AN", "an "]) {
    s.m.set(schalterSchluessel(NUTZER)!, v);
    assert.equal(schalterLesen(s, NUTZER), false, v);
  }
  s.m.set(schalterSchluessel(NUTZER)!, "an");
  assert.equal(schalterLesen(s, NUTZER), true);
});

// ---------- Laborkonten ----------

test("Laborkonten (labor-01 bis labor-16, labor-schau) sind erkannt, andere nicht", () => {
  for (const m of ["labor-01@wamo.test", "labor-09@wamo.test", "labor-16@wamo.test", "labor-schau@wamo.test", "LABOR-05@WAMO.TEST", " labor-02@wamo.test "]) {
    assert.equal(istLaborKonto(m), true, m);
  }
  for (const m of ["labor-00@wamo.test", "labor-17@wamo.test", "labor-1@wamo.test", "labor-01@example.com", "niko@wamocon.com", "x@labor-01@wamo.test", "", null, undefined]) {
    assert.equal(istLaborKonto(m), false, String(m));
  }
});

// ---------- Startentscheidung ----------

const bereit: StartLage = {
  schalter: true,
  laborKonto: false,
  sichtbar: true,
  presence: "offline",
  uebernommen: false,
  startZeiten: [],
  jetzt: 1_000_000,
};

test("alles in Ordnung: der erste Tastendruck startet", () => {
  assert.deepEqual(startEntscheidung(bereit), { art: "starten" });
});

test("Schalter aus (Standard): nie starten, ohne Hinweis", () => {
  assert.deepEqual(startEntscheidung({ ...bereit, schalter: false }), { art: "nein", grund: "schalter_aus" });
});

test("Laborkonto: nie starten, auch bei eingeschaltetem Schalter", () => {
  const e = startEntscheidung({ ...bereit, laborKonto: true });
  assert.equal(e.art, "nein");
  assert.equal(e.art === "nein" && e.grund, "labor");
});

test("verdeckter Tab: keine neue Sitzung", () => {
  const e = startEntscheidung({ ...bereit, sichtbar: false });
  assert.equal(e.art === "nein" && e.grund, "verdeckt");
});

test("es läuft schon eine Verbindung oder Sitzung: kein zweiter Start", () => {
  for (const presence of ["connecting", "idle", "listening", "thinking", "speaking"] as const) {
    const e = startEntscheidung({ ...bereit, presence });
    assert.equal(e.art === "nein" && e.grund, "laeuft", presence);
  }
});

test("nach einer Übernahme durch ein anderes Fenster startet das Tippen nicht, der Hinweis nennt das andere Fenster", () => {
  const e = startEntscheidung({ ...bereit, uebernommen: true });
  assert.equal(e.art, "nein");
  assert.equal(e.art === "nein" && e.grund, "uebernommen");
  assert.equal(e.art === "nein" && e.hinweis, HINWEIS_ANDERES_FENSTER);
  assert.match(HINWEIS_ANDERES_FENSTER, /anderen Fenster/);
});

test("höchstens 6 Starts je 10 Minuten, dann Hinweis statt Dauerfeuer", () => {
  const jetzt = 5_000_000;
  const sechs = Array.from({ length: STARTS_MAX }, (_, i) => jetzt - 1000 * (i + 1));
  const e = startEntscheidung({ ...bereit, jetzt, startZeiten: sechs });
  assert.equal(e.art === "nein" && e.grund, "zu_oft");
  assert.ok(e.art === "nein" && e.hinweis);
  // fünf Starts im Fenster reichen noch
  assert.deepEqual(startEntscheidung({ ...bereit, jetzt, startZeiten: sechs.slice(1) }), { art: "starten" });
  // alte Starts außerhalb des Fensters zählen nicht
  const alt = sechs.map((t) => t - STARTS_FENSTER_MS);
  assert.deepEqual(startEntscheidung({ ...bereit, jetzt, startZeiten: alt }), { art: "starten" });
});

test("startNotieren hängt an und verwirft Alte", () => {
  const jetzt = 9_000_000;
  const liste = startNotieren([jetzt - STARTS_FENSTER_MS - 1, jetzt - 5], jetzt);
  assert.deepEqual(liste, [jetzt - 5, jetzt]);
});

// ---------- Leerlauf ----------

const ruhig: LeerlaufLage = {
  perTippen: true,
  online: true,
  presence: "idle",
  pcSteuerung: false,
  karten: 0,
  auftragLaeuft: false,
  vorgangLaeuft: false,
  liveBild: false,
  werkzeugLaeuft: false,
};

test("die Ruhezeit ist eine benannte Konstante: 20 Minuten", () => {
  assert.equal(LEERLAUF_MS, 20 * 60 * 1000);
});

test("nach 20 Minuten ohne Eingabe trennt eine per Tippen gestartete Sitzung, vorher nicht", () => {
  const letzte = 1_000;
  assert.equal(leerlaufPruefen({ jetzt: letzte + LEERLAUF_MS - 1, letzte, lage: ruhig }).trennen, false);
  assert.equal(leerlaufPruefen({ jetzt: letzte + LEERLAUF_MS, letzte, lage: ruhig }).trennen, true);
});

test("eine mit 'Gespräch starten' geklickte Sitzung trennt nie von selbst", () => {
  const r = leerlaufPruefen({ jetzt: 10 * LEERLAUF_MS, letzte: 0, lage: { ...ruhig, perTippen: false } });
  assert.equal(r.trennen, false);
});

test("ohne Sitzung gibt es nichts zu trennen", () => {
  assert.equal(leerlaufPruefen({ jetzt: 10 * LEERLAUF_MS, letzte: 0, lage: { ...ruhig, online: false } }).trennen, false);
});

test("L1: nie trennen bei PC-Steuerung, offener Freigabekarte, laufendem Auftrag oder Vorgang, Live-Bild, laufender Antwort oder Werkzeug", () => {
  const faelle: Array<[string, Partial<LeerlaufLage>]> = [
    ["PC-Steuerung", { pcSteuerung: true }],
    ["Freigabekarte", { karten: 1 }],
    ["Entwicklungsauftrag", { auftragLaeuft: true }],
    ["Vorgang", { vorgangLaeuft: true }],
    ["Live-Bild", { liveBild: true }],
    ["WAMO spricht", { presence: "speaking" }],
    ["WAMO denkt", { presence: "thinking" }],
    ["Person spricht", { presence: "listening" }],
    ["Werkzeug läuft", { werkzeugLaeuft: true }],
  ];
  for (const [name, f] of faelle) {
    const lage = { ...ruhig, ...f };
    assert.notEqual(leerlaufBlockiert(lage), null, name);
    const r = leerlaufPruefen({ jetzt: 3 * LEERLAUF_MS, letzte: 0, lage });
    assert.equal(r.trennen, false, name);
  }
  assert.equal(leerlaufBlockiert(ruhig), null);
});

test("während eine Blockade besteht, beginnt die Ruhezeit jeweils neu (nach dem Auftrag nicht sofort trennen)", () => {
  const blockiert = { ...ruhig, auftragLaeuft: true };
  const r1 = leerlaufPruefen({ jetzt: 50 * LEERLAUF_MS, letzte: 0, lage: blockiert });
  assert.equal(r1.trennen, false);
  assert.equal(r1.letzte, 50 * LEERLAUF_MS);
  // Auftrag zu Ende: erst 20 Minuten später trennen
  assert.equal(leerlaufPruefen({ jetzt: 50 * LEERLAUF_MS + LEERLAUF_MS - 1, letzte: r1.letzte, lage: ruhig }).trennen, false);
  assert.equal(leerlaufPruefen({ jetzt: 50 * LEERLAUF_MS + LEERLAUF_MS, letzte: r1.letzte, lage: ruhig }).trennen, true);
});

test("eine zurückgestellte Uhr trennt nie", () => {
  assert.equal(leerlaufPruefen({ jetzt: 100, letzte: 10 * LEERLAUF_MS, lage: ruhig }).trennen, false);
});

// ---------- Halten der ersten Zeile ----------

test("die gehaltene Zeile geht genau einmal hinaus (kein Doppelversand)", () => {
  const h = new ZeilenHalter();
  assert.equal(h.hat, false);
  assert.equal(h.halten("Wie spät ist es?"), true);
  assert.equal(h.hat, true);
  assert.equal(h.nehmen(), "Wie spät ist es?");
  assert.equal(h.nehmen(), null);
  assert.equal(h.hat, false);
});

test("höchstens eine Zeile wird gehalten, die zweite wird abgelehnt und die erste bleibt", () => {
  const h = new ZeilenHalter();
  assert.equal(h.halten("eins"), true);
  assert.equal(h.halten("zwei"), false);
  assert.equal(h.nehmen(), "eins");
});

test("verwerfen (Abbruch beim Verbinden) lässt nichts mehr hinaus", () => {
  const h = new ZeilenHalter();
  h.halten("eins");
  h.verwerfen();
  assert.equal(h.nehmen(), null);
  assert.equal(h.halten("neu"), true);
});

test("leere oder nur aus Leerraum bestehende Zeilen werden nicht gehalten", () => {
  const h = new ZeilenHalter();
  assert.equal(h.halten(""), false);
  assert.equal(h.halten("   "), false);
  assert.equal(h.hat, false);
});

// ---------- Vertrag mit dem Kern ----------

test("Zustand ohne stillen Start ist der Ausgangszustand", () => {
  assert.deepEqual(STILL_AUS, { still: false, perTippen: false, uebernommen: false });
});

test("Vertrag: der Übernahmetext der Oberfläche stimmt mit dem Kern überein (voice-core/sitzungen.py)", () => {
  const quelle = readFileSync(new URL("../../../voice-core/sitzungen.py", import.meta.url), "utf8");
  const m = /^GRUND_UEBERNOMMEN\s*=\s*"([^"]+)"/m.exec(quelle);
  assert.ok(m, "GRUND_UEBERNOMMEN im Kern nicht gefunden");
  assert.equal(GRUND_UEBERNOMMEN, m[1]);
  assert.equal(istUebernommen(m[1]), true);
  assert.equal(istUebernommen("Die maximale Sitzungsdauer ist erreicht."), false);
  assert.equal(istUebernommen(null), false);
});
