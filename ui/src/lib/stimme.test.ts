// Test für die Logik der Stimmerkennung (lib/stimme.ts): Stand und Ergebnis, das Modul in der Kopfzeile ("Eigen 0,78", "Fremd",
// "Unklar", "Nicht eingelernt"), die Zeilen der Einstellungen, der Ablauf des Einlernens (Einwilligung, Start, Aufnahme, Ergebnis)
// und was Einlernen und Löschen erlaubt. Ohne Browser und ohne React.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  abdruckAnwenden,
  abdruckBadge,
  abdruckGestartet,
  abdruckLaeuft,
  abdruckOeffnen,
  abdruckProzent,
  abdruckStatusText,
  ergebnisText,
  kannEinlernen,
  kannLoeschen,
  modusText,
  STIMME_LEER,
  stimmeErgebnisAnwenden,
  stimmeModul,
  stimmeStatusAnwenden,
  stimmeStatusText,
  VORLESETEXT,
} from "./stimme.ts";

const bereit = stimmeStatusAnwenden(STIMME_LEER, { status: "bereit", modus: "streng" });

test("der Stand beginnt leer und übernimmt Status und Modus", () => {
  assert.deepEqual(STIMME_LEER, { status: null, modus: null, ergebnis: null, wert: null, ignoriert: false, ignoriertZaehler: 0 });
  assert.deepEqual(bereit, { status: "bereit", modus: "streng", ergebnis: null, wert: null, ignoriert: false, ignoriertZaehler: 0 });
});

test("ein Ergebnis kommt zum Stand, ein neuer Status ohne Modus behält den bisherigen Modus", () => {
  const eigen = stimmeErgebnisAnwenden(bereit, { ergebnis: "eigen", wert: 0.78, modus: "streng" });
  assert.deepEqual(eigen, { status: "bereit", modus: "streng", ergebnis: "eigen", wert: 0.78, ignoriert: false, ignoriertZaehler: 0 });
  assert.equal(stimmeStatusAnwenden(eigen, { status: "bereit", modus: null }).modus, "streng");
  assert.equal(stimmeStatusAnwenden(eigen, { status: "bereit", modus: "beobachten" }).modus, "beobachten");
});

test("verlässt der Status bereit, gilt das letzte Ergebnis nicht mehr", () => {
  const eigen = stimmeErgebnisAnwenden(bereit, { ergebnis: "eigen", wert: 0.78, modus: "streng" });
  const weg = stimmeStatusAnwenden(eigen, { status: "fehlt", modus: "streng" });
  assert.equal(weg.ergebnis, null);
  assert.equal(weg.wert, null);
  assert.equal(stimmeStatusAnwenden(eigen, { status: "bereit", modus: "streng" }).ergebnis, "eigen", "bereit nach bereit behält es");
});

test("Modul in der Kopfzeile: ohne Stand oder bei aus gibt es keines", () => {
  assert.equal(stimmeModul(STIMME_LEER), null);
  assert.equal(stimmeModul(stimmeStatusAnwenden(STIMME_LEER, { status: "aus", modus: "aus" })), null);
});

test("Modul in der Kopfzeile: Nicht eingelernt, Fehler und Lädt", () => {
  const modul = (status: "fehlt" | "kaputt" | "laedt") => stimmeModul(stimmeStatusAnwenden(STIMME_LEER, { status, modus: "streng" }));
  assert.equal(modul("fehlt")?.text, "Nicht eingelernt");
  assert.equal(modul("fehlt")?.an, "warn");
  assert.equal(modul("kaputt")?.text, "Fehler");
  assert.equal(modul("kaputt")?.an, "warn");
  assert.equal(modul("laedt")?.text, "Lädt");
  assert.equal(modul("laedt")?.an, "aus");
});

