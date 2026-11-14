// Szenariotest für Stimme und Sprachverhalten: Chromium mit einem virtuellen Mikrofon gegen die Oberfläche und einen lokalen
// Kern (Betriebsart Unterwegs). Das virtuelle Mikrofon spielt Sprechproben und Geräusche genau dann, wenn WAMO in einem
// bestimmten Zustand ist (z. B. "seit 5 s am Sprechen"). Aufgenommen werden WAMOs Ausgabestrom und das Mikrofon mit einer
// gemeinsamen Uhr (AudioContext), dazu die Lautstärke des Wiedergabeelements (weiches Ausblenden der Oberfläche).
// Auswertung: scripts/wamo_stimme.py (Kennzahlen, Hörproben). Ablauf und Voraussetzungen: dort im Kopf der Datei.
//
//   node scripts/wamo-stimme-messen.cjs <lauf> [szenarien] [wiederholungen]   (szenarien: "einwurf:8,echo" nennt je Szenario die Wiederholungen)
//
// Szenarien (Komma): fakten, kurz, lang, einwurf, ein_wort, backchannel, geraeusch, echo, schnell, pause, werkzeug, werkzeug_mhm (Standard: alle).
//
// Umgebung:
//   WAMO_MESS_DIR     Messordner mit konto.json ({email, password, secret?}) und clips/ (python wamo_stimme.py clips)
//   WAMO_UI_URL       Oberfläche (Standard http://127.0.0.1:3100), gebaut mit NEXT_PUBLIC_VOICE_CORE_URL=http://127.0.0.1:7861
//   WAMO_REPO         Hauptordner des Repos (Standard: Ordner über scripts/)
//   KERN_ENV          zusätzliche Umgebung des Kerns, "A=1;B=2" (Schalter zum Vergleichen)
//   WAMO_PYTHON       Python des Kerns (Standard: voice-core/.venv im Repo; im Worktree das venv des Hauptordners)
//   KEIN_KERN=1       Kern nicht starten (läuft schon auf 7861)
//   ECHO_PEGEL        Pegel der Echo-Simulation im Szenario echo (Standard 0.5, 120 ms Verzögerung)
//   PLAYWRIGHT_MODUL  Pfad oder Name von playwright(-core); der Browser ist das installierte Chrome
//
// Ausgabe: <lauf>.json (Ereignisse, Ergebnisse je Wiederholung, Lautstärkeverlauf), <lauf>-<szenario>-bot.wav und -mic.wav
// (24 kHz, sample-genau zueinander), <lauf>.kern.log. Keine Schlüssel, keine Kennwörter in der Ausgabe.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const net = require("net");
const { spawn, spawnSync } = require("child_process");

function playwrightLaden() {
  const orte = [path.join(__dirname, "..", "ui"), path.join(__dirname, "..", "desktop")]; // nie aus dem Arbeitsordner laden
  for (const name of [process.env.PLAYWRIGHT_MODUL, "playwright", "playwright-core"].filter(Boolean)) {
    try {
      return require(require.resolve(name, { paths: orte }));
    } catch {
      /* nächster Versuch */
    }
  }
  console.error("Playwright fehlt: PLAYWRIGHT_MODUL auf den Ordner von playwright-core setzen.");
  process.exit(2);
}
const { chromium } = playwrightLaden();

