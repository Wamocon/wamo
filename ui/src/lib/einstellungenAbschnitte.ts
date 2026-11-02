import type { EinrichtungsZustand, TunnelZustand } from "./geraet.ts";
import type { Modellzugang, Rolle } from "./nachrichten.ts";

/**
 * Reine Logik der Einstellungsseite (components/einstellungen/): welche Abschnitte es gibt, wer sie sieht, wie der
 * Abschnitt in der Adresse steht (Hash) und welche Kurztexte die Oberfläche daraus macht. Ohne React und ohne Browser,
 * damit sie sich mit node:test prüfen lässt (einstellungenAbschnitte.test.ts).
 *
 * Die Seite ist keine eigene Route, sondern eine Ansicht der Hauptseite, erreichbar über den Hash
 * (#einstellungen, #einstellungen/modellzugang). Das funktioniert im Browser wie in der Desktop-App, ohne dass die
 * Seite neu geladen wird, und ein laufendes Gespräch bleibt unberührt. Im Hash steht nur eine Abschnittskennung aus der
 * festen Liste unten, nie ein Wert aus dem Formular.
 */

export const ABSCHNITT_IDS = [
  "konto",
  "modellzugang",
  "verbrauch",
  "sprache",
  "darstellung",
  "geraete",
  "fernzugriff",
  "aenderungen",
  "teilen",
  "info",
] as const;
export type AbschnittId = (typeof ABSCHNITT_IDS)[number];

export type Abschnitt = {
  id: AbschnittId;
  titel: string;
  /** eine Zeile unter dem Titel in der Gliederung */
  kurz: string;
  /** nur für Profile mit der Rolle admin (die Rolle kommt vom Kern, der Kern prüft sie noch einmal) */
  nurAdmin?: boolean;
};

export const ABSCHNITTE: readonly Abschnitt[] = [
  { id: "konto", titel: "Konto und Profil", kurz: "E-Mail, Rolle und Abmelden" },
  { id: "modellzugang", titel: "Modellzugang", kurz: "API-Schlüssel, Token und Modus" },
  { id: "verbrauch", titel: "Verbrauch", kurz: "Kontingent deines Claude-Abos", nurAdmin: true },
  { id: "sprache", titel: "Sprache und Ton", kurz: "Mikrofon, Hören und Stimme" },
  { id: "darstellung", titel: "Darstellung", kurz: "Normal, Fokus, Zentrale und Nacht" },
  { id: "geraete", titel: "Geräte und PC", kurz: "Verbindungen, Geräteverwaltung, PC-Steuerung und Betriebsart" },
  {
    id: "fernzugriff",
    titel: "Fernzugriff",
    kurz: "Desktop der Sparks unterwegs bedienen",
    nurAdmin: true,
  },
  {
    id: "aenderungen",
    titel: "Änderungen",
    kurz: "Stand der Selbstüberarbeitung von WAMO",
    nurAdmin: true,
  },
  { id: "teilen", titel: "Teilen", kurz: "Geteilte Dokumente und Dateien" },
  { id: "info", titel: "Info", kurz: "Version, Kern und Hinweise" },
];

/** Abschnitte, die diese Rolle sieht. Eine unbekannte Rolle (noch kein Profil vom Kern) zählt wie mitglied. */
export function abschnitteFuer({ rolle }: { rolle: Rolle | null }): readonly Abschnitt[] {
  return ABSCHNITTE.filter((a) => !a.nurAdmin || rolle === "admin");
}

export const abschnittTitel = (id: AbschnittId): string => ABSCHNITTE.find((a) => a.id === id)?.titel ?? "";

export function istAbschnittId(v: unknown): v is AbschnittId {
  return typeof v === "string" && (ABSCHNITT_IDS as readonly string[]).includes(v);
}

// ---------- Adresse (Hash) ----------
export const HASH_WURZEL = "einstellungen";

export type HashZiel = Readonly<{ offen: false; abschnitt: null } | { offen: true; abschnitt: AbschnittId | null }>;

