// Vollbild-Wächter für den Stimmen-Rand (nur Windows, Hauptprozess der Desktop-App).
//
// Bei Spielen, Vollbild-Videos und Präsentationen stört der Rand und kann Spiele aus dem schnellen
// Darstellungsmodus drängen. Windows meldet den Zustand über SHQueryUserNotificationState (shell32):
//   1 kein Benutzer, 2 Vollbild-Programm (BUSY), 3 Direct3D-Vollbild (Spiele), 4 Präsentationsmodus,
//   5 Benachrichtigungen erlaubt (normal), 6 ruhige Zeit nach der Einrichtung, 7 Store-App läuft (APP).
// Ausgeblendet wird bei 2, 3 und 4. Wert 7 zählt bewusst nicht: Microsoft beschreibt ihn nur als "eine Store-App
// läuft", nicht als Vollbild. Mitzählen hieße, der Rand verschwände womöglich bei jedem Store-Fenster im Vordergrund
// (Rechner, Einstellungen). Ob Vollbild in Store-Apps 2 oder 3 meldet, ist nicht gemessen: Mit WAMO_VOLLBILD_LOG=1
// schreibt der Wächter jeden Rohwert ins Log (desktop/logs/vollbild.log), damit sich das prüfen lässt.
//
// Electron kann den Wert nicht selbst abfragen. Ein kleiner PowerShell-Wächter (voller Pfad im Windows-Ordner) gibt
// ihn alle 1,5 s aus; der Befehl geht als -EncodedCommand (UTF-16LE, Base64) ohne Quoting-Probleme und ohne
// Skriptdatei. Er wartet über das Prozess-Handle der App (WaitForExit) und endet kurz nach ihr. Stirbt er, startet er
// nach 5 s neu, bei Dauerfehlern mit wachsender Pause (bis 60 s); nach mehreren schnellen Fehlschlägen in Folge gibt
// er auf, schreibt den Grund ins Log und zeigt den Rand wieder (lieber sichtbar als für immer weg). Umgeschaltet wird
// erst nach zwei gleichen Werten hintereinander (kein Flackern). In den Befehl gelangt nur die eigene PID als Ganzzahl.
"use strict";

const { spawn } = require("node:child_process");
const path = require("node:path");

const VOLLBILD_WERTE = new Set([2, 3, 4]);
const ABFRAGE_MS = 1500;
const NEUSTART_MS = 5000;
const NEUSTART_MAX_MS = 60000;
const AUFGEBEN_NACH = 5; // so viele schnelle Fehlschläge in Folge, dann Schluss
const SCHNELL_MS = 10000; // Lauf kürzer als das, ohne gültigen Wert: zählt als Fehlschlag
const MAX_REST = 256; // eine unvollständige Zeile ist höchstens eine Ziffer; mehr ist Unsinn
const MAX_STDERR = 500;

