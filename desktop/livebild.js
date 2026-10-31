// WAMO-Desktop: Schalter am Laptop für das Live-Bild (reines Node-Modul, ohne electron-Import).
//
// Das Live-Bild zeigt, was auf diesem Laptop zu sehen ist. Deshalb entscheidet hier nur die Person am Laptop:
//  * Standard ist AUS. Eingeschaltet wird nur über das Tray-Menü der App (main.js), nie über die Oberfläche im Fenster, nie
//    über den Kern. Die Brücke window.wamoDesktop kennt dafür keinen Befehl.
//  * Vor dem ersten Einschalten (und nach jeder neuen Bindung des Laptops) fragt ein Dialog nach der Einwilligung mit
//    Inhalt, Zweck, Dauer und Datenschutz (EINWILLIGUNG_TEXT). Ohne Zustimmung bleibt alles aus.
//  * Der Schalter ist eine Datei, %LOCALAPPDATA%\WAMO\live-bild.json (neben geraet.json). Sie gilt nur für DIESEN Laptop
//    (Kennung und Besitzer aus geraet.json) und nur, wenn dort die Stufe "admin" steht. Der Host (host/live_bild.py) liest sie
//    bei jedem Bild selbst und prüft zusätzlich Ablauf ("bis", höchstens eine Stunde), einen Herzschlag (alle 2 s erneuert)
//    und "sichtbar": der rote Hinweis am Laptop muss leben. Fällt die App aus oder ist der Hinweis verdeckt oder weggeblendet,
//    kommt kein Bild mehr.
//  * Beim Start, beim Beenden und bei Ablauf wird die Datei auf "aus" gesetzt: ein Absturz hinterlässt nie ein "an".
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const EINWILLIGUNG_VERSION = 1;
const DAUER_MS = 30 * 60 * 1000; // so lange gilt ein Einschalten; danach schaltet die Person neu ein
const DAUER_MAX_MS = 60 * 60 * 1000; // der Host akzeptiert höchstens eine Stunde
const HERZSCHLAG_MS = 2000;
const DATEI_MAX = 8192;
const RE_KENNUNG = /^pc-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const RE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** Text der Einwilligung (Dialog am Laptop): Inhalt, Zweck, Dauer, Datenschutz. Ohne Gedankenstriche, echte Umlaute. */
const EINWILLIGUNG = {
  titel: "WAMO Live-Bild",
  frage: "Live-Bild dieses Laptops einschalten?",
  detail: [
    "Inhalt: Das Live-Bild überträgt als einzelne Bilder, was auf dem Bildschirm dieses Laptops zu sehen ist. Dabei können Kundendaten, E-Mails und Chats anderer Personen sichtbar sein.",
    "Zweck: Du siehst deinen eigenen Laptop von unterwegs oder von einem anderen Gerät aus in deiner WAMO-Ansicht.",
    `Dauer: Das Live-Bild läuft höchstens ${DAUER_MS / 60000} Minuten. Es endet früher bei Sperre, Abmelden, Trennen oder wenn niemand mehr zusieht. Danach schaltest du es hier neu ein.`,
    "Datenschutz: Nichts wird aufgezeichnet oder gespeichert. Die Bilder gehen nur an deine eigene Sitzung, nie an ein Sprachmodell. Solange das Bild läuft, zeigt dieser Laptop einen roten Rahmen. Passwortfelder lassen sich nicht automatisch ausblenden: Schließe vorher Fenster, die niemand sehen soll.",
    "Du kannst das Live-Bild jederzeit im Tray-Menü von WAMO ausschalten.",
  ].join("\n\n"),
  zustimmen: "Einwilligen und einschalten",
  ablehnen: "Abbrechen",
};

const istObjekt = (v) => typeof v === "object" && v !== null && !Array.isArray(v);

/**
 * @param {object} opt
 * @param {string} opt.datenOrdner   %LOCALAPPDATA%\WAMO
 * @param {() => Promise<boolean>} opt.fragen   Einwilligungsdialog; true = Zustimmung
 * @param {(an: boolean, bisMs: number) => void} [opt.hinweis]   roten Hinweis am Laptop zeigen oder entfernen
 * @param {() => boolean} [opt.sichtbar]   ist der Hinweis gerade zu sehen (und kein Vollbild)?
 * @param {(an: boolean) => void} [opt.geaendert]   Zustand hat sich geändert (Tray)
 */