const GESCHLOSSEN: HashZiel = Object.freeze({ offen: false, abschnitt: null });
const OFFEN_OHNE_ABSCHNITT: HashZiel = Object.freeze({ offen: true, abschnitt: null });

/**
 * Hash der Adresse (mit oder ohne #) als Ziel. "#einstellungen" öffnet die Seite ohne Abschnitt,
 * "#einstellungen/modellzugang" mit Abschnitt. Alles andere, auch ein unbekannter Abschnitt, zusätzliche Teile oder
 * ein überlanger Rest, öffnet die Seite ohne Abschnitt; fremde Hashes lassen sie geschlossen. Die Ergebnisse sind
 * eingefroren und werden gemeinsam benutzt.
 */
export function parseHash(hash: string): HashZiel {
  const roh = (hash.startsWith("#") ? hash.slice(1) : hash).toLowerCase();
  const teile = roh.split("/", 8); // mehr Teile gibt es nie; ein überlanger Hash fällt unten auf "ohne Abschnitt"
  if (teile[0] !== HASH_WURZEL) return GESCHLOSSEN;
  if (roh.length > 64) return OFFEN_OHNE_ABSCHNITT;
  const rest = teile.slice(1).filter((t, i, alle) => !(t === "" && i === alle.length - 1)); // ein Schrägstrich am Ende zählt nicht
  if (rest.length === 1 && istAbschnittId(rest[0])) return Object.freeze({ offen: true, abschnitt: rest[0] });
  return OFFEN_OHNE_ABSCHNITT;
}

/** Hash (mit #) für die Seite; ohne Abschnitt die Gliederung beziehungsweise der erste Abschnitt. */
export const hashFuer = (abschnitt: AbschnittId | null): string =>
  abschnitt ? `#${HASH_WURZEL}/${abschnitt}` : `#${HASH_WURZEL}`;

/**
 * Welcher Abschnitt gilt jetzt? Ein erlaubter Abschnitt aus der Adresse gilt immer. Sonst zeigt die zweispaltige
 * Ansicht den ersten erlaubten Abschnitt, die einspaltige (Handy) die Liste (null).
 */
export function wirksamerAbschnitt(
  ziel: HashZiel,
  erlaubt: readonly Abschnitt[],
  zweispaltig: boolean,
): AbschnittId | null {
  if (!ziel.offen) return null;
  if (ziel.abschnitt && erlaubt.some((a) => a.id === ziel.abschnitt)) return ziel.abschnitt;
  return zweispaltig ? (erlaubt[0]?.id ?? null) : null;
}

/** Wohin führt "Zurück" (und die Taste Escape)? Am Handy aus einem Abschnitt zur Liste, sonst zum Gespräch. */
export function zurueckZiel(wirksam: AbschnittId | null, zweispaltig: boolean): "liste" | "gespraech" {
  return !zweispaltig && wirksam !== null ? "liste" : "gespraech";
}

// ---------- Geräte und PC ----------
export type GeraeteBlock = "status" | "verwaltung" | "pc" | "fernzugriff" | "betriebsart" | "einrichtung";

/** Kennung der Karte Geräteverwaltung im Dokument: Hinweise und Info springen hierhin (lib/einstellungenHash.ts). */
export const GERAETEVERWALTUNG_ID = "geraeteverwaltung";

/**
 * Blöcke des Abschnitts "Geräte und PC": der Status der Verbindungen sieht jeder. Die Geräteverwaltung (Freigeben,
 * Sperren, Widerrufen), die PC-Steuerung und der Fernzugriff nur der Admin (die PC-Steuerung im
 * Browser auf dem Server mit dem Hinweis, dass sie zur Desktop-App gehört). Die Betriebsart gibt es nur, wo es etwas zu
 * wählen gibt (Büro eingerichtet und kein Browser auf dem Server). Die Einrichtung des Geräts gibt es nur in der
 * Desktop-App.
 */
