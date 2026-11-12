import test from "node:test";
import assert from "node:assert/strict";
import {
  ABFRAGE_MS, SPALTEN, UNVOLLSTAENDIG_TEXT, detailModell, durchlaufText, fehlerMeldung, gruppiereNachStatus, kartenModell, naechsterSchritt, nummerWert, zusammenfassung,
} from "../../wamo_labor_dienst/web/board-logik.js";

const ticket = (nummer, status, extra = {}) => ({
  nummer: `LAB-${nummer}`, titel: `Titel ${nummer}`, typ: "bug", prioritaet: "mittel", status, beschreibung: "", version: 1, im_durchlauf: false,
  angelegt: "2026-11-28T09:30:00Z", geaendert: "2026-11-28T09:30:00Z", kommentare: [], ...extra,
});

test("die Spalten stehen in der Reihenfolge des Boards", () => {
  assert.deepEqual(SPALTEN.map((s) => s.id), ["zu_erledigen", "in_arbeit", "erledigt"]);
  assert.deepEqual(SPALTEN.map((s) => s.titel), ["Zu erledigen", "In Arbeit", "Erledigt"]);
  assert.equal(ABFRAGE_MS, 3000);
});

test("nummerWert liest die Zahl hinter LAB-", () => {
  assert.equal(nummerWert("LAB-12"), 12);
  assert.equal(nummerWert("LAB-9999"), 9999);
  assert.equal(nummerWert("irgendwas"), 0);
  assert.equal(nummerWert(null), 0);
});

test("gruppiereNachStatus sortiert je Spalte nach Nummer, neueste zuerst", () => {
  const g = gruppiereNachStatus([ticket(11, "erledigt"), ticket(30, "zu_erledigen"), ticket(12, "in_arbeit"), ticket(20, "zu_erledigen"), ticket(100, "zu_erledigen"), ticket(9, "zu_erledigen")]);
  assert.deepEqual(g.zu_erledigen.map((t) => t.nummer), ["LAB-100", "LAB-30", "LAB-20", "LAB-9"], "zahlenmäßig, nicht als Text: 100 vor 30");
  assert.deepEqual(g.in_arbeit.map((t) => t.nummer), ["LAB-12"]);
  assert.deepEqual(g.erledigt.map((t) => t.nummer), ["LAB-11"]);
});

test("gruppiereNachStatus liefert immer alle drei Spalten und verwirft Unbekanntes", () => {
  assert.deepEqual(gruppiereNachStatus([]), { zu_erledigen: [], in_arbeit: [], erledigt: [] });
  assert.deepEqual(gruppiereNachStatus(null), { zu_erledigen: [], in_arbeit: [], erledigt: [] });
  assert.deepEqual(gruppiereNachStatus("kein Feld"), { zu_erledigen: [], in_arbeit: [], erledigt: [] });
  const g = gruppiereNachStatus([ticket(1, "offen"), ticket(2, "__proto__"), ticket(3, "constructor"), null, ticket(4, "erledigt")]);
  assert.deepEqual(g.erledigt.map((t) => t.nummer), ["LAB-4"]);
  assert.equal(g.zu_erledigen.length + g.in_arbeit.length, 0);
});

test("kartenModell nennt Schlüssel, Titel, Typ, Priorität und die Zahl der Kommentare", () => {
  const k = kartenModell(ticket(12, "in_arbeit", { typ: "story", prioritaet: "hoechste", kommentare: [{}, {}, {}], im_durchlauf: true }));
  assert.deepEqual(k, { nummer: "LAB-12", titel: "Titel 12", typText: "Story", prioritaetText: "Höchste", prioritaetKlasse: "prio-hoechste", kommentare: 3, neu: true });
  assert.equal(kartenModell(ticket(1, "erledigt", { typ: "aufgabe", prioritaet: "niedrig" })).typText, "Aufgabe");
  assert.equal(kartenModell(ticket(1, "erledigt", { prioritaet: "hoch" })).prioritaetKlasse, "prio-hoch");
});

