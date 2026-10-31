// Bereitet den Bau des Windows-Installers vor (läuft vor electron-builder, siehe "npm run dist" in desktop/):
// holt uv (die Python-Verwaltung für den Host) in der in desktop/build/uv-pin.json festgelegten Version, prüft die
// SHA-256-Prüfsumme der Archivdatei VOR dem Auspacken und legt uv.exe samt Lizenztext nach desktop/build-cache/uv.
// Ohne passende Prüfsumme wird nichts ausgepackt und der Bau bricht ab. Lokal ausführen, nie in einer fremden Pipeline.
//
//   node scripts/wamo-dist-vorbereiten.cjs
"use strict";

const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const https = require("node:https");
const path = require("node:path");

const ERLAUBTE_HOSTS = ["github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"];
const MAX_UMLEITUNGEN = 5;
const MAX_BYTES = 200 * 1024 * 1024;
const RE_SHA256 = /^[0-9a-f]{64}$/;

function sha256Datei(datei) {
  return crypto.createHash("sha256").update(fs.readFileSync(datei)).digest("hex");
}

/** Liest und prüft uv-pin.json: https-Adresse auf einem erlaubten Host, genau eine 64-stellige Prüfsumme. */
function ladePin(datei) {
  const pin = JSON.parse(fs.readFileSync(datei, "utf8"));
  if (pin.name !== "uv" || !/^\d+\.\d+\.\d+$/.test(pin.version)) throw new Error("uv-pin.json: Name oder Version ungültig");
  if (typeof pin.sha256 !== "string" || !RE_SHA256.test(pin.sha256)) throw new Error("uv-pin.json: sha256 ungültig");
  const url = new URL(pin.url);
  if (url.protocol !== "https:" || !ERLAUBTE_HOSTS.includes(url.hostname)) throw new Error("uv-pin.json: Adresse nicht erlaubt");
  if (!url.pathname.includes(`/${pin.version}/`) || !url.pathname.endsWith(`/${pin.datei}`)) throw new Error("uv-pin.json: Adresse passt nicht zu Version und Datei");
  if (path.basename(pin.datei) !== pin.datei) throw new Error("uv-pin.json: Dateiname ungültig");
  return pin;
}

/** Lädt eine Datei per https (Umleitungen nur auf erlaubte Hosts, begrenzte Größe) in eine neue Datei. */
function herunterladen(adresse, ziel, umleitungen = 0) {
  return new Promise((resolve, reject) => {
    const url = new URL(adresse);
    if (url.protocol !== "https:" || !ERLAUBTE_HOSTS.includes(url.hostname)) {
      reject(new Error(`Adresse nicht erlaubt: ${url.hostname}`));
      return;
    }
    https
      .get(url, (res) => {
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
          res.resume();
          if (umleitungen >= MAX_UMLEITUNGEN) {
            reject(new Error("zu viele Umleitungen"));
            return;
          }
          herunterladen(new URL(res.headers.location, url).toString(), ziel, umleitungen + 1).then(resolve, reject);
          return;
        }
        if (res.statusCode !== 200) {
          res.resume();
          reject(new Error(`Download fehlgeschlagen (HTTP ${res.statusCode})`));
          return;
        }
        let gesamt = 0;
        const aus = fs.createWriteStream(ziel, { flags: "wx" });
        res.on("data", (d) => {
          gesamt += d.length;
          if (gesamt > MAX_BYTES) res.destroy(new Error("Datei zu groß"));
        });
        res.on("error", (err) => {
          aus.destroy();
          reject(err);
        });
        res.pipe(aus);
        aus.on("finish", () => resolve());
        aus.on("error", reject);
      })
      .on("error", reject);
  });
}

async function bereiteUvVor({ pinDatei, cacheDir, ausgabeDir, lizenzDatei, log = console.log }) {
  const pin = ladePin(pinDatei);
  fs.mkdirSync(cacheDir, { recursive: true });
  const archiv = path.join(cacheDir, `uv-${pin.version}-${pin.datei}`);
  if (fs.existsSync(archiv) && sha256Datei(archiv) !== pin.sha256) {
    log("Zwischengespeichertes Archiv hat die falsche Prüfsumme und wird verworfen.");
    fs.rmSync(archiv);
  }
  if (!fs.existsSync(archiv)) {
    log(`Lade uv ${pin.version} ...`);
    const teil = `${archiv}.teil`;
    fs.rmSync(teil, { force: true });
    await herunterladen(pin.url, teil);
    const ist = sha256Datei(teil);
    if (ist !== pin.sha256) {
      fs.rmSync(teil, { force: true });
      throw new Error(`Prüfsumme von uv stimmt nicht (erwartet ${pin.sha256}, erhalten ${ist}). Nichts wurde ausgepackt.`);
    }
    fs.renameSync(teil, archiv);
  }
  log(`Prüfsumme gültig: ${pin.sha256}`);
  fs.rmSync(ausgabeDir, { recursive: true, force: true });
  fs.mkdirSync(ausgabeDir, { recursive: true });
  // Nur das eine Programm auspacken (tar ist seit Windows 10 dabei), nie das ganze Archiv
  const tar = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe");
  const r = spawnSync(tar, ["-xf", archiv, "-C", ausgabeDir, "uv.exe"], { encoding: "utf8", windowsHide: true });
  const uv = path.join(ausgabeDir, "uv.exe");
  if (r.status !== 0 || !fs.existsSync(uv)) throw new Error(`uv.exe ließ sich nicht auspacken: ${r.stderr || r.error?.message || ""}`);
  const v = spawnSync(uv, ["--version"], { encoding: "utf8", windowsHide: true });
  if (v.status !== 0 || !v.stdout.startsWith(`uv ${pin.version}`)) throw new Error(`uv meldet eine andere Version: ${v.stdout}`);
  fs.copyFileSync(lizenzDatei, path.join(ausgabeDir, "LIZENZ-uv-MIT.txt"));
  fs.writeFileSync(
    path.join(ausgabeDir, "HERKUNFT.txt"),
    `uv ${pin.version} von Astral Software Inc.\nQuelle: ${pin.url}\nSHA-256 des Archivs: ${pin.sha256}\nLizenz: ${pin.lizenz}\n`,
  );
  log(`uv ${pin.version} liegt in ${ausgabeDir}`);
  return pin;
}

module.exports = { ERLAUBTE_HOSTS, bereiteUvVor, ladePin, sha256Datei };

if (require.main === module) {
  const desktop = path.join(__dirname, "..", "desktop");
  bereiteUvVor({
    pinDatei: path.join(desktop, "build", "uv-pin.json"),
    cacheDir: path.join(desktop, "build-cache", "download"),
    ausgabeDir: path.join(desktop, "build-cache", "uv"),
    lizenzDatei: path.join(desktop, "build", "uv-LICENSE-MIT.txt"),
  }).catch((err) => {
    console.error(`Vorbereitung fehlgeschlagen: ${err.message}`);
    process.exit(1);
  });
}
