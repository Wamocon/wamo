// WAMO-Desktop: der Client auf dem PC.
//
// * Hauptfenster mit der bekannten Oberfläche (localhost:3000), OHNE Hintergrund-Drosselung:
//   Mikrofon und Sprachausgabe laufen weiter, auch wenn Chrome, Amazon & Co. vorne sind.
//   Beim Start zeigt es sofort eine Ladeseite (start.html), bei Ladefehlern eine Fehlerseite mit
//   "Wiederholen".
// * Stimmen-Rand "Rote Kante": pro Monitor ein durchsichtiges, nicht anklickbares Fenster ganz oben.
//   Im Ruhezustand unsichtbar; WAMO spricht rot, der Mensch als Licht von unten, dazu Denken, Handeln
//   und wartende Freigaben (Zustand kommt mit den Pegeln). Die Farben kommen einmalig aus den
//   CSS-Variablen der Oberfläche (ui/src/lib/stimmfarben.ts), sie sind nur dort definiert.
//   Bei Vollbild-Programmen (Spiele, Videos, Präsentationen) blendet sich der Rand aus (vollbild.js); sichtbar ist er
//   nur, wenn der Tray-Haken "Stimmen-Rand" an ist und kein Vollbild läuft (randSichtbarkeit).
// * Tray-Symbol mit "Mit Windows starten"; Schließen versteckt nur ins Tray, das Gespräch bleibt.
// * Startet bei Bedarf Voice-Core und UI selbst (WAMO_MANAGE_SERVICES=off schaltet das ab). Die UI
//   läuft als Produktions-Build (next build einmalig, dann next start nur auf 127.0.0.1);
//   WAMO_UI_DEV=1 startet stattdessen next dev.
// * Betriebsart je Gerät (userData/einstellungen.json, Tray und Oberfläche): "unterwegs" (Standard) startet den
//   lokalen Voice-Core wie bisher, "buero" nutzt den Kern auf Sokrates-2 und startet lokal keinen. Die Oberfläche
//   bekommt nur den Namen der Betriebsart; die Adressen stehen in ihrer Build-Konfiguration (und CSP). Der Kern im
//   Büro läuft über HTTPS mit der WAMO-CA, der Chromium der App vertraut ihr über den Windows-Speicher.
//
// * Einrichtung des Laptops bei der Anmeldung (geraet.js, nur Büro): eigener Geräteschlüssel, Bindung an das Profil über
//   den Kern, Tunnel zu Sokrates-2; ein Wächter stößt die Aufgabe "WAMO Tunnel" an, wenn ihr Überwacher schweigt.
//
// * Zwei Formen (installation.js): Entwicklermodus (nicht paketiert, alles aus dem Repository) und installierte App
//   (Installer, siehe README "Windows-Installer"). Installiert gibt es nur die Betriebsart Büro: Die Oberfläche kommt von
//   Sokrates-2, Host und Skripte liegen unter resources/, die Python-Umgebung des Hosts richtet host-umgebung.js ein, Logs
//   und Einstellungen liegen unter %APPDATA%\WAMO. Der Unterwegs-Kern ist dort nicht verfügbar und im Tray ausgegraut.
// * Die WAMO-CA gilt nur innerhalb der App (zertifikat.js), nie als Änderung am Windows-Speicher und nie über
//   --ignore-certificate-errors.
//
// Umgebung: WAMO_UI_URL, WAMO_VOICE_CORE_URL (http oder https), WAMO_MANAGE_SERVICES, WAMO_UI_DEV,
// WAMO_VEIL_CM, WAMO_LOG_LEVEL (an den Voice-Core; Standard INFO, nie DEBUG), WAMO_VOLLBILD_LOG=1 (Rohwerte des
// Vollbild-Wächters in logs/vollbild.log), WAMO_BERECHTIGUNG_LOG=1 (jede Berechtigungsanfrage und -prüfung in
// logs/berechtigungen.log; abgelehnte Mikrofon-Anfragen stehen immer dort). Mikrofon-Meldungen der Oberfläche
// ("[WAMO] ...", z. B. "Mikrofon liefert Ton: Gerät, Spitze -52 dBFS" oder "liefert nur Stille") und der
// Windows-Mikrofonzugriff beim Start landen in logs/oberflaeche.log.
//
// Sicherheit: contextIsolation + sandbox, keine nodeIntegration. Die Seite darf nur Pegel mit Zustand
// und einmalig die Randfarben melden; beides wird hier gegen eine feste Liste geprüft. Navigation und
// Weiterleitungen bleiben auf dem UI-Origin, keine Webviews, Mikrofon (nur Audio) nur für das
// Hauptfenster mit der eigenen Oberfläche, fremde Seiten öffnen im normalen Browser.
// Prozesse aus PID-Dateien werden nur beendet, wenn Programmname, Befehlszeile und Startzeit passen.

const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  ipcMain,
  nativeImage,
  powerSaveBlocker,
  screen,
  session,
  shell,
  systemPreferences,
} = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const os = require("node:os");
const path = require("node:path");
const { GeraetFehler, erzeugeGeraet } = require("./geraet");
const { erzeugeHostUmgebung } = require("./host-umgebung");
const { adressen, loeseAuf, wirksameBetriebsart } = require("./installation");
const { erzeugePruefer, ladeCa, pruefeSocket } = require("./zertifikat");
const { powershellPfad, starteVollbildWaechter } = require("./vollbild");
const {
  installieren,
  mikrofonStatusWert,
  oberflaechenZeile,
  protokollieren,
  protokollZeile,
  ratenGrenze,
} = require("./berechtigungen");

