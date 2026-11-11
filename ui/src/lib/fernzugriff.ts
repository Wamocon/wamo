import { codePruefen, CODE_ZU_ALT, sauber } from "./geraeteVerwaltung.ts";

/**
 * Reine Logik des Fernzugriffs (components/einstellungen/Fernzugriff.tsx), ohne React und ohne Browser, damit sie sich mit
 * node:test prüfen lässt (fernzugriff.test.ts). Die Daten kommen vom Kern (voice-core/rd_api.py) und werden hier streng geprüft.
 *
 * Grundregeln: Die Oberfläche entscheidet nichts. Sie zeigt die Ziele, fragt den Code der Authenticator-App nur ab, wenn der Kern ihn
 * verlangt (403 mit zweiter_faktor_zu_alt), und öffnet die Adresse, die der Kern liefert. Zugangsdaten, Zielhost und Konto kennt sie nie; die Adresse der Gateway-Seite trägt nur das
 * Sitzungstoken des Gateways. Maßgeblich bleibt der Kern (Rolle admin, Code der Anmeldung, ein Zuschauer je Ziel).
 */

export type ZielStatus = "frei" | "belegt" | "nicht_erreichbar";

export type Ziel = {
  name: string;
  anzeige: string;
  status: ZielStatus;
  /** die Sitzung gehört dem Profil, mit dem diese Sitzung angemeldet ist */
  eigene: boolean;
  belegtVon: string | null;
  seit: string | null;
};

export type MeineSitzung = { sitzung: string; ziel: string; anzeige: string; start: string | null; endet: string };

export type Uebersicht = {
  /** der Kern hat den Fernzugriff eingeschaltet */
  aktiv: boolean;
  ziele: Ziel[];
  meine: MeineSitzung | null;
  maxMinuten: number;
  leerlaufMinuten: number;
};

export type Start = {
  sitzung: string;
  ziel: string;
  anzeige: string;
  url: string;
  endet: string;
  maxMinuten: number;
  leerlaufMinuten: number;
};

const ZIEL_NAME = /^[a-z0-9]{1,16}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LOOPBACK = new Set(["localhost", "127.0.0.1"]);
const STANDARD_MAX_MINUTEN = 60;
const STANDARD_LEERLAUF_MINUTEN = 10;

type Objekt = Record<string, unknown>;
const istObjekt = (v: unknown): v is Objekt => typeof v === "object" && v !== null && !Array.isArray(v);
const istStatus = (v: unknown): v is ZielStatus => v === "frei" || v === "belegt" || v === "nicht_erreichbar";
const istZielName = (v: unknown): v is string => typeof v === "string" && ZIEL_NAME.test(v);
const istSitzung = (v: unknown): v is string => typeof v === "string" && UUID.test(v);

function zeitpunkt(v: unknown): string | null {
  return typeof v === "string" && v.length <= 40 && !Number.isNaN(Date.parse(v)) ? v : null;
}

function minuten(v: unknown, standard: number): number {
  return typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= 240 ? v : standard;
}

// ---------- Prüfung der Antworten ----------
/** Antwort von GET /rd/ziele. Ziele, die nicht passen, fallen weg; nichts Unbekanntes wird angezeigt. null: unbrauchbare Antwort. */
export function alsUebersicht(roh: unknown): Uebersicht | null {
  if (!istObjekt(roh) || typeof roh.aktiv !== "boolean" || !Array.isArray(roh.ziele)) return null;
  const gesehen = new Set<string>();
  const ziele = roh.ziele.slice(0, 16).flatMap((v): Ziel[] => {
    if (!istObjekt(v) || !istZielName(v.name) || !istStatus(v.status) || gesehen.has(v.name)) return [];
    gesehen.add(v.name);
    return [
      {
        name: v.name,
        anzeige: sauber(v.anzeige, 40) ?? v.name,
        status: v.status,
        eigene: v.eigene === true,
        belegtVon: sauber(v.belegt_von, 60),
        seit: zeitpunkt(v.seit),
      },
    ];
  });
  const m = istObjekt(roh.meine) ? roh.meine : null;
  const meine: MeineSitzung | null =
    m && istSitzung(m.sitzung) && istZielName(m.ziel) && zeitpunkt(m.endet)
      ? { sitzung: m.sitzung, ziel: m.ziel, anzeige: sauber(m.anzeige, 40) ?? m.ziel, start: zeitpunkt(m.start), endet: m.endet as string }
      : null;
  return {
    aktiv: roh.aktiv,
    ziele,
    meine,
    maxMinuten: minuten(roh.max_minuten, STANDARD_MAX_MINUTEN),
    leerlaufMinuten: minuten(roh.leerlauf_minuten, STANDARD_LEERLAUF_MINUTEN),
  };
}

