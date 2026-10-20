// Hauptfenster: die Oberfläche darf nur Pegel an den Stimmen-Rand melden, einmalig die Stimmfarben
// (ui/src/lib/stimmfarben.ts) übergeben, die Betriebsart (buero oder unterwegs) lesen und setzen und den
// Rechnernamen erfragen, sonst nichts. main.js prüft Absender und Werte.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("wamoDesktop", {
  isDesktop: true,
  levels: (sample) => ipcRenderer.send("levels", sample),
  farben: (farben) => ipcRenderer.send("farben", farben),
  betriebsart: {
    lesen: () => ipcRenderer.invoke("betriebsart:lesen"),
    setzen: (art) => ipcRenderer.invoke("betriebsart:setzen", art === "buero" ? "buero" : "unterwegs"),
    abonnieren: (rueckruf) => {
      const hoerer = (_event, art) => rueckruf(art);
      ipcRenderer.on("betriebsart", hoerer);
      return () => ipcRenderer.removeListener("betriebsart", hoerer);
    },
  },
  rechnername: () => ipcRenderer.invoke("rechnername"),
});
