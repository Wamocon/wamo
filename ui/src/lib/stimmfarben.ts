/**
 * Stimmfarben lesen statt sie doppelt zu definieren.
 *
 * Einzige Quelle sind die CSS-Variablen in app/globals.css (hell und dunkel):
 *   --voice-wamo, --voice-user, --voice-other                für Kern (Orb.tsx, KernRinge) und Stimmlinie
 *   --kern-heiss, --kern-mensch, --kern-fremd                 für die Glut der Lichtkugel (Orb.tsx)
 *   --overlay-wamo, --overlay-user, --overlay-other         für den Stimmen-Rand der Desktop-App
 * Kern und Stimmlinie lesen sie per getComputedStyle. Der Rand (desktop/overlay.js) bekommt sie
 * einmalig über window.wamoDesktop.farben; desktop/main.js prüft die Werte.
 */

/** Farbe als Anteile 0 bis 1 (r, g, b, a). */
export type Rgba = [number, number, number, number];
/** Farbe als Ganzzahlen 0 bis 255 (für die IPC-Brücke). */
export type Rgb255 = [number, number, number];

export type OverlayFarben = { wamo: Rgb255; mensch: Rgb255; andere: Rgb255 };

/** #RGB, #RRGGBB oder #RRGGBBAA in Anteile umrechnen; anderes ergibt null. */
export function parseHex(value: string): Rgba | null {
  const v = value.trim().replace(/^#/, "");
  const full = v.length === 3 ? v.replace(/./g, (c) => c + c) : v;
  if (!/^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(full)) return null;
  const n = (i: number) => parseInt(full.slice(i, i + 2), 16) / 255;
  return [n(0), n(2), n(4), full.length === 8 ? n(6) : 1];
}

/** CSS-Variable als Farbe lesen (Schwarz, falls nicht gesetzt). */
export function cssFarbe(style: CSSStyleDeclaration, name: string): Rgba {
  return parseHex(style.getPropertyValue(name)) ?? [0, 0, 0, 1];
}

const to255 = (c: Rgba): Rgb255 => [Math.round(c[0] * 255), Math.round(c[1] * 255), Math.round(c[2] * 255)];

/** Farben für den Stimmen-Rand aus den CSS-Variablen der Seite. */
export function overlayFarben(): OverlayFarben {
  const style = getComputedStyle(document.documentElement);
  return {
    wamo: to255(cssFarbe(style, "--overlay-wamo")),
    mensch: to255(cssFarbe(style, "--overlay-user")),
    andere: to255(cssFarbe(style, "--overlay-other")),
  };
}
