// Hauptfenster: die Oberfläche darf nur Pegel an den Stimmen-Rand melden und einmalig die
// Stimmfarben (ui/src/lib/stimmfarben.ts) übergeben, sonst nichts. main.js prüft beides.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("wamoDesktop", {
  isDesktop: true,
  levels: (sample) => ipcRenderer.send("levels", sample),
  farben: (farben) => ipcRenderer.send("farben", farben),
});