const DIR = process.env.WAMO_MESS_DIR || process.cwd();
const URL = process.env.WAMO_UI_URL || "http://127.0.0.1:3100";
const REPO = process.env.WAMO_REPO || path.join(__dirname, "..");
const [lauf, szenarioArg, wdhArg] = process.argv.slice(2);
const ALLE = ["fakten", "kurz", "lang", "einwurf", "ein_wort", "backchannel", "geraeusch", "echo", "schnell", "pause", "werkzeug", "werkzeug_mhm"];
const WIEDERHOLUNGEN = { fakten: 8, kurz: 5, lang: 3, einwurf: 6, ein_wort: 5, backchannel: 5, geraeusch: 5, echo: 3, schnell: 5, pause: 6, werkzeug: 5, werkzeug_mhm: 5 };
if (!lauf || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(lauf)) {
  console.error("Aufruf: node wamo-stimme-messen.cjs <lauf> [szenarien] [wiederholungen] (lauf: Buchstaben, Ziffern, . _ -)");
  process.exit(2);
}
{
  // Der Messordner enthält das Testkonto im Klartext, Gesprächsaufnahmen und Logs: nie im Repository
  const rel = path.relative(path.resolve(REPO), path.resolve(DIR));
  if (!rel.startsWith("..") && !path.isAbsolute(rel)) {
    console.error("WAMO_MESS_DIR liegt im Repository (" + DIR + "): bitte einen Ordner außerhalb wählen, z. B. D:\tmp\wamo-stimme.");
    process.exit(2);
  }
}
const LOOPBACK = ["127.0.0.1", "localhost", "[::1]"].includes(new globalThis.URL(URL).hostname);
const eintraege = szenarioArg && szenarioArg !== "alle" ? szenarioArg.split(",") : ALLE; // "einwurf:8" nennt die Wiederholungen
const szenarien = eintraege.map((e) => e.split(":")[0]);
for (const e of eintraege) { const w = parseInt(e.split(":")[1] || "", 10); if (w > 0) WIEDERHOLUNGEN[e.split(":")[0]] = w; }
for (const s of szenarien) if (!ALLE.includes(s)) { console.error("Unbekanntes Szenario:", s); process.exit(2); }
const konto = JSON.parse(fs.readFileSync(path.join(DIR, "konto.json"), "utf8"));
const clipDauer = JSON.parse(fs.readFileSync(path.join(DIR, "clips", "clips.json"), "utf8"));

function base32(s) {
  const a = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of s.replace(/=+$/, "").replace(/\s+/g, "").toUpperCase()) {
    const v = a.indexOf(c);
    if (v >= 0) bits += v.toString(2).padStart(5, "0");
  }
  const out = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(out);
}
function totp(secret) {
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30)));
  const h = crypto.createHmac("sha1", base32(secret)).update(buf).digest();
  const o = h[h.length - 1] & 0xf;
  return ((h.readUInt32BE(o) & 0x7fffffff) % 1e6).toString().padStart(6, "0");
}
const warte = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------------------------------------------------
// Kern starten (eigener Port, Latenz-Log an, Profil des Testkontos bekommt den Claude-Zugang des Kerns)
let kern = null;
async function kernStarten() {
  if (process.env.KEIN_KERN === "1") return;
  const logPfad = path.join(DIR, `${lauf}.kern.log`);
  const fd = fs.openSync(logPfad, "w");
  const env = {
    ...process.env, WAMO_LATENZ_LOG: "1", PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8", WAMO_BETRIEB: "",
    WAMO_CLAUDE_SCHLUESSEL_PROFIL: konto.user_id || process.env.WAMO_CLAUDE_SCHLUESSEL_PROFIL || "",
  };
  for (const paar of (process.env.KERN_ENV || "").split(";").filter(Boolean)) {
    const i = paar.indexOf("=");
    if (i < 1) throw new Error("KERN_ENV: Eintrag ohne Namen oder ohne = : " + paar.split("=")[0]);
    env[paar.slice(0, i).trim()] = paar.slice(i + 1).trim();
  }
  // Ein Arbeitsordner (Worktree) hat kein eigenes venv: WAMO_PYTHON zeigt dann auf das venv des Hauptordners.
  const python = process.env.WAMO_PYTHON || path.join(REPO, "voice-core", ".venv", "Scripts", "python.exe");
  const ui = new globalThis.URL(URL);
  // Die .env des Kerns (override=True) darf Profil und Schalter der Messung nicht überschreiben: eigener Starter
  env.WAMO_MESS_FEST = ["WAMO_CLAUDE_SCHLUESSEL_PROFIL", ...(process.env.KERN_ENV || "").split(";").filter(Boolean).map((p) => p.split("=")[0].trim())].join(",");
  kern = spawn(python, [path.join(__dirname, "wamo_kern_messstart.py"), "bot.py", "--port", "7861", "--allowed-origins", ui.origin, ui.origin.replace("127.0.0.1", "localhost")], {
    cwd: path.join(REPO, "voice-core"), env, stdio: ["ignore", fd, fd], windowsHide: true,
  });
  for (let i = 0; i < 120; i++) {
    if (await new Promise((r) => { const s = net.connect(7861, "127.0.0.1"); s.on("connect", () => { s.destroy(); r(true); }); s.on("error", () => r(false)); })) return;
    await warte(1000);
  }
  throw new Error("Kern auf Port 7861 nicht erreichbar, siehe " + logPfad);
}
function kernBeenden() {
  if (kern && kern.pid && kern.exitCode === null) {
    const taskkill = path.join(process.env.SystemRoot || "C:\Windows", "System32", "taskkill.exe");
    spawnSync(taskkill, ["/pid", String(kern.pid), "/T", "/F"], { stdio: "ignore" });
  }
  kern = null;
}

