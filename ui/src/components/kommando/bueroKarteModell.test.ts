// Tests der Büro-Karte (bueroKarteModell.ts): Prüfung der Antwort des Kerns, Darstellung (Zustand nie nur über Farbe),
// Beschriftungen, Anordnung der Ringe und Punkte, Takt des Abrufs, ruhige Leerzustände und der Hinweis auf unbekannte Geräte.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  AKTION_GESENDET,
  AKTION_KNOPF,
  AKTIONEN,
  brauchtBestaetigung,
  schaltFehlerText,
  ABRUF_MAX_MS,
  ABRUF_MIN_MS,
  ABRUF_MS,
  abrufAbstand,
  aktualisierungHinweis,
  anordnung,
  beschriftung,
  bueroAnsage,
  darstellung,
  GRUPPEN,
  kleinsterAbstand,
  KARTE,
  LEER_OHNE_GERAETE,
  leerFehler,
  nachGruppen,
  naechsterAbruf,
  prozent,
  pruefeBuero,
  ringe,
  sauber,
  TAKT_NEU,
  taktErfolg,
  taktFehler,
  taktNeuBeginnen,
  UNBEKANNT_TIPP,
  unbekanntHinweis,
  uhrzeitKurz,
  zaehlen,
  zusammenfassung,
  type Ampel,
  type BueroGeraet,
  type BueroStand,
  type GruppeId,
  type Takt,
} from "./bueroKarteModell.ts";

function geraet(id: string, gruppe: GruppeId, ueber: Partial<BueroGeraet> = {}): BueroGeraet {
  return { id, name: id, gruppe, ampel: "ok", text: "bereit", tipp: "Sag: Test", ...ueber };
}

/** Die vierzehn Geräte des Büros in der Reihenfolge des Kerns. */
function buero(): BueroGeraet[] {
  return [
    geraet("sokrates-2", "server"),
    geraet("sokrates-1", "server"),
    geraet("caesar", "server"),
    geraet("drucker", "drucker"),
    geraet("scanner", "drucker"),
    geraet("router", "netz"),
    geraet("imac", "netz"),
    geraet("nas-1", "speicher"),
    geraet("nas-testelite", "speicher"),
    geraet("fernseher", "medien"),
    geraet("lautsprecher", "medien"),
    geraet("steckdose-1", "steckdosen"),
    geraet("steckdose-2", "steckdosen"),
    geraet("steckdose-3", "steckdosen"),
  ];
}

function rohGeraet(ueber: Record<string, unknown> = {}): Record<string, unknown> {
  return { id: "drucker", name: "Drucker", gruppe: "drucker", ampel: "ok", text: "bereit, Toner 80 Prozent", tipp: "Sag: Wie ist der Tonerstand am Drucker?", ...ueber };
}

function rohAntwort(geraete: unknown[], ueber: Record<string, unknown> = {}): Record<string, unknown> {
  return { geraete, unbekannt: null, stand: "2026-11-09T08:00:00Z", ...ueber };
}

// ---------- Gruppen ----------
test("sechs Gruppen, von innen nach außen, mit den Namen der Aufgabe", () => {
  assert.deepEqual(
    GRUPPEN.map((g) => g.label),
    ["Server", "Drucker und Scanner", "Netz", "Speicher", "Medien", "Steckdosen"],
  );
  assert.deepEqual(
    GRUPPEN.map((g) => g.id),
    ["server", "drucker", "netz", "speicher", "medien", "steckdosen"],
  );
});

// ---------- Antwort des Kerns prüfen ----------
test("gültige Antwort wird übernommen", () => {
  const s = pruefeBuero(rohAntwort([rohGeraet({ kennzahlen: { toner_prozent: 80 } })]));
  assert.ok(s);
  assert.equal(s.geraete.length, 1);
  assert.deepEqual(s.geraete[0], {
    id: "drucker",
    name: "Drucker",
    gruppe: "drucker",
    ampel: "ok",
    text: "bereit, Toner 80 Prozent",
    tipp: "Sag: Wie ist der Tonerstand am Drucker?",
    kennzahlen: { toner_prozent: 80 },
  });
  assert.equal(s.unbekannt, null);
  assert.equal(s.stand, "2026-11-09T08:00:00Z");
});

