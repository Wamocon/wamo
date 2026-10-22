// Gesprächsmessung ohne Mensch: Chromium mit einer WAV-Datei als Mikrofon gegen die Oberfläche, Anmeldung mit zweitem
// Faktor, dann spielt die Datei die Fragen. Gemessen wird der erste hörbare Ton von WAMO im Browser nach dem Satzende
// laut Zeitplan der Datei. Ablauf und Hilfen: scripts/wamo_latenz.py (Fragen erzeugen, auswerten).
//
//   node scripts/wamo-latenz-messen.cjs <buero|unterwegs> <name> [ausgabe-name]
//
// <name>.wav und <name>.json (Sätze mit start/ende in Sekunden, optional "stopp": true für Einwürfe) liegen im Ordner
// WAMO_MESS_DIR (Standard: Arbeitsordner). Dort liegt auch konto.json ({email, password, secret} eines TEMPORÄREN
// Testkontos, angelegt mit scripts/wamo_nutzer.py und danach gesperrt) und dort landet <ausgabe-name>.json.
//
// Umgebung:
//   WAMO_UI_URL   Adresse der Oberfläche (Standard http://127.0.0.1:3000). Für "unterwegs" muss sie auf einen Kern
//                 zeigen, der mit WAMO_LATENZ_LOG=1 läuft (NEXT_PUBLIC_VOICE_CORE_URL beim Bauen), für "buero" auf
//                 NEXT_PUBLIC_WAMO_KERN_BUERO; der Kern des Büros wird nur gelesen, nicht verändert.
//   ECHO          Pegel (0 bis 1) einer Echo-Simulation: WAMOs Stimme wird um 120 ms verzögert ins Mikrofon gemischt,
//                 als hätte die Echounterdrückung versagt. 0 oder leer: kein Echo.
//   WAMO_MESS_DIR Ordner der Dateien (siehe oben).
//   PLAYWRIGHT_MODUL  Name oder Pfad des Playwright-Moduls, falls es nicht im Arbeitsordner liegt.
//
// Ausgabe: Zeiten je Frage (latenzenMs, null = Bot sprach noch oder kein Ton), Stoppzeit je Einwurf (stoppMs: vom Beginn
// des Einwurfs bis der Bot mindestens 900 ms lang schweigt), Verlauf, Statusleiste. Keine Schlüssel, keine Kennwörter.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

function playwrightLaden() {
  // Playwright gehört nicht zu den Abhängigkeiten des Repos: im Arbeitsordner "npm install --no-save playwright" oder
  // PLAYWRIGHT_MODUL auf einen vorhandenen Ordner setzen.
  const orte = [process.cwd(), path.join(__dirname, "..", "ui"), path.join(__dirname, "..", "desktop")];
  for (const name of [process.env.PLAYWRIGHT_MODUL, "playwright"].filter(Boolean)) {
    try {
      return require(require.resolve(name, { paths: orte }));
    } catch {
      /* nächster Versuch */
    }
  }
  console.error('Playwright fehlt: im Arbeitsordner "npm install --no-save playwright" oder PLAYWRIGHT_MODUL setzen.');
  process.exit(2);
}
const { chromium } = playwrightLaden();

const DIR = process.env.WAMO_MESS_DIR || process.cwd();
const URL = process.env.WAMO_UI_URL || "http://127.0.0.1:3000";
const [modus, name, ausgabe = `${process.argv[2]}-${process.argv[3]}`] = process.argv.slice(2);
if (!["buero", "unterwegs"].includes(modus) || !name) {
  console.error("Aufruf: node wamo-latenz-messen.cjs <buero|unterwegs> <name> [ausgabe]");
  process.exit(2);
}
const konto = JSON.parse(fs.readFileSync(path.join(DIR, "konto.json"), "utf8"));
const plan = JSON.parse(fs.readFileSync(path.join(DIR, `${name}.json`), "utf8"));
const wav = path.join(DIR, `${name}.wav`);
const dauerS = Math.ceil(plan[plan.length - 1].ende + 14);

function base32(s) {
  const a = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of s.replace(/=+$/, "").toUpperCase()) {
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
const quantil = (werte, p) => {
  const s = [...werte].sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.ceil(p * s.length) - 1)] : null;
};

