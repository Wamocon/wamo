// WAMO-Desktop: Einrichtung des Laptops bei der Anmeldung (reines Node-Modul, ohne electron-Import, testbar).
//
// Ablauf (die Oberfläche steuert ihn, ui/src/lib/geraet.ts):
//   vorbereiten  eigenen ed25519-Geräteschlüssel anlegen (nur wenn er fehlt, nie überschreiben) und den öffentlichen
//                Teil liefern
//   signieren    die Herausforderung des Kerns mit dem Geräteschlüssel signieren (feste Nachricht, nie freie Daten)
//   aktivieren   die Antwort des Kerns streng prüfen, known_hosts und geraet.json schreiben, den Tunnel starten
//   status       eingerichtet? Tunnel verbunden? (liest tunnel.status des Überwachers)
// Dazu ein Wächter, der die geplante Aufgabe "WAMO Tunnel" neu anstößt, wenn der Überwacher nicht mehr schreibt.
//
// Der private Schlüssel liegt im OpenSSH-Format unter %USERPROFILE%\.ssh (ssh.exe liest ihn direkt) und verlässt
// diesen Prozess nie. IP und Rechnername sind nur Anzeige: die Identität ist Schlüssel plus Anmeldung.
// Alle Pfade, die Uhr und das Ausführen von Programmen sind einstellbar (für die Tests); nichts wird geschrieben,
// was nicht unter den übergebenen Ordnern liegt.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFile } = require("node:child_process");

const SCHLUESSEL_NAME = "id_ed25519_wamo_pc";
const KNOWN_HOSTS_NAME = "known_hosts_wamo_pc";
const TUNNEL_AUFGABE = "WAMO Tunnel";
const SKRIPT_NAME = "wamo-tunnel-einrichten.ps1";
const SKRIPT_FRIST_MS = 120_000; // Einrichtungsskript
const KURZ_FRIST_MS = 30_000; // whoami, icacls, schtasks
const WAECHTER_ABSTAND_MS = 30_000;
const STATUS_FRISCH_MS = 60_000; // tunnel.status ist frisch, wenn ts höchstens so alt ist
const NEUSTART_ABSTAND_MS = 120_000; // die Aufgabe frühestens nach 2 Minuten erneut anstoßen

const RE_HOSTNAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,62}$/;
const RE_NONCE = /^[A-Za-z0-9_-]{8,200}$/;
const RE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const RE_KENNUNG = /^pc-[a-z0-9]+(?:-[a-z0-9]+)*$/; // wie devices_kennung_chk; Länge unten (höchstens 23)
const MAX_KENNUNG = 23;
const RE_BENUTZER = /^wamo-t-(0[1-9]|[1-5][0-9]|6[0-4])$/;
const RE_SOCKET = /^\/run\/wamo-pc\/t(0[1-9]|[1-5][0-9]|6[0-4])\/pc\.sock$/;
const OKTETT = "(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])";
const RE_IPV4 = new RegExp(`^${OKTETT}(?:\\.${OKTETT}){3}$`);
const RE_HOSTSCHLUESSEL = /^ssh-ed25519 [A-Za-z0-9+/]{68}$/;
// Tunnelziel und Fingerabdruck des SSH-Hostschlüssels von Sokrates-2 (ED25519, geprüft am 21.10.2026 gegen
// /etc/ssh/ssh_host_ed25519_key.pub). Beides kommt aus dieser Datei bzw. der Umgebung des Hauptprozesses
// (WAMO_PC_SSH_HOST, WAMO_PC_SSH_HOSTKEY_FP, Liste mit Komma), nie aus der Oberfläche: Eine Antwort mit anderem Ziel
// oder anderem Schlüssel wird abgelehnt, ein vorhandener Eintrag nie still ersetzt. Wechselt Sokrates-2 den Schlüssel,
// kommt der neue Fingerabdruck mit einer App-Version oder über WAMO_PC_SSH_HOSTKEY_FP.
const SSH_HOST_STANDARD = "192.168.178.136";
const HOSTSCHLUESSEL_FP_STANDARD = ["SHA256:FI+Dtt+vY0zJgLOwu7IajO50ywDaPwznZ9UgwJLN4lQ"];
const RE_FINGERABDRUCK = /^SHA256:[A-Za-z0-9+/]{43}$/;
const RE_STEUERZEICHEN = /[\u0000-\u001f\u007f]/;
const STUFEN = ["admin", "mitglied"];
const ERGEBNISSE = ["neu_aktiv", "neu_beantragt", "bekannt"];

