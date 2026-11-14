"use strict";

/**
 * Absicherung des Taskleistenknopfs des Hauptfensters (nur Windows, reines Node-Modul ohne electron-Import, testbar).
 *
 * Befund: Startet der Installer die App (runAfterFinish) im selben Moment, in dem Windows die neue EXE schreibt und SmartScreen sie
 * prüft, behält der Taskleistenknopf mitunter für die ganze Lebensdauer des Fensters ein leeres Datei-Symbol, obwohl EXE,
 * Verknüpfung im Startmenü und Symbolspeicher der Shell das richtige Symbol liefern. Abhilfe: nach ready-to-show und noch einmal
 * NACHLAUF_MS später die Eigenschaften des Knopfs setzen (setAppDetails) und das Fenstersymbol neu zuweisen (setIcon). Dann belegt
 * Windows den Knopf neu.
 *
 * setAppDetails (Electron 44, BrowserWindow): appId muss gesetzt sein, sonst wirkt keine Angabe. relaunchCommand und
 * relaunchDisplayName gelten nur zusammen; fehlt einer, setzt Electron keinen, und Windows nimmt zum Anheften die Verknüpfung mit
 * derselben AppUserModelId. Hier sind beide gesetzt, mit demselben Befehl wie beim Autostart (main.js, loginItem): installiert nur
 * die EXE, im Entwicklermodus electron.exe mit dem App-Ordner. Das Symbol zum Anheften liest Windows aus einer echten Datei, nie aus
 * der asar: installiert aus der EXE (Index 0, dort trägt electron-builder das App-Symbol ein), im Entwicklermodus aus der .ico-Datei.
 * Autostart und Einzelinstanz bleiben davon unberührt.
 */

const APP_ID = "de.wamocon.wamo"; // wie app.setAppUserModelId in main.js und appId in package.json
const ANZEIGENAME = "W.A.I.S.";
const NACHLAUF_MS = 3000;

/** Befehlszeile mit jedem Teil in Anführungszeichen (Pfade unter C:\Users\<Name mit Leerzeichen>). */
function befehlszeile(teile) {
  return teile.map((t) => `"${t}"`).join(" ");
}

/** Werte für setAppDetails je Form der Installation. */
function appDetails({ istPaketiert, execPath, appDir, symbolDatei }) {
  return {
    appId: APP_ID,
    appIconPath: istPaketiert ? execPath : symbolDatei,
    appIconIndex: 0,
    relaunchCommand: befehlszeile(istPaketiert ? [execPath] : [execPath, appDir]),
    relaunchDisplayName: ANZEIGENAME,
  };
}

/** Eigenschaften und Symbol des Knopfs neu setzen. Liefert true, wenn es gesetzt wurde. Wirft nie. */
function knopfNeuSetzen(fenster, details, symbolDatei, log) {
  if (fenster.isDestroyed()) return false;
  try {
    fenster.setAppDetails(details);
    fenster.setIcon(symbolDatei);
    return true;
  } catch (err) {
    log(`Taskleistenknopf nicht neu gesetzt: ${err.message}`);
    return false;
  }
}

/**
 * Hängt die Absicherung an das Hauptfenster: nach ready-to-show sofort und nach NACHLAUF_MS noch einmal. Nur auf Windows.
 * Optionen: plattform (process.platform), istPaketiert (app.isPackaged), execPath (process.execPath), appDir (__dirname von
 * main.js), symbolDatei (Pfad der .ico für setIcon), warte (setTimeout, für Tests einstellbar), log.
 * Liefert true, wenn die Absicherung angehängt wurde.
 */
function taskleisteAbsichern(fenster, { plattform, istPaketiert, execPath, appDir, symbolDatei, warte = setTimeout, log = () => {} }) {
  if (plattform !== "win32" || !fenster) return false;
  const details = appDetails({ istPaketiert, execPath, appDir, symbolDatei });
  fenster.once("ready-to-show", () => {
    knopfNeuSetzen(fenster, details, symbolDatei, log);
    const zeitgeber = warte(() => knopfNeuSetzen(fenster, details, symbolDatei, log), NACHLAUF_MS);
    zeitgeber?.unref?.(); // hält die App beim Beenden nicht auf
  });
  return true;
}

module.exports = { ANZEIGENAME, APP_ID, NACHLAUF_MS, appDetails, taskleisteAbsichern };
