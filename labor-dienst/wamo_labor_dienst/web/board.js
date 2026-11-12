// Board-Seite für Besucher: nur lesend. Der Platz-Token steht im URL-Fragment (#t=...), wird nur im Speicher dieser Seite gehalten und sofort aus der
// Adresszeile entfernt. Alle 3 Sekunden fragt die Seite GET /api/browser/board (nur bei sichtbarem Tab). Alle Texte aus dem Dienst gehen über
// textContent (dom.js), nie als HTML.

import { el, ersetzeKinder } from "./dom.js";
import { fragmentLesen, platzTokenGueltig } from "./gemeinsam.js";
import {
  SPALTEN, UNVOLLSTAENDIG_TEXT, detailModell, durchlaufText, gruppiereNachStatus, kartenModell, naechsterSchritt, zusammenfassung,
} from "./board-logik.js";

const ADRESSE = "/api/browser/board";
const zustand = { token: null, timer: null, stopp: false, laeuft: false, fehlerFolge: 0, letzterText: "", board: null, offeneNummer: null, offenerStand: "", karten: new Map() };
const $ = (id) => document.getElementById(id);

// ---------- Token ----------
function tokenUebernehmen() {
  const roh = fragmentLesen(location.hash, "t");
  history.replaceState(null, "", location.pathname); // Fragment (und jede Abfrage) verschwinden aus der Adresszeile, auch aus dem Verlauf
  return roh !== null && platzTokenGueltig(roh) ? roh : null;
}

// ---------- Anzeige ----------
function meldungZeigen(text, art) {
  const feld = $("meldung");
  feld.hidden = !text;
  feld.setAttribute("class", art === "fehler" ? "meldung meldung-fehler" : "meldung");
  feld.textContent = text;
}

function kopfZeichnen(board) {
  $("platz").textContent = `Platz ${board.platz}`;
  const offen = board.durchlauf && board.durchlauf.offen === true;
  const feld = $("zustand");
  feld.setAttribute("class", offen ? "zustand zustand-offen" : "zustand zustand-beendet");
  feld.textContent = durchlaufText(board.durchlauf);
  document.title = `Platz ${board.platz}, WAMOCON Test-Jira`;
}

function zusammenfassungZeichnen(board) {
  const z = zusammenfassung(board);
  ersetzeKinder($("zusammenfassung"), el("span", { klasse: "chip", text: `Tickets: ${z.tickets}` }), " ", el("span", { klasse: "chip", text: `Neu in diesem Durchlauf: ${z.neu}` }), " ",
    el("span", { klasse: "chip", text: `Kommentare: ${z.kommentare}` }));
}

function kartenElement(ticket) {
  const m = kartenModell(ticket);
  const karte = el("button", { klasse: `karte ${m.prioritaetKlasse}`, attr: { type: "button" }, ereignisse: { click: () => detailOeffnen(m.nummer) } },
    el("span", { klasse: "karte-kopf" }, el("span", { klasse: "schluessel", text: m.nummer }), m.neu ? el("span", { klasse: "chip chip-neu", text: "Neu" }) : null),
    el("span", { klasse: "karte-titel", text: m.titel }),
    el("span", { klasse: "karte-fuss" }, el("span", { klasse: "chip", text: m.typText }), " ", el("span", { klasse: `chip ${m.prioritaetKlasse}`, text: m.prioritaetText }), " ",
      el("span", { klasse: "chip", text: `Kommentare: ${m.kommentare}` })));
  zustand.karten.set(m.nummer, karte);
  return el("li", { klasse: "karten-eintrag" }, karte);
}

function spalteBauen(spalte, tickets) {
  const titel = el("h2", { klasse: "spalte-titel", attr: { id: `spalte-${spalte.id}` } }, spalte.titel, " ", el("span", { klasse: "zaehler", text: String(tickets.length) }));
  const eintraege = tickets.length ? tickets.map(kartenElement) : [el("li", { klasse: "leer", text: "Keine Tickets" })];
  return el("section", { klasse: "spalte", attr: { "data-spalte": spalte.id } }, titel, el("ul", { klasse: "spalte-liste", attr: { "aria-labelledby": `spalte-${spalte.id}` } }, ...eintraege));
}

function spaltenZeichnen(board) {
  const alt = new Map([...document.querySelectorAll("[data-spalte]")].map((s) => [s.getAttribute("data-spalte"), s.querySelector("ul").scrollTop]));
  const fokus = [...zustand.karten].find(([, karte]) => karte === document.activeElement);
  zustand.karten = new Map();
  const gruppen = gruppiereNachStatus(board.tickets);
  ersetzeKinder($("spalten"), ...SPALTEN.map((s) => spalteBauen(s, gruppen[s.id])));
  for (const spalte of document.querySelectorAll("[data-spalte]")) spalte.querySelector("ul").scrollTop = alt.get(spalte.getAttribute("data-spalte")) ?? 0;
  if (fokus && zustand.karten.has(fokus[0])) zustand.karten.get(fokus[0]).focus();
}

// ---------- Detail ----------
function chipReihe(modell) {
  return el("p", { klasse: "chips" }, el("span", { klasse: "chip", text: modell.typText }), " ", el("span", { klasse: `chip ${modell.prioritaetKlasse}`, text: modell.prioritaetText }), " ",
    el("span", { klasse: "chip", text: modell.statusText }));
}