/** Fehler mit einem Text, den die Oberfläche anzeigen darf (deutsch, ohne interne Einzelheiten). */
class GeraetFehler extends Error {
  constructor(text) {
    super(text);
    this.name = "GeraetFehler";
    this.text = text;
  }
}

// ---------- SSH-Datenformat ----------

function uint32(n) {
  const b = Buffer.alloc(4);
  b.writeUInt32BE(n >>> 0);
  return b;
}

/** SSH-"string": Länge (4 Byte) plus Inhalt. */
function sshString(inhalt) {
  const b = Buffer.isBuffer(inhalt) ? inhalt : Buffer.from(String(inhalt), "utf8");
  return Buffer.concat([uint32(b.length), b]);
}

/** Öffentlicher Schlüssel im SSH-Drahtformat (Typ plus 32 Byte). */
function publikBlob(pub) {
  return Buffer.concat([sshString("ssh-ed25519"), sshString(pub)]);
}

class Leser {
  constructor(buf) {
    this.buf = buf;
    this.pos = 0;
  }

  uint32() {
    if (this.pos + 4 > this.buf.length) throw new Error("zu kurz");
    const v = this.buf.readUInt32BE(this.pos);
    this.pos += 4;
    return v;
  }

  string() {
    const n = this.uint32();
    if (n > this.buf.length - this.pos) throw new Error("zu kurz");
    const teil = this.buf.subarray(this.pos, this.pos + n);
    this.pos += n;
    return teil;
  }
}

/** Privatschlüssel im Format "openssh-key-v1", ohne Passphrase (ssh.exe liest ihn so). */
function opensshPrivat(seed, pub, kommentar, pruefzahl = crypto.randomBytes(4)) {
  if (seed.length !== 32 || pub.length !== 32) throw new Error("Schlüssellänge");
  const innen = Buffer.concat([
    pruefzahl,
    pruefzahl,
    sshString("ssh-ed25519"),
    sshString(pub),
    sshString(Buffer.concat([seed, pub])),
    sshString(kommentar),
  ]);
  const fehlend = (8 - (innen.length % 8)) % 8;
  const polster = Buffer.from(Array.from({ length: fehlend }, (_, i) => i + 1));
  const roh = Buffer.concat([
    Buffer.from("openssh-key-v1\0", "latin1"),
    sshString("none"), // Verschlüsselung
    sshString("none"), // Schlüsselableitung
    sshString(""),
    uint32(1),
    sshString(publikBlob(pub)),
    sshString(Buffer.concat([innen, polster])),
  ]);
  const zeilen = roh.toString("base64").match(/.{1,70}/g).join("\n");
  return `-----BEGIN OPENSSH PRIVATE KEY-----\n${zeilen}\n-----END OPENSSH PRIVATE KEY-----\n`;
}

/**
 * Liest eine unverschlüsselte OpenSSH-Privatschlüsseldatei (nur ed25519) streng und prüft, dass der öffentliche Teil
 * zum Seed passt. Liefert { pub, kommentar, privKey } (privKey ist ein KeyObject, der Seed wird gelöscht).
 */
