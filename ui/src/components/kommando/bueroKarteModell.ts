/**
 * Reine Logik der Büro-Karte (kommando/BueroKarte.tsx), ohne React und ohne Browser, damit sie sich mit node:test prüfen
 * lässt (bueroKarteModell.test.ts). Die Daten kommen vom Kern (voice-core/buero_api.py, GET /wamo/api/buero/status) und
 * werden hier streng geprüft: nur bekannte Felder, Texte begrenzt und von Steuerzeichen gesäubert, Gerätetexte sind Daten.
 *
 * Die Karte ist nur eine Ansicht. Sie steuert nichts; gesteuert wird per Sprache.
 */

export type Ampel = "ok" | "stoerung" | "unbekannt";

export type GruppeId = "server" | "drucker" | "netz" | "speicher" | "medien" | "steckdosen";

export type BueroGeraet = {
  id: string;
  name: string;
  gruppe: GruppeId;
  ampel: Ampel;
  /** Zustand in Klartext, zum Beispiel "bereit, Toner 80 Prozent" */
  text: string;
  /** Sprechtipp, zum Beispiel "Sag: Schalte den Fernseher aus" */
  tipp: string;
  kennzahlen?: Record<string, number>;
};

export type BueroStand = {
  geraete: BueroGeraet[];
  /** Anzahl unbekannter Geräte im Netz; der Kern liefert vorerst null */
  unbekannt: number | null;
  /** Zeitpunkt der Antwort (ISO, UTC) oder leer */
  stand: string;
};

/** Ringe von innen nach außen. */
export const GRUPPEN: readonly { id: GruppeId; label: string }[] = [
  { id: "server", label: "Server" },
  { id: "drucker", label: "Drucker und Scanner" },
  { id: "netz", label: "Netz" },
  { id: "speicher", label: "Speicher" },
  { id: "medien", label: "Medien" },
  { id: "steckdosen", label: "Steckdosen" },
];

const GRUPPE_IDS = new Set<string>(GRUPPEN.map((g) => g.id));
const AMPELN = new Set<string>(["ok", "stoerung", "unbekannt"]);
const KENNUNG = /^[a-z0-9][a-z0-9-]{0,30}$/;
const KENNZAHL = /^[a-z_]{1,24}$/;
// Steuerzeichen (Cc), unsichtbare und umlenkende Formatzeichen (Cf), Zeilen- und Absatztrenner (Zl, Zp)
const STEUER = new RegExp("[\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}]", "gu");
const MAX_GERAETE = 64;

export function sauber(wert: unknown, grenze: number): string {
  return typeof wert === "string" ? wert.replace(STEUER, " ").replace(/\s+/g, " ").trim().slice(0, grenze) : "";
}

function geraetPruefen(roh: unknown): BueroGeraet | null {
  if (!roh || typeof roh !== "object") return null;
  const g = roh as Record<string, unknown>;
  if (typeof g.id !== "string" || !KENNUNG.test(g.id)) return null;
  if (typeof g.gruppe !== "string" || !GRUPPE_IDS.has(g.gruppe)) return null;
  if (typeof g.ampel !== "string" || !AMPELN.has(g.ampel)) return null;
  const name = sauber(g.name, 60);
  const text = sauber(g.text, 80);
  if (!name || !text) return null;
  const aus: BueroGeraet = {
    id: g.id,
    name,
    gruppe: g.gruppe as GruppeId,
    ampel: g.ampel as Ampel,
    text,
    tipp: sauber(g.tipp, 120),
  };
  if (g.kennzahlen && typeof g.kennzahlen === "object" && !Array.isArray(g.kennzahlen)) {
    const zahlen: Record<string, number> = {};
    for (const [k, v] of Object.entries(g.kennzahlen as Record<string, unknown>).slice(0, 8)) {
      if (KENNZAHL.test(k) && typeof v === "number" && Number.isFinite(v)) zahlen[k] = v;
    }
    if (Object.keys(zahlen).length) aus.kennzahlen = zahlen;
  }
  return aus;
}

