// Tests für scripts/wamo-last-browser.cjs (Browser-Worker des Lasttests).
// Aufruf:  node --test scripts/wamo-last-browser.test.cjs
// Kein Chrome, kein Playwright, kein Netz: Playwright ist eine Attrappe, die jeden Aufruf aufzeichnet. Das Seitenskript (seite) läuft in einem vm-Kontext mit
// Attrappen für AudioContext und DOM; Zeiten sind echt (die Schritte warten bis zu einigen Sekunden), das Verhalten im echten Chrome prüft nur ein Lauf.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const w = require("./wamo-last-browser.cjs");

// ---------- Attrappe für Playwright ----------
function playwright(opts = {}) {
  const log = [];
  const zaehler = opts.zaehler || {};
  const loc = (name) => ({
    first() { return this; },
    async fill(v) { log.push(["fill", name, v]); if (opts.fillFehler) throw new Error(`Timeout beim Füllen von ${name} mit ${v}`); },
    async press(k) { log.push(["press", name, k]); },
    async click(o) { log.push(["click", name, o]); },
    async waitFor(o) { log.push(["waitFor", name, o]); if (opts.warteFehler === name) throw new Error("Zeitüberschreitung bei " + name); },
    async count() { return zaehler[name] || 0; },
  });
  const seiteObj = {
    handlers: {},
    on(ev, fn) { this.handlers[ev] = fn; },
    async goto(url, o) { log.push(["goto", url, o]); },
    async evaluate(fn, arg) { log.push(["evaluate", String(fn).slice(0, 50), arg]); return opts.evaluate ? opts.evaluate(fn, arg) : undefined; },
    locator: (s) => loc(s),
    getByRole: (r, o) => loc(`${r}:${o.name}`),
  };
  const kontext = {
    routen: [],
    async addInitScript(fn) { log.push(["addInitScript", fn]); },
    async route(muster, fn) { log.push(["route", muster]); this.routen.push(fn); },
    async newPage() { return seiteObj; },
  };
  const browser = {
    async newContext(o) { log.push(["newContext", o]); return kontext; },
    async close() { log.push(["close"]); },
  };
  const pw = { chromium: { async launch(o) { log.push(["launch", o]); return browser; } } };
  return { pw, log, seiteObj, kontext };
}

function klone(dir) {
  const ordner = fs.mkdtempSync(path.join(os.tmpdir(), "wamo-last-test-"));
  fs.writeFileSync(path.join(ordner, "a1.wav"), Buffer.from("RIFFxxxx"));
  fs.writeFileSync(path.join(ordner, "notiz.txt"), "kein wav");
  return ordner;
}

// ---------- Adressen ----------
test("nur die feste Adresse der Oberfläche ist erlaubt", () => {
  assert.ok(w.urlErlaubt("https://192.168.178.136:55445"));
  assert.ok(w.urlErlaubt("https://192.168.178.136:55445/"));
  for (const u of ["http://192.168.178.136:55445", "https://192.168.178.136:55444", "https://example.org:55445", "https://192.168.178.136:55445/x",
    "https://192.168.178.136:55445/?a=1", "https://u:p@192.168.178.136:55445", "https://127.0.0.1:55445", "", "kein url", null, undefined])
    assert.equal(w.urlErlaubt(u), false, String(u));
});

test("Anfragen der Seite gehen nur an die vier Herkünfte", () => {
  for (const p of [55443, 55444, 55445, 55447]) assert.ok(w.herkunftErlaubt(`https://192.168.178.136:${p}/x`), String(p));
  assert.ok(w.herkunftErlaubt("data:text/plain,hallo") && w.herkunftErlaubt("blob:https://192.168.178.136:55445/abc"));
  for (const u of ["https://example.org/", "http://192.168.178.136:55445/", "https://192.168.178.136:9/", "https://stun.l.google.com:19302/", "ws://x/"])
    assert.equal(w.herkunftErlaubt(u), false, u);
});

test("Adresse und Ports stimmen mit scripts/wamo_labor_platz_adressen.py überein", () => {
  const py = fs.readFileSync(path.join(__dirname, "wamo_labor_platz_adressen.py"), "utf8");
  assert.ok(py.includes(`HOST = "${w.HOST}"`));
  const ports = /PORT_ANMELDUNG, PORT_KERN, PORT_OBERFLAECHE, PORT_LABOR = ([\d, ]+)/.exec(py)[1].split(",").map((s) => Number(s.trim()));
  assert.deepEqual(ports, w.PORTS);
});