// Die installierte App hat eigene Daten (%APPDATA%\WAMO), getrennt vom Entwicklermodus (%APPDATA%\wamo-desktop): Beide
// dürfen nebeneinander installiert sein, ohne Einstellungen, Cookies oder die Sperre der Einzelinstanz zu teilen.
// Muss vor requestSingleInstanceLock stehen, weil die Sperre im userData-Ordner liegt.
if (app.isPackaged) app.setPath("userData", path.join(app.getPath("appData"), "WAMO"));

// Zweite Instanz: sofort zurückkehren, bevor irgendetwas registriert oder gestartet wird. Sonst würde
// sie beim Beenden die Dienste der ersten Instanz stoppen (killStale, stopServices). Die erste Instanz
// holt über "second-instance" ihr Fenster nach vorne.
if (!app.requestSingleInstanceLock()) {
  app.quit();
  return;
}

// Pfade je Form der Installation (installation.js): Entwickler = Repository über desktop/, installiert = resources/
const PFADE = loeseAuf({
  istPaketiert: app.isPackaged,
  resourcesPath: process.resourcesPath,
  verzeichnis: __dirname,
  userData: app.getPath("userData"),
});
let ADRESSEN;
try {
  // Nur http(s) (installiert nur https): bei file:, data: o. ä. wäre der Origin "null", und sameOrigin() ließe jeden solchen zu
  ADRESSEN = adressen(PFADE, process.env);
} catch (err) {
  console.error(err.message);
  app.exit(1);
  return;
}
const UI_URL = ADRESSEN.uiUrl;
const VOICE_CORE_URL = ADRESSEN.kernUrl;
const UI_DIR = PFADE.uiDir;
const UI_ORIGIN = new URL(UI_URL).origin;
const LOG_DIR = PFADE.logDir;
// Die WAMO-CA (feste Prüfsumme, zertifikat.js): Node-Anfragen der App an die Oberfläche und der Prüfer für Chromium
let WAMO_CA = null;
try {
  WAMO_CA = ladeCa(fs.readFileSync(PFADE.caDatei, "utf8"));
} catch (err) {
  console.error(`WAMO-CA nicht geladen (${err.message}); https-Ziele mit der WAMO-CA werden dann nicht vertraut.`);
}
// Installierte App: Python-Umgebung des Hosts einmalig mit dem mitgelieferten uv einrichten (host-umgebung.js)
const hostUmgebung = PFADE.hostUmgebungVerwalten
  ? erzeugeHostUmgebung({
      hostDir: PFADE.hostDir,
      uvDatei: PFADE.uvDatei,
      cacheDir: path.join(app.getPath("userData"), "uv-cache"),
      pythonDir: path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "WAMO", "python"),
      log: (text) => console.log(`[Host-Umgebung] ${text}`),
    })
  : null;
const geraet = erzeugeGeraet({
  repo: PFADE.ressourcen,
  log: (text) => console.log(`[Gerät] ${text}`),
  vorTunnel: hostUmgebung ? () => hostUmgebung.bereit() : undefined,
}); // geraet.js
const LOG_MAX_BYTES = 5 * 1024 * 1024; // je Logdatei höchstens 5 MB
const LOG_DATEIEN = 3; // name.log, name.log.1, name.log.2
const LOG_LEVEL = ["INFO", "WARNING", "ERROR"].includes(process.env.WAMO_LOG_LEVEL) ? process.env.WAMO_LOG_LEVEL : "INFO";

// Chromium-Drosselung für verdeckte/Hintergrund-Fenster aus (die Ursache der Abbrüche)
app.commandLine.appendSwitch("disable-renderer-backgrounding");
app.commandLine.appendSwitch("disable-background-timer-throttling");
app.commandLine.appendSwitch("disable-backgrounding-occluded-windows");
app.commandLine.appendSwitch("disable-features", "CalculateNativeWinOcclusion,IntensiveWakeUpThrottling");
if (process.platform === "win32") app.setAppUserModelId("de.wamocon.wamo");

let mainWin = null;
let tray = null;
let overlays = [];
let borderOn = true;
let vollbild = false; // Vollbild-Programm vorne (vollbild.js, entprellt)
let vollbildWaechter = null;
let quitting = false;
let farben = null; // Randfarben aus der Oberfläche, geprüft (siehe ipcMain "farben")
let energieId = null; // powerSaveBlocker nur während eines Gesprächs
const children = [];
const kinder = {}; // Dienstname -> laufender Kindprozess aus diesem Lauf

// ---------- Einstellungen je Gerät (Betriebsart) ----------
const BETRIEBSARTEN = ["buero", "unterwegs"];
// Standard Unterwegs, bis die Betriebsart Büro abgenommen ist; ohne lokalen Kern (installierte App) gilt nur Büro
let betriebsart = wirksameBetriebsart(undefined, PFADE);

function einstellungsDatei() {
  return path.join(app.getPath("userData"), "einstellungen.json");
}

function ladeEinstellungen() {
  try {
    const roh = JSON.parse(fs.readFileSync(einstellungsDatei(), "utf8"));
    betriebsart = wirksameBetriebsart(roh?.betriebsart, PFADE);
  } catch {
    betriebsart = wirksameBetriebsart(undefined, PFADE); // fehlt oder kaputt: Standard
  }
}

