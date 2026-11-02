/**
 * Reine Logik der Karte "Änderungen" (Selbstüberarbeitung, Schritt 1.5; components/einstellungen/Aenderungen.tsx): was der Kern von
 * GET /wamo/api/release/status liefert (voice-core/release_client.ansicht), wie die Oberfläche es streng prüft und welche Texte und Zeilen
 * daraus werden. Ohne React und ohne Browser, damit sich alles mit node:test prüfen lässt (aenderungen.test.ts).
 *
 * Die Karte zeigt den Stand, sie ändert nichts: Anfragen vorschlagen kommt mit den Werkzeugen des Kerns (Schritt 3.4), Freigeben geht allein über
 * den Freigabe-Dienst des Ankers (POST /wamo/release/<id>/freigeben, Schritt 1.4c) und braucht einen frischen zweiten Faktor. Wo der Release-Dienst
 * noch nicht kann, was die Karte später zeigen soll (Diff, Wirkungsflächen, Versionsvorschlag), steht das ehrlich da, nicht als leere Fläche.
 */

export type SperreZustand = "an" | "aus" | "unbekannt" | "defekt";
export type Quelle = "selbst" | "main";

export type Anfrage = {
  id: string;
  status: string | null;
  quelle: Quelle | null;
  schritt: number | null;
  schritte: number | null;
  seit: string | null;
  frist_bis: string | null;
  versuch: number | null;
  fertig: boolean;
  version: string | null;
  klasse: string | null;
};

export type Abgeschlossen = { id: string; status: string | null; grund: string | null };

export type Stand = {
  verfuegbar: true;
  modus: string;
  version: string | null;
  bereit: boolean;
  defekt: string | null;
  angehalten: string | null;
  anker: { erreichbar: boolean; sperre: SperreZustand };
  datenbank: "angeschlossen" | "fehlt" | "nicht_erreichbar" | "unbekannt";
  arbeiter: "frei" | "beschaeftigt" | "haengt" | "unbekannt";
  audit_kette: "ok" | "defekt";
  installation: "ok" | "abweichung";
  anfrage: Anfrage | null;
  abgeschlossen: Abgeschlossen[];
  zeit: string | null;
};

export type Nichts = { verfuegbar: false; grund: string };
export type Status = Stand | Nichts;

const WORT = /^[a-z][a-z0-9_]{0,39}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const VERSION = /^\d{1,6}\.\d{1,6}\.\d{1,6}$/;

const istObjekt = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);
const wortOderNull = (x: unknown): string | null => (typeof x === "string" && WORT.test(x) ? x : null);
const textOderNull = (x: unknown, n = 80): string | null =>
  typeof x === "string" && x.length > 0 ? x.replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, n) : null;
const zahlOderNull = (x: unknown, max = 100): number | null => (typeof x === "number" && Number.isInteger(x) && x >= 0 && x <= max ? x : null);
const eines = <T extends string>(x: unknown, erlaubt: readonly T[], standard: T): T =>
  typeof x === "string" && (erlaubt as readonly string[]).includes(x) ? (x as T) : standard;

function alsAnfrage(x: unknown): Anfrage | null {
  if (!istObjekt(x) || typeof x.id !== "string" || !UUID.test(x.id)) return null;
  return {
    id: x.id,
    status: wortOderNull(x.status),
    quelle: x.quelle === "selbst" || x.quelle === "main" ? x.quelle : null,
    schritt: zahlOderNull(x.schritt, 40),
    schritte: zahlOderNull(x.schritte, 40),
    seit: textOderNull(x.seit, 40),
    frist_bis: textOderNull(x.frist_bis, 40),
    versuch: zahlOderNull(x.versuch),
    fertig: x.fertig === true,
    version: typeof x.version === "string" && VERSION.test(x.version) ? x.version : null,
    klasse: wortOderNull(x.klasse),
  };
}

/** Prüft die Antwort des Kerns streng. Unbekanntes oder Falsches ergibt null (die Oberfläche zeigt dann "unerwartete Antwort"). */
export function alsStatus(roh: unknown): Status | null {
  if (!istObjekt(roh)) return null;
  if (roh.verfuegbar === false) return { verfuegbar: false, grund: wortOderNull(roh.grund) ?? "unbekannt" };
  if (roh.verfuegbar !== true) return null;
  const anker = istObjekt(roh.anker) ? roh.anker : {};
  const liste = Array.isArray(roh.abgeschlossen) ? roh.abgeschlossen : [];
  const abgeschlossen: Abgeschlossen[] = [];
  for (const e of liste) {
    if (istObjekt(e) && typeof e.id === "string" && UUID.test(e.id)) {
      abgeschlossen.push({ id: e.id, status: wortOderNull(e.status), grund: wortOderNull(e.grund) });
    }
  }
  return {
    verfuegbar: true,
    modus: wortOderNull(roh.modus) ?? "unbekannt",
    version: textOderNull(roh.version, 20),
    bereit: roh.bereit === true,
    defekt: textOderNull(roh.defekt),
    angehalten: textOderNull(roh.angehalten),
    anker: { erreichbar: anker.erreichbar === true, sperre: eines(anker.sperre, ["an", "aus", "unbekannt", "defekt"] as const, "unbekannt") },
    datenbank: eines(roh.datenbank, ["angeschlossen", "fehlt", "nicht_erreichbar", "unbekannt"] as const, "unbekannt"),
    arbeiter: eines(roh.arbeiter, ["frei", "beschaeftigt", "haengt", "unbekannt"] as const, "unbekannt"),
    audit_kette: roh.audit_kette === "ok" ? "ok" : "defekt",
    installation: roh.installation === "ok" ? "ok" : "abweichung",
    anfrage: alsAnfrage(roh.anfrage),
    abgeschlossen: abgeschlossen.slice(-5),
    zeit: textOderNull(roh.zeit, 40),
  };
}

