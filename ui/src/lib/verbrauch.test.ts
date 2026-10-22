// Tests für das Anzeigemodell des Verbrauchs (lib/verbrauch.ts): Leerzustand, tolerante Auswertung, Zeitpunkte.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage, type Kontingent } from "./nachrichten.ts";
import {
  VERBRAUCH_FORMAT_GEPRUEFT,
  VERBRAUCH_LEER,
  dauerText,
  verbrauchAnzeige,
  zeitpunktText,
} from "./verbrauch.ts";

// Voraussetzung dieser Tests: Node mit voller ICU (deutsche Wochentage, Zeitzonendaten). Ohne sie schlägt nur dieser Test
// fehl und sagt, woran es liegt, statt dass alle Zeitangaben mit rätselhaften Texten abweichen.
test("Voraussetzung: volle ICU mit Deutsch und Zeitzonen", () => {
  const f = new Intl.DateTimeFormat("de-DE", { weekday: "long", timeZone: "Europe/Berlin", hour: "2-digit", hourCycle: "h23" });
  const text = f.format(Date.parse("2026-10-22T10:00:00Z"));
  assert.match(text, /Donnerstag/, "Node ohne volle ICU: Wochentage nicht deutsch");
  assert.match(text, /12/, "Node ohne Zeitzonendaten: Europe/Berlin fehlt");
});

const ZONE = "Europe/Berlin";
// 22.10.2026, 12:00 Uhr in Berlin (MESZ endet am 25.10.2026, also noch UTC+2)
const JETZT = Date.parse("2026-10-22T10:00:00Z");

const BEKANNT: Kontingent = {
  zustand: "bekannt",
  stand: "2026-10-22T09:59:00Z",
  limits: [
    { art: "sitzung", prozent: 42, endet: "2026-10-22T13:05:00Z", status: "ok" },
    { art: "woche", prozent: 13.4, endet: "2026-10-26T08:00:00Z" },
    { art: "fable", prozent: 104, status: "erreicht" },
  ],
};

test("das Format ist als ungeprüft gekennzeichnet", () => {
  assert.equal(VERBRAUCH_FORMAT_GEPRUEFT, false);
  assert.equal(verbrauchAnzeige(BEKANNT, JETZT, ZONE).formatGeprueft, false);
});

test("ohne Stand, unbekannt oder ohne Limits: Leerzustand", () => {
  for (const k of [null, { zustand: "unbekannt", limits: [] } as Kontingent, { zustand: "bekannt", limits: [] } as Kontingent]) {
    const a = verbrauchAnzeige(k, JETZT, ZONE);
    assert.equal(a.leer, true);
    assert.deepEqual(a.zeilen, []);
    assert.equal(a.stand, null);
  }
  assert.match(VERBRAUCH_LEER, /^Noch keine Angaben\. Sie erscheinen nach dem ersten Entwicklungsauftrag mit deinem Claude-Code-Token\.$/);
});

test("Limits ohne verwertbares Feld zählen nicht", () => {
  const a = verbrauchAnzeige({ zustand: "bekannt", limits: [{ art: "woche" }] }, JETZT, ZONE);
  assert.equal(a.leer, true);
});

test("drei Zeilen mit Prozent, Balken, Status und Zurücksetzen", () => {
  const a = verbrauchAnzeige(BEKANNT, JETZT, ZONE);
  assert.equal(a.leer, false);
  assert.deepEqual(a.zeilen.map((z) => z.art), ["sitzung", "woche", "fable"]);
  const [sitzung, woche, fable] = a.zeilen;
  assert.equal(sitzung.titel, "Sitzungslimit");
  assert.equal(sitzung.prozentText, "42 %");
  assert.equal(sitzung.balken, 42);
  assert.equal(sitzung.statusText, "im Rahmen");
  assert.deepEqual(sitzung.zuruecksetzen, { absolut: "heute, 15:05 Uhr", relativ: "in 3 Stunden und 5 Minuten", vorbei: false });
  assert.equal(woche.prozentText, "13,4 %");
  assert.equal(woche.statusText, null);
  assert.equal(woche.zuruecksetzen?.absolut, "Montag, 26.10.2026, 09:00 Uhr");
  assert.equal(woche.zuruecksetzen?.relativ, "in 3 Tagen und 22 Stunden");
  // über 100 Prozent: Balken bleibt voll, die Zahl bleibt ehrlich
  assert.equal(fable.titel, "Fable limit");
  assert.equal(fable.prozentText, "104 %");
  assert.equal(fable.balken, 100);
  assert.equal(fable.statusText, "Limit erreicht");
  assert.equal(fable.zuruecksetzen, null);
  assert.equal(a.stand, "heute, 11:59 Uhr");
});

