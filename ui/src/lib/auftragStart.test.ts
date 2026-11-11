// Tests für die Entscheidungslogik der Startkarte eines Entwicklungsauftrags (lib/auftragStart.ts): Quittung des Kerns,
// Frist von 15 Sekunden, Zustand der Karte, Warnung vor dem Neuladen. Reine Funktionen, keine Uhr, kein React.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  QUITTUNG_FRIST_MS,
  auftraegeNachFrist,
  auftraegeNachQuittung,
  auftraegeNachStart,
  auftragLaeuft,
  auftragStartbar,
  auftragVerwerfbar,
  kartenAnsicht,
  repoAnzeige,
} from "./auftragStart.ts";
import type { Auftrag } from "./nachrichten.ts";

const A: Auftrag = { job: "j1", projekt: "kern-testrepo", art: "Analyse", auftrag: "Prüfe das Repo", abnahme: [], repo: "acme/testrepo" };
const B: Auftrag = { job: "j2", projekt: "kern-marktstudie", art: "Recherche", auftrag: "Recherchiere", abnahme: [] };

test("die Frist beträgt 15 Sekunden", () => {
  assert.equal(QUITTUNG_FRIST_MS, 15_000);
});

test("Starten: die Karte bleibt und wartet auf die Quittung", () => {
  const nach = auftraegeNachStart([A, B], "j1", 1000);
  assert.equal(nach.length, 2);
  assert.deepEqual(nach[0].stand, { phase: "wartet", seitMs: 1000 });
  assert.equal(nach[1], B, "die andere Karte bleibt dasselbe Objekt");
  const ansicht = kartenAnsicht(nach[0], false);
  assert.equal(ansicht.startGesperrt, true, "während des Wartens kein zweiter Klick");
  assert.equal(ansicht.verwerfenGesperrt, true, "der Auftrag ist unterwegs: Verwerfen wirkt beim Kern nicht mehr");
  assert.match(ansicht.hinweis ?? "", /nicht mehr verwerfen/);
  assert.match(ansicht.hinweis ?? "", /auflegen/i, "der ehrliche Weg, ihn zu beenden");
});

test("ein zweiter Klick während des Wartens ändert nichts", () => {
  const eins = auftraegeNachStart([A], "j1", 1000);
  assert.equal(auftraegeNachStart(eins, "j1", 5000), eins);
  assert.equal(auftraegeNachStart([A], "unbekannt", 1000)[0], A, "unbekannte Kennung: unverändert");
});

test("Quittung ok: die Karte verschwindet und die Ansage sagt gestartet", () => {
  const wartend = auftraegeNachStart([A, B], "j1", 0);
  const r = auftraegeNachQuittung(wartend, { job: "j1", ok: true });
  assert.deepEqual(r.auftraege.map((x) => x.job), ["j2"]);
  assert.equal(r.ansage, "Auftrag gestartet: Analyse");
});

test("Quittung mit Grund: die Karte bleibt stehen und zeigt den Grund, Starten geht erneut", () => {
  const wartend = auftraegeNachStart([A], "j1", 0);
  const r = auftraegeNachQuittung(wartend, { job: "j1", ok: false, grund: "Der Kanal ist geschlossen, es startet kein Auftrag." });
  assert.equal(r.auftraege.length, 1, "die Karte bleibt bei einem Grund bestehen");
  assert.deepEqual(r.auftraege[0].stand, { phase: "abgelehnt", grund: "Der Kanal ist geschlossen, es startet kein Auftrag." });
  assert.equal(r.ansage, null, "den Grund sagt die Karte selbst an (role alert), nicht zweimal");
  const ansicht = kartenAnsicht(r.auftraege[0], false);
  assert.equal(ansicht.grund, "Der Kanal ist geschlossen, es startet kein Auftrag.");
  assert.equal(ansicht.startGesperrt, false);
  assert.match(ansicht.startText, /erneut/i);
  const nochmal = auftraegeNachStart(r.auftraege, "j1", 9000);
  assert.deepEqual(nochmal[0].stand, { phase: "wartet", seitMs: 9000 });
});

test("Quittung ohne Grund bekommt einen allgemeinen Satz, nie eine leere Zeile", () => {
  const r = auftraegeNachQuittung(auftraegeNachStart([A], "j1", 0), { job: "j1", ok: false });
  const stand = r.auftraege[0].stand;
  assert.ok(stand && stand.phase === "abgelehnt" && stand.grund.length > 10);
});

