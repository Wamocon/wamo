// Tests des Fernzugriffs (lib/fernzugriff.ts): Prüfung der Antworten, Adresse der Gateway-Seite, Anzeige der Ziele, Knöpfe, Dialog,
// Fehlerbehandlung, Restzeit und Ansagen. Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  alsStart,
  alsUebersicht,
  ANSAGE_SCHWELLEN,
  CODE_HILFE,
  eingabePruefen,
  EINLEITUNG,
  endeMeldung,
  hinweisZeilen,
  modusNachFehler,
  restSekunden,
  restzeitAnsage,
  restzeitKnapp,
  restzeitSprache,
  restzeitText,
  seitenUrlPruefen,
  sortiereZiele,
  startFehler,
  startFokus,
  startMeldung,
  STATUS_INFO,
  uhrzeit,
  verbindenKnopf,
  zielZeile,
  type Ziel,
} from "./fernzugriff.ts";

const SITZUNG = "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d";
const TOKEN = "AB12cd34EF56gh78IJ90kl12MN34op56QR78st90";
const URL_OK = `https://192.168.178.136:55446/?token=${TOKEN}#/client/ZmVybnNpdHp1bmcAYwBqc29u`;
const UI = "https://192.168.178.136:55445";

function ziel(ueber: Partial<Ziel> = {}): Ziel {
  return { name: "sokrates2", anzeige: "Sokrates-2", status: "frei", eigene: false, belegtVon: null, seit: null, ...ueber };
}

function rohUebersicht(ueber: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    aktiv: true,
    ziele: [
      { name: "sokrates2", anzeige: "Sokrates-2", status: "frei", eigene: false, belegt_von: null, seit: null },
      { name: "caesar", anzeige: "Caesar", status: "belegt", eigene: false, belegt_von: "Bernd", seit: "2026-10-31T12:00:00+00:00" },
    ],
    meine: null,
    max_minuten: 60,
    leerlauf_minuten: 10,
    ...ueber,
  };
}

// ---------- Antworten ----------
test("Übersicht: gültige Antwort", () => {
  const u = alsUebersicht(rohUebersicht());
  assert.ok(u);
  assert.equal(u.aktiv, true);
  assert.deepEqual(u.ziele.map((z) => [z.name, z.status, z.belegtVon]), [["sokrates2", "frei", null], ["caesar", "belegt", "Bernd"]]);
  assert.equal(u.meine, null);
  assert.equal(u.maxMinuten, 60);
  assert.equal(u.leerlaufMinuten, 10);
});

test("Übersicht: eigene Sitzung", () => {
  const u = alsUebersicht(
    rohUebersicht({ meine: { sitzung: SITZUNG, ziel: "caesar", anzeige: "Caesar", start: "2026-10-31T12:00:00+00:00", endet: "2026-10-31T13:00:00+00:00" } }),
  );
  assert.deepEqual(u?.meine, { sitzung: SITZUNG, ziel: "caesar", anzeige: "Caesar", start: "2026-10-31T12:00:00+00:00", endet: "2026-10-31T13:00:00+00:00" });
});

test("Übersicht: unbrauchbare Antworten ergeben null, schlechte Zeilen fallen weg", () => {
  for (const schlecht of [null, undefined, 5, "x", [], {}, { aktiv: "ja", ziele: [] }, { aktiv: true }, { aktiv: true, ziele: "x" }]) {
    assert.equal(alsUebersicht(schlecht), null);
  }
  const u = alsUebersicht(
    rohUebersicht({
      ziele: [
        { name: "gut", anzeige: "Gut", status: "frei" },
        { name: "GROSS", anzeige: "x", status: "frei" },
        { name: "../x", anzeige: "x", status: "frei" },
        { name: "falsch", anzeige: "x", status: "offen" },
        { name: "gut", anzeige: "doppelt", status: "belegt" },
        { name: "ohneanzeige", status: "nicht_erreichbar" },
        "kein Objekt",
        null,
      ],
    }),
  );
  assert.deepEqual(u?.ziele.map((z) => z.name), ["gut", "ohneanzeige"]);
  assert.equal(u?.ziele[1].anzeige, "ohneanzeige");
});

