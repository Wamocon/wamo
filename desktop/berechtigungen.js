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
  if (!eigeneUi || details?.isMainFrame === false) return false; // Unterframes bekommen nie etwas
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
  if (!eigeneUi || details?.isMainFrame === false) return false;
  if (permission === "media") return details?.mediaType === "audio";
  return permission === "clipboard-sanitized-write";
}

/**
 * Hängt beide Handler an eine Session. eigeneUi(wc, url) sagt, ob die Anfrage vom Hauptfenster mit der eigenen
 * Oberfläche kommt (main.js: dasselbe WebContents und derselbe Origin). protokoll(art, permission, details, eigene,
 * erlaubt) schreibt die Diagnose. Ein Weg für App und Test, damit der Test genau diesen Code prüft.
 */
function installieren(ses, eigeneUi, protokoll = () => {}) {
  // Jede Ausnahme (eigeneUi, Entscheidung, Protokoll) führt zu "nicht erlaubt", nie zu einem hängenden Aufruf;
  // geantwortet wird vor dem Protokoll, das Schreiben der Diagnose darf nichts aufhalten.
  const bewerten = (wc, url, entscheiden) => {
    try {
      const eigene = Boolean(eigeneUi(wc, url));
      return { eigene, erlaubt: entscheiden(eigene) === true };
    } catch {
      return { eigene: false, erlaubt: false };
    }
  };
  const festhalten = (...args) => {
    try {
      protokoll(...args);
    } catch {
      // Diagnose ist nebensächlich
    }
  };
  ses.setPermissionRequestHandler((wc, permission, callback, details) => {
    const { eigene, erlaubt } = bewerten(wc, details?.requestingUrl, (e) => anfrageErlaubt(permission, details, e));
    callback(erlaubt);
    festhalten("anfrage", permission, details, eigene, erlaubt);
  });
  ses.setPermissionCheckHandler((wc, permission, requestingOrigin, details) => {
    const { eigene, erlaubt } = bewerten(wc, requestingOrigin, (e) => pruefungErlaubt(permission, details, e));
    festhalten("pruefung", permission, details, eigene, erlaubt);
    return erlaubt;
  });
}

// Windows-Datenschutz für das Mikrofon (systemPreferences.getMediaAccessStatus): Sperrt Windows Desktop-Apps das
// Mikrofon, öffnet Chromium es trotzdem und bekommt nur Stille, ohne Fehler. Die Oberfläche darf nur diesen Wert erfahren.
const MIKROFON_STATUS = ["granted", "denied", "restricted", "not-determined"];

/** Fester Wertevorrat für die Oberfläche; alles andere ist "unknown". */
function mikrofonStatusWert(roh) {
  return MIKROFON_STATUS.includes(roh) ? roh : "unknown";
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

// Meldungen der Oberfläche, die in logs/oberflaeche.log gehören: nur Warnungen und Fehler (und Infos) mit der Kennung
// "[WAMO] Mikrofon" (Mikrofon still, Gerät lässt sich nicht öffnen, Spur ersetzt, liefert Ton). Gesprächsinhalte loggt
// die Oberfläche nie so. Jeder Code im Origin der Oberfläche könnte solche Zeilen schreiben, deshalb: enge Kennung,
// Steuer- und Formatzeichen (auch Unicode: Zeilentrenner, Bidi) raus, Länge und Rate begrenzt (ratenGrenze).
const OBERFLAECHE_KENNUNG = "[WAMO] Mikrofon";

function oberflaechenZeile(level, message, jetzt = new Date()) {
  if (!["info", "warning", "error"].includes(level)) return null;
  const text = String(message ?? "");
  if (!text.startsWith(OBERFLAECHE_KENNUNG)) return null;
  // eine Zeile, ohne Steuer- und Formatzeichen, gekürzt
  const sauber = text.replace(/[\p{Cc}\p{Cf}\u2028\u2029]+/gu, " ").slice(0, 300);
  return `${jetzt.toISOString()} ${level} ${sauber}`;
}

/** Höchstens max Aufrufe je zeitraumMs; danach false (z. B. gegen eine Seite, die das Protokoll flutet). */
function ratenGrenze(max, zeitraumMs, jetzt = Date.now) {
  let fensterStart = jetzt();
  let anzahl = 0;
  return () => {
    const t = jetzt();
    if (t - fensterStart >= zeitraumMs) {
      fensterStart = t;
      anzahl = 0;
    }
    anzahl += 1;
    return anzahl <= max;
  };
}

module.exports = {
  anfrageErlaubt,
  herkunft,
  installieren,
  mikrofonStatusWert,
  oberflaechenZeile,
  protokollieren,
  protokollZeile,
  pruefungErlaubt,
  ratenGrenze,
};
