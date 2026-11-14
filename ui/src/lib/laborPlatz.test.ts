// Tests für die Platz-Ansicht der Laborkonten (lib/laborPlatz.ts): Platznummer aus der Kennung und die Schalter, die eine Laborsicht
// von der vollen Ansicht trennen. Nur Darstellung: Der Kern sperrt weiter selbst, hier wird nichts entschieden, was Sicherheit wäre.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { laborAnsicht, laborPlatzNummer } from "./laborPlatz.ts";
import { istLaborKonto } from "./stillStart.ts";

const NORMAL = ["niko@wamocon.com", "bernd.admin@wamocon.com", "labor-01@wamocon.com", "labor-01@wamo.test.example.org", "xlabor-01@wamo.test"];

test("Platznummer: labor-01 bis labor-16 geben 1 bis 16", () => {
  for (let n = 1; n <= 16; n++) {
    assert.equal(laborPlatzNummer(`labor-${String(n).padStart(2, "0")}@wamo.test`), n, `Platz ${n}`);
  }
});

test("Platznummer: Groß- und Kleinschreibung und Leerzeichen am Rand zählen wie bei istLaborKonto", () => {
  assert.equal(laborPlatzNummer("  Labor-03@WAMO.test "), 3);
  assert.equal(laborPlatzNummer("LABOR-16@wamo.test"), 16);
});

test("Platznummer: das Schaufenster ist ein Laborkonto ohne Nummer", () => {
  assert.equal(istLaborKonto("labor-schau@wamo.test"), true);
  assert.equal(laborPlatzNummer("labor-schau@wamo.test"), null);
});

test("Platznummer: alles, was kein Laborkonto ist, hat keine Nummer", () => {
  for (const mail of [...NORMAL, "labor-00@wamo.test", "labor-17@wamo.test", "labor-1@wamo.test", "labor-001@wamo.test", "labor-ab@wamo.test", ""]) {
    assert.equal(laborPlatzNummer(mail), null, mail);
  }
});

test("Platznummer: null, undefined und Nicht-Texte geben null, ohne zu werfen", () => {
  assert.equal(laborPlatzNummer(null), null);
  assert.equal(laborPlatzNummer(undefined), null);
  assert.equal(laborPlatzNummer(3 as unknown as string), null);
  assert.equal(laborPlatzNummer({} as unknown as string), null);
});

test("laborAnsicht: ein normales Konto sieht alles, die Kopfzeile bleibt WAMO, Abmelden ohne Rückfrage", () => {
  for (const mail of [...NORMAL, null, undefined, ""]) {
    const a = laborAnsicht(mail);
    assert.deepEqual(
      a,
      {
        labor: false,
        platz: null,
        kopfTitel: null,
        bueroKarte: true,
        verlauf: true,
        vorgaenge: true,
        adminHinweise: true,
        geraeteEinrichtung: true,
        modellzugang: true,
        stimme: true,
        einstellungen: "alle",
        abmeldenFragen: false,
      },
      String(mail),
    );
  }
});

test("laborAnsicht: ein Platzkonto schaltet Büro-Karte, Verlauf, Vorgänge, Admin-Hinweise, Geräteeinrichtung und Modellzugang aus", () => {
  const a = laborAnsicht("labor-03@wamo.test");
  assert.equal(a.labor, true);
  assert.equal(a.platz, 3);
  assert.equal(a.bueroKarte, false);
  assert.equal(a.verlauf, false);
  assert.equal(a.vorgaenge, false);
  assert.equal(a.adminHinweise, false);
  assert.equal(a.geraeteEinrichtung, false);
  assert.equal(a.modellzugang, false);
  assert.equal(a.stimme, false, "keine Stimmerkennung im Labor");
  assert.equal(a.einstellungen, "mikrofon");
  assert.equal(a.abmeldenFragen, true);
});

test("laborAnsicht: Titel der Kopfzeile mit Platz, ohne Nummer nur Testlabor", () => {
  assert.equal(laborAnsicht("labor-03@wamo.test").kopfTitel, "WAMO Testlabor, Platz 3");
  assert.equal(laborAnsicht("labor-16@wamo.test").kopfTitel, "WAMO Testlabor, Platz 16");
  const schau = laborAnsicht("labor-schau@wamo.test");
  assert.equal(schau.labor, true);
  assert.equal(schau.platz, null);
  assert.equal(schau.kopfTitel, "WAMO Testlabor");
  assert.ok(!/[–—]|\s-\s/.test(`${laborAnsicht("labor-03@wamo.test").kopfTitel}`), "kein Gedankenstrich");
});

test("laborAnsicht: das Schaufenster hat dieselben Schalter wie ein Platz", () => {
  const { platz: _p, kopfTitel: _t, ...schau } = laborAnsicht("labor-schau@wamo.test");
  const { platz: _q, kopfTitel: _u, ...platz } = laborAnsicht("labor-01@wamo.test");
  void [_p, _t, _q, _u];
  assert.deepEqual(schau, platz);
});

test("laborAnsicht: labor stimmt immer mit istLaborKonto überein (keine zweite Regel)", () => {
  for (const mail of [...NORMAL, "labor-01@wamo.test", "labor-schau@wamo.test", " LABOR-12@WAMO.TEST ", null, undefined, ""]) {
    assert.equal(laborAnsicht(mail).labor, istLaborKonto(mail), String(mail));
  }
});

test("laborAnsicht: das Ergebnis ist eingefroren, ein Aufrufer kann die gemeinsame Sicht nicht verändern", () => {
  assert.throws(() => {
    (laborAnsicht("niko@wamocon.com") as { verlauf: boolean }).verlauf = false;
  }, TypeError);
  assert.throws(() => {
    (laborAnsicht("labor-01@wamo.test") as { verlauf: boolean }).verlauf = true;
  }, TypeError);
  assert.equal(laborAnsicht("niko@wamocon.com").verlauf, true);
});
