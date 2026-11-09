import { KernFehler } from "@/lib/kernApi";

/**
 * NUR ENTWICKLUNG (components/Vorschau.tsx): Beispieldaten für die Büro-Karte ohne Kern und ohne Gerätedienst.
 *
 *   /?vorschau=1&buero=beispiel     vierzehn Geräte, ein paar unbekannt (Normalfall)
 *   /?vorschau=1&buero=stoerung     zusätzlich drei Störungen (Router, Drucker, ein Server-Dienst)
 *   /?vorschau=1&buero=alles        alles in Ordnung
 *   /?vorschau=1&buero=leer         keine Geräte eingerichtet
 *   /?vorschau=1&buero=offline      Gerätedienst nicht erreichbar (503)
 *   /?vorschau=1&buero=fehler       Kern antwortet mit einem Fehler (500)
 *
 * Die Form gleicht der Antwort von GET /wamo/api/buero/status (voice-core/buero_api.py).
 */

type Zeile = { id: string; name: string; gruppe: string; ampel: string; text: string; tipp: string; kennzahlen?: Record<string, number> };

const GERAETE: Zeile[] = [
  { id: "sokrates-2", name: "Sokrates-2", gruppe: "server", ampel: "ok", text: "läuft, Last 0,4", tipp: "Sag: Wie geht es den Servern?", kennzahlen: { last: 0.4 } },
  { id: "sokrates-1", name: "Sokrates-1", gruppe: "server", ampel: "ok", text: "läuft, Last 1,2", tipp: "Sag: Wie geht es den Servern?", kennzahlen: { last: 1.2 } },
  { id: "caesar", name: "Caesar", gruppe: "server", ampel: "ok", text: "läuft, Last 0,1", tipp: "Sag: Wie geht es den Servern?", kennzahlen: { last: 0.1 } },
  { id: "drucker", name: "Drucker Brother MFC-L3750CDW", gruppe: "drucker", ampel: "ok", text: "bereit, Toner 80 Prozent", tipp: "Sag: Wie ist der Tonerstand am Drucker?", kennzahlen: { toner_prozent: 80 } },
  { id: "scanner", name: "Scanner Brother MFC-L3750CDW", gruppe: "drucker", ampel: "ok", text: "bereit", tipp: "Sag: Scanne eine Seite vom Glas" },
  { id: "router", name: "FRITZ!Box 7590", gruppe: "netz", ampel: "ok", text: "Internet verbunden, 86 Mbit runter", tipp: "Sag: Ist das Internet in Ordnung?", kennzahlen: { runter_mbit: 86, hoch_mbit: 20 } },
  { id: "imac", name: "iMac", gruppe: "netz", ampel: "unbekannt", text: "nicht sichtbar, vielleicht aus", tipp: "Sag: Welche Bürogeräte sind gerade erreichbar?" },
  { id: "nas-1", name: "WMC_NAS1", gruppe: "speicher", ampel: "ok", text: "erreichbar", tipp: "Sag: Suche auf dem NAS nach dem Angebot" },
  { id: "nas-testelite", name: "WMC-Testelite", gruppe: "speicher", ampel: "unbekannt", text: "erreichbar, WAMO-Zugang nicht eingerichtet", tipp: "Sag: Suche auf dem NAS nach dem Angebot" },
  { id: "fernseher", name: "Fernseher LG OLED 83 Zoll", gruppe: "medien", ampel: "ok", text: "an, nicht gekoppelt", tipp: "Zum Steuern per Sprache muss der Fernseher erst gekoppelt werden" },
  { id: "lautsprecher", name: "Lautsprecher Beispiel", gruppe: "medien", ampel: "unbekannt", text: "keine Antwort, vielleicht aus", tipp: "Sag: Stell den Lautsprecher leiser" },
  { id: "steckdose-1", name: "Steckdose 1 (0470)", gruppe: "steckdosen", ampel: "ok", text: "an, 12 Watt", tipp: "Sag: Schalte Steckdose 1 aus", kennzahlen: { leistung_w: 12.3 } },
  { id: "steckdose-2", name: "Steckdose 2 (2F90)", gruppe: "steckdosen", ampel: "ok", text: "aus", tipp: "Sag: Schalte Steckdose 2 an", kennzahlen: { leistung_w: 0 } },
  { id: "steckdose-3", name: "Steckdose 3 (75B0)", gruppe: "steckdosen", ampel: "ok", text: "aus", tipp: "Sag: Schalte Steckdose 3 an", kennzahlen: { leistung_w: 0 } },
];

function mit(zeilen: Zeile[], ueber: Record<string, Partial<Zeile>>): Zeile[] {
  return zeilen.map((z) => ({ ...z, ...(ueber[z.id] ?? {}) }));
}

function antwort(geraete: Zeile[]): unknown {
  return { geraete, unbekannt: null, stand: new Date().toISOString().replace(/\.\d{3}Z$/, "Z") };
}

/** Der Abruf, der in der Vorschau an die Stelle des Kerns tritt; null, wenn die Vorschau keine Büro-Daten verlangt. */
export function bueroBeispiel(art: string | null): (() => Promise<unknown>) | null {
  switch (art) {
    case "beispiel":
      return async () => antwort(GERAETE);
    case "stoerung":
      return async () =>
        antwort(
          mit(GERAETE, {
            caesar: { ampel: "stoerung", text: "läuft, 1 Dienst nicht aktiv" },
            drucker: { ampel: "stoerung", text: "Papierstau", kennzahlen: { toner_prozent: 80 } },
            router: { ampel: "stoerung", text: "Internet getrennt" },
          }),
        );
    case "alles":
      return async () =>
        antwort(
          mit(GERAETE, {
            imac: { ampel: "ok", text: "im Netz sichtbar" },
            "nas-testelite": { ampel: "ok", text: "erreichbar" },
            lautsprecher: { ampel: "ok", text: "an, Lautstärke 30" },
          }),
        );
    case "leer":
      return async () => antwort([]);
    case "offline":
      return async () => {
        throw new KernFehler(503, "Der Gerätedienst ist gerade nicht erreichbar.");
      };
    case "fehler":
      return async () => {
        throw new KernFehler(500, "Fehler 500");
      };
    default:
      return null;
  }
}
