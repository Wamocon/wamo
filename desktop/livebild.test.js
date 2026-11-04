// Tests für den Schalter am Laptop (livebild.js): Standard aus, Einwilligung, nur Admin-Laptop, Herzschlag, Ablauf.
// Ausführen im Ordner desktop/:  npm test
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { DAUER_MAX_MS, DAUER_MS, EINWILLIGUNG, EINWILLIGUNG_VERSION, HERZSCHLAG_MS, erzeugeLiveBild } = require("./livebild");

const BESITZER = "11111111-2222-3333-4444-555555555555";

function umgebung({ stufe = "admin", geraetJson = true, antwort = true, sichtbar = true } = {}) {
  const ordner = fs.mkdtempSync(path.join(os.tmpdir(), "wamo-live-"));
  const uhr = { t: 1_700_000_000_000 };
  const protokoll = { fragen: 0, hinweis: [], geaendert: 0, timer: [], meldungen: [] };
  const zustand = { antwort, sichtbar };
  if (geraetJson) {
    fs.writeFileSync(path.join(ordner, "geraet.json"), JSON.stringify({ v: 1, kennung: "pc-test", besitzer: BESITZER, stufe }));
  }
  const live = erzeugeLiveBild({
    datenOrdner: ordner,
    jetzt: () => uhr.t,
    fragen: async () => {
      protokoll.fragen += 1;
      return zustand.antwort;
    },
    hinweis: (an, bis) => protokoll.hinweis.push([an, bis]),
    sichtbar: () => zustand.sichtbar,
    geaendert: () => (protokoll.geaendert += 1),
    melden: (titel, text) => protokoll.meldungen.push([titel, text]),
    setInterval: (fn, ms) => {
      const t = { fn, ms, unref() {} };
      protokoll.timer.push(t);
      return t;
    },
    clearInterval: (t) => {
      t.weg = true;
    },
  });
  const datei = () => JSON.parse(fs.readFileSync(path.join(ordner, "live-bild.json"), "utf8"));
  return { ordner, uhr, live, protokoll, zustand, datei, aufraeumen: () => fs.rmSync(ordner, { recursive: true, force: true }) };
}

test("Standard ist aus: ohne Einschalten gibt es keinen Schalter außer 'aus'", () => {
  const u = umgebung();
  try {
    u.live.start();
    assert.deepEqual(u.datei(), { v: 1, an: false });
    assert.equal(u.live.status().an, false);
  } finally {
    u.aufraeumen();
  }
});

test("ein übrig gebliebenes 'an' nach einem Absturz wird beim Start gelöscht", () => {
  const u = umgebung();
  try {
    fs.writeFileSync(path.join(u.ordner, "live-bild.json"), JSON.stringify({ v: 1, an: true, bis: 9e12 }));
    u.live.start();
    assert.equal(u.datei().an, false);
  } finally {
    u.aufraeumen();
  }
});

test("erstes Einschalten fragt nach der Einwilligung, danach nicht mehr", async () => {
  const u = umgebung();
  try {
    assert.equal((await u.live.einschalten()).ok, true);
    assert.equal(u.protokoll.fragen, 1);
    u.live.ausschalten();
    assert.equal((await u.live.einschalten()).ok, true);
    assert.equal(u.protokoll.fragen, 1);
  } finally {
    u.aufraeumen();
  }
});

test("Ablehnung lässt alles aus und schreibt nie 'an'", async () => {
  const u = umgebung({ antwort: false });
  try {
    const r = await u.live.einschalten();
    assert.equal(r.ok, false);
    assert.match(r.grund, /Einwilligung/);
    assert.equal(u.live.status().an, false);
    assert.equal(fs.existsSync(path.join(u.ordner, "live-bild.json")), false);
    assert.equal(fs.existsSync(path.join(u.ordner, "live-bild-einwilligung.json")), false);
    assert.deepEqual(u.protokoll.hinweis, []);
  } finally {
    u.aufraeumen();
  }
});

test("die Datei enthält alles, was der Host prüft", async () => {
  const u = umgebung();
  try {
    await u.live.einschalten();
    const d = u.datei();
    assert.equal(d.v, 1);
    assert.equal(d.an, true);
    assert.equal(d.einwilligung, EINWILLIGUNG_VERSION);
    assert.equal(d.kennung, "pc-test");
    assert.equal(d.besitzer, BESITZER);
    assert.equal(d.sichtbar, true);
    assert.equal(d.herzschlag, u.uhr.t / 1000);
    assert.equal(d.bis, (u.uhr.t + DAUER_MS) / 1000);
    assert.ok(DAUER_MS <= DAUER_MAX_MS);
    assert.deepEqual(u.protokoll.hinweis[0], [true, u.uhr.t + DAUER_MS]); // der rote Hinweis erscheint
    assert.equal(u.protokoll.timer[0].ms, HERZSCHLAG_MS);
  } finally {
    u.aufraeumen();
  }
});

