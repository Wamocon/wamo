// Browser-Worker des Lasttests (scripts/wamo_labor_last.py): eine Chrome-Instanz mit virtuellem Mikrofon gegen die Oberfläche von WAMO auf Sokrates-2.
// Abgeleitet von scripts/wamo-stimme-messen.cjs (dort der erprobte Weg mit Browser, virtuellem Mikrofon und Aufnahme der Wiedergabe über eine gemeinsame
// Audio-Uhr). Unterschiede: kein lokaler Kern, nur die feste Adresse des Servers, nur Rohdaten statt Auswertung (die Kennzahlen entstehen in
// scripts/wamo_labor_last_messung.py), Anmeldung mit Zugangsdaten, die Python über die Pipe schickt, und ein Befehl "abbrechen", der laufende Wartezeiten beendet.
//
// Protokoll: je eine JSON-Zeile auf stdin (Anfrage) und stdout (Antwort), siehe scripts/wamo_labor_last_browser.py. Auf stdout steht nie etwas anderes,
// Meldungen gehen nach stderr und nie mit Zugangsdaten. Passwort und Code stehen nur im Speicher, in keiner Datei, keinem Argument und keiner Ausgabe.
//
// Umgebung: PLAYWRIGHT_MODUL  Pfad oder Name von playwright(-core) (wie beim Messskript); der Browser ist das installierte Chrome.
//
// Begründete Ausnahme zur Funktionslänge: seite() ist eine einzige lange Funktion, weil Playwright sie als Ganzes in die Seite schickt (addInitScript serialisiert
// sie, sie darf nichts aus Node verwenden). Ihre inneren Funktionen sind kurz. Die Datei ist unter 800 Zeilen.
"use strict";
const fs = require("fs");
const path = require("path");
const readline = require("readline");

// Die feste Adresse der Oberfläche und die vier Herkünfte auf Sokrates-2 (scripts/wamo_labor_platz_adressen.py, ein Test hält beide zusammen)
const HOST = "192.168.178.136";
const PORT_OBERFLAECHE = 55445;
const PORTS = [55443, 55444, 55445, 55447];
const WAMO_URL = `https://${HOST}:${PORT_OBERFLAECHE}`;

function urlErlaubt(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && u.hostname === HOST && Number(u.port) === PORT_OBERFLAECHE && !u.username && !u.password && u.pathname === "/" && !u.search && !u.hash;
  } catch {
    return false;
  }
}
// Anfragen der Seite: nur die vier Herkünfte, nichts sonst (Daten-URLs und Blobs der Seite selbst bleiben erlaubt)
function herkunftErlaubt(url) {
  if (/^(data|blob|about):/i.test(url)) return true;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && u.hostname === HOST && PORTS.includes(Number(u.port));
  } catch {
    return false;
  }
}

function playwrightLaden() {
  const orte = [path.join(__dirname, "..", "ui"), path.join(__dirname, "..", "desktop")]; // nie aus dem Arbeitsordner laden
  for (const name of [process.env.PLAYWRIGHT_MODUL, "playwright", "playwright-core"].filter(Boolean)) {
    try {
      return require(require.resolve(name, { paths: orte }));
    } catch {
      /* nächster Versuch */
    }
  }
  throw new Error("Playwright fehlt: PLAYWRIGHT_MODUL auf den Ordner von playwright-core setzen");
}

