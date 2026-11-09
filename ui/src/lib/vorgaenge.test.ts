// Tests der Hilfen für die Tafel "Vorgänge" (lib/vorgaenge.ts): Liste führen, Zeiten und Prozent als Text, Ansagen, Zustand
// beim Trennen. Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Vorgang, VorgangSchritt } from "./nachrichten.ts";
import {
  MAX_BEENDET,
  MAX_VERLAUF,
  MAX_VORGAENGE,
  SCHRITT_DROSSEL_MS,
  alsVerlauf,
  artText,
  aufgeteilt,
  balkenTon,
  dauerText,
  endeText,
  fertigText,
  freigabeKartenSelektor,
  freigabeKartenSuffix,
  fortschrittText,
  istBeendet,
  kriteriumMarke,
  kriteriumText,
  markeArt,
  prozent,
  restzeitText,
  rundenText,
  schrittAenderung,
  schrittFortschrittText,
  schrittGleich,
  schrittStatusText,
  statusText,
  tafelListen,
  vorgaengeAusAntwort,
  vorgaengeBeenden,
  vorgangAnsage,
  vorgangEinordnen,
  zeitZeile,
} from "./vorgaenge.ts";

const vorgang = (id: string, extra: Partial<Vorgang> = {}): Vorgang => ({
  id,
  titel: `Vorgang ${id}`,
  art: "aufgabe",
  status: "laeuft",
  gestartetMs: 1000,
  beendetMs: null,
  gesamt: 0.5,
  restzeitS: null,
  schritte: [],
  kriterien: [],
  runde: null,
  maxRunden: null,
  freigabeId: null,
  canvasId: null,
  ...extra,
});

test("prozent rundet auf ganze Prozent und bleibt zwischen 0 und 100", () => {
  assert.equal(prozent(0), 0);
  assert.equal(prozent(0.333), 33);
  assert.equal(prozent(0.995), 100);
  assert.equal(prozent(1), 100);
  assert.equal(prozent(7), 100);
  assert.equal(prozent(-1), 0);
  assert.equal(prozent(Number.NaN), 0);
});

test("dauerText ist mm:ss, ab einer Stunde nur mit mehr Minuten", () => {
  assert.equal(dauerText(0), "00:00");
  assert.equal(dauerText(5_000), "00:05");
  assert.equal(dauerText(135_000), "02:15");
  assert.equal(dauerText(3_725_000), "62:05");
  assert.equal(dauerText(-5), "00:00");
  assert.equal(dauerText(Number.NaN), "00:00");
});

test("fertigText nur bei fertigem Vorgang mit Ende: Fertig in mm:ss", () => {
  assert.equal(fertigText(vorgang("a", { status: "fertig", gestartetMs: 1_000, beendetMs: 136_000 })), "Fertig in 02:15");
  assert.equal(fertigText(vorgang("a", { status: "fertig", gestartetMs: 1_000, beendetMs: null })), null);
  assert.equal(fertigText(vorgang("a", { status: "laeuft", beendetMs: 5_000 })), null);
  assert.equal(fertigText(vorgang("a", { status: "fehler", beendetMs: 5_000 })), null);
  // Uhren, die nicht zusammenpassen: nie eine negative Dauer
  assert.equal(fertigText(vorgang("a", { status: "fertig", gestartetMs: 9_000, beendetMs: 1_000 })), "Fertig in 00:00");
});

test("restzeitText: ohne Wert ein Hinweis, mit Wert mm:ss, nach dem Ende nichts", () => {
  assert.equal(restzeitText(vorgang("a", { restzeitS: null })), "Restzeit wird geschätzt");
  assert.equal(restzeitText(vorgang("a", { restzeitS: 150 })), "noch etwa 02:30");
  assert.equal(restzeitText(vorgang("a", { restzeitS: 0 })), "gleich fertig");
  assert.equal(restzeitText(vorgang("a", { status: "geplant" })), "");
  assert.equal(restzeitText(vorgang("a", { status: "wartet_freigabe", restzeitS: 90 })), "");
  assert.equal(restzeitText(vorgang("a", { status: "fertig" })), "");
  assert.equal(restzeitText(vorgang("a", { status: "fehler", restzeitS: 5 })), "");
});

