/**
 * Gemeinsame Vorbereitung der Komponententests (vitest.config.ts): Aufräumen nach jedem Test und die Browserfunktionen,
 * die jsdom nicht mitbringt, aber die Komponenten aufrufen (matchMedia für prefers-reduced-motion, scrollIntoView, und mit neueren
 * Node-Versionen localStorage, das dort in jsdom fehlen kann).
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

// Neuere Node-Versionen lassen localStorage in der jsdom-Umgebung ungesetzt: dann ein einfacher Speicher im Arbeitsspeicher
if (typeof window.localStorage === "undefined") {
  const werte = new Map<string, string>();
  const speicher = {
    get length() {
      return werte.size;
    },
    key: (i: number) => [...werte.keys()][i] ?? null,
    getItem: (k: string) => werte.get(k) ?? null,
    setItem: (k: string, v: string) => void werte.set(k, String(v)),
    removeItem: (k: string) => void werte.delete(k),
    clear: () => werte.clear(),
  };
  Object.defineProperty(window, "localStorage", { value: speicher, configurable: true });
  Object.defineProperty(globalThis, "localStorage", { value: speicher, configurable: true });
}