test("Übersicht: Texte werden gesäubert, Zahlen begrenzt, ungültige Sitzung fällt weg", () => {
  const u = alsUebersicht(
    rohUebersicht({
      ziele: [{ name: "a", anzeige: "Sokrates‮-2\u0000" + "x".repeat(100), status: "belegt", belegt_von: "B​ernd", seit: "kein Datum" }],
      meine: { sitzung: "keine-uuid", ziel: "a", endet: "2026-10-31T13:00:00+00:00" },
      max_minuten: 99999,
      leerlauf_minuten: -3,
    }),
  );
  assert.ok(u);
  assert.ok(u.ziele[0].anzeige.length <= 40);
  assert.ok(!/[‮\u0000​]/.test(u.ziele[0].anzeige + (u.ziele[0].belegtVon ?? "")));
  assert.equal(u.ziele[0].seit, null);
  assert.equal(u.meine, null);
  assert.equal(u.maxMinuten, 60);
  assert.equal(u.leerlaufMinuten, 10);
});

test("Übersicht: höchstens 16 Ziele", () => {
  const viele = Array.from({ length: 40 }, (_, i) => ({ name: `z${i}`, anzeige: `Z${i}`, status: "frei" }));
  assert.equal(alsUebersicht(rohUebersicht({ ziele: viele }))?.ziele.length, 16);
});

// ---------- Adresse der Gateway-Seite ----------
test("Seitenadresse: nur https, Fragment mit Verbindung und Token, andere Herkunft als die Oberfläche", () => {
  assert.equal(seitenUrlPruefen(URL_OK, UI), URL_OK);
  assert.equal(seitenUrlPruefen(URL_OK), URL_OK);
  assert.equal(seitenUrlPruefen(`http://127.0.0.1:18080/?token=${TOKEN}#/client/ZmVybnNpdHp1bmcAYwBqc29u`, UI)?.startsWith("http://127.0.0.1:18080/"), true);
});

test("Seitenadresse: alles andere wird abgelehnt", () => {
  const schlecht = [
    `http://192.168.178.136:55446/?token=${TOKEN}#/client/ZmVybnNpdHp1bmcAYwBqc29u`, // kein https, keine Loopback
    `javascript:alert(1)`,
    `data:text/html,<script>1</script>`,
    `https://user:pw@192.168.178.136:55446/?token=${TOKEN}#/client/ZmVybnNpdHp1bmcAYwBqc29u`,
    `https://192.168.178.136:55446/pfad?token=${TOKEN}#/client/ZmVybnNpdHp1bmcAYwBqc29u`,
    `https://192.168.178.136:55446/?x=1#/client/ZmVybnNpdHp1bmcAYwBqc29u`,
    `https://192.168.178.136:55446/?token=kurz#/client/ZmVybnNpdHp1bmcAYwBqc29u`,
    `https://192.168.178.136:55446/?token=${TOKEN}&x=1#/client/ZmVybnNpdHp1bmcAYwBqc29u`,
    `https://192.168.178.136:55446/?token=${TOKEN}#/settings`,
    `https://192.168.178.136:55446/?token=${TOKEN}#/client/a/b`,
    `https://192.168.178.136:55446/#/client/ZmVybnNpdHp1bmcAYwBqc29u?token=${TOKEN}`, // Token hinter dem Fragment: die Seite nähme es nicht an
    `https://192.168.178.136:55446/?token=${TOKEN}#/client/ZmVybnNpdHp1bmcAYwBqc29u x`,
    `${URL_OK}${"a".repeat(700)}`,
    "",
    "kein url",
    5,
    null,
    undefined,
    {},
  ];
  for (const s of schlecht) assert.equal(seitenUrlPruefen(s, UI), null, String(s).slice(0, 60));
  // dieselbe Herkunft wie die Oberfläche (das Token der Oberfläche läge dort im Speicher): nie öffnen
  assert.equal(seitenUrlPruefen(URL_OK, "https://192.168.178.136:55446"), null);
});

test("Start: gültige Antwort", () => {
  const s = alsStart({ ok: true, sitzung: SITZUNG, ziel: "sokrates2", anzeige: "Sokrates-2", url: URL_OK, endet: "2026-10-31T13:00:00+00:00", max_minuten: 60, leerlauf_minuten: 10 }, UI);
  assert.deepEqual(s, { sitzung: SITZUNG, ziel: "sokrates2", anzeige: "Sokrates-2", url: URL_OK, endet: "2026-10-31T13:00:00+00:00", maxMinuten: 60, leerlaufMinuten: 10 });
});

