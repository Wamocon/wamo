// Tests für die Breite der Seitenleisten (kommando/seitenbreite.ts): Grenzen und Klemmen, Platz für die Mitte, Lesen und Schreiben
// im Speicher (auch gesperrt oder voll), Tastaturschritte, Ziehen und der kleine Speicher mit Hörern.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BEREICH,
  breiteLesen,
  breiteLoeschen,
  breiteSchreiben,
  breitenLesen,
  erzeugeBreitenSpeicher,
  grenzenFuer,
  klemmen,
  LINKS_MAX,
  LINKS_MIN,
  LINKS_STANDARD,
  MITTE_MIN,
  passend,
  RECHTS_MAX,
  RECHTS_MIN,
  RECHTS_STANDARD,
  SCHLUESSEL,
  SCHRITT,
  SCHRITT_GROSS,
  STANDARD_BREITEN,
  tastenBreite,
  ziehBreite,
} from "./seitenbreite.ts";

/** Einfacher Speicher wie localStorage. */
function speicher(start: Record<string, string> = {}) {
  const daten = new Map(Object.entries(start));
  return {
    daten,
    getItem: (k: string) => daten.get(k) ?? null,
    setItem: (k: string, v: string) => void daten.set(k, v),
    removeItem: (k: string) => void daten.delete(k),
  };
}

/** Gesperrter oder voller Speicher: jeder Zugriff wirft. */
const kaputt = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
  removeItem: () => {
    throw new Error("SecurityError");
  },
};

const GRENZEN_LINKS = { min: LINKS_MIN, max: LINKS_MAX };
const GRENZEN_RECHTS = { min: RECHTS_MIN, max: RECHTS_MAX };

// ---------- feste Werte ----------

test("Grenzen und Standardwerte wie vereinbart", () => {
  assert.deepEqual([LINKS_MIN, LINKS_MAX, LINKS_STANDARD], [200, 520, 300]);
  assert.deepEqual([RECHTS_MIN, RECHTS_MAX, RECHTS_STANDARD], [260, 640, 380]);
  assert.equal(MITTE_MIN, 360);
  assert.deepEqual([SCHRITT, SCHRITT_GROSS], [16, 48]);
  assert.deepEqual(STANDARD_BREITEN, { links: 300, rechts: 380 });
  assert.deepEqual(BEREICH.links, { min: 200, max: 520, standard: 300 });
  assert.deepEqual(BEREICH.rechts, { min: 260, max: 640, standard: 380 });
});

test("die Schlüssel tragen das Präfix wamo. und sind je Seite verschieden", () => {
  assert.ok(SCHLUESSEL.links.startsWith("wamo."));
  assert.ok(SCHLUESSEL.rechts.startsWith("wamo."));
  assert.notEqual(SCHLUESSEL.links, SCHLUESSEL.rechts);
});

// ---------- klemmen ----------

test("klemmen: innerhalb bleibt, außerhalb wird begrenzt, gerundet wird ganzzahlig", () => {
  assert.equal(klemmen(300, 200, 520), 300);
  assert.equal(klemmen(100, 200, 520), 200);
  assert.equal(klemmen(900, 200, 520), 520);
  assert.equal(klemmen(300.6, 200, 520), 301);
  assert.equal(klemmen(200, 200, 520), 200);
  assert.equal(klemmen(520, 200, 520), 520);
});

test("klemmen: keine Zahl gilt als Mindestwert, vertauschte Grenzen ergeben den Mindestwert", () => {
  assert.equal(klemmen(Number.NaN, 200, 520), 200);
  assert.equal(klemmen(Number.POSITIVE_INFINITY, 200, 520), 200);
  assert.equal(klemmen(400, 300, 100), 300);
});

// ---------- Platz für die Mitte ----------

test("grenzenFuer: ohne bekannte Gesamtbreite gelten die festen Grenzen", () => {
  assert.deepEqual(grenzenFuer("links", 380, 0), GRENZEN_LINKS);
  assert.deepEqual(grenzenFuer("rechts", 300, 0), GRENZEN_RECHTS);
});

test("grenzenFuer: die Mitte behält mindestens ihre Breite", () => {
  // 1280 - 360 - 600 = 320 Platz für links
  assert.deepEqual(grenzenFuer("links", 600, 1280), { min: LINKS_MIN, max: 320 });
  // 1280 - 360 - 300 = 620 Platz, das liegt unter der festen Obergrenze von 640: 620 gilt
  assert.deepEqual(grenzenFuer("rechts", 300, 1280), { min: RECHTS_MIN, max: 620 });
  // viel Platz: die feste Obergrenze gilt
  assert.deepEqual(grenzenFuer("links", 380, 2560), GRENZEN_LINKS);
});

