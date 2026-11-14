// WAMO-Desktop: Python-Umgebung des PC-Hosts in der installierten App (reines Node-Modul, ohne electron-Import, testbar).
//
// Der Installer bringt host/ (ohne .venv und Tests) und uv.exe mit. Die Umgebung entsteht beim ersten Start der App, nicht im
// Installer: `uv sync --locked --no-dev` liest host/uv.lock. Darin steht für jedes Paket der SHA-256 der Datei; uv bricht ab,
// wenn ein Paket nicht passt. Der Python-Interpreter kommt (falls nötig) von uv selbst, dessen Downloads ebenfalls gegen
// fest eingebaute Prüfsummen laufen. Ohne Netz und ohne vorhandene Umgebung bleibt der Host aus, die App meldet es ehrlich.
// Das Ergebnis liegt in host/.venv, genau dort, wo scripts/wamo-tunnel.ps1 es erwartet.
"use strict";

const crypto = require("node:crypto");
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const PYTHON_VERSION = "3.11"; // wie die getestete Entwicklungsumgebung des Hosts
const FRIST_MS = 10 * 60 * 1000;
const MARKE = "wamo-umgebung.json";

class HostUmgebungFehler extends Error {
  constructor(text) {
    super(text);
    this.name = "HostUmgebungFehler";
    this.text = text;
  }
}

function standardAusfuehren(datei, args, optionen) {
  return new Promise((resolve, reject) => {
    execFile(
      datei,
      args,
      { cwd: optionen.cwd, env: optionen.env, windowsHide: true, timeout: optionen.frist, encoding: "utf8", maxBuffer: 4 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err) {
          err.stdout = stdout;
          err.stderr = stderr;
          reject(err);
        } else {
          resolve({ stdout, stderr });
        }
      },
    );
  });
}

/** Umgebung für uv: eigene Ordner, keine fremde Konfiguration, keine Hardlinks in den Cache. */
function uvUmgebung(basis, { cacheDir, pythonDir }) {
  const env = { ...basis };
  for (const name of Object.keys(env)) {
    // nichts erben, was Python oder uv umlenkt (Proxy- und Zertifikatsvariablen bleiben für Firmennetze)
    if (/^(UV_|PYTHON)/i.test(name) || /^VIRTUAL_ENV$/i.test(name)) delete env[name];
  }
  return {
    ...env,
    PYTHONNOUSERSITE: "1",
    UV_CACHE_DIR: cacheDir,
    UV_PYTHON_INSTALL_DIR: pythonDir,
    UV_LINK_MODE: "copy",
    UV_NO_CONFIG: "1",
    UV_NO_PROGRESS: "1",
    PYTHONUTF8: "1",
  };
}

/**
 * Optionen: hostDir, uvDatei, cacheDir, pythonDir (alle absolut); env (Standard process.env), ausfuehren, fs, log.
 * bereit() liefert ein Versprechen, das erfüllt ist, wenn host/.venv zur Sperrdatei passt. Gleichzeitige Aufrufe teilen sich
 * einen Lauf; nach einem Fehler startet der nächste Aufruf neu.
 */
function erzeugeHostUmgebung(opt) {
  const dsys = opt.fs ?? fs;
  const ausfuehren = opt.ausfuehren ?? standardAusfuehren;
  const log = opt.log ?? (() => {});
  const lockDatei = path.join(opt.hostDir, "uv.lock");
  const venvOrdner = path.join(opt.hostDir, ".venv");
  const pythonDatei = path.join(venvOrdner, "Scripts", "python.exe");
  const markeDatei = path.join(venvOrdner, MARKE);
  let lauf = null;
  let letzterFehler = null;

  function lockHash() {
    try {
      return crypto.createHash("sha256").update(dsys.readFileSync(lockDatei)).digest("hex");
    } catch {
      throw new HostUmgebungFehler("Die Sperrdatei des Hosts (uv.lock) fehlt in der Installation.");
    }
  }

  function istAktuell(hash) {
    try {
      if (!dsys.existsSync(pythonDatei)) return false;
      const marke = JSON.parse(dsys.readFileSync(markeDatei, "utf8"));
      return marke?.lock === hash && marke?.python === PYTHON_VERSION;
    } catch {
      return false;
    }
  }

  async function einrichten() {
    const hash = lockHash();
    if (istAktuell(hash)) return;
    if (!dsys.existsSync(opt.uvDatei)) throw new HostUmgebungFehler("Das Programm uv fehlt in der Installation. Bitte W.A.I.S. neu installieren.");
    log("Python-Umgebung des Hosts wird eingerichtet (einmalig, braucht Netz)");
    dsys.mkdirSync(opt.cacheDir, { recursive: true });
    dsys.mkdirSync(opt.pythonDir, { recursive: true });
    // --no-build: nur fertige Wheels mit Prüfsumme aus der Sperrdatei, nie Quelltext bauen (die Build-Werkzeuge hätten keine
    // Prüfsumme). --managed-python: ein von uv geladener Interpreter (Prüfsumme in uv), nicht irgendein Python aus PATH oder
    // Registry, das später verschwinden oder verändert werden kann.
    const args = ["sync", "--locked", "--no-dev", "--no-build", "--managed-python", "--python", PYTHON_VERSION, "--project", opt.hostDir];
    try {
      await ausfuehren(opt.uvDatei, args, {
        cwd: opt.hostDir,
        env: uvUmgebung(opt.env ?? process.env, opt),
        frist: opt.frist ?? FRIST_MS,
      });
    } catch (err) {
      // Zugangsdaten in Adressen (https://nutzer:token@host) nie ins Protokoll
      const ende = String(err?.stderr ?? "").trim().split(/\r?\n/).slice(-6).join(" | ").replace(/:\/\/[^@\s/]+@/g, "://***@");
      log(`uv sync fehlgeschlagen (${err?.killed ? "Zeitüberschreitung" : `Code ${err?.code ?? "?"}`}): ${ende}`);
      throw new HostUmgebungFehler(
        err?.killed
          ? "Die Einrichtung der Python-Umgebung des Hosts dauert zu lange."
          : "Die Python-Umgebung des Hosts konnte nicht eingerichtet werden. Bitte die Netzverbindung prüfen und W.A.I.S. neu starten.",
      );
    }
    if (!dsys.existsSync(pythonDatei)) throw new HostUmgebungFehler("Die Python-Umgebung des Hosts ist nach der Einrichtung unvollständig.");
    dsys.writeFileSync(markeDatei, JSON.stringify({ lock: hash, python: PYTHON_VERSION }));
    log("Python-Umgebung des Hosts ist bereit");
  }

  function bereit() {
    if (!lauf) {
      lauf = einrichten().then(
        () => {
          letzterFehler = null;
        },
        (err) => {
          letzterFehler = err instanceof HostUmgebungFehler ? err : new HostUmgebungFehler("Die Python-Umgebung des Hosts ist nicht bereit.");
          lauf = null; // nächster Aufruf versucht es erneut
          throw letzterFehler;
        },
      );
    }
    return lauf;
  }

  /** "bereit" | "fehlt" | "fehler" ohne etwas zu starten (für Anzeige und Tests). */
  function status() {
    try {
      if (istAktuell(lockHash())) return "bereit";
    } catch {
      return "fehler";
    }
    return letzterFehler ? "fehler" : "fehlt";
  }

  return { bereit, status, pfade: { lockDatei, venvOrdner, pythonDatei, markeDatei } };
}

module.exports = { FRIST_MS, HostUmgebungFehler, PYTHON_VERSION, erzeugeHostUmgebung, uvUmgebung };