test("Modul in der Kopfzeile: Eigen mit Wert und Komma, Fremd, Unklar, ohne Äußerung Bereit", () => {
  const mit = (ergebnis: "eigen" | "fremd" | "unklar", wert: number | null) =>
    stimmeModul(stimmeErgebnisAnwenden(bereit, { ergebnis, wert, modus: "streng" }));
  assert.deepEqual(mit("eigen", 0.78), { an: "ja", text: "Eigen 0,78", titel: "Nur deine Stimme darf Aufträge auslösen" });
  assert.equal(mit("eigen", 1)?.text, "Eigen 1,00");
  assert.equal(mit("eigen", 0.5)?.text, "Eigen 0,50");
  assert.equal(mit("fremd", 0.31)?.text, "Fremd 0,31");
  assert.equal(mit("fremd", 0.31)?.an, "warn");
  assert.equal(mit("unklar", 0.55)?.text, "Unklar 0,55");
  assert.equal(mit("unklar", 0.55)?.an, "aus");
  assert.equal(mit("eigen", null)?.text, "Eigen", "ohne Wert steht nur das Wort");
  assert.deepEqual(stimmeModul(bereit), { an: "aus", text: "Bereit", titel: "Nur deine Stimme darf Aufträge auslösen" });
});

test("Modul in der Kopfzeile: der Hinweistext folgt dem Modus", () => {
  const titel = (modus: "aus" | "beobachten" | "streng") =>
    stimmeModul(stimmeStatusAnwenden(STIMME_LEER, { status: "bereit", modus }))?.titel;
  assert.equal(titel("beobachten"), "Beobachten: misst nur, sperrt noch nichts");
  assert.equal(titel("streng"), "Nur deine Stimme darf Aufträge auslösen");
});

test("Zeilen der Einstellungen: Status und Modus in ganzen Sätzen", () => {
  assert.equal(stimmeStatusText(STIMME_LEER), "Der Kern meldet noch nichts");
  assert.equal(stimmeStatusText({ ...STIMME_LEER, status: "aus" }), "Aus");
  assert.equal(stimmeStatusText({ ...STIMME_LEER, status: "fehlt" }), "Noch nicht eingelernt");
  assert.equal(stimmeStatusText({ ...STIMME_LEER, status: "laedt" }), "Wird geladen");
  assert.equal(stimmeStatusText({ ...STIMME_LEER, status: "kaputt" }), "Fehler: Modell oder Abdruck konnte nicht geladen werden");
  assert.equal(stimmeStatusText({ ...STIMME_LEER, status: "bereit" }), "Eingelernt");
  assert.equal(modusText(null), "unbekannt");
  assert.equal(modusText("aus"), "Aus");
  assert.equal(modusText("beobachten"), "Beobachten: misst nur, sperrt noch nichts");
  assert.equal(modusText("streng"), "Streng: nur deine Stimme darf Aufträge auslösen");
});

test("Einlernen geht online bei fehlt, bereit und kaputt, nie offline, bei aus und beim Laden", () => {
  const stand = (status: "aus" | "fehlt" | "laedt" | "kaputt" | "bereit") => ({ ...STIMME_LEER, status });
  assert.equal(kannEinlernen(stand("fehlt"), true), true);
  assert.equal(kannEinlernen(stand("bereit"), true), true);
  assert.equal(kannEinlernen(stand("kaputt"), true), true);
  assert.equal(kannEinlernen(stand("aus"), true), false);
  assert.equal(kannEinlernen(stand("laedt"), true), false);
  assert.equal(kannEinlernen(STIMME_LEER, true), false);
  assert.equal(kannEinlernen(stand("bereit"), false), false);
});

test("Löschen geht nur online und nur, wenn es einen Abdruck gibt (bereit oder kaputt)", () => {
  const stand = (status: "aus" | "fehlt" | "laedt" | "kaputt" | "bereit") => ({ ...STIMME_LEER, status });
  assert.equal(kannLoeschen(stand("bereit"), true), true);
  assert.equal(kannLoeschen(stand("kaputt"), true), true);
  assert.equal(kannLoeschen(stand("fehlt"), true), false);
  assert.equal(kannLoeschen(stand("aus"), true), false);
  assert.equal(kannLoeschen(stand("bereit"), false), false);
});

test("Einlernen beginnt mit der Einwilligung: erst nach dem Start geht es in den Ablauf", () => {
  const a = abdruckOeffnen();
  assert.deepEqual(a, { phase: "einwilligung", sekunden: 0, ziel: 20 });
  assert.equal(abdruckLaeuft(a), false);
  const g = abdruckGestartet(a);
  assert.deepEqual(g, { phase: "start", sekunden: 0, ziel: 20 });
  assert.equal(abdruckLaeuft(g), true);
});