function leseOpensshPrivat(text) {
  const treffer = /^-----BEGIN OPENSSH PRIVATE KEY-----\s+([A-Za-z0-9+/=\s]+?)\s+-----END OPENSSH PRIVATE KEY-----\s*$/.exec(
    String(text),
  );
  if (!treffer) throw new Error("kein OpenSSH-Schlüssel");
  const roh = Buffer.from(treffer[1].replace(/\s+/g, ""), "base64");
  const kopf = Buffer.from("openssh-key-v1\0", "latin1");
  if (roh.length < kopf.length || !roh.subarray(0, kopf.length).equals(kopf)) throw new Error("falsche Kennung");
  const aussen = new Leser(roh.subarray(kopf.length));
  const chiffre = aussen.string().toString("latin1");
  const ableitung = aussen.string().toString("latin1");
  aussen.string(); // Ableitungsoptionen
  if (chiffre !== "none" || ableitung !== "none") throw new Error("mit Passphrase");
  if (aussen.uint32() !== 1) throw new Error("nicht genau ein Schlüssel");
  const blob = aussen.string();
  const innen = new Leser(aussen.string());
  const a = innen.uint32();
  const b = innen.uint32();
  if (a !== b) throw new Error("Prüfzahlen verschieden");
  if (innen.string().toString("latin1") !== "ssh-ed25519") throw new Error("kein ed25519");
  const pub = Buffer.from(innen.string());
  const privat = Buffer.from(innen.string());
  const kommentar = innen.string().toString("utf8");
  if (pub.length !== 32 || privat.length !== 64 || !privat.subarray(32).equals(pub)) throw new Error("Schlüssellänge");
  if (!blob.equals(publikBlob(pub))) throw new Error("öffentlicher Teil weicht ab");
  const seed = privat.subarray(0, 32);
  const privKey = crypto.createPrivateKey({
    key: { kty: "OKP", crv: "Ed25519", d: seed.toString("base64url"), x: pub.toString("base64url") },
    format: "jwk",
  });
  seed.fill(0);
  if (crypto.createPublicKey(privKey).export({ format: "jwk" }).x !== pub.toString("base64url")) {
    throw new Error("Seed passt nicht zum öffentlichen Teil");
  }
  return { pub, kommentar, privKey };
}

/** "SHA256:<Base64 ohne =>" des öffentlichen Blobs (so zeigt es auch ssh-keygen -l). */
function fingerabdruck(blob) {
  return `SHA256:${crypto.createHash("sha256").update(blob).digest("base64").replace(/=+$/, "")}`;
}

/** Die Nachricht, die der Geräteschlüssel signiert (Kern und Gerät bauen sie gleich). */
function nachrichtBinden({ nonce, userId, hostname }) {
  return `wamo-geraet-binden\n${nonce}\n${userId}\n${hostname}`;
}

// ---------- Prüfungen ----------

function pruefeHostname(name) {
  if (typeof name !== "string" || !RE_HOSTNAME.test(name)) {
    throw new GeraetFehler("Der Rechnername dieses Laptops enthält Zeichen, die WAMO nicht annimmt (erlaubt: Buchstaben, Ziffern, Bindestrich).");
  }
  return name;
}

const istObjekt = (v) => typeof v === "object" && v !== null && !Array.isArray(v);

/** Prüft ein SSH-Schlüsselobjekt "ssh-ed25519 <Base64>" bis in den Blob hinein. */
function pruefeHostschluessel(wert) {
  if (typeof wert !== "string" || !RE_HOSTSCHLUESSEL.test(wert)) return false;
  const blob = Buffer.from(wert.slice("ssh-ed25519 ".length), "base64");
  try {
    const l = new Leser(blob);
    return l.string().toString("latin1") === "ssh-ed25519" && l.string().length === 32 && l.pos === blob.length;
  } catch {
    return false;
  }
}

/**
 * Prüft die Antwort des Kerns auf /geraet/binden streng. Nur ein aktives Gerät mit vollständigem Ziel wird
 * angenommen, user_id muss dem Besitzer entsprechen, für den zuletzt signiert wurde. Liefert die bereinigten Werte
 * oder wirft GeraetFehler (dann wird nichts geschrieben).
 */
