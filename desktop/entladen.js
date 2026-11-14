"use strict";

/**
 * Nachfrage beim Beenden oder Neuladen des Hauptfensters, solange die Oberfläche einen Entwicklungsauftrag meldet.
 *
 * Die Oberfläche (ui/src/lib/useNeuladenWarnung.ts) setzt dafür beforeunload mit preventDefault. Ein Browser fragt dann selbst nach,
 * Electron nicht: Ohne einen Hörer für will-prevent-unload bleibt das Fenster stillschweigend stehen, Beenden und Neuladen wirken
 * dann wie ein Absturz. Hier fragt die App selbst. "Beenden" ruft event.preventDefault() auf: Das ist bei will-prevent-unload das
 * Zeichen, die Seite trotz der Warnung zu entladen. Alles andere (Abbrechen, Esc, ein Fehler des Dialogs) lässt das Entladen
 * angehalten, im Zweifel wird nichts beendet.
 *
 * Kein neuer IPC-Kanal, keine Änderung an Preload und Fuses: Die Frage entsteht allein im Hauptprozess.
 */

const FRAGE = Object.freeze({
  type: "question",
  title: "W.A.I.S.",
  message: "Ein Entwicklungsauftrag läuft noch. Trotzdem beenden?",
  detail: "Ein Auftrag endet mit dem Gespräch.",
  buttons: Object.freeze(["Abbrechen", "Beenden"]),
  defaultId: 0, // Eingabetaste: Abbrechen
  cancelId: 0, // Esc und Schließen des Dialogs: Abbrechen
  noLink: true,
});
const BEENDEN = 1; // Index des Knopfes "Beenden"

/**
 * Handler für webContents.on("will-prevent-unload"). Zeigt die Frage (synchron, die Entscheidung muss vor dem Ende des Handlers
 * fallen) und erlaubt das Entladen nur bei "Beenden". Liefert true, wenn es erlaubt wurde. Wirft nie.
 */
function entladenBehandeln(event, dialog, fenster) {
  const eltern = fenster && typeof fenster.isDestroyed === "function" && fenster.isDestroyed() ? undefined : fenster || undefined;
  let auswahl;
  try {
    auswahl = dialog.showMessageBoxSync(eltern, { ...FRAGE, buttons: [...FRAGE.buttons] });
  } catch {
    return false;
  }
  if (auswahl !== BEENDEN) return false;
  event.preventDefault();
  return true;
}

module.exports = { BEENDEN, FRAGE, entladenBehandeln };