test("keine Antwort oder falsche Form ergibt null", () => {
  for (const roh of [null, undefined, 5, "x", [], {}, { geraete: "x" }, { geraete: {} }, { geraete: null }]) {
    assert.equal(pruefeBuero(roh), null);
  }
  assert.deepEqual(pruefeBuero({ geraete: [] })?.geraete, []);
});

test("unbrauchbare Geräte fallen einzeln weg, die übrigen bleiben", () => {
  const s = pruefeBuero(
    rohAntwort([
      rohGeraet({ id: "a" }),
      rohGeraet({ id: "../x" }),
      rohGeraet({ id: "A" }),
      rohGeraet({ id: 5 }),
      rohGeraet({ id: "b", gruppe: "kueche" }),
      rohGeraet({ id: "c", ampel: "gruen" }),
      rohGeraet({ id: "d", name: "" }),
      rohGeraet({ id: "e", name: "   " }),
      rohGeraet({ id: "f", text: "" }),
      rohGeraet({ id: "g", text: 7 }),
      "text",
      null,
      5,
      [],
      rohGeraet({ id: "h" }),
    ]),
  );
  assert.deepEqual(s?.geraete.map((g) => g.id), ["a", "h"]);
});

test("doppelte Kennungen zählen einmal, höchstens 64 Geräte", () => {
  const doppelt = pruefeBuero(rohAntwort([rohGeraet(), rohGeraet({ name: "Zweiter" })]));
  assert.equal(doppelt?.geraete.length, 1);
  assert.equal(doppelt?.geraete[0].name, "Drucker");
  const viele = pruefeBuero(rohAntwort(Array.from({ length: 100 }, (_, i) => rohGeraet({ id: `steckdose-${i}` }))));
  assert.equal(viele?.geraete.length, 64);
});

test("Texte werden gesäubert und begrenzt (Gerätetexte sind Daten)", () => {
  const umlenkung = String.fromCharCode(0x202e); // Rechts-nach-links-Überschreibung
  const unsichtbar = String.fromCharCode(0x200b); // Null-Breite-Leerzeichen
  const s = pruefeBuero(
    rohAntwort([
      rohGeraet({
        name: "Dru\u0000cker" + umlenkung + "  A\n\tB" + "x".repeat(300),
        text: "a" + unsichtbar + "b\r\nc" + "y".repeat(200),
        tipp: "t".repeat(500),
      }),
    ]),
  );
  const g = s?.geraete[0];
  assert.ok(g);
  assert.ok(g.name.length <= 60 && g.text.length <= 80 && g.tipp.length <= 120);
  assert.ok(g.name.startsWith("Dru cker A B"));
  assert.ok(g.text.startsWith("a b c"));
  assert.ok(!new RegExp("[\\p{Cc}\\p{Cf}]", "u").test(g.name + g.text + g.tipp));
  assert.equal(sauber(42, 10), "");
  assert.equal(sauber("  a   b ", 10), "a b");
});

test("fremde Felder werden nie übernommen (keine Adressen, keine Rohtexte)", () => {
  const s = pruefeBuero(rohAntwort([rohGeraet({ adresse: "192.168.178.24", summary: "roh", html: "<b>x</b>", __proto__: { x: 1 } })]));
  const text = JSON.stringify(s);
  assert.ok(!text.includes("192.168") && !text.includes("roh") && !text.includes("<b>"));
  assert.deepEqual(Object.keys(s!.geraete[0]).sort(), ["ampel", "gruppe", "id", "name", "text", "tipp"]);
});

