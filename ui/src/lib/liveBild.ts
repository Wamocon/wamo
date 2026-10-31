/**
 * Live-Bild des eigenen Laptops: Bilder kurz im Arbeitsspeicher, nie auf Platte.
 *
 * Ein Bild kommt als Servernachricht (Base64-JPEG, lib/nachrichten.ts prüft es), wird hier entschlüsselt, vom Browser
 * dekodiert und auf die Zeichenfläche der Ansicht gelegt, danach bestätigt useWamo es dem Kern (ohne Bestätigung schickt
 * der Kern kein weiteres). Es gibt keinen Store-Eintrag, keinen localStorage, keine Datei, keinen Download und keinen
 * Verlauf: Wer die Ansicht schließt oder das Gespräch beendet, löscht die Fläche. Bilder werden nur gezeichnet, solange
 * die Person die Ansicht selbst geöffnet hat (WamoState.liveBild.geoeffnet).
 */

const BILD_KANTE_MAX = 4096;

/** Zeichnet ein Bild auf die Fläche der Ansicht (das Bild wird danach geschlossen, nicht aufheben). */
export type Zeichner = (bild: ImageBitmap) => void;

let zeichner: Zeichner | null = null;
let loeschen: (() => void) | null = null;
let lauf = 0; // jede Löschung macht laufende Dekodierungen ungültig

/** Die Ansicht meldet sich an (beim Einhängen) und ab (beim Entfernen). Gibt die Abmeldung zurück. */
export function zeichnerSetzen(z: Zeichner, leeren: () => void): () => void {
  zeichner = z;
  loeschen = leeren;
  return () => {
    if (zeichner === z) {
      zeichner = null;
      loeschen = null;
    }
  };
}

/** Base64 (ohne Zeilenumbrüche) in Bytes. Wirft bei ungültigen Zeichen. */
export function base64ZuBytes(b64: string): Uint8Array {
  const roh = atob(b64);
  const bytes = new Uint8Array(roh.length);
  for (let i = 0; i < roh.length; i += 1) bytes[i] = roh.charCodeAt(i);
  return bytes;
}

/** Bild dekodieren und zeichnen. true, wenn es gezeichnet wurde (dann bestätigt useWamo es). */
export async function bildAnzeigen(jpeg: string): Promise<boolean> {
  const ziel = zeichner;
  if (!ziel) return false;
  const nr = lauf;
  try {
    const bytes = base64ZuBytes(jpeg);
    const bitmap = await createImageBitmap(new Blob([bytes as BlobPart], { type: "image/jpeg" }));
    try {
      // Die gemeldeten Maße stimmen mit dem Bild überein und bleiben unter der Grenze (kein kleines JPEG mit riesiger Fläche)
      if (nr !== lauf || zeichner !== ziel || bitmap.width > BILD_KANTE_MAX || bitmap.height > BILD_KANTE_MAX) return false;
      ziel(bitmap);
      return true;
    } finally {
      bitmap.close();
    }
  } catch {
    return false;
  }
}

/** Zeichenfläche leeren und laufende Dekodierungen verwerfen (Ende, Pause, Abmelden, Gespräch beendet). */
export function liveBildLoeschen(): void {
  lauf += 1;
  loeschen?.();
}

/** "m:ss" für die Restzeit, nie negativ. */
export function restZeit(sekunden: number): string {
  const s = Math.max(0, Math.floor(sekunden));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Erklärung zu einem Grund, wenn der Kern keinen Text mitschickt. */
export const GRUND_TEXT: Record<string, string> = {
  hinweis_fehlt: "Der rote Hinweis am Laptop ist nicht sichtbar. Ohne ihn gibt es kein Bild.",
  abgelaufen: "Die Zeit des Live-Bilds am Laptop ist abgelaufen. Die Person am Laptop schaltet es erneut ein.",
  nicht_erlaubt: "Das Live-Bild gibt es nur für Admins an ihrem eigenen Laptop, wenn die PC-Steuerung an ist.",
  schalter_aus: "Am Laptop ist das Live-Bild aus. Die Person am Laptop schaltet es im Tray-Menü von WAMO ein.",
  keine_einwilligung: "Die Person am Laptop muss dem Live-Bild dort erst zustimmen.",
  freigabe: "Eine Freigabe ist offen: Das Bild ruht, bis sie entschieden ist.",
  vollbild: "Am Laptop läuft ein Programm im Vollbild: Das Bild ruht.",
  geschuetzt: "Am Laptop liegt ein geschütztes Fenster vorne: Das Bild ruht.",
};