function speichereEinstellungen() {
  const datei = einstellungsDatei();
  const tmp = `${datei}.neu`;
  fs.mkdirSync(path.dirname(datei), { recursive: true });
  fs.writeFileSync(tmp, JSON.stringify({ betriebsart }, null, 2));
  fs.renameSync(tmp, datei); // atomar ersetzen
}

/** Betriebsart umschalten: speichern, lokalen Kern starten oder (nur den eigenen) stoppen, alle informieren. */
async function setzeBetriebsart(art) {
  if (!BETRIEBSARTEN.includes(art)) return betriebsart;
  if (wirksameBetriebsart(art, PFADE) !== art) return betriebsart; // Unterwegs gibt es ohne lokalen Kern nicht
  if (art !== betriebsart) {
    betriebsart = art;
    try {
      speichereEinstellungen();
    } catch (err) {
      console.error(`Einstellungen nicht gespeichert: ${err.message}`);
    }
    // Ein laufendes Gespräch mit dem lokalen Kern nicht abreißen: dann erst nach dem Gespräch stoppen
    if (art === "buero" && energieId === null) stoppeEigenenKern();
    else if (art === "unterwegs") void starteKernWennNoetig();
    aktualisiereTray();
    if (mainWin && !mainWin.isDestroyed()) mainWin.webContents.send("betriebsart", art);
  }
  return betriebsart;
}

/** Nur den Voice-Core beenden, den diese App in diesem Lauf selbst gestartet hat. */
function stoppeEigenenKern() {
  const kind = kinder["voice-core"];
  delete kinder["voice-core"]; // sofort, nicht erst beim exit-Ereignis (sonst startet ein Neustart nicht)
  if (kind && kind.exitCode === null && kind.signalCode === null) killTree(kind.pid);
}

// ---------- Logdateien mit Rotation ----------
class Logdatei {
  constructor(name) {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    this.file = path.join(LOG_DIR, `${name}.log`);
    this.oeffnen();
  }

  oeffnen() {
    this.fd = fs.openSync(this.file, "a");
    this.size = fs.fstatSync(this.fd).size;
    if (this.size > LOG_MAX_BYTES) this.rotieren();
  }

  rotieren() {
    try {
      fs.closeSync(this.fd);
    } catch {
      // schon zu
    }
    // name.log.1 -> name.log.2, name.log -> name.log.1 (die älteste fällt weg)
    for (let i = LOG_DATEIEN - 1; i >= 1; i--) {
      const von = i === 1 ? this.file : `${this.file}.${i - 1}`;
      try {
        if (fs.existsSync(von)) fs.renameSync(von, `${this.file}.${i}`);
      } catch {
        // Datei gerade gesperrt: dann eben beim nächsten Mal
      }
    }
    this.fd = fs.openSync(this.file, "a");
    this.size = 0;
  }

  schreiben(chunk) {
    try {
      fs.writeSync(this.fd, chunk);
      this.size += chunk.length;
      if (this.size > LOG_MAX_BYTES) this.rotieren();
    } catch {
      // Logging darf den Dienst nie stören
    }
  }

  schliessen() {
    try {
      fs.closeSync(this.fd);
    } catch {
      // schon zu
    }
  }
}

// ---------- Dienste (Voice-Core, UI) ----------
// Erwartete Prozesse je Dienst: nur solche werden über eine PID-Datei beendet.
const DIENSTE = {
  "voice-core": { programme: ["uv.exe", "python.exe", "pythonw.exe"], merkmal: "bot.py" },
  ui: { programme: ["node.exe"], merkmal: "next" },
};

/**
 * Erreichbarkeit prüfen; das passende Modul je Protokoll, damit eine https-Adresse den Start nicht
 * abbricht. pruefe(status, body) entscheidet, ob die Antwort vom erwarteten Dienst stammt.
 */