test("Kennzahlen: nur Zahlen mit sauberen Namen", () => {
  const s = pruefeBuero(
    rohAntwort([rohGeraet({ kennzahlen: { toner_prozent: 80, runter_mbit: Infinity, "x y": 1, A: 2, text: "5", leer: null, ok_: 1.5 } })]),
  );
  assert.deepEqual(s?.geraete[0].kennzahlen, { toner_prozent: 80, ok_: 1.5 });
  assert.equal(pruefeBuero(rohAntwort([rohGeraet({ kennzahlen: [1, 2] })]))?.geraete[0].kennzahlen, undefined);
  assert.equal(pruefeBuero(rohAntwort([rohGeraet({ kennzahlen: {} })]))?.geraete[0].kennzahlen, undefined);
});

test("Zahl der unbekannten Geräte und Stand werden streng gelesen", () => {
  assert.equal(pruefeBuero(rohAntwort([], { unbekannt: 3 }))?.unbekannt, 3);
  for (const u of [-1, 1.5, "3", NaN, undefined, null]) assert.equal(pruefeBuero(rohAntwort([], { unbekannt: u }))?.unbekannt, null);
  for (const t of ["gestern", "2026-11-09 08:00:00", 5, null]) assert.equal(pruefeBuero(rohAntwort([], { stand: t }))?.stand, "");
});

// ---------- Darstellung ----------
test("drei Zustände haben drei verschiedene Formen und Wörter, nur die Störung pulsiert", () => {
  const ampeln: Ampel[] = ["ok", "stoerung", "unbekannt"];
  const d = ampeln.map(darstellung);
  assert.deepEqual(d.map((x) => x.form), ["voll", "strich", "hohl"]);
  assert.deepEqual(d.map((x) => x.wort), ["in Ordnung", "Störung", "unbekannt"]);
  assert.deepEqual(d.map((x) => x.pulsiert), [false, true, false]);
  assert.equal(new Set(d.map((x) => x.form)).size, 3);
});

test("Beschriftung nennt Name, Zustandswort und Klartext", () => {
  assert.equal(beschriftung(geraet("d", "drucker", { name: "Drucker", text: "bereit, Toner 80 Prozent" })), "Drucker, in Ordnung: bereit, Toner 80 Prozent");
  assert.equal(beschriftung(geraet("r", "netz", { name: "Router", ampel: "stoerung", text: "Internet getrennt" })), "Router, Störung: Internet getrennt");
  assert.equal(beschriftung(geraet("i", "netz", { name: "iMac", ampel: "unbekannt", text: "nicht sichtbar" })), "iMac, unbekannt: nicht sichtbar");
});

test("Zählung und Zusammenfassung", () => {
  const g = buero();
  g[2] = { ...g[2], ampel: "stoerung" };
  g[6] = { ...g[6], ampel: "unbekannt" };
  g[7] = { ...g[7], ampel: "unbekannt" };
  assert.deepEqual(zaehlen(g), { gesamt: 14, ok: 11, stoerung: 1, unbekannt: 2 });
  assert.equal(zusammenfassung(g), "14 Geräte, 1 Störung, 2 unbekannt");
  assert.equal(zusammenfassung(buero()), "14 Geräte, alles in Ordnung");
  assert.equal(zusammenfassung([]), "Keine Geräte");
  assert.equal(zusammenfassung([geraet("a", "netz", { ampel: "stoerung" })]), "1 Gerät, 1 Störung");
  assert.equal(zusammenfassung([geraet("a", "netz", { ampel: "stoerung" }), geraet("b", "netz", { ampel: "stoerung" })]), "2 Geräte, 2 Störungen");
});

test("nach Gruppen in Ringreihenfolge, leere Gruppen entfallen", () => {
  const g = [geraet("t", "steckdosen"), geraet("s", "server"), geraet("s2", "server")];
  const n = nachGruppen(g);
  assert.deepEqual(n.map((x) => x.id), ["server", "steckdosen"]);
  assert.deepEqual(n[0].geraete.map((x) => x.id), ["s", "s2"]);
  assert.equal(n[0].label, "Server");
  assert.deepEqual(nachGruppen([]), []);
});

