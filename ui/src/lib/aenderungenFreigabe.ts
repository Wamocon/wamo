/**
 * Reine Logik der Freigabe in der Karte "Änderungen" (Schritt 1.5b): wann der Knopf möglich ist, wie frisch der Code sein muss, was der Freigabe-Dienst
 * (POST /wamo/release/<id>/freigeben, infra/sokrates2/anker/freigabe_dienst.py) antworten kann und was die Oberfläche dann tut. Ohne React und ohne Netz,
 * damit sich alles mit node:test prüfen lässt (aenderungenFreigabe.test.ts).
 *
 * Die Freigabe bindet allein Commit und Baum, die die Karte zeigt. Entschieden wird im Freigabe-Dienst und in der Datenbank, nie hier: Diese Datei
 * macht nur die Bedienung klar und verhindert offensichtlich sinnlose Aufrufe.
 */
import type { Anfrage } from "./aenderungen.ts";

/** Der Freigabe-Dienst lehnt einen Code ab 60 Sekunden ab, die Oberfläche akzeptiert sonst bis zu 120: vor dem Senden gilt die strengere Grenze. */
export const CODE_MAX_ALTER_MS = 45_000;
/** Uhren dürfen leicht abweichen. */
const VORLAUF_MS = 2_000;

export type Voraussetzung = {
  moeglich: boolean;
  /** warum es nicht geht, oder ein Hinweis, wenn es geht */
  text: string | null;
  /** Bei einer Änderung an der Schutzschicht muss die Person den Haken setzen (Konzept E.2) */
  pflichtHaken: boolean;
};

/** Darf die Karte den Freigeben-Knopf anbieten? */
export function voraussetzung(a: Anfrage | null): Voraussetzung {
  if (!a || a.status !== "wartet_freigabe") return { moeglich: false, text: null, pflichtHaken: false };
  if (!a.v_commit || !a.tree_id) {
    return { moeglich: false, text: "Commit und Baum der Änderung fehlen in der Antwort. Bitte die Karte neu laden.", pflichtHaken: false };
  }
  if (a.klasse === "gesperrt") {
    return { moeglich: false, text: "Die Änderung berührt gesperrte Teile der Schutzschicht und lässt sich nicht freigeben.", pflichtHaken: false };
  }
  if (a.klasse === "sonder") {
    if (a.von_mir) {
      return { moeglich: false, text: "Die Änderung berührt die Schutzschicht. Weil du sie angefragt hast, muss ein anderer Admin freigeben.", pflichtHaken: true };
    }
    return { moeglich: true, text: "Die Änderung berührt die Schutzschicht (Sonderfreigabe, mit Wartezeit).", pflichtHaken: true };
  }
  return { moeglich: true, text: null, pflichtHaken: false };
}

/** Ist der Code im Token frisch genug zum Senden? aal2Ms: Zeitpunkt des letzten Codes in ms (anmeldung.aal2Zeitpunkt) oder null. */
export function codeFrisch(aal2Ms: number | null, jetztMs: number): boolean {
  if (aal2Ms === null || !Number.isFinite(aal2Ms)) return false;
  const alter = jetztMs - aal2Ms;
  return alter >= -VORLAUF_MS && alter <= CODE_MAX_ALTER_MS;
}

/** Der Rumpf des Aufrufs: genau Commit und Baum, die die Karte gezeigt hat, sonst nichts. */
export function freigabeBody(a: Anfrage): { commit_id: string; tree_id: string } | null {
  return a.v_commit && a.tree_id ? { commit_id: a.v_commit, tree_id: a.tree_id } : null;
}

export type FehlerAntwort = {
  text: string;
  /** der Dialog bleibt offen (die Person kann es gleich noch einmal versuchen) */
  offen: boolean;
  /** die Karte lädt den Stand neu */
  neuLaden: boolean;
  /** gilt als Erfolg */
  erfolg?: boolean;
  /** der Code soll neu eingegeben werden */
  neuerCode?: boolean;
};

