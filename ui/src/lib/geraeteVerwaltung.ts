/**
 * Reine Logik der Geräteverwaltung für Admins (components/einstellungen/GeraeteVerwaltung.tsx), ohne React und ohne
 * Browser, damit sie sich mit node:test prüfen lässt (geraeteVerwaltung.test.ts).
 *
 * Die Daten kommen vom Kern (voice-core/geraete_admin_api.py) und werden hier streng geprüft; fremde Texte (Namen,
 * Gründe, Rechnernamen) werden von Steuer- und Bidi-Zeichen befreit und gekürzt und später nur als Text angezeigt.
 * Maßgeblich bleibt die Datenbank: Die Oberfläche entscheidet nichts, sie zeigt an, fragt nach und sendet.
 *
 * Grundregel der Oberfläche: Ein Gerät wird nie mit einem einzigen Klick entschieden. Jede Aktion geht durch einen
 * Bestätigungsdialog (bestaetigungFuer) mit dem Fingerabdruck, dem Hinweis auf den Vergleich am Laptop und einem frischen
 * Code des zweiten Faktors.
 */

export type GeraeteStatus = "beantragt" | "aktiv" | "gesperrt" | "widerrufen";
export type Aktion = "freigeben" | "sperren" | "entsperren" | "widerrufen";

export type Ereignis = {
  von: GeraeteStatus | null;
  nach: GeraeteStatus | null;
  durch: string | null;
  grund: string | null;
  at: string | null;
};

export type Geraet = {
  kennung: string;
  name: string;
  rechnername: string | null;
  profilName: string | null;
  profilAktiv: boolean;
  status: GeraeteStatus;
  bindung: "automatisch" | "admin";
  fingerabdruck: string | null;
  erstellt: string | null;
  freigegeben: string | null;
  gesperrt: string | null;
  grund: string | null;
  zuletztGesehen: string | null;
  ereignisse: Ereignis[];
  /** das Gerät gehört dem Profil, mit dem diese Sitzung angemeldet ist */
  eigen: boolean;
};

const STATUS: readonly GeraeteStatus[] = ["beantragt", "aktiv", "gesperrt", "widerrufen"];
const KENNUNG = /^pc-[a-z0-9]+(?:-[a-z0-9]+)*$/;
const FINGERABDRUCK = /^SHA256:[A-Za-z0-9+/]{43}$/;
const GRUND_MIN = 3;
export const GRUND_MAX = 200;

export const istKennung = (v: unknown): v is string => typeof v === "string" && v.length <= 23 && KENNUNG.test(v);

// ---------- Prüfung der Antworten ----------
type Objekt = Record<string, unknown>;
const istObjekt = (v: unknown): v is Objekt => typeof v === "object" && v !== null && !Array.isArray(v);
const istStatus = (v: unknown): v is GeraeteStatus => typeof v === "string" && (STATUS as readonly string[]).includes(v);

/** Steuer-, Bidi- und Zero-Width-Zeichen (wie voice-core/geraete_admin_api.py: STEUER_RE) */
const STEUER_MUSTER = "[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]";
const STEUER = new RegExp(STEUER_MUSTER, "g");
// im Grund sind Tab und Zeilenumbruch erlaubt (mehrzeiliges Feld); sie werden zu Leerzeichen
const HAT_STEUER = new RegExp("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]");

/** Text zur Anzeige: Steuerzeichen werden zu Leerzeichen, der Rest gekürzt; leer ergibt null. */
export function sauber(v: unknown, grenze: number): string | null {
  if (typeof v !== "string") return null;
  const t = Array.from(v.replace(STEUER, " ").replace(/\s+/g, " ").trim()).slice(0, grenze).join("");
  return t || null;
}

function zeitpunkt(v: unknown): string | null {
  return typeof v === "string" && v.length <= 40 && !Number.isNaN(Date.parse(v)) ? v : null;
}

function alsEreignisse(roh: unknown): Ereignis[] {
  if (!Array.isArray(roh)) return [];
  return roh.slice(0, 10).flatMap((e): Ereignis[] =>
    istObjekt(e)
      ? [
          {
            von: istStatus(e.von) ? e.von : null,
            nach: istStatus(e.nach) ? e.nach : null,
            durch: sauber(e.durch, 100),
            grund: sauber(e.grund, GRUND_MAX),
            at: zeitpunkt(e.at),
          },
        ]
      : [],
  );
}