function isUp(url, pruefe) {
  return new Promise((resolve) => {
    let target;
    try {
      target = new URL(url);
    } catch {
      resolve(false);
      return;
    }
    const client = target.protocol === "https:" ? https : target.protocol === "http:" ? http : null;
    if (!client) {
      resolve(false);
      return;
    }
    // https mit geladener WAMO-CA: Die Kette prüft pruefeSocket selbst (siehe zertifikat.js), bevor eine Antwort gelesen wird.
    // Ohne geladene CA bleibt die normale strenge Prüfung von Node, die nur öffentliche CAs kennt.
    const eigenePruefung = target.protocol === "https:" && WAMO_CA;
    let req;
    try {
      req = client.get(target, eigenePruefung ? { rejectUnauthorized: false, agent: false } : {}, (res) => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", (d) => {
          if (body.length < 65536) body += d;
        });
        res.on("end", () => resolve(pruefe(res.statusCode ?? 0, body)));
        res.on("error", () => resolve(false));
      });
    } catch {
      resolve(false);
      return;
    }
    if (eigenePruefung) {
      // Eigene Verbindung (agent: false). Kette nur prüfen, wenn Node sie nicht schon selbst als gültig kennt; bei einem
      // Fehler wird die Anfrage abgebrochen, bevor eine Antwort gelesen wird.
      req.on("socket", (s) =>
        s.once("secureConnect", () => {
          if (!s.authorized && !pruefeSocket(s, target.hostname, WAMO_CA).ok) req.destroy(new Error("Zertifikat nicht vertraut"));
        }),
      );
    }
    req.on("error", () => resolve(false));
    req.setTimeout(2000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

const uiIstDa = () => isUp(UI_URL, (status, body) => status >= 200 && status < 400 && body.includes("WAMO"));
// Der Voice-Core lehnt Anfragen ohne Token mit 401 und "nicht berechtigt" ab: das ist seine Kennung
const coreIstDa = () =>
  isUp(VOICE_CORE_URL, (status, body) => (status >= 200 && status < 400) || (status === 401 && body.includes("nicht berechtigt")));

function pidDatei(name) {
  return path.join(LOG_DIR, `${name}.pid`);
}

function startService(name, command, args, cwd, extraEnv = {}) {
  const log = new Logdatei(name);
  // argv-Liste ohne Shell
  const child = spawn(command, args, {
    cwd,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8", ...extraEnv },
  });
  child.stdout?.on("data", (d) => log.schreiben(d));
  child.stderr?.on("data", (d) => log.schreiben(d));
  const weg = () => {
    const i = children.indexOf(child);
    if (i >= 0) children.splice(i, 1);
    if (kinder[name] === child) delete kinder[name];
    log.schliessen();
    fs.rmSync(pidDatei(name), { force: true });
  };
  child.on("error", (err) => {
    console.error(`${name} konnte nicht starten: ${err.message}`);
    log.schreiben(`[WAMO-Desktop] Start fehlgeschlagen: ${err.message}\n`);
    weg();
  });
  child.on("exit", (code) => {
    console.log(`${name} beendet (${code})`);
    weg();
  });
  children.push(child);
  kinder[name] = child;
  // PID-Datei nur mit echter PID; dazu Programm und Startzeit, damit ein späterer Start nur genau
  // diesen Prozess beendet und nie einen fremden mit wiederverwendeter PID
  if (child.pid) {
    const eintrag = { pid: child.pid, startedAt: Date.now(), dienst: name };
    fs.writeFileSync(pidDatei(name), JSON.stringify(eintrag));
  }
  return child;
}

// Ganzen Prozessbaum beenden: "uv run" startet Python als Kindprozess; nur uv zu beenden ließ den
// alten Voice-Core weiterlaufen (Port 7860 belegt, neue Version wurde nie geladen).
function killTree(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return;
  // voller Pfad: nie ein gleichnamiges Programm aus dem Arbeitsordner oder PATH starten
  const taskkill = path.win32.join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe");
  spawnSync(taskkill, ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
}

/** Name, Pfad, Befehlszeile und Startzeit eines Prozesses (Windows, über CIM). */
function prozessInfo(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return null;
  const befehl =
    `Get-CimInstance Win32_Process -Filter "ProcessId=${pid}" | ForEach-Object { ` +
    "[pscustomobject]@{ Name = $_.Name; Pfad = $_.ExecutablePath; Befehl = $_.CommandLine; " +
    "Start = $_.CreationDate.ToUniversalTime().ToString('o') } } | ConvertTo-Json -Compress";
  const r = spawnSync(powershellPfad(), ["-NoProfile", "-NonInteractive", "-Command", befehl], {
    windowsHide: true,
    encoding: "utf8",
    timeout: 10000,
  });
  if (r.status !== 0 || !r.stdout.trim()) return null;
  try {
    return JSON.parse(r.stdout);
  } catch {
    return null;
  }
}

/** Verwaisten Dienst eines früheren Laufs beenden, aber nur wenn er sicher unserer ist. */
function killStale(name) {
  const datei = pidDatei(name);
  if (!fs.existsSync(datei)) return;
  let eintrag = null;
  try {
    eintrag = JSON.parse(fs.readFileSync(datei, "utf8"));
  } catch {
    eintrag = null; // alte Form (nur Zahl) oder kaputt: nicht prüfbar, also nicht beenden
  }
  fs.rmSync(datei, { force: true });
  const erwartet = DIENSTE[name];
  if (!eintrag || !erwartet || !Number.isInteger(eintrag.pid) || !Number.isFinite(eintrag.startedAt)) return;
  const info = prozessInfo(eintrag.pid);
  if (!info) return; // läuft nicht mehr
  const programm = String(info.Name ?? "").toLowerCase();
  const befehl = String(info.Befehl ?? "").toLowerCase();
  const start = Date.parse(info.Start ?? "");
  const passt =
    erwartet.programme.includes(programm) &&
    befehl.includes(erwartet.merkmal) &&
    Number.isFinite(start) &&
    Math.abs(start - eintrag.startedAt) < 15000;
  if (passt) killTree(eintrag.pid);
  else console.warn(`${name}: PID ${eintrag.pid} gehört nicht (mehr) zu WAMO, nicht beendet`);
}

function stopServices() {
  for (const c of children.splice(0)) {
    // nur noch laufende eigene Kindprozesse (Handle aus diesem Lauf, keine PID-Datei nötig)
    if (c.exitCode === null && c.signalCode === null) killTree(c.pid);
  }
  for (const name of Object.keys(DIENSTE)) fs.rmSync(pidDatei(name), { force: true });
}

/** Neueste Änderungszeit unter einem Pfad (für "Build veraltet?"). */
function neuesteAenderung(p) {
  let neueste = 0;
  let stat;
  try {
    stat = fs.statSync(p);
  } catch {
    return 0;
  }
  if (!stat.isDirectory()) return stat.mtimeMs;
  for (const e of fs.readdirSync(p, { withFileTypes: true })) {
    if (e.name === "node_modules" || e.name.startsWith(".")) continue;
    neueste = Math.max(neueste, neuesteAenderung(path.join(p, e.name)));
  }
  return neueste;
}

function buildVeraltet() {
  const buildId = path.join(UI_DIR, ".next", "BUILD_ID");
  if (!fs.existsSync(buildId)) return true;
  const gebaut = fs.statSync(buildId).mtimeMs;
  const quellen = ["src", "public", "package.json", "package-lock.json", "next.config.ts", ".env.local"].map((q) =>
    neuesteAenderung(path.join(UI_DIR, q)),
  );
  return Math.max(...quellen) > gebaut;
}

function warteAufEnde(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) resolve(child.exitCode);
    child.once("exit", (code) => resolve(code));
    child.once("error", () => resolve(-1));
  });
}