/** Antwort des Kerns prüfen. null, wenn sie nicht die erwartete Form hat. Unbrauchbare Geräte fallen einzeln weg. */
export function pruefeBuero(roh: unknown): BueroStand | null {
  if (!roh || typeof roh !== "object") return null;
  const d = roh as Record<string, unknown>;
  if (!Array.isArray(d.geraete)) return null;
  const gesehen = new Set<string>();
  const geraete: BueroGeraet[] = [];
  for (const eintrag of d.geraete.slice(0, MAX_GERAETE)) {
    const g = geraetPruefen(eintrag);
    if (!g || gesehen.has(g.id)) continue;
    gesehen.add(g.id);
    geraete.push(g);
  }
  const unbekannt = typeof d.unbekannt === "number" && Number.isInteger(d.unbekannt) && d.unbekannt >= 0 ? d.unbekannt : null;
  const stand = typeof d.stand === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(d.stand) ? d.stand : "";
  return { geraete, unbekannt, stand };
}

// ---------- Darstellung: Zustand nie nur über Farbe ----------
export type Form = "voll" | "strich" | "hohl";

export type Darstellung = {
  /** Kreis gefüllt (in Ordnung), Kreis mit Strich (Störung), Kreis hohl (unbekannt) */
  form: Form;
  wort: string;
  /** langsames Pulsieren; entfällt bei reduzierter Bewegung (CSS) */
  pulsiert: boolean;
};

export const DARSTELLUNG: Record<Ampel, Darstellung> = {
  ok: { form: "voll", wort: "in Ordnung", pulsiert: false },
  stoerung: { form: "strich", wort: "Störung", pulsiert: true },
  unbekannt: { form: "hohl", wort: "unbekannt", pulsiert: false },
};

export function darstellung(ampel: Ampel): Darstellung {
  return DARSTELLUNG[ampel];
}

/** Beschriftung eines Punkts für Screenreader: Name, Zustandswort und Klartext. */
export function beschriftung(g: BueroGeraet): string {
  return `${g.name}, ${darstellung(g.ampel).wort}: ${g.text}`;
}

export function gruppenName(id: GruppeId): string {
  return GRUPPEN.find((g) => g.id === id)?.label ?? id;
}

export type Zaehlung = { gesamt: number; ok: number; stoerung: number; unbekannt: number };

export function zaehlen(geraete: readonly BueroGeraet[]): Zaehlung {
  const z: Zaehlung = { gesamt: geraete.length, ok: 0, stoerung: 0, unbekannt: 0 };
  for (const g of geraete) z[g.ampel]++;
  return z;
}

/** Eine Zeile für Kopf und Screenreader, zum Beispiel "14 Geräte, 1 Störung, 3 unbekannt". */
export function zusammenfassung(geraete: readonly BueroGeraet[]): string {
  const z = zaehlen(geraete);
  if (z.gesamt === 0) return "Keine Geräte";
  const teile = [`${z.gesamt} ${z.gesamt === 1 ? "Gerät" : "Geräte"}`];
  if (z.stoerung) teile.push(z.stoerung === 1 ? "1 Störung" : `${z.stoerung} Störungen`);
  if (z.unbekannt) teile.push(`${z.unbekannt} unbekannt`);
  if (!z.stoerung && !z.unbekannt) teile.push("alles in Ordnung");
  return teile.join(", ");
}

/** Geräte nach Gruppen in Ringreihenfolge; Gruppen ohne Geräte entfallen. Innerhalb einer Gruppe bleibt die Reihenfolge des Kerns. */
export function nachGruppen(geraete: readonly BueroGeraet[]): { id: GruppeId; label: string; geraete: BueroGeraet[] }[] {
  return GRUPPEN.map((gr) => ({ ...gr, geraete: geraete.filter((g) => g.gruppe === gr.id) })).filter((gr) => gr.geraete.length > 0);
}

// ---------- Anordnung (viewBox 0 0 400 400, Mitte 200 / 200) ----------
export const KARTE = { groesse: 400, mitte: 200, mitteRadius: 30, punktRadius: 8, ringMin: 62, ringMax: 180, ringAbstandMax: 34 } as const;
/** Jeder Ring beginnt um 150 Grad versetzt, damit Punkte benachbarter Ringe nicht übereinander liegen (für die vierzehn Geräte des Büros gemessen: kleinster Abstand 44 von 400). */
const VERSATZ_GRAD = 150;

export type Punkt = {
  id: string;
  /** Koordinaten im viewBox 0 bis 400 */
  x: number;
  y: number;
  ring: number;
  winkel: number;
};

export type Ring = { id: GruppeId; label: string; radius: number };