test("fehlt eine Standardart, steht keine Angabe da; weitere Arten kommen dazu", () => {
  const a = verbrauchAnzeige(
    { zustand: "bekannt", limits: [{ art: "sitzung", prozent: 0 }, { art: "opus", prozent: 7, endet: "2026-10-27T09:00:00Z" }] },
    JETZT,
    ZONE,
  );
  assert.deepEqual(a.zeilen.map((z) => z.art), ["sitzung", "woche", "opus", "fable"]);
  const woche = a.zeilen[1];
  assert.equal(woche.gemeldet, false);
  assert.equal(woche.prozentText, "keine Angabe");
  assert.equal(woche.balken, 0);
  assert.equal(woche.zuruecksetzen, null);
  assert.equal(a.zeilen[0].prozentText, "0 %");
  assert.equal(a.zeilen[2].titel, "Opus-Wochenlimit");
  assert.equal(a.stand, null);
});

test("Status erreicht ohne Prozent füllt den Balken", () => {
  const a = verbrauchAnzeige({ zustand: "bekannt", limits: [{ art: "sitzung", status: "erreicht" }] }, JETZT, ZONE);
  assert.equal(a.zeilen[0].prozentText, "keine Angabe");
  assert.equal(a.zeilen[0].balken, 100);
  assert.equal(a.zeilen[0].statusText, "Limit erreicht");
});

test("Unsinn aus dem Netz wird nicht angezeigt: Prozent liegt schon in Prozent vor, es gibt keine Heuristik", () => {
  // 0,42 bedeutet 0,42 Prozent, nicht 42: Umrechnung macht allein der Kern (voice-core/kontingent.py)
  const a = verbrauchAnzeige({ zustand: "bekannt", limits: [{ art: "sitzung", prozent: 0.4 }] }, JETZT, ZONE);
  assert.equal(a.zeilen[0].prozentText, "0,4 %");
  // negative oder nicht endliche Zahlen sind keine Angabe
  const b = verbrauchAnzeige(
    { zustand: "bekannt", limits: [{ art: "sitzung", prozent: -5, endet: "2026-10-22T13:05:00Z" }, { art: "woche", prozent: Number.NaN, status: "ok" }] },
    JETZT,
    ZONE,
  );
  assert.equal(b.zeilen[0].prozentText, "keine Angabe");
  assert.equal(b.zeilen[1].prozentText, "keine Angabe");
  assert.equal(b.zeilen[1].statusText, "im Rahmen");
});

test("durch den Parser der Servernachricht: kaputte Zeiten fallen weg, die Anzeige bleibt ruhig", () => {
  const m = parseServerMessage({
    type: "kontingent",
    zustand: "bekannt",
    stand: "gestern",
    limits: [{ art: "sitzung", prozent: 12, endet: "bald" }, { art: "woche", prozent: 5000 }],
  });
  assert.ok(m && m.type === "kontingent");
  const a = verbrauchAnzeige(m, JETZT, ZONE);
  assert.equal(a.zeilen[0].prozentText, "12 %");
  assert.equal(a.zeilen[0].zuruecksetzen, null);
  assert.equal(a.zeilen[1].gemeldet, false);
  assert.equal(a.stand, null);
});

test("ein Zeitpunkt in der Vergangenheit ist als vorbei gekennzeichnet", () => {
  const a = verbrauchAnzeige(
    { zustand: "bekannt", limits: [{ art: "sitzung", prozent: 90, endet: "2026-10-22T09:55:00Z" }] },
    JETZT,
    ZONE,
  );
  assert.deepEqual(a.zeilen[0].zuruecksetzen, { absolut: "heute, 11:55 Uhr", relativ: "vor 5 Minuten", vorbei: true });
});