// ------------------------------------------------------------------------------------------------ Schritte
/** Die Zustände einer Anfrage mit Namen für Menschen (Weg: infra/sokrates2/release/release_automat.WEG). */
export const SCHRITT_TITEL: Record<string, string> = {
  angefragt: "Angefragt",
  arbeit: "In Arbeit (Agent)",
  uebernommen: "Übernommen",
  pruefung1: "Prüfung 1 (Bauen, Tests, Lint, Scans)",
  review: "Review",
  refactor: "Refactor",
  pruefung2: "Prüfung 2",
  versionswahl: "Versionswahl",
  pruefung3: "Prüfung 3 (Schutzschicht)",
  wartet_freigabe: "Wartet auf Freigabe",
  freigegeben: "Freigegeben",
  veroeffentlicht: "Veröffentlicht",
  bereitgestellt: "Bereitgestellt",
  umgeschaltet: "Umgeschaltet",
  bewaehrt: "Bewährt",
  zurueckgenommen: "Zurückgenommen",
  abgebrochen: "Abgebrochen",
};

export const WEG: Record<Quelle, readonly string[]> = {
  main: ["angefragt", "uebernommen", "pruefung1", "versionswahl", "pruefung3", "wartet_freigabe", "freigegeben", "veroeffentlicht", "bereitgestellt", "umgeschaltet", "bewaehrt"],
  selbst: [
    "angefragt", "arbeit", "uebernommen", "pruefung1", "review", "refactor", "pruefung2", "versionswahl", "pruefung3", "wartet_freigabe", "freigegeben",
    "veroeffentlicht", "bereitgestellt", "umgeschaltet", "bewaehrt",
  ],
};

export type SchrittZeile = { name: string; titel: string; zustand: "fertig" | "aktuell" | "offen" };

/** Der Weg der Anfrage mit dem Stand: Schritte davor fertig, der aktuelle, danach offen. Ein Zustand außerhalb des Weges ergibt eine leere Liste. */
export function schrittZeilen(a: Anfrage | null): SchrittZeile[] {
  if (!a || !a.quelle || !a.status) return [];
  const weg = WEG[a.quelle];
  const hier = weg.indexOf(a.status);
  if (hier < 0) return [];
  return weg.map((name, i) => ({ name, titel: SCHRITT_TITEL[name] ?? name, zustand: i < hier ? "fertig" : i === hier ? "aktuell" : "offen" }));
}

// ------------------------------------------------------------------------------------------------ Lage und Texte
export type Lage = "nicht_erreichbar" | "stoerung" | "gesperrt" | "wartet" | "arbeit" | "bereit";

export type Ansicht = {
  lage: Lage;
  /** ein Satz, der die Lage sagt */
  lageText: string;
  /** zusätzliche Hinweise, jeder ein Satz */
  hinweise: string[];
  /** Beschriftungsliste "Dienste" */
  zeilen: { name: string; wert: string }[];
  anfrage: { id: string; kurz: string; quelleText: string; versionText: string | null; fristText: string | null; schritte: SchrittZeile[] } | null;
  /** Die Anfrage wartet auf eine Freigabe (die Freigabe selbst folgt, sobald der Release-Dienst Diff und Versionsvorschlag liefert) */
  wartetAufFreigabe: boolean;
};

export const NICHT_ERREICHBAR_TEXT: Record<string, string> = {
  nicht_verfuegbar: "Der Release-Dienst ist nicht erreichbar.",
  kein_zugang: "Der Kern darf den Release-Dienst nicht erreichen (Rechte des Sockets).",
  keine_antwort: "Der Release-Dienst antwortet nicht.",
  antwort_ungueltig: "Der Release-Dienst hat unklar geantwortet.",
  abgelehnt: "Der Release-Dienst hat die Abfrage abgelehnt.",
};