// ---------- Anordnung ----------
test("Ringe: ein Ring je belegter Gruppe, von innen nach außen, mit begrenztem Abstand", () => {
  const r = ringe(buero());
  assert.equal(r.length, 6);
  assert.deepEqual(r.map((x) => x.id), GRUPPEN.map((g) => g.id));
  assert.equal(r[0].radius, KARTE.ringMin);
  assert.equal(r[5].radius, KARTE.ringMax);
  for (let i = 1; i < r.length; i++) {
    const abstand = r[i].radius - r[i - 1].radius;
    assert.ok(abstand > 0 && abstand <= KARTE.ringAbstandMax, `Abstand ${abstand}`);
  }
  assert.deepEqual(ringe([]), []);
  const einer = ringe([geraet("a", "netz")]);
  assert.equal(einer.length, 1);
  assert.ok(einer[0].radius > KARTE.ringMin && einer[0].radius < KARTE.ringMax);
  const zwei = ringe([geraet("a", "server"), geraet("b", "medien")]);
  assert.equal(zwei[1].radius - zwei[0].radius, KARTE.ringAbstandMax);
});

test("Anordnung: jedes Gerät genau einmal, im Rahmen, nicht auf W.A.I.S. in der Mitte", () => {
  const g = buero();
  const p = anordnung(g);
  assert.deepEqual(p.map((x) => x.id), g.map((x) => x.id));
  const grenze = KARTE.mitte - 12; // Platz für den Rand des Punkts und das Pulsieren
  for (const x of p) {
    const abstand = Math.hypot(x.x - KARTE.mitte, x.y - KARTE.mitte);
    assert.ok(abstand <= grenze && abstand >= KARTE.mitteRadius + KARTE.punktRadius + 4, `${x.id}: ${abstand}`);
    assert.ok(x.x >= 0 && x.x <= KARTE.groesse && x.y >= 0 && x.y <= KARTE.groesse);
    assert.ok(x.winkel >= 0 && x.winkel < 360);
  }
  assert.deepEqual(p.map((x) => x.ring), [0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 5]);
});

test("Anordnung: Punkte liegen weit genug auseinander für einen Fingertipp", () => {
  const min = kleinsterAbstand(anordnung(buero()));
  assert.ok(min >= 40, `kleinster Abstand ${min} von ${KARTE.groesse}`);
  // auch mit einem Gerät mehr oder weniger je Gruppe bleibt Platz
  for (const extra of [0, 1]) {
    const g = [...buero(), ...(extra ? [geraet("steckdose-4", "steckdosen"), geraet("nas-3", "speicher")] : [])];
    assert.ok(kleinsterAbstand(anordnung(g)) >= 30, `mit ${g.length} Geräten`);
  }
  assert.equal(kleinsterAbstand([]), Infinity);
});

test("Anordnung ist stabil: gleiche Eingabe, gleiche Orte", () => {
  assert.deepEqual(anordnung(buero()), anordnung(buero()));
  assert.deepEqual(anordnung([]), []);
});

test("Prozent der Kartenbreite", () => {
  assert.equal(prozent(0), 0);
  assert.equal(prozent(200), 50);
  assert.equal(prozent(400), 100);
  assert.equal(prozent(100), 25);
});

// ---------- Takt des Abrufs ----------
test("Abruf alle 30 Sekunden, nur bei sichtbarer Seite", () => {
  assert.equal(ABRUF_MS, 30_000);
  assert.ok(ABRUF_MIN_MS >= 10_000);
  assert.equal(naechsterAbruf(null, 1000, true), 0);
  assert.equal(naechsterAbruf(1000, 1000, true), 30_000);
  assert.equal(naechsterAbruf(1000, 11_000, true), 20_000);
  assert.equal(naechsterAbruf(1000, 31_000, true), 0);
  assert.equal(naechsterAbruf(1000, 99_000, true), 0);
  assert.equal(naechsterAbruf(null, 1000, false), null);
  assert.equal(naechsterAbruf(1000, 11_000, false), null);
});

