// Tests für den Verlauf der Einstellungsseite (lib/einstellungenVerlauf.ts): wohin "Zurück" und "Zum Gespräch" führen.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { hashFuer, parseHash, type AbschnittId } from "./einstellungenAbschnitte.ts";
import {
  basisNachErsetzen,
  nachRueckschrittNochOffen,
  schliessenSchritt,
  tiefe,
  waehlenSchritt,
  zurListeSchritt,
} from "./einstellungenVerlauf.ts";

test("Tiefe der Adresse: zweispaltig 1, am Handy 2 im Abschnitt", () => {
  assert.equal(tiefe(parseHash(""), true), 0);
  assert.equal(tiefe(parseHash("#einstellungen"), true), 1);
  assert.equal(tiefe(parseHash("#einstellungen/konto"), true), 1);
  assert.equal(tiefe(parseHash("#einstellungen"), false), 1);
  assert.equal(tiefe(parseHash("#einstellungen/konto"), false), 2);
  assert.equal(tiefe(parseHash("#einstellungen/unbekannt"), false), 1);
});

test("schließen: über der Basis zurückgehen, sonst ersetzen", () => {
  assert.deepEqual(schliessenSchritt(1, 0), { art: "zurueck", schritte: 1 });
  assert.deepEqual(schliessenSchritt(2, 0), { art: "zurueck", schritte: 2 });
  assert.deepEqual(schliessenSchritt(2, 1), { art: "zurueck", schritte: 1 }); // Link auf die Liste, dann Abschnitt
  assert.deepEqual(schliessenSchritt(1, 1), { art: "ersetzen" }); // Link oder Neuladen: kein eigener Eintrag
  assert.deepEqual(schliessenSchritt(2, 2), { art: "ersetzen" });
  assert.equal(nachRueckschrittNochOffen(0), false);
  assert.equal(nachRueckschrittNochOffen(1), true); // zurück bis zum Eintrag, bei dem die Seite begann: der bleibt offen
});

test("zur Liste: ein Schritt zurück nur, wenn die Liste davor von der Seite stammt", () => {
  assert.deepEqual(zurListeSchritt(2, 0), { art: "zurueck", schritte: 1 });
  assert.deepEqual(zurListeSchritt(2, 1), { art: "zurueck", schritte: 1 });
  assert.deepEqual(zurListeSchritt(2, 2), { art: "ersetzen" }); // Link direkt auf einen Abschnitt
  assert.deepEqual(zurListeSchritt(1, 0), { art: "ersetzen" });
});

test("Basis folgt, wenn der Eintrag der Basis ersetzt wird", () => {
  assert.equal(basisNachErsetzen(2, 2, 1), 1); // Link auf einen Abschnitt, dann zurück zur Liste
  assert.equal(basisNachErsetzen(0, 2, 1), 0); // die Seite hat den Abschnitt selbst angelegt: Basis bleibt das Gespräch
  assert.equal(basisNachErsetzen(1, 1, 1), 1);
  assert.equal(basisNachErsetzen(null, 2, 1), null);
});

test("Abschnitt wählen: derselbe Hash nichts, am Handy aus der Liste neu, sonst ersetzen", () => {
  const ziel = hashFuer("konto");
  assert.equal(waehlenSchritt({ zweispaltig: false, aktuell: ziel, ziel, vonListe: false }), "nichts");
  assert.equal(waehlenSchritt({ zweispaltig: false, aktuell: "#einstellungen", ziel, vonListe: true }), "neu");
  assert.equal(waehlenSchritt({ zweispaltig: false, aktuell: hashFuer("info"), ziel, vonListe: false }), "ersetzen");
  assert.equal(waehlenSchritt({ zweispaltig: true, aktuell: "#einstellungen", ziel, vonListe: true }), "ersetzen");
});

/** Kleiner Browserverlauf, der nur tut, was pushState, replaceState und go tun; hält die Entscheidungen zusammen. */
class Verlauf {
  eintraege: string[] = [""];
  index = 0;
  basis: number | null = null;
  get hash() {
    return this.eintraege[this.index];
  }
  push(h: string) {
    this.eintraege = [...this.eintraege.slice(0, this.index + 1), h];
    this.index++;
  }
  ersetzen(h: string) {
    this.eintraege[this.index] = h;
  }
  go(n: number) {
    this.index += n;
  }
}

