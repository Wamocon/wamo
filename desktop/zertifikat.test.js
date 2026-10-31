// Tests für das Vertrauen in die WAMO-CA innerhalb der App (zertifikat.js). Ausführen im Ordner desktop/:  npm test
// Die Zertifikate in testdaten/ sind Testzertifikate (nur öffentliche Teile, 100 Jahre gültig): ca.crt -> zw.crt -> blatt.crt;
// fremd.crt ist eine andere CA mit gleichem Namen, fremdblatt.crt von ihr ausgestellt, client.crt nur für Clients.
"use strict";

const assert = require("node:assert/strict");
const { X509Certificate } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { WAMO_CA_SHA256, erzeugePruefer, ladeCa, pruefeKette, pruefeSocket, zwischenZertifikate } = require("./zertifikat");

const lies = (name) => fs.readFileSync(path.join(__dirname, "testdaten", name), "utf8");
const CA = lies("ca.crt");
const ZW = lies("zw.crt");
const BLATT = lies("blatt.crt");
const ca = new X509Certificate(CA);
const HOST = "192.168.178.136";
const JETZT = Date.now();

test("Die feste CA-Prüfsumme passt zu infra/sokrates2/wamo-ca.crt", () => {
  const echt = fs.readFileSync(path.join(__dirname, "..", "infra", "sokrates2", "wamo-ca.crt"), "utf8");
  const z = ladeCa(echt);
  assert.equal(z.fingerprint256, WAMO_CA_SHA256);
  assert.match(z.subject, /WAMO interne CA/);
});

test("ladeCa: falscher Fingerabdruck oder kein CA-Zertifikat wird abgelehnt", () => {
  assert.throws(() => ladeCa(CA), /Fingerabdruck/); // Testzertifikat gegen die feste Prüfsumme
  assert.equal(ladeCa(CA, ca.fingerprint256).fingerprint256, ca.fingerprint256);
  assert.throws(() => ladeCa(BLATT, new X509Certificate(BLATT).fingerprint256), /keine CA/);
});

test("Kette Blatt -> Zwischenzertifikat -> CA wird für die Adresse vertraut", () => {
  assert.deepEqual(pruefeKette({ blattPem: BLATT, zwischenPem: [ZW], ca, host: HOST, jetzt: JETZT }), { ok: true });
  assert.equal(pruefeKette({ blattPem: BLATT, zwischenPem: [ZW], ca, host: "localhost", jetzt: JETZT }).ok, true);
});

test("Ohne das Zwischenzertifikat führt die Kette nicht zur CA", () => {
  const r = pruefeKette({ blattPem: BLATT, zwischenPem: [], ca, host: HOST, jetzt: JETZT });
  assert.equal(r.ok, false);
  assert.match(r.grund, /Kette/);
});

test("Andere CA mit gleichem Namen wird nicht vertraut", () => {
  const r = pruefeKette({ blattPem: lies("fremdblatt.crt"), zwischenPem: [lies("fremd.crt")], ca, host: HOST, jetzt: JETZT });
  assert.equal(r.ok, false);
  const r2 = pruefeKette({ blattPem: lies("fremdblatt.crt"), zwischenPem: [], ca, host: HOST, jetzt: JETZT });
  assert.equal(r2.ok, false);
});

test("Falscher Servername, CA-Zertifikat als Blatt, nur Client-Zertifikat, Müll", () => {
  assert.match(pruefeKette({ blattPem: BLATT, zwischenPem: [ZW], ca, host: "192.168.178.137", jetzt: JETZT }).grund, /Servername/);
  assert.match(pruefeKette({ blattPem: BLATT, zwischenPem: [ZW], ca, host: "beispiel.de", jetzt: JETZT }).grund, /Servername/);
  assert.match(pruefeKette({ blattPem: ZW, zwischenPem: [], ca, host: HOST, jetzt: JETZT }).grund, /CA-Zertifikat/);
  assert.match(pruefeKette({ blattPem: lies("client.crt"), zwischenPem: [ZW], ca, host: HOST, jetzt: JETZT }).grund, /für Server/);
  assert.match(pruefeKette({ blattPem: "kein Zertifikat", zwischenPem: [], ca, host: HOST, jetzt: JETZT }).grund, /lesbar/);
});

test("Abgelaufen oder noch nicht gültig", () => {
  const spaet = Date.parse("2300-01-01");
  const frueh = Date.parse("2000-01-01");
  assert.match(pruefeKette({ blattPem: BLATT, zwischenPem: [ZW], ca, host: HOST, jetzt: spaet }).grund, /Zeitraum/);
  assert.match(pruefeKette({ blattPem: BLATT, zwischenPem: [ZW], ca, host: HOST, jetzt: frueh }).grund, /Zeitraum/);
});