const SPERRE_TEXT: Record<SperreZustand, string> = {
  an: "gesetzt (Selbständerung gesperrt)",
  aus: "aufgehoben",
  unbekannt: "unbekannt (Notanker nicht erreichbar)",
  defekt: "defekt (gilt als gesetzt)",
};
const DATENBANK_TEXT = { angeschlossen: "angeschlossen", fehlt: "fehlt", nicht_erreichbar: "nicht erreichbar", unbekannt: "unbekannt" } as const;
const ARBEITER_TEXT = { frei: "frei", beschaeftigt: "beschäftigt", haengt: "hängt", unbekannt: "unbekannt" } as const;
const QUELLE_TEXT: Record<Quelle, string> = { selbst: "WAMO selbst (Auftrag an den Agenten)", main: "main (von Hand gemergte Änderung)" };

export function ansicht(status: Status | null, fehler: string | null = null): Ansicht {
  const leer = { zeilen: [], anfrage: null, wartetAufFreigabe: false, hinweise: [] as string[] };
  if (status === null) {
    return { lage: "nicht_erreichbar", lageText: fehler ?? "Die Antwort des Kerns war unerwartet.", ...leer };
  }
  if (!status.verfuegbar) {
    return { lage: "nicht_erreichbar", lageText: NICHT_ERREICHBAR_TEXT[status.grund] ?? "Der Release-Dienst ist nicht erreichbar.", ...leer };
  }
  const s = status;
  const a = s.anfrage;
  const hinweise: string[] = [];
  if (s.modus === "geruest") {
    hinweise.push("Der Release-Dienst läuft im Gerüstmodus: Die Stufen sind noch nicht alle gefüllt, eine Anfrage käme nicht bis zum Ende.");
  }
  if (s.angehalten) hinweise.push(`Der Dienst ist angehalten: ${s.angehalten}.`);
  if (s.installation === "abweichung") hinweise.push("Die installierten Dateien des Release-Dienstes weichen von der Prüfsumme ab.");
  if (a) hinweise.push("Diff, Wirkungsflächen und Versionsvorschlag liefert der Release-Dienst noch nicht, die Freigabe folgt, sobald sie da sind.");
  const zeilen = [
    { name: "Release-Dienst", wert: `läuft${s.version ? `, Version ${s.version}` : ""}${s.bereit ? "" : ", nicht bereit"}` },
    { name: "Sperre des Notankers", wert: SPERRE_TEXT[s.anker.sperre] },
    { name: "Datenbank", wert: DATENBANK_TEXT[s.datenbank] },
    { name: "Arbeiter", wert: ARBEITER_TEXT[s.arbeiter] },
    { name: "Protokoll (Hashkette)", wert: s.audit_kette === "ok" ? "in Ordnung" : "defekt" },
  ];
  const anfrage = a
    ? {
        id: a.id,
        kurz: a.id.slice(0, 8),
        quelleText: a.quelle ? QUELLE_TEXT[a.quelle] : "unbekannt",
        versionText: a.version,
        fristText: a.frist_bis,
        schritte: schrittZeilen(a),
      }
    : null;
  const wartet = a?.status === "wartet_freigabe";
  let lage: Lage;
  let lageText: string;
  if (s.defekt || s.audit_kette === "defekt") {
    lage = "stoerung";
    lageText = s.defekt ? `Der Release-Dienst meldet einen Defekt: ${s.defekt}.` : "Das Protokoll des Release-Dienstes ist defekt, es beginnt nichts Neues.";
  } else if (s.anker.sperre !== "aus") {
    lage = "gesperrt";
    lageText =
      s.anker.sperre === "unbekannt"
        ? "Der Notanker ist nicht erreichbar. Es beginnt nichts Neues."
        : "Selbständerung ist gesperrt. Neue Änderungen beginnen nicht, bis ein Mensch die Sperre am Notanker aufhebt.";
  } else if (wartet) {
    lage = "wartet";
    lageText = "Eine Änderung wartet auf deine Freigabe.";
  } else if (a) {
    const schritt = a.schritt !== null && a.schritte !== null ? `Schritt ${a.schritt + 1} von ${a.schritte}` : "";
    lage = "arbeit";
    lageText = `Eine Änderung ist in Arbeit${schritt ? `: ${schritt}` : ""}${a.status ? ` (${SCHRITT_TITEL[a.status] ?? a.status})` : ""}.`;
  } else {
    lage = "bereit";
    lageText = "Keine offene Änderung.";
  }
  return { lage, lageText, hinweise, zeilen, anfrage, wartetAufFreigabe: wartet && lage === "wartet" };
}

/** Abgeschlossene Anfragen als Zeilen für die Liste "Zuletzt". */
export function abgeschlossenText(e: Abgeschlossen): string {
  const status = e.status ? (SCHRITT_TITEL[e.status] ?? e.status) : "unbekannt";
  return `${e.id.slice(0, 8)}: ${status}${e.grund ? ` (Grund: ${e.grund})` : ""}`;
}
