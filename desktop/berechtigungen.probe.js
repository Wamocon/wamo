// Hilfsprogramm für berechtigungen.electron.test.js: ein echtes Electron mit genau den Handlern der App
// (berechtigungen.installieren), zwei Fenstern (eigene Oberfläche und fremder Origin) und einer Seite, die Mikrofon
// und Kamera anfragt. Kein echtes Gerät: Chromium bekommt ein künstliches Mikrofon (--use-fake-device-for-media-stream).
// Ausgabe: eine JSON-Zeile mit den Ergebnissen, danach Ende. Nie Teil der App.
"use strict";

const { app, BrowserWindow, session } = require("electron");
const http = require("node:http");
const { installieren } = require("./berechtigungen");

// Nur das künstliche Gerät. NICHT --use-fake-ui-for-media-stream: Das umgeht den Anfrage-Handler (Chromium nickt dann
// jede getUserMedia-Anfrage ab, Video und fremde Origins eingeschlossen) und der Test prüfte nichts mehr.
app.commandLine.appendSwitch("use-fake-device-for-media-stream");
if (process.env.WAMO_PROBE_USERDATA) app.setPath("userData", process.env.WAMO_PROBE_USERDATA);

const SEITE = `<!doctype html><meta charset="utf-8"><title>probe</title><script>
async function versuche(constraints) {
  try {
    const s = await navigator.mediaDevices.getUserMedia(constraints);
    const t = s.getTracks().map((x) => x.kind);
    s.getTracks().forEach((x) => x.stop());
    return { ok: true, spuren: t };
  } catch (e) { return { ok: false, fehler: e.name }; }
}
async function abfrage(name) {
  try { return (await navigator.permissions.query({ name })).state; } catch (e) { return "fehler:" + e.name; }
}
if (location.search.includes("rahmen")) {
  // Unterframe mit demselben Origin: meldet sein Ergebnis an die Seite darüber
  versuche({ audio: true }).then((r) => parent.postMessage({ rahmen: r }, "*"));
} else window.ergebnis = (async () => ({
  rahmen: await new Promise((resolve) => {
    addEventListener("message", (e) => e.data?.rahmen && resolve(e.data.rahmen));
    const f = document.createElement("iframe");
    f.src = "/?rahmen";
    document.documentElement.appendChild(f);
    setTimeout(() => resolve({ ok: false, fehler: "Zeitüberschreitung" }), 8000);
  }),
  audio: await versuche({ audio: true }),
  video: await versuche({ video: true }),
  beides: await versuche({ audio: true, video: true }),
  mikrofon: await abfrage("microphone"),
  kamera: await abfrage("camera"),
}))();
</script>`;

const antwort = (_req, res) => {
  res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
  res.end(SEITE);
};
const server = http.createServer(antwort); // eigener Origin
const fremdServer = http.createServer(antwort); // anderer Port, also anderer Origin

async function lauf(url, hauptfenster, alsHaupt, partition) {
  const win = new BrowserWindow({
    show: false,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false, ...(partition ? { partition } : {}) },
  });
  if (alsHaupt) hauptfenster.fenster = win.webContents;
  await win.loadURL(url);
  const r = await win.webContents.executeJavaScript("window.ergebnis");
  win.destroy();
  return r;
}

app.on("window-all-closed", () => {}); // ein Listener genügt: sonst beendet das Schließen des ersten Fensters die App

app.whenReady().then(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  await new Promise((resolve) => fremdServer.listen(0, "127.0.0.1", resolve));
  const eigeneOrigin = `http://127.0.0.1:${server.address().port}`;
  const fremdeOrigin = `http://127.0.0.1:${fremdServer.address().port}`;
  const hauptfenster = { fenster: null };
  // wie main.js: dasselbe Fenster UND derselbe Origin
  const eigeneUi = (wc, url) => {
    let origin = "";
    try {
      origin = new URL(url ?? "").origin;
    } catch {
      origin = "";
    }
    return Boolean(wc) && wc === hauptfenster.fenster && origin === eigeneOrigin;
  };
  // wie main.js: Standard-Session und jede später entstehende (Partition)
  installieren(session.defaultSession, eigeneUi);
  app.on("session-created", (ses) => installieren(ses, eigeneUi));
  const out = {};
  out.eigene = await lauf(eigeneOrigin + "/", hauptfenster, true); // Hauptfenster, eigener Origin
  out.fremderOrigin = await lauf(fremdeOrigin + "/", hauptfenster, true); // Hauptfenster, aber anderer Origin
  out.fremdesFenster = await lauf(eigeneOrigin + "/", hauptfenster, false); // eigener Origin, aber nicht das Hauptfenster
  out.fremdePartition = await lauf(eigeneOrigin + "/", hauptfenster, false, "persist:wamo-probe"); // andere Session
  process.stdout.write(`PROBE ${JSON.stringify(out)}\n`);
  server.close();
  fremdServer.close();
  app.exit(0);
});

setTimeout(() => app.exit(2), 60000).unref();