function istLoopback(url) {
  try {
    return ["localhost", "127.0.0.1", "[::1]"].includes(new URL(url).hostname);
  } catch {
    return false;
  }
}

/** Lokalen Voice-Core starten, aber nur in "unterwegs" und nur, wenn nicht schon einer läuft. */
async function starteKernWennNoetig() {
  if (!PFADE.lokalerKernMoeglich || process.env.WAMO_MANAGE_SERVICES === "off" || betriebsart !== "unterwegs" || kinder["voice-core"]) return;
  if (await coreIstDa()) return;
  const uv = path.join(os.homedir(), ".local", "bin", "uv.exe");
  startService("voice-core", fs.existsSync(uv) ? uv : "uv", ["run", "bot.py"], PFADE.voiceCoreDir, {
    WAMO_LOG_LEVEL: LOG_LEVEL,
  });
}

async function ensureServices() {
  if (process.env.WAMO_MANAGE_SERVICES === "off") return;
  // Reste eines früheren Laufs dieser App beenden (nur wenn sicher unsere), damit der aktuelle Code läuft
  killStale("voice-core");
  await new Promise((r) => setTimeout(r, 800));
  // Büro: Der Kern läuft auf Sokrates-2, lokal wird keiner gestartet (und auf keinen gewartet)
  await starteKernWennNoetig();
  // Die UI nur dann selbst starten, wenn sie auf diesem Rechner laufen soll
  if (UI_DIR && istLoopback(UI_URL) && !(await uiIstDa())) {
    killStale("ui");
    const nextBin = path.join(UI_DIR, "node_modules", "next", "dist", "bin", "next");
    const port = new URL(UI_URL).port || "3000";
    if (process.env.WAMO_UI_DEV === "1") {
      startService("ui", "node", [nextBin, "dev", "-H", "127.0.0.1", "-p", port], UI_DIR);
    } else {
      if (buildVeraltet()) {
        zeigeStart("bauen");
        const build = startService("ui-build", "node", [nextBin, "build"], UI_DIR);
        await warteAufEnde(build);
      }
      startService("ui", "node", [nextBin, "start", "-H", "127.0.0.1", "-p", port], UI_DIR);
    }
  }
  for (let i = 0; i < 45; i++) {
    if ((await uiIstDa()) && (betriebsart !== "unterwegs" || (await coreIstDa()))) return;
    await new Promise((r) => setTimeout(r, 2000));
  }
}

// ---------- Fenster ----------
function sameOrigin(url) {
  try {
    return new URL(url).origin === UI_ORIGIN;
  } catch {
    return false;
  }
}

/** Lade- oder Fehlerseite im Hauptfenster (lokale Datei, eigene CSP). */
function zeigeStart(zustand, code = "") {
  if (!mainWin || mainWin.isDestroyed()) return;
  void mainWin.loadFile(path.join(__dirname, "start.html"), { query: { zustand, ziel: UI_URL, code } });
}

function createMainWindow() {
  mainWin = new BrowserWindow({
    width: 1400,
    height: 900,
    title: "WAMO",
    icon: iconPath(),
    autoHideMenuBar: true,
    backgroundColor: "#FFFFFF",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webviewTag: false,
      backgroundThrottling: false,
    },
  });
  const wc = mainWin.webContents;

  // Fremde Seiten nie im WAMO-Fenster: externe Links gehen in den normalen Browser
  wc.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (event, url) => {
    if (!sameOrigin(url)) event.preventDefault();
  });
  wc.on("will-redirect", (event, url) => {
    if (!sameOrigin(url)) event.preventDefault();
  });
  wc.on("will-frame-navigate", (event) => {
    if (!sameOrigin(event.url)) event.preventDefault();
  });
  // Ladefehler der Oberfläche: Fehlerseite mit "Wiederholen" statt eines leeren Fensters
  wc.on("did-fail-load", (_event, code, beschreibung, url, isMainFrame) => {
    if (!isMainFrame || code === -3 || !sameOrigin(url)) return; // -3 = abgebrochen
    zeigeStart("fehler", beschreibung);
  });
  // Mikrofon-Meldungen der eigenen Oberfläche ("[WAMO] ...") mitschreiben: Gerät, Stille, Fehler beim Öffnen
  const logRate = ratenGrenze(30, 10_000); // höchstens 30 Zeilen je 10 s aus der Oberfläche
  wc.on("console-message", (event) => {
    if (!sameOrigin(event?.frame?.url ?? wc.getURL())) return;
    const zeile = oberflaechenZeile(event?.level, event?.message);
    if (zeile && logRate()) schreibeLog("oberflaeche", zeile);
  });

  mainWin.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      mainWin.hide();
    }
  });
  // Windows meldet sich ab oder fährt herunter: Dienste sauber beenden
  mainWin.on("session-end", () => {
    quitting = true;
    stopServices();
  });
  zeigeStart("laden");
}

