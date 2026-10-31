// Prüft den Inhalt des gebauten Installer-Pakets (entpackter Ordner "win-unpacked" von electron-builder).
// Es wird nichts installiert und nichts gestartet. Prüfungen:
//   * Dateiliste: keine Tests, Testdaten, .env, Schlüssel, virtuelle Umgebungen, Protokolle, Quellen der Oberfläche
//   * Inhalt aller Textdateien: keine Geheimnis-Muster (Schlüssel, Token, Passwörter)
//   * optional --verboten=wort1,wort2: diese Wörter dürfen in keiner Textdatei und keinem Dateinamen stehen
// Aufruf:  node scripts/wamo-dist-pruefen.cjs <Ordner win-unpacked> [--verboten=a,b] [--liste]
// Rückgabewert 0 = sauber, 1 = Funde.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

// Pfade (mit / geschrieben, relativ zum Paket), die nie im Paket stehen dürfen
const VERBOTENE_PFADE = [
  { re: /(^|\/)\.env(\.|$)/i, grund: ".env-Datei" },
  { re: /(^|\/)\.venv(\/|$)/i, grund: "virtuelle Python-Umgebung" },
  { re: /(^|\/)__pycache__(\/|$)/i, grund: "Python-Zwischenspeicher" },
  { re: /(^|\/)(test_[^/]*\.py|conftest\.py|[^/]*\.test\.[cm]?js|[^/]*\.probe\.js|testdaten)(\/|$)/i, grund: "Test" },
  { re: /\.(pem|key|pfx|p12|jsonl|log|stackdump)$/i, grund: "Schlüssel, Protokoll oder Audit-Datei" },
  { re: /(^|\/)(voice-core|ui|supabase|infra|node_modules|\.git|\.claude|logs|docs)(\/|$)/i, grund: "Quellordner, der nicht ins Paket gehört" },
  { re: /(^|\/)wamo_audit\.jsonl$/i, grund: "Audit-Protokoll" },
];