test("Statustexte stehen für jeden Status und Schrittstatus da (nicht nur als Farbe)", () => {
  for (const s of ["geplant", "laeuft", "wartet_freigabe", "pruefen", "fertig", "fehler", "abgebrochen"] as const) {
    assert.ok(statusText[s].length > 3, s);
  }
  for (const s of ["offen", "laeuft", "fertig", "fehler", "uebersprungen", "wartet_freigabe"] as const) {
    assert.ok(schrittStatusText[s].length > 3, s);
  }
  assert.equal(statusText.wartet_freigabe, "wartet auf Freigabe");
});

test("fortschrittText nennt Prozent, Runde und Restzeit für Screenreader", () => {
  assert.equal(fortschrittText(vorgang("a", { gesamt: 0.4, restzeitS: 90 })), "40 Prozent, noch etwa 01:30");
  assert.equal(fortschrittText(vorgang("a", { gesamt: 0.4, runde: 2, maxRunden: 3 })), "40 Prozent, Runde 2 von 3, Restzeit wird geschätzt");
  assert.equal(fortschrittText(vorgang("a", { status: "fertig", gesamt: 1, gestartetMs: 0, beendetMs: 5000 })), "100 Prozent, Fertig in 00:05");
  assert.equal(fortschrittText(vorgang("a", { status: "wartet_freigabe", gesamt: 0.5 })), "50 Prozent, wartet auf Freigabe");
});

test("vorgangEinordnen ersetzt dieselbe Kennung an ihrer Stelle und hängt Neues hinten an", () => {
  let liste: Vorgang[] = [];
  liste = vorgangEinordnen(liste, vorgang("a"));
  liste = vorgangEinordnen(liste, vorgang("b"));
  liste = vorgangEinordnen(liste, vorgang("a", { gesamt: 0.9 }));
  assert.deepEqual(liste.map((v) => v.id), ["a", "b"]);
  assert.equal(liste[0].gesamt, 0.9);
  // dieselbe Liste, wenn sich nichts ändert (kein unnötiges Neurendern)
  const gleich = vorgangEinordnen(liste, vorgang("a", { gesamt: 0.9 }));
  assert.equal(gleich, liste);
});

test("vorgangEinordnen hält die Grenze: zuerst fällt der älteste beendete heraus, nie ein laufender", () => {
  let liste: Vorgang[] = [];
  for (let i = 0; i < MAX_VORGAENGE; i++) liste = vorgangEinordnen(liste, vorgang(`v${i}`, { status: i < 3 ? "fertig" : "laeuft" }));
  liste = vorgangEinordnen(liste, vorgang("neu"));
  assert.equal(liste.length, MAX_VORGAENGE);
  assert.ok(!liste.some((v) => v.id === "v0")); // der älteste beendete ging
  assert.ok(liste.some((v) => v.id === "v1") && liste.some((v) => v.id === "neu"));
  let nur: Vorgang[] = [];
  for (let i = 0; i < MAX_VORGAENGE + 5; i++) nur = vorgangEinordnen(nur, vorgang(`l${i}`));
  assert.equal(nur.length, MAX_VORGAENGE + 5); // laufende bleiben alle stehen
});

test("aufgeteilt: aktive in der Reihenfolge des Eingangs, beendete mit dem neuesten zuerst", () => {
  const liste = [
    vorgang("a", { status: "fertig", beendetMs: 5_000 }),
    vorgang("b"),
    vorgang("c", { status: "fehler", beendetMs: 9_000 }),
    vorgang("d", { status: "wartet_freigabe" }),
    vorgang("e", { status: "geplant" }),
    vorgang("f", { status: "abgebrochen", beendetMs: 7_000 }),
  ];
  const { aktiv, beendet } = aufgeteilt(liste);
  assert.deepEqual(aktiv.map((v) => v.id), ["b", "d", "e"]);
  assert.deepEqual(beendet.map((v) => v.id), ["c", "f", "a"]);
  assert.ok(istBeendet(liste[0]) && !istBeendet(liste[1]));
});