/** Die Abläufe von lib/einstellungenHash.ts auf dem kleinen Verlauf, ohne Browser. */
function ablauf(zweispaltig: boolean, start = "") {
  const v = new Verlauf();
  v.eintraege = ["/", start];
  v.index = start === "" ? 0 : 1;
  v.eintraege = start === "" ? [""] : ["", start]; // Gespräch, dann ggf. Direkteinstieg per Link
  v.basis = start === "" ? null : tiefe(parseHash(start), zweispaltig);
  const t = () => tiefe(parseHash(v.hash), zweispaltig);
  return {
    v,
    oeffnen(abschnitt: AbschnittId | null = null) {
      if (parseHash(v.hash).offen) return;
      v.basis = 0;
      v.push(hashFuer(abschnitt));
    },
    waehlen(id: AbschnittId) {
      const art = waehlenSchritt({ zweispaltig, aktuell: v.hash, ziel: hashFuer(id), vonListe: parseHash(v.hash).abschnitt === null });
      if (art === "neu") v.push(hashFuer(id));
      else if (art === "ersetzen") v.ersetzen(hashFuer(id));
    },
    zurListe() {
      const s = zurListeSchritt(t(), v.basis ?? 0);
      if (s.art === "zurueck") v.go(-s.schritte);
      else {
        v.basis = basisNachErsetzen(v.basis, t(), 1);
        v.ersetzen(hashFuer(null));
      }
    },
    schliessen() {
      const s = schliessenSchritt(t(), v.basis ?? t());
      if (s.art === "zurueck") {
        const nochOffen = nachRueckschrittNochOffen(v.basis ?? 0);
        v.go(-s.schritte);
        if (nochOffen && parseHash(v.hash).offen) v.ersetzen("");
      } else {
        v.ersetzen("");
      }
      v.basis = null;
    },
  };
}

test("zweispaltig: öffnen, Abschnitte wechseln, schließen hinterlässt keinen Eintrag", () => {
  const a = ablauf(true);
  a.oeffnen();
  a.waehlen("modellzugang");
  a.waehlen("info");
  assert.equal(a.v.eintraege.length, 2);
  a.schliessen();
  assert.equal(a.v.hash, "");
  assert.equal(a.v.index, 0);
  assert.deepEqual(a.v.eintraege.slice(0, a.v.index + 1), [""]);
});

test("am Handy: Liste, Abschnitt, zurück zur Liste, schließen", () => {
  const a = ablauf(false);
  a.oeffnen();
  a.waehlen("verbrauch");
  assert.equal(a.v.hash, "#einstellungen/verbrauch");
  a.zurListe();
  assert.equal(a.v.hash, "#einstellungen");
  a.waehlen("konto");
  a.schliessen();
  assert.equal(a.v.hash, "");
  assert.equal(a.v.index, 0);
});

test("Direkteinstieg per Link auf einen Abschnitt: Zurück zur Liste ersetzt, Schließen ersetzt", () => {
  const a = ablauf(false, "#einstellungen/modellzugang");
  a.zurListe();
  assert.equal(a.v.hash, "#einstellungen");
  assert.equal(a.v.index, 1);
  a.waehlen("info"); // Liste, dann Abschnitt: ein neuer Eintrag, der Eintrag der Liste bleibt darunter
  assert.equal(a.v.index, 2);
  a.schliessen();
  // zurück bis zum Eintrag, bei dem die Seite begann (jetzt die Liste), dort ersetzt: das Gespräch bleibt darunter
  assert.equal(a.v.hash, "");
  assert.equal(a.v.index, 1);
  assert.equal(a.v.eintraege[0], "");
});

test("Direkteinstieg zweispaltig: Schließen ersetzt den Eintrag, nichts wird zurückgegangen", () => {
  const a = ablauf(true, "#einstellungen/info");
  a.schliessen();
  assert.equal(a.v.hash, "");
  assert.equal(a.v.index, 1);
});

test("erneutes Öffnen bei offener Seite legt keinen weiteren Eintrag an", () => {
  const a = ablauf(true);
  a.oeffnen();
  const n = a.v.eintraege.length;
  a.oeffnen();
  a.oeffnen();
  assert.equal(a.v.eintraege.length, n);
});