// Muster, die auf Geheimnisse hindeuten (Textdateien)
const GEHEIMNIS_MUSTER = [
  // Kopfzeile plus Base64-Inhalt (nur die Kopfzeile als Text, wie in geraet.js beim Schreiben des Formats, ist kein Fund)
  { re: /-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{40,}/, grund: "privater Schlüssel" },
  { re: /\bsk-ant-[A-Za-z0-9_-]{20,}/, grund: "Anthropic-Schlüssel" },
  { re: /\bsbp_[0-9a-f]{30,}/, grund: "Supabase-Token" },
  { re: /\bgh[pousr]_[A-Za-z0-9]{30,}/, grund: "GitHub-Token" },
  { re: /\bAKIA[0-9A-Z]{16}\b/, grund: "AWS-Schlüssel" },
  { re: /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/, grund: "JWT" },
  { re: /\bservice_role\b\s*[:=]\s*["'][^"']{20,}/, grund: "service_role-Schlüssel" },
  { re: /\b(?:SONIOX_API_KEY|ANTHROPIC_API_KEY|WAMO_DATABASE_URL|WAMO_ADMIN_DATABASE_URL|LITELLM_API_KEY)\s*=\s*\S{8,}/, grund: "Zuweisung eines Zugangswerts" },
  { re: /postgres(?:ql)?:\/\/[^:\s/]+:[^@\s]{4,}@/, grund: "Datenbankadresse mit Passwort" },
];

const TEXT_ENDUNGEN = new Set([".js", ".cjs", ".mjs", ".json", ".html", ".css", ".ps1", ".py", ".toml", ".lock", ".txt", ".md", ".crt", ".yml", ".yaml", ".nsh", ".cmd"]);
const MAX_TEXT_BYTES = 8 * 1024 * 1024;

/** Verstöße gegen die Pfadregeln für eine Liste relativer Pfade. */
function pruefePfade(pfade) {
  const funde = [];
  for (const p of pfade) {
    const norm = p.replace(/\\/g, "/").replace(/^\//, "");
    for (const regel of VERBOTENE_PFADE) if (regel.re.test(norm)) funde.push({ pfad: norm, grund: regel.grund });
  }
  return funde;
}

/** Verstöße in einem Text (Geheimnis-Muster und verbotene Wörter, Groß- und Kleinschreibung egal). */
function pruefeText(text, verboten = []) {
  const funde = [];
  for (const m of GEHEIMNIS_MUSTER) if (m.re.test(text)) funde.push({ grund: m.grund });
  const klein = text.toLowerCase();
  for (const w of verboten) if (w && klein.includes(w.toLowerCase())) funde.push({ grund: "verbotenes Wort" });
  return funde;
}

function alleDateien(wurzel, relativ = "") {
  const aus = [];
  for (const e of fs.readdirSync(path.join(wurzel, relativ), { withFileTypes: true })) {
    const rel = relativ ? `${relativ}/${e.name}` : e.name;
    if (e.isDirectory()) aus.push(...alleDateien(wurzel, rel));
    else aus.push(rel);
  }
  return aus;
}

function asarPaket(ressourcen) {
  const asar = require(require.resolve("@electron/asar", { paths: [path.join(__dirname, "..", "desktop")] }));
  return { asar, datei: path.join(ressourcen, "app.asar") };
}

/** Prüft ein entpacktes Paket. Liefert { funde, dateien, groesse }. */
function pruefePaket(ordner, { verboten = [] } = {}) {
  const ressourcen = path.join(ordner, "resources");
  const funde = [];
  const eigene = alleDateien(ressourcen).filter((p) => p !== "app.asar" && !p.startsWith("app.asar.unpacked/"));
  for (const f of pruefePfade(eigene)) funde.push({ ...f, wo: "resources" });
  const { asar, datei } = asarPaket(ressourcen);
  const imAsar = asar.listPackage(datei, { isPack: false }).map((p) => p.replace(/\\/g, "/").replace(/^\//, ""));
  for (const f of pruefePfade(imAsar)) funde.push({ ...f, wo: "app.asar" });
  const text = (rel, inhalt, wo) => {
    for (const f of pruefeText(inhalt, verboten)) funde.push({ pfad: rel, wo, grund: f.grund });
  };
  for (const rel of eigene) {
    if (!TEXT_ENDUNGEN.has(path.extname(rel).toLowerCase())) continue;
    const voll = path.join(ressourcen, rel);
    if (fs.statSync(voll).size <= MAX_TEXT_BYTES) text(rel, fs.readFileSync(voll, "utf8"), "resources");
  }
  for (const rel of imAsar) {
    if (!TEXT_ENDUNGEN.has(path.extname(rel).toLowerCase())) continue;
    let inhalt;
    try {
      inhalt = asar.extractFile(datei, rel).toString("utf8");
    } catch {
      continue; // Ordner
    }
    text(rel, inhalt, "app.asar");
  }
  for (const w of verboten) {
    const k = w.toLowerCase();
    for (const p of [...eigene, ...imAsar]) if (k && p.toLowerCase().includes(k)) funde.push({ pfad: p, grund: "verbotenes Wort im Dateinamen" });
  }
  const groesse = alleDateien(ordner).reduce((s, rel) => s + fs.statSync(path.join(ordner, rel)).size, 0);
  return { funde, dateien: { resources: eigene, asar: imAsar }, groesse };
}

module.exports = { GEHEIMNIS_MUSTER, VERBOTENE_PFADE, pruefePaket, pruefePfade, pruefeText };

if (require.main === module) {
  const argumente = process.argv.slice(2);
  const ordner = argumente.find((a) => !a.startsWith("--"));
  const verboten = (argumente.find((a) => a.startsWith("--verboten="))?.slice("--verboten=".length) ?? "").split(",").filter(Boolean);
  if (!ordner || !fs.existsSync(path.join(ordner, "resources", "app.asar"))) {
    console.error("Aufruf: node scripts/wamo-dist-pruefen.cjs <Ordner win-unpacked> [--verboten=a,b] [--liste]");
    process.exit(2);
  }
  const erg = pruefePaket(ordner, { verboten });
  if (argumente.includes("--liste")) {
    console.log("== resources (ohne app.asar)");
    for (const p of erg.dateien.resources) console.log(`  ${p}`);
    console.log("== app.asar");
    for (const p of erg.dateien.asar.filter((x) => !x.endsWith("/") && path.extname(x))) console.log(`  ${p}`);
  }
  console.log(`Größe des Ordners: ${(erg.groesse / 1024 / 1024).toFixed(1)} MB, ${erg.dateien.resources.length} Dateien in resources, ${erg.dateien.asar.length} Einträge im asar`);
  if (erg.funde.length === 0) {
    console.log("Keine Funde.");
  } else {
    for (const f of erg.funde) console.log(`FUND [${f.wo ?? ""}] ${f.pfad ?? ""}: ${f.grund}`);
    process.exit(1);
  }
}
