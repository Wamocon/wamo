/**
 * Komponententests der Oberfläche (React-Komponenten und Hooks), getrennt von den Logiktests.
 *
 * Zwei getrennte Systeme, damit sich beide nicht stören:
 *   npm test                  node --test "src/**\/*.test.ts"   reine Logik, kein DOM, kein JSX (unverändert)
 *   npm run test:komponenten  vitest run                        Komponenten und Hooks in jsdom, Dateien *.comp.test.tsx
 *
 * Wahl der Werkzeuge:
 * - vitest: läuft auf Vite und esbuild, versteht TypeScript und JSX ohne weiteren Bauschritt und hat Attrappen (vi.mock) sowie
 *   falsche Uhren (vi.useFakeTimers) eingebaut, beides brauchen die Tests des Abfragetakts. MIT-Lizenz, laufend gepflegt.
 *   Die Linie 4 passt zum Projekt (@types/node 20, Node 22 im Image), die Linie 5 verlangt @types/node 22 oder neuer.
 * - jsdom: das DOM für die Umgebung. MIT-Lizenz, laufend gepflegt.
 * - @testing-library/react und @testing-library/user-event: prüfen, was Nutzer erleben (Rollen, Namen, Tastatur), nicht den
 *   inneren Aufbau. user-event ist nötig, weil nur es Enter und Leertaste auf einem Button wie der Browser in einen Klick umsetzt.
 * - Kein jest-dom: die wenigen Prüfungen von Attributen und Fokus gehen mit den Bordmitteln von vitest.
 *
 * JSX: automatische Laufzeit über esbuild, wie "jsx": "react-jsx" in tsconfig.json. Der Alias "@" zeigt wie dort auf src/.
 * Die Testdateien liegen neben dem Code (*.comp.test.tsx), gehören aber nie zum Build: Next.js bündelt nur, was von
 * src/app erreichbar ist, und die node-Tests suchen nur *.test.ts.
 */
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.comp.test.tsx"],
    setupFiles: ["./vitest.setup.ts"],
    // Attrappen und Spione nach jedem Test auf den Ausgangszustand, damit kein Test vom vorigen abhängt
    restoreMocks: true,
    clearMocks: true,
  },
});