function pruefeKernAntwort(antwort, besitzer, erwartet = { host: SSH_HOST_STANDARD, fingerabdruecke: HOSTSCHLUESSEL_FP_STANDARD }) {
  const schlecht = () => new GeraetFehler("Die Antwort des Kerns zur Gerätebindung ist unvollständig oder ungültig.");
  if (!istObjekt(antwort)) throw schlecht();
  if (!ERGEBNISSE.includes(antwort.ergebnis)) throw schlecht();
  if (antwort.status !== "aktiv") throw new GeraetFehler("Der Laptop ist noch nicht freigegeben.");
  if (!STUFEN.includes(antwort.stufe)) throw schlecht();
  if (typeof antwort.user_id !== "string" || !RE_UUID.test(antwort.user_id)) throw schlecht();
  if (!besitzer || antwort.user_id !== besitzer) {
    throw new GeraetFehler("Die Antwort des Kerns gehört nicht zur aktuellen Anmeldung.");
  }
  if (typeof antwort.kennung !== "string" || !RE_KENNUNG.test(antwort.kennung) || antwort.kennung.length > MAX_KENNUNG) throw schlecht();
  const slot = antwort.slot;
  if (slot !== null && slot !== undefined && !(Number.isInteger(slot) && slot >= 1 && slot <= 64)) throw schlecht();
  const name = antwort.name;
  if (typeof name !== "string" || name.length > 100 || RE_STEUERZEICHEN.test(name)) throw schlecht();
  const ziel = antwort.ziel;
  if (!istObjekt(ziel)) throw schlecht();
  const { benutzer, host, socket, hostschluessel } = ziel;
  if (typeof benutzer !== "string" || !RE_BENUTZER.test(benutzer)) throw schlecht();
  if (typeof host !== "string" || !RE_IPV4.test(host)) throw schlecht();
  if (host !== erwartet.host) throw new GeraetFehler("Die Antwort nennt ein anderes Tunnelziel als dieses WAMO erwartet.");
  const nummer = /^wamo-t-(\d{2})$/.exec(benutzer)[1];
  if (typeof socket !== "string" || !RE_SOCKET.test(socket) || socket !== `/run/wamo-pc/t${nummer}/pc.sock`) {
    throw schlecht();
  }
  if (!pruefeHostschluessel(hostschluessel)) throw schlecht();
  if (!erwartet.fingerabdruecke.includes(fingerabdruck(Buffer.from(hostschluessel.slice("ssh-ed25519 ".length), "base64")))) {
    throw new GeraetFehler("Der Hostschlüssel von Sokrates-2 passt nicht zum erwarteten Fingerabdruck. Bitte den IT-Support rufen.");
  }
  return {
    kennung: antwort.kennung,
    slot: slot ?? null,
    besitzer: antwort.user_id,
    name: name.trim(),
    stufe: antwort.stufe,
    benutzer,
    host,
    socket,
    hostschluessel,
  };
}

// ---------- Tunnel-Status und Wächter (reine Funktionen) ----------

/** "verbunden" | "host" | "aus" | "unbekannt" aus dem Inhalt von tunnel.status. */
function tunnelZustand(status, jetztMs) {
  if (!istObjekt(status) || status.v !== 1) return "unbekannt";
  const ts = typeof status.ts === "string" ? Date.parse(status.ts) : NaN;
  if (!Number.isFinite(ts)) return "unbekannt";
  if (Math.abs(jetztMs - ts) > STATUS_FRISCH_MS) return "aus"; // der Überwacher schreibt nicht mehr
  if (status.tunnel === true) return "verbunden";
  if (status.host === true) return "host";
  return "aus";
}

/**
 * Soll der Wächter die Aufgabe "WAMO Tunnel" anstoßen? Nur wenn das Gerät eingerichtet ist, tunnel.status fehlt oder
 * zu alt ist (der Überwacher läuft nicht), gerade kein Skript läuft und der letzte Anstoß mindestens 2 Minuten her ist.
 */
function waechterEntscheidung({ eingerichtet, tunnelStatus, jetzt, letzterStart = null, skriptLaeuft = false }) {
  if (!eingerichtet) return { starten: false, grund: "nicht eingerichtet" };
  if (skriptLaeuft) return { starten: false, grund: "Einrichtung läuft" };
  const ts = istObjekt(tunnelStatus) && typeof tunnelStatus.ts === "string" ? Date.parse(tunnelStatus.ts) : NaN;
  const frisch = Number.isFinite(ts) && Math.abs(jetzt - ts) <= STATUS_FRISCH_MS;
  if (frisch) return { starten: false, grund: "Überwacher läuft" };
  if (letzterStart !== null && jetzt - letzterStart < NEUSTART_ABSTAND_MS) return { starten: false, grund: "zu früh" };
  return { starten: true, grund: Number.isFinite(ts) ? "Status veraltet" : "Status fehlt" };
}

// ---------- Dateien ----------

