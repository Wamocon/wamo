import test from "node:test";
import assert from "node:assert/strict";
import {
  AKTIONEN, EINWILLIGUNG_MAX, TOKEN_ANZEIGE_SEKUNDEN, aktionAdresse, bestaetigungOk, einwilligungPruefen, fehlerText, hoechsteEinwilligung, platzLink, platzModell,
  tokenRestSekunden, uebersichtOk,
} from "../../wamo_labor_dienst/web/betreuer-logik.js";
import { platzToken, uuid } from "./werte.mjs";

const platz = (nummer, extra = {}) => ({
  platz: nummer, durchlauf_id: uuid(), offen: true, einwilligung_version: 1, einwilligung_zeit: "2026-11-28T09:30:00Z", tickets: 7, kommentare: 2,
  hat_platz_token: true, reserviert: false, ...extra,
});

test("einwilligungPruefen nimmt ganze Zahlen von 1 bis 1000 an, die nicht kleiner sind als die höchste bisherige", () => {
  assert.deepEqual(einwilligungPruefen("1", 0), { ok: true, wert: 1, fehler: "" });
  assert.deepEqual(einwilligungPruefen(" 5 ", 3), { ok: true, wert: 5, fehler: "" });
  assert.equal(einwilligungPruefen("5", 5).ok, true, "dieselbe Version gilt für den nächsten Besucher");
  assert.equal(einwilligungPruefen("1000", null).ok, true);
  assert.equal(EINWILLIGUNG_MAX, 1000);
});

test("einwilligungPruefen lehnt eine kleinere Version ab und nennt die höchste", () => {
  const r = einwilligungPruefen("4", 5);
  assert.equal(r.ok, false);
  assert.equal(r.wert, null);
  assert.match(r.fehler, /nicht kleiner/);
  assert.match(r.fehler, /5/);
});

test("einwilligungPruefen lehnt alles ab, was keine ganze Zahl von 1 bis 1000 ist", () => {
  for (const kaputt of ["", " ", "0", "-1", "1.5", "1,5", "abc", "1e3", "01", "٣", "１", "99999999999", "+5", "5 5", null, undefined, 5, {}]) {
    assert.equal(einwilligungPruefen(kaputt, 0).ok, false, String(kaputt));
  }
  assert.match(einwilligungPruefen("1001", 0).fehler, /höchstens 1000/);
  assert.match(einwilligungPruefen("abc", 0).fehler, /ganze Zahl/);
});

test("hoechsteEinwilligung nimmt die größte erfasste Version über alle Plätze", () => {
  assert.equal(hoechsteEinwilligung([platz(1, { einwilligung_version: 3 }), platz(2, { einwilligung_version: null }), platz(3, { einwilligung_version: 7 }), {}]), 7);
  assert.equal(hoechsteEinwilligung([]), 0);
  assert.equal(hoechsteEinwilligung(null), 0);
  assert.equal(hoechsteEinwilligung([platz(1, { einwilligung_version: "9" })]), 0, "nur ganze Zahlen zählen");
});

test("bestaetigungOk verlangt genau die Platznummer", () => {
  assert.equal(bestaetigungOk("3", 3), true);
  assert.equal(bestaetigungOk(" 3 ", 3), true);
  for (const falsch of ["03", "4", "", "3.0", "drei", "Platz 3", null, undefined, 3]) assert.equal(bestaetigungOk(falsch, 3), false, String(falsch));
  assert.equal(bestaetigungOk("16", 16), true);
  assert.equal(bestaetigungOk("1", 16), false);
});

test("AKTIONEN: Bestätigung für alles, was Daten verwirft oder den Durchlauf wechselt, neuer Token nach Wechsel und Löschen", () => {
  assert.deepEqual(Object.keys(AKTIONEN).sort(), ["daten-loeschen", "durchlauf-beenden", "neuer-durchlauf", "platz-token", "zuruecksetzen"]);
  const mitBestaetigung = Object.entries(AKTIONEN).filter(([, a]) => a.bestaetigung).map(([id]) => id).sort();
  assert.deepEqual(mitBestaetigung, ["daten-loeschen", "neuer-durchlauf", "zuruecksetzen"]);
  const mitToken = Object.entries(AKTIONEN).filter(([, a]) => a.tokenNachher).map(([id]) => id).sort();
  assert.deepEqual(mitToken, ["daten-loeschen", "neuer-durchlauf"]);
  assert.equal(AKTIONEN["neuer-durchlauf"].braucht, "einwilligung");
  assert.equal(Object.isFrozen(AKTIONEN), true);
  for (const a of Object.values(AKTIONEN)) assert.ok(a.titel && a.beschreibung);
});

test("aktionAdresse baut nur die Pfade der Betreuer-Schnittstelle", () => {
  assert.equal(aktionAdresse(3, "neuer-durchlauf"), "/api/betreuer/plaetze/3/neuer-durchlauf");
  assert.equal(aktionAdresse(16, "platz-token"), "/api/betreuer/plaetze/16/platz-token");
  for (const [p, a] of [[0, "platz-token"], [17, "platz-token"], ["3", "platz-token"], [3.5, "platz-token"], [null, "platz-token"], [3, "loeschen"], [3, "__proto__"], [3, "../x"], [3, ""], [3, "constructor"]]) {
    assert.throws(() => aktionAdresse(p, a), TypeError, `${p} ${a}`);
  }
});