// ---------- Sitzung ----------
test("start startet Chrome mit Prüfung des Zertifikats, blockiert fremde Adressen und lädt die Clips", async () => {
  const { pw, log, kontext } = playwright();
  const s = new w.Sitzung(pw);
  const clips = klone();
  await s.start({ url: w.WAMO_URL, clips_ordner: clips, kopflos: false });
  const start = log.find((l) => l[0] === "launch")[1];
  assert.equal(start.channel, "chrome");
  assert.equal(start.headless, false);
  assert.ok(start.args.includes("--use-fake-ui-for-media-stream") && start.args.includes("--use-fake-device-for-media-stream"));
  const ctxOpts = log.find((l) => l[0] === "newContext")[1];
  assert.equal(ctxOpts.ignoreHTTPSErrors, undefined, "Zertifikat wird geprüft");
  assert.deepEqual(ctxOpts.permissions, ["microphone"]);
  assert.equal(log.find((l) => l[0] === "addInitScript")[1], w.seite);
  assert.ok(log.some((l) => l[0] === "goto" && l[1] === w.WAMO_URL));
  const laden = log.filter((l) => l[0] === "evaluate" && Array.isArray(l[2]) && l[2][0] === "a1");
  assert.equal(laden.length, 1, "nur .wav-Dateien werden geladen");
  const entscheidungen = [];
  const route = (url) => ({ request: () => ({ url: () => url }), continue: () => { entscheidungen.push("weiter"); }, abort: (g) => { entscheidungen.push("abbruch:" + g); } });
  kontext.routen[0](route("https://192.168.178.136:55447/board"));
  kontext.routen[0](route("https://example.org/track"));
  assert.deepEqual(entscheidungen, ["weiter", "abbruch:blockedbyclient"]);
  assert.equal(s.blockiert, 1);
  fs.rmSync(clips, { recursive: true, force: true });
});

test("start mit fremder Adresse scheitert, bevor ein Browser startet", async () => {
  const { pw, log } = playwright();
  await assert.rejects(new w.Sitzung(pw).start({ url: "https://example.org", clips_ordner: "x" }), /Adresse nicht zulässig/);
  assert.equal(log.filter((l) => l[0] === "launch").length, 0);
});

test("anmelden füllt E-Mail, Passwort und Code und wartet auf den Startknopf", async () => {
  const { pw, log } = playwright();
  const s = new w.Sitzung(pw);
  await s.start({ url: w.WAMO_URL, clips_ordner: klone() });
  await s.anmelden({ email: "labor-01@wamo.test", passwort: "Gehe1m-Passwort", code: "123456" });
  const gefuellt = log.filter((l) => l[0] === "fill").map((l) => l[1]);
  assert.deepEqual(gefuellt, ['input[name="email"]', 'input[name="password"]', 'input[autocomplete="one-time-code"]']);
  assert.ok(log.some((l) => l[0] === "press" && l[2] === "Enter"));
  assert.ok(log.some((l) => l[0] === "waitFor" && l[1] === "button:Gespräch starten"));
});

test("Fehler bei der Anmeldung nennen weder Passwort noch Code noch E-Mail", async () => {
  const { pw } = playwright({ fillFehler: true });
  const s = new w.Sitzung(pw);
  await s.start({ url: w.WAMO_URL, clips_ordner: klone() });
  const d = await w.verarbeite(s, { id: 4, cmd: "anmelden", email: "labor-01@wamo.test", passwort: "Gehe1m-Passwort", code: "123456" });
  assert.equal(d.ok, false);
  assert.equal(d.id, 4);
  for (const g of ["Gehe1m-Passwort", "123456", "labor-01@wamo.test"]) assert.ok(!JSON.stringify(d).includes(g), g);
  assert.match(d.fehler, /Anmeldung fehlgeschlagen/);
});

test("ein nicht eingerichteter zweiter Faktor wird als solcher gemeldet", async () => {
  const { pw } = playwright({ zaehler: { "details.gate-secret": 1 } });
  const s = new w.Sitzung(pw);
  await s.start({ url: w.WAMO_URL, clips_ordner: klone() });
  await assert.rejects(s.anmelden({ email: "labor-01@wamo.test", passwort: "Gehe1m-Passwort", code: "123456" }), /zweite Faktor/);
});

