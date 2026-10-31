// WAMO-Desktop: Vertrauen in die WAMO-CA nur innerhalb der App (reines Node-Modul, ohne electron-Import, testbar).
//
// Warum nicht der Windows-Speicher: "certutil -user -addstore Root" öffnet eine Sicherheitsabfrage von Windows, ändert das
// Vertrauen für ALLE Programme des Benutzers und bleibt nach dem Deinstallieren bestehen. Stattdessen prüft die App die
// Kette selbst (Electron: session.setCertificateVerifyProc) und nur unter diesen Bedingungen:
//   * Der Servername steht in der festen Liste (die Adresse der Büro-Oberfläche und des Kerns), sonst gilt die normale
//     Prüfung von Chromium.
//   * Chromium meldet genau "Aussteller unbekannt" (net::ERR_CERT_AUTHORITY_INVALID). Jeder andere Fehler bleibt ein Fehler.
//   * Die Kette führt vom Serverzertifikat (und evtl. Zwischenzertifikaten, alle als CA gekennzeichnet) per Signatur
//     bis zu genau der mitgelieferten WAMO-CA, deren SHA-256-Fingerabdruck hier fest eingetragen ist.
//   * Das Serverzertifikat ist zeitlich gültig, kein CA-Zertifikat, für Server erlaubt und nennt den Servernamen
//     (Subject Alternative Name).
// Die Namensbeschränkung der WAMO-CA (nur Sokrates-2, localhost u. a.) setzt die feste Liste der Servernamen um. Wer die
// WAMO-CA zusätzlich in den Windows-Speicher importiert hat, ändert daran nichts: Dann meldet Chromium schon "OK".
"use strict";

const { X509Certificate } = require("node:crypto");
const { isIP } = require("node:net");

// SHA-256-Fingerabdruck von infra/sokrates2/wamo-ca.crt (CN=WAMO interne CA, gültig bis 16.10.2036). Wechselt die CA,
// kommt der neue Wert mit einer neuen App-Version; der Test zertifikat.test.js vergleicht ihn mit der Datei im Repository.
const WAMO_CA_SHA256 = "A9:7E:9E:DA:3D:9E:C6:74:32:B0:59:7E:2F:16:AC:FB:45:5F:80:7F:3F:C5:88:21:55:6F:E3:5E:E6:9C:E0:23";
const EKU_SERVER = "1.3.6.1.5.5.7.3.1";
const ERSETZBARER_FEHLER = "net::ERR_CERT_AUTHORITY_INVALID";
const MAX_KETTE = 5; // Obergrenze beim Einlesen der Kette
const MAX_ZWISCHEN = 1; // die WAMO-CA hat pathlen 1: höchstens ein Zwischenzertifikat (das von Caddy)
// Die Namensbeschränkung der WAMO-CA (permittedSubtrees): nur diese Namen werden je geprüft, egal was konfiguriert ist
const WAMO_NAMEN = ["192.168.178.136", "127.0.0.1", "localhost", "sokrates2", "spark-651f.local", "supavisor", "wamo-supabase-pooler"];

/** Hostname ohne eckige Klammern (URL.hostname liefert IPv6 mit Klammern), kleingeschrieben. */
const normalisiere = (host) => String(host).replace(/^\[|\]$/g, "").toLowerCase();

/** Liest die CA aus einer PEM-Datei und prüft Fingerabdruck und CA-Kennzeichnung. Wirft bei Abweichung. */
function ladeCa(pem, erwartet = WAMO_CA_SHA256) {
  const ca = new X509Certificate(pem);
  if (ca.fingerprint256 !== erwartet) throw new Error("Die WAMO-CA stimmt nicht mit dem fest eingetragenen Fingerabdruck überein.");
  if (!ca.ca) throw new Error("Das Zertifikat ist keine CA.");
  return ca;
}

const zeitlichGueltig = (z, jetzt) => Date.parse(z.validFrom) <= jetzt && jetzt <= Date.parse(z.validTo);

function zuZertifikat(pem) {
  try {
    return new X509Certificate(pem);
  } catch {
    return null;
  }
}

/**
 * Prüft Serverzertifikat plus mitgelieferte Zwischenzertifikate (PEM) gegen die CA und den Servernamen.
 * Liefert { ok: true } oder { ok: false, grund }.
 */