test("naechsterAbruf mit eigenem Abstand: länger wartet länger, null stoppt von selbst", () => {
  assert.equal(naechsterAbruf(1000, 11_000, true, 60_000), 50_000);
  assert.equal(naechsterAbruf(1000, 99_000, true, 60_000), 0);
  assert.equal(naechsterAbruf(null, 1000, true, 60_000), 0); // noch kein Abruf: sofort
  assert.equal(naechsterAbruf(1000, 11_000, true, null), null); // gestoppt
  assert.equal(naechsterAbruf(null, 1000, true, null), null);
  assert.equal(naechsterAbruf(1000, 11_000, false, 60_000), null); // verborgen ruht er in jedem Fall
});

/** Fehler in Folge ab dem Neubeginn: so oft schlug der Abruf mit diesem Status hintereinander fehl. */
function nachFehlern(status: number, anzahl: number, start: Takt = TAKT_NEU): Takt {
  let takt = start;
  for (let i = 0; i < anzahl; i++) takt = taktFehler(takt, 1_000 * (i + 1), status);
  return takt;
}

test("ohne Fehler bleibt es beim Takt von 30 Sekunden", () => {
  assert.equal(abrufAbstand(TAKT_NEU), ABRUF_MS);
  assert.equal(abrufAbstand(taktErfolg(5_000)), ABRUF_MS);
});

test("503 und nicht erreichbar (0) warten 30, 60, 120, 240 und dann höchstens 300 Sekunden", () => {
  for (const status of [503, 0]) {
    const abstaende = [1, 2, 3, 4, 5, 6, 40, 5000].map((n) => abrufAbstand(nachFehlern(status, n)));
    assert.deepEqual(abstaende, [30_000, 60_000, 120_000, 240_000, 300_000, 300_000, 300_000, 300_000], String(status));
  }
  assert.equal(ABRUF_MAX_MS, 300_000);
});

test("ein Erfolg setzt das Wachstum zurück", () => {
  const nachFehlerReihe = nachFehlern(503, 4);
  assert.equal(abrufAbstand(nachFehlerReihe), 240_000);
  assert.equal(abrufAbstand(taktErfolg(9_000)), ABRUF_MS);
  assert.equal(taktErfolg(9_000).letzter, 9_000);
  assert.equal(abrufAbstand(taktFehler(taktErfolg(9_000), 10_000, 503)), 30_000); // der nächste Fehler beginnt wieder bei 30
});

test("401 und 403 stoppen den Abruf von selbst, jeder andere Fehler hält den Takt von 30 Sekunden", () => {
  assert.equal(abrufAbstand(nachFehlern(401, 1)), null);
  assert.equal(abrufAbstand(nachFehlern(403, 1)), null);
  assert.equal(abrufAbstand(nachFehlern(401, 7)), null);
  for (const status of [429, 500, 502, 504, 404]) assert.equal(abrufAbstand(nachFehlern(status, 3)), ABRUF_MS, String(status));
});

test("jeder Versuch zählt für den Abstand, auch ein fehlgeschlagener (der Kern lässt 6 je Minute zu)", () => {
  const takt = nachFehlern(503, 1);
  assert.equal(takt.letzter, 1_000);
  assert.equal(naechsterAbruf(takt.letzter, 1_000, true, abrufAbstand(takt)), 30_000);
  assert.ok(ABRUF_MS >= ABRUF_MIN_MS);
});

test("Sichtbarwerden oder Einhängen hebt Stopp und Wachstum auf, behält aber den Zeitpunkt des letzten Versuchs", () => {
  const gestoppt = nachFehlern(401, 1);
  const neu = taktNeuBeginnen(gestoppt);
  assert.equal(neu.fehler, null);
  assert.equal(neu.reihe, 0);
  assert.equal(neu.letzter, gestoppt.letzter);
  assert.equal(naechsterAbruf(neu.letzter, 5_000, true, abrufAbstand(neu)), 26_000); // frühestens 30 Sekunden nach dem letzten Versuch
  assert.equal(naechsterAbruf(neu.letzter, 99_000, true, abrufAbstand(neu)), 0);
  assert.deepEqual(taktNeuBeginnen(TAKT_NEU), TAKT_NEU);
  assert.equal(abrufAbstand(taktNeuBeginnen(nachFehlern(503, 6))), ABRUF_MS);
});