/** Antwort von GET /geraete/verwaltung. Zeilen, die nicht passen, fallen weg; der Rest bleibt benutzbar. */
export function alsGeraete(roh: unknown): Geraet[] {
  if (!Array.isArray(roh)) return [];
  return roh.slice(0, 200).flatMap((v): Geraet[] => {
    if (!istObjekt(v) || !istKennung(v.kennung) || !istStatus(v.status)) return [];
    const profil = istObjekt(v.profil) ? v.profil : {};
    return [
      {
        kennung: v.kennung,
        name: sauber(v.name, 100) ?? v.kennung,
        rechnername: sauber(v.rechnername, 63),
        profilName: sauber(profil.name, 100),
        profilAktiv: profil.aktiv === true,
        status: v.status,
        bindung: v.bindung === "automatisch" ? "automatisch" : "admin",
        fingerabdruck: typeof v.fingerabdruck === "string" && FINGERABDRUCK.test(v.fingerabdruck) ? v.fingerabdruck : null,
        erstellt: zeitpunkt(v.erstellt),
        freigegeben: zeitpunkt(v.freigegeben),
        gesperrt: zeitpunkt(v.gesperrt),
        grund: sauber(v.grund, GRUND_MAX),
        zuletztGesehen: zeitpunkt(v.zuletzt_gesehen),
        ereignisse: alsEreignisse(v.ereignisse),
        eigen: v.eigen === true,
      },
    ];
  });
}

// ---------- Sortierung und Anzeige ----------
export const STATUS_INFO: Record<GeraeteStatus, { wort: string; form: "raute" | "kreis" | "quadrat" | "kreuz"; erklaerung: string }> = {
  beantragt: { wort: "Wartet auf Freigabe", form: "raute", erklaerung: "Das Gerät hat sich gemeldet und hat noch keinen Zugang." },
  aktiv: { wort: "Aktiv", form: "kreis", erklaerung: "Das Gerät hat Zugang zu W.A.I.S." },
  gesperrt: { wort: "Gesperrt", form: "quadrat", erklaerung: "Der Zugang ruht. Ein Admin kann ihn wieder freigeben." },
  widerrufen: { wort: "Widerrufen", form: "kreuz", erklaerung: "Endgültig entzogen. Der Schlüssel dieses Geräts wird nicht mehr angenommen." },
};

const REIHENFOLGE: Record<GeraeteStatus, number> = { beantragt: 0, aktiv: 1, gesperrt: 2, widerrufen: 3 };

const zeitWert = (iso: string | null): number => (iso ? Date.parse(iso) : 0);

/**
 * Wartende Geräte zuerst (das älteste Gesuch ganz oben), dann aktive, gesperrte und widerrufene, jeweils nach Profil und
 * Gerätename. Die Eingabe bleibt unverändert.
 */
export function sortiere(liste: readonly Geraet[]): Geraet[] {
  return [...liste].sort((a, b) => {
    const nachStatus = REIHENFOLGE[a.status] - REIHENFOLGE[b.status];
    if (nachStatus) return nachStatus;
    if (a.status === "beantragt") {
      const nachAlter = zeitWert(a.erstellt) - zeitWert(b.erstellt);
      if (nachAlter) return nachAlter;
    }
    return (
      (a.profilName ?? "").localeCompare(b.profilName ?? "", "de") ||
      a.name.localeCompare(b.name, "de") ||
      a.kennung.localeCompare(b.kennung, "de")
    );
  });
}

export function wartende(liste: readonly Geraet[]): Geraet[] {
  return liste.filter((g) => g.status === "beantragt");
}

/** Fingerabdruck in Vierergruppen: "SHA256:" und 11 Gruppen (zehn mit vier Zeichen, die letzte mit drei). */
export function fingerabdruckGruppen(fp: string | null): { praefix: string; gruppen: string[] } | null {
  if (!fp || !FINGERABDRUCK.test(fp)) return null;
  const rest = fp.slice("SHA256:".length);
  return { praefix: "SHA256:", gruppen: rest.match(/.{1,4}/g) ?? [] };
}

/** Derselbe Fingerabdruck als ein Text mit Leerzeichen (zum Vorlesen und Vergleichen). */
export function fingerabdruckText(fp: string | null): string {
  const g = fingerabdruckGruppen(fp);
  return g ? `${g.praefix} ${g.gruppen.join(" ")}` : "unbekannt";
}