test("grenzenFuer: der Höchstwert fällt nie unter den Mindestwert", () => {
  assert.deepEqual(grenzenFuer("links", 640, 900), { min: LINKS_MIN, max: LINKS_MIN });
});

test("passend: was passt, bleibt, auch bei unbekannter Gesamtbreite", () => {
  assert.deepEqual(passend({ links: 300, rechts: 380 }, 1280), { links: 300, rechts: 380 });
  assert.deepEqual(passend({ links: 500, rechts: 600 }, 0), { links: 500, rechts: 600 });
});

test("passend: klemmt jede Seite auf ihren Bereich", () => {
  assert.deepEqual(passend({ links: 10, rechts: 9999 }, 0), { links: LINKS_MIN, rechts: RECHTS_MAX });
});

test("passend: bei Platzmangel gibt zuerst rechts nach, dann links", () => {
  // 500 + 600 + 360 = 1460, bei 1280 fehlen 180: rechts wird 420
  assert.deepEqual(passend({ links: 500, rechts: 600 }, 1280), { links: 500, rechts: 420 });
  // 520 + 260 + 360 = 1140, bei 1000 fehlen 140: rechts steht schon am Minimum, links gibt 140 ab
  assert.deepEqual(passend({ links: 520, rechts: 260 }, 1000), { links: 380, rechts: 260 });
  // beide am Minimum: mehr ist nicht zu holen
  assert.deepEqual(passend({ links: 200, rechts: 260 }, 700), { links: 200, rechts: 260 });
});

test("passend: die Mitte bleibt in jedem Fall bei mindestens 360 px, solange es überhaupt geht", () => {
  for (const gesamt of [1100, 1280, 1366, 1600, 1920]) {
    const b = passend({ links: LINKS_MAX, rechts: RECHTS_MAX }, gesamt);
    assert.ok(gesamt - b.links - b.rechts >= MITTE_MIN, `${gesamt}: ${JSON.stringify(b)}`);
  }
});

test("passend: ändert die Eingabe nicht, sondern gibt ein neues Objekt zurück", () => {
  const eingabe = Object.freeze({ links: 500, rechts: 600 });
  const aus = passend(eingabe, 1280);
  assert.notEqual(aus, eingabe);
  assert.deepEqual(eingabe, { links: 500, rechts: 600 });
});

// ---------- Lesen ----------

test("breiteLesen: gültiger Wert, geklemmt in den Bereich", () => {
  assert.equal(breiteLesen(speicher({ [SCHLUESSEL.links]: "340" }), "links"), 340);
  assert.equal(breiteLesen(speicher({ [SCHLUESSEL.links]: "9999" }), "links"), LINKS_MAX);
  assert.equal(breiteLesen(speicher({ [SCHLUESSEL.links]: "5" }), "links"), LINKS_MIN);
  assert.equal(breiteLesen(speicher({ [SCHLUESSEL.rechts]: "400.4" }), "rechts"), 400);
  assert.equal(breiteLesen(speicher({ [SCHLUESSEL.rechts]: " 410 " }), "rechts"), 410);
});

test("breiteLesen: Ungültiges wird verworfen (null)", () => {
  for (const v of ["", "abc", "12px", "-5", "1e3", "NaN", "Infinity", "0x20", "3 4", "[]", "null"]) {
    assert.equal(breiteLesen(speicher({ [SCHLUESSEL.links]: v }), "links"), null, JSON.stringify(v));
  }
  assert.equal(breiteLesen(speicher(), "links"), null);
});

test("breiteLesen: kein Speicher oder gesperrter Speicher wirft nie", () => {
  assert.equal(breiteLesen(null, "links"), null);
  assert.equal(breiteLesen(kaputt, "rechts"), null);
});

test("breitenLesen: je Seite der Wert oder der Standard", () => {
  assert.deepEqual(breitenLesen(speicher({ [SCHLUESSEL.links]: "350" })), { links: 350, rechts: RECHTS_STANDARD });
  assert.deepEqual(breitenLesen(speicher({ [SCHLUESSEL.rechts]: "unsinn" })), STANDARD_BREITEN);
  assert.deepEqual(breitenLesen(null), STANDARD_BREITEN);
  assert.deepEqual(breitenLesen(kaputt), STANDARD_BREITEN);
});

