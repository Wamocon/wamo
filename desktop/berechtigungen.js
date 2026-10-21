// Berechtigungen der Desktop-App: Mikrofon (nur Audio, nie Kamera) und Zwischenablage (schreiben) nur für die eigene
// Oberfläche im Hauptfenster, sonst nichts. Dazu Protokollzeilen für die Diagnose (logs/berechtigungen.log) und das
// Weiterreichen der Mikrofon-Meldungen der Oberfläche (logs/oberflaeche.log), damit eine stille Spur im Alltag
// nachvollziehbar bleibt. main.js hängt das an die Session; Tests: berechtigungen.test.js.
"use strict";

/**
 * Anfrage (setPermissionRequestHandler), z. B. getUserMedia. Electron 44 liefert bei "media" details.mediaTypes,
 * etwa ["audio"]; fehlt die Liste oder ist Video dabei, wird abgelehnt.
 */
function anfrageErlaubt(permission, details, eigeneUi) {
  if (!eigeneUi) return false;
  if (permission === "media") {
    const typen = Array.isArray(details?.mediaTypes) ? details.mediaTypes : [];
    return typen.length > 0 && typen.every((t) => t === "audio");
  }
  return permission === "clipboard-sanitized-write";
}

/**
 * Prüfung (setPermissionCheckHandler), z. B. navigator.permissions.query und Gerätebezeichnungen in
 * enumerateDevices. Electron liefert bei "media" details.mediaType ("audio", "video" oder "unknown").
 */
function pruefungErlaubt(permission, details, eigeneUi) {
  if (!eigeneUi) return false;
  if (permission === "media") return details?.mediaType === "audio";
  return permission === "clipboard-sanitized-write";
}

/** Nur die Herkunft einer Adresse (kein Pfad, keine Abfrage), leer bei ungültiger Angabe. */
function herkunft(url) {
  try {
    return new URL(String(url ?? "")).origin;
  } catch {
    return "";
  }
}

/** Soll diese Entscheidung ins Protokoll? Mit WAMO_BERECHTIGUNG_LOG=1 alles, sonst nur abgelehnte Mikrofon-Anfragen. */
function protokollieren(art, permission, erlaubt, alles) {
  return alles || (art === "anfrage" && permission === "media" && !erlaubt);
}

/** Eine Protokollzeile (JSON) ohne Pfade oder Inhalte der Seite. */
function protokollZeile(art, permission, details, eigeneUi, erlaubt, jetzt = new Date()) {
  const medien = Array.isArray(details?.mediaTypes)
    ? details.mediaTypes.map(String).slice(0, 4)
    : details?.mediaType !== undefined
      ? [String(details.mediaType)]
      : [];
  return JSON.stringify({
    zeit: jetzt.toISOString(),
    art,
    berechtigung: String(permission).slice(0, 40),
    herkunft: herkunft(details?.requestingUrl ?? details?.securityOrigin),
    medien,
    eigeneUi: Boolean(eigeneUi),
    erlaubt: Boolean(erlaubt),
  });
}

// Meldungen der Oberfläche, die in logs/oberflaeche.log gehören: nur Warnungen und Fehler mit Kennung "[WAMO]"
// (Mikrofon still, Gerät lässt sich nicht öffnen, Spur ersetzt). Gesprächsinhalte loggt die Oberfläche nie so.
const OBERFLAECHE_KENNUNG = "[WAMO]";

function oberflaechenZeile(level, message, jetzt = new Date()) {
  if (!["info", "warning", "error"].includes(level)) return null;
  const text = String(message ?? "");
  if (!text.startsWith(OBERFLAECHE_KENNUNG)) return null;
  // eine Zeile, ohne Steuerzeichen, gekürzt
  const sauber = text.replace(/[\u0000-\u001f\u007f]+/g, " ").slice(0, 300);
  return `${jetzt.toISOString()} ${level} ${sauber}`;
}

module.exports = { anfrageErlaubt, herkunft, oberflaechenZeile, protokollieren, protokollZeile, pruefungErlaubt };