// Schleierbreite in geräteunabhängigen Pixeln: 96 DIP entsprechen nominal 2,54 cm (Windows rechnet
// Skalierung schon ein). Standard 4,55 cm; WAMO_VEIL_CM ändert das (2 bis 12 cm).
function veilWidthDip() {
  const cm = Math.min(12, Math.max(2, Number(process.env.WAMO_VEIL_CM) || 4.55));
  return Math.round((cm * 96) / 2.54);
}

function createOverlays() {
  overlays.forEach((w) => !w.isDestroyed() && w.destroy());
  overlays = screen.getAllDisplays().map((display) => {
    const { x, y, width, height } = display.bounds;
    const win = new BrowserWindow({
      x, y, width, height,
      transparent: true,
      frame: false,
      resizable: false,
      movable: false,
      focusable: false,
      skipTaskbar: true,
      hasShadow: false,
      alwaysOnTop: true,
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "overlay-preload.js"),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        webviewTag: false,
        backgroundThrottling: false,
      },
    });
    win.setIgnoreMouseEvents(true); // Klicks gehen durch auf darunterliegende Programme
    win.setAlwaysOnTop(true, "screen-saver");
    win.setVisibleOnAllWorkspaces(true);
    // Der Rand lädt nur seine lokale Datei, nie etwas anderes
    win.webContents.on("will-navigate", (event) => event.preventDefault());
    win.webContents.on("will-redirect", (event) => event.preventDefault());
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("did-finish-load", () => {
      if (farben) win.webContents.send("farben", farben);
    });
    void win.loadFile(path.join(__dirname, "overlay.html"), { query: { veil: String(veilWidthDip()) } });
    win.once("ready-to-show", () => {
      if (randSoll()) win.showInactive();
    });
    return win;
  });
}

/** Soll der Rand zu sehen sein? Tray-Haken an und kein Vollbild-Programm vorne. */
function randSoll() {
  return borderOn && !vollbild;
}

/** Gemeinsamer Weg für Tray-Haken und Vollbild-Zustand: alle Rand-Fenster zeigen oder ausblenden. */
function randSichtbarkeit() {
  const zeigen = randSoll();
  for (const w of overlays) {
    if (w.isDestroyed()) continue;
    if (zeigen) w.showInactive();
    else w.hide();
  }
}

// Bildschirmwechsel kommen oft in Schüben (Dock, Skalierung): Ränder erst nach 300 ms Ruhe neu bauen
let overlayTimer = null;
function overlaysSpaeter() {
  if (overlayTimer) clearTimeout(overlayTimer);
  overlayTimer = setTimeout(() => {
    overlayTimer = null;
    createOverlays();
  }, 300);
}

// Nicht gepackte App: Windows muss electron.exe MIT dem App-Ordner starten
function loginItem() {
  return app.isPackaged ? {} : { path: process.execPath, args: [__dirname] };
}

function raisePriority() {
  for (const m of app.getAppMetrics()) {
    try {
      os.setPriority(m.pid, os.constants.priority.PRIORITY_ABOVE_NORMAL);
    } catch {
      // Prozess schon weg oder keine Rechte: egal
    }
  }
}

// App-Symbol aus dem WMC-Logo (desktop/assets): unter Windows .ico (mehrere Größen), sonst PNG
function iconPath() {
  return path.join(__dirname, "assets", process.platform === "win32" ? "wamo.ico" : "wamo.png");
}

function showMain() {
  if (!mainWin) return;
  mainWin.show();
  mainWin.focus();
}

function createTray() {
  tray = new Tray(nativeImage.createFromPath(iconPath()));
  aktualisiereTray();
  tray.on("click", showMain);
}

/** Tray-Menü (neu aufgebaut, wenn sich die Betriebsart ändert). */
function aktualisiereTray() {
  if (!tray) return;
  tray.setToolTip(betriebsart === "buero" ? "WAMO (Büro, Kern auf Sokrates-2)" : "WAMO (Unterwegs, Kern auf diesem PC)");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "WAMO öffnen", click: showMain },
      {
        label: "Betriebsart",
        submenu: [
          {
            label: "Büro (Kern auf Sokrates-2)",
            type: "radio",
            checked: betriebsart === "buero",
            click: () => void setzeBetriebsart("buero"),
          },
          {
            // Ohne lokalen Kern (installierte App) ehrlich ausgegraut statt still wirkungslos
            label: PFADE.lokalerKernMoeglich ? "Unterwegs (Kern auf diesem PC)" : "Unterwegs (in dieser Installation nicht verfügbar)",
            type: "radio",
            enabled: PFADE.lokalerKernMoeglich,
            checked: betriebsart === "unterwegs",
            click: () => void setzeBetriebsart("unterwegs"),
          },
        ],
      },
      {
        label: "Stimmen-Rand",
        type: "checkbox",
        checked: borderOn,
        click: (item) => {
          borderOn = item.checked;
          randSichtbarkeit();
        },
      },
      {
        label: "Mit Windows starten",
        type: "checkbox",
        checked: app.getLoginItemSettings(loginItem()).openAtLogin,
        click: (item) => app.setLoginItemSettings({ ...loginItem(), openAtLogin: item.checked }),
      },
      { type: "separator" },
      {
        label: "Voice-Core neu starten",
        visible: betriebsart === "unterwegs", // im Büro läuft der Kern als Dienst auf Sokrates-2
        click: async () => {
          stoppeEigenenKern();
          killStale("voice-core");
          await ensureServices();
          mainWin?.reload();
        },
      },
      { label: "WAMO beenden", click: () => { quitting = true; app.quit(); } },
    ]),
  );
}

