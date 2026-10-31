// Tests für die Texteingabe im Gespräch (lib/textEingabe.ts, nimmeTextAuf in lib/wamo.ts).
// Schwerpunkt: Ein getippter Schlüssel (sk-ant-…) gelangt nie in den Kern, in die Anzeige, in eine Meldung oder ins
// Protokoll, das Feld wird geleert, und getippter Text ersetzt nie eine Freigabe.
// Ausführen im Ordner ui/:  npm test   (node:test über tsx, ohne Browser)
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import {
  bereiteText,
  eingabeHinweis,
  MAX_TEXT,
  schluesselEntfernen,
  SCHLUESSEL_MARKE,
  textAbschicken,
  unterstuetztText,
  type TextWege,
} from "./textEingabe.ts";
import { beginneNutzerZug, initialState, leereCaption, nimmeTextAuf, type WamoState } from "./wamo.ts";

const API = "sk-ant-api03-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99";
const TOKEN = "sk-ant-oat01-GEHEIMgeheim-0123456789abcdefghijklmnop-Zz99";
const GEHEIM = ["GEHEIM", "0123456789", "Zz99", "sk-ant"];

const ohneGeheimnis = (text: string) => !GEHEIM.some((g) => text.toLowerCase().includes(g.toLowerCase()));

const SAETZE_MIT_SCHLUESSEL = [
  `Mein Schlüssel ist ${API} und jetzt?`,
  `Der Token lautet ${TOKEN}.`,
  TOKEN,
  "mein schlüssel ist sk ant api null drei A B C D E F G H I J K",
  "S K Strich A N T Strich O A T null eins A B C D E F G H",
  "sk-ant-oat01-abc def ghi jkl mno",
  "SK-ANT-API03-Abcd1234 Efgh5678",
];

const NORMALE_SAETZE = [
  "Wo trage ich meinen Schlüssel ein?",
  "Die Antwort auf die Frage ist schnell da.",
  "Ich skizziere kurz den Ablauf, dann fangen wir an.",
  "sk ist die Abkürzung, ant steht für Ameise",
  "Mein Anthropic-Schlüssel beginnt mit sk, den sage ich nicht.",
];

// ---------- Schlüsselfilter (gleiche Fälle wie voice-core/test_schluessel_filter.py) ----------

for (const satz of SAETZE_MIT_SCHLUESSEL) {
  test(`Schlüssel wird entfernt: ${satz.slice(0, 32)}`, () => {
    const { text, entfernt } = schluesselEntfernen(satz);
    assert.equal(entfernt, true);
    assert.ok(text.includes(SCHLUESSEL_MARKE));
    assert.ok(ohneGeheimnis(text), text);
  });
}

for (const satz of NORMALE_SAETZE) {
  test(`normaler Satz bleibt unverändert: ${satz.slice(0, 32)}`, () => {
    assert.deepEqual(schluesselEntfernen(satz), { text: satz, entfernt: false });
  });
}

test("der Satz um den Schlüssel bleibt erhalten", () => {
  assert.equal(schluesselEntfernen(`Hier: ${TOKEN}`).text, `Hier: ${SCHLUESSEL_MARKE}`);
});

test("auch ein sehr langer Satz wird zügig geprüft (keine Rückverfolgungsfalle)", () => {
  const start = performance.now();
  const lang = `sk ant api ${"a".repeat(MAX_TEXT * 3)}`;
  schluesselEntfernen(lang);
  schluesselEntfernen("s k a n t ".repeat(400));
  assert.ok(performance.now() - start < 500);
});

// ---------- Vorbereitung ----------

test("leere und leere-nach-Trimmen Eingaben ergeben nichts", () => {
  assert.equal(bereiteText(""), null);
  assert.equal(bereiteText("   \t\n "), null);
});

test("Steuerzeichen und Umbrüche werden zu Leerzeichen, der Rand wird getrimmt", () => {
  assert.deepEqual(bereiteText("  Hallo\nWelt\u0000! "), { text: "Hallo Welt !", geschwaerzt: false, senden: true });
});

test("die Nachricht ist höchstens MAX_TEXT Zeichen lang", () => {
  const t = bereiteText("x".repeat(MAX_TEXT * 3));
  assert.equal(t?.text.length, MAX_TEXT);
});

test("ein Schlüssel mitten im Text wird ersetzt, der Rest wird gesendet", () => {
  const t = bereiteText(`Merk dir bitte ${API} für später`);
  assert.deepEqual(t, { text: `Merk dir bitte ${SCHLUESSEL_MARKE} für später`, geschwaerzt: true, senden: true });
});

