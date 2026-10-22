/**
 * Prüfungen der öffentlichen Adressen (Build-Konstanten). WAMO läuft im Büro-LAN oft über http:
 * das ist erlaubt, soll aber sichtbar sein. Nutzer: LoginGate (Hinweis unter der Karte) und
 * Statusleiste (Kennzeichen "unverschlüsselt").
 */

import { NUR_BUERO_BUILD } from "@/lib/geraet";

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** true, wenn die Adresse weder https noch Loopback ist (Daten gehen unverschlüsselt durchs Netz). */
export function unverschluesselt(url: string | undefined): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.protocol !== "https:" && !LOOPBACK.has(u.hostname);
  } catch {
    return false;
  }
}

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const VOICE_CORE_URL = process.env.NEXT_PUBLIC_VOICE_CORE_URL ?? "http://localhost:7860";

/** Adressen, die unverschlüsselt im LAN laufen (für den Hinweis in der Oberfläche). Den Büro-Kern nimmt
 * lib/betriebsart.ts nur mit https an, er taucht hier deshalb nie auf. */
export function unverschluesselteDienste(): { name: string; url: string }[] {
  return [
    { name: "Anmeldung (Supabase)", url: SUPABASE_URL },
    // Im Build für Sokrates-2 gibt es keinen lokalen Kern (lib/geraet.ts)
    { name: "Voice-Core (Unterwegs)", url: NUR_BUERO_BUILD ? "" : VOICE_CORE_URL },
  ].filter((d) => unverschluesselt(d.url));
}
