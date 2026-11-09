// Tests der Büro-Karte (bueroKarteModell.ts): Prüfung der Antwort des Kerns, Darstellung (Zustand nie nur über Farbe),
// Beschriftungen, Anordnung der Ringe und Punkte, Takt des Abrufs, ruhige Leerzustände und der Hinweis auf unbekannte Geräte.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ABRUF_MIN_MS,
  ABRUF_MS,
  anordnung,
  beschriftung,
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
  UNBEKANNT_TIPP,
  unbekanntHinweis,
  uhrzeitKurz,
  zaehlen,
  zusammenfassung,
  type Ampel,
  type BueroGeraet,
  type GruppeId,
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

test("Anordnung: jedes Gerät genau einmal, im Rahmen, nicht auf WAMO in der Mitte", () => {
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

// ---------- Leerzustand ----------
test("ruhige Leerzustände ohne Technik und ohne Gedankenstriche", () => {
  const alle = [leerFehler(503), leerFehler(0), leerFehler(null), leerFehler(429), leerFehler(500), leerFehler(401), LEER_OHNE_GERAETE];
  assert.equal(leerFehler(503).titel, "Gerätedienst nicht erreichbar");
  assert.equal(leerFehler(null).titel, "Gerätedienst nicht erreichbar");
  assert.notEqual(leerFehler(429).titel, leerFehler(503).titel);
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