// ---------------------------------------------------------------------------------------------------------------------
// Seite: virtuelles Mikrofon, Aufnahme der Wiedergabe, Murmeln und die Schritte. Läuft im Browser (addInitScript), darf nichts aus Node verwenden.
function seite() {
  const S = (window.__s = {
    ac: null, clips: {}, remote: [], micBus: null, dest: null, botNode: null, bot: [], botBegin: -1e9, botLetzt: -1e9,
    murm: null, cut: 0, cutAktiv: false, abbrechen: false, beobachter: null,
  });
  const SCHWELLE = 0.003; // Pegel (RMS je Block), ab dem WAMO als sprechend gilt
  const LUECKE = 0.8; // Pausen bis dahin gehören zur selben Rede
  const jetzt = () => S.ac.currentTime;
  const schlaf = (ms) => new Promise((r) => setTimeout(r, ms));
  async function warteBis(fn, sekunden, schritt = 25) {
    const t0 = performance.now();
    while (performance.now() - t0 < sekunden * 1000) {
      if (S.abbrechen) return false;
      if (fn()) return true;
      await schlaf(schritt);
    }
    return false;
  }
  const botAktiv = () => jetzt() - S.botLetzt < LUECKE;
  const botSeit = () => (botAktiv() ? jetzt() - S.botBegin : 0);
  const botStille = () => jetzt() - S.botLetzt;
  const online = () => [...document.querySelectorAll("button")].some((b) => /^(Gespräch )?beenden$/i.test((b.textContent || "").trim()));

  const WORKLET = `class Rec extends AudioWorkletProcessor {
    constructor() { super(); this.n = 0; this.sum = 0; this.start = 0; }
    process(inputs) {
      const ch = inputs[0] && inputs[0][0];
      const len = ch ? ch.length : 128;
      for (let i = 0; i < len; i++) {
        if (this.n === 0) this.start = currentFrame + i;
        const v = ch ? ch[i] : 0;
        this.sum += v * v;
        this.n++;
        if (this.n === 2048) { this.port.postMessage({ start: this.start, n: this.n, rms: Math.sqrt(this.sum / this.n), sr: sampleRate }); this.n = 0; this.sum = 0; }
      }
      return true;
    }
  }
  registerProcessor("rec", Rec);`;

  async function audioStart() {
    if (S.ac) return;
    const ac = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    await ac.resume();
    S.ac = ac;
    await ac.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));
    const n = new AudioWorkletNode(ac, "rec", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: "explicit" });
    n.port.onmessage = (e) => {
      const { start, n: anzahl, rms, sr } = e.data;
      if (rms > SCHWELLE) {
        const t = start / sr;
        if (t - S.botLetzt > LUECKE) S.botBegin = t;
        S.botLetzt = t + anzahl / sr;
        S.bot.push([t, t + anzahl / sr]);
      }
    };
    const g = ac.createGain();
    g.gain.value = 0;
    n.connect(g);
    g.connect(ac.destination);
    S.botNode = n;
    S.micBus = ac.createGain();
    // Rauschboden (-66 dBFS): wie ein echtes Mikrofon, und der Mikrofon-Wächter der Oberfläche meldet keine digitale Stille
    const nb = ac.createBuffer(1, ac.sampleRate * 2, ac.sampleRate);
    const nd = nb.getChannelData(0);
    for (let i = 0; i < nd.length; i++) nd[i] = (Math.random() * 2 - 1) * 0.0008;
    const nq = ac.createBufferSource();
    nq.buffer = nb; nq.loop = true; nq.connect(S.micBus); nq.start();
    for (const r of S.remote) verbinde(r);
  }
  function verbinde(track) {
    if (track.__v) return;
    track.__v = true;
    S.ac.createMediaStreamSource(new MediaStream([track])).connect(S.botNode);
  }
  const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    if (!c || !c.audio) return gum(c);
    await audioStart();
    if (S.dest) { try { S.micBus.disconnect(S.dest); } catch { /* alt */ } }
    S.dest = S.ac.createMediaStreamDestination();
    S.micBus.connect(S.dest);
    return S.dest.stream;
  };
  const OrigPC = window.RTCPeerConnection;
  window.RTCPeerConnection = function (...a) {
    const pc = new OrigPC(...a);
    pc.addEventListener("track", (e) => {
      if (e.track.kind !== "audio") return;
      S.remote.push(e.track);
      if (S.ac && S.botNode) verbinde(e.track);
    });
    return pc;
  };
  window.RTCPeerConnection.prototype = OrigPC.prototype;

  const dekodieren = async (b64) => {
    const bin = atob(b64);
    const b = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i);
    return S.ac.decodeAudioData(b.buffer);
  };
  // Nach dem Laden der Seite: Audio bereit, Schnitte der Wiedergabe mitzählen (data-cut setzt die Oberfläche, wenn WAMO unterbrochen wird)
  window.__start = async () => {
    await audioStart();
    S.abbrechen = false;
    if (!S.beobachter) {
      const pruefen = () => {
        const da = !!document.querySelector("[data-cut]");
        if (da && !S.cutAktiv) S.cut++;
        S.cutAktiv = da;
      };
      S.beobachter = new MutationObserver(pruefen);
      S.beobachter.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-cut"] });
    }
  };
  window.__lade = async (name, b64) => { await audioStart(); S.clips[name] = await dekodieren(b64); };
  window.__abbrechen = () => { S.abbrechen = true; };
  window.__murmeln = async (b64, gain) => {
    if (S.murm) { try { S.murm.q.stop(); } catch { /* schon gestoppt */ } S.murm = null; }
    if (!b64) return false;
    const q = S.ac.createBufferSource();
    q.buffer = await dekodieren(b64);
    q.loop = true;
    const g = S.ac.createGain();
    g.gain.value = gain;
    q.connect(g); g.connect(S.micBus);
    q.start();
    S.murm = { q, g };
    return true;
  };
  function sprich(name) {
    const buf = S.clips[name];
    if (!buf) throw new Error("Sprechprobe fehlt: " + name);
    const q = S.ac.createBufferSource();
    q.buffer = buf;
    q.connect(S.micBus);
    const t0 = S.ac.currentTime + 0.03;
    q.start(t0);
    return { t0, ende: t0 + buf.duration };
  }
  window.__begruessung = async () => {
    await warteBis(() => botAktiv(), 25);
    await warteBis(() => botStille() >= 2.0, 20);
    return S.botLetzt > 0;
  };
  // Eine Freigabekarte (Ticket anlegen) entscheiden: "ablehnen" oder "freigeben", sonst liegen lassen
  function karteEntscheiden(wunsch) {
    const karte = document.querySelector("article.approval");
    if (!karte) return "";
    const name = wunsch === "freigeben" ? "Freigeben" : wunsch === "ablehnen" ? "Ablehnen" : "";
    const knopf = name && [...karte.querySelectorAll("button")].find((b) => (b.textContent || "").trim() === name);
    if (!knopf) return "offen";
    knopf.click();
    return wunsch === "freigeben" ? "freigegeben" : "abgelehnt";
  }
  async function einwurfSpielen(ergebnis, clip, abS) {
    const ok = await warteBis(() => !online() || !botAktiv() || botSeit() >= abS, 30);
    if (!ok || !online() || !botAktiv() || botSeit() < abS) return;
    const seit = botSeit();
    const e = sprich(clip);
    ergebnis.einwurf = { clip, t0: e.t0, ende: e.ende, bot_seit: seit };
    await warteBis(() => botStille() >= 3.0 || !online(), 25);
  }
  // Ein Schritt: Sprechprobe sprechen, auf den ersten Ton und das Ende der Antwort warten, bei A1 einen Einwurf, eine Karte entscheiden.
  window.__schritt = async ({ clip, einwurfClip, startFristS, endeFristS, einwurfAbS, freigabe }) => {
    const ergebnis = { clip, clip_t0: 0, clip_ende: 0, bot: [], einwurf: null, ende_t: 0, abbruch: "", zeitueberschreitung: false, geschnitten: 0, freigabe: "" };
    const abschluss = () => {
      ergebnis.bot = S.bot.splice(0);
      ergebnis.ende_t = S.ac ? jetzt() : 0;
      ergebnis.geschnitten = S.cut - cut0;
      return ergebnis;
    };
    const cut0 = S.cut;
    S.bot.splice(0);
    if (!online()) { ergebnis.abbruch = "Gespräch nicht aktiv"; return abschluss(); }
    const q = sprich(clip);
    ergebnis.clip_t0 = q.t0;
    ergebnis.clip_ende = q.ende;
    const gestartet = await warteBis(() => !online() || (botAktiv() && S.botBegin > q.ende - 0.1), startFristS + Math.max(0, q.ende - jetzt()));
    if (S.abbrechen) { ergebnis.abbruch = "abgebrochen"; return abschluss(); }
    if (!online()) { ergebnis.abbruch = "Gespräch beendet"; return abschluss(); }
    if (!gestartet) { ergebnis.zeitueberschreitung = true; return abschluss(); }
    if (einwurfClip) await einwurfSpielen(ergebnis, einwurfClip, einwurfAbS);
    await warteBis(() => !online() || botStille() >= 3.0, endeFristS);
    if (!online() && !S.abbrechen) ergebnis.abbruch = "Gespräch beendet";
    ergebnis.freigabe = karteEntscheiden(freigabe);
    return abschluss();
  };
}

