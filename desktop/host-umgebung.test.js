// Tests für die Python-Umgebung des Hosts in der installierten App (host-umgebung.js). Ausführen im Ordner desktop/:  npm test
// uv wird nie wirklich gestartet: ausfuehren ist ersetzt und legt nur die erwartete python.exe an.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { HostUmgebungFehler, PYTHON_VERSION, erzeugeHostUmgebung, uvUmgebung } = require("./host-umgebung");

function aufbau({ lock = "version = 1\n", mitUv = true } = {}) {
  const wurzel = fs.mkdtempSync(path.join(os.tmpdir(), "wamo-hostumg-"));
  const hostDir = path.join(wurzel, "resources", "host");
  fs.mkdirSync(hostDir, { recursive: true });
  if (lock !== null) fs.writeFileSync(path.join(hostDir, "uv.lock"), lock);
  const uvDatei = path.join(wurzel, "resources", "uv", "uv.exe");
  if (mitUv) {
    fs.mkdirSync(path.dirname(uvDatei), { recursive: true });
    fs.writeFileSync(uvDatei, "");
  }
  const aufrufe = [];
  const logs = [];
  const optionen = {
    hostDir,
    uvDatei,
    cacheDir: path.join(wurzel, "userData", "uv-cache"),
    pythonDir: path.join(wurzel, "local", "WAMO", "python"),
    env: { PATH: "C:\\x", UV_INDEX_URL: "https://boese.example/simple", UV_PYTHON: "9", VIRTUAL_ENV: "C:\\fremd", Uv_Token: "x" },
    log: (t) => logs.push(t),
    ausfuehren: async (datei, args, o) => {
      aufrufe.push({ datei, args, o });
      const py = path.join(hostDir, ".venv", "Scripts", "python.exe");
      fs.mkdirSync(path.dirname(py), { recursive: true });
      fs.writeFileSync(py, "");
    },
  };
  return { wurzel, hostDir, optionen, aufrufe, logs, aufraeumen: () => fs.rmSync(wurzel, { recursive: true, force: true }) };
}

test("Erster Start: uv sync mit Sperrdatei, ohne Dev-Pakete, in den eigenen Ordnern", async () => {
  const a = aufbau();
  try {
    const u = erzeugeHostUmgebung(a.optionen);
    assert.equal(u.status(), "fehlt");
    await u.bereit();
    assert.equal(a.aufrufe.length, 1);
    const { datei, args, o } = a.aufrufe[0];
    assert.equal(datei, a.optionen.uvDatei);
    assert.deepEqual(args, ["sync", "--locked", "--no-dev", "--no-build", "--managed-python", "--python", PYTHON_VERSION, "--project", a.hostDir]);
    assert.equal(o.cwd, a.hostDir);
    assert.equal(o.env.UV_CACHE_DIR, a.optionen.cacheDir);
    assert.equal(o.env.UV_PYTHON_INSTALL_DIR, a.optionen.pythonDir);
    assert.equal(o.env.UV_NO_CONFIG, "1");
    assert.equal(o.env.UV_LINK_MODE, "copy");
    assert.equal(o.env.PATH, "C:\\x");
    assert.equal(u.status(), "bereit");
  } finally {
    a.aufraeumen();
  }
});

test("uvUmgebung erbt keine UV_*-Einstellungen und kein VIRTUAL_ENV", () => {
  const env = uvUmgebung(
    { PATH: "p", UV_INDEX_URL: "x", uv_token: "y", VIRTUAL_ENV: "v", HOME: "h", PYTHONPATH: "C:\\boese", PythonHome: "C:\\boese", HTTPS_PROXY: "http://proxy:3128" },
    { cacheDir: "c", pythonDir: "p2" },
  );
  assert.equal(env.PYTHONPATH, undefined);
  assert.equal(env.PythonHome, undefined);
  assert.equal(env.HTTPS_PROXY, "http://proxy:3128", "Proxy bleibt für Firmennetze");
  assert.equal(env.PYTHONNOUSERSITE, "1");
  assert.equal(env.UV_INDEX_URL, undefined);
  assert.equal(env.uv_token, undefined);
  assert.equal(env.VIRTUAL_ENV, undefined);
  assert.equal(env.HOME, "h");
  assert.equal(env.UV_CACHE_DIR, "c");
});

