// Reine Funktionen der Board-Seite (kein DOM, kein Netz): Gruppieren nach Status, Modelle für Karte und Detail, Entscheidung nach einer Abfrage.
// Mit node --test geprüft (tests/js). Texte aus dem Dienst bleiben hier Text: Die Seite setzt sie nur über textContent.

import { formatZeit, ruecknahmeMs } from "./gemeinsam.js";

export const ABFRAGE_MS = 3000;
export const UNVOLLSTAENDIG_TEXT = "Dieser Platz-Link ist unvollständig, bitte beim Betreuer melden";

export const SPALTEN = Object.freeze([
  Object.freeze({ id: "zu_erledigen", titel: "Zu erledigen" }),
  Object.freeze({ id: "in_arbeit", titel: "In Arbeit" }),
  Object.freeze({ id: "erledigt", titel: "Erledigt" }),
]);

const TYP = Object.freeze({ bug: "Bug", aufgabe: "Aufgabe", story: "Story" });
const PRIORITAET = Object.freeze({ niedrig: "Niedrig", mittel: "Mittel", hoch: "Hoch", hoechste: "Höchste" });
const STATUS = Object.freeze({ zu_erledigen: "Zu erledigen", in_arbeit: "In Arbeit", erledigt: "Erledigt" });

const bezeichnung = (liste, wert) => (typeof wert === "string" && Object.hasOwn(liste, wert) ? liste[wert] : String(wert ?? ""));

export function nummerWert(nummer) {
  const treffer = typeof nummer === "string" ? /^LAB-(\d+)$/.exec(nummer) : null;
  return treffer ? Number.parseInt(treffer[1], 10) : 0;
}

// Die Tickets je Spalte, neueste Nummer zuerst (zahlenmäßig). Unbekannte Status fallen weg, die drei Spalten sind immer da.
export function gruppiereNachStatus(tickets) {
  const gruppen = { zu_erledigen: [], in_arbeit: [], erledigt: [] };
  if (!Array.isArray(tickets)) return gruppen;
  for (const ticket of tickets) {
    if (ticket && typeof ticket === "object" && typeof ticket.status === "string" && Object.hasOwn(gruppen, ticket.status)) gruppen[ticket.status].push(ticket);
  }
  for (const spalte of Object.values(gruppen)) spalte.sort((a, b) => nummerWert(b.nummer) - nummerWert(a.nummer));
  return gruppen;
}

// Die Klasse der Priorität kommt nur aus der festen Liste, nie aus dem Text des Dienstes.
const prioritaetKlasse = (wert) => (typeof wert === "string" && Object.hasOwn(PRIORITAET, wert) ? `prio-${wert}` : "prio-unbekannt");

export function kartenModell(ticket) {
  return {
    nummer: String(ticket.nummer ?? ""),
    titel: String(ticket.titel ?? ""),
    typText: bezeichnung(TYP, ticket.typ),
    prioritaetText: bezeichnung(PRIORITAET, ticket.prioritaet),
    prioritaetKlasse: prioritaetKlasse(ticket.prioritaet),
    kommentare: Array.isArray(ticket.kommentare) ? ticket.kommentare.length : 0,
    neu: ticket.im_durchlauf === true,
  };
}

export function detailModell(ticket, zeitzone = "Europe/Berlin") {
  const kommentare = Array.isArray(ticket.kommentare) ? ticket.kommentare : [];
  return {
    ...kartenModell(ticket),
    statusText: bezeichnung(STATUS, ticket.status),
    beschreibung: String(ticket.beschreibung ?? ""),
    version: ticket.version,
    angelegt: formatZeit(ticket.angelegt, zeitzone),
    geaendert: formatZeit(ticket.geaendert, zeitzone),
    kommentare: kommentare.map((k) => ({ id: k.kommentar_id, text: String(k.text ?? ""), zeit: formatZeit(k.angelegt, zeitzone) })),
  };
}

export function zusammenfassung(board) {
  const tickets = board && Array.isArray(board.tickets) ? board.tickets : [];
  return {
    tickets: tickets.length,
    neu: tickets.filter((t) => t && t.im_durchlauf === true).length,
    kommentare: tickets.reduce((summe, t) => summe + (t && Array.isArray(t.kommentare) ? t.kommentare.length : 0), 0),
  };
}

export const durchlaufText = (durchlauf) => (durchlauf && typeof durchlauf.offen === "boolean" ? (durchlauf.offen ? "Durchlauf offen" : "Durchlauf beendet") : "Zustand unbekannt");

export function fehlerMeldung(status) {
  if (status === 401) return "Dieser Platz-Link gilt nicht mehr, bitte beim Betreuer melden";
  if (status === 429) return "Zu viele Anfragen, die Seite wartet kurz.";
  return "Verbindung gestört, die Seite versucht es erneut.";
}

// Was die Seite nach einer Antwort tut: rendern und in 3 Sekunden wieder fragen, bei 401 aufhören (der Link gilt nicht mehr), bei 429 warten
// (Retry-After), bei allem anderen mit wachsender Pause wiederholen. status 0 heißt: keine Antwort (Verbindung gestört).
export function naechsterSchritt(status, retryAfter, fehlerFolge) {
  if (status === 200) return { aktion: "rendern", wartenMs: ABFRAGE_MS, meldung: "" };
  if (status === 401) return { aktion: "stoppen", wartenMs: 0, meldung: fehlerMeldung(401) };
  if (status === 429) return { aktion: "warten", wartenMs: ruecknahmeMs(retryAfter, fehlerFolge), meldung: fehlerMeldung(429) };
  return { aktion: "warten", wartenMs: ruecknahmeMs(null, fehlerFolge), meldung: fehlerMeldung(status) };
}
