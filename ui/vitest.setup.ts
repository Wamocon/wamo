/**
 * Gemeinsame Vorbereitung der Komponententests (vitest.config.ts): Aufräumen nach jedem Test und die zwei Browserfunktionen,
 * die jsdom nicht mitbringt, aber die Komponenten aufrufen (matchMedia für prefers-reduced-motion, scrollIntoView).
 * Nur Hilfen für die Testumgebung, am Produktivcode ändert sich nichts.
 */
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Ohne globale Namen (globals ist aus) räumt Testing Library nicht von selbst auf
afterEach(() => {
  cleanup();
});

if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => undefined;
}
