// Reine Funktionen der Betreuer-Seite (kein DOM, kein Netz): Prüfung der Einwilligungsversion und der Bestätigung, Modell eines Platzes, Adressen
// der Betreuer-Schnittstelle, Fehlertexte, Zählung der Anzeigezeit des neuen Tokens. Mit node --test geprüft (tests/js).

import { formatZeit, kurzeId, platzTokenGueltig } from "./gemeinsam.js";

export const EINWILLIGUNG_MAX = 1000;
export const TOKEN_ANZEIGE_SEKUNDEN = 60;
const PLAETZE = 16;

// Die Funktionen der Betreuer-Schnittstelle (Pfade wie in betreuer_api.py). bestaetigung: Die Oberfläche verlangt das Eintippen der Platznummer.
// tokenNachher: Danach holt die Seite den neuen Platz-Token (der alte gilt nicht mehr).
export const AKTIONEN = Object.freeze({
  "neuer-durchlauf": Object.freeze({
    titel: "Neuer Durchlauf", knopf: "Neuer Durchlauf …", bestaetigung: true, braucht: "einwilligung", tokenNachher: true,
    beschreibung: "Setzt Board und Ablage auf den Ausgangsstand, legt einen neuen Durchlauf mit der Einwilligung an und entzieht dem alten Platz-Token den Zugang.",
  }),
  "durchlauf-beenden": Object.freeze({
    titel: "Durchlauf beenden", knopf: "Durchlauf beenden", bestaetigung: false, braucht: null, tokenNachher: false,
    beschreibung: "Beendet den Durchlauf: Der Kern nimmt keine Schreibaufrufe dieses Durchlaufs mehr an.",
  }),
  "zuruecksetzen": Object.freeze({
    titel: "Zurücksetzen", knopf: "Zurücksetzen …", bestaetigung: true, braucht: null, tokenNachher: false,
    beschreibung: "Stellt den Ausgangsstand wieder her. Alle Tickets und Kommentare dieses Durchlaufs gehen verloren, der Durchlauf bleibt.",
  }),
  "daten-loeschen": Object.freeze({
    titel: "Daten löschen", knopf: "Daten löschen …", bestaetigung: true, braucht: null, tokenNachher: true,
    beschreibung: "Löscht alle Daten des Platzes, schließt den Platz und entzieht dem Platz-Token den Zugang. Das lässt sich nicht rückgängig machen.",
  }),
  "platz-token": Object.freeze({
    titel: "Platz-Token erzeugen", knopf: "Platz-Token erzeugen", bestaetigung: false, braucht: null, tokenNachher: false,
    beschreibung: "Erzeugt einen neuen Platz-Token für den Besucher. Der alte gilt danach nicht mehr.",
  }),
});

const istPlatz = (wert) => Number.isInteger(wert) && wert >= 1 && wert <= PLAETZE;

export function aktionAdresse(platz, aktion) {
  if (!istPlatz(platz)) throw new TypeError("Platz muss eine ganze Zahl von 1 bis 16 sein");
  if (typeof aktion !== "string" || !Object.hasOwn(AKTIONEN, aktion)) throw new TypeError("unbekannte Funktion");
  return `/api/betreuer/plaetze/${platz}/${aktion}`;
}

// Die Version des Datenschutzhinweises: eine ganze Zahl von 1 bis EINWILLIGUNG_MAX, nicht kleiner als die höchste bisher erfasste (sie wächst nur).
export function einwilligungPruefen(eingabe, hoechste) {
  const nein = (fehler) => ({ ok: false, wert: null, fehler });
  if (typeof eingabe !== "string" || !/^\s*[1-9][0-9]{0,9}\s*$/.test(eingabe)) return nein("Bitte eine ganze Zahl ab 1 eingeben.");
  const wert = Number.parseInt(eingabe, 10);
  if (wert > EINWILLIGUNG_MAX) return nein(`Die Version darf höchstens ${EINWILLIGUNG_MAX} sein.`);
  const grenze = Number.isInteger(hoechste) && hoechste > 0 ? hoechste : 0;
  if (wert < grenze) return nein(`Die Version darf nicht kleiner sein als die zuletzt erfasste (${grenze}).`);
  return { ok: true, wert, fehler: "" };
}