// ---------- Schreiben und Löschen ----------

test("breiteSchreiben: gerundet und geklemmt, danach lesbar", () => {
  const sp = speicher();
  assert.equal(breiteSchreiben(sp, "links", 333.4), true);
  assert.equal(sp.daten.get(SCHLUESSEL.links), "333");
  assert.equal(breiteSchreiben(sp, "rechts", 5000), true);
  assert.equal(breiteLesen(sp, "rechts"), RECHTS_MAX);
});

test("breiteSchreiben: kein Speicher, gesperrt oder voll gibt false und wirft nie", () => {
  assert.equal(breiteSchreiben(null, "links", 300), false);
  assert.equal(breiteSchreiben(kaputt, "links", 300), false);
});

test("breiteLoeschen: entfernt den Schlüssel, ohne Speicher oder bei Fehler false", () => {
  const sp = speicher({ [SCHLUESSEL.links]: "340", [SCHLUESSEL.rechts]: "400" });
  assert.equal(breiteLoeschen(sp, "links"), true);
  assert.equal(sp.daten.has(SCHLUESSEL.links), false);
  assert.equal(sp.daten.get(SCHLUESSEL.rechts), "400");
  assert.equal(breiteLoeschen(null, "links"), false);
  assert.equal(breiteLoeschen(kaputt, "links"), false);
});

// ---------- Tastatur ----------

test("tastenBreite links: Pfeil rechts wird breiter, Pfeil links schmaler, um 16 px, mit Umschalt 48 px", () => {
  assert.equal(tastenBreite("links", "ArrowRight", false, 300, GRENZEN_LINKS), 316);
  assert.equal(tastenBreite("links", "ArrowLeft", false, 300, GRENZEN_LINKS), 284);
  assert.equal(tastenBreite("links", "ArrowRight", true, 300, GRENZEN_LINKS), 348);
  assert.equal(tastenBreite("links", "ArrowLeft", true, 300, GRENZEN_LINKS), 252);
});

test("tastenBreite rechts: der Griff wandert mit dem Pfeil, Pfeil links macht die Leiste breiter", () => {
  assert.equal(tastenBreite("rechts", "ArrowLeft", false, 380, GRENZEN_RECHTS), 396);
  assert.equal(tastenBreite("rechts", "ArrowRight", false, 380, GRENZEN_RECHTS), 364);
  assert.equal(tastenBreite("rechts", "ArrowLeft", true, 380, GRENZEN_RECHTS), 428);
  assert.equal(tastenBreite("rechts", "ArrowRight", true, 380, GRENZEN_RECHTS), 332);
});

test("tastenBreite: Pos1 springt auf die Mindestbreite, Ende auf die Höchstbreite", () => {
  assert.equal(tastenBreite("links", "Home", false, 300, GRENZEN_LINKS), LINKS_MIN);
  assert.equal(tastenBreite("links", "End", false, 300, GRENZEN_LINKS), LINKS_MAX);
  assert.equal(tastenBreite("rechts", "Home", false, 380, GRENZEN_RECHTS), RECHTS_MIN);
  assert.equal(tastenBreite("rechts", "End", false, 380, GRENZEN_RECHTS), RECHTS_MAX);
  // die wirksamen Grenzen (Platz der Mitte) zählen, nicht die festen
  assert.equal(tastenBreite("links", "End", false, 300, { min: 200, max: 320 }), 320);
});

test("tastenBreite: an den Grenzen bleibt es stehen", () => {
  assert.equal(tastenBreite("links", "ArrowRight", true, 510, GRENZEN_LINKS), LINKS_MAX);
  assert.equal(tastenBreite("links", "ArrowLeft", true, 210, GRENZEN_LINKS), LINKS_MIN);
  assert.equal(tastenBreite("rechts", "ArrowLeft", true, 630, GRENZEN_RECHTS), RECHTS_MAX);
});

test("tastenBreite: andere Tasten gehören der Seite, null", () => {
  for (const t of ["ArrowUp", "ArrowDown", "PageUp", "Enter", " ", "a", "Tab", "Escape"]) {
    assert.equal(tastenBreite("links", t, false, 300, GRENZEN_LINKS), null, t);
  }
});

// ---------- Ziehen ----------