/** Datei atomar ersetzen: erst daneben schreiben, dann umbenennen; bei Fehler bleibt die alte Datei, nichts liegt herum. */
function schreibeAtomar(datei, text, dateisystem = fs) {
  const tmp = `${datei}.neu`;
  try {
    dateisystem.writeFileSync(tmp, text, { encoding: "utf8" });
    dateisystem.renameSync(tmp, datei);
  } catch (err) {
    try {
      dateisystem.rmSync(tmp, { force: true });
    } catch {
      // nichts mehr zu tun
    }
    throw err;
  }
}

function standardAusfuehren(datei, args, optionen = {}) {
  return new Promise((resolve, reject) => {
    execFile(
      datei,
      args,
      { windowsHide: true, timeout: optionen.frist ?? KURZ_FRIST_MS, encoding: "utf8", maxBuffer: 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err) {
          err.stdout = stdout;
          err.stderr = stderr;
          reject(err);
        } else {
          resolve({ stdout, stderr });
        }
      },
    );
  });
}

// ---------- Fassade ----------

/**
 * Optionen (alle für die Tests einstellbar): env, home, localAppData, systemRoot, repo, hostname (Funktion),
 * ausfuehren(datei, args, { frist }) -> Promise<{ stdout }>, fs, jetzt (Funktion, Millisekunden), sid, log(text),
 * vorTunnel() (async, läuft vor dem Einrichtungsskript; wirft Fehler mit .text).
 */
/** Tunnelziel und erlaubte Hostschlüssel-Fingerabdrücke aus Optionen (Tests), Umgebung des Hauptprozesses oder Standard. */
function erwartetesZiel(opt, env) {
  const host = opt.sshHost ?? env.WAMO_PC_SSH_HOST ?? SSH_HOST_STANDARD;
  if (typeof host !== "string" || !RE_IPV4.test(host)) throw new Error("WAMO_PC_SSH_HOST ist keine IPv4-Adresse");
  const roh = opt.hostFingerabdruecke ?? (env.WAMO_PC_SSH_HOSTKEY_FP ? String(env.WAMO_PC_SSH_HOSTKEY_FP).split(",") : HOSTSCHLUESSEL_FP_STANDARD);
  const fingerabdruecke = roh.map((f) => String(f).trim());
  if (fingerabdruecke.length === 0 || !fingerabdruecke.every((f) => RE_FINGERABDRUCK.test(f))) {
    throw new Error("WAMO_PC_SSH_HOSTKEY_FP enthält keinen gültigen Fingerabdruck");
  }
  return { host, fingerabdruecke };
}