test("Quittung für eine unbekannte Karte ändert nichts", () => {
  const liste = auftraegeNachStart([A], "j1", 0);
  const r = auftraegeNachQuittung(liste, { job: "gibtesnicht", ok: true });
  assert.equal(r.auftraege, liste);
  assert.equal(r.ansage, null);
});

test("ohne Quittung: nach 14 Sekunden noch Warten, nach 16 Sekunden der neutrale Hinweis", () => {
  const wartend = auftraegeNachStart([A], "j1", 100_000);
  const nach14 = auftraegeNachFrist(wartend, 100_000 + 14_000);
  assert.equal(nach14, wartend, "unverändert, kein neuer Render");
  assert.equal(nach14[0].stand?.phase, "wartet");
  const nach15 = auftraegeNachFrist(wartend, 100_000 + 15_000);
  assert.equal(nach15[0].stand?.phase, "ohneQuittung");
  const nach16 = auftraegeNachFrist(wartend, 100_000 + 16_000);
  assert.equal(nach16[0].stand?.phase, "ohneQuittung");
  const ansicht = kartenAnsicht(nach16[0], false);
  assert.ok(ansicht.hinweis && ansicht.hinweis.length > 20);
  assert.doesNotMatch(ansicht.hinweis ?? "", /fehler|abgelehnt|gescheitert/i, "neutral: der Auftrag kann trotzdem laufen");
  assert.equal(ansicht.startGesperrt, false, "erneutes Senden bleibt möglich");
  assert.equal(ansicht.grund, null);
});

test("eine späte Quittung nach dem Hinweis gilt wie jede andere", () => {
  const spaet = auftraegeNachFrist(auftraegeNachStart([A], "j1", 0), 20_000);
  assert.equal(auftraegeNachQuittung(spaet, { job: "j1", ok: true }).auftraege.length, 0);
  const abgelehnt = auftraegeNachQuittung(spaet, { job: "j1", ok: false, grund: "Es läuft schon ein Auftrag." });
  assert.equal(abgelehnt.auftraege[0].stand?.phase, "abgelehnt");
});

test("die Frist betrifft nur wartende Karten", () => {
  const liste = [A, B];
  assert.equal(auftraegeNachFrist(liste, 999_999), liste);
  const abgelehnt = auftraegeNachQuittung(auftraegeNachStart([A], "j1", 0), { job: "j1", ok: false, grund: "x" }).auftraege;
  assert.equal(auftraegeNachFrist(abgelehnt, 999_999), abgelehnt);
});

test("ein Kern ohne Quittung bricht nichts: die Karte ohne Stand ist die bisherige", () => {
  const ansicht = kartenAnsicht(A, false);
  assert.equal(ansicht.startGesperrt, false);
  assert.equal(ansicht.verwerfenGesperrt, false);
  assert.equal(ansicht.startText, "Starten");
  assert.equal(ansicht.hinweis, null);
  assert.equal(ansicht.grund, null);
});

test("meldet der Kern Entwicklung aus, bleibt Starten gesperrt, in jedem Zustand", () => {
  for (const karte of [A, auftraegeNachFrist(auftraegeNachStart([A], "j1", 0), 20_000)[0]]) {
    assert.equal(kartenAnsicht(karte, true).startGesperrt, true);
  }
});

test("fehlendes repo bricht die Anzeige nicht", () => {
  assert.equal(repoAnzeige(B), null);
  assert.equal(repoAnzeige({ ...B, repo: "" }), null);
  assert.equal(repoAnzeige(A), "acme/testrepo");
});

test("Warnung vor dem Neuladen gilt, solange ein Auftrag läuft", () => {
  assert.equal(auftragLaeuft([], []), false);
  assert.equal(auftragLaeuft([], [A]), false, "eine Karte, die nur wartet, verliert beim Neuladen nichts");
  assert.equal(auftragLaeuft([], auftraegeNachStart([A], "j1", 0)), true, "gesendet, Antwort steht aus");
  assert.equal(auftragLaeuft([], auftraegeNachFrist(auftraegeNachStart([A], "j1", 0), 20_000)), true);
  for (const status of ["laeuft", "wartet_freigabe", "pruefen"] as const) {
    assert.equal(auftragLaeuft([{ art: "entwicklung", status }], []), true, status);
  }
  for (const status of ["fertig", "fehler", "abgebrochen"] as const) {
    assert.equal(auftragLaeuft([{ art: "entwicklung", status }], []), false, status);
  }
  assert.equal(auftragLaeuft([{ art: "aufgabe", status: "laeuft" }, { art: "geraet", status: "laeuft" }], []), false);
});