/**
 * Ist das eine Adresse, die die Oberfläche öffnen darf? Nur https (oder http über Loopback für Proben), ohne Zugangsdaten in der
 * Adresse, mit dem Sitzungstoken als Abfrage und der Verbindung der Guacamole-Seite im Fragment (/?token=...#/client/...). Sie muss von einer anderen Herkunft kommen als die Oberfläche selbst,
 * damit die Seite des Gateways nie im Speicher der Oberfläche liest (herkunft: Herkunft der Oberfläche).
 */
export function seitenUrlPruefen(roh: unknown, herkunft?: string): string | null {
  if (typeof roh !== "string" || roh.length > 600 || /[\s\u0000-\u001f]/.test(roh)) return null;
  let u: URL;
  try {
    u = new URL(roh);
  } catch {
    return null;
  }
  const sicher = u.protocol === "https:" || (u.protocol === "http:" && LOOPBACK.has(u.hostname));
  if (!sicher || u.username || u.password || u.pathname !== "/") return null;
  if (!/^\?token=[A-Za-z0-9_-]{16,128}$/.test(u.search) || !/^#\/client\/[A-Za-z0-9]{8,80}$/.test(u.hash)) return null;
  if (herkunft && u.origin === herkunft) return null;
  return u.href;
}

/** Antwort von POST /rd/start. null: unbrauchbar (dann wird nichts geöffnet). */
export function alsStart(roh: unknown, herkunft?: string): Start | null {
  if (!istObjekt(roh) || roh.ok !== true || !istSitzung(roh.sitzung) || !istZielName(roh.ziel) || !zeitpunkt(roh.endet)) return null;
  const url = seitenUrlPruefen(roh.url, herkunft);
  if (!url) return null;
  return {
    sitzung: roh.sitzung,
    ziel: roh.ziel,
    anzeige: sauber(roh.anzeige, 40) ?? roh.ziel,
    url,
    endet: roh.endet as string,
    maxMinuten: minuten(roh.max_minuten, STANDARD_MAX_MINUTEN),
    leerlaufMinuten: minuten(roh.leerlauf_minuten, STANDARD_LEERLAUF_MINUTEN),
  };
}

// ---------- Anzeige der Ziele ----------
export const STATUS_INFO: Record<ZielStatus, { wort: string; form: "kreis" | "quadrat" | "kreuz"; erklaerung: string }> = {
  frei: { wort: "Frei", form: "kreis", erklaerung: "Das Ziel ist erreichbar und niemand ist verbunden." },
  belegt: { wort: "Belegt", form: "quadrat", erklaerung: "Eine Sitzung läuft. Es gibt einen Zuschauer je Ziel." },
  nicht_erreichbar: { wort: "Nicht erreichbar", form: "kreuz", erklaerung: "Das Gateway erreicht das Ziel gerade nicht oder es ist nicht eingerichtet." },
};

/** Uhrzeit "12:30 Uhr" aus einem Zeitpunkt; ungültig ergibt "unbekannt". timeZone nur für Tests. */
export function uhrzeit(iso: string | null, timeZone?: string): string {
  if (!iso || Number.isNaN(Date.parse(iso))) return "unbekannt";
  return `${new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", ...(timeZone ? { timeZone } : {}) })} Uhr`;
}

/** Ein Satz zum Zustand des Ziels, für die Karte und für Screenreader. */
export function zielZeile(z: Ziel, timeZone?: string): string {
  if (z.status === "belegt") {
    if (z.eigene) return `Du bist verbunden, seit ${uhrzeit(z.seit, timeZone)}.`;
    return `Belegt von ${z.belegtVon ?? "einer anderen Person"}${z.seit ? ` seit ${uhrzeit(z.seit, timeZone)}` : ""}.`;
  }
  return STATUS_INFO[z.status].erklaerung;
}

export type Knopf = { text: string; aktiv: boolean; grund: string | null };

/**
 * Beschriftung und Zustand des Knopfes am Ziel. "Verbinden" nur bei einem freien Ziel. Die eigene laufende Sitzung lässt sich
 * neu starten ("Neu verbinden"), das löst die alte ab. Belegt von anderen und nicht erreichbar: aus.
 */
export function verbindenKnopf(z: Ziel): Knopf {
  if (z.status === "frei") return { text: "Verbinden", aktiv: true, grund: null };
  if (z.status === "belegt" && z.eigene) return { text: "Neu verbinden", aktiv: true, grund: null };
  if (z.status === "belegt") return { text: "Belegt", aktiv: false, grund: "Das Ziel ist belegt. Bitte warte, bis die Sitzung endet." };
  return { text: "Nicht erreichbar", aktiv: false, grund: "Das Ziel ist gerade nicht erreichbar." };
}

/** Zuerst die eigene Sitzung, dann freie, dann belegte, zuletzt nicht erreichbare Ziele; sonst nach Anzeigename. */
export function sortiereZiele(liste: readonly Ziel[]): Ziel[] {
  const rang = (z: Ziel): number => (z.eigene ? 0 : z.status === "frei" ? 1 : z.status === "belegt" ? 2 : 3);
  return [...liste].sort((a, b) => rang(a) - rang(b) || a.anzeige.localeCompare(b.anzeige, "de") || a.name.localeCompare(b.name));
}

// ---------- Dialog ----------
/** Der Hinweistext im Dialog, bevor ein Code verlangt wird. Fest, ohne Werte aus dem Netz außer dem Anzeigenamen. */
export function hinweisZeilen(anzeige: string, maxMinuten: number, leerlaufMinuten: number): string[] {
  return [
    `Du steuerst den Desktop von ${anzeige}. Dort können Daten anderer Personen sichtbar sein.`,
    "Es wird nichts aufgezeichnet. Protokolliert werden nur, wer wann mit welchem Ziel verbunden war und wie lange.",
    `Die Sitzung endet nach ${maxMinuten} Minuten oder nach ${leerlaufMinuten} Minuten ohne Eingabe. Zwischenablage, Laufwerke und Ton sind aus.`,
  ];
}

/**
 * Einleitung der Karte (Einstellungen, Fernzugriff). Seit KP-2 startet "Verbinden" ohne Code: Der Code der Anmeldung genügt. Fragt der
 * Kern nach einem frischen Code (die Anmeldung ist älter als sein Höchstalter, WAMO_RD_ANMELDUNG_MAX_MIN), zeigt der Dialog das Codefeld.
 * Jeder Start geht als Hinweis an alle Admins (Datenbank, Migration 0019), damit ein fremder Start auffällt.
 */
export const EINLEITUNG =
  "Hier bedienst du unterwegs den Desktop von Sokrates-1, Sokrates-2 oder Caesar. Du startest eine Sitzung mit „Verbinden“, der Code von " +
  "deiner Anmeldung genügt. Ist deine Anmeldung zu alt, fragt der Dialog den Code deiner Authenticator-App ab. Jede Sitzung ist auf " +
  "eine Stunde begrenzt und hat einen Zuschauer je Ziel. Es wird nichts aufgezeichnet. Jeder Start wird allen Admins gemeldet.";

/** Hilfetext am Codefeld, das nur erscheint, wenn der Kern den Code verlangt. */
export const CODE_HILFE = "Der Code darf höchstens zwei Minuten alt sein.";

/**
 * Zustand des Dialogs: klick zeigt nur "Verbinden", code zusätzlich das Codefeld. Die Oberfläche entscheidet das nie selbst: Das
 * Codefeld kommt allein, wenn der Kern den Start mit 403 und zweiter_faktor_zu_alt ablehnt (startFehler, feld "code").
 */
export type DialogModus = "klick" | "code";

/** Modus nach einem Fehler beim Start. Ein einmal gezeigtes Codefeld bleibt bis zum Schließen des Dialogs. */
export function modusNachFehler(modus: DialogModus, f: FehlerBehandlung): DialogModus {
  return modus === "code" || f.feld === "code" ? "code" : "klick";
}

/**
 * Wohin der Fokus beim Öffnen des Dialogs geht: auf das Codefeld, sonst auf "Abbrechen". NIE auf "Verbinden": Ohne Codefeld war
 * früher das Feld die Schranke dagegen, dass ein Öffnen des Dialogs (auch durch WAMO, Werkzeug fernzugriff_oeffnen) schon eine Sitzung
 * startet. Jetzt braucht der Start einen echten Klick der Person (Befund M7).
 */
export const startFokus = (modus: DialogModus): "code" | "abbrechen" => (modus === "code" ? "code" : "abbrechen");

export type Pruefung = { ok: true; code: string } | { ok: false; text: string };

/** Darf der Dialog mit Codefeld absenden? Verlangt wird allein der sechsstellige Code der Authenticator-App. */
export function eingabePruefen(code: string): Pruefung {
  const c = codePruefen(code);
  return c ? { ok: true, code: c } : { ok: false, text: "Bitte den sechsstelligen Code aus der Authenticator-App eingeben." };
}

export type FehlerBehandlung = {
  /** Text für die Person, konkret und ohne interne Details */
  text: string;
  /** der Dialog bleibt offen, weil sich der Fehler dort beheben lässt (Code) */
  dialogOffen: boolean;
  /** die Liste neu laden, weil sich der Stand geändert hat (belegt, nicht erreichbar) */
  neuLaden: boolean;
  feld: "code" | null;
};

/** Was tun bei einem Fehler beim Start? status 0 heißt: keine Antwort bekommen. */
export function startFehler(f: { status: number; text: string; code?: string | null }): FehlerBehandlung {
  const text = sauber(f.text, 240) ?? "Unbekannter Fehler.";
  if (f.status === 403 && f.code === CODE_ZU_ALT) return { text, dialogOffen: true, neuLaden: false, feld: "code" };
  if (f.status === 404 || f.status === 409) return { text, dialogOffen: false, neuLaden: true, feld: null };
  if (f.status === 0 || f.status >= 500) {
    const satz = /[.!?]$/.test(text) ? text : `${text}.`;
    return { text: `${satz} Ob die Sitzung gestartet ist, zeigt die Liste.`, dialogOffen: false, neuLaden: true, feld: null };
  }
  return { text, dialogOffen: false, neuLaden: false, feld: null };
}

// ---------- Restzeit ----------
/** Verbleibende Sekunden bis zum Ende (nie negativ). Ungültiges Ende ergibt 0. */
export function restSekunden(endet: string | null, jetzt: number): number {
  if (!endet) return 0;
  const t = Date.parse(endet);
  return Number.isNaN(t) ? 0 : Math.max(0, Math.ceil((t - jetzt) / 1000));
}

/** "42:10" bis eine Stunde, darüber "1:05:00". */
export function restzeitText(sekunden: number): string {
  const s = Math.max(0, Math.floor(sekunden));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const zwei = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${zwei(m)}:${zwei(r)}` : `${zwei(m)}:${zwei(r)}`;
}

/** Dieselbe Zeit zum Vorlesen: "42 Minuten und 10 Sekunden". */
export function restzeitSprache(sekunden: number): string {
  const s = Math.max(0, Math.floor(sekunden));
  const m = Math.floor(s / 60);
  const r = s % 60;
  const teile = [m > 0 ? `${m} ${m === 1 ? "Minute" : "Minuten"}` : "", m < 10 && (r > 0 || m === 0) ? `${r} ${r === 1 ? "Sekunde" : "Sekunden"}` : ""];
  return teile.filter(Boolean).join(" und ") || "0 Sekunden";
}

/** Schwellen, bei denen die Restzeit angesagt wird (Sekunden). Dazwischen schweigt die Live-Region. */
export const ANSAGE_SCHWELLEN: readonly number[] = [600, 300, 120, 60];

/**
 * Meldung für die Live-Region, wenn die Restzeit zwischen zwei Prüfungen eine Schwelle unterschritten hat; sonst null.
 * Bei 0 meldet sie das Ende. vorher und jetzt in Sekunden.
 */
export function restzeitAnsage(vorher: number, jetzt: number): string | null {
  if (jetzt <= 0 && vorher > 0) return "Die Fernsitzung ist zu Ende.";
  for (const schwelle of [...ANSAGE_SCHWELLEN].reverse()) {
    if (vorher > schwelle && jetzt <= schwelle && jetzt > 0) {
      return schwelle >= 120 ? `Noch ${schwelle / 60} Minuten Fernsitzung.` : "Noch eine Minute Fernsitzung.";
    }
  }
  return null;
}

/** Wird die Restzeit knapp (höchstens zwei Minuten)? Dann hebt die Karte sie hervor. */
export const restzeitKnapp = (sekunden: number): boolean => sekunden > 0 && sekunden <= 120;

/** Erfolgsmeldung nach dem Start. geoeffnet: false, wenn der Browser das Fenster blockiert hat. */
export function startMeldung(anzeige: string, geoeffnet: boolean): string {
  return geoeffnet
    ? `Fernzugriff auf ${anzeige} gestartet. Der Desktop hat sich in einem neuen Fenster geöffnet.`
    : `Fernzugriff auf ${anzeige} gestartet. Der Browser hat das neue Fenster blockiert, bitte tippe auf "Fenster öffnen".`;
}

export const endeMeldung = (anzeige: string, bestaetigt: boolean): string =>
  bestaetigt
    ? `Die Fernsitzung mit ${anzeige} ist beendet.`
    : `Die Fernsitzung mit ${anzeige} ist beendet. Das Gateway hat es nicht bestätigt, die Sitzung läuft dort spätestens nach ${STANDARD_MAX_MINUTEN} Minuten aus.`;

