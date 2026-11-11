// Komponententest der Warnung vor dem Neuladen (lib/useNeuladenWarnung.ts): nur solange ein Auftrag läuft, und nur eine Warnung je Fenster.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { auftraegeNachStart } from "@/lib/auftragStart";
import type { Auftrag } from "@/lib/nachrichten";
import { useNeuladenWarnung } from "@/lib/useNeuladenWarnung";
import { resetWamo, wamo } from "@/lib/wamo";

const A: Auftrag = { job: "j1", projekt: "kern-testrepo", art: "Analyse", auftrag: "Prüfe das Repo", abnahme: [] };

/** Löst beforeunload aus und sagt, ob die Seite das Entladen anhalten will (die Abfrage des Browsers, in Electron will-prevent-unload). */
function entladen(): boolean {
  const e = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(e);
  return e.defaultPrevented;
}

describe("Warnung vor dem Neuladen", () => {
  beforeEach(() => resetWamo());

  it("ohne Auftrag keine Abfrage", () => {
    renderHook(() => useNeuladenWarnung());
    expect(entladen()).toBe(false);
  });

  it("eine Karte, die nur wartet, und ein geplanter Vorgang lösen keine Abfrage aus", () => {
    renderHook(() => useNeuladenWarnung());
    wamo.set((s) => ({
      ...s,
      auftraege: [A],
      vorgaenge: [{ id: "v1", titel: "Analyse", art: "entwicklung", status: "geplant" } as never],
    }));
    expect(entladen()).toBe(false);
  });

  it("gesendet (Quittung steht aus) oder laufender Vorgang: die Abfrage greift", () => {
    renderHook(() => useNeuladenWarnung());
    wamo.set((s) => ({ ...s, auftraege: auftraegeNachStart([A], "j1", 0) }));
    expect(entladen()).toBe(true);
    wamo.set((s) => ({ ...s, auftraege: [], vorgaenge: [{ id: "v1", titel: "Analyse", art: "entwicklung", status: "laeuft" } as never] }));
    expect(entladen()).toBe(true);
    wamo.set((s) => ({ ...s, vorgaenge: [{ id: "v1", titel: "Analyse", art: "entwicklung", status: "fertig" } as never] }));
    expect(entladen()).toBe(false);
  });

  it("nach dem Aufräumen des Hooks bleibt nichts am Fenster hängen", () => {
    wamo.set((s) => ({ ...s, auftraege: auftraegeNachStart([A], "j1", 0) }));
    const { unmount } = renderHook(() => useNeuladenWarnung());
    expect(entladen()).toBe(true);
    unmount();
    expect(entladen()).toBe(false);
  });
});