// ---------- Brücke von der Oberfläche zu den Rand-Fenstern ----------
// Nur das Hauptfenster mit der eigenen Oberfläche darf senden.
function fromMainUi(event) {
  return Boolean(mainWin) && event.sender === mainWin.webContents && sameOrigin(event.senderFrame?.url ?? "");
}

// Betriebsart: nur das Hauptfenster mit der eigenen Oberfläche, nur die zwei bekannten Werte (keine Adressen)
ipcMain.handle("betriebsart:lesen", (event) => (fromMainUi(event) ? betriebsart : null));
ipcMain.handle("betriebsart:setzen", async (event, art) => {
  if (!fromMainUi(event) || !BETRIEBSARTEN.includes(art)) return betriebsart;
  return setzeBetriebsart(art);
});
// Rechnername dieses PCs: die Oberfläche schaltet die PC-Steuerung im Büro nur automatisch ein, wenn der Kern
// genau diesen PC steuert
ipcMain.handle("rechnername", (event) => (fromMainUi(event) ? os.hostname() : null));
// Windows-Datenschutz für das Mikrofon: nur einer von fünf festen Werten, nur für das Hauptfenster (berechtigungen.js)
function mikrofonStatus() {
  try {
    return mikrofonStatusWert(systemPreferences.getMediaAccessStatus("microphone"));
  } catch {
    return "unknown";
  }
}
ipcMain.handle("mikrofon:status", (event) => (fromMainUi(event) ? mikrofonStatus() : null));

// Einrichtung des Laptops (geraet.js): nur das Hauptfenster mit der eigenen Oberfläche und nur in der Betriebsart Büro.
// Der private Geräteschlüssel bleibt in diesem Prozess; signiert wird nur die feste Bindungsnachricht. Fehler kommen als
// { ok: false, text } mit einem Text, den die Oberfläche anzeigen darf.
function geraetHandler(arbeit, nurBuero = true) {
  return async (event, ...args) => {
    if (!fromMainUi(event)) return { ok: false, text: "Nicht erlaubt." };
    if (nurBuero && betriebsart !== "buero") {
      return { ok: false, text: "Die Einrichtung des Laptops läuft nur in der Betriebsart Büro." };
    }
    try {
      return await arbeit(...args);
    } catch (err) {
      if (err instanceof GeraetFehler) return { ok: false, text: err.text };
      console.error(`Gerät: ${err.message}`);
      return { ok: false, text: "Die Einrichtung des Laptops ist fehlgeschlagen." };
    }
  };
}
ipcMain.handle("geraet:vorbereiten", geraetHandler(async () => ({ ok: true, ...(await geraet.vorbereiten()) })));
ipcMain.handle(
  "geraet:signieren",
  geraetHandler(async (anfrage) => ({
    ok: true,
    signatur: await geraet.signiere({ nonce: anfrage?.nonce, userId: anfrage?.userId }),
  })),
);
ipcMain.handle("geraet:aktivieren", geraetHandler((antwort) => geraet.aktivieren(antwort)));
ipcMain.handle("geraet:status", geraetHandler(async () => ({ ok: true, ...geraet.status() }), false));

// Zustände, die der Rand kennt (alles andere wird "idle")
const ZUSTAENDE = new Set(["offline", "idle", "listening", "thinking", "speaking", "acting", "approval"]);

/** Energiesparschutz nur während eines Gesprächs (Zustand nicht offline). */
function energieschutz(an) {
  if (an && energieId === null) energieId = powerSaveBlocker.start("prevent-app-suspension");
  if (!an && energieId !== null) {
    if (powerSaveBlocker.isStarted(energieId)) powerSaveBlocker.stop(energieId);
    energieId = null;
    // Während des Gesprächs auf Büro umgeschaltet: den eigenen lokalen Kern jetzt beenden
    if (betriebsart === "buero") stoppeEigenenKern();
  }
}

ipcMain.on("levels", (event, sample) => {
  if (!fromMainUi(event)) return;
  const s = {
    user: clamp(Number(sample?.user)),
    bot: clamp(Number(sample?.bot)),
    cut: Boolean(sample?.cut),
    speaker: Math.max(0, Math.min(7, Math.trunc(Number(sample?.speaker)) || 0)),
    zustand: ZUSTAENDE.has(sample?.zustand) ? sample.zustand : "idle",
  };
  energieschutz(s.zustand !== "offline");
  if (!borderOn) return; // Rand ausgeschaltet: nichts weiterreichen
  for (const w of overlays) if (!w.isDestroyed()) w.webContents.send("levels", s);
});

// Farben des Randes (einmalig beim Laden der Oberfläche, aus deren CSS-Variablen --overlay-*):
// { wamo: [r,g,b], mensch: [r,g,b], andere: [r,g,b] }
ipcMain.on("farben", (event, value) => {
  if (!fromMainUi(event)) return;
  const geprueft = checkFarben(value);
  if (!geprueft) return;
  farben = geprueft;
  for (const w of overlays) if (!w.isDestroyed()) w.webContents.send("farben", farben);
});