test("Hinweis bei fehlender Aktualisierung: nur bei 401 und 403 ohne Zusage, dass es weiter versucht wird", () => {
  assert.equal(aktualisierungHinweis(503, "14:32"), "Aktualisierung gerade nicht möglich, Stand 14:32 Uhr. Es wird weiter versucht.");
  assert.equal(aktualisierungHinweis(0, ""), "Aktualisierung gerade nicht möglich. Es wird weiter versucht.");
  for (const status of [401, 403]) {
    const text = aktualisierungHinweis(status, "14:32");
    assert.ok(text.startsWith("Aktualisierung gerade nicht möglich, Stand 14:32 Uhr."), text);
    assert.ok(!/weiter versucht/.test(text), text);
    assert.match(text, /nächsten Öffnen/);
  }
});

// ---------- Leerzustand ----------
test("ruhige Leerzustände ohne Technik und ohne Gedankenstriche", () => {
  const alle = [leerFehler(503), leerFehler(0), leerFehler(null), leerFehler(429), leerFehler(500), leerFehler(401), leerFehler(403), LEER_OHNE_GERAETE];
  assert.equal(leerFehler(503).titel, "Gerätedienst nicht erreichbar");
  assert.equal(leerFehler(null).titel, "Gerätedienst nicht erreichbar");
  assert.notEqual(leerFehler(429).titel, leerFehler(503).titel);
  // 401 und 403 werden nicht von selbst wiederholt: der Text verspricht kein "weiter versucht"
  for (const status of [401, 403]) {
    assert.notEqual(leerFehler(status).titel, leerFehler(500).titel);
    assert.ok(!/weiter versucht|Moment Geduld/.test(leerFehler(status).text), leerFehler(status).text);
  }
  for (const l of alle) {
    assert.ok(l.titel && l.text);
    assert.ok(!/\d{3}|HTTP|Fehler/.test(l.titel + l.text), l.titel);
    const gedankenstriche = new RegExp("[" + String.fromCharCode(0x2013, 0x2014) + "]"); // Regel für Texte
    assert.ok(!gedankenstriche.test(l.titel + l.text), l.titel);
  }
});

test("Uhrzeit aus dem Zeitpunkt der Antwort", () => {
  assert.match(uhrzeitKurz("2026-11-09T08:05:00Z"), /^\d{2}:\d{2}$/);
  assert.equal(uhrzeitKurz(""), "");
  assert.equal(uhrzeitKurz("gestern"), "");
});

// ---------- Ansage für Screenreader (ein Bereich, der immer da ist, nur der Text wechselt) ----------
const stand = (geraete: BueroGeraet[]): BueroStand => ({ geraete, unbekannt: null, stand: "2026-11-09T08:05:00Z" });

test("Ansage: vor der ersten Antwort wird geladen", () => {
  assert.equal(bueroAnsage({ geladen: false, stand: null, fehler: null }), "Geräte werden geladen");
});

test("Ansage ohne Stand: der ruhige Leerzustand mit Titel und Text", () => {
  const l = leerFehler(503);
  assert.equal(bueroAnsage({ geladen: true, stand: null, fehler: 503 }), `${l.titel}. ${l.text}`);
  const gesperrt = leerFehler(401);
  assert.equal(bueroAnsage({ geladen: true, stand: null, fehler: 401 }), `${gesperrt.titel}. ${gesperrt.text}`);
});

test("Ansage ohne Geräte im Stand: der Hinweis auf die fehlende Einrichtung", () => {
  assert.equal(bueroAnsage({ geladen: true, stand: stand([]), fehler: null }), `${LEER_OHNE_GERAETE.titel}. ${LEER_OHNE_GERAETE.text}`);
});

