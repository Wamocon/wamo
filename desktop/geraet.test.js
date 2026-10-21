// Tests für die Einrichtung des Laptops (geraet.js). Ausführen im Ordner desktop/:  npm test
// Alle Pfade zeigen in einen Testordner unter dem Temp-Ordner; Programme (icacls, schtasks, PowerShell) sind
// ersetzt, außer in den Tests, die ausdrücklich das echte Windows prüfen.
"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const {
  GeraetFehler,
  NEUSTART_ABSTAND_MS,
  STATUS_FRISCH_MS,
  erzeugeGeraet,
  fingerabdruck,
  leseOpensshPrivat,
  nachrichtBinden,
  opensshPrivat,
  pruefeHostname,
  pruefeKernAntwort,
  publikBlob,
  schreibeAtomar,
  tunnelZustand,
  waechterEntscheidung,
} = require("./geraet");

const START = Date.parse("2026-10-21T10:00:00Z");
const USER = "0b8a5f2e-3c1d-4e6f-9a7b-1c2d3e4f5a6b";
const NONCE = "q1w2e3r4t5y6u7i8o9p0a1s2d3f4g5h6j7k8l9z0x1c";
const SSH_KEYGEN = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "OpenSSH", "ssh-keygen.exe");
const HAT_KEYGEN = process.platform === "win32" && fs.existsSync(SSH_KEYGEN);
const FAKE_SID = "S-1-5-21-1-2-3-1001";

const aufzuraeumen = [];
process.on("exit", () => {
  for (const ordner of aufzuraeumen) fs.rmSync(ordner, { recursive: true, force: true });
});

/** Testordner mit home, lokal und repo samt Einrichtungsskript; Programme sind ersetzt und werden aufgezeichnet. */
function umgebung({ scheitere = () => false, echt = false } = {}) {
  const wurzel = fs.mkdtempSync(path.join(os.tmpdir(), "wamo-geraet-test-"));
  aufzuraeumen.push(wurzel);
  const home = path.join(wurzel, "home");
  const lokal = path.join(wurzel, "lokal");
  const repo = path.join(wurzel, "repo");
  fs.mkdirSync(home);
  fs.mkdirSync(lokal);
  fs.mkdirSync(path.join(repo, "scripts"), { recursive: true });
  fs.writeFileSync(
    path.join(repo, "scripts", "wamo-tunnel-einrichten.ps1"),
    "param([switch]$Geraet, [switch]$Starten)\nif (-not ($Geraet -and $Starten)) { exit 3 }\nexit 0\n",
  );
  const aufrufe = [];
  let zeit = START;
  const ersatz = async (datei, args, optionen) => {
    aufrufe.push({ datei, args, optionen });
    if (datei.endsWith("whoami.exe")) return { stdout: `"PC\\test","${FAKE_SID}"\r\n` };
    if (scheitere(datei, args)) throw Object.assign(new Error("fehlgeschlagen"), { code: 1 });
    return { stdout: "" };
  };
  const geraet = erzeugeGeraet({
    home,
    localAppData: lokal,
    systemRoot: "C:\\Windows",
    repo,
    hostname: () => "LAPTOP-07",
    hostFingerabdruecke: [HOST_FP], // im Test ist der Hostschlüssel ein Prüfwert, nicht der echte von Sokrates-2
    jetzt: () => zeit,
    ...(echt ? {} : { ausfuehren: ersatz }),
  });
  return {
    wurzel,
    home,
    lokal,
    repo,
    aufrufe,
    geraet,
    pfade: geraet.pfade,
    zeit: () => zeit,
    setzeZeit: (z) => {
      zeit = z;
    },
    aufrufeVon: (name) => aufrufe.filter((a) => a.datei.toLowerCase().endsWith(name)),
  };
}

const HOSTSCHLUESSEL = `ssh-ed25519 ${publikBlob(Buffer.alloc(32, 7)).toString("base64")}`;
const HOST_FP = fingerabdruck(publikBlob(Buffer.alloc(32, 7)));
const ERWARTET = { host: "192.168.178.136", fingerabdruecke: [HOST_FP] };

function guteAntwort(ueberschreibe = {}, ziel = {}) {
  return {
    ergebnis: "neu_aktiv",
    status: "aktiv",
    name: "Laptop Anna",
    kennung: "pc-anna",
    slot: 5,
    stufe: "mitglied",
    user_id: USER,
    ziel: {
      benutzer: "wamo-t-05",
      host: "192.168.178.136",
      socket: "/run/wamo-pc/t05/pc.sock",
      hostschluessel: HOSTSCHLUESSEL,
      ...ziel,
    },
    ...ueberschreibe,
  };
}

function oeffentlicherSchluessel(pubkeyZeile) {
  const blob = Buffer.from(pubkeyZeile.split(" ")[1], "base64");
  const pub = blob.subarray(blob.length - 32);
  return crypto.createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: pub.toString("base64url") }, format: "jwk" });
}

// ---------- Schlüssel ----------