/** Datum und Uhrzeit auf Deutsch ("30.10.26, 08:00"); ungültig ergibt "unbekannt". timeZone nur für Tests. */
export function zeitText(iso: string | null, timeZone?: string): string {
  if (!iso || Number.isNaN(Date.parse(iso))) return "unbekannt";
  return `${new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short", ...(timeZone ? { timeZone } : {}) })} Uhr`;
}

/** Ein Eintrag des Verlaufs als ein Satz. */
export function ereignisText(e: Ereignis, timeZone?: string): string {
  const wann = e.at ? `${zeitText(e.at, timeZone)}: ` : "";
  const wer = e.durch ? ` von ${e.durch}` : "";
  let was: string;
  if (e.von === null) was = e.nach === "aktiv" ? "automatisch gebunden und aktiv" : "beantragt";
  else if (e.von === e.nach) was = "Rechnername geändert";
  else if (e.nach === "aktiv") was = e.von === "gesperrt" ? "entsperrt" : "freigegeben";
  else if (e.nach === "gesperrt") was = "gesperrt";
  else if (e.nach === "widerrufen") was = e.von === "beantragt" ? "abgelehnt" : "widerrufen";
  else was = "geändert";
  return `${wann}${was}${wer}${e.grund ? `, ${e.grund}` : ""}`;
}

/** Ist dies der Laptop, an dem die Oberfläche gerade läuft? kennung stammt aus dem Stand der Geräteeinrichtung. */
export function istDieserLaptop(g: Pick<Geraet, "kennung">, kennungHier: string | null): boolean {
  return kennungHier !== null && g.kennung === kennungHier;
}

// ---------- Aktionen und Bestätigung ----------
/** Welche Aktionen gibt es für dieses Gerät? (Die Datenbank lässt genau diese Übergänge zu.) */
export function aktionenFuer(g: Pick<Geraet, "status">): Aktion[] {
  switch (g.status) {
    case "beantragt":
      return ["freigeben", "widerrufen"];
    case "aktiv":
      return ["sperren", "widerrufen"];
    case "gesperrt":
      return ["entsperren", "widerrufen"];
    default:
      return [];
  }
}

/** Beschriftung des Knopfes an der Karte (kurz, nach dem Zustand: bei einem Antrag heißt Widerrufen Ablehnen). */
export function aktionKnopf(g: Pick<Geraet, "status">, a: Aktion): string {
  if (a === "widerrufen") return g.status === "beantragt" ? "Ablehnen" : "Widerrufen";
  return { freigeben: "Freigeben", sperren: "Sperren", entsperren: "Entsperren" }[a];
}

export type Bestaetigung = {
  aktion: Aktion;
  titel: string;
  knopf: string;
  beschreibung: string;
  /** der Fingerabdruck steht im Dialog (immer, damit klar ist, welches Gerät gemeint ist) */
  fingerabdruck: string | null;
  /** der Admin muss ausdrücklich bestätigen, dass er den Fingerabdruck am Laptop verglichen hat */
  vergleichNoetig: boolean;
  grundNoetig: boolean;
  /** Warnungen im Dialog, die nicht zu übersehen sind */
  warnungen: string[];
  endgueltig: boolean;
};

const VERGLEICH_HINWEIS = "Vergleiche ihn mit dem Fingerabdruck am Laptop.";
export { VERGLEICH_HINWEIS };

function besitzer(g: Geraet): string {
  return g.profilName ?? "unbekanntes Profil";
}