test("vorgaengeBeenden (Verbindung weg): aktive werden abgebrochen, beendete bleiben", () => {
  const liste = [vorgang("a"), vorgang("b", { status: "fertig", beendetMs: 5_000 }), vorgang("c", { status: "wartet_freigabe", freigabeId: "x" })];
  const neu = vorgaengeBeenden(liste, 99_000);
  assert.deepEqual(neu.map((v) => v.status), ["abgebrochen", "fertig", "abgebrochen"]);
  assert.equal(neu[0].beendetMs, 99_000);
  assert.equal(neu[1], liste[1]);
  assert.equal(neu[2].freigabeId, null); // die Karte verfällt mit der Verbindung
  const keine = [vorgang("b", { status: "fertig", beendetMs: 5_000 })];
  assert.equal(vorgaengeBeenden(keine, 1), keine);
});

test("vorgangAnsage nur bei bedeutsamen Wechseln des Status", () => {
  assert.equal(vorgangAnsage(undefined, vorgang("a", { status: "laeuft" })), "Neuer Vorgang: Vorgang a");
  assert.equal(vorgangAnsage(undefined, vorgang("a", { status: "geplant" })), null);
  assert.equal(vorgangAnsage(vorgang("a"), vorgang("a", { gesamt: 0.9 })), null);
  assert.equal(vorgangAnsage(vorgang("a"), vorgang("a", { status: "wartet_freigabe" })), "Vorgang Vorgang a wartet auf Freigabe");
  assert.equal(
    vorgangAnsage(vorgang("a"), vorgang("a", { status: "fertig", gestartetMs: 0, beendetMs: 135_000 })),
    "Vorgang Vorgang a fertig. Fertig in 02:15",
  );
  assert.equal(vorgangAnsage(vorgang("a"), vorgang("a", { status: "fehler" })), "Vorgang Vorgang a fehlgeschlagen");
  assert.equal(vorgangAnsage(vorgang("a"), vorgang("a", { status: "abgebrochen" })), "Vorgang Vorgang a abgebrochen");
  assert.equal(vorgangAnsage(vorgang("a", { status: "fertig" }), vorgang("a", { status: "fertig" })), null);
});

test("freigabeKartenSuffix entspricht der Kennung der Karte in Freigaben.tsx (data-approval endet darauf)", () => {
  assert.equal(freigabeKartenSuffix("abcdef0123456789"), "-abcdef0123456789");
  assert.equal(freigabeKartenSuffix("a/b c.d"), "-a_b_c_d");
  assert.equal(freigabeKartenSuffix(""), null);
  assert.equal(freigabeKartenSuffix(null), null);
});

test("freigabeKartenSelektor findet die Karte über das Ende von data-approval, ohne rohe Netzdaten im Selektor", () => {
  assert.equal(freigabeKartenSelektor("abcdef0123456789"), '[data-approval$="-abcdef0123456789"]');
  assert.equal(freigabeKartenSelektor('x"] , body['), '[data-approval$="-x_____body_"]');
  assert.equal(freigabeKartenSelektor(null), null);
  assert.equal(freigabeKartenSelektor(""), null);
});

test("rundenText: Runde n von m, nur n oder nichts", () => {
  assert.equal(rundenText(vorgang("a", { runde: 2, maxRunden: 3 })), "Runde 2 von 3");
  assert.equal(rundenText(vorgang("a", { runde: 1, maxRunden: null })), "Runde 1");
  assert.equal(rundenText(vorgang("a", { runde: null, maxRunden: 3 })), "");
  assert.equal(rundenText(vorgang("a")), "");
});

test("zeitZeile: Fertig in mm:ss, sonst die Restzeit, sonst leer", () => {
  assert.equal(zeitZeile(vorgang("a", { status: "fertig", gestartetMs: 0, beendetMs: 65_000 })), "Fertig in 01:05");
  assert.equal(zeitZeile(vorgang("a", { restzeitS: 90 })), "noch etwa 01:30");
  assert.equal(zeitZeile(vorgang("a", { restzeitS: null })), "Restzeit wird geschätzt");
  assert.equal(zeitZeile(vorgang("a", { status: "fehler" })), "");
  assert.equal(zeitZeile(vorgang("a", { status: "wartet_freigabe" })), "");
});

