// Freigabe in der Karte "Änderungen" (Schritt 1.5b): Voraussetzungen, Frische des Codes, Fehlerbehandlung, Rumpf des Aufrufs.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Anfrage } from "./aenderungen.ts";
import { CODE_MAX_ALTER_MS, FEHLER, codeFrisch, erfolgText, fehlerFuer, freigabeBody, NETZ_FEHLER, voraussetzung } from "./aenderungenFreigabe.ts";

const A = "a".repeat(40);
const B = "b".repeat(40);

function anfrage(aenderung: Partial<Anfrage> = {}): Anfrage {
  return {
    id: "0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7b", status: "wartet_freigabe", quelle: "main", schritt: 5, schritte: 9, seit: null, frist_bis: null, versuch: 0,
    fertig: false, version: "0.1.0", klasse: "normal", von_mir: false, v_commit: A, tree_id: B, treffer_anzahl: 0, pruefstand: {}, pruefung: null, ...aenderung,
  };
}

test("Knopf nur bei wartet_freigabe und mit Commit und Baum", () => {
  assert.equal(voraussetzung(null).moeglich, false);
  assert.equal(voraussetzung(anfrage({ status: "pruefung3" })).moeglich, false);
  assert.equal(voraussetzung(anfrage({ status: "pruefung3" })).text, null, "vor der Freigabe gibt es keinen Hinweis");
  assert.equal(voraussetzung(anfrage()).moeglich, true);
  assert.equal(voraussetzung(anfrage({ v_commit: null })).moeglich, false);
  assert.equal(voraussetzung(anfrage({ tree_id: null })).moeglich, false);
  assert.match(voraussetzung(anfrage({ tree_id: null })).text ?? "", /neu laden/);
});

test("gesperrte Änderung lässt sich nie freigeben", () => {
  const v = voraussetzung(anfrage({ klasse: "gesperrt" }));
  assert.equal(v.moeglich, false);
  assert.match(v.text ?? "", /gesperrt/);
});

test("Sonderfreigabe: Pflicht-Haken, und wer angefragt hat, darf nicht selbst freigeben", () => {
  const fremd = voraussetzung(anfrage({ klasse: "sonder", von_mir: false }));
  assert.deepEqual([fremd.moeglich, fremd.pflichtHaken], [true, true]);
  const eigen = voraussetzung(anfrage({ klasse: "sonder", von_mir: true }));
  assert.equal(eigen.moeglich, false);
  assert.match(eigen.text ?? "", /anderer Admin/);
  assert.equal(voraussetzung(anfrage()).pflichtHaken, false);
});

test("Code älter als 45 Sekunden oder ohne Zeitstempel verlangt einen neuen Code", () => {
  const jetzt = 1_800_000_000_000;
  assert.equal(codeFrisch(jetzt - 10_000, jetzt), true);
  assert.equal(codeFrisch(jetzt - CODE_MAX_ALTER_MS, jetzt), true);
  assert.equal(codeFrisch(jetzt - CODE_MAX_ALTER_MS - 1, jetzt), false);
  assert.equal(codeFrisch(jetzt - 100_000, jetzt), false, "die Oberfläche würde 120 Sekunden erlauben, der Dienst nur 60");
  assert.equal(codeFrisch(null, jetzt), false);
  assert.equal(codeFrisch(Number.NaN, jetzt), false);
  assert.equal(codeFrisch(jetzt + 1_000, jetzt), true, "kleine Uhrenabweichung ist erlaubt");
  assert.equal(codeFrisch(jetzt + 60_000, jetzt), false, "weit aus der Zukunft nie");
});

test("Der Rumpf enthält nur Commit und Baum, die die Karte zeigte", () => {
  assert.deepEqual(freigabeBody(anfrage()), { commit_id: A, tree_id: B });
  assert.deepEqual(Object.keys(freigabeBody(anfrage())!), ["commit_id", "tree_id"]);
  assert.equal(freigabeBody(anfrage({ v_commit: null })), null);
});

test("jeder Grund des Freigabe-Dienstes hat einen Text", () => {
  const gruende = ["angaben", "token_ungueltig", "aal2_fehlt", "aal2_alt", "aal2_zukunft", "anfrage_unbekannt", "falscher_status", "fassung_passt_nicht",
    "keine_berechtigung", "zweiter_admin", "wartezeit", "schon_freigegeben", "regel", "zu_viele", "datenbank", "unbekannt", "intern", "anfrage"];
  for (const g of gruende) {
    const f = fehlerFuer(g, 400);
    assert.ok(f.text.length > 10, g);
    assert.equal(f, FEHLER[g]);
  }
});

test("Code-Fehler lassen den Dialog offen und verlangen einen neuen Code, Fassungsfehler laden neu", () => {
  for (const g of ["aal2_alt", "aal2_fehlt", "aal2_zukunft"]) {
    assert.deepEqual([fehlerFuer(g, 403).offen, fehlerFuer(g, 403).neuerCode], [true, true], g);
  }
  assert.equal(fehlerFuer("datenbank", 503).offen, true);
  for (const g of ["fassung_passt_nicht", "falscher_status", "anfrage_unbekannt"]) {
    assert.deepEqual([fehlerFuer(g, 409).offen, fehlerFuer(g, 409).neuLaden], [false, true], g);
  }
});

test("schon_freigegeben zählt als Erfolg", () => {
  assert.equal(fehlerFuer("schon_freigegeben", 409).erfolg, true);
  assert.equal(fehlerFuer("regel", 409).erfolg, undefined);
});

test("unbekannte Codes und Netzfehler ergeben nie ein stilles ok und senden nichts doppelt", () => {
  const n = fehlerFuer(null, 502);
  assert.equal(n, NETZ_FEHLER);
  assert.equal(n.offen, false, "kein Dialog zum erneuten Senden");
  assert.equal(n.neuLaden, true, "erst nachsehen, ob die Freigabe ankam");
  assert.equal(n.erfolg, undefined);
  assert.equal(fehlerFuer("__proto__", 400), NETZ_FEHLER);
  assert.equal(fehlerFuer("toString", 400), NETZ_FEHLER);
  assert.equal(fehlerFuer(null, 401), FEHLER.token_ungueltig);
  assert.equal(fehlerFuer(null, 429), FEHLER.zu_viele);
});

test("Erfolgstext nennt die Geltungsdauer", () => {
  assert.match(erfolgText(900), /15 Minuten/);
  assert.match(erfolgText(30), /1 Minute\./);
  assert.doesNotMatch(erfolgText(null), /gilt/);
});

test("keine Gedankenstriche in den Texten", () => {
  const alle = [...Object.values(FEHLER).map((f) => f.text), NETZ_FEHLER.text, erfolgText(600), voraussetzung(anfrage({ klasse: "sonder", von_mir: true })).text ?? ""];
  for (const t of alle) assert.doesNotMatch(t, /[–—]/);
});