function erzeugeGeraet(opt = {}) {
  const env = opt.env ?? process.env;
  const home = opt.home ?? env.USERPROFILE ?? os.homedir();
  const localAppData = opt.localAppData ?? env.LOCALAPPDATA ?? path.join(home, "AppData", "Local");
  const systemRoot = opt.systemRoot ?? env.SystemRoot ?? env.windir ?? "C:\\Windows";
  const ausfuehren = opt.ausfuehren ?? standardAusfuehren;
  const dsys = opt.fs ?? fs;
  const jetzt = opt.jetzt ?? Date.now;
  const log = opt.log ?? (() => {});
  const rechnername = opt.hostname ?? os.hostname;
  const erwartet = erwartetesZiel(opt, env);

  const sshOrdner = path.join(home, ".ssh");
  const schluesselDatei = path.join(sshOrdner, SCHLUESSEL_NAME);
  const pubDatei = `${schluesselDatei}.pub`;
  const knownHostsDatei = path.join(sshOrdner, KNOWN_HOSTS_NAME);
  const datenOrdner = path.join(localAppData, "WAMO");
  const geraetDatei = path.join(datenOrdner, "geraet.json");
  const tunnelStatusDatei = path.join(datenOrdner, "tunnel.status");
  const skript = opt.repo ? path.join(opt.repo, "scripts", SKRIPT_NAME) : null;
  const system32 = path.join(systemRoot, "System32");

  let sid = opt.sid ?? null;
  let besitzer = null; // user_id, für die zuletzt signiert wurde
  let datenordnerGeschuetzt = false;
  let tunnelFuer = null; // Inhalt von geraet.json, für den das Einrichtungsskript in diesem Lauf erfolgreich lief
  let skriptLaeuft = false;
  let letzterAnstoss = null;
  let zeitgeber = null;
  let schlange = Promise.resolve();

  /** Dateisystem-Arbeit nacheinander, damit zwei gleichzeitige Aufrufe sich nicht ins Gehege kommen. */
  function nacheinander(arbeit) {
    const lauf = schlange.then(arbeit);
    schlange = lauf.catch(() => undefined);
    return lauf;
  }

  async function benutzerSid() {
    if (sid) return sid;
    try {
      const { stdout } = await ausfuehren(path.join(system32, "whoami.exe"), ["/user", "/fo", "csv", "/nh"]);
      const treffer = /"(S-1-\d+(?:-\d+){1,15})"/.exec(String(stdout));
      if (treffer) {
        sid = treffer[1];
        return sid;
      }
    } catch {
      // unten gemeldet
    }
    throw new GeraetFehler("Das Windows-Konto dieses Laptops ließ sich nicht ermitteln.");
  }

  /** Zugriff nur für den angemeldeten Windows-Benutzer (Vererbung aus, ein einziger Eintrag). */
  async function nurBenutzer(pfad, ordner) {
    const kennung = await benutzerSid();
    const recht = ordner ? `*${kennung}:(OI)(CI)F` : `*${kennung}:F`;
    try {
      await ausfuehren(path.join(system32, "icacls.exe"), [pfad, "/inheritance:r", "/grant:r", recht]);
    } catch {
      throw new GeraetFehler("Die Zugriffsrechte für die Gerätedaten ließen sich nicht setzen.");
    }
  }

  function leseSchluessel() {
    let text;
    try {
      text = dsys.readFileSync(schluesselDatei, "utf8");
    } catch {
      throw new GeraetFehler("Der Geräteschlüssel dieses Laptops fehlt.");
    }
    try {
      return leseOpensshPrivat(text);
    } catch {
      // Die vorhandene Datei bleibt unangetastet.
      throw new GeraetFehler("Der vorhandene Geräteschlüssel ist nicht lesbar. Bitte den IT-Support rufen.");
    }
  }

  function vorhanden(datei) {
    try {
      dsys.accessSync(datei);
      return true;
    } catch {
      return false;
    }
  }

  async function erzeugeSchluessel(host) {
    const paar = crypto.generateKeyPairSync("ed25519");
    const seed = Buffer.from(paar.privateKey.export({ format: "jwk" }).d, "base64url");
    const pub = Buffer.from(paar.publicKey.export({ format: "jwk" }).x, "base64url");
    const text = opensshPrivat(seed, pub, `wamo-geraet@${host}`);
    seed.fill(0);
    try {
      dsys.writeFileSync(schluesselDatei, "", { flag: "wx", mode: 0o600 }); // nie überschreiben; zuerst leer anlegen
    } catch (err) {
      if (err?.code === "EEXIST") return false; // inzwischen von anderer Stelle angelegt: die vorhandene gilt
      throw new GeraetFehler("Der Geräteschlüssel konnte nicht gespeichert werden.");
    }
    try {
      await nurBenutzer(schluesselDatei, false); // erst die Rechte, dann der Inhalt: nie ein lesbarer Schlüssel
      dsys.writeFileSync(schluesselDatei, text, { flag: "w", mode: 0o600 });
    } catch (err) {
      dsys.rmSync(schluesselDatei, { force: true }); // ohne geschützte Rechte darf kein Schlüssel bleiben
      if (err instanceof GeraetFehler) throw err;
      throw new GeraetFehler("Der Geräteschlüssel konnte nicht gespeichert werden.");
    }
    return true;
  }

  /** Schlüssel anlegen (wenn er fehlt) und den öffentlichen Teil liefern. */
  function vorbereiten() {
    return nacheinander(async () => {
      const host = pruefeHostname(rechnername());
      dsys.mkdirSync(sshOrdner, { recursive: true });
      const neu = vorhanden(schluesselDatei) ? false : await erzeugeSchluessel(host);
      const schluessel = leseSchluessel();
      const blob = publikBlob(schluessel.pub);
      if (!vorhanden(pubDatei)) {
        try {
          dsys.writeFileSync(pubDatei, `ssh-ed25519 ${blob.toString("base64")} wamo-geraet@${host}\n`, { flag: "wx" });
        } catch {
          // Die .pub-Datei ist nur eine Hilfe, der Schlüssel selbst ist maßgeblich.
        }
      }
      return {
        pubkey: `ssh-ed25519 ${blob.toString("base64")}`,
        hostname: host,
        fingerprint: fingerabdruck(blob),
        neu,
      };
    });
  }

  /** Signatur (base64url) über die feste Bindungsnachricht. Signiert nie etwas anderes. */
  async function signiere({ nonce, userId } = {}) {
    const host = pruefeHostname(rechnername());
    if (typeof nonce !== "string" || !RE_NONCE.test(nonce)) {
      throw new GeraetFehler("Die Herausforderung des Kerns ist ungültig.");
    }
    if (typeof userId !== "string" || !RE_UUID.test(userId)) {
      throw new GeraetFehler("Die Benutzerkennung der Anmeldung ist ungültig.");
    }
    const schluessel = leseSchluessel();
    const signatur = crypto.sign(null, Buffer.from(nachrichtBinden({ nonce, userId, hostname: host }), "utf8"), schluessel.privKey);
    besitzer = userId;
    return signatur.toString("base64url");
  }

  function liesJson(datei) {
    try {
      return JSON.parse(dsys.readFileSync(datei, "utf8"));
    } catch {
      return null;
    }
  }

  function liesGeraet() {
    const g = liesJson(geraetDatei);
    return istObjekt(g) && g.v === 1 && typeof g.kennung === "string" && RE_KENNUNG.test(g.kennung) && g.kennung.length <= MAX_KENNUNG
      ? g
      : null;
  }

  /** Das Einrichtungsskript für den Tunnel (härtet scripts und host, legt die Aufgabe an und startet sie). */
  async function starteTunnel() {
    if (!skript || !vorhanden(skript)) throw new GeraetFehler("Das Einrichtungsskript für den Tunnel fehlt.");
    if (opt.vorTunnel) {
      // Installierte App: erst muss die Python-Umgebung des Hosts stehen (host-umgebung.js), sonst startet der Überwacher nicht
      try {
        await opt.vorTunnel();
      } catch (err) {
        throw new GeraetFehler(typeof err?.text === "string" ? err.text : "Die Python-Umgebung des Hosts ist nicht bereit.");
      }
    }
    const powershell = path.join(system32, "WindowsPowerShell", "v1.0", "powershell.exe");
    skriptLaeuft = true;
    try {
      await ausfuehren(
        powershell,
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", skript, "-Geraet", "-Starten"],
        { frist: SKRIPT_FRIST_MS },
      );
    } catch (err) {
      log(`Einrichtungsskript fehlgeschlagen (${err?.killed ? "Zeitüberschreitung" : `Code ${err?.code ?? "?"}`})`);
      throw new GeraetFehler(
        err?.killed ? "Die Einrichtung des Tunnels dauert zu lange." : "Der Tunnel zu WAMO konnte nicht eingerichtet werden.",
      );
    } finally {
      skriptLaeuft = false;
      letzterAnstoss = jetzt(); // der Wächter stößt die Aufgabe nicht gleich noch einmal an
    }
  }

  /** Antwort des Kerns prüfen, Dateien schreiben, Tunnel starten. Wirft GeraetFehler. */
  function aktivieren(antwort) {
    return nacheinander(async () => {
      const host = pruefeHostname(rechnername());
      const k = pruefeKernAntwort(antwort, besitzer, erwartet);
      const vorhandenes = liesGeraet();
      if (vorhandenes && vorhandenes.besitzer !== k.besitzer) {
        // Ein Laptop gehört genau einem Profil; ein Wechsel ist Sache eines Admins (Gerät widerrufen, Datei entfernen)
        throw new GeraetFehler("Dieser Laptop ist schon einem anderen Profil zugeordnet. Bitte einen Admin fragen.");
      }
      const schluessel = leseSchluessel();
      const blob = publikBlob(schluessel.pub);

      dsys.mkdirSync(datenOrdner, { recursive: true });
      if (!datenordnerGeschuetzt) {
        await nurBenutzer(datenOrdner, true);
        datenordnerGeschuetzt = true;
      }
      dsys.mkdirSync(sshOrdner, { recursive: true });
      try {
        schreibeAtomar(knownHostsDatei, `${k.host} ${k.hostschluessel}\n`, dsys);
      } catch {
        throw new GeraetFehler("Die Datei mit dem Hostschlüssel konnte nicht gespeichert werden.");
      }
      const text = `${JSON.stringify(
        {
          v: 1,
          kennung: k.kennung,
          slot: k.slot,
          besitzer: k.besitzer,
          hostname: host,
          name: k.name,
          stufe: k.stufe,
          ziel: `${k.benutzer}@${k.host}`,
          socket: k.socket,
          fingerabdruck: fingerabdruck(blob),
          schluessel: schluesselDatei,
          known_hosts: knownHostsDatei,
        },
        null,
        2,
      )}\n`;
      let alt = null;
      try {
        alt = dsys.readFileSync(geraetDatei, "utf8");
      } catch {
        // noch keine Datei
      }
      if (alt !== text) {
        try {
          schreibeAtomar(geraetDatei, text, dsys);
        } catch {
          throw new GeraetFehler("Die Gerätedatei konnte nicht gespeichert werden.");
        }
      }
      if (tunnelFuer !== text) {
        await starteTunnel();
        tunnelFuer = text;
      }
      return { ok: true, status: status() };
    });
  }

  /** { eingerichtet, kennung, stufe, tunnel }: tunnel nach tunnel.status des Überwachers (siehe tunnelZustand). */
  function status() {
    const g = liesGeraet();
    if (!g) return { eingerichtet: false, kennung: null, stufe: null, tunnel: "unbekannt" };
    return {
      eingerichtet: true,
      kennung: g.kennung,
      stufe: STUFEN.includes(g.stufe) ? g.stufe : null,
      tunnel: tunnelZustand(liesJson(tunnelStatusDatei), jetzt()),
    };
  }

  /** Ein Durchlauf des Wächters: die geplante Aufgabe anstoßen, wenn der Überwacher nicht mehr schreibt. */
  async function waechterTick() {
    const jetztMs = jetzt();
    const e = waechterEntscheidung({
      eingerichtet: liesGeraet() !== null,
      tunnelStatus: liesJson(tunnelStatusDatei),
      jetzt: jetztMs,
      letzterStart: letzterAnstoss,
      skriptLaeuft,
    });
    if (!e.starten) return e;
    letzterAnstoss = jetztMs; // auch bei Fehlschlag frühestens nach 2 Minuten erneut
    try {
      await ausfuehren(path.join(system32, "schtasks.exe"), ["/Run", "/TN", TUNNEL_AUFGABE]);
      log(`Aufgabe "${TUNNEL_AUFGABE}" angestoßen (${e.grund})`);
    } catch {
      log(`Aufgabe "${TUNNEL_AUFGABE}" ließ sich nicht anstoßen`);
    }
    return e;
  }

  /** Wächter alle 30 s. Er beendet nie Prozesse. */
  function starteWaechter() {
    if (zeitgeber) return;
    zeitgeber = setInterval(() => void waechterTick().catch(() => undefined), WAECHTER_ABSTAND_MS);
    zeitgeber.unref?.();
  }

  function stoppe() {
    if (zeitgeber) clearInterval(zeitgeber);
    zeitgeber = null;
  }

  return {
    vorbereiten,
    signiere,
    aktivieren,
    status,
    waechterTick,
    starteWaechter,
    stoppe,
    pfade: { schluesselDatei, pubDatei, knownHostsDatei, datenOrdner, geraetDatei, tunnelStatusDatei, skript },
  };
}

module.exports = {
  GeraetFehler,
  HOSTSCHLUESSEL_FP_STANDARD,
  SSH_HOST_STANDARD,
  KURZ_FRIST_MS,
  NEUSTART_ABSTAND_MS,
  SKRIPT_FRIST_MS,
  STATUS_FRISCH_MS,
  WAECHTER_ABSTAND_MS,
  erzeugeGeraet,
  fingerabdruck,
  leseOpensshPrivat,
  nachrichtBinden,
  opensshPrivat,
  pruefeHostname,
  pruefeKernAntwort,
  publikBlob,
  erwartetesZiel,
  schreibeAtomar,
  tunnelZustand,
  waechterEntscheidung,
};