test("ziehBreite: links folgt dem Zeiger, rechts läuft gegen ihn", () => {
  assert.equal(ziehBreite("links", 300, 60, GRENZEN_LINKS), 360);
  assert.equal(ziehBreite("links", 300, -40, GRENZEN_LINKS), 260);
  assert.equal(ziehBreite("rechts", 380, -50, GRENZEN_RECHTS), 430);
  assert.equal(ziehBreite("rechts", 380, 50, GRENZEN_RECHTS), 330);
});

test("ziehBreite: begrenzt auf die Grenzen und rundet", () => {
  assert.equal(ziehBreite("links", 300, 5000, GRENZEN_LINKS), LINKS_MAX);
  assert.equal(ziehBreite("links", 300, -5000, GRENZEN_LINKS), LINKS_MIN);
  assert.equal(ziehBreite("rechts", 380, -5000, GRENZEN_RECHTS), RECHTS_MAX);
  assert.equal(ziehBreite("rechts", 380, 5000, GRENZEN_RECHTS), RECHTS_MIN);
  assert.equal(ziehBreite("links", 300, 10.5, GRENZEN_LINKS), 311);
  assert.equal(ziehBreite("links", 300, 100, { min: 200, max: 320 }), 320);
});

// ---------- kleiner Speicher mit Hörern ----------

test("Speicher: liest erst beim ersten Zugriff und merkt sich das Ergebnis", () => {
  let lesungen = 0;
  const sp = { ...speicher({ [SCHLUESSEL.links]: "340" }), getItem: (k: string) => (lesungen++, k === SCHLUESSEL.links ? "340" : null) };
  const s = erzeugeBreitenSpeicher(sp);
  assert.equal(lesungen, 0);
  const a = s.lesen();
  assert.deepEqual(a, { links: 340, rechts: RECHTS_STANDARD });
  const gelesen = lesungen;
  assert.equal(s.lesen(), a, "derselbe Schnappschuss, solange sich nichts ändert");
  assert.equal(lesungen, gelesen);
});

test("Speicher: setzen ändert nur den Stand und meldet es, schreibt aber nichts", () => {
  const sp = speicher();
  const s = erzeugeBreitenSpeicher(sp);
  let meldungen = 0;
  s.abonnieren(() => meldungen++);
  s.setzen("links", 350);
  assert.deepEqual(s.lesen(), { links: 350, rechts: RECHTS_STANDARD });
  assert.equal(meldungen, 1);
  assert.equal(sp.daten.size, 0);
  s.setzen("links", 350);
  assert.equal(meldungen, 1, "gleicher Wert: keine Meldung");
});

test("Speicher: setzen klemmt in den Bereich der Seite", () => {
  const s = erzeugeBreitenSpeicher(null);
  s.setzen("links", 9999);
  s.setzen("rechts", 1);
  assert.deepEqual(s.lesen(), { links: LINKS_MAX, rechts: RECHTS_MIN });
});

test("Speicher: festlegen schreibt nur die eigene Seite", () => {
  const sp = speicher();
  const s = erzeugeBreitenSpeicher(sp);
  s.festlegen("rechts", 420);
  assert.equal(sp.daten.get(SCHLUESSEL.rechts), "420");
  assert.equal(sp.daten.has(SCHLUESSEL.links), false);
  assert.deepEqual(s.lesen(), { links: LINKS_STANDARD, rechts: 420 });
});

test("Speicher: standardSetzen entfernt den Schlüssel und stellt den Standard her", () => {
  const sp = speicher({ [SCHLUESSEL.links]: "340" });
  const s = erzeugeBreitenSpeicher(sp);
  assert.equal(s.lesen().links, 340);
  s.standardSetzen("links");
  assert.equal(s.lesen().links, LINKS_STANDARD);
  assert.equal(sp.daten.has(SCHLUESSEL.links), false);
});

test("Speicher: ohne nutzbaren Speicher gilt die Wahl bis zum Neuladen, ohne zu werfen", () => {
  for (const sp of [null, kaputt]) {
    const s = erzeugeBreitenSpeicher(sp);
    assert.deepEqual(s.lesen(), STANDARD_BREITEN);
    s.festlegen("links", 410);
    assert.equal(s.lesen().links, 410);
    s.standardSetzen("links");
    assert.equal(s.lesen().links, LINKS_STANDARD);
  }
});

test("Speicher: abmelden beendet die Meldungen", () => {
  const s = erzeugeBreitenSpeicher(null);
  let meldungen = 0;
  const ab = s.abonnieren(() => meldungen++);
  s.setzen("links", 400);
  ab();
  s.setzen("links", 420);
  assert.equal(meldungen, 1);
});
