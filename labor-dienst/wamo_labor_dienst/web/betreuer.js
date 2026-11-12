// Betreuer-Seite: Übersicht der 16 Plätze und die Funktionen der Betreuer-Schnittstelle. Der Betreuer-Token kommt aus dem Fragment (#b=...) oder aus dem
// Eingabefeld, bleibt nur im Speicher dieser Seite (nie in einem Speicher des Browsers) und wird sofort aus der Adresszeile entfernt. Alles, was Daten
// verwirft oder den Durchlauf wechselt, verlangt das Eintippen der Platznummer. Den neuen Platz-Token zeigt die Seite nur einmal und blendet ihn nach
// 60 Sekunden aus. Alle Texte aus dem Dienst gehen über textContent (dom.js), nie als HTML.

import { el, ersetzeKinder } from "./dom.js";
import { betreuerTokenGueltig, fragmentLesen, ruecknahmeMs } from "./gemeinsam.js";
import {
  AKTIONEN, aktionAdresse, bestaetigungOk, einwilligungPruefen, fehlerText, hoechsteEinwilligung, platzLink, platzModell, tokenRestSekunden, uebersichtOk,
} from "./betreuer-logik.js";

const UEBERSICHT = "/api/betreuer/plaetze";
const AUFFRISCHEN_MS = 10000;
const zustand = { token: null, plaetze: [], letzterText: "", timer: null, laeuft: false, fehlerFolge: 0, tokenAnzeige: null, fokus: null, knoepfe: new Map() };
const $ = (id) => document.getElementById(id);

// ---------- Meldungen ----------
function meldungZeigen(text, art) {
  const feld = $("meldung");
  feld.hidden = !text;
  feld.setAttribute("class", art === "fehler" ? "meldung meldung-fehler" : "meldung");
  feld.textContent = text;
}