function erzeugeLiveBild(opt) {
  const dsys = opt.fs ?? fs;
  const jetzt = opt.jetzt ?? Date.now;
  const log = opt.log ?? (() => {});
  const setzeTimer = opt.setInterval ?? setInterval;
  const loescheTimer = opt.clearInterval ?? clearInterval;
  const datei = path.join(opt.datenOrdner, "live-bild.json");
  const einwilligungsDatei = path.join(opt.datenOrdner, "live-bild-einwilligung.json");
  const geraetDatei = path.join(opt.datenOrdner, "geraet.json");
  let an = false;
  let bis = 0;
  let timer = null;
  let fehlschlaege = 0; // aufeinanderfolgende Schreibfehler im Herzschlag
  let gebunden = null; // Kennung und Besitzer beim Einschalten: eine neue Bindung beendet das Live-Bild
  let frage = null; // laufender Dialog: nie zwei zugleich

  function liesJson(pfad) {
    try {
      const roh = dsys.readFileSync(pfad, "utf8");
      if (roh.length > DATEI_MAX) return null;
      const d = JSON.parse(roh);
      return istObjekt(d) ? d : null;
    } catch {
      return null;
    }
  }

  /** { kennung, besitzer, stufe } dieses Laptops aus geraet.json oder null (nicht eingerichtet). */
  function geraet() {
    const g = liesJson(geraetDatei);
    if (!g || typeof g.kennung !== "string" || !RE_KENNUNG.test(g.kennung)) return null;
    if (typeof g.besitzer !== "string" || !RE_UUID.test(g.besitzer.toLowerCase())) return null;
    return { kennung: g.kennung, besitzer: g.besitzer.toLowerCase(), stufe: g.stufe === "admin" ? "admin" : "mitglied" };
  }

  function warte(ms) {
    try {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
    } catch {
      /* ohne Pause weiter */
    }
  }

  /** Atomar schreiben. Unter Windows scheitert das Umbenennen, solange der Host die Datei gerade liest: kurz wiederholen,
   *  zuletzt direkt schreiben (die Datei ist klein, der Host liest sie ohnehin bei jeder Anfrage neu). */
  function schreibeAtomar(pfad, inhalt) {
    const tmp = `${pfad}.neu`;
    const text = JSON.stringify(inhalt);
    dsys.mkdirSync(opt.datenOrdner, { recursive: true });
    for (let versuch = 0; versuch < 3; versuch += 1) {
      try {
        dsys.writeFileSync(tmp, text, { encoding: "utf8" });
        dsys.renameSync(tmp, pfad);
        return;
      } catch (err) {
        if (versuch === 2) {
          dsys.writeFileSync(pfad, text, { encoding: "utf8" }); // wirft, wenn auch das nicht geht
          return;
        }
        warte(25 * (versuch + 1));
      }
    }
  }

  function hatEinwilligung(g) {
    const e = liesJson(einwilligungsDatei);
    return Boolean(e && e.v === EINWILLIGUNG_VERSION && e.kennung === g.kennung && String(e.besitzer).toLowerCase() === g.besitzer);
  }

  function merkeEinwilligung(g) {
    schreibeAtomar(einwilligungsDatei, { v: EINWILLIGUNG_VERSION, kennung: g.kennung, besitzer: g.besitzer, zeit: new Date(jetzt()).toISOString() });
  }

  /** Schalterdatei schreiben. aus: nur "an: false" (die Datei verschwindet nie, damit der Host einen klaren Stand liest). */
  function schreibeSchalter() {
    const g = geraet();
    if (!an || !g) {
      schreibeAtomar(datei, { v: 1, an: false });
      return;
    }
    const t = jetzt();
    schreibeAtomar(datei, {
      v: 1,
      an: true,
      bis: Math.min(bis, t + DAUER_MAX_MS) / 1000,
      herzschlag: t / 1000,
      sichtbar: Boolean(opt.sichtbar ? opt.sichtbar() : false),
      einwilligung: hatEinwilligung(g) ? EINWILLIGUNG_VERSION : 0,
      kennung: g.kennung,
      besitzer: g.besitzer,
    });
  }

  function ausschalten(grund = "ausgeschaltet") {
    const war = an;
    an = false;
    bis = 0;
    gebunden = null;
    if (timer) loescheTimer(timer);
    timer = null;
    try {
      schreibeSchalter();
    } catch (err) {
      log(`Schalterdatei nicht geschrieben (${err.code ?? err.message})`);
    }
    if (war) {
      log(`Live-Bild aus (${grund})`);
      opt.hinweis?.(false, 0);
      opt.geaendert?.(false);
    }
  }

  /** Ein Schlag des Herzens: Ablauf prüfen, Herzschlag und Sichtbarkeit erneuern. */
  function tick() {
    if (!an) return;
    const g = geraet();
    if (jetzt() >= bis || !g || g.stufe !== "admin" || g.kennung !== gebunden?.kennung || g.besitzer !== gebunden?.besitzer) {
      ausschalten("Zeit abgelaufen oder Gerät geändert");
      return;
    }
    try {
      schreibeSchalter();
      fehlschlaege = 0;
    } catch (err) {
      // Ein einzelner Fehlschlag beendet nichts (der Host prüft den Herzschlag ohnehin); mehrere in Folge schon
      fehlschlaege += 1;
      if (fehlschlaege >= 3) ausschalten(`Schalterdatei nicht beschreibbar (${err.code ?? err.message})`);
    }
  }

  /** Verfügbar nur auf einem eingerichteten Laptop eines Admins. */
  function verfuegbar() {
    const g = geraet();
    return Boolean(g && g.stufe === "admin");
  }

  async function einschalten() {
    if (an) return { ok: true, schonAn: true };
    const g = geraet();
    if (!g) return { ok: false, grund: "Dieser Laptop ist noch nicht eingerichtet." };
    if (g.stufe !== "admin") return { ok: false, grund: "Das Live-Bild gibt es nur auf dem Laptop eines Admins." };
    if (frage) return { ok: false, grund: "Die Einwilligungsfrage ist schon offen." };
    if (!hatEinwilligung(g)) {
      frage = Promise.resolve().then(() => opt.fragen());
      let ja = false;
      try {
        ja = (await frage) === true;
      } catch {
        ja = false;
      } finally {
        frage = null;
      }
      if (!ja) return { ok: false, abgelehnt: true, grund: "Ohne Einwilligung bleibt das Live-Bild aus." };
      try {
        merkeEinwilligung(g);
      } catch (err) {
        log(`Einwilligung nicht gespeichert (${err.code ?? err.message})`);
        return { ok: false, grund: "Die Einwilligung ließ sich nicht speichern." };
      }
    }
    if (geraet()?.kennung !== g.kennung) return { ok: false, grund: "Der Laptop wurde währenddessen neu gebunden." };
    an = true;
    gebunden = g;
    fehlschlaege = 0;
    bis = jetzt() + DAUER_MS;
    try {
      opt.hinweis?.(true, bis);
      schreibeSchalter();
    } catch (err) {
      ausschalten(`Schalterdatei nicht beschreibbar (${err.code ?? err.message})`);
      return { ok: false, grund: "Der Schalter ließ sich nicht schreiben." };
    }
    timer = setzeTimer(tick, HERZSCHLAG_MS);
    timer.unref?.();
    log(`Live-Bild an bis ${new Date(bis).toISOString()}`);
    opt.geaendert?.(true);
    return { ok: true };
  }

  /** Beim Start: einen übrig gebliebenen Schalter (Absturz) löschen. */
  function start() {
    try {
      schreibeAtomar(datei, { v: 1, an: false });
    } catch (err) {
      log(`Schalterdatei nicht geschrieben (${err.code ?? err.message})`);
    }
  }

  return {
    start,
    einschalten,
    ausschalten,
    tick,
    verfuegbar,
    status: () => ({ an, verfuegbar: verfuegbar(), restS: an ? Math.max(0, Math.round((bis - jetzt()) / 1000)) : 0 }),
  };
}

module.exports = { erzeugeLiveBild, EINWILLIGUNG, DAUER_MS, DAUER_MAX_MS, HERZSCHLAG_MS, EINWILLIGUNG_VERSION };