test("Ein Zwischenzertifikat, das keine CA ist, wird nicht als Aussteller anerkannt", () => {
  // das Blatt als "Zwischenzertifikat" für das Client-Zertifikat: gleiche Schlüssel, aber CA:FALSE
  const r = pruefeKette({ blattPem: lies("client.crt"), zwischenPem: [BLATT], ca, host: HOST, jetzt: JETZT });
  assert.equal(r.ok, false);
});

test("zwischenZertifikate liest die issuerCert-Kette und bricht bei Schleifen ab", () => {
  const wurzel = { data: CA };
  const zw = { data: ZW, issuerCert: wurzel };
  assert.deepEqual(zwischenZertifikate({ data: BLATT, issuerCert: zw }), [ZW, CA]);
  const schleife = { data: ZW };
  schleife.issuerCert = schleife;
  assert.deepEqual(zwischenZertifikate({ data: BLATT, issuerCert: schleife }), [ZW]);
  assert.deepEqual(zwischenZertifikate({ data: BLATT }), []);
  assert.deepEqual(zwischenZertifikate(undefined), []);
});

test("pruefeSocket: Kette einer Node-TLS-Verbindung (Blatt, Zwischenzertifikat) wird gegen die CA geprüft", () => {
  const eintrag = (pem, aussteller) => {
    const z = new X509Certificate(pem);
    return { raw: z.raw, fingerprint256: z.fingerprint256, issuerCertificate: aussteller };
  };
  const zwEintrag = eintrag(ZW, undefined);
  zwEintrag.issuerCertificate = zwEintrag; // Node verweist bei der Wurzel auf sich selbst: darf nicht endlos laufen
  const socket = (blatt) => ({ getPeerCertificate: (voll) => (voll ? blatt : {}) });
  const gut = socket(eintrag(BLATT, zwEintrag));
  assert.deepEqual(pruefeSocket(gut, HOST, ca, JETZT), { ok: true });
  assert.equal(pruefeSocket(gut, "192.168.178.1", ca, JETZT).ok, false);
  assert.equal(pruefeSocket(socket(eintrag(BLATT, undefined)), HOST, ca, JETZT).ok, false, "ohne Zwischenzertifikat");
  const fremd = eintrag(lies("fremdblatt.crt"), eintrag(lies("fremd.crt"), undefined));
  assert.equal(pruefeSocket(socket(fremd), HOST, ca, JETZT).ok, false);
  assert.equal(pruefeSocket({ getPeerCertificate: () => ({}) }, HOST, ca, JETZT).ok, false);
  assert.equal(pruefeSocket({}, HOST, ca, JETZT).ok, false);
});

function frage(pruefer, anfrage) {
  let antwort;
  pruefer(anfrage, (wert) => {
    antwort = wert;
  });
  return antwort;
}

const kette = () => ({ data: BLATT, issuerCert: { data: ZW } });
const aufrufe = [];
const pruefer = erzeugePruefer({ ca, erlaubteHosts: [HOST], jetzt: () => JETZT, log: (t) => aufrufe.push(t) });

test("Prüfer: unbekannter Aussteller bei erlaubtem Host mit gültiger Kette wird vertraut (0)", () => {
  assert.equal(frage(pruefer, { hostname: HOST, verificationResult: "net::ERR_CERT_AUTHORITY_INVALID", certificate: kette() }), 0);
});

test("Prüfer: alles andere bleibt beim Ergebnis von Chromium (-3)", () => {
  const f = "net::ERR_CERT_AUTHORITY_INVALID";
  // anderer Host
  assert.equal(frage(pruefer, { hostname: "beispiel.de", verificationResult: f, certificate: kette() }), -3);
  // schon in Ordnung oder ein anderer Fehler: nichts wird gelockert
  assert.equal(frage(pruefer, { hostname: HOST, verificationResult: "net::OK", certificate: kette() }), -3);
  assert.equal(frage(pruefer, { hostname: HOST, verificationResult: "net::ERR_CERT_DATE_INVALID", certificate: kette() }), -3);
  assert.equal(frage(pruefer, { hostname: HOST, verificationResult: "net::ERR_CERT_REVOKED", certificate: kette() }), -3);
  // fremde Kette, fehlendes Zertifikat
  const fremd = { data: lies("fremdblatt.crt"), issuerCert: { data: lies("fremd.crt") } };
  assert.equal(frage(pruefer, { hostname: HOST, verificationResult: f, certificate: fremd }), -3);
  assert.equal(frage(pruefer, { hostname: HOST, verificationResult: f }), -3);
  assert.equal(frage(pruefer, { hostname: HOST, verificationResult: f, certificate: { data: "müll" } }), -3);
  assert.ok(aufrufe.length >= 2, "Ablehnungen werden mitgeschrieben");
});