test("kartenModell setzt nie Nutzertext in eine Klasse und zeigt Unbekanntes als Text", () => {
  const k = kartenModell(ticket(1, "erledigt", { typ: "<b>x</b>", prioritaet: "hoch\" onclick=\"x", titel: "<img src=x onerror=alert(1)>", kommentare: null }));
  assert.equal(k.prioritaetKlasse, "prio-unbekannt");
  assert.equal(k.typText, "<b>x</b>", "als Text, die Oberfläche setzt ihn nur über textContent");
  assert.equal(k.titel, "<img src=x onerror=alert(1)>");
  assert.equal(k.kommentare, 0);
  assert.match(k.prioritaetKlasse, /^prio-[a-z]+$/);
});

test("detailModell trägt Beschreibung, Kommentare und Zeiten", () => {
  const d = detailModell(ticket(12, "in_arbeit", {
    beschreibung: "Zeile 1\nZeile 2", version: 4, kommentare: [{ kommentar_id: 1, text: "Erster", angelegt: "2026-11-28T09:30:00Z" }, { kommentar_id: 2, text: "Zweiter", angelegt: "kaputt" }],
  }), "Europe/Berlin");
  assert.equal(d.statusText, "In Arbeit");
  assert.equal(d.beschreibung, "Zeile 1\nZeile 2");
  assert.deepEqual(d.kommentare, [{ id: 1, text: "Erster", zeit: "28.11.2026, 10:30" }, { id: 2, text: "Zweiter", zeit: "" }]);
  assert.equal(d.angelegt, "28.11.2026, 10:30");
  assert.equal(d.version, 4);
});

test("zusammenfassung zählt Tickets, neue Tickets und Kommentare", () => {
  const z = zusammenfassung({ tickets: [ticket(1, "erledigt", { kommentare: [{}, {}] }), ticket(30, "zu_erledigen", { im_durchlauf: true }), ticket(31, "zu_erledigen", { im_durchlauf: true, kommentare: [{}] })] });
  assert.deepEqual(z, { tickets: 3, neu: 2, kommentare: 3 });
  assert.deepEqual(zusammenfassung({}), { tickets: 0, neu: 0, kommentare: 0 });
  assert.deepEqual(zusammenfassung(null), { tickets: 0, neu: 0, kommentare: 0 });
});

test("durchlaufText nennt den Zustand des Durchlaufs", () => {
  assert.equal(durchlaufText({ offen: true, einwilligung_version: 1 }), "Durchlauf offen");
  assert.equal(durchlaufText({ offen: false, einwilligung_version: 1 }), "Durchlauf beendet");
  assert.equal(durchlaufText({}), "Zustand unbekannt");
  assert.equal(durchlaufText(null), "Zustand unbekannt");
});

test("naechsterSchritt: 200 aktualisiert alle drei Sekunden", () => {
  assert.deepEqual(naechsterSchritt(200, null, 0), { aktion: "rendern", wartenMs: 3000, meldung: "" });
});

test("naechsterSchritt: 401 beendet die Abfragen mit der Meldung für abgelaufene Links", () => {
  const s = naechsterSchritt(401, null, 1);
  assert.equal(s.aktion, "stoppen");
  assert.equal(s.meldung, "Dieser Platz-Link gilt nicht mehr, bitte beim Betreuer melden");
  assert.equal(fehlerMeldung(401), s.meldung);
});

test("naechsterSchritt: 429 nimmt sich zurück und beachtet Retry-After", () => {
  assert.deepEqual(naechsterSchritt(429, "2", 1), { aktion: "warten", wartenMs: 2000, meldung: "Zu viele Anfragen, die Seite wartet kurz." });
  assert.equal(naechsterSchritt(429, null, 1).wartenMs, 3000);
  assert.equal(naechsterSchritt(429, null, 3).wartenMs, 12000);
  assert.equal(naechsterSchritt(429, "60", 1).wartenMs, 60000);
});

test("naechsterSchritt: andere Fehler und Verbindungsabbrüche wiederholen mit wachsender Pause", () => {
  for (const status of [0, 500, 502, 503, 404, 422]) {
    const s = naechsterSchritt(status, null, 2);
    assert.equal(s.aktion, "warten", String(status));
    assert.equal(s.wartenMs, 6000);
    assert.match(s.meldung, /Verbindung/);
  }
});

test("der Text für einen unvollständigen Link", () => {
  assert.equal(UNVOLLSTAENDIG_TEXT, "Dieser Platz-Link ist unvollständig, bitte beim Betreuer melden");
});
