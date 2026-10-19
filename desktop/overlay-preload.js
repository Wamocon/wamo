// Rand-Fenster: empfängt nur Pegel und die geprüften Stimmfarben aus main.js.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("overlay", {
  onLevels: (callback) => ipcRenderer.on("levels", (_event, sample) => callback(sample)),
  onFarben: (callback) => ipcRenderer.on("farben", (_event, farben) => callback(farben)),
});
