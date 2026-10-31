// WAMO-Desktop: Pfade und Betriebsarten je nach Form der Installation (reines Node-Modul, ohne electron-Import, testbar).
//
// Zwei Formen:
//   entwickler   nicht paketiert (npm start, scripts\wamo-desktop.cmd): Das Repository liegt eine Ebene über desktop/.
//                Host, Skripte, Oberfläche und Voice-Core kommen von dort; Logs liegen in desktop/logs.
//   installiert  Installer-Paket (npm run dist): Host und Skripte liegen als extraResources unter dem Installationsordner
//                (resources/host, resources/scripts), dazu die WAMO-CA und uv. Es gibt keinen lokalen Voice-Core und keinen
//                Oberflächen-Build; die App lädt die Büro-Oberfläche auf Sokrates-2. Das Programm selbst liegt in einer
//                asar-Datei und ist schreibgeschützt gedacht, deshalb gehören Logs nach userData.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const BUERO_UI_URL = "https://192.168.178.136:55445";
const BUERO_KERN_URL = "https://192.168.178.136:55444";
const LOKALE_UI_URL = "http://localhost:3000";
const LOKALER_KERN_URL = "http://localhost:7860";
const BETRIEBSARTEN = ["buero", "unterwegs"];

/**
 * Löst alle Ordner und Dateien auf, die main.js braucht.
 * Optionen: istPaketiert (app.isPackaged), resourcesPath (process.resourcesPath), verzeichnis (__dirname von main.js),
 * userData (app.getPath("userData")), existiert (Dateiprüfung, für Tests einstellbar).
 */
function loeseAuf({ istPaketiert, resourcesPath, verzeichnis, userData, existiert = fs.existsSync }) {
  if (istPaketiert) {
    if (typeof resourcesPath !== "string" || resourcesPath === "") throw new Error("resourcesPath fehlt");
    if (typeof userData !== "string" || userData === "") throw new Error("userData fehlt");
    return {
      modus: "installiert",
      repo: null,
      ressourcen: resourcesPath,
      uiDir: null,
      voiceCoreDir: null,
      logDir: path.join(userData, "logs"),
      caDatei: path.join(resourcesPath, "wamo-ca.crt"),
      uvDatei: path.join(resourcesPath, "uv", "uv.exe"),
      hostDir: path.join(resourcesPath, "host"),
      lokalerKernMoeglich: false,
      hostUmgebungVerwalten: true,
    };
  }
  const repo = path.resolve(verzeichnis, "..");
  const voiceCoreDir = path.join(repo, "voice-core");
  return {
    modus: "entwickler",
    repo,
    ressourcen: repo,
    uiDir: path.join(repo, "ui"),
    voiceCoreDir,
    logDir: path.join(verzeichnis, "logs"),
    caDatei: path.join(repo, "infra", "sokrates2", "wamo-ca.crt"),
    uvDatei: null,
    hostDir: path.join(repo, "host"),
    lokalerKernMoeglich: existiert(path.join(voiceCoreDir, "bot.py")),
    hostUmgebungVerwalten: false,
  };
}

/** Adressen der Oberfläche und des lokalen Kerns. Installiert: nur die Büro-Oberfläche, nur https. */
function adressen(pfade, env = {}) {
  const installiert = pfade.modus === "installiert";
  // Installiert sind die Adressen fest: Die Umgebung ändert nichts, denn WAMO_UI_URL bestimmt, welche Seite im Fenster mit
  // Mikrofonfreigabe und Brücke zur App läuft und für welchen Server die WAMO-CA gilt
  if (installiert) return { uiUrl: BUERO_UI_URL, kernUrl: BUERO_KERN_URL };
  const uiUrl = env.WAMO_UI_URL || LOKALE_UI_URL;
  const kernUrl = env.WAMO_VOICE_CORE_URL || LOKALER_KERN_URL;
  let ziel;
  try {
    ziel = new URL(uiUrl);
  } catch {
    throw new Error("WAMO_UI_URL ist keine gültige Adresse.");
  }
  // Nur http(s): bei file:, data: o. ä. wäre der Origin "null", und der Origin-Vergleich ließe jeden solchen Origin zu
  if (!/^https?:$/.test(ziel.protocol)) throw new Error("WAMO_UI_URL muss eine http- oder https-Adresse sein.");
  return { uiUrl, kernUrl };
}

/** Die Betriebsart, die gilt: ohne lokalen Kern immer Büro, sonst der Wunsch (Standard Unterwegs im Entwicklermodus). */
function wirksameBetriebsart(wunsch, pfade) {
  if (!pfade.lokalerKernMoeglich) return "buero";
  return BETRIEBSARTEN.includes(wunsch) ? wunsch : "unterwegs";
}

module.exports = { BETRIEBSARTEN, BUERO_KERN_URL, BUERO_UI_URL, LOKALE_UI_URL, LOKALER_KERN_URL, adressen, loeseAuf, wirksameBetriebsart };