test("Zweiter Start mit unveränderter Sperrdatei: nichts wird neu eingerichtet", async () => {
  const a = aufbau();
  try {
    await erzeugeHostUmgebung(a.optionen).bereit();
    await erzeugeHostUmgebung(a.optionen).bereit();
    assert.equal(a.aufrufe.length, 1);
  } finally {
    a.aufraeumen();
  }
});

test("Geänderte Sperrdatei (neue App-Version): die Umgebung wird neu abgeglichen", async () => {
  const a = aufbau();
  try {
    await erzeugeHostUmgebung(a.optionen).bereit();
    fs.writeFileSync(path.join(a.hostDir, "uv.lock"), "version = 2\n");
    const u = erzeugeHostUmgebung(a.optionen);
    assert.equal(u.status(), "fehlt");
    await u.bereit();
    assert.equal(a.aufrufe.length, 2);
  } finally {
    a.aufraeumen();
  }
});

test("Gleichzeitige Aufrufe teilen sich einen Lauf", async () => {
  const a = aufbau();
  try {
    const u = erzeugeHostUmgebung(a.optionen);
    await Promise.all([u.bereit(), u.bereit(), u.bereit()]);
    assert.equal(a.aufrufe.length, 1);
  } finally {
    a.aufraeumen();
  }
});

test("uv fehlt oder Sperrdatei fehlt: klare Fehlertexte, nichts wird gestartet", async () => {
  const ohneUv = aufbau({ mitUv: false });
  const ohneLock = aufbau({ lock: null });
  try {
    await assert.rejects(erzeugeHostUmgebung(ohneUv.optionen).bereit(), (e) => e instanceof HostUmgebungFehler && /uv fehlt/.test(e.text));
    await assert.rejects(erzeugeHostUmgebung(ohneLock.optionen).bereit(), (e) => /uv\.lock/.test(e.text));
    assert.equal(ohneUv.aufrufe.length + ohneLock.aufrufe.length, 0);
    assert.equal(erzeugeHostUmgebung(ohneLock.optionen).status(), "fehler");
  } finally {
    ohneUv.aufraeumen();
    ohneLock.aufraeumen();
  }
});

test("uv scheitert: Fehlertext ohne Einzelheiten, der nächste Aufruf versucht es erneut", async () => {
  const a = aufbau();
  try {
    let versuche = 0;
    const optionen = {
      ...a.optionen,
      ausfuehren: async (...args) => {
        versuche += 1;
        if (versuche === 1) throw Object.assign(new Error("x"), { code: 2, stderr: "geheim: Token abc\nfehler bei https://nutzer:hunter2@index.example/simple/mcp/" });
        return a.optionen.ausfuehren(...args);
      },
    };
    const u = erzeugeHostUmgebung(optionen);
    await assert.rejects(u.bereit(), (e) => /Netzverbindung/.test(e.text) && !/geheim/.test(e.text));
    assert.equal(u.status(), "fehler");
    assert.ok(a.logs.some((t) => /Code 2/.test(t)));
    assert.ok(a.logs.every((t) => !/hunter2/.test(t)), "Zugangsdaten in Adressen stehen nicht im Protokoll");
    await u.bereit();
    assert.equal(u.status(), "bereit");
  } finally {
    a.aufraeumen();
  }
});

test("Zeitüberschreitung wird als solche gemeldet", async () => {
  const a = aufbau();
  try {
    const u = erzeugeHostUmgebung({
      ...a.optionen,
      ausfuehren: async () => {
        throw Object.assign(new Error("t"), { killed: true });
      },
    });
    await assert.rejects(u.bereit(), (e) => /dauert zu lange/.test(e.text));
  } finally {
    a.aufraeumen();
  }
});

test("uv endet ohne Fehler, aber ohne python.exe: unvollständig, keine Marke", async () => {
  const a = aufbau();
  try {
    const u = erzeugeHostUmgebung({ ...a.optionen, ausfuehren: async () => undefined });
    await assert.rejects(u.bereit(), (e) => /unvollständig/.test(e.text));
    assert.equal(fs.existsSync(u.pfade.markeDatei), false);
  } finally {
    a.aufraeumen();
  }
});