test("Mitglieder-Laptop und nicht eingerichteter Laptop: nie", async () => {
  for (const opt of [{ stufe: "mitglied" }, { geraetJson: false }]) {
    const u = umgebung(opt);
    try {
      const r = await u.live.einschalten();
      assert.equal(r.ok, false);
      assert.equal(u.protokoll.fragen, 0, "nicht einmal fragen");
      assert.equal(u.live.status().verfuegbar, false);
      assert.equal(fs.existsSync(path.join(u.ordner, "live-bild.json")), false);
    } finally {
      u.aufraeumen();
    }
  }
});

test("Herzschlag erneuert sich, 'sichtbar' folgt dem Hinweis", async () => {
  const u = umgebung();
  try {
    await u.live.einschalten();
    u.uhr.t += 2000;
    u.live.tick();
    assert.equal(u.datei().herzschlag, u.uhr.t / 1000);
    u.zustand.sichtbar = false; // Hinweis verdeckt oder Vollbild
    u.uhr.t += 2000;
    u.live.tick();
    assert.equal(u.datei().sichtbar, false);
    assert.equal(u.datei().an, true);
  } finally {
    u.aufraeumen();
  }
});

test("Ablauf schaltet aus und schreibt 'aus'", async () => {
  const u = umgebung();
  try {
    await u.live.einschalten();
    u.uhr.t += DAUER_MS + 1;
    u.live.tick();
    assert.equal(u.live.status().an, false);
    assert.equal(u.datei().an, false);
    assert.equal(u.protokoll.timer[0].weg, true);
    assert.deepEqual(u.protokoll.hinweis.at(-1), [false, 0]);
  } finally {
    u.aufraeumen();
  }
});

test("neue Bindung (anderer Besitzer) verlangt die Einwilligung neu und beendet ein laufendes Live-Bild", async () => {
  const u = umgebung();
  try {
    await u.live.einschalten();
    fs.writeFileSync(path.join(u.ordner, "geraet.json"), JSON.stringify({ v: 1, kennung: "pc-test", besitzer: "99999999-2222-3333-4444-555555555555", stufe: "admin" }));
    u.live.tick();
    assert.equal(u.live.status().an, false);
    assert.equal((await u.live.einschalten()).ok, true);
    assert.equal(u.protokoll.fragen, 2);
  } finally {
    u.aufraeumen();
  }
});

test("Stufe wird zum Mitglied: sofort aus", async () => {
  const u = umgebung();
  try {
    await u.live.einschalten();
    fs.writeFileSync(path.join(u.ordner, "geraet.json"), JSON.stringify({ v: 1, kennung: "pc-test", besitzer: BESITZER, stufe: "mitglied" }));
    u.live.tick();
    assert.equal(u.live.status().an, false);
  } finally {
    u.aufraeumen();
  }
});

test("Beenden der App schaltet aus; Ausschalten ist wiederholbar", async () => {
  const u = umgebung();
  try {
    await u.live.einschalten();
    u.live.ausschalten("App beendet");
    u.live.ausschalten("App beendet");
    assert.equal(u.datei().an, false);
    assert.equal(u.protokoll.hinweis.filter(([an]) => !an).length, 1);
  } finally {
    u.aufraeumen();
  }
});

test("zwei Einschalten gleichzeitig öffnen nur einen Dialog", async () => {
  const u = umgebung();
  try {
    const [a, b] = await Promise.all([u.live.einschalten(), u.live.einschalten()]);
    assert.equal(u.protokoll.fragen, 1);
    assert.equal([a, b].filter((r) => r.ok).length, 1);
  } finally {
    u.aufraeumen();
  }
});

test("Text der Einwilligung nennt Inhalt, Zweck, Dauer, Datenschutz und Kundendaten; ohne Gedankenstriche", () => {
  const t = `${EINWILLIGUNG.titel} ${EINWILLIGUNG.frage} ${EINWILLIGUNG.detail}`;
  for (const wort of ["Inhalt:", "Zweck:", "Dauer:", "Datenschutz:", "Kundendaten", "Chats", "nie an ein Sprachmodell", "roten Rahmen", "Passwortfelder"]) {
    assert.ok(t.includes(wort), wort);
  }
  assert.ok(!/[–—]/.test(t), "kein Gedankenstrich");
  assert.ok(EINWILLIGUNG.titel.startsWith("WAMO "), "Titel beginnt mit WAMO: der Host bedient solche Fenster nie");
});