test("Start: fehlerhafte Antworten öffnen nie ein Fenster", () => {
  const gut = { ok: true, sitzung: SITZUNG, ziel: "sokrates2", anzeige: "Sokrates-2", url: URL_OK, endet: "2026-10-31T13:00:00+00:00" };
  assert.ok(alsStart(gut, UI));
  for (const ueber of [{ ok: false }, { ok: "ja" }, { sitzung: "x" }, { ziel: "../x" }, { endet: "morgen" }, { url: "http://bose.example/" }, { url: undefined }]) {
    assert.equal(alsStart({ ...gut, ...ueber }, UI), null, JSON.stringify(ueber));
  }
  for (const k of [null, 5, "x", []]) assert.equal(alsStart(k, UI), null);
});

// ---------- Anzeige ----------
test("Zustände haben Form und Wort", () => {
  assert.deepEqual(Object.keys(STATUS_INFO).sort(), ["belegt", "frei", "nicht_erreichbar"]);
  const formen = new Set(Object.values(STATUS_INFO).map((i) => i.form));
  assert.equal(formen.size, 3); // nie nur Farbe: jede Form kommt einmal vor
  for (const i of Object.values(STATUS_INFO)) assert.ok(i.wort.length > 0 && i.erklaerung.length > 0);
});

test("Zeile zum Ziel", () => {
  assert.match(zielZeile(ziel()), /erreichbar/);
  assert.equal(zielZeile(ziel({ status: "belegt", belegtVon: "Bernd", seit: "2026-10-31T12:30:00+00:00" }), "UTC"), "Belegt von Bernd seit 12:30 Uhr.");
  assert.equal(zielZeile(ziel({ status: "belegt", belegtVon: null, seit: null })), "Belegt von einer anderen Person.");
  assert.equal(zielZeile(ziel({ status: "belegt", eigene: true, seit: "2026-10-31T12:30:00+00:00" }), "UTC"), "Du bist verbunden, seit 12:30 Uhr.");
  assert.match(zielZeile(ziel({ status: "nicht_erreichbar" })), /nicht erreichbar|nicht eingerichtet/);
  assert.equal(uhrzeit(null), "unbekannt");
  assert.equal(uhrzeit("kein datum"), "unbekannt");
});

test("Knopf: Verbinden nur bei freiem Ziel, eigene Sitzung lässt sich neu starten", () => {
  assert.deepEqual(verbindenKnopf(ziel()), { text: "Verbinden", aktiv: true, grund: null });
  assert.deepEqual(verbindenKnopf(ziel({ status: "belegt", eigene: true })), { text: "Neu verbinden", aktiv: true, grund: null });
  const belegt = verbindenKnopf(ziel({ status: "belegt" }));
  assert.equal(belegt.aktiv, false);
  assert.match(belegt.grund ?? "", /belegt/);
  const weg = verbindenKnopf(ziel({ status: "nicht_erreichbar" }));
  assert.equal(weg.aktiv, false);
  assert.equal(weg.text, "Nicht erreichbar");
});

test("Sortierung: eigene Sitzung, frei, belegt, nicht erreichbar; Eingabe bleibt unverändert", () => {
  const liste = [
    ziel({ name: "d", anzeige: "D", status: "nicht_erreichbar" }),
    ziel({ name: "c", anzeige: "C", status: "belegt" }),
    ziel({ name: "b", anzeige: "B", status: "frei" }),
    ziel({ name: "a", anzeige: "A", status: "belegt", eigene: true }),
    ziel({ name: "e", anzeige: "A frei", status: "frei" }),
  ];
  const kopie = [...liste];
  assert.deepEqual(sortiereZiele(liste).map((z) => z.name), ["a", "e", "b", "c", "d"]);
  assert.deepEqual(liste, kopie);
});

// ---------- Dialog ----------
test("Hinweis nennt Datenschutz, Aufzeichnung, Dauer und Leerlauf", () => {
  const t = hinweisZeilen("Sokrates-2", 60, 10).join(" ");
  assert.match(t, /Daten anderer Personen/);
  assert.match(t, /nichts aufgezeichnet/);
  assert.match(t, /60 Minuten/);
  assert.match(t, /10 Minuten ohne Eingabe/);
  assert.match(t, /Sokrates-2/);
  assert.ok(!/[–—]|\s-\s/.test(t), "Gedankenstrich im Hinweis");
});