/** Alles, was der Dialog zu dieser Aktion zeigen und verlangen muss. Reine Funktion: dieselbe Eingabe, dieselbe Regel. */
export function bestaetigungFuer(g: Geraet, aktion: Aktion, opts: { dieserLaptop: boolean }): Bestaetigung {
  const warnungen: string[] = [];
  const aendert = aktion === "sperren" || aktion === "widerrufen";
  if (aktion === "freigeben" && g.eigen) {
    warnungen.push(
      "Das ist ein Gerät deines eigenen Profils. Gib es nur frei, wenn du den Fingerabdruck am Laptop selbst abgelesen hast.",
    );
  }
  if (aendert && opts.dieserLaptop) {
    warnungen.push("Das ist der Laptop, an dem du gerade arbeitest. Danach verliert W.A.I.S. die Verbindung zu diesem PC.");
  } else if (aendert && g.eigen) {
    warnungen.push("Das ist eines deiner eigenen Geräte.");
  }
  if ((aktion === "freigeben" || aktion === "entsperren") && !g.profilAktiv) {
    warnungen.push(`Das Profil von ${besitzer(g)} ist deaktiviert. Das Gerät lässt sich erst freigeben, wenn das Profil wieder aktiv ist.`);
  }
  const vergleich = aktion === "freigeben" || aktion === "entsperren";
  const basis = { aktion, fingerabdruck: g.fingerabdruck, vergleichNoetig: vergleich, grundNoetig: aendert, warnungen };
  switch (aktion) {
    case "freigeben":
      return {
        ...basis,
        titel: "Gerät freigeben",
        knopf: "Freigeben",
        beschreibung: `${besitzer(g)} bekommt mit ${g.name} (${g.kennung}) Zugang zu W.A.I.S. ${VERGLEICH_HINWEIS}`,
        endgueltig: false,
      };
    case "entsperren":
      return {
        ...basis,
        titel: "Gerät entsperren",
        knopf: "Entsperren",
        beschreibung: `${g.name} (${g.kennung}) von ${besitzer(g)} bekommt den Zugang zurück. ${VERGLEICH_HINWEIS}`,
        endgueltig: false,
      };
    case "sperren":
      return {
        ...basis,
        titel: "Gerät sperren",
        knopf: "Sperren",
        beschreibung: `${g.name} (${g.kennung}) verliert den Zugang sofort. Ein Admin kann ihn später wieder freigeben. ${besitzer(g)} bekommt eine Meldung mit dem Grund.`,
        endgueltig: false,
      };
    default:
      return g.status === "beantragt"
        ? {
            ...basis,
            titel: "Antrag ablehnen",
            knopf: "Ablehnen",
            beschreibung: `Der Antrag von ${besitzer(g)} für ${g.name} (${g.kennung}) wird abgelehnt. Der Schlüssel dieses Geräts wird danach nicht mehr angenommen.`,
            endgueltig: true,
          }
        : {
            ...basis,
            titel: "Freigabe widerrufen",
            knopf: "Endgültig widerrufen",
            beschreibung: `${g.name} (${g.kennung}) verliert den Zugang endgültig. Das lässt sich nicht zurücknehmen, ein neues Gerät braucht eine neue Bindung.`,
            endgueltig: true,
          };
  }
}

export type Eingabe = { grund: string; verglichen: boolean; code: string };

export type Pruefung = { ok: true; grund: string | null; code: string } | { ok: false; text: string; feld: "verglichen" | "grund" | "code" };

/** Grund bereinigt oder ein Fehlertext. Gilt auch für die Vorab-Anzeige im Dialog. */
export function grundPruefen(roh: string, noetig: boolean): { ok: true; wert: string | null } | { ok: false; text: string } {
  if (HAT_STEUER.test(roh)) {
    return { ok: false, text: "Der Grund darf keine Steuerzeichen enthalten." };
  }
  const wert = roh.replace(/\s+/g, " ").trim();
  if (wert.length > GRUND_MAX) return { ok: false, text: `Der Grund ist zu lang (höchstens ${GRUND_MAX} Zeichen).` };
  if (noetig && wert.length < GRUND_MIN) return { ok: false, text: `Bitte einen Grund angeben (mindestens ${GRUND_MIN} Zeichen).` };
  return { ok: true, wert: wert || null };
}

/** Code der Authenticator-App: sechs Ziffern, Leerzeichen dazwischen sind erlaubt. */
export function codePruefen(roh: string): string | null {
  const code = roh.replace(/\s/g, "");
  return /^\d{6}$/.test(code) ? code : null;
}

/**
 * Darf der Dialog absenden? Reihenfolge der Meldungen: erst der Vergleich des Fingerabdrucks, dann der Grund, dann der Code.
 * Eine Freigabe ohne bestätigten Vergleich oder ohne sichtbaren Fingerabdruck geht nie durch.
 */
export function eingabePruefen(b: Bestaetigung, e: Eingabe): Pruefung {
  if (b.vergleichNoetig) {
    if (!b.fingerabdruck) {
      return { ok: false, feld: "verglichen", text: "Der Fingerabdruck dieses Geräts ist nicht bekannt. Bitte die Liste neu laden." };
    }
    if (!e.verglichen) {
      return { ok: false, feld: "verglichen", text: "Bitte bestätige, dass du den Fingerabdruck mit dem Laptop verglichen hast." };
    }
  }
  const grund = grundPruefen(e.grund, b.grundNoetig);
  if (!grund.ok) return { ok: false, feld: "grund", text: grund.text };
  const code = codePruefen(e.code);
  if (!code) return { ok: false, feld: "code", text: "Bitte den sechsstelligen Code aus der Authenticator-App eingeben." };
  return { ok: true, grund: grund.wert, code };
}

