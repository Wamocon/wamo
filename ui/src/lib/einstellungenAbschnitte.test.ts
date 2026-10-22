// Tests für die Logik der Einstellungsseite (lib/einstellungenAbschnitte.ts): Abschnitte je Rolle, Hash, Blöcke je Plattform.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ABSCHNITTE,
  ABSCHNITT_IDS,
  abschnitteFuer,
  geraeteBloecke,
  geraeteStandText,
  hashFuer,
  modellzugangKurz,
  parseHash,
  rolleText,
  wirksamerAbschnitt,
  zurueckZiel,
} from "./einstellungenAbschnitte.ts";
import type { Modellzugang } from "./nachrichten.ts";

const ids = (rolle: "admin" | "mitglied" | null) => abschnitteFuer({ rolle }).map((a) => a.id);

test("jede Kennung steht genau einmal in der Liste und ist ASCII", () => {
  assert.deepEqual(ABSCHNITTE.map((a) => a.id), [...ABSCHNITT_IDS]);
  for (const id of ABSCHNITT_IDS) assert.match(id, /^[a-z]+$/);
});

test("Admin sieht alle Abschnitte, Mitglied und unbekannte Rolle nicht den Verbrauch", () => {
  assert.equal(ids("admin").length, ABSCHNITT_IDS.length);
  assert.ok(ids("admin").includes("verbrauch"));
  for (const rolle of ["mitglied", null] as const) {
    assert.ok(!ids(rolle).includes("verbrauch"), `Rolle ${rolle}`);
    assert.deepEqual(ids(rolle), ["konto", "modellzugang", "sprache", "darstellung", "geraete", "teilen", "info"]);
  }
});

test("Texte der Gliederung ohne Gedankenstrich", () => {
  for (const a of ABSCHNITTE) assert.ok(!/[–—]|\s-\s/.test(`${a.titel} ${a.kurz}`), a.id);
});

test("Hash: Seite offen, mit und ohne Abschnitt", () => {
  assert.deepEqual(parseHash("#einstellungen"), { offen: true, abschnitt: null });
  assert.deepEqual(parseHash("einstellungen"), { offen: true, abschnitt: null });
  assert.deepEqual(parseHash("#einstellungen/"), { offen: true, abschnitt: null });
  assert.deepEqual(parseHash("#einstellungen/modellzugang"), { offen: true, abschnitt: "modellzugang" });
  assert.deepEqual(parseHash("#einstellungen/modellzugang/"), { offen: true, abschnitt: "modellzugang" });
  assert.deepEqual(parseHash("#Einstellungen/Verbrauch"), { offen: true, abschnitt: "verbrauch" });
  for (const id of ABSCHNITT_IDS) assert.deepEqual(parseHash(`#einstellungen/${id}`), { offen: true, abschnitt: id });
});

test("Hash: Unbekanntes und Fremdes ergibt nie einen Abschnitt", () => {
  for (const h of ["#einstellungen/gibtesnicht", "#einstellungen/konto/mehr", "#einstellungen//konto", "#einstellungen/%6Bonto", "#einstellungen/__proto__"]) {
    assert.deepEqual(parseHash(h), { offen: true, abschnitt: null }, h);
  }
  for (const h of ["", "#", "#konto", "#einstellungenx", "#x/einstellungen"]) {
    assert.deepEqual(parseHash(h), { offen: false, abschnitt: null }, h);
  }
  // überlanger Rest: die Seite öffnet, aber nie mit einem Abschnitt (wie bei jedem anderen unbekannten Abschnitt)
  for (const h of [`#einstellungen/${"a".repeat(80)}`, `#einstellungen/konto/${"a".repeat(80)}`]) {
    assert.deepEqual(parseHash(h), { offen: true, abschnitt: null }, h.slice(0, 30));
  }
});

test("Ergebnisse von parseHash sind eingefroren und lassen sich nicht verändern", () => {
  assert.ok(Object.isFrozen(parseHash("#x")));
  assert.ok(Object.isFrozen(parseHash("#einstellungen")));
  assert.ok(Object.isFrozen(parseHash("#einstellungen/konto")));
});

test("hashFuer und parseHash sind zueinander umkehrbar", () => {
  assert.equal(hashFuer(null), "#einstellungen");
  for (const id of ABSCHNITT_IDS) assert.deepEqual(parseHash(hashFuer(id)), { offen: true, abschnitt: id });
});

test("wirksamer Abschnitt: zweispaltig der erste, einspaltig die Liste", () => {
  const admin = abschnitteFuer({ rolle: "admin" });
  const mitglied = abschnitteFuer({ rolle: "mitglied" });
  assert.equal(wirksamerAbschnitt({ offen: false, abschnitt: null }, admin, true), null);
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: null }, admin, true), "konto");
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: null }, admin, false), null);
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: "teilen" }, admin, false), "teilen");
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: "verbrauch" }, admin, true), "verbrauch");
  // nicht erlaubt für diese Rolle: wie ohne Abschnitt
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: "verbrauch" }, mitglied, true), "konto");
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: "verbrauch" }, mitglied, false), null);
  // keine erlaubten Abschnitte: nie ein Abschnitt
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: "konto" }, [], true), null);
  assert.equal(wirksamerAbschnitt({ offen: true, abschnitt: null }, [], true), null);
});

test("Zurück: am Handy aus dem Abschnitt zur Liste, sonst zum Gespräch", () => {
  assert.equal(zurueckZiel("modellzugang", false), "liste");
  assert.equal(zurueckZiel(null, false), "gespraech");
  assert.equal(zurueckZiel("modellzugang", true), "gespraech");
  assert.equal(zurueckZiel(null, true), "gespraech");
});

