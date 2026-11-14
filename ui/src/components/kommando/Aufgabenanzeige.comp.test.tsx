// Komponententest der Aufgabenanzeige neben dem Kern (kommando/Aufgabenanzeige.tsx, lib/aufgabenanzeige.ts): Die Uhr taktet nur,
// solange ein Schritt offen ist und eine Sitzung läuft. Nach dem Trennen (echter Abbau in useWamo) steht die letzte Aufgabe als
// beendet da, ohne Takt und nie als "Läuft". Pipecat-Client und Transport sind durch Attrappen ersetzt, es gibt kein Netz.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Rueckrufe = Record<string, (...a: unknown[]) => void>;
type AttrappenClient = { opts: { callbacks: Rueckrufe } };

const h = vi.hoisted(() => {
  (URL as unknown as { createObjectURL: (b: unknown) => string }).createObjectURL = () => "blob:wamo-test";
  return { clients: [] as unknown[] };
});

vi.mock("@pipecat-ai/client-js", () => {
  class RTVIError extends Error {
    status?: number;
  }
  class PipecatClient {
    opts: unknown;
    sendText = vi.fn().mockResolvedValue(undefined);
    enableMic = vi.fn();
    updateMic = vi.fn();
    sendClientMessage = vi.fn();
    disconnect = vi.fn().mockResolvedValue(undefined);
    startBotAndConnect = vi.fn(() => Promise.resolve());
    get transport() {
      return (this.opts as { transport: unknown }).transport;
    }
    constructor(opts: unknown) {
      this.opts = opts;
      h.clients.push(this);
    }
  }
  return { PipecatClient, RTVIError };
});
vi.mock("@pipecat-ai/small-webrtc-transport", () => {
  class WavMediaManager {}
  class SmallWebRTCTransport {
    opts: unknown;
    constructor(opts: unknown) {
      this.opts = opts;
    }
  }
  return { SmallWebRTCTransport, WavMediaManager };
});
vi.mock("@/lib/betriebsart", async (orig) => ({
  ...(await orig<typeof import("@/lib/betriebsart")>()),
  betriebsartFuerStart: async () => "buero",
  kernUrl: () => "https://kern.test",
}));
vi.mock("@/lib/kernSlot", async (orig) => ({ ...(await orig<typeof import("@/lib/kernSlot")>()), aktiverSlot: async () => null }));
vi.mock("@/lib/supabase", () => ({ getSupabase: () => null, accessToken: async () => null }));

import { nutzerSetzen } from "@/lib/nutzer";
import { useWamo } from "@/lib/useWamo";
import { applyServerMessage, resetWamo, wamo } from "@/lib/wamo";
import { Aufgabenanzeige } from "./Aufgabenanzeige";

const NUTZER = "0b7f3c1e-2d4a-4c55-9a10-1a2b3c4d5e6f";
const clients = () => h.clients as AttrappenClient[];

const fluss = (ms = 10) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const aktivitaet = (id: string, status: "laeuft" | "wartet" | "fertig" | "fehler") =>
  act(() => applyServerMessage({ type: "aktivitaet", id, name: "websuche", detail: "", status }));

const uhr = () => screen.getByRole("timer").textContent;
const zustand = () => screen.getByRole("status").textContent ?? "";

describe("Aufgabenanzeige", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    h.clients.length = 0;
    resetWamo();
    localStorage.clear();
    nutzerSetzen({ id: null, email: null });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("ohne Schritte zeigt sie nichts", () => {
    const { container } = render(<Aufgabenanzeige />);
    expect(container.innerHTML).toBe("");
  });

  describe("Takt der Uhr", () => {
    it("läuft, solange ein Schritt offen ist, und endet mit dem letzten Schritt", async () => {
      wamo.set((s) => ({ ...s, presence: "thinking", turn: 1 }));
      render(<Aufgabenanzeige />);
      await aktivitaet("a", "laeuft");
      expect(zustand()).toContain("Läuft");
      await fluss(1500);
      const nach15 = uhr();
      await fluss(1000);
      expect(uhr()).not.toBe(nach15); // die Uhr zählt weiter
      expect(vi.getTimerCount()).toBeGreaterThan(0);

      await aktivitaet("a", "fertig");
      await fluss(0);
      expect(zustand()).toContain("Erledigt");
      expect(vi.getTimerCount()).toBe(0); // kein Takt mehr
      const fest = uhr();
      await fluss(2000);
      expect(uhr()).toBe(fest);
    });
  });

  describe("Aufgabe nach Trennung", () => {
    it("ohne laufende Sitzung taktet nichts und der Zustand ist nicht Läuft, auch wenn ein Schritt noch offen gemeldet ist", async () => {
      wamo.set((s) => ({ ...s, presence: "thinking", turn: 1 }));
      render(<Aufgabenanzeige />);
      await aktivitaet("a", "laeuft");
      await fluss(500);
      act(() => wamo.set((s) => ({ ...s, presence: "offline" })));
      await fluss(0);
      expect(vi.getTimerCount()).toBe(0);
      expect(zustand()).not.toContain("Läuft");
      const fest = uhr();
      await fluss(3000);
      expect(uhr()).toBe(fest);
    });

    it("trennt useWamo die Sitzung, bleibt die letzte Aufgabe als beendet stehen: keine Uhr, kein Läuft, kein offener Schritt", async () => {
      nutzerSetzen({ id: NUTZER, email: "niko@wamocon.com" });
      const hook = renderHook(() => useWamo());
      expect(hook.result.current.stillStarten?.()).toBe(true);
      await fluss();
      const c = clients()[clients().length - 1];
      await act(async () => {
        c.opts.callbacks.onBotReady({ version: "2.0.0" });
        wamo.set((s) => ({ ...s, betrieb: { modus: "buero", modelle: {}, pc: "aus" } }));
      });
      render(<Aufgabenanzeige />);
      await aktivitaet("a", "laeuft");
      await fluss(1200);
      expect(zustand()).toContain("Läuft");

      await act(async () => {
        c.opts.callbacks.onDisconnected();
      });
      await fluss();
      expect(wamo.get().presence).toBe("offline");
      expect(wamo.get().aufgabe.schritte.map((s) => s.status)).toEqual(["fehler"]);
      expect(zustand()).not.toContain("Läuft");
      const fest = uhr();
      await fluss(5000);
      expect(uhr()).toBe(fest);
    });
  });
});