test("Code: sechs Ziffern, Leerzeichen erlaubt", () => {
  assert.deepEqual(eingabePruefen("123456"), { ok: true, code: "123456" });
  assert.deepEqual(eingabePruefen("123 456"), { ok: true, code: "123456" });
  for (const schlecht of ["", "12345", "1234567", "abcdef", "12345a", "123-456"]) assert.equal(eingabePruefen(schlecht).ok, false, schlecht);
});

test("Fehler beim Start: Code zu alt hält den Dialog offen", () => {
  const f = startFehler({ status: 403, code: "zweiter_faktor_zu_alt", text: "Bitte gib den Code noch einmal ein." });
  assert.deepEqual([f.dialogOffen, f.neuLaden, f.feld], [true, false, "code"]);
});

test("Fehler beim Start: belegt oder nicht vorhanden lädt die Liste neu", () => {
  for (const status of [404, 409]) {
    const f = startFehler({ status, text: "Sokrates-2 ist belegt von Bernd seit 12:30 Uhr." });
    assert.deepEqual([f.dialogOffen, f.neuLaden], [false, true]);
  }
});

test("Fehler beim Start: keine Antwort oder Serverfehler heißt Ausgang offen", () => {
  for (const status of [0, 500, 502, 503]) {
    const f = startFehler({ status, text: "Kern nicht erreichbar" });
    assert.equal(f.neuLaden, true);
    assert.match(f.text, /zeigt die Liste/);
  }
});

test("Fehler beim Start: andere Fehler bleiben einfach Text (Rolle, Rate)", () => {
  for (const status of [400, 403, 429]) {
    const f = startFehler({ status, text: "Zu viele Anfragen" });
    assert.deepEqual([f.dialogOffen, f.neuLaden, f.feld], [false, false, null]);
  }
  assert.equal(startFehler({ status: 403, code: "andere_sache", text: "Nur für Admins" }).dialogOffen, false);
  assert.equal(startFehler({ status: 429, text: "\u0000‮" }).text, "Unbekannter Fehler.");
});

// ---------- Restzeit ----------
test("Restzeit: Sekunden bis zum Ende, nie negativ", () => {
  const jetzt = Date.parse("2026-10-31T12:00:00Z");
  assert.equal(restSekunden("2026-10-31T13:00:00Z", jetzt), 3600);
  assert.equal(restSekunden("2026-10-31T12:00:00.400Z", jetzt), 1); // aufgerundet
  assert.equal(restSekunden("2026-10-31T11:00:00Z", jetzt), 0);
  assert.equal(restSekunden(null, jetzt), 0);
  assert.equal(restSekunden("kein datum", jetzt), 0);
});

test("Restzeit: Text mm:ss, ab einer Stunde h:mm:ss", () => {
  assert.equal(restzeitText(0), "00:00");
  assert.equal(restzeitText(59), "00:59");
  assert.equal(restzeitText(2530), "42:10");
  assert.equal(restzeitText(3599), "59:59");
  assert.equal(restzeitText(3600), "1:00:00");
  assert.equal(restzeitText(3900), "1:05:00");
  assert.equal(restzeitText(-5), "00:00");
});

test("Restzeit: Sprache für Screenreader", () => {
  assert.equal(restzeitSprache(0), "0 Sekunden");
  assert.equal(restzeitSprache(1), "1 Sekunde");
  assert.equal(restzeitSprache(59), "59 Sekunden");
  assert.equal(restzeitSprache(60), "1 Minute");
  assert.equal(restzeitSprache(125), "2 Minuten und 5 Sekunden");
  assert.equal(restzeitSprache(2530), "42 Minuten"); // ab zehn Minuten ohne Sekunden, die Ansage bleibt kurz
  assert.equal(restzeitSprache(540), "9 Minuten");
});

test("Restzeit knapp: höchstens zwei Minuten", () => {
  assert.equal(restzeitKnapp(121), false);
  assert.equal(restzeitKnapp(120), true);
  assert.equal(restzeitKnapp(1), true);
  assert.equal(restzeitKnapp(0), false);
});