test("Ansage mit Stand: die Zusammenfassung, bei Fehlern der Hinweis dazu ohne Uhrzeit (die Zeile bleibt zwischen den Abrufen gleich)", () => {
  const g = [geraet("a", "server"), geraet("b", "netz", { ampel: "stoerung" })];
  assert.equal(bueroAnsage({ geladen: true, stand: stand(g), fehler: null }), "2 Geräte, 1 Störung");
  assert.equal(
    bueroAnsage({ geladen: true, stand: stand(g), fehler: 503 }),
    "2 Geräte, 1 Störung. Aktualisierung gerade nicht möglich. Es wird weiter versucht.",
  );
  assert.equal(bueroAnsage({ geladen: true, stand: stand(g), fehler: 503 }), bueroAnsage({ geladen: true, stand: stand(g), fehler: 0 }));
});

test("Ansage wechselt nur, wenn sich etwas Hörbares ändert", () => {
  const ok = [geraet("a", "server"), geraet("b", "netz")];
  const andereZeit = { ...stand(ok), stand: "2026-11-09T08:06:00Z" };
  assert.equal(bueroAnsage({ geladen: true, stand: stand(ok), fehler: null }), bueroAnsage({ geladen: true, stand: andereZeit, fehler: null }));
  const gestoert = [geraet("a", "server"), geraet("b", "netz", { ampel: "stoerung" })];
  assert.notEqual(bueroAnsage({ geladen: true, stand: stand(ok), fehler: null }), bueroAnsage({ geladen: true, stand: stand(gestoert), fehler: null }));
});

test("Ansagetexte ohne Gedankenstriche", () => {
  const gedankenstriche = new RegExp("[" + String.fromCharCode(0x2013, 0x2014) + "]");
  for (const z of [
    { geladen: false, stand: null, fehler: null },
    { geladen: true, stand: null, fehler: 0 },
    { geladen: true, stand: stand([]), fehler: null },
    { geladen: true, stand: stand([geraet("a", "server")]), fehler: 401 },
  ]) assert.ok(!gedankenstriche.test(bueroAnsage(z)), bueroAnsage(z));
});

