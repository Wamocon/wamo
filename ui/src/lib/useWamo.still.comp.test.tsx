// Komponententest der Verdrahtung des stillen Starts in useWamo (KP-1A): Client ohne Mikrofon, Schalter und Sperren, Antworten als Text,
// Sprechen holt das Mikrofon nach, Leerlauf nach 20 Minuten (nie bei PC-Steuerung, Karte, Auftrag), Übernahme durch ein anderes Fenster,
// Fehlerfälle und Ratenbegrenzung. Pipecat-Client und Transport sind durch Attrappen ersetzt, es gibt kein Netz und kein Mikrofon.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Rueckrufe = Record<string, (...a: unknown[]) => void>;
type AttrappenClient = {
  opts: { enableMic: boolean; transport: { opts: { mediaManager: unknown } }; callbacks: Rueckrufe };
  sendText: ReturnType<typeof vi.fn>;
  enableMic: ReturnType<typeof vi.fn>;
  updateMic: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  startBotAndConnect: ReturnType<typeof vi.fn>;
};

const h = vi.hoisted(() => {
  (URL as unknown as { createObjectURL: (b: unknown) => string }).createObjectURL = () => "blob:wamo-test";
  return {
    clients: [] as unknown[],
    /** Verhalten von startBotAndConnect: Standard gelingt sofort */
    start: null as null | (() => Promise<void>),
  };
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
    startBotAndConnect = vi.fn(() => (h.start ? h.start() : Promise.resolve()));
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

import { StillMedienManager } from "@/lib/stillMedien";
import { nutzerSetzen } from "@/lib/nutzer";
import { HINWEIS_ANDERES_FENSTER, HINWEIS_ZU_OFT, LEERLAUF_MS, STARTS_MAX, STILL_AUS, GRUND_UEBERNOMMEN } from "@/lib/stillStart";
import { useWamo } from "@/lib/useWamo";
import { resetWamo, wamo } from "@/lib/wamo";
import { WavMediaManager } from "@pipecat-ai/small-webrtc-transport";

const NUTZER = "0b7f3c1e-2d4a-4c55-9a10-1a2b3c4d5e6f";
const clients = () => h.clients as AttrappenClient[];
const letzter = () => clients()[clients().length - 1];
const aktuell = () => wamo.get();

/** Läuft, bis alle wartenden Versprechen und Zeitgeber der Attrappen durch sind. */
const fluss = (ms = 10) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

function einschalten(an = true, email = "niko@wamocon.com") {
  nutzerSetzen({ id: NUTZER, email });
  if (an) localStorage.setItem(`wamo.stillStart.${NUTZER}`, "an");
  else localStorage.removeItem(`wamo.stillStart.${NUTZER}`);
}

/** Der Kern hat "Bot bereit" gemeldet und die Betriebsdaten geschickt (stoppt auch die Verbindungsfrist). */
async function bereit(c: AttrappenClient, betrieb: Partial<NonNullable<ReturnType<typeof aktuell>["betrieb"]>> = {}) {
  await act(async () => {
    c.opts.callbacks.onBotReady({ version: "2.0.0" });
    wamo.set((s) => ({ ...s, betrieb: { modus: "buero", modelle: {}, pc: "aus", ...betrieb } }));
  });
}

describe("useWamo, stiller Start beim Tippen", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    h.clients.length = 0;
    h.start = null;
    resetWamo();
    localStorage.clear();
    nutzerSetzen({ id: null, email: null });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe("Start und Sperren", () => {
    it("Schalter aus (Standard): das Tippen startet nichts", async () => {
      einschalten(false);
      const { result } = renderHook(() => useWamo());
      expect(result.current.stillStarten?.()).toBe(false);
      await fluss();
      expect(clients()).toHaveLength(0);
      expect(aktuell().presence).toBe("offline");
    });

    it("Schalter an: der Client entsteht ohne Mikrofon und mit dem stillen Medienmanager", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      expect(result.current.stillStarten?.()).toBe(true);
      await fluss();
      expect(clients()).toHaveLength(1);
      const c = letzter();
      expect(c.opts.enableMic).toBe(false);
      expect(c.opts.transport.opts.mediaManager).toBeInstanceOf(StillMedienManager);
      expect(aktuell().presence).toBe("connecting");
      expect(aktuell().micOn).toBe(false);
      expect(aktuell().stillStart).toEqual({ still: true, perTippen: true, uebernommen: false });
    });

    it("'Gespräch starten' bleibt wie bisher: mit Mikrofon, Standardmanager, ohne stillen Zustand", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      await act(async () => {
        void result.current.connect();
        await vi.advanceTimersByTimeAsync(10);
      });
      const c = letzter();
      expect(c.opts.enableMic).toBe(true);
      expect(c.opts.transport.opts.mediaManager).toBeInstanceOf(WavMediaManager);
      expect(c.opts.transport.opts.mediaManager).not.toBeInstanceOf(StillMedienManager);
      expect(aktuell().micOn).toBe(true);
      expect(aktuell().stillStart).toEqual(STILL_AUS);
    });

    it("Laborkonto: nie, auch bei eingeschaltetem Schalter", async () => {
      einschalten(true, "labor-07@wamo.test");
      const { result } = renderHook(() => useWamo());
      expect(result.current.stillStarten?.()).toBe(false);
      await fluss();
      expect(clients()).toHaveLength(0);
    });

    it("verdeckter Tab: keine neue Sitzung", async () => {
      einschalten();
      const spion = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      const { result } = renderHook(() => useWamo());
      expect(result.current.stillStarten?.()).toBe(false);
      await fluss();
      expect(clients()).toHaveLength(0);
      spion.mockReturnValue("visible");
      expect(result.current.stillStarten?.()).toBe(true);
    });

    it("während des Verbindens gibt es keinen zweiten Start", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      expect(result.current.stillStarten?.()).toBe(true);
      expect(result.current.stillStarten?.()).toBe(false);
      await fluss();
      expect(clients()).toHaveLength(1);
    });
  });

  describe("Antworten als Text, Sprechen holt das Mikrofon nach", () => {
    it("bereit: Meldung 'Bereit, Mikrofon aus', Text geht ohne Sprachantwort hinaus", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      result.current.stillStarten?.();
      await fluss();
      await bereit(letzter());
      expect(aktuell().presence).toBe("idle");
      expect(aktuell().ansage).toBe("Bereit, Mikrofon aus");
      expect(result.current.textSenden?.("Wie spät ist es?")).toBe(true);
      await fluss();
      expect(letzter().sendText).toHaveBeenCalledTimes(1);
      expect(letzter().sendText).toHaveBeenCalledWith("Wie spät ist es?", { run_immediately: true, audio_response: false });
    });

    it("Sprechen (Mikrofonknopf): Mikrofon an, Stimme an, die Sitzung gilt nicht mehr als still", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      result.current.stillStarten?.();
      await fluss();
      await bereit(letzter());
      await act(async () => {
        result.current.toggleMic();
      });
      expect(letzter().enableMic).toHaveBeenCalledWith(true);
      expect(aktuell().micOn).toBe(true);
      expect(aktuell().stillStart.still).toBe(false);
      expect(aktuell().stillStart.perTippen).toBe(false);
      result.current.textSenden?.("Noch eine Frage");
      await fluss();
      expect(letzter().sendText).toHaveBeenLastCalledWith("Noch eine Frage", { run_immediately: true, audio_response: true });
    });

    it("ohne stille Sitzung öffnet sprechen() nichts", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      result.current.sprechen?.();
      await fluss();
      expect(clients()).toHaveLength(0);
    });
  });

  describe("Leerlauf nach 20 Minuten (Befund L1)", () => {
    async function laufendeStilleSitzung(betrieb = {}) {
      einschalten();
      const r = renderHook(() => useWamo());
      r.result.current.stillStarten?.();
      await fluss();
      await bereit(letzter(), betrieb);
      return r;
    }

    it("ohne Eingabe wird eine per Tippen gestartete Sitzung getrennt, mit Hinweis und ohne Fehlerton", async () => {
      await laufendeStilleSitzung();
      await fluss(LEERLAUF_MS - 60_000);
      expect(aktuell().presence).toBe("idle");
      await fluss(120_000);
      expect(aktuell().presence).toBe("offline");
      expect(letzter().disconnect).toHaveBeenCalled();
      expect(aktuell().error?.art).toBe("hinweis");
      expect(aktuell().error?.text).toMatch(/20 Minuten/);
      expect(aktuell().stillStart).toEqual(STILL_AUS);
    });

    it("jede Eingabe beginnt die Ruhezeit neu", async () => {
      const { result } = await laufendeStilleSitzung();
      await fluss(LEERLAUF_MS - 60_000);
      await act(async () => result.current.eingabeMelden?.());
      await fluss(LEERLAUF_MS - 60_000);
      expect(aktuell().presence).toBe("idle");
      await fluss(120_000);
      expect(aktuell().presence).toBe("offline");
    });

    it("nie trennen, solange die PC-Steuerung an ist", async () => {
      await laufendeStilleSitzung({ pcSteuerung: "an" });
      await fluss(3 * LEERLAUF_MS);
      expect(aktuell().presence).toBe("idle");
      expect(letzter().disconnect).not.toHaveBeenCalled();
    });

    it("nie trennen, solange eine Freigabekarte offen ist", async () => {
      await laufendeStilleSitzung();
      wamo.set((s) => ({ ...s, approvals: [{ job: "pc", projekt: "x", id: "1", grund: "g", detail: "d" }] }));
      await fluss(3 * LEERLAUF_MS);
      expect(aktuell().presence).toBe("idle");
    });

    it("nie trennen, solange ein Entwicklungsauftrag läuft", async () => {
      await laufendeStilleSitzung();
      wamo.set((s) => ({ ...s, vorgaenge: [{ id: "v1", titel: "Analyse", art: "entwicklung", status: "laeuft" } as never] }));
      await fluss(3 * LEERLAUF_MS);
      expect(aktuell().presence).toBe("idle");
    });

    it("eine mit 'Gespräch starten' begonnene Sitzung bleibt unverändert", async () => {
      const { result } = renderHook(() => useWamo());
      await act(async () => {
        void result.current.connect();
        await vi.advanceTimersByTimeAsync(10);
      });
      await bereit(letzter());
      await fluss(3 * LEERLAUF_MS);
      expect(aktuell().presence).toBe("idle");
      expect(letzter().disconnect).not.toHaveBeenCalled();
    });

    it("nach Sprechen gilt die Sitzung als Gespräch: kein Leerlauf mehr", async () => {
      const { result } = await laufendeStilleSitzung();
      await act(async () => result.current.sprechen?.());
      await fluss(3 * LEERLAUF_MS);
      expect(aktuell().presence).toBe("idle");
    });
  });

  describe("Übernahme durch ein anderes Fenster (Befund M6)", () => {
    it("nach der Ablösung startet das Tippen hier nicht wieder von selbst, der Hinweis nennt das andere Fenster", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      result.current.stillStarten?.();
      await fluss();
      const c = letzter();
      await bereit(c);
      await act(async () => {
        c.opts.callbacks.onServerMessage({ type: "sitzung_beendet", grund: GRUND_UEBERNOMMEN });
        c.opts.callbacks.onDisconnected();
      });
      expect(aktuell().presence).toBe("offline");
      expect(aktuell().stillStart.uebernommen).toBe(true);
      const vorher = clients().length;
      expect(result.current.stillStarten?.()).toBe(false);
      await fluss();
      expect(clients()).toHaveLength(vorher);
      expect(aktuell().error?.text).toBe(HINWEIS_ANDERES_FENSTER);
    });

    it("'Hier übernehmen' (Klick, normales Gespräch) startet und hebt die Sperre auf", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      wamo.set((s) => ({ ...s, stillStart: { ...STILL_AUS, uebernommen: true } }));
      await act(async () => {
        void result.current.connect();
        await vi.advanceTimersByTimeAsync(10);
      });
      expect(clients()).toHaveLength(1);
      expect(aktuell().stillStart.uebernommen).toBe(false);
    });

    it("ein anderer Beendigungsgrund (zum Beispiel Höchstdauer) sperrt das Tippen nicht", async () => {
      einschalten();
      const { result } = renderHook(() => useWamo());
      result.current.stillStarten?.();
      await fluss();
      const c = letzter();
      await bereit(c);
      await act(async () => {
        c.opts.callbacks.onServerMessage({ type: "sitzung_beendet", grund: "Die maximale Sitzungsdauer ist erreicht." });
        c.opts.callbacks.onDisconnected();
      });
      expect(aktuell().stillStart.uebernommen).toBe(false);
      expect(result.current.stillStarten?.()).toBe(true);
    });
  });

  describe("Fehlerfälle", () => {
    it("Kern nicht erreichbar: Meldung, wieder offline, kein stiller Zustand, kein Dauerfeuer", async () => {
      einschalten();
      h.start = () => Promise.reject(new Error("weg"));
      const { result } = renderHook(() => useWamo());
      result.current.stillStarten?.();
      await fluss();
      expect(aktuell().presence).toBe("offline");
      expect(aktuell().error?.art).toBe("verbindung");
      expect(aktuell().stillStart).toEqual(STILL_AUS);
      expect(clients()).toHaveLength(1);
      await fluss(60_000);
      expect(clients()).toHaveLength(1);
    });

    it("Abbruch beim Verbinden: ein spät fertiger Start wird sofort wieder geschlossen", async () => {
      einschalten();
      let fertig: () => void = () => undefined;
      h.start = () => new Promise<void>((r) => (fertig = r));
      const { result } = renderHook(() => useWamo());
      result.current.stillStarten?.();
      await fluss();
      await act(async () => result.current.disconnect());
      expect(aktuell().presence).toBe("offline");
      fertig();
      await fluss();
      expect(letzter().disconnect).toHaveBeenCalled();
      expect(aktuell().presence).toBe("offline");
    });

    it("höchstens 6 stille Starts je 10 Minuten, dann ein Hinweis statt Dauerfeuer", async () => {
      einschalten();
      h.start = () => Promise.reject(new Error("weg"));
      const { result } = renderHook(() => useWamo());
      for (let i = 0; i < STARTS_MAX; i++) {
        expect(result.current.stillStarten?.()).toBe(true);
        await fluss(1000);
      }
      expect(result.current.stillStarten?.()).toBe(false);
      expect(aktuell().error?.text).toBe(HINWEIS_ZU_OFT);
      expect(clients()).toHaveLength(STARTS_MAX);
    });
  });
});