test("schritt gibt die Rohdaten mit den neuen 429-Antworten, Konsolenfehlern und blockierten Anfragen zurück", async () => {
  const { pw, seiteObj } = playwright({ evaluate: (fn, arg) => (arg && arg.clip ? { clip: arg.clip, bot: [[1, 2]], clip_ende: 3 } : undefined) });
  const s = new w.Sitzung(pw);
  await s.start({ url: w.WAMO_URL, clips_ordner: klone() });
  seiteObj.handlers.response({ status: () => 429 });
  seiteObj.handlers.response({ status: () => 200 });
  seiteObj.handlers.console({ type: () => "error", text: () => "etwas" });
  seiteObj.handlers.console({ type: () => "error", text: () => "GL Driver Meldung" });
  const a = await s.schritt({ clip: "a1", einwurf_clip: "", start_frist_s: 45, ende_frist_s: 90, einwurf_ab_s: 3, freigabe: "ablehnen" });
  assert.deepEqual([a.r429, a.konsole, a.blockiert, a.clip], [1, 1, 0, "a1"]);
  const b = await s.schritt({ clip: "a1", einwurf_clip: "", start_frist_s: 45, ende_frist_s: 90, einwurf_ab_s: 3, freigabe: "ablehnen" });
  assert.deepEqual([b.r429, b.konsole], [0, 0], "nur das, was seit dem letzten Schritt neu ist");
});

test("unbekannte Befehle und Fehler werden als Antwort gemeldet, nie als Absturz", async () => {
  const s = new w.Sitzung(playwright().pw);
  assert.deepEqual(await w.verarbeite(s, { id: 1, cmd: "rm -rf" }), { id: 1, ok: false, fehler: "Unbekannter Befehl" });
  assert.equal((await w.verarbeite(s, null)).ok, false);
  assert.equal((await w.verarbeite(s, { id: 2, cmd: "gespraech_starten" })).ok, false, "ohne start gibt es keine Seite");
});

test("abbrechen setzt den Schalter in der Seite, schliessen schliesst den Browser", async () => {
  const { pw, log } = playwright();
  const s = new w.Sitzung(pw);
  await s.start({ url: w.WAMO_URL, clips_ordner: klone() });
  assert.deepEqual(await s.abbrechen(), { abgebrochen: true });
  assert.ok(log.some((l) => l[0] === "evaluate" && l[1].includes("__abbrechen")));
  await s.schliessen();
  await s.schliessen();
  assert.equal(log.filter((l) => l[0] === "close").length, 1);
});

test("kurz schwärzt Geheimnisse und kürzt auf die erste Zeile", () => {
  assert.equal(w.kurz(new Error("Zeile eins mit Geheim123\nZeile zwei"), ["Geheim123"]), "Zeile eins mit ***");
  assert.equal(w.kurz("x".repeat(500), []).length, 200);
});

// ---------- Seitenskript in einem vm-Kontext ----------
function seiteBauen({ online = true, karte = null } = {}) {
  const t0 = performance.now();
  class Knoten { constructor() { this.gain = { value: 1 }; } connect(x) { return x; } disconnect() {} start() {} stop() {} }
  class AC {
    constructor() { this.sampleRate = 48000; this.destination = new Knoten(); this.audioWorklet = { addModule: async () => {} }; }
    get currentTime() { return (performance.now() - t0) / 1000; }
    async resume() {}
    createGain() { return new Knoten(); }
    createBuffer(c, n, sr) { return { getChannelData: () => new Float32Array(n), duration: n / sr }; }
    createBufferSource() { return new Knoten(); }
    createMediaStreamDestination() { const k = new Knoten(); k.stream = {}; return k; }
    createMediaStreamSource() { return new Knoten(); }
    async decodeAudioData() { return { duration: 1.0 }; }
  }
  const arbeiter = [];
  class WN extends Knoten { constructor() { super(); this.port = {}; arbeiter.push(this); } }
  const knoepfe = online ? [{ textContent: "Gespräch beenden" }] : [{ textContent: "Gespräch starten" }];
  const dokument = {
    documentElement: {},
    querySelectorAll: (s) => (s === "button" ? knoepfe : []),
    querySelector: (s) => (s === "article.approval" ? karte : null),
  };
  const fenster = { RTCPeerConnection: function RTC() {} };
  fenster.RTCPeerConnection.prototype = { addEventListener() {} };
  const ctx = vm.createContext({
    window: fenster, document: dokument, navigator: { mediaDevices: { getUserMedia: async () => ({}) } }, performance, AudioContext: AC,
    AudioWorkletNode: WN, MediaStream: class {}, MutationObserver: class { observe() {} }, URL, Blob, setTimeout, atob, Uint8Array, Float32Array, console, Math,
  });
  vm.runInContext(`(${w.seite.toString()})()`, ctx);
  const sprechen = (von, dauerS) => new Promise((ok) => {
    const t1 = performance.now() + dauerS * 1000;
    const takt = setInterval(() => {
      const start = fenster.__s.ac.currentTime * 48000;
      arbeiter[0].port.onmessage({ data: { start, n: 2048, rms: 0.05, sr: 48000 } });
      if (performance.now() >= t1) { clearInterval(takt); ok(); }
    }, 40);
  });
  return { fenster, knoepfe, sprechen, arbeiter };
}
const optionen = (o = {}) => ({ clip: "fakt_1", einwurfClip: "", startFristS: 5, endeFristS: 10, einwurfAbS: 3, freigabe: "keine", ...o });