test("Zeitpunkt: heute, morgen, gestern, später mit Wochentag; Zeitzone des Geräts", () => {
  assert.equal(zeitpunktText("2026-10-23T07:30:00Z", JETZT, ZONE)?.absolut, "morgen, 09:30 Uhr");
  assert.equal(zeitpunktText("2026-10-21T07:30:00Z", JETZT, ZONE)?.absolut, "gestern, 09:30 Uhr");
  assert.equal(zeitpunktText("2026-10-24T07:30:00Z", JETZT, ZONE)?.absolut, "Samstag, 24.10.2026, 09:30 Uhr");
  // Winterzeit ab 25.10.2026: dieselbe UTC-Zeit liegt dann eine Stunde früher auf der Uhr
  assert.equal(zeitpunktText("2026-10-26T08:00:00Z", JETZT, ZONE)?.absolut, "Montag, 26.10.2026, 09:00 Uhr");
  assert.equal(zeitpunktText("2026-10-22T22:30:00Z", JETZT, "UTC")?.absolut, "heute, 22:30 Uhr");
  // Mitternacht gehört zum neuen Tag
  assert.equal(zeitpunktText("2026-10-22T22:00:00Z", JETZT, ZONE)?.absolut, "morgen, 00:00 Uhr");
  assert.equal(zeitpunktText("keine Zeit", JETZT, ZONE), null);
});

test("Dauer in Worten mit höchstens zwei Einheiten", () => {
  const min = 60_000;
  assert.equal(dauerText(0), "weniger als einer Minute");
  assert.equal(dauerText(59_000), "weniger als einer Minute");
  assert.equal(dauerText(1 * min), "1 Minute");
  assert.equal(dauerText(45 * min), "45 Minuten");
  assert.equal(dauerText(60 * min), "1 Stunde");
  assert.equal(dauerText(125 * min), "2 Stunden und 5 Minuten");
  assert.equal(dauerText(24 * 60 * min), "1 Tag");
  assert.equal(dauerText((3 * 24 + 4) * 60 * min + 30 * min), "3 Tagen und 4 Stunden");
  assert.equal(dauerText(-5), "weniger als einer Minute");
});

test("kein Gedankenstrich in den festen Texten", () => {
  const a = verbrauchAnzeige(BEKANNT, JETZT, ZONE);
  const alles = [VERBRAUCH_LEER, ...a.zeilen.flatMap((z) => [z.titel, z.erklaerung, z.statusText ?? "", z.prozentText])].join(" ");
  assert.ok(!/[–—]|\s-\s/.test(alles));
});

test("Prozent und Zeit werden vor dem Prüfen auf Verwertbares bereinigt", () => {
  for (const limits of [
    [{ art: "sitzung", prozent: -5 }],
    [{ art: "woche", endet: "bald" }],
    [{ art: "fable", prozent: 1e308 }],
    [{ art: "sitzung", prozent: 1001 }],
    [{ art: "sitzung", prozent: Number.NaN, endet: "gestern" }],
  ] as Kontingent["limits"][]) {
    assert.equal(verbrauchAnzeige({ zustand: "bekannt", limits }, JETZT, ZONE).leer, true, JSON.stringify(limits));
  }
  // die Grenze selbst gilt noch
  const grenze = verbrauchAnzeige({ zustand: "bekannt", limits: [{ art: "sitzung", prozent: 1000 }] }, JETZT, ZONE);
  assert.equal(grenze.zeilen[0].prozentText, "1000 %");
});

test("ab 100 Prozent ist das Limit erreicht, auch wenn der Kern ok meldet", () => {
  const z = verbrauchAnzeige({ zustand: "bekannt", limits: [{ art: "sitzung", prozent: 104, status: "ok" }] }, JETZT, ZONE).zeilen[0];
  assert.equal(z.status, "erreicht");
  assert.equal(z.statusText, "Limit erreicht");
  assert.equal(z.balken, 100);
});

test("ein veraltetes Fenster zeigt weder erreicht noch warnung, sondern Wert veraltet", () => {
  const k: Kontingent = {
    zustand: "bekannt",
    limits: [{ art: "sitzung", prozent: 100, status: "erreicht", endet: "2026-10-22T09:00:00Z" }],
  };
  const z = verbrauchAnzeige(k, JETZT, ZONE).zeilen[0];
  assert.equal(z.veraltet, true);
  assert.equal(z.status, null);
  assert.equal(z.statusText, "Wert veraltet");
  assert.equal(z.zuruecksetzen?.vorbei, true);
  // ein Fenster, das noch läuft, ist nicht veraltet
  assert.equal(verbrauchAnzeige(BEKANNT, JETZT, ZONE).zeilen[0].veraltet, false);
});