// ---------------------------------------------------------------------------------------------------------------------
// Seite: virtuelles Mikrofon, Aufnahme, Zustandsfragen und Szenarien. Läuft im Browser (addInitScript).
function seite({ modus, echoPegel }) {
  const S = (window.__s = {
    ac: null, clips: {}, events: [], vol: [], botRec: [], micRec: [], rep: 0, echo: null, remote: [], micBus: null, dest: null,
    botBegin: -1e9, botLetzt: -1e9, micLetzt: -1e9, wall: [], markeT: 0,
  });
  const SCHWELLE = 0.003; // Pegel (RMS je Block), ab dem WAMO als sprechend gilt
  window.wamoDesktop = {
    isDesktop: true,
    levels: () => {},
    farben: () => {},
    betriebsart: { lesen: async () => modus, setzen: async (a) => a, abonnieren: () => () => {} },
    rechnername: async () => "TESTGERAET",
  };
  // Lautstärke des Wiedergabeelements mitschreiben (weiches Ausblenden der Oberfläche setzt audio.volume)
  const vd = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "volume");
  Object.defineProperty(HTMLMediaElement.prototype, "volume", {
    configurable: true,
    get() { return vd.get.call(this); },
    set(v) { S.vol.push([S.ac ? S.ac.currentTime : -1, v]); vd.set.call(this, v); },
  });
  const jetzt = () => S.ac.currentTime;
  const schlaf = (ms) => new Promise((r) => setTimeout(r, ms));
  async function warteBis(fn, sekunden, schritt = 25) {
    const t0 = performance.now();
    while (performance.now() - t0 < sekunden * 1000) {
      if (fn()) return true;
      await schlaf(schritt);
    }
    return false;
  }
  const LUECKE = 0.8; // Pausen bis dahin (Satzpausen der Stimme liegen bei 0,4 bis 0,65 s) gehören zur selben Rede
  const botAktiv = () => jetzt() - S.botLetzt < LUECKE;
  const botSeit = () => (botAktiv() ? jetzt() - S.botBegin : 0);
  const botStille = () => jetzt() - S.botLetzt;

  const WORKLET = `class Rec extends AudioWorkletProcessor {
    constructor() { super(); this.n = 0; this.buf = new Float32Array(2048); this.start = 0; }
    process(inputs) {
      const ch = inputs[0] && inputs[0][0];
      const len = ch ? ch.length : 128;
      for (let i = 0; i < len; i++) {
        if (this.n === 0) this.start = currentFrame + i;
        this.buf[this.n++] = ch ? ch[i] : 0;
        if (this.n === 2048) { this.port.postMessage({ start: this.start, data: this.buf.slice(0) }); this.n = 0; }
      }
      return true;
    }
  }
  registerProcessor("rec", Rec);`;

  function rms(d) { let s = 0; for (let i = 0; i < d.length; i++) s += d[i] * d[i]; return Math.sqrt(s / d.length); }
  async function audioStart() {
    if (S.ac) return;
    const ac = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
    await ac.resume();
    S.ac = ac;
    await ac.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));
    const rec = (ziel, bot) => {
      const n = new AudioWorkletNode(ac, "rec", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: "explicit" });
      n.port.onmessage = (e) => {
        ziel.push(e.data);
        const t = e.data.start / ac.sampleRate;
        if (rms(e.data.data) > SCHWELLE) {
          if (bot) { if (t - S.botLetzt > LUECKE) S.botBegin = t; S.botLetzt = t + e.data.data.length / ac.sampleRate; }
          else S.micLetzt = t;
        }
      };
      const g = ac.createGain();
      g.gain.value = 0;
      n.connect(g);
      g.connect(ac.destination);
      return n;
    };
    S.botNode = rec(S.botRec, true);
    S.micNode = rec(S.micRec, false);
    S.micBus = ac.createGain();
    S.micBus.connect(S.micNode);
    // Rauschboden (-66 dBFS): wie ein echtes Mikrofon, und der Mikrofon-Wächter der Oberfläche meldet keine digitale Stille
    const nb = ac.createBuffer(1, ac.sampleRate * 2, ac.sampleRate);
    const nd = nb.getChannelData(0);
    for (let i = 0; i < nd.length; i++) nd[i] = (Math.random() * 2 - 1) * 0.0008;
    const nq = ac.createBufferSource();
    nq.buffer = nb; nq.loop = true; nq.connect(S.micBus); nq.start();
    // Echo-Simulation: WAMOs Stimme, 120 ms verzögert, mit Pegel echo im Mikrofon
    S.delay = ac.createDelay(1); S.delay.delayTime.value = 0.12;
    S.echo = ac.createGain(); S.echo.gain.value = 0;
    S.delay.connect(S.echo); S.echo.connect(S.micBus);
    for (const r of S.remote) verbinde(r);
  }
  function verbinde(track) {
    if (track.__v) return;
    track.__v = true;
    const q = S.ac.createMediaStreamSource(new MediaStream([track]));
    q.connect(S.botNode);
    q.connect(S.delay);
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
      if (S.ac) verbinde(e.track);
    });
    return pc;
  };
  window.RTCPeerConnection.prototype = OrigPC.prototype;

  window.__lade = async (name, b64) => {
    await audioStart();
    const bin = atob(b64);
    const b = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i);
    S.clips[name] = await S.ac.decodeAudioData(b.buffer);
  };

  function sprich(name, opt = {}) {
    const buf = S.clips[name];
    const q = S.ac.createBufferSource();
    q.buffer = buf;
    const g = S.ac.createGain();
    g.gain.value = opt.gain ?? 1;
    q.connect(g); g.connect(S.micBus);
    const t0 = opt.bei ?? S.ac.currentTime + 0.03;
    q.start(t0);
    S.events.push({ typ: "clip", name, t0, dauer: buf.duration, rolle: opt.rolle || "", rep: S.rep, botSeit: botSeit(), wall: Date.now(), jetzt: S.ac.currentTime });
    return { t0, dauer: buf.duration, ende: t0 + buf.duration };
  }
  // wartet, bis WAMO nach "ab" zu sprechen beginnt (höchstens startS) und danach 3 s schweigt (höchstens gesamtS)
  async function warteAntwort(ab, startS = 20, gesamtS = 70, stilleS = 3.0) {
    const t0 = performance.now();
    const gestartet = await warteBis(() => botAktiv() && S.botBegin > ab - 0.1, startS);
    if (!gestartet) return { gestartet: false };
    const rest = Math.max(1, gesamtS - (performance.now() - t0) / 1000);
    const fertig = await warteBis(() => botStille() >= stilleS, rest);
    return { gestartet: true, fertig };
  }
  const FRAGEN = ["frage_lang_1", "frage_lang_2", "frage_lang_3"];
  async function langeAntwort(rep, mindestS) {
    const q = sprich(FRAGEN[rep % 3], { rolle: "frage" });
    const gestartet = await warteBis(() => botAktiv() && S.botBegin > q.ende - 0.1, 20);
    if (!gestartet) return { q, ok: false, grund: "keine Antwort" };
    const lang = await warteBis(() => botSeit() >= mindestS, 40);
    if (!lang || !botAktiv()) return { q, ok: false, grund: "Antwort zu kurz" };
    return { q, ok: true };
  }
  const SZ = {
    async fakten(rep) {
      const q = sprich("fakt_" + ((rep % 8) + 1), { rolle: "frage" });
      return { ...(await warteAntwort(q.ende)), ok: true };
    },
    async kurz() { const q = sprich("frage_zeit", { rolle: "frage" }); return { ...(await warteAntwort(q.ende)), ok: true }; },
    async lang(rep) {
      const q = sprich(FRAGEN[rep % 3], { rolle: "frage" });
      return { ...(await warteAntwort(q.ende)), ok: true };
    },
    async einwurf(rep) {
      const l = await langeAntwort(rep, 5.0);
      if (!l.ok) { await warteAntwort(l.q.ende, 1, 40); return { ok: false, grund: l.grund }; }
      const e = sprich(rep % 2 ? "einwurf_b" : "einwurf_a", { rolle: "einwurf" });
      return { ...(await warteAntwort(e.ende, 25, 70)), ok: true };
    },
    // Ein einzelnes Wort ("Danke") mitten in der langen Antwort: WAMO soll an der Wortgrenze innehalten und danach an derselben Stelle
    // weitersprechen (WAMO_INNEHALTEN), nicht abbrechen. Nach dem Wort 4 s abwarten (Pause höchstens 2,5 s), dann muss WAMO wieder sprechen.
    async ein_wort(rep) {
      const l = await langeAntwort(rep, 5.0);
      if (!l.ok) { await warteAntwort(l.q.ende, 1, 40); return { ok: false, grund: l.grund }; }
      const e = sprich("ein_wort", { rolle: "ein_wort" });
      await schlaf(Math.max(0, (e.ende - jetzt()) * 1000) + 4000);
      return { ...(await warteAntwort(l.q.ende, 1, 70)), ok: true, weiter_nach_4s: botAktiv() };
    },
    async backchannel(rep) {
      const l = await langeAntwort(rep, 4.0);
      if (!l.ok) { await warteAntwort(l.q.ende, 1, 40); return { ok: false, grund: l.grund }; }
      let n = 0;
      for (const name of ["mhm", "ja_genau", "aha"]) {
        if (n > 0 && !botAktiv()) break;
        sprich(name, { rolle: "backchannel" });
        n++;
        await schlaf(4200);
      }
      return { ...(await warteAntwort(l.q.ende, 1, 70)), ok: true, gespielt: n };
    },
    async geraeusch(rep) {
      const l = await langeAntwort(rep, 4.0);
      if (!l.ok) { await warteAntwort(l.q.ende, 1, 40); return { ok: false, grund: l.grund }; }
      let n = 0;
      for (const name of ["husten", "klopfen"]) {
        if (n > 0 && !botAktiv()) break;
        sprich(name, { rolle: "geraeusch" });
        n++;
        await schlaf(4200);
      }
      return { ...(await warteAntwort(l.q.ende, 1, 70)), ok: true, gespielt: n };
    },
    async echo(rep) {
      S.echo.gain.value = echoPegel;
      S.events.push({ typ: "echo", pegel: echoPegel, t0: jetzt(), rep: S.rep });
      const q = sprich(FRAGEN[rep % 3], { rolle: "frage" });
      const r = await warteAntwort(q.ende, 20, 70);
      S.echo.gain.value = 0;
      S.events.push({ typ: "echo", pegel: 0, t0: jetzt(), rep: S.rep });
      return { ...r, ok: true };
    },
    async schnell() {
      const a = sprich("frage_zeit", { rolle: "frage" });
      const b = sprich("frage_datum", { rolle: "frage2", bei: a.ende + 0.5 });
      return { ...(await warteAntwort(b.ende, 25, 70)), ok: true };
    },
    async pause(rep) {
      const luecke = rep % 2 ? 2.5 : 1.5;
      const a = sprich("pause_a", { rolle: "pause_a" });
      const b = sprich("pause_b", { rolle: "pause_b", bei: a.ende + luecke });
      return { ...(await warteAntwort(b.ende, 25, 70)), ok: true, luecke };
    },
    async werkzeug() { const q = sprich("frage_suche", { rolle: "frage" }); return { ...(await warteAntwort(q.ende, 30, 120, 9.0)), ok: true }; },
    // Rückmeldelaut bei laufendem Werkzeug: Suchfrage, dann zwei Rückmeldelaute (2,5 s und 5,5 s nach dem Fragenende), während
    // die Websuche läuft. Das Werkzeug darf nicht abbrechen: danach kommt das Ergebnis (Antwort mit mehreren Sekunden Sprache).
    async werkzeug_mhm() {
      const q = sprich("frage_suche", { rolle: "frage" });
      await schlaf(Math.max(0, (q.ende - jetzt()) * 1000) + 2500);
      sprich("mhm", { rolle: "werkzeug_mhm" });
      await schlaf(3000);
      sprich("ja_genau", { rolle: "werkzeug_mhm" });
      return { ...(await warteAntwort(q.ende, 30, 120, 9.0)), ok: true };
    },
  };
  window.__begruessung = async () => {
    await warteBis(() => botAktiv(), 25);
    await warteBis(() => botStille() >= 2.0, 20);
    return S.botLetzt > 0;
  };
  window.__block = async (name, wiederholungen) => {
    const t0 = jetzt();
    S.events.push({ typ: "block", name, t0, wall: Date.now() });
    const ergebnisse = [];
    for (let i = 0; i < wiederholungen; i++) {
      S.rep = i;
      S.events.push({ typ: "rep", rep: i, t0: jetzt(), wall: Date.now() });
      let r;
      try { r = await SZ[name](i); } catch (e) { r = { ok: false, grund: String(e && e.message) }; }
      r.rep = i;
      ergebnisse.push(r);
      await warteBis(() => botStille() >= 1.5, 20);
      await schlaf(1500);
    }
    return { ergebnisse, t0, t1: jetzt(), wall1: Date.now() };
  };
  // Ausschnitt [t0, t1] als 24-kHz-Int16 (Mittel je zwei Proben), Base64; danach wird alles bis t1 verworfen
  window.__export = async (t0, t1) => {
    const sr = S.ac.sampleRate;
    const holen = (blocks) => {
      const a = Math.floor(t0 * sr), b = Math.ceil(t1 * sr);
      const aus = new Int16Array(Math.ceil((b - a) / 2));
      for (const bl of blocks) {
        for (let i = 0; i < bl.data.length; i += 2) {
          const k = bl.start + i;
          if (k < a || k + 1 >= b) continue;
          const v = (bl.data[i] + bl.data[i + 1]) / 2;
          aus[(k - a) >> 1] = Math.max(-1, Math.min(1, v)) * 32767;
        }
      }
      return aus;
    };
    const alsBase64 = (arr) => new Promise((ok) => {
      const fr = new FileReader();
      fr.onload = () => ok(String(fr.result).split(",")[1]);
      fr.readAsDataURL(new Blob([arr.buffer]));
    });
    const bot = await alsBase64(holen(S.botRec)), mic = await alsBase64(holen(S.micRec));
    const fertig = Math.floor(t1 * sr);
    for (const arr of [S.botRec, S.micRec]) arr.splice(0, arr.length, ...arr.filter((b) => b.start + 2048 > fertig));
    return { sr: sr / 2, bot, mic, events: S.events.splice(0), vol: S.vol.splice(0), wallDiff: Date.now() - jetzt() * 1000 };
  };
}