test("endeText für die Zeile eines beendeten Vorgangs: Fertig in mm:ss, sonst der Status als Wort", () => {
  assert.equal(endeText(vorgang("a", { status: "fertig", gestartetMs: 0, beendetMs: 135_000 })), "Fertig in 02:15");
  assert.equal(endeText(vorgang("a", { status: "fertig", beendetMs: null })), "fertig");
  assert.equal(endeText(vorgang("a", { status: "fehler", beendetMs: 5 })), "fehlgeschlagen");
  assert.equal(endeText(vorgang("a", { status: "abgebrochen" })), "abgebrochen");
});

test("kriteriumText: Haken, nicht erfüllt, noch offen (immer als Wort)", () => {
  assert.equal(kriteriumText({ text: "x", erfuellt: true, hinweis: null }), "erfüllt");
  assert.equal(kriteriumText({ text: "x", erfuellt: false, hinweis: null }), "nicht erfüllt");
  assert.equal(kriteriumText({ text: "x", erfuellt: null, hinweis: null }), "noch offen");
});

test("kriteriumMarke: Haken, Fehlerzeichen und leerer Ring", () => {
  assert.equal(kriteriumMarke({ text: "x", erfuellt: true, hinweis: null }), "fertig");
  assert.equal(kriteriumMarke({ text: "x", erfuellt: false, hinweis: null }), "fehler");
  assert.equal(kriteriumMarke({ text: "x", erfuellt: null, hinweis: null }), "offen");
});

test("schrittFortschrittText nennt Prozent und Zustand des Schritts", () => {
  const s = { nr: 2, titel: "t", status: "laeuft" as const, fortschritt: 0.5, detail: null };
  assert.equal(schrittFortschrittText(s), "50 Prozent, läuft");
  assert.equal(schrittFortschrittText({ ...s, status: "fertig", fortschritt: 1 }), "100 Prozent, erledigt");
  assert.equal(schrittFortschrittText({ ...s, status: "offen", fortschritt: 0 }), "0 Prozent, offen");
});

test("markeArt: jeder Status hat eine Form (nicht nur eine Farbe)", () => {
  assert.equal(markeArt("laeuft"), "laeuft");
  assert.equal(markeArt("pruefen"), "laeuft");
  assert.equal(markeArt("fertig"), "fertig");
  assert.equal(markeArt("fehler"), "fehler");
  assert.equal(markeArt("wartet_freigabe"), "wartet");
  assert.equal(markeArt("geplant"), "offen");
  assert.equal(markeArt("offen"), "offen");
  assert.equal(markeArt("abgebrochen"), "aus");
  assert.equal(markeArt("uebersprungen"), "aus");
});

test("balkenTon: rot nur bei Arbeit und Ergebnis, grau bei Warten und Stillstand", () => {
  for (const s of ["laeuft", "pruefen", "fertig", "fehler"] as const) assert.equal(balkenTon(s), "aktiv", s);
  for (const s of ["geplant", "wartet_freigabe", "abgebrochen"] as const) assert.equal(balkenTon(s), "still", s);
});

test("artText benennt jede Art", () => {
  assert.deepEqual(
    (["aufgabe", "entwicklung", "geraet", "sonstiges"] as const).map((a) => artText[a]),
    ["Aufgabe", "Entwicklung", "Gerät", "Vorgang"],
  );
});

test("alsVerlauf: ein Vorgang ohne Ende gilt im Verlauf als abgebrochen (der Kern lief nicht mehr), beendete bleiben", () => {
  const fertig = vorgang("a", { status: "fertig", beendetMs: 9_000 });
  assert.equal(alsVerlauf(fertig), fertig);
  const offen = alsVerlauf(vorgang("b", { status: "wartet_freigabe", restzeitS: 40, freigabeId: "f1", gestartetMs: 3_000 }));
  assert.equal(offen.status, "abgebrochen");
  assert.equal(offen.beendetMs, null);
  assert.equal(offen.restzeitS, null);
  assert.equal(offen.freigabeId, null);
  assert.equal(offen.gestartetMs, 3_000);
});