test("nur das Tray-Menü schaltet ein: die Brücke zur Oberfläche kennt das Live-Bild nicht", () => {
  const preload = fs.readFileSync(path.join(__dirname, "preload.js"), "utf8");
  const main = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
  assert.ok(!/live/i.test(preload.replace(/\/\/.*$/gm, "")), "preload.js enthält nichts zum Live-Bild");
  assert.ok(!/ipcMain\.(on|handle)\(\s*"live/i.test(main), "kein IPC-Kanal zum Live-Bild");
  assert.match(main, /click: \(\) => void liveBildUmschalten\(\)/);
});

test("der Hinweis am Laptop ist nicht vor Bildschirmfotos geschützt und nicht anklickbar", () => {
  const main = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
  const start = main.indexOf("function zeigeLiveHinweise");
  const ende = main.indexOf("function entferneLiveHinweise");
  const funktion = main.slice(start, ende);
  assert.ok(start > 0 && ende > start);
  assert.ok(!funktion.includes("setContentProtection"));
  assert.ok(funktion.includes("setIgnoreMouseEvents(true)"));
  assert.ok(funktion.includes('setAlwaysOnTop(true, "screen-saver")'));
  const html = fs.readFileSync(path.join(__dirname, "livebild-hinweis.html"), "utf8");
  assert.ok(html.includes("<title>WAMO Live-Bild Hinweis</title>"));
});

test("Ablehnung ist als abgelehnt gekennzeichnet; andere Fehler nicht", async () => {
  const u = umgebung({ antwort: false });
  try {
    assert.equal((await u.live.einschalten()).abgelehnt, true);
  } finally {
    u.aufraeumen();
  }
  const v = umgebung({ geraetJson: false });
  try {
    assert.equal((await v.live.einschalten()).abgelehnt, undefined);
  } finally {
    v.aufraeumen();
  }
});

test("scheitert das Umbenennen (Host liest gerade), wird wiederholt und zuletzt direkt geschrieben", async () => {
  const u = umgebung();
  const echt = fs.renameSync;
  let versuche = 0;
  try {
    await u.live.einschalten();
    fs.renameSync = () => {
      versuche += 1;
      throw Object.assign(new Error("busy"), { code: "EBUSY" });
    };
    u.uhr.t += 2000;
    u.live.tick(); // darf nicht ausschalten
    assert.equal(u.live.status().an, true);
    assert.equal(u.datei().herzschlag, u.uhr.t / 1000);
    assert.ok(versuche >= 3);
  } finally {
    fs.renameSync = echt;
    u.aufraeumen();
  }
});

// ---------- Fernanfrage vom Handy (04.11.2026) ----------
function anfrageSchreiben(u, aenderungen = {}) {
  const inhalt = { v: 1, id: "ab".repeat(8), ts: u.uhr.t / 1000, kennung: "pc-test", besitzer: BESITZER, ...aenderungen };
  fs.writeFileSync(path.join(u.ordner, "live-bild-anfrage.json"), typeof aenderungen === "string" ? aenderungen : JSON.stringify(inhalt));
}
const anfrageDa = (u) => fs.existsSync(path.join(u.ordner, "live-bild-anfrage.json"));
async function mitEinwilligung(u) {
  await u.live.einschalten(); // der Dialog am Laptop (Einwilligung), einmalig
  u.live.ausschalten();
  u.protokoll.fragen = 0;
}

test("Fernanfrage: mit Einwilligung am Laptop schaltet sie ein, ohne Dialog, mit Meldung, und die Datei ist weg", async () => {
  const u = umgebung();
  try {
    await mitEinwilligung(u);
    anfrageSchreiben(u);
    const r = await u.live.anfrageVerarbeiten();
    assert.equal(r.ok, true);
    assert.equal(u.live.status().an, true);
    assert.equal(u.protokoll.fragen, 0, "per Fernanfrage nie ein Dialog");
    assert.equal(anfrageDa(u), false, "einmalig");
    assert.equal(u.protokoll.meldungen.length, 1);
    assert.deepEqual(u.protokoll.hinweis.at(-1)[0], true, "roter Rahmen");
    assert.ok(u.datei().bis * 1000 - u.uhr.t <= DAUER_MS);
  } finally {
    u.aufraeumen();
  }
});

test("Fernanfrage ohne Einwilligung am Laptop: nichts wird eingeschaltet, nie eine Frage, Meldung erklärt es", async () => {
  const u = umgebung();
  try {
    anfrageSchreiben(u);
    const r = await u.live.anfrageVerarbeiten();
    assert.equal(r.ok, false);
    assert.equal(u.live.status().an, false);
    assert.equal(u.protokoll.fragen, 0);
    assert.equal(anfrageDa(u), false);
    assert.match(u.protokoll.meldungen[0][1], /Einwilligung/);
  } finally {
    u.aufraeumen();
  }
});

test("Fernanfrage: zu alt, aus der Zukunft, fremdes Gerät, fremder Besitzer, kaputt oder zu groß wird verworfen", async () => {
  const faelle = [
    { ts: 0 },
    { ts: 1_700_000_000 - 61 },
    { ts: 1_700_000_000 + 30 },
    { kennung: "pc-anderer" },
    { besitzer: "99999999-2222-3333-4444-555555555555" },
    { v: 2 },
    { ts: "jetzt" },
    "kein json",
    "x".repeat(9000),
  ];
  for (const f of faelle) {
    const u = umgebung();
    try {
      await mitEinwilligung(u);
      typeof f === "string" ? anfrageSchreiben(u, f) : anfrageSchreiben(u, f);
      if (typeof f === "string") fs.writeFileSync(path.join(u.ordner, "live-bild-anfrage.json"), f);
      const r = await u.live.anfrageVerarbeiten();
      assert.equal(r.ok, false, JSON.stringify(f).slice(0, 60));
      assert.equal(u.live.status().an, false);
      assert.equal(anfrageDa(u), false, "auch verworfene Anfragen werden gelöscht");
    } finally {
      u.aufraeumen();
    }
  }
});

test("Fernanfrage: nie auf dem Laptop eines Mitglieds, nie ohne geraet.json, nichts zu tun ohne Datei", async () => {
  const m = umgebung({ stufe: "mitglied" });
  try {
    anfrageSchreiben(m);
    assert.equal((await m.live.anfrageVerarbeiten()).ok, false);
    assert.equal(m.live.status().an, false);
  } finally {
    m.aufraeumen();
  }
  const o = umgebung({ geraetJson: false });
  try {
    anfrageSchreiben(o);
    assert.equal((await o.live.anfrageVerarbeiten()).ok, false);
    assert.equal(o.live.status().an, false);
  } finally {
    o.aufraeumen();
  }
  const k = umgebung();
  try {
    assert.deepEqual(await k.live.anfrageVerarbeiten(), { ok: false, keine: true });
    assert.equal(k.protokoll.meldungen.length, 0);
  } finally {
    k.aufraeumen();
  }
});

test("Fernanfrage bei laufendem Live-Bild verlängert nichts und meldet nicht noch einmal; start löscht alte Anfragen", async () => {
  const u = umgebung();
  try {
    await mitEinwilligung(u);
    anfrageSchreiben(u);
    await u.live.anfrageVerarbeiten();
    const bis = u.datei().bis;
    u.uhr.t += 5000;
    anfrageSchreiben(u, { id: "ef".repeat(8) });
    const r = await u.live.anfrageVerarbeiten();
    assert.equal(r.schonAn, true);
    assert.equal(u.datei().bis, bis);
    assert.equal(u.protokoll.meldungen.length, 1);
    u.live.ausschalten();
    anfrageSchreiben(u, { id: "12".repeat(8) });
    u.live.start();
    assert.equal(anfrageDa(u), false, "eine Anfrage aus einem früheren Lauf zählt nie");
  } finally {
    u.aufraeumen();
  }
});

test("Fernanfrage: dieselbe Anfrage zählt nur einmal, auch wenn die Datei bleibt", async () => {
  const u = umgebung();
  try {
    await mitEinwilligung(u);
    anfrageSchreiben(u);
    assert.equal((await u.live.anfrageVerarbeiten()).ok, true);
    u.live.ausschalten("im Tray ausgeschaltet");
    anfrageSchreiben(u); // dieselbe id: die Datei war nicht gelöscht
    const r = await u.live.anfrageVerarbeiten();
    assert.equal(r.ok, false);
    assert.equal(u.live.status().an, false, "eine ausgeschaltete Person bekommt es nicht wieder eingeschaltet");
    anfrageSchreiben(u, { id: "cd".repeat(8) }); // eine neue id gilt
    assert.equal((await u.live.anfrageVerarbeiten()).ok, true);
  } finally {
    u.aufraeumen();
  }
});

test("Einwilligung der Version 1 gilt nicht für den Fernstart (der Text nannte ihn nicht)", async () => {
  const u = umgebung();
  try {
    fs.writeFileSync(path.join(u.ordner, "live-bild-einwilligung.json"), JSON.stringify({ v: 1, kennung: "pc-test", besitzer: BESITZER }));
    anfrageSchreiben(u);
    const r = await u.live.anfrageVerarbeiten();
    assert.equal(r.ok, false);
    assert.equal(u.live.status().an, false);
    assert.equal(u.protokoll.fragen, 0);
  } finally {
    u.aufraeumen();
  }
});