const basis = { rolle: "mitglied" as const, desktop: false, serverBrowser: false, bueroVerfuegbar: true };

test("Geräteblöcke je Rolle und Plattform", () => {
  assert.deepEqual(geraeteBloecke(basis), ["status", "betriebsart"]);
  assert.deepEqual(geraeteBloecke({ ...basis, rolle: "admin" }), ["status", "pc", "betriebsart"]);
  assert.deepEqual(geraeteBloecke({ ...basis, rolle: "admin", desktop: true }), ["status", "pc", "betriebsart", "einrichtung"]);
  // Browser auf dem Server: nichts zu wählen, aber der Admin sieht den Hinweis zur PC-Steuerung
  assert.deepEqual(geraeteBloecke({ ...basis, rolle: "admin", serverBrowser: true }), ["status", "pc"]);
  assert.deepEqual(geraeteBloecke({ ...basis, serverBrowser: true }), ["status"]);
  // Büro nicht eingerichtet: keine Betriebsart zu wählen
  assert.deepEqual(geraeteBloecke({ ...basis, bueroVerfuegbar: false, desktop: true }), ["status", "einrichtung"]);
  assert.deepEqual(geraeteBloecke({ ...basis, rolle: null }), ["status", "betriebsart"]);
});

test("Stand der Geräteeinrichtung als Satz", () => {
  const stand = { zustand: "eingerichtet" as const, tunnel: "verbunden" as const, kennung: "pc-niko" };
  const buero = { desktop: true, bueroBetrieb: true };
  const t = geraeteStandText(stand, buero);
  assert.equal(t.art, "bereit");
  assert.match(t.text, /Kennung pc-niko/);
  assert.match(t.text, /Tunnel zu WAMO verbunden/);
  assert.equal(geraeteStandText({ ...stand, tunnel: "aus" }, buero).art, "offen");
  assert.equal(geraeteStandText({ zustand: "wartet", tunnel: null, kennung: null }, buero).art, "wartet");
  const f = geraeteStandText({ zustand: "fehler", text: "Der Kern ist nicht erreichbar.", tunnel: null, kennung: null }, buero);
  assert.deepEqual(f, { text: "Der Kern ist nicht erreichbar.", art: "fehler" });
  assert.equal(geraeteStandText({ zustand: "nicht_noetig", tunnel: null, kennung: null }, buero).art, "offen");
  assert.match(geraeteStandText(stand, { desktop: false, bueroBetrieb: true }).text, /nur in der Desktop-App/);
  assert.match(geraeteStandText(stand, { desktop: true, bueroBetrieb: false }).text, /Betriebsart Büro/);
});

test("Stand der Geräteeinrichtung: alle Tunnelzustände, fehlende Kennung, Fehler ohne Text", () => {
  const buero = { desktop: true, bueroBetrieb: true };
  const eingerichtet = (tunnel: "verbunden" | "host" | "aus" | "unbekannt" | null, kennung: string | null) =>
    geraeteStandText({ zustand: "eingerichtet", tunnel, kennung }, buero);
  assert.equal(eingerichtet("host", "pc-niko").art, "bereit");
  assert.match(eingerichtet("host", "pc-niko").text, /kein Tunnel nötig/);
  assert.equal(eingerichtet("unbekannt", null).art, "offen");
  assert.match(eingerichtet("unbekannt", null).text, /Tunnel unbekannt/);
  // Tunnel noch nicht gemeldet: eingerichtet, aber noch nichts weiter zu sagen
  assert.deepEqual(eingerichtet(null, null), { text: "Eingerichtet.", art: "offen" });
  assert.ok(!eingerichtet("verbunden", null).text.includes("Kennung"));
  assert.match(geraeteStandText({ zustand: "fehler", tunnel: null, kennung: null }, buero).text, /fehlgeschlagen/);
});

const Z: Modellzugang = { aktion: "status", hinterlegt: false, festgelegt: false, codeHinterlegt: false };

test("Kurzzeile Modellzugang: nur der Stand, nie ein Wert", () => {
  assert.equal(modellzugangKurz(null), "wird geladen");
  assert.equal(modellzugangKurz({ ...Z, fehler: "Kern nicht erreichbar" }), "Stand nicht lesbar");
  assert.equal(modellzugangKurz({ ...Z, art: "haus" }), "fehlt");
  assert.equal(modellzugangKurz({ ...Z, art: "claude", hinterlegt: true, ende: "ab12" }), "Claude-Schlüssel hinterlegt");
  assert.equal(modellzugangKurz({ ...Z, art: "haus", codeHinterlegt: true, codeEnde: "cd34" }), "Token hinterlegt");
  assert.equal(modellzugangKurz({ ...Z, art: "claude", hinterlegt: true, codeHinterlegt: true }), "Claude-Schlüssel und Token hinterlegt");
  assert.equal(modellzugangKurz({ ...Z, art: "claude", quelle: "zuordnung" }), "Schlüssel des Kerns");
  // ohne lesbare Art ist der Stand nicht lesbar, auch ohne Fehlertext; mit Art zählt ein anhängender Fehler nicht
  assert.equal(modellzugangKurz({ ...Z }), "Stand nicht lesbar");
  assert.equal(modellzugangKurz({ ...Z, art: "claude", hinterlegt: true, fehler: "alter Fehler" }), "Claude-Schlüssel hinterlegt");
  const alle = [null, Z, { ...Z, hinterlegt: true, ende: "ab12" }].map(modellzugangKurz).join(" ");
  assert.ok(!alle.includes("ab12"));
});

test("Rollentext", () => {
  assert.equal(rolleText("admin"), "Admin");
  assert.equal(rolleText("mitglied"), "Mitglied");
  assert.equal(rolleText(null), "noch unbekannt");
});
