// Rand-Fenster: empfängt nur Pegel, die geprüften Stimmfarben und die geprüfte, umgerechnete Markierung des
// Lichtrahmens aus main.js.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("overlay", {
  onLevels: (callback) => ipcRenderer.on("levels", (_event, sample) => callback(sample)),
  onFarben: (callback) => ipcRenderer.on("farben", (_event, farben) => callback(farben)),
  onMarkierung: (callback) => ipcRenderer.on("markierung", (_event, markierung) => callback(markierung)),
});
