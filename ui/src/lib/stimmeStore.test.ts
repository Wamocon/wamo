// Test für den Zustand der Stimmerkennung im Store (lib/wamo.ts): Servernachrichten stimme, stimmabdruck und
// stimmabdruck_einwilligung, die Einlernansicht öffnen und schließen, Ansagen für Screenreader und das Zurücksetzen.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { STIMME_LEER } from "./stimme.ts";
import {
  abdruckAnsichtOeffnen,
  abdruckAnsichtSchliessen,
  applyServerMessage,
  initialState,
  nachrichtAbgelehnt,
  resetWamo,
  wamo,
} from "./wamo.ts";
import type { ServerMessage } from "./nachrichten.ts";

const stand = () => wamo.get();
const online = () => wamo.set((s) => ({ ...s, presence: "idle" }));
const status = (s: "aus" | "fehlt" | "laedt" | "kaputt" | "bereit", modus: "aus" | "beobachten" | "streng" = "streng"): ServerMessage => ({
  type: "stimme_status",
  status: s,
  modus,
});

beforeEach(() => resetWamo());

test("der Anfangszustand: keine Stimmerkennung gemeldet, keine Einlernansicht, keine Einwilligung", () => {
  assert.deepEqual(initialState.stimme, STIMME_LEER);
  assert.equal(initialState.abdruck, null);
  assert.equal(initialState.einwilligung, null);
});

test("ein verworfenes fremdes Ergebnis (ignoriert) steht im Stand, die nächste Äußerung nimmt es zurück", () => {
  applyServerMessage(status("bereit"));
  applyServerMessage({ type: "stimme_ergebnis", ergebnis: "fremd", wert: 0.12, modus: "streng", ignoriert: true });
  assert.equal(stand().stimme.ignoriert, true);
  assert.equal(stand().stimme.ignoriertZaehler, 1);
  applyServerMessage({ type: "stimme_ergebnis", ergebnis: "eigen", wert: 0.8, modus: "streng" });
  assert.equal(stand().stimme.ignoriert, false);
  assert.equal(stand().stimme.ignoriertZaehler, 1);
  assert.equal(stand().toast, null, "der Store zeigt selbst nichts an: Laborkonten bekommen keinen Hinweis");
});

test("stimme_status und stimme_ergebnis füllen den Stand", () => {
  applyServerMessage(status("bereit"));
  applyServerMessage({ type: "stimme_ergebnis", ergebnis: "eigen", wert: 0.78, modus: "streng" });
  assert.deepEqual(stand().stimme, { status: "bereit", modus: "streng", ergebnis: "eigen", wert: 0.78, ignoriert: false, ignoriertZaehler: 0 });
  applyServerMessage(status("fehlt"));
  assert.equal(stand().stimme.ergebnis, null, "ohne Abdruck gilt kein Ergebnis mehr");
});

test("die Einwilligung vom Kern wird mit Version und Text abgelegt", () => {
  applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: "WAMO speichert einen Abdruck." });
  assert.deepEqual(stand().einwilligung, { version: "stimme-1", text: "WAMO speichert einen Abdruck." });
  assert.equal(stand().einwilligungAbgelehnt, false);
});

test("ein abgelehnter Einwilligungstext wird vermerkt und nimmt den alten Text weg, ein gültiger hebt den Vermerk auf", () => {
  applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: "Alt." });
  nachrichtAbgelehnt({ type: "stimmabdruck_einwilligung", version: "stimme-2", text: "x".repeat(5000) });
  assert.equal(stand().einwilligung, null);
  assert.equal(stand().einwilligungAbgelehnt, true);
  applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-2", text: "Neu." });
  assert.equal(stand().einwilligungAbgelehnt, false);
  assert.equal(stand().einwilligung?.version, "stimme-2");
});

test("andere abgelehnte Nachrichten ändern nichts an der Einwilligung", () => {
  applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: "Text." });
  const vorher = stand();
  for (const roh of [null, "text", 42, { type: "stimme_status", status: "kaputt?" }, { typ: "stimmabdruck_einwilligung" }]) nachrichtAbgelehnt(roh);
  assert.equal(stand(), vorher);
});

test("die Einlernansicht öffnet nur mit laufender Sitzung und eingeschalteter Stimmerkennung, zuerst mit der Einwilligung", () => {
  assert.equal(abdruckAnsichtOeffnen(), false, "offline");
  online();
  assert.equal(abdruckAnsichtOeffnen(), false, "ohne Stand des Kerns");
  applyServerMessage(status("aus", "aus"));
  assert.equal(abdruckAnsichtOeffnen(), false, "Stimmerkennung aus");
  applyServerMessage(status("fehlt"));
  assert.equal(abdruckAnsichtOeffnen(), true);
  assert.equal(stand().abdruck?.phase, "einwilligung");
});

test("Schließen nimmt die Einlernansicht weg", () => {
  online();
  applyServerMessage(status("fehlt"));
  abdruckAnsichtOeffnen();
  abdruckAnsichtSchliessen();
  assert.equal(stand().abdruck, null);
});