test("Schlüssel: OpenSSH-Format, Public-Zeile und Fingerabdruck stimmen, vorhandener wird nie überschrieben", async () => {
  const u = umgebung();
  const erst = await u.geraet.vorbereiten();
  assert.equal(erst.neu, true);
  assert.equal(erst.hostname, "LAPTOP-07");
  assert.match(erst.pubkey, /^ssh-ed25519 [A-Za-z0-9+/]{68}$/);
  assert.match(erst.fingerprint, /^SHA256:[A-Za-z0-9+/]{43}$/);

  const privat = fs.readFileSync(u.pfade.schluesselDatei, "utf8");
  assert.match(privat, /^-----BEGIN OPENSSH PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+\n-----END OPENSSH PRIVATE KEY-----\n$/);
  const pubDatei = fs.readFileSync(u.pfade.pubDatei, "utf8");
  assert.equal(pubDatei, `${erst.pubkey} wamo-geraet@LAPTOP-07\n`);
  const gelesen = leseOpensshPrivat(privat);
  assert.equal(gelesen.kommentar, "wamo-geraet@LAPTOP-07");
  assert.equal(fingerabdruck(publikBlob(gelesen.pub)), erst.fingerprint);

  // Zugriffsregeln wurden angefordert: Datei nur für die SID des Benutzers
  const icacls = u.aufrufeVon("icacls.exe");
  assert.equal(icacls.length, 1);
  assert.deepEqual(icacls[0].args, [u.pfade.schluesselDatei, "/inheritance:r", "/grant:r", `*${FAKE_SID}:F`]);

  // zweiter Aufruf: nichts Neues, Datei unverändert
  const zweit = await u.geraet.vorbereiten();
  assert.equal(zweit.neu, false);
  assert.equal(zweit.pubkey, erst.pubkey);
  assert.equal(fs.readFileSync(u.pfade.schluesselDatei, "utf8"), privat);
  assert.equal(u.aufrufeVon("icacls.exe").length, 1);

  // fehlende .pub-Datei wird aus dem Schlüssel wiederhergestellt, der Schlüssel bleibt
  fs.rmSync(u.pfade.pubDatei);
  const dritt = await u.geraet.vorbereiten();
  assert.equal(dritt.pubkey, erst.pubkey);
  assert.equal(fs.readFileSync(u.pfade.pubDatei, "utf8"), pubDatei);
  assert.equal(fs.readFileSync(u.pfade.schluesselDatei, "utf8"), privat);
});

test("Schlüssel: scheitern die Zugriffsrechte, wird der Schlüssel gelöscht und der Fehler gemeldet", async () => {
  const u = umgebung({ scheitere: (datei) => datei.endsWith("icacls.exe") });
  await assert.rejects(u.geraet.vorbereiten(), (e) => e instanceof GeraetFehler && /Zugriffsrechte/.test(e.text));
  assert.equal(fs.existsSync(u.pfade.schluesselDatei), false);
  assert.equal(fs.existsSync(u.pfade.pubDatei), false);
});

test("Schlüssel: kaputte vorhandene Datei wird gemeldet und nicht überschrieben", async () => {
  const u = umgebung();
  fs.mkdirSync(path.dirname(u.pfade.schluesselDatei), { recursive: true });
  fs.writeFileSync(u.pfade.schluesselDatei, "kein Schlüssel");
  await assert.rejects(u.geraet.vorbereiten(), (e) => e instanceof GeraetFehler && /nicht lesbar/.test(e.text));
  assert.equal(fs.readFileSync(u.pfade.schluesselDatei, "utf8"), "kein Schlüssel");
});

test("Schlüssel: Passphrase, falsche Prüfzahl und fremder Seed werden abgelehnt", () => {
  const paar = crypto.generateKeyPairSync("ed25519");
  const seed = Buffer.from(paar.privateKey.export({ format: "jwk" }).d, "base64url");
  const pub = Buffer.from(paar.publicKey.export({ format: "jwk" }).x, "base64url");
  assert.ok(leseOpensshPrivat(opensshPrivat(seed, pub, "k")));
  const anderer = crypto.generateKeyPairSync("ed25519");
  const fremdPub = Buffer.from(anderer.publicKey.export({ format: "jwk" }).x, "base64url");
  assert.throws(() => leseOpensshPrivat(opensshPrivat(seed, fremdPub, "k")));
  assert.throws(() => leseOpensshPrivat(opensshPrivat(seed, pub, "k").replace("OPENSSH", "RSA")));
  assert.throws(() => opensshPrivat(Buffer.alloc(31), pub, "k"));
  // verschlüsselt (Chiffre "aes256-ctr"): nicht unterstützt
  const text = opensshPrivat(seed, pub, "k");
  const roh = Buffer.from(text.split("\n").slice(1, -2).join(""), "base64");
  const ersetzt = Buffer.concat([roh.subarray(0, 19), Buffer.from("aes256-ctr"), roh.subarray(23)]);
  assert.throws(() => leseOpensshPrivat(`-----BEGIN OPENSSH PRIVATE KEY-----\n${ersetzt.toString("base64")}\n-----END OPENSSH PRIVATE KEY-----\n`));
});

test("Schlüssel: ssh-keygen liest die Datei und meldet dieselbe öffentliche Zeile und denselben Fingerabdruck", { skip: !HAT_KEYGEN && "ssh-keygen.exe nicht vorhanden" }, async () => {
  const u = umgebung({ echt: true });
  const r = await u.geraet.vorbereiten();
  const y = execFileSync(SSH_KEYGEN, ["-y", "-f", u.pfade.schluesselDatei], { encoding: "utf8", windowsHide: true });
  assert.equal(y.trim().split(" ").slice(0, 2).join(" "), r.pubkey);
  const l = execFileSync(SSH_KEYGEN, ["-l", "-f", u.pfade.schluesselDatei], { encoding: "utf8", windowsHide: true });
  assert.ok(l.includes(r.fingerprint), l);
  assert.match(l, /\(ED25519\)/);
  const lPub = execFileSync(SSH_KEYGEN, ["-l", "-f", u.pfade.pubDatei], { encoding: "utf8", windowsHide: true });
  assert.ok(lPub.includes(r.fingerprint), lPub);
});

