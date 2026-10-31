// Tests für Höchstalter der Anmeldung und "Angemeldet bleiben" (lib/anmeldung.ts).
// Ausführen im Ordner ui/:  npm test   (node:test, Node lädt TypeScript selbst)
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BROWSER_MAX_STUNDEN,
  DESKTOP_MAX_STUNDEN,
  aal2Frisch,
  aal2Zeitpunkt,
  anmeldeZeitpunkt,
  anmeldungAbgelaufen,
  authSpeicher,
  dauerText,
  maxAnmeldungStunden,
  merkeAbmeldeGrund,
  nimmAbmeldeGrund,
  type Speicher,
} from "./anmeldung.ts";

const STUNDE = 3600_000;

/** Unsigniertes Test-JWT (die Oberfläche liest nur, geprüft wird im Kern). */
function jwt(claims: Record<string, unknown>): string {
  const teil = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${teil({ alg: "ES256" })}.${teil(claims)}.signatur`;
}

test("Anmeldezeitpunkt ist der früheste Zeitstempel in amr", () => {
  const t = jwt({ amr: [{ method: "totp", timestamp: 2000 }, { method: "password", timestamp: 1000 }] });
  assert.equal(anmeldeZeitpunkt(t), 1000_000);
});

test("ohne amr oder kaputt: kein Zeitpunkt", () => {
  for (const t of [jwt({}), jwt({ amr: [] }), jwt({ amr: [{ timestamp: "1" }] }), jwt({ amr: "x" }), "kein.jwt", ""]) {
    assert.equal(anmeldeZeitpunkt(t), null, t);
  }
  // Umlaute im Token (base64url mit - und _) werden richtig gelesen
  const t = jwt({ name: "Jürgen ä?>", amr: [{ timestamp: 5 }] });
  assert.equal(anmeldeZeitpunkt(t), 5000);
});

test("abgelaufen nach Höchstalter, ohne amr sofort", () => {
  const jetzt = 1_760_000_000_000;
  const vor = (h: number) => jwt({ amr: [{ method: "password", timestamp: (jetzt - h * STUNDE) / 1000 }] });
  assert.equal(anmeldungAbgelaufen(vor(23), jetzt, 24), false);
  assert.equal(anmeldungAbgelaufen(vor(25), jetzt, 24), true);
  assert.equal(anmeldungAbgelaufen(vor(167), jetzt, 168), false);
  assert.equal(anmeldungAbgelaufen(vor(169), jetzt, 168), true);
  assert.equal(anmeldungAbgelaufen(jwt({}), jetzt, 168), true); // ohne Zeitpunkt gilt die Anmeldung als zu alt
  assert.equal(anmeldungAbgelaufen(undefined, jetzt, 168), false); // ohne Sitzung gibt es nichts abzumelden
  // Zeitstempel in der Zukunft (Millisekunden statt Sekunden, falsche Uhr): nicht glaubwürdig, also abgelaufen
  assert.equal(anmeldungAbgelaufen(jwt({ amr: [{ timestamp: jetzt }] }), jetzt, 168), true);
  assert.equal(anmeldungAbgelaufen(jwt({ amr: [{ timestamp: (jetzt + 60_000) / 1000 }] }), jetzt, 168), false);
});

test("Desktop 7 Tage, Browser kürzer und begrenzt", () => {
  assert.equal(DESKTOP_MAX_STUNDEN, 168);
  assert.equal(BROWSER_MAX_STUNDEN, 24);
  assert.equal(maxAnmeldungStunden(true, "2"), 168); // der Browser-Wert gilt nicht für die Desktop-App
  assert.equal(maxAnmeldungStunden(false, undefined), 24);
  assert.equal(maxAnmeldungStunden(false, "8"), 8);
  for (const wert of ["", "0", "-1", "abc", "1000", "1.5", "Infinity"]) {
    assert.equal(maxAnmeldungStunden(false, wert), 24, wert);
  }
  assert.equal(maxAnmeldungStunden(false, "168"), 168);
  assert.equal(dauerText(168), "7 Tagen");
  assert.equal(dauerText(24), "24 Stunden");
  assert.equal(dauerText(1), "1 Stunde");
});

class Mem implements Speicher {
  daten = new Map<string, string>();
  getItem(k: string) {
    return this.daten.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.daten.set(k, v);
  }
  removeItem(k: string) {
    this.daten.delete(k);
  }
}

test("Angemeldet bleiben: neue Sitzung je nach Wahl, Abmelden räumt beide", () => {
  const lokal = new Mem();
  const sitzung = new Mem();
  let bleiben = true;
  const s = authSpeicher(lokal, sitzung, () => bleiben);
  s.setItem("sb", "a");
  assert.equal(lokal.getItem("sb"), "a");
  assert.equal(sitzung.getItem("sb"), null);
  s.removeItem("sb");
  bleiben = false;
  s.setItem("sb", "b");
  assert.equal(sitzung.getItem("sb"), "b");
  assert.equal(lokal.getItem("sb"), null);
  assert.equal(s.getItem("sb"), "b");
  s.removeItem("sb");
  assert.equal(lokal.getItem("sb"), null);
  assert.equal(sitzung.getItem("sb"), null);
});

test("eine laufende Sitzung bleibt, wo sie ist, auch wenn ein anderer Tab die Wahl ändert", () => {
  const lokal = new Mem();
  const sitzung = new Mem();
  let bleiben = false;
  const s = authSpeicher(lokal, sitzung, () => bleiben);
  s.setItem("sb", "kurz");
  bleiben = true; // zweiter Tab meldet sich mit Haken an
  s.setItem("sb", "kurz-erneuert"); // Token-Erneuerung im ersten Tab
  assert.equal(sitzung.getItem("sb"), "kurz-erneuert");
  assert.equal(lokal.getItem("sb"), null); // nicht dauerhaft gemacht
  lokal.setItem("sb", "fremd"); // Sitzung des anderen Tabs
  assert.equal(s.getItem("sb"), "kurz-erneuert"); // die eigene (sessionStorage) hat Vorrang
});

test("Schreibfehler im Ziel löscht keine vorhandene Kopie", () => {
  const lokal = new Mem();
  const sitzung = new Mem();
  sitzung.setItem("sb", "alt");
  sitzung.setItem = () => {
    throw new Error("voll");
  };
  const s = authSpeicher(lokal, sitzung, () => false);
  s.setItem("sb", "neu");
  assert.equal(s.getItem("sb") !== null, true); // die Sitzung ist nicht verloren
});

test("ohne Speicher lebt die Sitzung im Speicher der Seite", () => {
  const s = authSpeicher(null, null, () => true);
  s.setItem("sb", "x");
  assert.equal(s.getItem("sb"), "x");
  s.removeItem("sb");
  assert.equal(s.getItem("sb"), null);
});

test("Abmeldegrund gilt nur kurz", () => {
  merkeAbmeldeGrund("zu alt", 1000);
  assert.equal(nimmAbmeldeGrund(5000), "zu alt");
  assert.equal(nimmAbmeldeGrund(5000), null); // nur einmal
  merkeAbmeldeGrund("zu alt", 1000);
  assert.equal(nimmAbmeldeGrund(1000 + 60_000), null); // veraltet: nicht bei einer späteren Abmeldung zeigen
});

test("ohne Speicher (privates Fenster) kein Absturz", () => {
  const kaputt: Speicher = {
    getItem: () => {
      throw new Error("gesperrt");
    },
    setItem: () => {
      throw new Error("gesperrt");
    },
    removeItem: () => {
      throw new Error("gesperrt");
    },
  };
  const s = authSpeicher(kaputt, null, () => true);
  assert.equal(s.getItem("sb"), null);
  s.setItem("sb", "x");
  s.removeItem("sb");
});

test("Zeitpunkt des zweiten Faktors ist der späteste totp-Eintrag, nicht das Passwort", () => {
  const t = jwt({
    aal: "aal2",
    amr: [{ method: "password", timestamp: 9000 }, { method: "totp", timestamp: 1000 }, { method: "totp", timestamp: 2000 }],
  });
  assert.equal(aal2Zeitpunkt(t), 2_000_000);
  assert.equal(aal2Zeitpunkt(jwt({ aal: "aal2", amr: [{ method: "mfa/totp", timestamp: 5 }] })), 5000);
  for (const kaputt of [
    jwt({ aal: "aal1", amr: [{ method: "totp", timestamp: 5 }] }),
    jwt({ aal: "aal2", amr: [{ method: "password", timestamp: 5 }] }),
    jwt({ aal: "aal2", amr: [{ method: "totp", timestamp: "5" }] }),
    jwt({ aal: "aal2", amr: [{ method: "totp", timestamp: -1 }] }),
    jwt({ aal: "aal2", amr: "x" }),
    jwt({ aal: "aal2" }),
    "kein.jwt",
    "",
  ]) {
    assert.equal(aal2Zeitpunkt(kaputt), null);
  }
});

test("frischer zweiter Faktor: höchstens zwei Minuten alt, kaum aus der Zukunft", () => {
  const jetzt = 1_000_000_000;
  const vor = (s: number) => jwt({ aal: "aal2", amr: [{ method: "totp", timestamp: (jetzt - s * 1000) / 1000 }] });
  assert.equal(aal2Frisch(vor(10), jetzt), true);
  assert.equal(aal2Frisch(vor(120), jetzt), true);
  assert.equal(aal2Frisch(vor(121), jetzt), false);
  assert.equal(aal2Frisch(vor(-3), jetzt), true);
  assert.equal(aal2Frisch(vor(-30), jetzt), false);
  assert.equal(aal2Frisch(null, jetzt), false);
  assert.equal(aal2Frisch(jwt({ aal: "aal1" }), jetzt), false);
});