test("Meldungen des Kerns führen durch Aufnahme, Rechnen und Ergebnis, Sekunden und Ziel bleiben erhalten", () => {
  let a = abdruckAnwenden(abdruckGestartet(abdruckOeffnen()), { status: "aufnahme", sekunden: 8.4, ziel: 20 });
  assert.deepEqual(a, { phase: "aufnahme", sekunden: 8.4, ziel: 20 });
  a = abdruckAnwenden(a, { status: "aufnahme", sekunden: 12 });
  assert.equal(a?.ziel, 20, "ohne ziel gilt das bisherige");
  assert.equal(a?.sekunden, 12);
  a = abdruckAnwenden(a, { status: "rechnet" });
  assert.deepEqual([a?.phase, a?.sekunden], ["rechnet", 12]);
  assert.equal(abdruckLaeuft(a), true);
  a = abdruckAnwenden(a, { status: "fertig", sekunden: 20 });
  assert.equal(a?.phase, "fertig");
  assert.equal(abdruckLaeuft(a), false);
});

test("ein Fehler trägt den Satz des Kerns, abgebrochen und gelöscht schließen den Ablauf", () => {
  const fehler = abdruckAnwenden(abdruckGestartet(abdruckOeffnen()), { status: "fehler", text: "Zu wenig Sprache." });
  assert.equal(fehler?.phase, "fehler");
  assert.equal(fehler?.text, "Zu wenig Sprache.");
  assert.equal(abdruckAnwenden(fehler, { status: "abgebrochen" }), null);
  assert.equal(abdruckAnwenden(fehler, { status: "geloescht" }), null);
  assert.equal(abdruckAnwenden(null, { status: "geloescht" }), null);
});

test("eine geschlossene Einlernansicht öffnen nur start, aufnahme und rechnet; fertig, fehler und abgebrochen öffnen nie", () => {
  assert.equal(abdruckAnwenden(null, { status: "start" })?.phase, "start");
  assert.equal(abdruckAnwenden(null, { status: "aufnahme", sekunden: 3, ziel: 20 })?.phase, "aufnahme");
  assert.equal(abdruckAnwenden(null, { status: "rechnet" })?.phase, "rechnet");
  for (const status of ["fertig", "fehler", "abgebrochen", "geloescht"] as const) {
    assert.equal(abdruckAnwenden(null, { status, text: "x" }), null, status);
  }
});

test("nach lokalem Abbrechen oder Schließen öffnet nur eine neue Startmeldung die Einlernansicht wieder", () => {
  for (const status of ["aufnahme", "rechnet", "fertig", "fehler", "abgebrochen"] as const) {
    assert.equal(abdruckAnwenden(null, { status, sekunden: 5 }, true), null, status);
  }
  assert.equal(abdruckAnwenden(null, { status: "start" }, true)?.phase, "start");
  // eine offene Ansicht führt der Kern wie bisher weiter
  assert.equal(abdruckAnwenden(abdruckGestartet(null), { status: "aufnahme", sekunden: 2 }, true)?.phase, "aufnahme");
});

test("eine Meldung in der Einwilligung wird ignoriert, nur Abbrechen schließt", () => {
  const einwilligung = abdruckOeffnen();
  assert.equal(abdruckAnwenden(einwilligung, { status: "aufnahme", sekunden: 3 }), einwilligung, "ohne Einwilligung geht nichts weiter");
  assert.equal(abdruckAnwenden(einwilligung, { status: "abgebrochen" }), null, "Abbrechen schließt auch hier");
});

test("Fortschritt in Prozent: gerundet und auf 0 bis 100 begrenzt, ohne Ziel keine Division durch null", () => {
  assert.equal(abdruckProzent({ sekunden: 8.4, ziel: 20 }), 42);
  assert.equal(abdruckProzent({ sekunden: 25, ziel: 20 }), 100);
  assert.equal(abdruckProzent({ sekunden: -1, ziel: 20 }), 0);
  assert.equal(abdruckProzent({ sekunden: 5, ziel: 0 }), 0);
});