test("Seitenskript: ohne laufendes Gespräch endet ein Schritt sofort mit Abbruch", async () => {
  const { fenster } = seiteBauen({ online: false });
  const r = await fenster.__schritt(optionen());
  assert.equal(r.abbruch, "Gespräch nicht aktiv");
  assert.equal(r.bot.length, 0);
});

test("Seitenskript: ohne Antwort gibt es eine Zeitüberschreitung, kein Abbruch", async () => {
  const { fenster } = seiteBauen();
  await fenster.__start();
  await fenster.__lade("fakt_1", "AAAA");
  const r = await fenster.__schritt(optionen({ startFristS: 0.4 }));
  assert.equal(r.zeitueberschreitung, true);
  assert.equal(r.abbruch, "");
  assert.ok(r.clip_ende > r.clip_t0);
});

test("Seitenskript: abbrechen beendet den Schritt sofort", async () => {
  const { fenster } = seiteBauen();
  await fenster.__start();
  await fenster.__lade("fakt_1", "AAAA");
  const t0 = performance.now();
  const lauf = fenster.__schritt(optionen({ startFristS: 30 }));
  setTimeout(() => fenster.__abbrechen(), 300);
  const r = await lauf;
  assert.equal(r.abbruch, "abgebrochen");
  assert.ok(performance.now() - t0 < 3000);
});

test("Seitenskript: Antwort wird aufgezeichnet, eine Karte wird abgelehnt", async () => {
  let geklickt = false;
  const karte = { querySelectorAll: () => [{ textContent: "Freigeben", click() { throw new Error("falscher Knopf"); } }, { textContent: "Ablehnen", click() { geklickt = true; } }] };
  const { fenster, sprechen } = seiteBauen({ karte });
  await fenster.__start();
  await fenster.__lade("fakt_1", "AAAA");
  const lauf = fenster.__schritt(optionen({ freigabe: "ablehnen" }));
  setTimeout(() => sprechen(0, 1.2), 1400); // WAMO beginnt nach dem Ende der Sprechprobe (rund 1,03 s)
  const r = await lauf;
  assert.equal(r.abbruch, "");
  assert.equal(r.zeitueberschreitung, false);
  assert.ok(r.bot.length > 10 && r.bot.every((b) => b[1] > b[0] && b[0] >= r.clip_ende - 0.2));
  assert.ok(r.ende_t > r.bot[r.bot.length - 1][1] + 2.9);
  assert.equal(r.freigabe, "abgelehnt");
  assert.equal(geklickt, true);
});

test("Seitenskript: der Einwurf wird gespielt, sobald WAMO lange genug spricht", async () => {
  const { fenster, sprechen } = seiteBauen({ karte: { querySelectorAll: () => [] } });
  await fenster.__start();
  await fenster.__lade("fakt_1", "AAAA");
  await fenster.__lade("einwurf_a", "AAAA");
  const lauf = fenster.__schritt(optionen({ einwurfClip: "einwurf_a", einwurfAbS: 0.8, freigabe: "keine" }));
  setTimeout(() => sprechen(0, 2.2), 1300);
  const r = await lauf;
  assert.ok(r.einwurf, "Einwurf gespielt");
  assert.equal(r.einwurf.clip, "einwurf_a");
  assert.ok(r.einwurf.bot_seit >= 0.8);
  assert.equal(r.freigabe, "offen", "ohne Wunsch bleibt eine Karte liegen");
});

test("Seitenskript: murmeln startet und stoppt ohne Fehler", async () => {
  const { fenster } = seiteBauen();
  await fenster.__start();
  assert.equal(await fenster.__murmeln("AAAA", 0.3), true);
  assert.equal(await fenster.__murmeln(null, 0), false);
});
