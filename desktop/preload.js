// Hauptfenster: die Oberfläche darf nur Pegel an den Stimmen-Rand melden, einmalig die Stimmfarben
// (ui/src/lib/stimmfarben.ts) übergeben, die Betriebsart (buero oder unterwegs) lesen und setzen und den
// Rechnernamen, den Windows-Datenschutzstatus des Mikrofons erfragen und (nur Büro) den Laptop einrichten, sonst nichts.
// main.js prüft Absender und Werte.
const { contextBridge, ipcRenderer } = require("electron");

// Nur Text und ganze Zahlen gehen über die Brücke; main.js und geraet.js prüfen die Werte danach streng.
const text = (v) => (typeof v === "string" ? v.slice(0, 512) : "");
const zahlOderNull = (v) => (Number.isInteger(v) ? v : null);
const textOderNull = (v) => (typeof v === "string" ? v.slice(0, 512) : null);

/** Antwort des Kerns auf die Gerätebindung auf die bekannten Felder und einfache Typen verkleinern. */
function schmaleAntwort(a) {
  const z = a && typeof a === "object" && a.ziel && typeof a.ziel === "object" ? a.ziel : null;
  return {
    ergebnis: text(a?.ergebnis),
    status: text(a?.status),
    name: text(a?.name),
    kennung: textOderNull(a?.kennung),
    slot: zahlOderNull(a?.slot),
    stufe: text(a?.stufe),
    user_id: text(a?.user_id),
    ziel: z
      ? { benutzer: text(z.benutzer), host: text(z.host), socket: text(z.socket), hostschluessel: text(z.hostschluessel) }
      : null,
  };
}

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
  mikrofonStatus: () => ipcRenderer.invoke("mikrofon:status"),
  // Einrichtung des Laptops (nur Büro, geraet.js): nur einfache Werte gehen hinaus, der private Schlüssel bleibt im
  // Hauptprogramm. Antworten: { ok: true, ... } oder { ok: false, text }.
  geraet: {
    vorbereiten: () => ipcRenderer.invoke("geraet:vorbereiten"),
    signieren: (anfrage) => ipcRenderer.invoke("geraet:signieren", { nonce: text(anfrage?.nonce), userId: text(anfrage?.userId) }),
    aktivieren: (antwort) => ipcRenderer.invoke("geraet:aktivieren", schmaleAntwort(antwort)),
    status: () => ipcRenderer.invoke("geraet:status"),
  },
});