test("falsche Eingaben werfen nie: jetzt ungültig, Zeitzone unbekannt", () => {
  const a = verbrauchAnzeige(BEKANNT, Number.NaN, ZONE);
  assert.equal(a.leer, false);
  assert.equal(a.zeilen[0].zuruecksetzen, null);
  assert.equal(a.stand, null);
  assert.equal(zeitpunktText("2026-10-22T13:05:00Z", Number.NaN, ZONE), null);
  assert.match(zeitpunktText("2026-10-22T13:05:00Z", JETZT, "Mars/Olympus")?.absolut ?? "", /Uhr$/);
  assert.match(zeitpunktText("2026-10-22T13:05:00Z", JETZT)?.absolut ?? "", /Uhr$/); // Zone des Geräts
});

test("weitere Art sonnet kommt in der Reihenfolge des Kerns", () => {
  const a = verbrauchAnzeige({ zustand: "bekannt", limits: [{ art: "sonnet", prozent: 3 }] }, JETZT, ZONE);
  assert.deepEqual(a.zeilen.map((z) => z.art), ["sitzung", "woche", "sonnet", "fable"]);
  assert.equal(a.zeilen[2].titel, "Sonnet-Wochenlimit");
});

test("Umstellung auf Winterzeit am 25.10.2026: Kalendertage der Zone, nicht Vielfache von 24 Stunden", () => {
  // um 03:00 Uhr MESZ springt die Uhr auf 02:00 MEZ; 00:30 Uhr am 25.10. ist 22:30Z des Vortags
  const vorUmstellung = Date.parse("2026-10-24T22:30:00Z");
  assert.equal(zeitpunktText("2026-10-25T00:30:00Z", vorUmstellung, ZONE)?.absolut, "heute, 02:30 Uhr");
  assert.equal(zeitpunktText("2026-10-25T01:30:00Z", vorUmstellung, ZONE)?.absolut, "heute, 02:30 Uhr"); // dieselbe Uhrzeit, eine Stunde später
  // 25 Stunden später ist es der nächste Kalendertag, nicht "heute"
  assert.equal(zeitpunktText("2026-10-25T23:30:00Z", vorUmstellung, ZONE)?.absolut, "morgen, 00:30 Uhr");
  // Sommerzeit am 28.03.2027: 02:00 Uhr MEZ springt auf 03:00 MESZ
  const vorSommerzeit = Date.parse("2027-03-27T23:30:00Z"); // 00:30 Uhr MEZ am 28.03.
  assert.equal(zeitpunktText("2027-03-28T00:30:00Z", vorSommerzeit, ZONE)?.absolut, "heute, 01:30 Uhr");
  assert.equal(zeitpunktText("2027-03-28T01:30:00Z", vorSommerzeit, ZONE)?.absolut, "heute, 03:30 Uhr");
});

test("bis zum Zeitpunkt wird aufgerundet, danach abgerundet", () => {
  assert.equal(zeitpunktText("2026-10-22T10:01:01Z", JETZT, ZONE)?.relativ, "in 2 Minuten");
  assert.equal(zeitpunktText("2026-10-22T10:01:00Z", JETZT, ZONE)?.relativ, "in 1 Minute");
  assert.equal(zeitpunktText("2026-10-22T09:58:59Z", JETZT, ZONE)?.relativ, "vor 1 Minute");
});

test("Dauer: Grenzen von Minuten, Stunden und Tagen", () => {
  const min = 60_000;
  assert.equal(dauerText(59 * min), "59 Minuten");
  assert.equal(dauerText(61 * min), "1 Stunde und 1 Minute");
  assert.equal(dauerText(1439 * min), "23 Stunden und 59 Minuten");
  assert.equal(dauerText(1440 * min), "1 Tag");
  assert.equal(dauerText(1441 * min), "1 Tag");
  assert.equal(dauerText(61_000, true), "2 Minuten");
});