// ---------- Meldungen ----------
export type Ergebnis = "ok" | "schon";

/** Erfolgsmeldung mit dem Namen des Geräts. */
export function erfolgText(g: Pick<Geraet, "name" | "kennung" | "status">, aktion: Aktion, ergebnis: Ergebnis): string {
  const wer = `${g.name} (${g.kennung})`;
  if (ergebnis === "schon") {
    const zustand = { freigeben: "freigegeben", entsperren: "wieder freigegeben", sperren: "gesperrt", widerrufen: "widerrufen" }[aktion];
    return `${wer} ist schon ${zustand}. Es wurde nichts geändert.`;
  }
  switch (aktion) {
    case "freigeben":
      return `${wer} ist freigegeben.`;
    case "entsperren":
      return `${wer} ist wieder freigegeben.`;
    case "sperren":
      return `${wer} ist gesperrt.`;
    default:
      return g.status === "beantragt" ? `Der Antrag für ${wer} ist abgelehnt.` : `Die Freigabe von ${wer} ist widerrufen.`;
  }
}

export type FehlerBehandlung = {
  /** Text für die Person, konkret und ohne interne Details */
  text: string;
  /** der Dialog bleibt offen, weil sich der Fehler dort beheben lässt (Code, Grund) */
  dialogOffen: boolean;
  /** die Liste neu laden, weil der Stand unklar oder inzwischen anders ist */
  neuLaden: boolean;
  /** Feld, das den Fokus bekommt, wenn der Dialog offen bleibt */
  feld: "code" | "grund" | null;
};

export const CODE_ZU_ALT = "zweiter_faktor_zu_alt";

/**
 * Was tun bei einem Fehler der Aktion? status 0 heißt: keine Antwort bekommen. Dann ist offen, ob die Aktion gelaufen ist;
 * die Liste zeigt es. Bei 400 und einem alten Code bleibt der Dialog offen, damit die Eingabe nicht verloren geht.
 */
export function fehlerBehandlung(f: { status: number; text: string; code?: string | null }): FehlerBehandlung {
  const text = sauber(f.text, 240) ?? "Unbekannter Fehler.";
  if (f.status === 403 && f.code === CODE_ZU_ALT) {
    return { text, dialogOffen: true, neuLaden: false, feld: "code" };
  }
  if (f.status === 400) return { text, dialogOffen: true, neuLaden: false, feld: text.includes("Grund") ? "grund" : null };
  if (f.status === 0 || f.status >= 500) {
    const satz = /[.!?]$/.test(text) ? text : `${text}.`;
    return { text: `${satz} Ob die Aktion gelaufen ist, zeigt die Liste. Bitte prüfe sie.`, dialogOffen: false, neuLaden: true, feld: null };
  }
  if (f.status === 404 || f.status === 409) return { text, dialogOffen: false, neuLaden: true, feld: null };
  return { text, dialogOffen: false, neuLaden: false, feld: null };
}

// ---------- Hinweise (Anzeige) ----------
/** Hinweise der Datenbank (Migration 0002), die zur Geräteverwaltung führen. */
export const GERAETE_HINWEIS_ARTEN: readonly string[] = ["geraet_beantragt", "geraet_gebunden", "geraet_konflikt"];

export const istGeraeteHinweis = (art: string): boolean => GERAETE_HINWEIS_ARTEN.includes(art);

export const KARTE_PFAD = "Einstellungen, Geräte und PC, Karte Geräteverwaltung";

/**
 * Die Hinweistexte aus Migration 0002 nennen ein Skript, das es nicht gibt ("Freigeben: wamo_geraete.py freigeben
 * --kennung ..."). Die Datenbank bleibt unverändert (Migrationen sind nach dem Anwenden unveränderlich); angezeigt wird
 * stattdessen der Weg über die Karte Geräteverwaltung.
 */
export function hinweisTextAnzeige(text: string): string {
  return text
    .replace(/Freigeben: wamo_geraete\.py freigeben --kennung [A-Za-z0-9-]+\.?/g, `Freigeben oder ablehnen: ${KARTE_PFAD}.`)
    .replace(/Sperren: wamo_geraete\.py sperren --kennung [A-Za-z0-9-]+\.?/g, `Sperren: ${KARTE_PFAD}.`)
    .replace(/wamo_geraete\.py [a-z]+ --kennung [A-Za-z0-9-]+\.?/g, `${KARTE_PFAD}.`);
}