export function hoechsteEinwilligung(plaetze) {
  if (!Array.isArray(plaetze)) return 0;
  return plaetze.reduce((hoechste, p) => (p && Number.isInteger(p.einwilligung_version) && p.einwilligung_version > hoechste ? p.einwilligung_version : hoechste), 0);
}

// Die Bestätigung: genau die Platznummer, mit Leerraum am Rand erlaubt.
export const bestaetigungOk = (eingabe, platz) => typeof eingabe === "string" && /^\s*[1-9][0-9]?\s*$/.test(eingabe) && eingabe.trim() === String(platz);

export function uebersichtOk(daten) {
  return Boolean(daten) && Array.isArray(daten.plaetze) && daten.plaetze.length > 0 && daten.plaetze.every((p) => p && Number.isInteger(p.platz));
}

function zustandVon(p) {
  if (p.offen === true) return { text: "Durchlauf offen", klasse: "zustand-offen" };
  if (Number.isInteger(p.einwilligung_version)) return { text: "Durchlauf beendet", klasse: "zustand-beendet" };
  return { text: "Geschlossen, kein Durchlauf", klasse: "zustand-beendet" };
}

export function platzModell(p, zeitzone = "Europe/Berlin") {
  const zustand = zustandVon(p);
  const zeit = formatZeit(p.einwilligung_zeit, zeitzone);
  const einwilligung = Number.isInteger(p.einwilligung_version) ? `Version ${p.einwilligung_version}${zeit ? ` vom ${zeit}` : ""}` : "keine";
  return {
    platz: p.platz, titel: `Platz ${p.platz}`, zustandText: zustand.text, zustandKlasse: zustand.klasse, durchlauf: kurzeId(p.durchlauf_id), einwilligungText: einwilligung,
    tickets: p.tickets, kommentare: p.kommentare, tokenText: p.hat_platz_token === true ? "Platz-Token vergeben" : "Kein Platz-Token", reserviert: p.reserviert === true,
    kannBeenden: p.offen === true,
  };
}

const kurzerText = (daten) => (daten && typeof daten.text === "string" && daten.text.length > 0 && daten.text.length <= 200 ? daten.text : "");

export function fehlerText(status, daten) {
  switch (status) {
    case 0: return "Keine Verbindung zum Dienst.";
    case 401: case 403: return "Der Betreuer-Token gilt nicht.";
    case 404: return "Dieser Platz oder diese Funktion ist nicht bekannt.";
    case 422: return kurzerText(daten) || "Die Eingabe wurde nicht angenommen.";
    case 429: return "Zu viele Anfragen, bitte kurz warten.";
    case 503: return kurzerText(daten) || "Das Löschen ist noch nicht abgeschlossen. Bitte wiederholen.";
    default: return "Der Dienst hat einen Fehler gemeldet.";
  }
}

// Wie viele Sekunden der neue Token noch sichtbar bleibt (60 ab dem Anzeigen). Eine zurückgestellte Uhr verlängert nie über 60.
export function tokenRestSekunden(startMs, jetztMs) {
  if (!Number.isFinite(startMs) || !Number.isFinite(jetztMs)) return 0;
  return Math.max(0, Math.ceil(TOKEN_ANZEIGE_SEKUNDEN - Math.max(0, jetztMs - startMs) / 1000));
}

// Der Link, den der Besucher öffnet: der Token steht im Fragment, nie in der Abfrage.
export const platzLink = (origin, token) => (platzTokenGueltig(token) ? `${origin}/board#t=${token}` : "");