/** Voller Pfad zu Windows PowerShell (nie über PATH oder den aktuellen Ordner suchen). */
function powershellPfad(env = process.env) {
  const windows = env.SystemRoot || env.windir || "C:\\Windows";
  return path.win32.join(windows, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
}

/** PowerShell-Befehl des Wächters für die PID der App (nur Ganzzahl). */
function waechterSkript(pid, abfrageMs = ABFRAGE_MS) {
  if (!Number.isInteger(pid) || pid <= 0) throw new Error("ungültige PID");
  if (!Number.isInteger(abfrageMs) || abfrageMs < 200) throw new Error("ungültiger Abstand");
  return [
    "$ProgressPreference = 'SilentlyContinue'",
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -Namespace WamoRand -Name Shell -MemberDefinition " +
      "'[DllImport(\"shell32.dll\")] public static extern int SHQueryUserNotificationState(out int state);'",
    // Handle statt PID-Abfrage: endet sicher mit der App, auch wenn Windows die PID neu vergibt
    `$app = [System.Diagnostics.Process]::GetProcessById(${pid})`,
    "do { $s = 0; if ([WamoRand.Shell]::SHQueryUserNotificationState([ref]$s) -ne 0) { $s = 0 }; " +
      `[Console]::Out.WriteLine($s); [Console]::Out.Flush() } until ($app.WaitForExit(${abfrageMs}))`,
  ].join("; ");
}

/** Für -EncodedCommand: UTF-16LE, Base64. */
function kodiert(skript) {
  return Buffer.from(skript, "utf16le").toString("base64");
}

/** Schaltet erst um, wenn zwei Werte hintereinander dasselbe sagen (Vollbild ja oder nein). */
class Entpreller {
  constructor(onWechsel) {
    this.onWechsel = onWechsel;
    this.zuletzt = null;
    this.vollbild = false;
  }

  /** true, wenn die Zeile ein gültiger Wert war. */
  wert(zeile) {
    const text = String(zeile).trim();
    if (!/^[0-7]$/.test(text)) return false; // nur die bekannten Werte 0 bis 7
    const busy = VOLLBILD_WERTE.has(Number(text));
    if (busy === this.zuletzt && busy !== this.vollbild) this.setzen(busy);
    this.zuletzt = busy;
    return true;
  }

  setzen(busy) {
    this.vollbild = busy;
    this.onWechsel(busy);
  }
}

/**
 * Startet den Wächter. onWechsel(vollbild) meldet jeden entprellten Wechsel; log(text) bekommt Diagnosen (nie
 * Geheimnisse, der Wächter kennt keine). Rückgabe: { stop(), vollbild }.
 * spawnFn, setTimeoutFn, clearTimeoutFn, jetztFn und env nur für Tests.
 */
function starteVollbildWaechter({
  pid,
  onWechsel,
  log = () => {},
  rohwerte = false,
  spawnFn = spawn,
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
  jetztFn = Date.now,
  env = process.env,
}) {
  const sicherMelden = (an) => {
    try {
      onWechsel(an);
    } catch (err) {
      log(`Vollbild-Wächter: Rückruf fehlgeschlagen (${err.message})`);
    }
  };
  const entpreller = new Entpreller(sicherMelden);
  const programm = powershellPfad(env);
  const argv = ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", kodiert(waechterSkript(pid))];
  let kind = null;
  let timer = null;
  let beendet = false;
  let fehlschlaege = 0;

  const planeNeustart = () => {
    if (beendet || timer) return;
    if (fehlschlaege >= AUFGEBEN_NACH) {
      beendet = true;
      log(`Vollbild-Wächter aufgegeben nach ${fehlschlaege} Fehlschlägen; der Rand bleibt sichtbar`);
      if (entpreller.vollbild) entpreller.setzen(false);
      return;
    }
    const pause = Math.min(NEUSTART_MAX_MS, NEUSTART_MS * 2 ** Math.max(0, fehlschlaege - 1));
    timer = setTimeoutFn(() => {
      timer = null;
      start();
    }, pause);
  };

  const start = () => {
    if (beendet) return;
    let k;
    try {
      k = spawnFn(programm, argv, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"], cwd: path.dirname(programm) });
    } catch (err) {
      fehlschlaege += 1;
      log(`Vollbild-Wächter startet nicht: ${err.message}`);
      planeNeustart();
      return;
    }
    kind = k;
    const seit = jetztFn();
    let gueltig = false;
    let rest = "";
    let fehlertext = "";
    k.stdout?.setEncoding?.("utf8");
    k.stderr?.setEncoding?.("utf8");
    k.stdout?.on("data", (stueck) => {
      if (beendet || kind !== k) return; // nach stop oder von einem alten Kind: nichts mehr melden
      const zeilen = (rest + stueck).split(/\r?\n/);
      rest = zeilen.pop() ?? "";
      if (rest.length > MAX_REST) rest = ""; // nur der unvollständige Rest ist begrenzt
      for (const zeile of zeilen) {
        if (entpreller.wert(zeile)) {
          gueltig = true;
          fehlschlaege = 0; // läuft: die Fehlerzählung beginnt neu
          if (rohwerte) log(`Vollbild-Wächter: Wert ${zeile.trim()}`);
        }
      }
    });
    k.stderr?.on("data", (stueck) => {
      fehlertext = (fehlertext + stueck).slice(-MAX_STDERR);
    });
    let vorbei = false;
    const ende = (grund) => {
      if (vorbei) return;
      vorbei = true;
      if (kind !== k) return; // altes Kind (nach stop oder Neustart): kein zweiter Wächter
      kind = null;
      if (beendet) return;
      if (!gueltig || jetztFn() - seit < SCHNELL_MS) fehlschlaege += 1;
      if (fehlschlaege === 1 || fehlschlaege >= AUFGEBEN_NACH) {
        const text = fehlertext.replace(/\s+/g, " ").trim();
        log(`Vollbild-Wächter beendet (${grund})${text ? `: ${text}` : ""}`);
      }
      planeNeustart();
    };
    k.on("error", (err) => ende(`Fehler ${err.message}`)); // on statt once: ein zweites error darf nicht werfen
    k.once("exit", (code) => ende(`Code ${code}`));
  };

  start();
  return {
    stop() {
      beendet = true;
      if (timer) clearTimeoutFn(timer);
      timer = null;
      const k = kind;
      kind = null;
      try {
        k?.kill();
      } catch {
        // schon beendet
      }
    },
    get vollbild() {
      return entpreller.vollbild;
    },
  };
}

module.exports = {
  ABFRAGE_MS,
  AUFGEBEN_NACH,
  Entpreller,
  NEUSTART_MAX_MS,
  NEUSTART_MS,
  VOLLBILD_WERTE,
  kodiert,
  powershellPfad,
  starteVollbildWaechter,
  waechterSkript,
};