// ---------------------------------------------------------------------------------------------------------------------
// Worker: ein Browser, Befehle aus der Pipe
function kurz(text, geheim) {
  let t = String(text && text.message ? text.message : text).split("\n")[0].slice(0, 200);
  for (const g of geheim) if (g && g.length >= 4) t = t.split(g).join("***");
  return t;
}

class Sitzung {
  constructor(playwright) {
    this.playwright = playwright;
    this.browser = null; this.page = null; this.ctx = null;
    this.geheim = []; this.r429 = 0; this.konsole = 0; this.blockiert = 0;
    this.gesendet = { r429: 0, konsole: 0, blockiert: 0 };
  }

  async start({ url, clips_ordner: clips, kopflos }) {
    if (!urlErlaubt(url)) throw new Error("Adresse nicht zulässig");
    this.browser = await this.playwright.chromium.launch({
      channel: "chrome", headless: !!kopflos,
      args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
    });
    // Das Zertifikat wird geprüft (die WAMO-CA muss im Benutzerspeicher stehen): kein ignoreHTTPSErrors
    this.ctx = await this.browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ["microphone"], serviceWorkers: "block" });
    await this.ctx.addInitScript(seite);
    await this.ctx.route("**/*", (route) => {
      if (herkunftErlaubt(route.request().url())) return route.continue();
      this.blockiert++;
      return route.abort("blockedbyclient");
    });
    this.page = await this.ctx.newPage();
    this.page.on("response", (r) => { if (r.status() === 429) this.r429++; });
    this.page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("GL Driver")) this.konsole++; });
    await this.page.goto(url, { waitUntil: "networkidle" });
    await this.page.evaluate(() => window.__start());
    for (const name of fs.readdirSync(clips).filter((f) => f.endsWith(".wav"))) {
      await this.page.evaluate(([n, b]) => window.__lade(n, b), [name.replace(/\.wav$/, ""), fs.readFileSync(path.join(clips, name)).toString("base64")]);
    }
    return {};
  }

  async anmelden({ email, passwort, code }) {
    this.geheim.push(email, passwort, code);
    const p = this.page;
    try {
      await p.locator('input[name="email"]').fill(email);
      await p.locator('input[name="password"]').fill(passwort);
      await p.getByRole("button", { name: "Anmelden" }).click();
      const feld = p.locator('input[autocomplete="one-time-code"]').first();
      await feld.waitFor({ timeout: 20000 });
      if (await p.locator("details.gate-secret").count()) throw new Error("Der zweite Faktor ist für dieses Profil nicht eingerichtet");
      await feld.fill(code);
      await feld.press("Enter");
      await p.getByRole("button", { name: "Gespräch starten" }).waitFor({ timeout: 30000 });
    } catch (e) {
      throw new Error("Anmeldung fehlgeschlagen: " + kurz(e, this.geheim));
    }
    return {};
  }

  async gespraech_starten() {
    const p = this.page;
    await p.getByRole("button", { name: "Gespräch starten" }).click();
    await p.getByRole("button", { name: /^(Gespräch )?beenden$/i }).waitFor({ timeout: 40000 });
    await p.evaluate(() => window.__begruessung());
    return {};
  }

  async murmeln({ datei, gain }) {
    const b64 = datei ? fs.readFileSync(datei).toString("base64") : null;
    await this.page.evaluate(([b, g]) => window.__murmeln(b, g), [b64, gain]);
    return {};
  }

  async schritt({ clip, einwurf_clip: einwurfClip, start_frist_s: startFristS, ende_frist_s: endeFristS, einwurf_ab_s: einwurfAbS, freigabe }) {
    const roh = await this.page.evaluate((o) => window.__schritt(o), { clip, einwurfClip: einwurfClip || "", startFristS, endeFristS, einwurfAbS, freigabe });
    const delta = (name) => { const d = this[name] - this.gesendet[name]; this.gesendet[name] = this[name]; return d; };
    return { ...roh, r429: delta("r429"), konsole: delta("konsole"), blockiert: delta("blockiert") };
  }

  async gespraech_beenden() {
    const knopf = this.page.getByRole("button", { name: /^(Gespräch )?beenden$/i });
    if (await knopf.count()) {
      await knopf.first().click({ timeout: 10000 });
      await this.page.getByRole("button", { name: "Gespräch starten" }).waitFor({ timeout: 10000 }).catch(() => {});
    }
    return {};
  }

  async abbrechen() {
    if (this.page) await this.page.evaluate(() => window.__abbrechen()).catch(() => {});
    return { abgebrochen: true };
  }

  async schliessen() {
    const b = this.browser;
    this.browser = null;
    if (b) await b.close().catch(() => {});
    return {};
  }
}