test("Zugriff: das echte icacls lässt auf Schlüssel und Datenordner nur den Benutzer übrig", { skip: process.platform !== "win32" && "nur unter Windows" }, async () => {
  const u = umgebung({ echt: true });
  await u.geraet.vorbereiten();
  const sig = await u.geraet.signiere({ nonce: NONCE, userId: USER });
  assert.ok(sig);
  const antwort = await u.geraet.aktivieren(guteAntwort());
  assert.equal(antwort.ok, true);
  for (const pfad of [u.pfade.schluesselDatei, u.pfade.datenOrdner]) {
    const ausgabe = execFileSync(path.join(process.env.SystemRoot, "System32", "icacls.exe"), [pfad], { encoding: "utf8", windowsHide: true });
    const eintraege = ausgabe.split(/\r?\n/).filter((z) => /:\((?:[A-Z]+|\()/.test(z) || /\(F\)/.test(z));
    assert.equal(eintraege.length, 1, ausgabe);
    assert.ok(!/Everyone|BUILTIN\\Users|SYSTEM|Administrators|Authenticated/i.test(ausgabe), ausgabe);
  }
});

// ---------- Signatur ----------

test("Signatur: 64 Byte base64url, mit dem öffentlichen Schlüssel über die feste Nachricht prüfbar", async () => {
  const u = umgebung();
  const { pubkey } = await u.geraet.vorbereiten();
  const sig = await u.geraet.signiere({ nonce: NONCE, userId: USER });
  assert.match(sig, /^[A-Za-z0-9_-]{86}$/);
  const roh = Buffer.from(sig, "base64url");
  assert.equal(roh.length, 64);
  const nachricht = `wamo-geraet-binden\n${NONCE}\n${USER}\nLAPTOP-07`;
  assert.equal(nachricht, nachrichtBinden({ nonce: NONCE, userId: USER, hostname: "LAPTOP-07" }));
  const pub = oeffentlicherSchluessel(pubkey);
  assert.equal(crypto.verify(null, Buffer.from(nachricht), pub, roh), true);
  // jede Änderung der Nachricht macht die Signatur ungültig
  for (const andere of [`${nachricht}x`, nachricht.replace(NONCE, `${NONCE}x`), nachricht.replace("LAPTOP-07", "LAPTOP-08")]) {
    assert.equal(crypto.verify(null, Buffer.from(andere), pub, roh), false);
  }
});

test("Signatur: signiert nie freie Daten (Zeilenumbrüche, falsche Form, fehlender Schlüssel)", async () => {
  const u = umgebung();
  await assert.rejects(u.geraet.signiere({ nonce: NONCE, userId: USER }), /Geräteschlüssel/); // noch keiner da
  await u.geraet.vorbereiten();
  const schlecht = [
    { nonce: `${NONCE}\n${USER}`, userId: USER },
    { nonce: `${NONCE}\nLAPTOP-07`, userId: USER },
    { nonce: "", userId: USER },
    { nonce: "kurz", userId: USER },
    { nonce: "a".repeat(201), userId: USER },
    { nonce: NONCE, userId: `${USER}\nx` },
    { nonce: NONCE, userId: USER.toUpperCase() },
    { nonce: NONCE, userId: "nicht-uuid" },
    { nonce: 42, userId: USER },
    { nonce: NONCE, userId: null },
    {},
  ];
  for (const s of schlecht) {
    await assert.rejects(u.geraet.signiere(s), (e) => e instanceof GeraetFehler, JSON.stringify(s));
  }
  await assert.rejects(u.geraet.signiere(), (e) => e instanceof GeraetFehler);
});

test("Rechnername: nur Buchstaben, Ziffern, Bindestrich (höchstens 63)", () => {
  assert.equal(pruefeHostname("LAPTOP-07"), "LAPTOP-07");
  assert.equal(pruefeHostname("a".repeat(63)), "a".repeat(63));
  for (const schlecht of ["", "a".repeat(64), "pc_1", "pc.lan", "pc 1", "pc\nx", "ä", null, 5]) {
    assert.throws(() => pruefeHostname(schlecht), GeraetFehler, String(schlecht));
  }
});

// ---------- Antwort des Kerns ----------

test("Antwort des Kerns: gute Werte werden übernommen", () => {
  const k = pruefeKernAntwort(guteAntwort(), USER, ERWARTET);
  assert.deepEqual(k, {
    kennung: "pc-anna",
    slot: 5,
    besitzer: USER,
    name: "Laptop Anna",
    stufe: "mitglied",
    benutzer: "wamo-t-05",
    host: "192.168.178.136",
    socket: "/run/wamo-pc/t05/pc.sock",
    hostschluessel: HOSTSCHLUESSEL,
  });
  assert.equal(pruefeKernAntwort(guteAntwort({ slot: null, stufe: "admin", ergebnis: "bekannt" }), USER, ERWARTET).slot, null);
  assert.equal(pruefeKernAntwort(guteAntwort({ name: "" }), USER, ERWARTET).name, "");
  for (const benutzer of ["01", "09", "10", "59", "60", "64"]) {
    const k2 = pruefeKernAntwort(
      guteAntwort({}, { benutzer: `wamo-t-${benutzer}`, socket: `/run/wamo-pc/t${benutzer}/pc.sock` }),
      USER,
      ERWARTET,
    );
    assert.equal(k2.benutzer, `wamo-t-${benutzer}`);
  }
});

test("Antwort des Kerns: böse oder fehlerhafte Werte werden abgelehnt", () => {
  const schlecht = {
    "kein Objekt": null,
    "Liste": [],
    "Ergebnis unbekannt": guteAntwort({ ergebnis: "ok" }),
    "wartet": guteAntwort({ status: "beantragt", ziel: null }),
    "gesperrt": guteAntwort({ status: "gesperrt" }),
    "Stufe root": guteAntwort({ stufe: "root" }),
    "fremde user_id": guteAntwort({ user_id: "11111111-2222-4333-8444-555555555555" }),
    "user_id groß": guteAntwort({ user_id: USER.toUpperCase() }),
    "Kennung groß": guteAntwort({ kennung: "pc-ANNA" }),
    "Kennung leer": guteAntwort({ kennung: "pc-" }),
    "Kennung lang": guteAntwort({ kennung: `pc-${"a".repeat(21)}` }),
    "Kennung null": guteAntwort({ kennung: null }),
    "Kennung mit Umbruch": guteAntwort({ kennung: "pc-anna\n" }),
    "Slot 0": guteAntwort({ slot: 0 }),
    "Slot 65": guteAntwort({ slot: 65 }),
    "Slot Text": guteAntwort({ slot: "5" }),
    "Name lang": guteAntwort({ name: "x".repeat(101) }),
    "Name mit Steuerzeichen": guteAntwort({ name: "a\u0000b" }),
    "Name Zahl": guteAntwort({ name: 5 }),
    "ohne Ziel": guteAntwort({ ziel: null }),
    "Benutzer 00": guteAntwort({}, { benutzer: "wamo-t-00", socket: "/run/wamo-pc/t00/pc.sock" }),
    "Benutzer 65": guteAntwort({}, { benutzer: "wamo-t-65", socket: "/run/wamo-pc/t65/pc.sock" }),
    "Benutzer einstellig": guteAntwort({}, { benutzer: "wamo-t-5" }),
    "Benutzer root": guteAntwort({}, { benutzer: "root" }),
    "Benutzer mit Option": guteAntwort({}, { benutzer: "wamo-t-05 -oProxyCommand=x" }),
    "Host Name": guteAntwort({}, { host: "evil.example.com" }),
    "Host 256": guteAntwort({}, { host: "192.168.178.256" }),
    "Host mit Option": guteAntwort({}, { host: "1.2.3.4 -oProxyCommand=x" }),
    "Host mit Umbruch": guteAntwort({}, { host: "192.168.178.136\n" }),
    "Host Bindestrich": guteAntwort({}, { host: "-oProxyCommand=x" }),
    "Host führende Null": guteAntwort({}, { host: "192.168.178.0136" }),
    "Socket anderer Benutzer": guteAntwort({}, { socket: "/run/wamo-pc/t06/pc.sock" }),
    "Socket aufwärts": guteAntwort({}, { socket: "/run/wamo-pc/t05/../t06/pc.sock" }),
    "Socket anderer Name": guteAntwort({}, { socket: "/run/wamo-pc/t05/andere.sock" }),
    "Socket mit Umbruch": guteAntwort({}, { socket: "/run/wamo-pc/t05/pc.sock\n" }),
    "Schlüsseltyp": guteAntwort({}, { hostschluessel: HOSTSCHLUESSEL.replace("ssh-ed25519", "ssh-rsa") }),
    "Schlüssel zu kurz": guteAntwort({}, { hostschluessel: HOSTSCHLUESSEL.slice(0, -1) }),
    "Schlüssel zu lang": guteAntwort({}, { hostschluessel: `${HOSTSCHLUESSEL}A` }),
    "Schlüssel mit Umbruch": guteAntwort({}, { hostschluessel: `${HOSTSCHLUESSEL}\n` }),
    "Schlüssel mit Kommentar": guteAntwort({}, { hostschluessel: `${HOSTSCHLUESSEL} kommentar` }),
    "Schlüssel kein Blob": guteAntwort({}, { hostschluessel: `ssh-ed25519 ${"A".repeat(68)}` }),
  };
  for (const [name, antwort] of Object.entries(schlecht)) {
    assert.throws(() => pruefeKernAntwort(antwort, USER, ERWARTET), GeraetFehler, name);
  }
  assert.throws(() => pruefeKernAntwort(guteAntwort(), null, ERWARTET), GeraetFehler, "ohne Besitzer");
});

// ---------- aktivieren: Dateien, Tunnel ----------

test("aktivieren: schreibt known_hosts und geraet.json, startet den Tunnel genau einmal", async () => {
  const u = umgebung();
  const vor = await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  const r = await u.geraet.aktivieren(guteAntwort());
  assert.equal(r.ok, true);
  assert.deepEqual(r.status, { eingerichtet: true, kennung: "pc-anna", stufe: "mitglied", tunnel: "unbekannt" });

  assert.equal(fs.readFileSync(u.pfade.knownHostsDatei, "utf8"), `192.168.178.136 ${HOSTSCHLUESSEL}\n`);
  assert.equal(path.basename(u.pfade.knownHostsDatei), "known_hosts_wamo_pc");
  const json = JSON.parse(fs.readFileSync(u.pfade.geraetDatei, "utf8"));
  assert.deepEqual(json, {
    v: 1,
    kennung: "pc-anna",
    slot: 5,
    besitzer: USER,
    hostname: "LAPTOP-07",
    name: "Laptop Anna",
    stufe: "mitglied",
    ziel: "wamo-t-05@192.168.178.136",
    socket: "/run/wamo-pc/t05/pc.sock",
    fingerabdruck: vor.fingerprint,
    schluessel: u.pfade.schluesselDatei,
    known_hosts: u.pfade.knownHostsDatei,
  });
  assert.equal(fs.existsSync(`${u.pfade.geraetDatei}.neu`), false);
  assert.equal(fs.existsSync(`${u.pfade.knownHostsDatei}.neu`), false);

  // Datenordner: nur der Benutzer, mit Vererbung für Unterordner und Dateien
  const icacls = u.aufrufeVon("icacls.exe").map((a) => a.args);
  assert.deepEqual(icacls[1], [u.pfade.datenOrdner, "/inheritance:r", "/grant:r", `*${FAKE_SID}:(OI)(CI)F`]);

  // Tunnel: PowerShell mit dem Skript, 120 s Frist
  const ps = u.aufrufeVon("powershell.exe");
  assert.equal(ps.length, 1);
  assert.deepEqual(ps[0].args, [
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    path.join(u.repo, "scripts", "wamo-tunnel-einrichten.ps1"),
    "-Geraet",
    "-Starten",
  ]);
  assert.equal(ps[0].optionen.frist, 120_000);

  // gleiche Antwort noch einmal: nichts ändert sich, kein zweiter Start, keine zweite Rechtevergabe
  const stand = fs.statSync(u.pfade.geraetDatei).mtimeMs;
  await u.geraet.aktivieren(guteAntwort());
  assert.equal(u.aufrufeVon("powershell.exe").length, 1);
  assert.equal(u.aufrufeVon("icacls.exe").length, 2);
  assert.equal(fs.statSync(u.pfade.geraetDatei).mtimeMs, stand);

  // geänderte Angaben (anderer Name): Datei neu, Tunnel-Skript noch einmal
  await u.geraet.aktivieren(guteAntwort({ name: "Laptop Anna 2" }));
  assert.equal(JSON.parse(fs.readFileSync(u.pfade.geraetDatei, "utf8")).name, "Laptop Anna 2");
  assert.equal(u.aufrufeVon("powershell.exe").length, 2);
});

test("aktivieren: Antwort eines anderen Benutzers, wartendes Gerät oder fehlendes Signieren schreiben nichts", async () => {
  const u = umgebung();
  await u.geraet.vorbereiten();
  // noch nicht signiert: kein Besitzer
  await assert.rejects(u.geraet.aktivieren(guteAntwort()), GeraetFehler);
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await assert.rejects(u.geraet.aktivieren(guteAntwort({ user_id: "11111111-2222-4333-8444-555555555555" })), /gehört nicht zur aktuellen Anmeldung/);
  await assert.rejects(u.geraet.aktivieren(guteAntwort({ status: "beantragt", ziel: null })), /noch nicht freigegeben/);
  await assert.rejects(u.geraet.aktivieren(guteAntwort({}, { host: "evil.example.com" })), GeraetFehler);
  assert.equal(fs.existsSync(u.pfade.geraetDatei), false);
  assert.equal(fs.existsSync(u.pfade.knownHostsDatei), false);
  assert.equal(fs.existsSync(u.pfade.datenOrdner), false);
  assert.equal(u.aufrufeVon("powershell.exe").length, 0);
  // Besitzer ist der, für den zuletzt signiert wurde
  const anderer = "11111111-2222-4333-8444-555555555555";
  await u.geraet.signiere({ nonce: NONCE, userId: anderer });
  await assert.rejects(u.geraet.aktivieren(guteAntwort()), /gehört nicht zur aktuellen Anmeldung/);
});

test("aktivieren: schlägt das Skript fehl, kommt ein Fehler und beim nächsten Versuch wird neu gestartet", async () => {
  let kaputt = true;
  const u = umgebung({ scheitere: (datei) => kaputt && datei.endsWith("powershell.exe") });
  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await assert.rejects(u.geraet.aktivieren(guteAntwort()), (e) => e instanceof GeraetFehler && /Tunnel/.test(e.text));
  kaputt = false;
  const r = await u.geraet.aktivieren(guteAntwort());
  assert.equal(r.ok, true);
  assert.equal(u.aufrufeVon("powershell.exe").length, 2);
});

test("aktivieren: fehlt das Einrichtungsskript, kommt ein Fehler", async () => {
  const u = umgebung();
  fs.rmSync(path.join(u.repo, "scripts", "wamo-tunnel-einrichten.ps1"));
  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await assert.rejects(u.geraet.aktivieren(guteAntwort()), /Einrichtungsskript/);
});

test("Atomar: bei Fehler beim Umbenennen bleibt die alte Datei und kein Rest liegt herum", () => {
  const ordner = fs.mkdtempSync(path.join(os.tmpdir(), "wamo-geraet-test-"));
  aufzuraeumen.push(ordner);
  const datei = path.join(ordner, "geraet.json");
  fs.writeFileSync(datei, "alt");
  schreibeAtomar(datei, "neu");
  assert.equal(fs.readFileSync(datei, "utf8"), "neu");
  assert.deepEqual(fs.readdirSync(ordner), ["geraet.json"]);

  const kaputt = { ...fs, renameSync: () => { throw new Error("gesperrt"); } };
  assert.throws(() => schreibeAtomar(datei, "kaputt", kaputt), /gesperrt/);
  assert.equal(fs.readFileSync(datei, "utf8"), "neu");
  assert.deepEqual(fs.readdirSync(ordner), ["geraet.json"]);

  // Der Inhalt wird erst in eine Nebendatei geschrieben (nie halb in die Zieldatei)
  const reihenfolge = [];
  const spur = {
    ...fs,
    writeFileSync: (p, ...rest) => { reihenfolge.push(["schreibe", path.basename(p)]); return fs.writeFileSync(p, ...rest); },
    renameSync: (a, b) => { reihenfolge.push(["benenne", path.basename(a), path.basename(b)]); return fs.renameSync(a, b); },
  };
  schreibeAtomar(datei, "dritte", spur);
  assert.deepEqual(reihenfolge, [["schreibe", "geraet.json.neu"], ["benenne", "geraet.json.neu", "geraet.json"]]);
});

test("Nichts wird außerhalb des Testordners geschrieben oder geändert", async () => {
  const echteSsh = path.join(os.homedir(), ".ssh");
  const beobachtet = ["id_ed25519_wamo_pc", "id_ed25519_wamo_pc.pub", "known_hosts_wamo_pc"].map((n) => path.join(echteSsh, n));
  const zustand = () => beobachtet.map((p) => { try { return fs.statSync(p).mtimeMs; } catch { return null; } });
  const vorher = zustand();

  const u = umgebung();
  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await u.geraet.aktivieren(guteAntwort());
  u.setzeZeit(u.zeit() + 5 * 60_000);
  await u.geraet.waechterTick();

  assert.deepEqual(zustand(), vorher);
  // alle Pfade, die Programme als Argument bekamen (icacls, PowerShell -File), liegen im Testordner
  let pfadArgumente = 0;
  for (const a of u.aufrufe) {
    for (const arg of a.args) {
      if (/^[A-Za-z]:\\/.test(arg)) {
        pfadArgumente++;
        assert.ok(arg.startsWith(u.wurzel), `${a.datei} ${arg}`);
      }
    }
  }
  assert.equal(pfadArgumente, 3); // Schlüsseldatei, Datenordner, Skript
  // Dateien im Testordner: genau die erwarteten
  const alle = [];
  const gehe = (p) => {
    for (const e of fs.readdirSync(p, { withFileTypes: true })) {
      const voll = path.join(p, e.name);
      if (e.isDirectory()) gehe(voll);
      else alle.push(path.relative(u.wurzel, voll).replaceAll("\\", "/"));
    }
  };
  gehe(u.wurzel);
  assert.deepEqual(alle.sort(), [
    "home/.ssh/id_ed25519_wamo_pc",
    "home/.ssh/id_ed25519_wamo_pc.pub",
    "home/.ssh/known_hosts_wamo_pc",
    "lokal/WAMO/geraet.json",
    "repo/scripts/wamo-tunnel-einrichten.ps1",
  ]);
});

test("geraet.js importiert kein electron (testbar ohne App)", () => {
  const quelle = fs.readFileSync(path.join(__dirname, "geraet.js"), "utf8");
  assert.ok(!/require\(["']electron["']\)/.test(quelle));
});

// ---------- Status, Tunnel, Wächter ----------

function statusDatei(ts, rest = {}) {
  return { v: 1, pid: 4242, ts: new Date(ts).toISOString(), host: true, tunnel: true, seit: new Date(ts - 600_000).toISOString(), modus: "geraet", ...rest };
}

test("Tunnel-Zustand aus tunnel.status", () => {
  const jetzt = START;
  assert.equal(tunnelZustand(statusDatei(jetzt - 5000), jetzt), "verbunden");
  assert.equal(tunnelZustand(statusDatei(jetzt - 5000, { tunnel: false }), jetzt), "host");
  assert.equal(tunnelZustand(statusDatei(jetzt - 5000, { tunnel: false, host: false }), jetzt), "aus");
  assert.equal(tunnelZustand(statusDatei(jetzt - STATUS_FRISCH_MS - 1), jetzt), "aus"); // veraltet
  assert.equal(tunnelZustand(statusDatei(jetzt - STATUS_FRISCH_MS), jetzt), "verbunden"); // genau an der Grenze noch frisch
  assert.equal(tunnelZustand(null, jetzt), "unbekannt");
  assert.equal(tunnelZustand({ v: 2, ts: new Date(jetzt).toISOString() }, jetzt), "unbekannt");
  assert.equal(tunnelZustand({ v: 1, ts: "gestern" }, jetzt), "unbekannt");
  assert.equal(tunnelZustand({ v: 1 }, jetzt), "unbekannt");
  assert.equal(tunnelZustand("text", jetzt), "unbekannt");
});

test("status(): nicht eingerichtet, eingerichtet mit Tunnel-Zustand", async () => {
  const u = umgebung();
  assert.deepEqual(u.geraet.status(), { eingerichtet: false, kennung: null, stufe: null, tunnel: "unbekannt" });
  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await u.geraet.aktivieren(guteAntwort({ stufe: "admin" }));
  assert.equal(u.geraet.status().tunnel, "unbekannt"); // noch keine tunnel.status
  fs.writeFileSync(u.pfade.tunnelStatusDatei, JSON.stringify(statusDatei(u.zeit() - 3000)));
  assert.deepEqual(u.geraet.status(), { eingerichtet: true, kennung: "pc-anna", stufe: "admin", tunnel: "verbunden" });
  fs.writeFileSync(u.pfade.tunnelStatusDatei, JSON.stringify(statusDatei(u.zeit() - 3000, { tunnel: false })));
  assert.equal(u.geraet.status().tunnel, "host");
  u.setzeZeit(u.zeit() + 90_000);
  assert.equal(u.geraet.status().tunnel, "aus");
  fs.writeFileSync(u.pfade.tunnelStatusDatei, "{kaputt");
  assert.equal(u.geraet.status().tunnel, "unbekannt");
  fs.writeFileSync(u.pfade.geraetDatei, JSON.stringify({ v: 1, kennung: "PC kaputt" }));
  assert.equal(u.geraet.status().eingerichtet, false);
});

test("Wächter-Entscheidung (reine Funktion mit eingespeister Uhr)", () => {
  const jetzt = START;
  const frisch = statusDatei(jetzt - 10_000);
  const alt = statusDatei(jetzt - 61_000);
  const basis = { eingerichtet: true, tunnelStatus: null, jetzt, letzterStart: null };
  assert.equal(waechterEntscheidung({ ...basis, eingerichtet: false }).starten, false);
  assert.equal(waechterEntscheidung({ ...basis, tunnelStatus: frisch }).starten, false);
  assert.equal(waechterEntscheidung({ ...basis, tunnelStatus: statusDatei(jetzt - 10_000, { tunnel: false, host: false }) }).starten, false);
  assert.deepEqual(waechterEntscheidung({ ...basis, tunnelStatus: null }), { starten: true, grund: "Status fehlt" });
  assert.deepEqual(waechterEntscheidung({ ...basis, tunnelStatus: alt }), { starten: true, grund: "Status veraltet" });
  assert.equal(waechterEntscheidung({ ...basis, tunnelStatus: { v: 1, ts: "kaputt" } }).starten, true);
  assert.equal(waechterEntscheidung({ ...basis, tunnelStatus: statusDatei(jetzt - STATUS_FRISCH_MS) }).starten, false);
  // frühestens nach 2 Minuten erneut
  assert.equal(waechterEntscheidung({ ...basis, letzterStart: jetzt - NEUSTART_ABSTAND_MS + 1 }).starten, false);
  assert.equal(waechterEntscheidung({ ...basis, letzterStart: jetzt - NEUSTART_ABSTAND_MS }).starten, true);
  // während das Einrichtungsskript läuft, nie
  assert.equal(waechterEntscheidung({ ...basis, skriptLaeuft: true }).starten, false);
});

test("Wächter: stößt die Aufgabe an, wenn der Überwacher schweigt, und nicht öfter als alle 2 Minuten", async () => {
  const u = umgebung();
  const schtasks = () => u.aufrufeVon("schtasks.exe");
  // nicht eingerichtet: nie
  assert.equal((await u.geraet.waechterTick()).starten, false);
  assert.equal(schtasks().length, 0);

  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await u.geraet.aktivieren(guteAntwort());
  // gerade erst eingerichtet (Skript lief eben): kein Anstoß
  assert.equal((await u.geraet.waechterTick()).starten, false);

  u.setzeZeit(u.zeit() + NEUSTART_ABSTAND_MS);
  assert.equal((await u.geraet.waechterTick()).starten, true);
  assert.equal(schtasks().length, 1);
  assert.deepEqual(schtasks()[0].args, ["/Run", "/TN", "WAMO Tunnel"]);
  // 30 s später: zu früh
  u.setzeZeit(u.zeit() + 30_000);
  assert.equal((await u.geraet.waechterTick()).starten, false);
  // Überwacher meldet sich wieder: kein Anstoß, auch nach langer Zeit
  u.setzeZeit(u.zeit() + 300_000);
  fs.writeFileSync(u.pfade.tunnelStatusDatei, JSON.stringify(statusDatei(u.zeit() - 2000)));
  assert.equal((await u.geraet.waechterTick()).starten, false);
  // Überwacher schweigt wieder: nach Ablauf der Frist erneut
  u.setzeZeit(u.zeit() + 90_000);
  assert.equal((await u.geraet.waechterTick()).starten, true);
  assert.equal(schtasks().length, 2);
  // der Wächter beendet nie Prozesse: nur schtasks /Run kam vor
  assert.equal(u.aufrufe.filter((a) => /taskkill|Stop-Process|\/End/i.test(`${a.datei} ${a.args.join(" ")}`)).length, 0);
});

test("Wächter: scheitert schtasks, geht es trotzdem erst nach 2 Minuten weiter", async () => {
  const u = umgebung({ scheitere: (datei) => datei.endsWith("schtasks.exe") });
  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await u.geraet.aktivieren(guteAntwort());
  u.setzeZeit(u.zeit() + NEUSTART_ABSTAND_MS);
  assert.equal((await u.geraet.waechterTick()).starten, true);
  u.setzeZeit(u.zeit() + 30_000);
  assert.equal((await u.geraet.waechterTick()).starten, false);
  assert.equal(u.aufrufeVon("schtasks.exe").length, 1);
});

test("Wächter: läuft alle 30 s und lässt sich anhalten", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const u = umgebung();
  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await u.geraet.aktivieren(guteAntwort());
  u.setzeZeit(u.zeit() + NEUSTART_ABSTAND_MS);
  u.geraet.starteWaechter();
  u.geraet.starteWaechter(); // zweiter Start ändert nichts
  t.mock.timers.tick(29_000);
  await new Promise((r) => setImmediate(r));
  assert.equal(u.aufrufeVon("schtasks.exe").length, 0);
  t.mock.timers.tick(1_000);
  await new Promise((r) => setImmediate(r));
  assert.equal(u.aufrufeVon("schtasks.exe").length, 1);
  u.geraet.stoppe();
  u.setzeZeit(u.zeit() + NEUSTART_ABSTAND_MS);
  t.mock.timers.tick(120_000);
  await new Promise((r) => setImmediate(r));
  assert.equal(u.aufrufeVon("schtasks.exe").length, 1);
});

// ---------- Sicherheitsreview: Ziel, Hostschlüssel, Besitzer ----------

test("Antwort des Kerns: anderes Tunnelziel oder anderer Hostschlüssel wird abgelehnt", () => {
  assert.throws(() => pruefeKernAntwort(guteAntwort({}, { host: "10.0.0.5" }), USER, ERWARTET), GeraetFehler, "anderes Ziel");
  const fremderBlob = publikBlob(Buffer.alloc(32, 9)).toString("base64");
  assert.throws(
    () => pruefeKernAntwort(guteAntwort({}, { hostschluessel: `ssh-ed25519 ${fremderBlob}` }), USER, ERWARTET),
    GeraetFehler,
    "anderer Hostschlüssel",
  );
  // Standard: der festgehaltene Fingerabdruck von Sokrates-2 (der Prüfwert aus dem Test passt dazu nicht)
  assert.throws(() => pruefeKernAntwort(guteAntwort(), USER), GeraetFehler, "Standard-Fingerabdruck");
});

test("erwartetesZiel: Umgebung des Hauptprozesses, ungültige Werte brechen ab", () => {
  const { erwartetesZiel, HOSTSCHLUESSEL_FP_STANDARD, SSH_HOST_STANDARD } = require("./geraet");
  assert.deepEqual(erwartetesZiel({}, {}), { host: SSH_HOST_STANDARD, fingerabdruecke: HOSTSCHLUESSEL_FP_STANDARD });
  assert.deepEqual(erwartetesZiel({}, { WAMO_PC_SSH_HOST: "10.8.0.1", WAMO_PC_SSH_HOSTKEY_FP: `${HOST_FP}, ${HOST_FP}` }), {
    host: "10.8.0.1",
    fingerabdruecke: [HOST_FP, HOST_FP],
  });
  assert.throws(() => erwartetesZiel({}, { WAMO_PC_SSH_HOST: "evil.example.com" }));
  assert.throws(() => erwartetesZiel({}, { WAMO_PC_SSH_HOSTKEY_FP: "SHA256:zu-kurz" }));
});

test("Aktivieren: der Besitzer einer vorhandenen geraet.json ist unveränderlich", async () => {
  const u = umgebung();
  await u.geraet.vorbereiten();
  await u.geraet.signiere({ nonce: NONCE, userId: USER });
  await u.geraet.aktivieren(guteAntwort());
  const vorher = fs.readFileSync(u.pfade.geraetDatei, "utf8");
  const ANDERER = "11111111-2222-4333-8444-555555555555";
  await u.geraet.signiere({ nonce: NONCE, userId: ANDERER }); // die Oberfläche behauptet ein anderes Profil
  await assert.rejects(u.geraet.aktivieren(guteAntwort({ user_id: ANDERER })), GeraetFehler);
  assert.equal(fs.readFileSync(u.pfade.geraetDatei, "utf8"), vorher, "geraet.json bleibt unverändert");
});

test("Schlüsseldatei: erst leer anlegen, Rechte setzen, dann den Inhalt schreiben", async () => {
  const reihenfolge = [];
  const u2 = umgebung({
    scheitere: (datei) => {
      if (datei.toLowerCase().endsWith("icacls.exe")) {
        const dateien = fs.existsSync(path.join(u2.home, ".ssh")) ? fs.readdirSync(path.join(u2.home, ".ssh")) : [];
        const schluessel = path.join(u2.home, ".ssh", "id_ed25519_wamo_pc");
        reihenfolge.push(dateien.includes("id_ed25519_wamo_pc") ? fs.statSync(schluessel).size : -1);
      }
      return false;
    },
  });
  await u2.geraet.vorbereiten();
  assert.ok(reihenfolge.includes(0), "icacls läuft, solange die Schlüsseldatei noch leer ist");
  assert.ok(fs.statSync(path.join(u2.home, ".ssh", "id_ed25519_wamo_pc")).size > 100, "danach steht der Schlüssel darin");
});