test("Abzeichen und Statuszeile je Phase, bei einem Fehler zählt der Satz des Kerns", () => {
  const p = (phase: "einwilligung" | "start" | "aufnahme" | "rechnet" | "fertig" | "fehler", extra = {}) => ({ phase, sekunden: 8.4, ziel: 20, ...extra });
  assert.deepEqual(abdruckBadge(p("einwilligung")), { an: "wartet", text: "Einwilligung" });
  assert.deepEqual(abdruckBadge(p("start")), { an: "wartet", text: "Gleich" });
  assert.deepEqual(abdruckBadge(p("aufnahme")), { an: "ja", text: "8 / 20 s" });
  assert.deepEqual(abdruckBadge(p("rechnet")), { an: "wartet", text: "Rechnet" });
  assert.deepEqual(abdruckBadge(p("fertig")), { an: "ja", text: "Fertig" });
  assert.deepEqual(abdruckBadge(p("fehler")), { an: "fehler", text: "Fehler" });
  assert.equal(abdruckStatusText(p("fehler", { text: "Zu wenig Sprache." })), "Zu wenig Sprache.");
  assert.equal(abdruckStatusText(p("fehler")), "Das hat nicht geklappt.");
  assert.match(abdruckStatusText(p("aufnahme")), /Ich höre zu/);
  assert.match(abdruckStatusText(p("fertig")), /erkennt jetzt deine Stimme/);
});

test("der Vorlesetext ist Der Nordwind und die Sonne mit echten Umlauten", () => {
  assert.match(VORLESETEXT, /^Einst stritten sich Nordwind und Sonne/);
  assert.match(VORLESETEXT, /Stärkere/);
  assert.match(VORLESETEXT, /für den Stärkeren gelten sollte/);
  assert.ok(VORLESETEXT.length > 500);
});

// ---------- fremde Stimme ignoriert (strenger Modus) ----------

const fremdIgnoriert = (s = bereit, wert: number | null = 0.12) =>
  stimmeErgebnisAnwenden(s, { ergebnis: "fremd", wert, modus: "streng", ignoriert: true });

test("ignoriert gilt nur für ein fremdes Ergebnis und zählt jede verworfene Äußerung", () => {
  const eins = fremdIgnoriert();
  assert.equal(eins.ignoriert, true);
  assert.equal(eins.ignoriertZaehler, 1);
  const zwei = fremdIgnoriert(eins);
  assert.equal(zwei.ignoriertZaehler, 2, "jede verworfene Äußerung zählt, auch zwei hintereinander");
  const unsinn = stimmeErgebnisAnwenden(bereit, { ergebnis: "eigen", wert: 0.9, modus: "streng", ignoriert: true });
  assert.equal(unsinn.ignoriert, false, "eine eigene Stimme wird nie ignoriert");
  assert.equal(unsinn.ignoriertZaehler, 0);
});

test("die nächste Äußerung und ein Statuswechsel nehmen das Ignoriert wieder zurück, der Zähler bleibt", () => {
  const eins = fremdIgnoriert();
  const danach = stimmeErgebnisAnwenden(eins, { ergebnis: "eigen", wert: 0.8, modus: "streng" });
  assert.equal(danach.ignoriert, false);
  assert.equal(danach.ignoriertZaehler, 1);
  const weg = stimmeStatusAnwenden(eins, { status: "fehlt", modus: "streng" });
  assert.equal(weg.ignoriert, false);
  assert.equal(weg.ignoriertZaehler, 1);
  assert.equal(stimmeStatusAnwenden(eins, { status: "bereit", modus: "streng" }).ignoriert, true, "bereit nach bereit behält es");
});

test("Modul in der Kopfzeile: Fremd · ignoriert, gelb, der title nennt den Wert und dass das Sprachmodell nichts bekam", () => {
  const modul = stimmeModul(fremdIgnoriert());
  assert.equal(modul?.text, "Fremd · ignoriert");
  assert.equal(modul?.an, "warn");
  assert.match(modul?.titel ?? "", /ignoriert/);
  assert.match(modul?.titel ?? "", /0,12/);
  assert.equal(stimmeModul(fremdIgnoriert(bereit, null))?.text, "Fremd · ignoriert", "auch ohne Wert");
  assert.equal(stimmeModul(stimmeErgebnisAnwenden(bereit, { ergebnis: "fremd", wert: 0.31, modus: "beobachten" }))?.text, "Fremd 0,31");
});

test("Zeile Letzte Äußerung in den Einstellungen nennt das Ignorieren", () => {
  assert.equal(ergebnisText(fremdIgnoriert()), "Fremde Stimme 0,12, ignoriert");
});