test("besteht die Nachricht nur aus einem Schlüssel, wird nichts gesendet", () => {
  assert.equal(bereiteText(TOKEN)?.senden, false);
  assert.equal(bereiteText(`  ${API}. `)?.senden, false);
});

// ---------- Ablauf von Eingabe bis Anzeige ----------

type Aufruf = { was: string; text: string };

/** Attrappe des Kerns und der Oberfläche: schreibt jeden Text mit, der irgendwo ankommt. */
function wege(opt: Partial<TextWege> = {}) {
  const aufrufe: Aufruf[] = [];
  const besetzt = { wert: false };
  let aufloesen: () => void = () => undefined;
  let ablehnen: (e: unknown) => void = () => undefined;
  const w: TextWege = {
    bereit: () => "ok",
    besetzt: () => besetzt.wert,
    setzeBesetzt: (b) => {
      besetzt.wert = b;
    },
    senden: (text) => {
      aufrufe.push({ was: "senden", text });
      return new Promise<void>((res, rej) => {
        aufloesen = res;
        ablehnen = rej;
      });
    },
    hinweis: (text) => aufrufe.push({ was: "hinweis", text }),
    fehler: (_art, text) => aufrufe.push({ was: "fehler", text }),
    angenommen: (text) => aufrufe.push({ was: "angenommen", text }),
    aktuell: () => true,
    ...opt,
  };
  return { w, aufrufe, besetzt, ok: () => aufloesen(), fehl: () => ablehnen(new Error("zu")) };
}

const warte = () => new Promise((r) => setImmediate(r));

let konsole: ReturnType<typeof mock.method>[] = [];
beforeEach(() => {
  konsole = (["log", "info", "warn", "error", "debug"] as const).map((n) => mock.method(console, n, () => undefined));
});
afterEach(() => mock.restoreAll());

test("ein getippter Schlüssel erreicht weder Kern noch Anzeige noch Meldung noch Konsole, das Feld wird geleert", async () => {
  const { w, aufrufe, ok } = wege();
  assert.equal(textAbschicken(`Merk dir ${API} bitte`, w), true, "true: das Feld wird geleert");
  ok();
  await warte();
  assert.deepEqual(
    aufrufe.map((a) => a.was),
    ["hinweis", "senden", "angenommen"],
  );
  for (const a of aufrufe) assert.ok(ohneGeheimnis(a.text), `${a.was}: ${a.text}`);
  assert.equal(aufrufe.find((a) => a.was === "senden")?.text, `Merk dir ${SCHLUESSEL_MARKE} bitte`);
  // Wert nie geloggt: keine Konsolenausgabe, in der etwas vom Text steht
  for (const m of konsole) assert.equal(m.mock.callCount(), 0);
});

test("ein Schlüssel in jeder Schreibweise geht nie an den Kern", async () => {
  for (const satz of SAETZE_MIT_SCHLUESSEL) {
    const { w, aufrufe, ok } = wege();
    textAbschicken(`Hallo ${satz}`, w);
    ok();
    await warte();
    for (const a of aufrufe) assert.ok(ohneGeheimnis(a.text), `${a.was}: ${a.text}`);
  }
});

test("nur ein Schlüssel: nichts wird gesendet, die Eingabe gilt trotzdem als angenommen", () => {
  const { w, aufrufe } = wege();
  assert.equal(textAbschicken(TOKEN, w), true);
  assert.deepEqual(
    aufrufe.map((a) => a.was),
    ["hinweis"],
  );
  assert.ok(ohneGeheimnis(aufrufe[0].text));
});

test("ohne Verbindung wird nichts gesendet und der Text bleibt im Feld", () => {
  const { w, aufrufe } = wege({ bereit: () => "offline" });
  assert.equal(textAbschicken("Hallo", w), false);
  assert.deepEqual(
    aufrufe.map((a) => a.was),
    ["fehler"],
  );
});

test("ein Kern ohne Textannahme sperrt ehrlich: Meldung, nichts gesendet", () => {
  const { w, aufrufe } = wege({ bereit: () => "kein_text" });
  assert.equal(textAbschicken("Hallo", w), false);
  assert.match(aufrufe[0].text, /keinen getippten Text/);
});

test("leere Eingabe sendet nichts", () => {
  const { w, aufrufe } = wege();
  assert.equal(textAbschicken("   ", w), false);
  assert.equal(aufrufe.length, 0);
});

test("nie zwei Sendungen zugleich: die zweite bleibt im Feld, danach geht es wieder", async () => {
  const { w, aufrufe, besetzt, ok } = wege();
  assert.equal(textAbschicken("eins", w), true);
  assert.equal(besetzt.wert, true);
  assert.equal(textAbschicken("zwei", w), false);
  assert.equal(aufrufe.filter((a) => a.was === "senden").length, 1);
  ok();
  await warte();
  assert.equal(besetzt.wert, false);
  assert.equal(textAbschicken("zwei", w), true);
});