/** Jeder Grund des Freigabe-Dienstes (anker/freigabe.py FEHLER) bekommt einen Satz und ein Verhalten. */
export const FEHLER: Record<string, FehlerAntwort> = {
  angaben: { text: "Die Angaben waren unvollständig. Bitte die Karte neu laden.", offen: false, neuLaden: true },
  anfrage: { text: "Die Anfrage war nicht lesbar. Bitte die Karte neu laden.", offen: false, neuLaden: true },
  token_ungueltig: { text: "Die Anmeldung ist nicht mehr gültig. Bitte melde dich neu an.", offen: false, neuLaden: false },
  aal2_fehlt: { text: "Für die Freigabe ist der zweite Faktor nötig. Bitte gib einen neuen Code ein.", offen: true, neuLaden: false, neuerCode: true },
  aal2_alt: { text: "Der Code ist zu alt. Bitte gib einen neuen Code ein.", offen: true, neuLaden: false, neuerCode: true },
  aal2_zukunft: { text: "Die Uhr deines Geräts geht anders als die des Servers. Bitte stelle die Uhrzeit automatisch ein und versuche es noch einmal.", offen: true, neuLaden: false, neuerCode: true },
  anfrage_unbekannt: { text: "Diese Änderung gibt es nicht mehr.", offen: false, neuLaden: true },
  falscher_status: { text: "Die Änderung wartet nicht mehr auf eine Freigabe.", offen: false, neuLaden: true },
  fassung_passt_nicht: { text: "Die Änderung hat sich geändert. Bitte lies die neue Fassung und gib dann frei.", offen: false, neuLaden: true },
  keine_berechtigung: { text: "Dir fehlt die Berechtigung für diese Freigabe.", offen: false, neuLaden: false },
  zweiter_admin: { text: "Diese Änderung braucht einen anderen Admin als dich.", offen: false, neuLaden: false },
  wartezeit: { text: "Die Wartezeit der Sonderfreigabe ist noch nicht vorbei. Bitte versuche es später noch einmal.", offen: false, neuLaden: false },
  schon_freigegeben: { text: "Diese Änderung ist schon freigegeben.", offen: false, neuLaden: true, erfolg: true },
  regel: { text: "Die Datenbank lehnt die Freigabe nach ihren Regeln ab.", offen: false, neuLaden: true },
  zu_viele: { text: "Zu viele Versuche. Bitte warte ein paar Minuten.", offen: false, neuLaden: false },
  datenbank: { text: "Die Datenbank ist gerade nicht erreichbar. Bitte versuche es gleich noch einmal.", offen: true, neuLaden: false },
  unbekannt: { text: "Der Freigabe-Dienst ist nicht eingerichtet.", offen: false, neuLaden: false },
  intern: { text: "Der Freigabe-Dienst meldet einen Fehler. Bitte versuche es später noch einmal.", offen: false, neuLaden: true },
};

export const NETZ_FEHLER: FehlerAntwort = {
  text: "Der Freigabe-Dienst hat nicht geantwortet. Ob die Freigabe angekommen ist, siehst du nach dem Neuladen der Karte. Bitte sende sie nicht noch einmal, bevor du nachgesehen hast.",
  offen: false,
  neuLaden: true,
};

/** Die Antwort zu einem Fehlercode. Unbekannte Codes und Statusfehler ohne Code ergeben den Netzfehler (nie ein stilles "ok"). */
export function fehlerFuer(code: string | null, httpStatus: number): FehlerAntwort {
  if (code && Object.prototype.hasOwnProperty.call(FEHLER, code)) return FEHLER[code];
  if (httpStatus === 401) return FEHLER.token_ungueltig;
  if (httpStatus === 429) return FEHLER.zu_viele;
  return NETZ_FEHLER;
}

/** Erfolgstext mit der Geltungsdauer der Freigabe (gilt_s in Sekunden), wenn der Dienst sie nennt. */
export function erfolgText(giltS: number | null): string {
  if (giltS !== null && Number.isFinite(giltS) && giltS > 0) {
    const min = Math.max(1, Math.round(giltS / 60));
    return `Freigegeben. Die Freigabe gilt etwa ${min} ${min === 1 ? "Minute" : "Minuten"}. Der Release-Dienst macht jetzt weiter.`;
  }
  return "Freigegeben. Der Release-Dienst macht jetzt weiter.";
}