export function ringe(geraete: readonly BueroGeraet[]): Ring[] {
  const gruppen = nachGruppen(geraete);
  const k = gruppen.length;
  const abstand = k > 1 ? Math.min(KARTE.ringAbstandMax, (KARTE.ringMax - KARTE.ringMin) / (k - 1)) : 0;
  // einzelne oder wenige Ringe liegen mittig zwischen innen und außen, nicht ganz innen
  const start = k > 1 ? KARTE.ringMin + ((KARTE.ringMax - KARTE.ringMin) - abstand * (k - 1)) / 2 : (KARTE.ringMin + KARTE.ringMax) / 2;
  return gruppen.map((g, i) => ({ id: g.id, label: g.label, radius: Math.round((start + i * abstand) * 10) / 10 }));
}

const rund = (n: number) => Math.round(n * 100) / 100;

/** Ort jedes Geräts: je Ring gleichmäßig verteilt, der Beginn von Ring zu Ring um 150 Grad versetzt. */
export function anordnung(geraete: readonly BueroGeraet[]): Punkt[] {
  const aus: Punkt[] = [];
  const gruppen = nachGruppen(geraete);
  const radien = ringe(geraete);
  gruppen.forEach((gr, ring) => {
    const n = gr.geraete.length;
    gr.geraete.forEach((g, k) => {
      const grad = -90 + ring * VERSATZ_GRAD + (k * 360) / n;
      const rad = (grad * Math.PI) / 180;
      const r = radien[ring].radius;
      aus.push({
        id: g.id,
        x: rund(KARTE.mitte + r * Math.cos(rad)),
        y: rund(KARTE.mitte + r * Math.sin(rad)),
        ring,
        winkel: ((grad % 360) + 360) % 360,
      });
    });
  });
  return aus;
}

/** Kleinster Abstand zweier Punkte im viewBox (für Tests und die Wahl der Trefferfläche). */
export function kleinsterAbstand(punkte: readonly Punkt[]): number {
  let min = Infinity;
  for (let i = 0; i < punkte.length; i++) {
    for (let j = i + 1; j < punkte.length; j++) {
      min = Math.min(min, Math.hypot(punkte[i].x - punkte[j].x, punkte[i].y - punkte[j].y));
    }
  }
  return min;
}

/** Ort in Prozent der Kartenbreite (für die Knöpfe, die über der Zeichnung liegen). */
export function prozent(wert: number): number {
  return rund((wert / KARTE.groesse) * 100);
}

// ---------- Abruf: alle 30 Sekunden, nur bei sichtbarer Seite ----------
export const ABRUF_MS = 30_000;
/** Der Kern lässt höchstens 6 Abrufe je Minute zu; ein Abstand unter 10 Sekunden gibt es nie. */
export const ABRUF_MIN_MS = 10_000;

/**
 * Wann der nächste Abruf fällig ist: null, solange die Seite verborgen ist oder der Reiter nicht sichtbar ist (dann ruht
 * der Abruf); sonst die Restzeit bis 30 Sekunden nach dem letzten Abruf (sofort, wenn noch keiner lief oder er älter ist).
 */
export function naechsterAbruf(letzter: number | null, jetzt: number, sichtbar: boolean): number | null {
  if (!sichtbar) return null;
  if (letzter === null) return 0;
  return Math.max(0, letzter + ABRUF_MS - jetzt);
}

// ---------- Leerzustand und Fehler (ruhig, ohne Interna) ----------
export type Leer = { titel: string; text: string };

export function leerFehler(status: number | null): Leer {
  if (status === 503 || status === 0 || status === null) {
    return {
      titel: "Gerätedienst nicht erreichbar",
      text: "Die Büro-Karte erscheint wieder, sobald WAMO im Büro Verbindung zu den Geräten hat.",
    };
  }
  if (status === 429) {
    return { titel: "Gerade zu viele Abrufe", text: "Die Karte aktualisiert sich gleich von selbst." };
  }
  return { titel: "Die Büro-Karte lädt gerade nicht", text: "Es wird weiter versucht, bitte einen Moment Geduld." };
}

export const LEER_OHNE_GERAETE: Leer = {
  titel: "Noch keine Geräte eingerichtet",
  text: "Sobald im Büro Geräte für WAMO eingerichtet sind, erscheinen sie hier.",
};

/** Uhrzeit "14:32" aus einem ISO-Zeitpunkt in der Zeitzone des Geräts; leer bei ungültiger Eingabe. */
export function uhrzeitKurz(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  return new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" }).format(new Date(t));
}