// ---------------------------------------------------------------------------------------------------------------------
(async () => {
  await kernStarten();
  const browser = await chromium.launch({
    channel: "chrome",
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: LOOPBACK, bypassCSP: LOOPBACK, viewport: { width: 1400, height: 900 }, permissions: ["microphone"] });
  await ctx.addInitScript(seite, { modus: "unterwegs", echoPegel: parseFloat(process.env.ECHO_PEGEL || "0.5") });
  const page = await ctx.newPage();
  const konsole = [];
  page.on("console", (m) => {
    if (["error", "warning"].includes(m.type()) && !m.text().includes("GL Driver")) konsole.push(`${m.type()}: ${m.text().slice(0, 200)}`);
  });
  const ergebnis = { lauf, zeit: new Date().toISOString(), kernEnv: (process.env.KERN_ENV || "").split(";").filter(Boolean).map((e) => (/KEY|SECRET|TOKEN|PASSWORD|URL|KEK/i.test(e.split("=")[0]) ? e.split("=")[0] + "=<verdeckt>" : e)).join(";"), szenarien: [], konsole };
  const speichern = () => fs.writeFileSync(path.join(DIR, `${lauf}.json`), JSON.stringify(ergebnis, null, 1));
  try {
    await page.goto(URL, { waitUntil: "networkidle" });
    for (const name of fs.readdirSync(path.join(DIR, "clips")).filter((f) => f.endsWith(".wav"))) {
      await page.evaluate(([n, b]) => window.__lade(n, b), [name.replace(/\.wav$/, ""), fs.readFileSync(path.join(DIR, "clips", name)).toString("base64")]);
    }
    // Anmeldung; beim ersten Mal richtet das Konto den zweiten Faktor ein (Geheimnis kommt in konto.json)
    await page.locator('input[name="email"]').fill(konto.email);
    await page.locator('input[name="password"]').fill(konto.password);
    await page.getByRole("button", { name: "Anmelden" }).click();
    const code = page.locator('input[autocomplete="one-time-code"]').first();
    await code.waitFor({ timeout: 20000 });
    if (await page.locator("details.gate-secret").count()) {
      await page.locator("details.gate-secret summary").click();
      konto.secret = (await page.locator("details.gate-secret code").innerText()).trim();
      fs.writeFileSync(path.join(DIR, "konto.json"), JSON.stringify(konto), { mode: 0o600 });
    }
    await code.fill(totp(konto.secret));
    await code.press("Enter");
    await page.getByRole("button", { name: "Gespräch starten" }).waitFor({ timeout: 25000 });

    for (const name of szenarien) {
      const wdh = wdhArg ? parseInt(wdhArg, 10) : WIEDERHOLUNGEN[name];
      await page.getByRole("button", { name: "Gespräch starten" }).click();
      await page.getByRole("button", { name: "Gespräch beenden" }).waitFor({ timeout: 40000 });
      const begruesst = await page.evaluate(() => window.__begruessung());
      const aus = await page.evaluate(([n, w]) => window.__block(n, w), [name, wdh]);
      await warte(1200);
      const ex = await page.evaluate(([a, b]) => window.__export(a, b), [Math.max(0, aus.t0 - 1), aus.t1 + 0.5]);
      for (const k of ["bot", "mic"]) {
        const datei = `${lauf}-${name}-${k}.wav`;
        const pcm = Buffer.from(ex[k], "base64");
        const kopf = Buffer.alloc(44);
        kopf.write("RIFF", 0); kopf.writeUInt32LE(36 + pcm.length, 4); kopf.write("WAVEfmt ", 8); kopf.writeUInt32LE(16, 16);
        kopf.writeUInt16LE(1, 20); kopf.writeUInt16LE(1, 22); kopf.writeUInt32LE(ex.sr, 24); kopf.writeUInt32LE(ex.sr * 2, 28);
        kopf.writeUInt16LE(2, 32); kopf.writeUInt16LE(16, 34); kopf.write("data", 36); kopf.writeUInt32LE(pcm.length, 40);
        fs.writeFileSync(path.join(DIR, datei), Buffer.concat([kopf, pcm]));
      }
      ergebnis.szenarien.push({
        name, begruesst, von: Math.max(0, aus.t0 - 1), bis: aus.t1 + 0.5, sr: ex.sr, wallDiff: ex.wallDiff,
        bot: `${lauf}-${name}-bot.wav`, mic: `${lauf}-${name}-mic.wav`, events: ex.events, vol: ex.vol, ergebnisse: aus.ergebnisse,
      });
      speichern();
      console.log(`${name}: ${aus.ergebnisse.map((r) => (r.ok ? "ok" : r.grund || "?")).join(", ")}`);
      await page.getByRole("button", { name: "Gespräch beenden" }).click().catch(() => {});
      await warte(2500);
    }
  } finally {
    speichern();
    await browser.close().catch(() => {});
    kernBeenden();
  }
  if (konsole.length) console.log("KONSOLE:\n" + konsole.slice(0, 10).join("\n"));
})().catch((e) => {
  console.error("FEHLER:", String(e.message).split("\n")[0]);
  kernBeenden();
  process.exit(1);
});
