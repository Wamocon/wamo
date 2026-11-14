// Tests für die Texte der Freigabekarte des Testlabors (lib/laborKarte.ts) und dafür, dass die Oberfläche eine Karte mit Job "labor"
// vollständig übernimmt: jede Zeile, nichts gekürzt, keine Steuer- oder Bidi-Zeichen (lib/nachrichten.ts).
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { abgelaufenHinweis, GERAET_JOB, karteTexte, LABOR_JOB } from "./laborKarte.ts";
import { parseServerMessage } from "./nachrichten.ts";

test("Job labor hat eigene Texte, die PC-Karte und die Entwicklungskarte behalten ihre", () => {
  assert.equal(LABOR_JOB, "labor");
  const labor = karteTexte("labor");
  assert.equal(labor.ueberschrift, "Aktion freigeben");
  assert.equal(labor.hinweis, "Du entscheidest, nicht W.A.I.S.");
  assert.equal(labor.zielLabel, "Ziel:");
  const pc = karteTexte("pc");
  assert.equal(pc.ueberschrift, "PC-Aktion freigeben");
  assert.equal(pc.hinweis, undefined);
  assert.equal(pc.zielLabel, "Ziel (Fenster und Programm):");
  const dev = karteTexte("j-123");
  assert.equal(dev.ueberschrift, "Freigabe nötig");
  assert.equal(dev.hinweis, undefined);
});

test("abgelaufen: bei Labor ist nichts passiert, nur bei PC am PC", () => {
  assert.equal(abgelaufenHinweis("labor"), "Die Freigabe ist abgelaufen; es ist nichts passiert.");
  assert.equal(abgelaufenHinweis("pc"), "Die Freigabe ist abgelaufen; am PC ist nichts passiert.");
  assert.doesNotMatch(abgelaufenHinweis("labor"), /PC/);
});

test("Job geraet (Bürogeräte) hat eigene Texte und sagt nach Ablauf, dass nichts passiert ist", () => {
  assert.equal(GERAET_JOB, "geraet");
  const g = karteTexte("geraet");
  assert.equal(g.ueberschrift, "Bürogerät freigeben");
  assert.equal(g.hinweis, "Du entscheidest, nicht W.A.I.S.");
  assert.equal(g.zielLabel, "Gerät:");
  assert.equal(abgelaufenHinweis("geraet"), "Die Freigabe ist abgelaufen; es ist nichts passiert.");
  assert.doesNotMatch(abgelaufenHinweis("geraet"), /PC/);
});

const KARTE = {
  type: "freigaben",
  job: "labor",
  projekt: "",
  items: [
    {
      id: "0123456789abcdef0123456789abcdef",
      grund: "Ticket anlegen",
      detail: ["Typ: Bug", "Priorität: Hoch", "Titel: Anmeldung schlägt fehl", "Beschreibung:", "> Zeile eins", "> Zeile zwei"].join("\n"),
      von: "Gast",
      ziel: "Test-Jira, Platz 3",
      gueltig_s: 120,
    },
  ],
};

test("die Oberfläche übernimmt die Laborkarte vollständig, jede Zeile in eigener Zeile", () => {
  const m = parseServerMessage(KARTE);
  assert.ok(m && m.type === "freigaben");
  assert.equal(m.job, "labor");
  const it = m.items[0];
  assert.equal(it.id, KARTE.items[0].id);
  assert.equal(it.detail, KARTE.items[0].detail);
  assert.deepEqual(it.detail.split("\n"), ["Typ: Bug", "Priorität: Hoch", "Titel: Anmeldung schlägt fehl", "Beschreibung:", "> Zeile eins", "> Zeile zwei"]);
  assert.equal(it.ziel, "Test-Jira, Platz 3");
  assert.equal(it.gueltigS, 120);
});

test("Kartenzeilen bis 1900 UTF-16-Einheiten kommen ungekürzt an, die Grenze der Oberfläche liegt bei 2000", () => {
  const zeile = "> " + "x".repeat(97); // 99 Zeichen plus Umbruch
  const detail = Array.from({ length: 19 }, () => zeile).join("\n"); // 19 * 99 + 18 = 1899
  const m = parseServerMessage({ ...KARTE, items: [{ ...KARTE.items[0], detail }] });
  assert.ok(m && m.type === "freigaben");
  assert.equal(m.items[0].detail, detail);
  assert.ok(detail.length <= 1900);
  assert.ok(!m.items[0].detail.includes("gekürzt"));
});

test("Emojis zählen zwei Einheiten: die Kürzung der Oberfläche ist der Grund für die Grenze im Kern", () => {
  const detail = "😀".repeat(1100); // 1100 Zeichen, aber 2200 UTF-16-Einheiten: über 2000
  const m = parseServerMessage({ ...KARTE, items: [{ ...KARTE.items[0], detail }] });
  assert.ok(m && m.type === "freigaben");
  assert.ok(m.items[0].detail.includes("gekürzt"));
  assert.equal("😀".length, 2);
});

test("Steuer- und Bidi-Zeichen werden entfernt, Umbrüche bleiben", () => {
  const m = parseServerMessage({ ...KARTE, items: [{ ...KARTE.items[0], detail: "Titel: a\u202eb\u200bc\x07d\n> zweite" }] });
  assert.ok(m && m.type === "freigaben");
  assert.equal(m.items[0].detail, "Titel: abcd\n> zweite");
});