test("ein geplanter Vorgang ist keine laufende Arbeit: der Kern legt ihn schon mit der Karte an", () => {
  // Beim Erzeugen der Karte steht der Vorgang auf "geplant" (voice-core/vorgang_stream.py). Wer nur eine Karte vor sich hat, verliert
  // beim Neuladen nichts, die Warnung darf dann nicht greifen.
  assert.equal(auftragLaeuft([{ art: "entwicklung", status: "geplant" }], [A]), false);
  assert.equal(auftragLaeuft([{ art: "entwicklung", status: "geplant" }], []), false);
  assert.equal(auftragLaeuft([{ art: "entwicklung", status: "geplant" }, { art: "aufgabe", status: "laeuft" }], []), false);
  assert.equal(auftragLaeuft([{ art: "entwicklung", status: "geplant" }, { art: "entwicklung", status: "laeuft" }], []), true);
  assert.equal(auftragLaeuft([{ art: "entwicklung", status: "geplant" }], auftraegeNachStart([A], "j1", 0)), true, "gesendet, Antwort steht aus");
});

test("Verwerfen geht nur, solange der Auftrag nicht beim Kern liegen kann", () => {
  // Der Kern ignoriert "nein" bei einem schon gestarteten Auftrag (dev_agent._auftrag_start): ein Klick würde die Karte entfernen und
  // "verworfen" sagen, der Auftrag liefe weiter. Einen Abbruchweg für einen laufenden Auftrag gibt es nicht, nur das Auflegen.
  const wartend = auftraegeNachStart([A, B], "j1", 0);
  assert.equal(auftragVerwerfbar([A, B], "j1"), true, "noch nicht gesendet");
  assert.equal(auftragVerwerfbar(wartend, "j1"), false, "gesendet, Quittung steht aus");
  assert.equal(auftragVerwerfbar(wartend, "j2"), true, "eine andere Karte bleibt verwerfbar");
  const ohne = auftraegeNachFrist(wartend, 20_000);
  assert.equal(auftragVerwerfbar(ohne, "j1"), false, "nach 15 Sekunden kann der Auftrag trotzdem laufen");
  const abgelehnt = auftraegeNachQuittung(wartend, { job: "j1", ok: false, grund: "Der Kanal ist geschlossen." }).auftraege;
  assert.equal(auftragVerwerfbar(abgelehnt, "j1"), true, "vom Kern abgelehnt: er kennt den Auftrag nicht als gestartet, Verwerfen wirkt");
  assert.equal(auftragVerwerfbar([A], "unbekannt"), false, "unbekannte Karte: nichts senden");
});

test("die Karte sperrt Verwerfen in den Phasen wartet und ohne Quittung und erklärt es", () => {
  const wartend = auftraegeNachStart([A], "j1", 0)[0];
  const ohne = auftraegeNachFrist([wartend], 20_000)[0];
  const abgelehnt = auftraegeNachQuittung([wartend], { job: "j1", ok: false, grund: "Es läuft schon ein Auftrag." }).auftraege[0];
  assert.equal(kartenAnsicht(wartend, false).verwerfenGesperrt, true);
  assert.equal(kartenAnsicht(ohne, false).verwerfenGesperrt, true);
  assert.match(kartenAnsicht(ohne, false).hinweis ?? "", /nicht mehr verwerfen/);
  assert.match(kartenAnsicht(ohne, false).hinweis ?? "", /keine Rückmeldung/i, "der neutrale Hinweis bleibt");
  assert.equal(kartenAnsicht(abgelehnt, false).verwerfenGesperrt, false);
  assert.equal(kartenAnsicht(A, false).verwerfenGesperrt, false);
  assert.equal(kartenAnsicht(wartend, true).verwerfenGesperrt, true, "auch bei Entwicklung aus");
});

test("ein zweiter Start während des Wartens wird gar nicht erst gesendet", () => {
  const wartend = auftraegeNachStart([A], "j1", 0);
  assert.equal(auftragStartbar([A], "j1"), true);
  assert.equal(auftragStartbar(wartend, "j1"), false, "die Antwort auf den ersten Klick steht noch aus");
  assert.equal(auftragStartbar(auftraegeNachFrist(wartend, 20_000), "j1"), true, "nach der Frist geht erneutes Senden");
  const abgelehnt = auftraegeNachQuittung(wartend, { job: "j1", ok: false, grund: "x" }).auftraege;
  assert.equal(auftragStartbar(abgelehnt, "j1"), true);
  assert.equal(auftragStartbar([A], "unbekannt"), false, "unbekannte Karte: nichts senden");
});