test("platzModell: offener Durchlauf", () => {
  const m = platzModell(platz(3), "Europe/Berlin");
  assert.deepEqual(m, {
    platz: 3, titel: "Platz 3", zustandText: "Durchlauf offen", zustandKlasse: "zustand-offen", durchlauf: uuid().slice(0, 8), einwilligungText: "Version 1 vom 28.11.2026, 10:30",
    tickets: 7, kommentare: 2, tokenText: "Platz-Token vergeben", reserviert: false, kannBeenden: true,
  });
});

test("platzModell: beendeter und geschlossener Platz, reservierter Platz ohne Token", () => {
  const beendet = platzModell(platz(4, { offen: false }));
  assert.equal(beendet.zustandText, "Durchlauf beendet");
  assert.equal(beendet.zustandKlasse, "zustand-beendet");
  assert.equal(beendet.kannBeenden, false);
  const zu = platzModell(platz(5, { offen: false, einwilligung_version: null, einwilligung_zeit: null, hat_platz_token: false, tickets: 6, kommentare: 0 }));
  assert.equal(zu.zustandText, "Geschlossen, kein Durchlauf");
  assert.equal(zu.einwilligungText, "keine");
  assert.equal(zu.tokenText, "Kein Platz-Token");
  assert.equal(platzModell(platz(15, { reserviert: true })).reserviert, true);
});

test("uebersichtOk prüft die Form der Antwort", () => {
  assert.equal(uebersichtOk({ plaetze: [platz(1), platz(2)] }), true);
  for (const kaputt of [null, {}, { plaetze: "x" }, { plaetze: [{ platz: "1" }] }, { plaetze: [null] }, []]) assert.equal(uebersichtOk(kaputt), false, JSON.stringify(kaputt));
});

test("fehlerText nennt für jeden Fall einen deutschen Satz und übernimmt nur kurzen Text des Dienstes bei 422 und 503", () => {
  assert.equal(fehlerText(401, null), "Der Betreuer-Token gilt nicht.");
  assert.equal(fehlerText(403, { text: "<b>egal</b>" }), "Der Betreuer-Token gilt nicht.");
  assert.equal(fehlerText(404, null), "Dieser Platz oder diese Funktion ist nicht bekannt.");
  assert.equal(fehlerText(422, { text: "Die Einwilligungsversion darf nicht kleiner sein als die zuletzt erfasste." }), "Die Einwilligungsversion darf nicht kleiner sein als die zuletzt erfasste.");
  assert.equal(fehlerText(422, null), "Die Eingabe wurde nicht angenommen.");
  assert.equal(fehlerText(422, { text: "x".repeat(300) }), "Die Eingabe wurde nicht angenommen.");
  assert.equal(fehlerText(422, { text: 5 }), "Die Eingabe wurde nicht angenommen.");
  assert.equal(fehlerText(429, null), "Zu viele Anfragen, bitte kurz warten.");
  assert.equal(fehlerText(503, { text: "Das Löschen ist noch nicht abgeschlossen. Bitte wiederholen." }), "Das Löschen ist noch nicht abgeschlossen. Bitte wiederholen.");
  assert.equal(fehlerText(503, null), "Das Löschen ist noch nicht abgeschlossen. Bitte wiederholen.");
  assert.equal(fehlerText(500, null), "Der Dienst hat einen Fehler gemeldet.");
  assert.equal(fehlerText(0, null), "Keine Verbindung zum Dienst.");
});

test("tokenRestSekunden zählt von 60 auf 0", () => {
  assert.equal(TOKEN_ANZEIGE_SEKUNDEN, 60);
  assert.equal(tokenRestSekunden(0, 0), 60);
  assert.equal(tokenRestSekunden(0, 1000), 59);
  assert.equal(tokenRestSekunden(0, 1500), 59);
  assert.equal(tokenRestSekunden(0, 59999), 1);
  assert.equal(tokenRestSekunden(0, 60000), 0);
  assert.equal(tokenRestSekunden(0, 999999), 0);
  assert.equal(tokenRestSekunden(5000, 4000), 60, "eine zurückgestellte Uhr verlängert nie über 60");
  assert.equal(tokenRestSekunden("a", 1), 0);
});

test("platzLink baut den Link für den Besucher mit dem Token im Fragment", () => {
  assert.equal(platzLink("http://127.0.0.1:18765", platzToken()), `http://127.0.0.1:18765/board#t=${platzToken()}`);
  assert.equal(platzLink("https://labor.example", platzToken(2)), `https://labor.example/board#t=${platzToken(2)}`);
  assert.equal(platzLink("http://127.0.0.1:18765", "kein-token"), "");
  assert.equal(platzLink("http://127.0.0.1:18765", null), "");
});