function checkRgb(c) {
  if (!Array.isArray(c) || c.length !== 3) return null;
  const out = c.map((v) => Math.round(Number(v)));
  return out.every((v) => Number.isFinite(v) && v >= 0 && v <= 255) ? out : null;
}

function checkFarben(value) {
  const wamo = checkRgb(value?.wamo);
  const mensch = checkRgb(value?.mensch);
  const andere = checkRgb(value?.andere);
  return wamo && mensch && andere ? { wamo, mensch, andere } : null;
}

function clamp(v) {
  return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0;
}

// Keine Webviews, keine neuen Fenster aus irgendeinem Inhalt
app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (event) => event.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
});

app.on("second-instance", showMain);

/** Berechtigungen nur für das Hauptfenster mit der eigenen Oberfläche. */
function istEigeneUi(wc, url) {
  return Boolean(wc) && Boolean(mainWin) && wc === mainWin.webContents && sameOrigin(url ?? "");
}

// Diagnose der Berechtigungen (logs/berechtigungen.log, siehe berechtigungen.js) und der Mikrofon-Meldungen der
// Oberfläche (logs/oberflaeche.log); die Logdateien entstehen erst beim ersten Eintrag
const BERECHTIGUNG_LOG = process.env.WAMO_BERECHTIGUNG_LOG === "1";
const logs = {};
function schreibeLog(name, zeile) {
  try {
    logs[name] ??= new Logdatei(name);
    logs[name].schreiben(`${zeile}\n`);
  } catch {
    // Diagnose darf Start und Berechtigungen nie stören (schreibgeschützter Ordner o. ä.)
  }
}
function entscheide(art, permission, details, eigene, erlaubt) {
  if (protokollieren(art, permission, erlaubt, BERECHTIGUNG_LOG)) {
    schreibeLog("berechtigungen", protokollZeile(art, permission, details, eigene, erlaubt));
  }
  return erlaubt;
}

app.whenReady().then(async () => {
  // Mikrofon (nur Audio, nie Kamera) und Zwischenablage nur für die eigene Oberfläche, sonst nichts: als Erstes,
  // für die Standard-Session und jede weitere (Partitionen), die später entsteht
  installieren(session.defaultSession, istEigeneUi, entscheide);
  app.on("session-created", (ses) => installieren(ses, istEigeneUi, entscheide));
  // Die WAMO-CA gilt nur hier, nur für den Servernamen der Oberfläche (und des Kerns auf demselben Server)
  const ziel = new URL(UI_URL);
  if (WAMO_CA && ziel.protocol === "https:") {
    const pruefer = erzeugePruefer({
      ca: WAMO_CA,
      erlaubteHosts: [ziel.hostname],
      log: (text) => schreibeLog("oberflaeche", `${new Date().toISOString()} warn [WAMO-Desktop] ${text}`),
    });
    session.defaultSession.setCertificateVerifyProc(pruefer);
    app.on("session-created", (ses) => ses.setCertificateVerifyProc(pruefer));
  }
  ladeEinstellungen(); // Betriebsart vor dem Start der Dienste (Büro: kein lokaler Kern)
  schreibeLog("oberflaeche", `${new Date().toISOString()} info [WAMO-Desktop] Windows-Mikrofonzugriff: ${mikrofonStatus()}`);
  // Audio bekommt Vorrang, wenn Chrome & Co. beim Öffnen kurz die CPU belegen (Aussetzer)
  setInterval(raisePriority, 5000);
  createTray();
  createMainWindow(); // sofort mit Ladeseite, nicht erst nach dem Hochfahren der Dienste
  // Installierte App: Python-Umgebung des Hosts im Hintergrund einrichten (einmalig); bis sie steht, bleibt der Host aus
  hostUmgebung?.bereit().catch((err) => schreibeLog("oberflaeche", `${new Date().toISOString()} warn [WAMO-Desktop] ${err.text ?? err.message}`));
  await ensureServices();
  if (await uiIstDa()) void mainWin.loadURL(UI_URL);
  else zeigeStart("fehler", "Zeitüberschreitung beim Start");
  createOverlays();
  if (process.platform === "win32") {
    const vollbildLog = new Logdatei("vollbild");
    vollbildWaechter = starteVollbildWaechter({
      pid: process.pid,
      onWechsel: (an) => {
        vollbild = an;
        randSichtbarkeit();
      },
      log: (text) => vollbildLog.schreiben(`${new Date().toISOString()} ${text}\n`),
      rohwerte: process.env.WAMO_VOLLBILD_LOG === "1", // Rohwerte von SHQueryUserNotificationState mitschreiben
    });
  }
  screen.on("display-added", overlaysSpaeter);
  screen.on("display-removed", overlaysSpaeter);
  screen.on("display-metrics-changed", overlaysSpaeter);
  if (process.platform === "win32") geraet.starteWaechter(); // Tunnel-Aufgabe anstoßen, wenn der Überwacher schweigt
});

app.on("before-quit", () => {
  quitting = true;
  geraet.stoppe();
  vollbildWaechter?.stop();
  vollbildWaechter = null;
  energieschutz(false);
  stopServices();
});

app.on("window-all-closed", (e) => e.preventDefault()); // lebt im Tray weiter