const antwortZeile = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  titel: `Vorgang ${id}`,
  art: "aufgabe",
  status: "fertig",
  gestartet_ms: 1_000,
  beendet_ms: 5_000,
  gesamt: 1,
  restzeit_s: null,
  schritte: [],
  kriterien: [],
  runde: null,
  max_runden: null,
  freigabe_id: null,
  ergebnis: null,
  ...extra,
});

test("vorgaengeAusAntwort liest die Liste der Schnittstelle, prüft jede Zeile und begrenzt sie", () => {
  const liste = vorgaengeAusAntwort({ vorgaenge: [antwortZeile("a"), { id: "kaputt" }, antwortZeile("b", { status: "laeuft", beendet_ms: null }), 7, null] });
  assert.deepEqual(liste.map((v) => v.id), ["a", "b"]);
  assert.equal(liste[0].beendetMs, 5_000);
  assert.equal(liste[1].status, "abgebrochen"); // lief beim Absturz noch: Verlauf zeigt es nicht als aktiv
  const viele = vorgaengeAusAntwort({ vorgaenge: Array.from({ length: MAX_VERLAUF + 10 }, (_, i) => antwortZeile(`v${i}`)) });
  assert.equal(viele.length, MAX_VERLAUF);
  const doppelt = vorgaengeAusAntwort({ vorgaenge: [antwortZeile("a"), antwortZeile("a", { titel: "zweites" })] });
  assert.equal(doppelt.length, 1);
});

test("vorgaengeAusAntwort ohne brauchbare Antwort ist leer (Fehler zeigt die Tafel nie an)", () => {
  for (const roh of [null, undefined, "x", 5, [], {}, { vorgaenge: null }, { vorgaenge: "x" }, { vorgaenge: {} }]) {
    assert.deepEqual(vorgaengeAusAntwort(roh), [], JSON.stringify(roh));
  }
});

test("tafelListen: aktive nur aus der Sitzung, beendete aus Sitzung und Verlauf, gleiche Kennung gilt aus der Sitzung", () => {
  const sitzung = [
    vorgang("laufend"),
    vorgang("neu-fertig", { status: "fertig", gestartetMs: 0, beendetMs: 8_000 }),
    vorgang("beides", { status: "fertig", beendetMs: 6_000 }),
  ];
  const verlauf = [
    vorgang("beides", { status: "fehler", beendetMs: 1_000, titel: "Stand aus der Datenbank" }),
    vorgang("laufend", { status: "abgebrochen", beendetMs: null }), // Verlauf kennt den laufenden nur als alten Stand
    vorgang("alt", { status: "fertig", gestartetMs: 100, beendetMs: 2_000 }),
    vorgang("ohne-ende", { status: "abgebrochen", gestartetMs: 4_000, beendetMs: null }),
  ];
  const { aktiv, beendet } = tafelListen(sitzung, verlauf);
  assert.deepEqual(aktiv.map((v) => v.id), ["laufend"]);
  assert.equal(aktiv[0].status, "laeuft"); // der laufende wird nicht überschrieben
  assert.deepEqual(beendet.map((v) => v.id), ["neu-fertig", "beides", "ohne-ende", "alt"]);
  assert.equal(beendet.find((v) => v.id === "beides")?.status, "fertig"); // Stand der Sitzung
});

test("tafelListen begrenzt die Zahl der beendeten und gibt ohne Verlauf nur die Sitzung wieder", () => {
  const viele = Array.from({ length: MAX_BEENDET + 5 }, (_, i) => vorgang(`v${i}`, { status: "fertig", beendetMs: 1_000 + i }));
  const { beendet } = tafelListen([], viele);
  assert.equal(beendet.length, MAX_BEENDET);
  assert.equal(beendet[0].id, `v${MAX_BEENDET + 4}`); // der zuletzt beendete zuerst
  const leer = tafelListen([], []);
  assert.deepEqual(leer, { aktiv: [], beendet: [] });
});