test("schlägt das Senden fehl, kommt eine Meldung und die Sperre wird gelöst", async () => {
  const { w, aufrufe, besetzt, fehl } = wege();
  textAbschicken("Hallo", w);
  fehl();
  await warte();
  assert.deepEqual(
    aufrufe.map((a) => a.was),
    ["senden", "fehler"],
  );
  assert.equal(besetzt.wert, false);
});

test("nach einem Trennen wird die späte Antwort nicht mehr in die Anzeige übernommen", async () => {
  const lebt = { wert: true };
  const { w, aufrufe, ok } = wege({ aktuell: () => lebt.wert });
  textAbschicken("Hallo", w);
  lebt.wert = false;
  ok();
  await warte();
  assert.deepEqual(
    aufrufe.map((a) => a.was),
    ["senden"],
  );
});

// ---------- Anzeige und Freigaben ----------

const freigabe = { job: "pc", projekt: "", id: "a1", grund: "Datei löschen", detail: "x" };
const auftrag = { job: "j1", projekt: "atlas", art: "Umsetzung", auftrag: "Alles neu" };

function mitKarten(): WamoState {
  return {
    ...initialState,
    presence: "idle",
    textMoeglich: true,
    approvals: [freigabe],
    auftraege: [auftrag],
    caption: { ...leereCaption, text: "Ich lösche jetzt", spoken: 2, turnEnd: true },
  };
}

test("getippter Text ersetzt nie eine Freigabe oder einen Auftrag", () => {
  for (const wort of ["ja", "Ja, freigeben", "ok", "starten"]) {
    const vorher = mitKarten();
    const nachher = nimmeTextAuf(vorher, wort);
    assert.deepEqual(nachher.approvals, vorher.approvals);
    assert.deepEqual(nachher.auftraege, vorher.auftraege);
    assert.equal(nachher.ansage, vorher.ansage, "keine Ansage wie Freigegeben");
  }
});

test("der Text erscheint als Nutzerzeile der Untertitel, die Anzeige enthält nie einen Schlüssel", () => {
  const roh = `Hier ${API}`;
  const t = bereiteText(roh);
  assert.ok(t);
  const nachher = nimmeTextAuf(mitKarten(), t.text);
  assert.equal(nachher.caption.userDone, `Hier ${SCHLUESSEL_MARKE}`);
  assert.equal(nachher.presence, "thinking");
  assert.ok(ohneGeheimnis(JSON.stringify(nachher)));
});

test("ein neuer Beitrag beendet den alten Zug; spricht WAMO, gilt der Rest als unterbrochen", () => {
  const spricht: WamoState = { ...mitKarten(), presence: "speaking", caption: { ...leereCaption, text: "Ich lösche", spoken: 1 } };
  const n = beginneNutzerZug(spricht);
  assert.equal(n.caption.interrupted, true);
  assert.equal(n.turn, spricht.turn + 1);
  const fertig = beginneNutzerZug(mitKarten());
  assert.equal(fertig.caption.text, "");
  assert.equal(fertig.caption.turnEnd, false);
});

test("ein schon in den Verlauf übernommener Nutzerbeitrag beginnt neu", () => {
  const s: WamoState = { ...mitKarten(), caption: { ...leereCaption, userDone: "alt", userLogged: true } };
  assert.equal(beginneNutzerZug(s).caption.userDone, "");
});

// ---------- Kernunterstützung und Hinweis ----------

test("Text nimmt nur ein Kern mit Protokoll 2 oder neuer an", () => {
  assert.equal(unterstuetztText("2.1.0"), true);
  assert.equal(unterstuetztText("3.0"), true);
  assert.equal(unterstuetztText("1.4.2"), false);
  assert.equal(unterstuetztText(""), false);
  assert.equal(unterstuetztText(undefined), false);
  assert.equal(unterstuetztText({ version: "2.1.0" }), false);
  assert.equal(unterstuetztText("zwei"), false);
});

test("der Hinweis unter der Eingabe nennt zuerst die Freigabe, dann die laufende Arbeit", () => {
  assert.equal(eingabeHinweis({ entscheidungen: 0, werkzeugLaeuft: false }), null);
  assert.match(eingabeHinweis({ entscheidungen: 1, werkzeugLaeuft: true }) ?? "", /Freigaben gibst du nur mit Klick/);
  assert.match(eingabeHinweis({ entscheidungen: 0, werkzeugLaeuft: true }) ?? "", /unterbricht die Arbeit/);
});