const BEFEHLE = ["start", "anmelden", "gespraech_starten", "murmeln", "schritt", "gespraech_beenden", "abbrechen", "schliessen"];

async function verarbeite(sitzung, msg) {
  if (!msg || !BEFEHLE.includes(msg.cmd)) return { id: msg && msg.id, ok: false, fehler: "Unbekannter Befehl" };
  try {
    return { id: msg.id, ok: true, daten: await sitzung[msg.cmd](msg) };
  } catch (e) {
    return { id: msg.id, ok: false, fehler: kurz(e, sitzung.geheim) };
  }
}

function haupt(playwrightFabrik = playwrightLaden) {
  let sitzung = null;
  const antworten = (d) => process.stdout.write(JSON.stringify(d) + "\n");
  const beenden = async () => { if (sitzung) await sitzung.schliessen(); process.exit(0); };
  const rl = readline.createInterface({ input: process.stdin });
  rl.on("line", async (zeile) => {
    let msg;
    try { msg = JSON.parse(zeile); } catch { return; }
    try { sitzung = sitzung || new Sitzung(playwrightFabrik()); } catch (e) { return antworten({ id: msg.id, ok: false, fehler: kurz(e, []) }); }
    const d = await verarbeite(sitzung, msg);
    antworten(d);
    if (msg.cmd === "schliessen") setTimeout(() => process.exit(0), 50);
  });
  rl.on("close", beenden); // Python ist weg: Chrome nicht verwaisen lassen
  process.on("SIGTERM", beenden);
}

module.exports = { seite, Sitzung, verarbeite, urlErlaubt, herkunftErlaubt, kurz, WAMO_URL, HOST, PORTS, BEFEHLE };
if (require.main === module) haupt();