function kommentarListe(modell) {
  if (!modell.kommentare.length) return el("p", { klasse: "leer", text: "Noch keine Kommentare." });
  return el("ol", { klasse: "kommentare" }, ...modell.kommentare.map((k) => el("li", { klasse: "kommentar" }, el("p", { klasse: "textblock", text: k.text }), el("p", { klasse: "meta", text: k.zeit }))));
}

function detailZeichnen(ticket) {
  const modell = detailModell(ticket);
  const dialog = $("detail");
  const hatteFokus = dialog.contains(document.activeElement);
  const schliessen = el("button", { klasse: "knopf", attr: { type: "button" }, text: "Schließen", ereignisse: { click: () => dialog.close() } });
  ersetzeKinder(dialog, el("div", { klasse: "detail-inhalt" },
    el("div", { klasse: "detail-kopf" }, el("p", { klasse: "schluessel", text: modell.nummer }), schliessen),
    el("h2", { attr: { id: "detail-titel" }, text: modell.titel }),
    chipReihe(modell),
    el("h3", { text: "Beschreibung" }), el("p", { klasse: "textblock", text: modell.beschreibung || "Keine Beschreibung." }),
    el("h3", { text: `Kommentare (${modell.kommentare.length})` }), kommentarListe(modell),
    el("p", { klasse: "meta", text: `Angelegt ${modell.angelegt}, geändert ${modell.geaendert}, Version ${modell.version}` })));
  if (hatteFokus) schliessen.focus();
}

function detailOeffnen(nummer) {
  const ticket = (zustand.board?.tickets ?? []).find((t) => t.nummer === nummer);
  if (!ticket) return;
  zustand.offeneNummer = nummer;
  zustand.offenerStand = JSON.stringify(ticket);
  detailZeichnen(ticket);
  const dialog = $("detail");
  if (!dialog.open) dialog.showModal();
}

function detailAktualisieren() {
  if (zustand.offeneNummer === null) return;
  const ticket = zustand.board.tickets.find((t) => t.nummer === zustand.offeneNummer);
  if (!ticket) {
    $("detail").close();
    return;
  }
  const stand = JSON.stringify(ticket);
  if (stand === zustand.offenerStand) return;
  zustand.offenerStand = stand;
  detailZeichnen(ticket);
}

// ---------- Abfragen ----------
function uebernehmen(text) {
  let daten;
  try {
    daten = JSON.parse(text);
  } catch {
    return false;
  }
  if (!daten || typeof daten.platz !== "number" || !Array.isArray(daten.tickets)) return false;
  if (text !== zustand.letzterText) {
    zustand.letzterText = text;
    zustand.board = daten;
    kopfZeichnen(daten);
    zusammenfassungZeichnen(daten);
    spaltenZeichnen(daten);
    detailAktualisieren();
  }
  return true;
}

function planen(ms) {
  clearTimeout(zustand.timer);
  zustand.timer = setTimeout(abfragen, ms);
}

function beenden(meldung) {
  zustand.stopp = true;
  zustand.token = null; // der Token verschwindet aus dem Speicher der Seite
  zustand.board = null;
  zustand.letzterText = "";
  if ($("detail").open) $("detail").close();
  ersetzeKinder($("spalten"));
  ersetzeKinder($("zusammenfassung"));
  $("zustand").textContent = "";
  $("zustand").setAttribute("class", "zustand");
  meldungZeigen(meldung, "fehler");
}

async function antwortAuswerten(antwort) {
  if (antwort.status === 200 && uebernehmen(await antwort.text())) {
    zustand.fehlerFolge = 0;
    return naechsterSchritt(200, null, 0);
  }
  zustand.fehlerFolge += 1;
  return naechsterSchritt(antwort.status === 200 ? 0 : antwort.status, antwort.headers.get("Retry-After"), zustand.fehlerFolge);
}

async function abfragen() {
  zustand.timer = null;
  if (zustand.stopp || zustand.laeuft || document.hidden) return; // ein unsichtbarer Tab fragt nicht, das Sichtbarwerden holt es sofort nach
  zustand.laeuft = true;
  let schritt;
  try {
    const antwort = await fetch(ADRESSE, {
      method: "GET", headers: { "X-Platz-Token": zustand.token }, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer", redirect: "error",
    });
    schritt = await antwortAuswerten(antwort);
  } catch {
    zustand.fehlerFolge += 1;
    schritt = naechsterSchritt(0, null, zustand.fehlerFolge);
  }
  zustand.laeuft = false;
  if (schritt.aktion === "stoppen") {
    beenden(schritt.meldung);
    return;
  }
  meldungZeigen(schritt.meldung, schritt.aktion === "warten" ? "hinweis" : "");
  planen(schritt.wartenMs);
}

function start() {
  zustand.token = tokenUebernehmen();
  $("detail").addEventListener("click", (ereignis) => {
    if (ereignis.target === $("detail")) $("detail").close(); // Klick neben das Fenster
  });
  $("detail").addEventListener("close", () => {
    zustand.offeneNummer = null;
  });
  if (zustand.token === null) {
    zustand.stopp = true;
    $("platz").textContent = "Kein Platz";
    meldungZeigen(UNVOLLSTAENDIG_TEXT, "fehler");
    return;
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !zustand.stopp) planen(0);
  });
  abfragen();
}

start();