// ---------- Anfragen ----------
async function anfrage(methode, pfad, koerper) {
  const kopf = { Authorization: `Bearer ${zustand.token}` };
  const optionen = { method: methode, headers: kopf, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer", redirect: "error" };
  if (koerper !== undefined) {
    kopf["Content-Type"] = "application/json";
    optionen.body = JSON.stringify(koerper);
  }
  try {
    const antwort = await fetch(pfad, optionen);
    let daten = null;
    try {
      daten = await antwort.json();
    } catch {
      daten = null;
    }
    return { status: antwort.status, daten, retryAfter: antwort.headers.get("Retry-After") };
  } catch {
    return { status: 0, daten: null, retryAfter: null };
  }
}

// ---------- Anmeldung und Abmeldung ----------
function ansichtUmschalten(angemeldet) {
  $("anmeldung").hidden = angemeldet;
  $("uebersicht").hidden = !angemeldet;
  $("abmelden").hidden = !angemeldet;
}

function abmelden(meldung) {
  clearTimeout(zustand.timer);
  tokenAusblenden(false);
  if ($("aktion").open) $("aktion").close();
  zustand.token = null;
  zustand.plaetze = [];
  zustand.letzterText = "";
  ersetzeKinder($("plaetze"));
  $("token-eingabe").value = "";
  ansichtUmschalten(false);
  meldungZeigen(meldung, meldung ? "fehler" : "");
}

async function anmelden(token) {
  zustand.token = token;
  zustand.fehlerFolge = 0;
  meldungZeigen("", "");
  await uebersichtLaden();
}

// ---------- Übersicht ----------
function zeile(name, wert) {
  return el("div", { klasse: "zeile" }, el("dt", { text: name }), el("dd", { text: String(wert) }));
}

function aktionsKnopf(m, id) {
  const a = AKTIONEN[id];
  const gefahr = id === "zuruecksetzen" || id === "daten-loeschen";
  const sperre = id === "durchlauf-beenden" && !m.kannBeenden;
  const knopf = el("button", {
    klasse: gefahr ? "knopf knopf-gefahr" : "knopf", text: a.knopf,
    attr: { type: "button", "aria-label": `${a.titel} für Platz ${m.platz}`, ...(sperre ? { disabled: "" } : {}) },
    ereignisse: { click: () => aktionStarten(m.platz, id) },
  });
  zustand.knoepfe.set(`${m.platz}:${id}`, knopf);
  return knopf;
}

function platzKarte(p) {
  const m = platzModell(p);
  return el("article", { klasse: "platz-karte", attr: { "aria-labelledby": `platz-titel-${m.platz}` } },
    el("div", { klasse: "platz-kopf" }, el("h3", { attr: { id: `platz-titel-${m.platz}` }, text: m.titel }), el("span", { klasse: `zustand ${m.zustandKlasse}`, text: m.zustandText })),
    m.reserviert ? el("p", { klasse: "hinweis", text: "Reserviert für die Abnahme, kein Besucher" }) : null,
    el("dl", { klasse: "daten" }, zeile("Durchlauf", m.durchlauf), zeile("Einwilligung", m.einwilligungText), zeile("Tickets", m.tickets), zeile("Kommentare", m.kommentare), zeile("Platz-Token", m.tokenText)),
    el("div", { klasse: "knopfreihe" }, ...Object.keys(AKTIONEN).map((id) => aktionsKnopf(m, id))));
}

function uebersichtZeichnen() {
  const fokus = [...zustand.knoepfe].find(([, knopf]) => knopf === document.activeElement);
  zustand.knoepfe = new Map();
  ersetzeKinder($("plaetze"), ...zustand.plaetze.map(platzKarte));
  if (fokus && zustand.knoepfe.has(fokus[0])) zustand.knoepfe.get(fokus[0]).focus();
}

function planen(ms) {
  clearTimeout(zustand.timer);
  if (zustand.token !== null) zustand.timer = setTimeout(auffrischen, ms);
}

async function uebersichtLaden() {
  for (let versuch = 0; versuch < 40 && zustand.laeuft; versuch += 1) await new Promise((fertig) => setTimeout(fertig, 50)); // eine laufende Abfrage abwarten
  if (zustand.laeuft) return false;
  zustand.laeuft = true;
  const r = await anfrage("GET", UEBERSICHT);
  zustand.laeuft = false;
  if (r.status === 401 || r.status === 403) {
    abmelden(fehlerText(r.status, r.daten));
    return false;
  }
  if (r.status === 200 && uebersichtOk(r.daten)) {
    zustand.fehlerFolge = 0;
    ansichtUmschalten(true);
    const text = JSON.stringify(r.daten);
    if (text !== zustand.letzterText) {
      zustand.letzterText = text;
      zustand.plaetze = r.daten.plaetze;
      uebersichtZeichnen();
    }
    $("stand").textContent = `Stand: ${new Date().toLocaleTimeString("de-DE")}`;
    meldungZeigen("", "");
    planen(AUFFRISCHEN_MS);
    return true;
  }
  zustand.fehlerFolge += 1;
  meldungZeigen(fehlerText(r.status, r.daten), "fehler");
  planen(ruecknahmeMs(r.retryAfter, zustand.fehlerFolge));
  return false;
}

async function auffrischen() {
  zustand.timer = null;
  if (zustand.token === null) return;
  if (document.hidden || $("aktion").open) { // ein unsichtbarer Tab oder ein offenes Fenster fragt nicht
    planen(AUFFRISCHEN_MS);
    return;
  }
  await uebersichtLaden();
}

// ---------- Neuer Platz-Token (nur einmal sichtbar) ----------
function tokenAusblenden(mitHinweis) {
  if (zustand.tokenAnzeige) clearInterval(zustand.tokenAnzeige.intervall);
  zustand.tokenAnzeige = null;
  const feld = $("token-feld");
  ersetzeKinder(feld, mitHinweis ? el("p", { klasse: "meta", text: "Der Token ist ausgeblendet. Mit „Platz-Token erzeugen“ gibt es einen neuen." }) : null);
  feld.hidden = !mitHinweis;
}

async function kopieren(text, was) {
  const status = $("kopier-status");
  try {
    await navigator.clipboard.writeText(text);
    status.textContent = `${was} kopiert.`;
  } catch {
    status.textContent = "Kopieren ist hier nicht möglich. Bitte den Text markieren und mit Strg+C kopieren.";
  }
}

function tokenTakt() {
  const anzeige = zustand.tokenAnzeige;
  if (!anzeige) return;
  const rest = tokenRestSekunden(anzeige.start, Date.now());
  if (rest <= 0) {
    tokenAusblenden(true);
    return;
  }
  $("token-rest").textContent = `Wird in ${rest} Sekunden ausgeblendet.`;
}

function tokenZeigen(platz, token) {
  tokenAusblenden(false);
  const link = platzLink(location.origin, token);
  const feld = $("token-feld");
  ersetzeKinder(feld,
    el("h3", { text: `Neuer Platz-Token für Platz ${platz}` }),
    el("p", { klasse: "meta", text: "Wird nur jetzt angezeigt. Der Besucher öffnet diesen Link (der Token steht im Fragment, nicht in der Abfrage):" }),
    el("p", { klasse: "token-box", text: link }),
    el("p", { klasse: "meta", text: "Der Token allein:" }),
    el("p", { klasse: "token-box", text: token }),
    el("div", { klasse: "knopfreihe" },
      el("button", { klasse: "knopf knopf-primaer", text: "Link kopieren", attr: { type: "button" }, ereignisse: { click: () => kopieren(link, "Link") } }),
      el("button", { klasse: "knopf", text: "Token kopieren", attr: { type: "button" }, ereignisse: { click: () => kopieren(token, "Token") } }),
      el("button", { klasse: "knopf", text: "Jetzt ausblenden", attr: { type: "button" }, ereignisse: { click: () => tokenAusblenden(true) } })),
    el("p", { klasse: "meta", attr: { id: "token-rest" }, text: "Wird in 60 Sekunden ausgeblendet." }),
    el("p", { klasse: "meta", attr: { id: "kopier-status", role: "status" } }));
  feld.hidden = false;
  zustand.tokenAnzeige = { platz, start: Date.now(), intervall: setInterval(tokenTakt, 1000) };
}

async function tokenHolen(platz) {
  const r = await anfrage("POST", aktionAdresse(platz, "platz-token"), {});
  if (r.status === 200 && r.daten && typeof r.daten.platz_token === "string") {
    tokenZeigen(platz, r.daten.platz_token);
    return "";
  }
  return fehlerText(r.status, r.daten);
}

// ---------- Funktionen ----------
async function ausfuehren(platz, id, koerper) {
  tokenAusblenden(false); // eine neue Funktion beginnt: ein noch sichtbarer Token verschwindet
  const r = await anfrage("POST", aktionAdresse(platz, id), koerper ?? {});
  if (r.status === 401 || r.status === 403) {
    abmelden(fehlerText(r.status, r.daten));
    return { ok: false, text: "" };
  }
  if (r.status !== 200) return { ok: false, text: fehlerText(r.status, r.daten) };
  if (id === "platz-token" && r.daten && typeof r.daten.platz_token === "string") tokenZeigen(platz, r.daten.platz_token);
  let hinweis = `${AKTIONEN[id].titel} für Platz ${platz}: ausgeführt.`;
  if (AKTIONEN[id].tokenNachher) {
    const problem = await tokenHolen(platz);
    if (problem) hinweis += ` Der neue Platz-Token konnte nicht geholt werden (${problem}). Bitte „Platz-Token erzeugen“ wählen.`;
  }
  zustand.letzterText = "";
  await uebersichtLaden();
  meldungZeigen(hinweis, "");
  return { ok: true, text: hinweis };
}

function aktionStarten(platz, id) {
  if (AKTIONEN[id].bestaetigung) fensterOeffnen(platz, id);
  else ausfuehren(platz, id, undefined);
}

function feldMitBeschriftung(kennung, beschriftung, eingabe, hilfe) {
  return el("p", { klasse: "feld" }, el("label", { attr: { for: kennung }, text: beschriftung }), eingabe, hilfe);
}

function fensterOeffnen(platz, id) {
  const a = AKTIONEN[id];
  const dialog = $("aktion");
  const hoechste = hoechsteEinwilligung(zustand.plaetze);
  const fehler = el("p", { klasse: "meldung meldung-fehler", attr: { role: "alert" } });
  fehler.hidden = true;
  const version = a.braucht === "einwilligung"
    ? el("input", { attr: { id: "einwilligung-eingabe", type: "text", inputmode: "numeric", autocomplete: "off", maxlength: "4", value: String(Math.max(1, hoechste)) } }) : null;
  const versionFehler = el("span", { klasse: "feld-fehler", attr: { id: "einwilligung-fehler" } });
  const bestaetigung = el("input", { attr: { id: "bestaetigung-eingabe", type: "text", inputmode: "numeric", autocomplete: "off", maxlength: "2", "aria-describedby": "bestaetigung-hilfe" } });
  const los = el("button", { klasse: "knopf knopf-gefahr", text: "Ausführen", attr: { type: "submit", disabled: "" } });

  const pruefen = () => {
    const v = version ? einwilligungPruefen(version.value, hoechste) : { ok: true, fehler: "" };
    versionFehler.textContent = version && version.value !== "" ? v.fehler : "";
    los.disabled = !(v.ok && bestaetigungOk(bestaetigung.value, platz));
  };
  const absenden = async (ereignis) => {
    ereignis.preventDefault();
    if (los.disabled) return;
    los.disabled = true;
    const koerper = version ? { einwilligung_version: einwilligungPruefen(version.value, hoechste).wert } : undefined;
    const ergebnis = await ausfuehren(platz, id, koerper);
    if (ergebnis.ok || zustand.token === null) {
      dialog.close();
      return;
    }
    fehler.textContent = ergebnis.text;
    fehler.hidden = false;
    pruefen();
  };
  for (const feld of [version, bestaetigung]) if (feld) feld.addEventListener("input", pruefen);

  ersetzeKinder(dialog, el("form", { klasse: "aktion-inhalt formular", ereignisse: { submit: absenden } },
    el("h2", { attr: { id: "aktion-titel" }, text: `Platz ${platz}: ${a.titel}` }),
    el("p", { text: a.beschreibung }),
    version ? feldMitBeschriftung("einwilligung-eingabe", "Version des Datenschutzhinweises (die Einwilligung liegt vor)", version,
      el("span", { klasse: "meta", text: hoechste > 0 ? ` Die Zahl darf nur wachsen, zuletzt erfasst: ${hoechste}.` : " Die Zahl darf nur wachsen." })) : null,
    version ? versionFehler : null,
    feldMitBeschriftung("bestaetigung-eingabe", `Zur Bestätigung die Platznummer ${platz} eintippen`, bestaetigung, el("span", { klasse: "meta", attr: { id: "bestaetigung-hilfe" }, text: " Erst dann wird „Ausführen“ frei." })),
    fehler,
    el("div", { klasse: "knopfreihe" }, los, el("button", { klasse: "knopf", text: "Abbrechen", attr: { type: "button" }, ereignisse: { click: () => dialog.close() } }))));
  pruefen();
  dialog.showModal();
  (version ?? bestaetigung).focus();
}

// ---------- Start ----------
function start() {
  const roh = fragmentLesen(location.hash, "b");
  history.replaceState(null, "", location.pathname); // Fragment und Abfrage verschwinden aus der Adresszeile und dem Verlauf
  $("anmelde-formular").addEventListener("submit", (ereignis) => {
    ereignis.preventDefault();
    const wert = $("token-eingabe").value.trim();
    $("token-eingabe").value = "";
    if (!betreuerTokenGueltig(wert)) {
      meldungZeigen("Der Token hat nicht die erwartete Form (43 bis 200 Zeichen).", "fehler");
      return;
    }
    anmelden(wert);
  });
  $("abmelden").addEventListener("click", () => abmelden(""));
  $("aktualisieren").addEventListener("click", () => {
    zustand.letzterText = "";
    uebersichtLaden();
  });
  $("aktion").addEventListener("click", (ereignis) => {
    if (ereignis.target === $("aktion")) $("aktion").close();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && zustand.token !== null) planen(0);
  });
  if (roh !== null) {
    if (betreuerTokenGueltig(roh)) anmelden(roh);
    else meldungZeigen("Der Token im Link hat nicht die erwartete Form.", "fehler");
  }
}

start();
