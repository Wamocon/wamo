/**
 * Farbschema der Oberfläche: Dunkel (Standard) oder Hell. Die Wahl gilt je Gerät und liegt im localStorage (jeder Zugriff
 * mit try/catch: in privaten Fenstern oder bei gesperrten Websitedaten läuft die Seite einfach dunkel weiter).
 *
 * Wirksam ist das Attribut data-theme am Element html: "light" oder "dark" (globals.css kennt beide Blöcke; Orb.tsx,
 * KernRinge, VoiceLine und Canvas.tsx beobachten es). INIT_SKRIPT setzt es im Kopf der Seite vor dem ersten Zeichnen
 * (layout.tsx), damit nichts aufblitzt. Die Darstellung "Nacht" erzwingt dunkel (kommando/Rahmen.tsx ruft themaNacht):
 * eine Wahl während der Nacht wird nur gespeichert und gilt wieder, sobald die Nacht endet.
 * Diese Datei kommt ohne React aus, damit layout.tsx (Server-Komponente) sie laden kann; der Hook steht in useThema.ts.
 * Der Zugriff auf document steht nur in Funktionen, damit node:test sie laden kann (thema.test.ts).
 */

export type Thema = "dunkel" | "hell";

export const THEMA_SCHLUESSEL = "wamo.thema";
export const THEMEN: readonly Thema[] = ["dunkel", "hell"];
/** Farbe der Browserleiste (meta theme-color) je Thema, gleich dem Hintergrund --bg in globals.css */
export const LEISTENFARBE: Record<Thema, string> = { dunkel: "#07070a", hell: "#f3f3f3" };

export const istThema = (wert: unknown): wert is Thema => wert === "dunkel" || wert === "hell";
export const attributWert = (t: Thema): "light" | "dark" => (t === "hell" ? "light" : "dark");
export const themaAusAttribut = (a: string | null | undefined): Thema => (a === "light" ? "hell" : "dunkel");

function lokalerSpeicher(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null; // schon der Zugriff auf localStorage kann werfen
  }
}

/** Gespeicherte Wahl, sonst dunkel. Ohne Angabe liest die Funktion den localStorage dieses Geräts. */
export function themaLesen(speicher?: Pick<Storage, "getItem"> | null): Thema {
  try {
    const s = speicher === undefined ? lokalerSpeicher() : speicher;
    const wert = s?.getItem(THEMA_SCHLUESSEL);
    return istThema(wert) ? wert : "dunkel";
  } catch {
    return "dunkel";
  }
}

/** Wahl ablegen; false, wenn der Speicher fehlt oder nicht schreibt (die Wahl gilt dann nur bis zum Neuladen). */
export function themaSpeichern(t: Thema, speicher?: Pick<Storage, "setItem"> | null): boolean {
  try {
    const s = speicher === undefined ? lokalerSpeicher() : speicher;
    if (!s) return false;
    s.setItem(THEMA_SCHLUESSEL, t);
    return true;
  } catch {
    return false;
  }
}

/**
 * Skript für den Kopf der Seite: liest die Wahl und setzt data-theme und die Farbe der Browserleiste, noch bevor etwas
 * gezeichnet wird. Bewusst ohne Abhängigkeiten und mit eigenem try/catch (es läuft vor jedem anderen Code).
 */
export const INIT_SKRIPT =
  `(function(){var d=document.documentElement,t="dunkel";` +
  `try{t=localStorage.getItem(${JSON.stringify(THEMA_SCHLUESSEL)})}catch(e){}` +
  `var h=t==="hell";d.setAttribute("data-theme",h?"light":"dark");` +
  `var m=document.querySelector('meta[name="theme-color"]');` +
  `if(m)m.setAttribute("content",h?${JSON.stringify(LEISTENFARBE.hell)}:${JSON.stringify(LEISTENFARBE.dunkel)})})();`;

const hoerer = new Set<() => void>();

/** Thema am Dokument einstellen (data-theme und Browserleiste). Speichert nichts. Hörer erfahren es sofort. */
export function themaAnwenden(t: Thema): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", attributWert(t));
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", LEISTENFARBE[t]);
  hoerer.forEach((h) => h());
}

let nachtAktiv = false;

/** Die Darstellung Nacht beginnt (an) oder endet: während der Nacht ist es dunkel, danach gilt wieder die gespeicherte Wahl. */
export function themaNacht(an: boolean): void {
  nachtAktiv = an;
  themaAnwenden(an ? "dunkel" : themaLesen());
}

/** Wahl der Person: speichern und anwenden (in der Nacht nur speichern). */
export function themaWaehlen(t: Thema): void {
  themaSpeichern(t);
  if (!nachtAktiv) themaAnwenden(t);
}

// Zwei Wege zum Hörer: die eigenen Aufrufe melden sofort (themaAnwenden), eine Änderung des Attributs von außen
// (zum Beispiel das Skript im Kopf der Seite) fängt der MutationObserver auf.
export function themaAbonnieren(melden: () => void): () => void {
  hoerer.add(melden);
  const beobachter = new MutationObserver(melden);
  beobachter.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => {
    hoerer.delete(melden);
    beobachter.disconnect();
  };
}

/** Das gerade wirksame Thema aus data-theme (auch die erzwungene Nacht). Nur im Browser; der Hook dazu steht in useThema.ts. */
export const themaWirksam = (): Thema => themaAusAttribut(document.documentElement.getAttribute("data-theme"));