export function geraeteBloecke(o: {
  rolle: Rolle | null;
  desktop: boolean;
  serverBrowser: boolean;
  bueroVerfuegbar: boolean;
}): readonly GeraeteBlock[] {
  const bloecke: GeraeteBlock[] = ["status"];
  if (o.rolle === "admin") bloecke.push("verwaltung", "pc", "fernzugriff");
  if (o.bueroVerfuegbar && !o.serverBrowser) bloecke.push("betriebsart");
  if (o.desktop) bloecke.push("einrichtung");
  return bloecke;
}

export type GeraeteStand = {
  zustand: EinrichtungsZustand;
  text?: string;
  tunnel: TunnelZustand | null;
  kennung: string | null;
};

const TUNNEL_TEXT: Record<TunnelZustand, string> = {
  verbunden: "Tunnel zu WAMO verbunden",
  host: "Dieser PC ist selbst der Host, kein Tunnel nötig",
  aus: "Tunnel aus",
  unbekannt: "Tunnel unbekannt",
};

/** Ein Satz zum Stand der Geräteeinrichtung; "bereit" heißt: in Ordnung, nichts zu tun. */
export function geraeteStandText(
  s: GeraeteStand,
  o: { desktop: boolean; bueroBetrieb: boolean },
): { text: string; art: "bereit" | "offen" | "wartet" | "fehler" } {
  if (!o.desktop) return { text: "Die Einrichtung gibt es nur in der Desktop-App.", art: "offen" };
  if (!o.bueroBetrieb) {
    return { text: "Die Einrichtung läuft nur in der Betriebsart Büro. Wechsle dafür die Betriebsart.", art: "offen" };
  }
  switch (s.zustand) {
    case "wartet":
      return { text: "Dein Laptop wartet auf die Freigabe durch einen Admin.", art: "wartet" };
    case "fehler":
      return { text: s.text || "Die Einrichtung deines Laptops ist fehlgeschlagen.", art: "fehler" };
    case "eingerichtet": {
      const tunnel = s.tunnel ? `${TUNNEL_TEXT[s.tunnel]}.` : "";
      const kennung = s.kennung ? ` Kennung ${s.kennung}.` : "";
      return { text: `Eingerichtet.${kennung}${tunnel ? ` ${tunnel}` : ""}`, art: s.tunnel === "verbunden" || s.tunnel === "host" ? "bereit" : "offen" };
    }
    case "nicht_noetig":
      return { text: "Noch nicht geprüft. Die Einrichtung startet nach der Anmeldung von selbst.", art: "offen" };
    default: {
      const unbekannt: never = s.zustand; // neue Zustände müssen hier behandelt werden
      void unbekannt;
      return { text: "Stand unbekannt.", art: "offen" };
    }
  }
}

// ---------- Seitenleiste: eine Zeile zum Modellzugang ----------
/**
 * Kurzer Stand für die Seitenleiste. Genau das, was der Kern gemeldet hat: ob ein eigener Claude-Schlüssel (Gespräch)
 * und ein Claude-Code-Token (Entwicklungsaufträge) hinterlegt sind. Nie ein Wert, nur "hinterlegt".
 */
export function modellzugangKurz(z: Modellzugang | null): string {
  if (!z) return "wird geladen";
  if (!z.art) return "Stand nicht lesbar"; // der Kern hat keinen lesbaren Stand gemeldet, mit oder ohne Fehlertext
  if (z.hinterlegt && z.codeHinterlegt) return "Claude-Schlüssel und Token hinterlegt";
  if (z.hinterlegt) return "Claude-Schlüssel hinterlegt";
  if (z.codeHinterlegt) return "Token hinterlegt";
  if (z.quelle === "zuordnung") return "Schlüssel des Kerns";
  return "fehlt";
}

export const ROLLE_TEXT: Record<Rolle, string> = { admin: "Admin", mitglied: "Mitglied" };
export const rolleText = (rolle: Rolle | null): string => (rolle ? ROLLE_TEXT[rolle] : "noch unbekannt");
