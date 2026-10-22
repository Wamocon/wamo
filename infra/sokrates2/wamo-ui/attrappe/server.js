// Attrappe der WAMO-Oberfläche für den Rauchtest des Umschaltens (infra/sokrates2/wamo_ui_rauchtest.py).
// Sie ahmt nur nach, was das Umschalten braucht: GET /version.json im Format der echten Route, eine Seite mit
// Sicherheitsheadern (CSP mit Anmeldung und Kern), Chunks unter /_next/static/ (404 für Unbekanntes, wie Next.js) und
// auf Wunsch einen Ausfall nach einer Zeit. Kein Geheimnis, kein Netz nach außen.
"use strict";
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const env = process.env;
const STATIC_DIR = path.join(__dirname, "static");
const START = Date.now();
const MODUS = env.ATTRAPPE_MODUS || "";
const NACH = Number(env.ATTRAPPE_NACH_S || "0") * 1000;

const info = {
  name: "wamo-ui",
  version: require("./package.json").version || "unbekannt",
  commit: env.WAMO_COMMIT || "unbekannt",
  gebaut: env.WAMO_BAUZEIT || "unbekannt",
  sauber: env.WAMO_SAUBER === "ja",
};
// Modus "ohne-csp": der Build kennt Anmeldung und Kern nicht (falsche Adressen), die Prüfung vor dem Umschalten muss das erkennen
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  MODUS === "ohne-csp"
    ? "connect-src 'self'"
    : `connect-src 'self' ${env.NEXT_PUBLIC_SUPABASE_URL || ""} ${env.NEXT_PUBLIC_WAMO_KERN_BUERO || ""}`.trim(),
].join("; ");
const kopf = {
  "Content-Security-Policy": csp,
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

function ausgefallen() {
  return NACH > 0 && Date.now() - START > NACH;
}

http
  .createServer((req, res) => {
    if (ausgefallen() && MODUS === "absturz") process.exit(1);
    if (ausgefallen() && MODUS === "fehler") {
      res.writeHead(500, { "Content-Type": "text/plain" });
      res.end("Fehler");
      return;
    }
    let url;
    try {
      url = new URL(req.url, "http://x");
    } catch {
      res.writeHead(400);
      res.end("ungueltig");
      return;
    }
    // Modus "alt": die Fassung vor der Umstellung auf blau/grün kennt /version.json nicht
    if (url.pathname === "/version.json" && MODUS !== "alt") {
      res.writeHead(200, { ...kopf, "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(JSON.stringify(info));
      return;
    }
    if (url.pathname === "/") {
      const dateien = fs.existsSync(STATIC_DIR) ? fs.readdirSync(STATIC_DIR).sort() : [];
      const skripte = dateien.map((d) => `<script src="/_next/static/chunks/${d}"></script>`).join("");
      res.writeHead(200, { ...kopf, "Content-Type": "text/html; charset=utf-8" });
      res.end(`<!doctype html><title>WAMO ${info.version}</title>${skripte}`);
      return;
    }
    const m = /^\/_next\/static\/chunks\/([A-Za-z0-9._-]+)$/.exec(url.pathname);
    if (m && fs.existsSync(path.join(STATIC_DIR, m[1]))) {
      res.writeHead(200, { ...kopf, "Content-Type": "text/javascript", "Cache-Control": "public, max-age=31536000, immutable" });
      res.end(fs.readFileSync(path.join(STATIC_DIR, m[1])));
      return;
    }
    res.writeHead(404, { ...kopf, "Content-Type": "text/html; charset=utf-8" });
    res.end("<!doctype html><title>404</title>nicht gefunden");
  })
  .listen(Number(env.PORT || "3000"), env.HOSTNAME || "0.0.0.0");