test("Meldungen des Kerns führen durch das Einlernen, das Ende wird angesagt", () => {
  online();
  applyServerMessage(status("fehlt"));
  abdruckAnsichtOeffnen();
  wamo.set((s) => ({ ...s, abdruck: { phase: "start", sekunden: 0, ziel: 20 } })); // der Start ist hinausgegangen
  applyServerMessage({ type: "stimmabdruck", status: "aufnahme", sekunden: 8.4, ziel: 20 });
  assert.deepEqual(stand().abdruck, { phase: "aufnahme", sekunden: 8.4, ziel: 20 });
  applyServerMessage({ type: "stimmabdruck", status: "rechnet" });
  assert.equal(stand().abdruck?.phase, "rechnet");
  applyServerMessage({ type: "stimmabdruck", status: "fertig", sekunden: 20 });
  assert.equal(stand().abdruck?.phase, "fertig");
  assert.equal(stand().ansage, "Stimme eingelernt");
});

test("ein Fehler bleibt mit dem Satz des Kerns stehen und wird angesagt", () => {
  online();
  wamo.set((s) => ({ ...s, abdruck: { phase: "aufnahme", sekunden: 3, ziel: 20 } }));
  applyServerMessage({ type: "stimmabdruck", status: "fehler", text: "Zu wenig Sprache." });
  assert.equal(stand().abdruck?.phase, "fehler");
  assert.equal(stand().abdruck?.text, "Zu wenig Sprache.");
  assert.equal(stand().ansage, "Stimme einlernen fehlgeschlagen");
});

test("abgebrochen und gelöscht schließen die Einlernansicht und werden angesagt", () => {
  online();
  wamo.set((s) => ({ ...s, abdruck: { phase: "aufnahme", sekunden: 3, ziel: 20 } }));
  applyServerMessage({ type: "stimmabdruck", status: "abgebrochen" });
  assert.equal(stand().abdruck, null);
  assert.equal(stand().ansage, "Stimme einlernen abgebrochen");
  applyServerMessage({ type: "stimmabdruck", status: "geloescht" });
  assert.equal(stand().abdruck, null);
  assert.equal(stand().ansage, "Stimmabdruck gelöscht");
});

test("nach dem Schließen öffnet eine späte Meldung des Kerns die Einlernansicht nicht wieder, eine neue Startmeldung schon", () => {
  online();
  applyServerMessage(status("fehlt"));
  abdruckAnsichtOeffnen();
  wamo.set((s) => ({ ...s, abdruck: { phase: "aufnahme", sekunden: 3, ziel: 20 } }));
  abdruckAnsichtSchliessen(); // Abbrechen oder Schließen in der Oberfläche
  applyServerMessage({ type: "stimmabdruck", status: "aufnahme", sekunden: 6, ziel: 20 });
  assert.equal(stand().abdruck, null, "späte Aufnahme");
  applyServerMessage({ type: "stimmabdruck", status: "rechnet" });
  assert.equal(stand().abdruck, null, "spätes Rechnen");
  applyServerMessage({ type: "stimmabdruck", status: "fehler", text: "abgebrochen" });
  assert.equal(stand().abdruck, null, "später Fehler");
  applyServerMessage({ type: "stimmabdruck", status: "start" });
  assert.equal(stand().abdruck?.phase, "start", "ein neuer Durchlauf öffnet die Ansicht");
  applyServerMessage({ type: "stimmabdruck", status: "aufnahme", sekunden: 1, ziel: 20 });
  assert.equal(stand().abdruck?.phase, "aufnahme");
});

test("ohne geöffnete Ansicht und ohne lokales Schließen öffnet eine Aufnahme sie, ein Ergebnis nie", () => {
  online();
  applyServerMessage({ type: "stimmabdruck", status: "fertig", sekunden: 20 });
  assert.equal(stand().abdruck, null);
  applyServerMessage({ type: "stimmabdruck", status: "aufnahme", sekunden: 2, ziel: 20 });
  assert.equal(stand().abdruck?.phase, "aufnahme");
});

test("ohne Einwilligung der Person geht eine Meldung des Kerns nicht an der Einwilligung vorbei", () => {
  online();
  applyServerMessage(status("fehlt"));
  abdruckAnsichtOeffnen();
  applyServerMessage({ type: "stimmabdruck", status: "aufnahme", sekunden: 5, ziel: 20 });
  assert.equal(stand().abdruck?.phase, "einwilligung");
});

test("das Zurücksetzen der Sitzung leert Stand, Einlernansicht und Einwilligung", () => {
  online();
  applyServerMessage(status("bereit"));
  applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: "Text" });
  abdruckAnsichtOeffnen();
  resetWamo();
  assert.deepEqual(stand().stimme, STIMME_LEER);
  assert.equal(stand().abdruck, null);
  assert.equal(stand().einwilligung, null);
});