function pruefeKette({ blattPem, zwischenPem = [], ca, host: roherHost, jetzt = Date.now() }) {
  const host = normalisiere(roherHost);
  if (!WAMO_NAMEN.includes(host)) return { ok: false, grund: "Servername nicht in der Namensbeschränkung der WAMO-CA" };
  const blatt = zuZertifikat(blattPem);
  if (!blatt) return { ok: false, grund: "Serverzertifikat nicht lesbar" };
  if (blatt.ca) return { ok: false, grund: "Serverzertifikat ist ein CA-Zertifikat" };
  if (!zeitlichGueltig(blatt, jetzt)) return { ok: false, grund: "Serverzertifikat nicht gültig (Zeitraum)" };
  if (Array.isArray(blatt.keyUsage) && blatt.keyUsage.length > 0 && !blatt.keyUsage.includes(EKU_SERVER)) {
    return { ok: false, grund: "Serverzertifikat nicht für Server erlaubt" };
  }
  if (isIP(host) ? !blatt.checkIP(host) : !blatt.checkHost(host)) return { ok: false, grund: "Servername nicht im Zertifikat" };
  if (!zeitlichGueltig(ca, jetzt)) return { ok: false, grund: "WAMO-CA nicht gültig (Zeitraum)" };

  const zwischen = zwischenPem.map(zuZertifikat).filter(Boolean);
  let aktuell = blatt;
  for (let schritt = 0; schritt <= MAX_ZWISCHEN; schritt++) {
    if (aktuell.checkIssued(ca) && aktuell.verify(ca.publicKey)) return { ok: true };
    const naechstes = zwischen.find(
      (z) => z.ca && z !== aktuell && zeitlichGueltig(z, jetzt) && aktuell.checkIssued(z) && aktuell.verify(z.publicKey),
    );
    if (!naechstes) return { ok: false, grund: "Kette führt nicht zur WAMO-CA" };
    aktuell = naechstes;
  }
  return { ok: false, grund: "Kette zu lang" };
}

/**
 * Kette einer fertig ausgehandelten TLS-Verbindung (Node) als PEM-Liste, Blatt zuerst. Wird für Anfragen aus dem
 * Hauptprozess gebraucht: Das Node in Electron (BoringSSL) kann die IP-Namensbeschränkung der WAMO-CA nicht lesen und
 * bricht mit "unsupported name constraint type" ab, deshalb prüft pruefeSocket die Kette hier selbst.
 */
function pemKetteVonSocket(socket) {
  const liste = [];
  const gesehen = new Set();
  let z = socket?.getPeerCertificate?.(true);
  while (z && z.raw && !gesehen.has(z.fingerprint256) && liste.length < MAX_KETTE) {
    gesehen.add(z.fingerprint256);
    liste.push(new X509Certificate(z.raw).toString());
    z = z.issuerCertificate;
  }
  return liste;
}

/** Wie pruefeKette, aber für den Peer einer Node-TLS-Verbindung. */
function pruefeSocket(socket, host, ca, jetzt = Date.now()) {
  let kette;
  try {
    kette = pemKetteVonSocket(socket);
  } catch {
    return { ok: false, grund: "Kette nicht lesbar" };
  }
  if (kette.length === 0) return { ok: false, grund: "Kein Zertifikat vom Server" };
  return pruefeKette({ blattPem: kette[0], zwischenPem: kette.slice(1), ca, host, jetzt });
}

/** PEM-Texte der Zwischenzertifikate aus dem Electron-Zertifikat (issuerCert-Kette, ohne das Blatt selbst). */
function zwischenZertifikate(zertifikat) {
  const liste = [];
  let z = zertifikat?.issuerCert;
  while (z && typeof z.data === "string" && liste.length < MAX_KETTE) {
    liste.push(z.data);
    z = z.issuerCert === z ? null : z.issuerCert;
  }
  return liste;
}

/**
 * Der Rückruf für session.setCertificateVerifyProc. Rückgabe 0 = vertraut, -3 = Ergebnis von Chromium gilt.
 * Es wird nie abgelehnt oder gelockert, außer die Kette führt sicher zur WAMO-CA.
 */
function erzeugePruefer({ ca, erlaubteHosts, jetzt = Date.now, log = () => {} }) {
  const hosts = new Set(erlaubteHosts.map(normalisiere));
  return (anfrage, rueckruf) => {
    try {
      if (!hosts.has(normalisiere(anfrage.hostname)) || anfrage.verificationResult !== ERSETZBARER_FEHLER) {
        rueckruf(-3);
        return;
      }
      const ergebnis = pruefeKette({
        blattPem: anfrage.certificate?.data,
        zwischenPem: zwischenZertifikate(anfrage.certificate),
        ca,
        host: anfrage.hostname,
        jetzt: jetzt(),
      });
      if (!ergebnis.ok) log(`Zertifikat von ${anfrage.hostname} abgelehnt: ${ergebnis.grund}`);
      rueckruf(ergebnis.ok ? 0 : -3);
    } catch (err) {
      log(`Zertifikatsprüfung fehlgeschlagen: ${err.message}`);
      rueckruf(-3);
    }
  };
}

module.exports = { WAMO_CA_SHA256, erzeugePruefer, ladeCa, pemKetteVonSocket, pruefeKette, pruefeSocket, zwischenZertifikate };