test("Ansage: nur beim Unterschreiten einer Schwelle, nie dazwischen, am Ende ein Satz", () => {
  assert.deepEqual([...ANSAGE_SCHWELLEN], [600, 300, 120, 60]);
  assert.equal(restzeitAnsage(601, 600), "Noch 10 Minuten Fernsitzung.");
  assert.equal(restzeitAnsage(301, 299), "Noch 5 Minuten Fernsitzung.");
  assert.equal(restzeitAnsage(121, 120), "Noch 2 Minuten Fernsitzung.");
  assert.equal(restzeitAnsage(61, 60), "Noch eine Minute Fernsitzung.");
  assert.equal(restzeitAnsage(3000, 2999), null);
  assert.equal(restzeitAnsage(600, 599), null); // 600 war schon unterschritten
  assert.equal(restzeitAnsage(1, 0), "Die Fernsitzung ist zu Ende.");
  assert.equal(restzeitAnsage(0, 0), null);
  // ein großer Sprung (Tab im Hintergrund) meldet nur die kleinste unterschrittene Schwelle, nie mehrere
  assert.equal(restzeitAnsage(700, 100), "Noch 2 Minuten Fernsitzung.");
});

// ---------- Meldungen ----------
test("Meldungen ohne Gedankenstrich", () => {
  const texte = [
    startMeldung("Sokrates-2", true),
    startMeldung("Sokrates-2", false),
    endeMeldung("Sokrates-2", true),
    endeMeldung("Sokrates-2", false),
    ...Object.values(STATUS_INFO).flatMap((i) => [i.wort, i.erklaerung]),
    verbindenKnopf(ziel({ status: "belegt" })).grund ?? "",
  ];
  for (const t of texte) assert.ok(!/[–—]|\s-\s/.test(t), t);
  assert.match(startMeldung("Sokrates-2", false), /blockiert/);
  assert.match(endeMeldung("Sokrates-2", false), /nicht bestätigt/);
});

// ---------- KP-2: der Code nur auf Verlangen des Kerns ----------
test("Dialog beginnt ohne Codefeld und bekommt es nur, wenn der Kern den Code verlangt", () => {
  const verlangt = startFehler({ status: 403, code: "zweiter_faktor_zu_alt", text: "Deine Anmeldung ist älter als 24 Stunden." });
  assert.equal(modusNachFehler("klick", verlangt), "code");
  const andere = [
    startFehler({ status: 409, text: "Sokrates-2 ist belegt." }),
    startFehler({ status: 404, text: "Dieses Ziel gibt es nicht." }),
    startFehler({ status: 429, text: "Zu viele Anfragen" }),
    startFehler({ status: 503, text: "Gateway" }),
    startFehler({ status: 0, text: "Kern nicht erreichbar" }),
    startFehler({ status: 403, code: "andere_sache", text: "Nur für Admins" }),
    startFehler({ status: 403, text: "Nur für Admins" }),
  ];
  for (const f of andere) assert.equal(modusNachFehler("klick", f), "klick", f.text);
  // ein einmal gezeigtes Codefeld bleibt bis zum Schließen des Dialogs, auch nach einem anderen Fehler
  assert.equal(modusNachFehler("code", andere[0]), "code");
});

test("Fokus beim Öffnen: nie auf Verbinden, das braucht einen echten Klick (Befund M7)", () => {
  assert.equal(startFokus("klick"), "abbrechen");
  assert.equal(startFokus("code"), "code");
  for (const m of ["klick", "code"] as const) assert.notEqual(startFokus(m) as string, "verbinden");
});

test("Texte: Einleitung nennt Verbinden, den Code nur bei zu alter Anmeldung und die Meldung an alle Admins", () => {
  assert.match(EINLEITUNG, /„Verbinden“/);
  assert.match(EINLEITUNG, /Ist deine Anmeldung zu alt, fragt der Dialog den Code/);
  assert.match(EINLEITUNG, /allen Admins gemeldet/);
  assert.match(EINLEITUNG, /eine Stunde/);
  assert.doesNotMatch(EINLEITUNG, /aktuellen Code|Jede Sitzung braucht|bei jedem Start/);
  assert.match(CODE_HILFE, /höchstens zwei Minuten/);
  assert.doesNotMatch(CODE_HILFE, /jeder Start|jedem Start|auch wenn du dich gerade angemeldet/i);
  for (const t of [EINLEITUNG, CODE_HILFE]) assert.ok(!/[–—]|\s-\s/.test(t), t);
});