// ---------- Hinweis auf unbekannte Geräte im Netz ----------
test("Hinweis nur bei einer Zahl größer 0, mit Einzahl und Mehrzahl", () => {
  for (const n of [null, 0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) assert.equal(unbekanntHinweis(n, true), null, String(n));
  assert.equal(unbekanntHinweis(1, true)?.text, "1 unbekanntes Gerät im Netz");
  assert.equal(unbekanntHinweis(2, true)?.text, "2 unbekannte Geräte im Netz");
  assert.equal(unbekanntHinweis(37, false)?.text, "37 unbekannte Geräte im Netz");
});

test("Sprechtipp nur für Admins, denn nur sie dürfen die Liste abfragen", () => {
  assert.equal(UNBEKANNT_TIPP, "Sag: Welche Geräte kenne ich nicht?");
  assert.equal(unbekanntHinweis(3, true)?.tipp, UNBEKANNT_TIPP);
  assert.equal(unbekanntHinweis(3, false)?.tipp, null);
});

test("Hinweistexte ruhig: ohne Alarmwörter, Technik und Gedankenstriche", () => {
  const gedankenstriche = new RegExp("[" + String.fromCharCode(0x2013, 0x2014) + "]");
  for (const n of [1, 2, 50]) {
    const h = unbekanntHinweis(n, true);
    assert.ok(h);
    const text = h.text + " " + (h.tipp ?? "");
    assert.ok(!/Alarm|Warnung|Gefahr|Achtung|!|Eindringling|Angriff|MAC|IP/.test(text), text);
    assert.ok(!gedankenstriche.test(text), text);
  }
});

test("Stil des Hinweises ist grau und nie im Alarmrot", () => {
  const css = readFileSync(new URL("./bueroKarte.css", import.meta.url), "utf8");
  const regeln = [...css.matchAll(/(\.bk-unbekannt[^{]*)\{([^}]*)\}/g)];
  assert.ok(regeln.length > 0, "Regel .bk-unbekannt fehlt");
  for (const [, , inhalt] of regeln) assert.ok(!/accent|red|#f40e0e|stoerung/i.test(inhalt), inhalt);
  assert.ok(regeln.some(([, , inhalt]) => /ink-soft/.test(inhalt)), "Text soll grau (--ink-soft) sein");
});

// ---------- Schalten (Aktionen vom Kern) ----------

test("aktionen: nur die sechs bekannten, ohne Doppelte, in fester Reihenfolge", () => {
  const s = pruefeBuero(rohAntwort([rohGeraet({ id: "fernseher", gruppe: "medien", aktionen: ["lauter", "aus", "aus", "leiser"] })]));
  assert.deepEqual(s?.geraete[0].aktionen, ["aus", "lauter", "leiser"]);
  assert.deepEqual(AKTIONEN, ["an", "aus", "lauter", "leiser", "stumm", "laut"]);
});

test("aktionen: Unbekanntes, Fremdes und Falsches fällt weg, ohne Rest fehlt das Feld", () => {
  for (const roh of [["lautstaerke"], ["eingang"], ["AN"], [5, null, {}], "an", [], undefined, ["rm -rf"]]) {
    const s = pruefeBuero(rohAntwort([rohGeraet({ aktionen: roh })]));
    assert.equal(s?.geraete[0].aktionen, undefined, JSON.stringify(roh));
  }
  const s = pruefeBuero(rohAntwort([rohGeraet({ aktionen: ["an", "lautstaerke", 7] })]));
  assert.deepEqual(s?.geraete[0].aktionen, ["an"]);
});

test("aktionen: höchstens sechs Einträge werden gelesen", () => {
  const viele = Array.from({ length: 50 }, () => "an");
  assert.deepEqual(pruefeBuero(rohAntwort([rohGeraet({ aktionen: viele })]))?.geraete[0].aktionen, ["an"]);
});

test("jede Aktion hat Knopftext und Meldung, ohne Gedankenstrich", () => {
  for (const a of AKTIONEN) {
    assert.ok(AKTION_KNOPF[a].length > 0, a);
    assert.ok(AKTION_GESENDET[a].endsWith("."), a);
    const text = AKTION_KNOPF[a] + AKTION_GESENDET[a];
    assert.doesNotMatch(text, new RegExp("[" + String.fromCharCode(0x2013, 0x2014) + "]"));
    assert.ok(!text.includes(" - "), a);
  }
});

test("nur Ausschalten einer Steckdose verlangt einen zweiten Tipp", () => {
  const g = (gruppe: string) => pruefeBuero(rohAntwort([rohGeraet({ gruppe })]))!.geraete[0];
  assert.equal(brauchtBestaetigung(g("steckdosen"), "aus"), true);
  for (const a of AKTIONEN) if (a !== "aus") assert.equal(brauchtBestaetigung(g("steckdosen"), a), false, a);
  for (const gr of ["medien", "server", "drucker", "netz", "speicher"]) assert.equal(brauchtBestaetigung(g(gr), "aus"), false, gr);
});

test("Fehlersätze: 403, 401 und keine Verbindung haben feste Sätze, sonst gilt der gesäuberte Satz des Kerns", () => {
  assert.equal(schaltFehlerText(403, "egal"), "Bürogeräte schalten dürfen nur Admins.");
  assert.equal(schaltFehlerText(401, "egal"), "Bitte melde dich neu an.");
  assert.match(schaltFehlerText(0, "Kern nicht erreichbar"), /Unklar, ob der Befehl angekommen ist/);
  assert.equal(schaltFehlerText(409, "Das geht mit diesem Gerät nicht"), "Das geht mit diesem Gerät nicht");
  assert.equal(schaltFehlerText(502, "a\u202eb\u0000c   d"), "a b c d");
  assert.match(schaltFehlerText(500, ""), /nicht geklappt/);
  assert.ok(schaltFehlerText(500, "x".repeat(500)).length <= 120);
});