// ---------- Schritte: was sichtbar ist, und wann eine Änderung warten darf ----------
const schritt = (nr: number, extra: Partial<VorgangSchritt> = {}): VorgangSchritt => ({
  nr,
  titel: `Schritt ${nr}`,
  status: "laeuft",
  fortschritt: 0.4,
  detail: null,
  ...extra,
});

test("schrittGleich vergleicht, was man sieht: Fortschritt als ganze Prozent, dazu Titel, Status und Detail", () => {
  assert.equal(SCHRITT_DROSSEL_MS, 250);
  assert.equal(schrittGleich(schritt(1), schritt(1)), true);
  assert.equal(schrittGleich(schritt(1, { fortschritt: 0.401 }), schritt(1, { fortschritt: 0.404 })), true);
  assert.equal(schrittGleich(schritt(1), schritt(1, { fortschritt: 0.45 })), false);
  assert.equal(schrittGleich(schritt(1), schritt(1, { status: "fertig" })), false);
  assert.equal(schrittGleich(schritt(1), schritt(1, { titel: "anders" })), false);
  assert.equal(schrittGleich(schritt(1), schritt(1, { detail: "neu" })), false);
  assert.equal(schrittGleich(schritt(1), schritt(2)), false);
});

test("schrittAenderung: gleicher Inhalt in neuen Listen ist keine Änderung", () => {
  const alt = [schritt(1, { status: "fertig", fortschritt: 1 }), schritt(2)];
  assert.equal(schrittAenderung(alt, alt), "gleich");
  assert.equal(schrittAenderung(alt, [schritt(1, { status: "fertig", fortschritt: 1 }), schritt(2)]), "gleich");
  assert.equal(schrittAenderung(alt, [alt[0], schritt(2, { fortschritt: 0.404 })]), "gleich"); // unter einem Prozent
  assert.equal(schrittAenderung([], []), "gleich");
});

test("schrittAenderung: ein Zwischenstand darf warten (nur wachsende Prozent unter 100)", () => {
  const alt = [schritt(1, { status: "fertig", fortschritt: 1 }), schritt(2)];
  assert.equal(schrittAenderung(alt, [alt[0], schritt(2, { fortschritt: 0.45 })]), "zwischenstand");
  assert.equal(schrittAenderung(alt, [alt[0], schritt(2, { fortschritt: 0.99 })]), "zwischenstand"); // 99 Prozent noch nicht der Endwert
  const zwei = [schritt(1, { fortschritt: 0.1 }), schritt(2, { fortschritt: 0.2 })];
  assert.equal(schrittAenderung(zwei, [schritt(1, { fortschritt: 0.3 }), schritt(2, { fortschritt: 0.5 })]), "zwischenstand");
});

test("schrittAenderung: der Endwert 100 Prozent, ein Rücksprung und jede andere Änderung gelten sofort", () => {
  const alt = [schritt(1, { fortschritt: 0.9 })];
  assert.equal(schrittAenderung(alt, [schritt(1, { fortschritt: 1 })]), "sonst"); // Endwert
  assert.equal(schrittAenderung(alt, [schritt(1, { fortschritt: 0.996 })]), "sonst"); // rundet auf 100
  assert.equal(schrittAenderung(alt, [schritt(1, { fortschritt: 0.2 })]), "sonst"); // Rücksprung
  assert.equal(schrittAenderung(alt, [schritt(1, { fortschritt: 0.95, status: "fertig" })]), "sonst");
  assert.equal(schrittAenderung(alt, [schritt(1, { fortschritt: 0.95, titel: "anders" })]), "sonst");
  assert.equal(schrittAenderung(alt, [schritt(1, { fortschritt: 0.95, detail: "Hinweis" })]), "sonst");
  assert.equal(schrittAenderung(alt, [alt[0], schritt(2)]), "sonst"); // neuer Schritt
  assert.equal(schrittAenderung(alt, []), "sonst");
});

test("schrittAenderung: ein Zwischenstand neben einer sofortigen Änderung gilt als sofort", () => {
  const alt = [schritt(1, { fortschritt: 0.1 }), schritt(2, { fortschritt: 0.2 })];
  assert.equal(schrittAenderung(alt, [schritt(1, { fortschritt: 0.3 }), schritt(2, { fortschritt: 1 })]), "sonst");
});