(async () => {
  const browser = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${wav}%noloop`,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1400, height: 900 }, permissions: ["microphone"] });
  await ctx.addInitScript(
    ({ modus, echo }) => {
      window.__m = { samples: [], mic: null };
      window.wamoDesktop = {
        isDesktop: true,
        levels: (s) => window.__m.samples.push([performance.now(), s.user, s.bot, s.zustand]),
        farben: () => {},
        betriebsart: { lesen: async () => modus, setzen: async (a) => a, abonnieren: () => () => {} },
        rechnername: async () => "TESTGERAET",
      };
      const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      if (echo > 0) {
        // Echo-Simulation: die Stimme des Bots (Remote-Spur) wird verzögert und mit Pegel "echo" in das Mikrofon gemischt
        window.__remote = [];
        const OrigPC = window.RTCPeerConnection;
        window.RTCPeerConnection = function (...a) {
          const pc = new OrigPC(...a);
          pc.addEventListener("track", (e) => {
            if (e.track.kind === "audio") window.__remote.push(new MediaStream([e.track]));
            if (window.__verbinde) window.__verbinde();
          });
          return pc;
        };
        window.RTCPeerConnection.prototype = OrigPC.prototype;
        navigator.mediaDevices.getUserMedia = async (c) => {
          const s = await gum(c);
          if (window.__m.mic === null) window.__m.mic = performance.now();
          const ac = new AudioContext();
          await ac.resume();
          const dest = ac.createMediaStreamDestination();
          ac.createMediaStreamSource(s).connect(dest);
          window.__verbinde = () => {
            for (const r of window.__remote) {
              if (r.__verbunden) continue;
              r.__verbunden = true;
              const verz = ac.createDelay(1);
              verz.delayTime.value = 0.12;
              const g = ac.createGain();
              g.gain.value = echo;
              ac.createMediaStreamSource(r).connect(verz);
              verz.connect(g);
              g.connect(dest);
            }
          };
          window.__verbinde();
          return dest.stream;
        };
      } else {
        navigator.mediaDevices.getUserMedia = async (c) => {
          const s = await gum(c);
          if (window.__m.mic === null) window.__m.mic = performance.now();
          return s;
        };
      }
    },
    { modus, echo: parseFloat(process.env.ECHO || "0") },
  );
  const page = await ctx.newPage();
  const konsole = [];
  page.on("console", (m) => {
    if (["error", "warning"].includes(m.type()) && !m.text().includes("GL Driver")) konsole.push(`${m.type()}: ${m.text().slice(0, 200)}`);
  });
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.locator('input[name="email"]').fill(konto.email);
  await page.locator('input[name="password"]').fill(konto.password);
  await page.getByRole("button", { name: "Anmelden" }).click();
  const code = page.locator('input[autocomplete="one-time-code"]').first();
  await code.waitFor({ timeout: 20000 });
  await code.fill(totp(konto.secret));
  await code.press("Enter");
  const start = page.getByRole("button", { name: "Gespräch starten" });
  await start.waitFor({ timeout: 20000 });
  const t0 = Date.now();
  await start.click();
  await page.getByRole("button", { name: "Gespräch beenden" }).waitFor({ timeout: 30000 });
  const verbunden = (Date.now() - t0) / 1000;
  await warte(dauerS * 1000);
  const verlauf = await page.locator('[role="log"]').allInnerTexts();
  const status = await page.locator(".status-items [data-feld]").allInnerTexts();
  const fehler = await page.locator(".error").allInnerTexts();
  const proben = await page.evaluate(() => window.__m);
  await page.getByRole("button", { name: "Gespräch beenden" }).click().catch(() => {});
  await warte(1500);
  await browser.close();

  const S = proben.samples;
  const SCHWELLE = 0.02;
  const latenzen = [];
  for (const satz of plan) {
    const tEnde = proben.mic + satz.ende * 1000;
    // Spricht der Bot beim Satzende noch (Antwort auf die vorige Frage), zählt die Frage nicht (null)
    const laeuft = S.some((x) => x[0] > tEnde - 600 && x[0] <= tEnde && x[2] > SCHWELLE);
    const k = S.findIndex((x) => x[0] > tEnde && x[2] > SCHWELLE);
    latenzen.push(!laeuft && k >= 0 && S[k][0] - tEnde < 15000 ? Math.round(S[k][0] - tEnde) : null);
  }
  // Stoppzeit bei Einwürfen: kurze Wortlücken zählen nicht, die Antwort auf den Einwurf kommt erst deutlich später
  const stopp = plan.map((satz) => {
    if (!satz.stopp) return undefined;
    const t1 = proben.mic + satz.start * 1000;
    for (let i = 0; i < S.length; i++) {
      if (S[i][0] <= t1 || S[i][2] > SCHWELLE) continue;
      const fenster = S.filter((x) => x[0] >= S[i][0] && x[0] < S[i][0] + 900);
      if (S[i][0] + 900 <= S[S.length - 1][0] && fenster.every((x) => x[2] <= SCHWELLE)) return Math.round(S[i][0] - t1);
    }
    return null;
  });
  const gueltig = latenzen.filter((x) => x !== null);
  const maxUser = Math.max(0, ...S.map((x) => x[1]));
  const ergebnis = {
    modus, name, zeit: new Date().toISOString(), verbundenS: verbunden, latenzenMs: latenzen, stoppMs: stopp,
    p50: quantil(gueltig, 0.5), p95: quantil(gueltig, 0.95), max: gueltig.length ? Math.max(...gueltig) : null, maxUser, verlauf, status, fehler, konsole,
  };
  fs.writeFileSync(path.join(DIR, `${ausgabe}.json`), JSON.stringify(ergebnis, null, 1));
  console.log(`${modus} ${name}: verbunden nach ${verbunden.toFixed(1)} s; erster Ton (ms): ${latenzen.join(", ")}`);
  console.log(`p50 ${ergebnis.p50} ms, p95 ${ergebnis.p95} ms, max ${ergebnis.max} ms; max Pegel Mensch ${maxUser.toFixed(3)}`);
  console.log("STATUS:", status.map((s) => s.replace(/\s+/g, " ")).join(" | "));
  if (fehler.length) console.log("MELDUNGEN:", fehler.map((f) => f.slice(0, 160)).join(" || "));
  console.log("VERLAUF:\n" + verlauf.join("\n").replace(/\n{2,}/g, "\n").slice(0, 2500));
  if (konsole.length) console.log("KONSOLE:\n" + konsole.slice(0, 10).join("\n"));
})().catch((e) => {
  console.error("FEHLER:", e.message.split("\n")[0]);
  process.exit(1);
});
