// Komponententest der Aktionen am Stimmabdruck in useWamo (lib/useWamo.ts, lib/stimme.ts): Start nur aus der Einwilligung und nur mit der
// Version des Textes, den der Kern geschickt hat, Abbrechen, Löschen und das Zurücksetzen mit der Sitzung. Pipecat-Client und Transport sind
// durch Attrappen ersetzt, es gibt kein Netz und kein Mikrofon.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Rueckrufe = Record<string, (...a: unknown[]) => void>;
type AttrappenClient = {
  opts: { callbacks: Rueckrufe };
  sendClientMessage: ReturnType<typeof vi.fn>;
};

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
import { abdruckAnsichtOeffnen, resetWamo, wamo } from "@/lib/wamo";

const NUTZER = "0b7f3c1e-2d4a-4c55-9a10-1a2b3c4d5e6f";
const clients = () => h.clients as AttrappenClient[];
const letzter = () => clients()[clients().length - 1];
const aktuell = () => wamo.get();

const fluss = (ms = 10) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

/** Eine Sitzung ist bereit: Client da, "Bot bereit" gemeldet. */
async function sitzung() {
  nutzerSetzen({ id: NUTZER, email: "niko@wamocon.com" });
  const r = renderHook(() => useWamo());
  expect(r.result.current.stillStarten?.()).toBe(true);
  await fluss();
  const c = letzter();
  await act(async () => {
    c.opts.callbacks.onBotReady({ version: "2.0.0" });
    wamo.set((s) => ({ ...s, betrieb: { modus: "buero", modelle: {}, pc: "aus" } }));
  });
  return { ...r, c };
}

const vomKern = (c: AttrappenClient, nachricht: Record<string, unknown>) =>
  act(async () => {
    c.opts.callbacks.onServerMessage(nachricht);
  });

const EINWILLIGUNG = { type: "stimmabdruck_einwilligung", version: "stimme-1", text: "W.A.I.S. speichert aus deiner Stimme einen Abdruck." };
const stimmabdruckGesendet = (c: AttrappenClient) => c.sendClientMessage.mock.calls.filter((a) => a[0] === "stimmabdruck");

describe("useWamo, Stimmabdruck", () => {
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

  async function bereitZumEinlernen() {
    const s = await sitzung();
    await vomKern(s.c, { type: "stimme", status: "fehlt", modus: "streng" });
    await vomKern(s.c, EINWILLIGUNG);
    expect(abdruckAnsichtOeffnen()).toBe(true);
    return s;
  }

  describe("Start", () => {
    it("schickt den Start mit der Version des Einwilligungstextes und führt die Ansicht in die Phase start", async () => {
      const { result, c } = await bereitZumEinlernen();
      let ok = false;
      act(() => {
        ok = result.current.stimmabdruck!.start("stimme-1");
      });
      expect(ok).toBe(true);
      expect(c.sendClientMessage).toHaveBeenCalledWith("stimmabdruck", { aktion: "start", einwilligung: "stimme-1" });
      expect(aktuell().abdruck?.phase).toBe("start");
    });

    it("startet nicht mit einer anderen Version als der, die der Kern geschickt hat", async () => {
      const { result, c } = await bereitZumEinlernen();
      expect(result.current.stimmabdruck!.start("stimme-2")).toBe(false);
      expect(stimmabdruckGesendet(c)).toHaveLength(0);
      expect(aktuell().abdruck?.phase).toBe("einwilligung");
    });

    it("startet nicht ohne Einwilligungstext vom Kern", async () => {
      const s = await sitzung();
      await vomKern(s.c, { type: "stimme", status: "fehlt", modus: "streng" });
      abdruckAnsichtOeffnen();
      expect(s.result.current.stimmabdruck!.start("stimme-1")).toBe(false);
      expect(stimmabdruckGesendet(s.c)).toHaveLength(0);
    });

    it("startet nicht, wenn die Einlernansicht nicht in der Einwilligung steht (zum Beispiel schon in der Aufnahme)", async () => {
      const { result, c } = await bereitZumEinlernen();
      act(() => void result.current.stimmabdruck!.start("stimme-1"));
      expect(stimmabdruckGesendet(c)).toHaveLength(1);
      expect(result.current.stimmabdruck!.start("stimme-1")).toBe(false);
      expect(stimmabdruckGesendet(c)).toHaveLength(1);
    });

    it("startet nicht, wenn die Stimmerkennung aus ist", async () => {
      const { result, c } = await bereitZumEinlernen();
      await vomKern(c, { type: "stimme", status: "aus", modus: "aus" });
      expect(result.current.stimmabdruck!.start("stimme-1")).toBe(false);
      expect(stimmabdruckGesendet(c)).toHaveLength(0);
    });
  });

  describe("Abbrechen", () => {
    it("in der Einwilligung schließt nur die Ansicht, der Kern erfährt nichts", async () => {
      const { result, c } = await bereitZumEinlernen();
      act(() => void result.current.stimmabdruck!.abbrechen());
      expect(aktuell().abdruck).toBeNull();
      expect(stimmabdruckGesendet(c)).toHaveLength(0);
    });

    it("während der Aufnahme schickt es abbrechen und schließt die Ansicht", async () => {
      const { result, c } = await bereitZumEinlernen();
      act(() => void result.current.stimmabdruck!.start("stimme-1"));
      await vomKern(c, { type: "stimmabdruck", status: "aufnahme", sekunden: 8.4, ziel: 20 });
      act(() => void result.current.stimmabdruck!.abbrechen());
      expect(c.sendClientMessage).toHaveBeenLastCalledWith("stimmabdruck", { aktion: "abbrechen" });
      expect(aktuell().abdruck).toBeNull();
    });

    it("nach dem Ergebnis (fertig) schließt es nur noch die Ansicht", async () => {
      const { result, c } = await bereitZumEinlernen();
      act(() => void result.current.stimmabdruck!.start("stimme-1"));
      await vomKern(c, { type: "stimmabdruck", status: "fertig", sekunden: 20 });
      const vorher = stimmabdruckGesendet(c).length;
      act(() => void result.current.stimmabdruck!.abbrechen());
      expect(stimmabdruckGesendet(c)).toHaveLength(vorher);
      expect(aktuell().abdruck).toBeNull();
    });
  });

  describe("Löschen", () => {
    it("schickt loeschen, wenn ein Abdruck da ist", async () => {
      const { result, c } = await sitzung();
      await vomKern(c, { type: "stimme", status: "bereit", modus: "streng" });
      let ok = false;
      act(() => {
        ok = result.current.stimmabdruck!.loeschen();
      });
      expect(ok).toBe(true);
      expect(c.sendClientMessage).toHaveBeenCalledWith("stimmabdruck", { aktion: "loeschen" });
    });

    it("schickt nichts, wenn es keinen Abdruck gibt", async () => {
      const { result, c } = await sitzung();
      await vomKern(c, { type: "stimme", status: "fehlt", modus: "streng" });
      expect(result.current.stimmabdruck!.loeschen()).toBe(false);
      expect(stimmabdruckGesendet(c)).toHaveLength(0);
    });
  });

  it("ein Einwilligungstext, den die Oberfläche ablehnt (zu lang), wird vermerkt statt still verworfen", async () => {
    const s = await sitzung();
    await vomKern(s.c, { type: "stimmabdruck_einwilligung", version: "stimme-1", text: "x".repeat(5000) });
    expect(aktuell().einwilligung).toBeNull();
    expect(aktuell().einwilligungAbgelehnt).toBe(true);
  });

  it("mit dem Ende der Sitzung verschwinden Stand, Einlernansicht und Einwilligung", async () => {
    const { result } = await bereitZumEinlernen();
    expect(aktuell().stimme.status).toBe("fehlt");
    await act(async () => {
      await result.current.disconnect();
    });
    await fluss();
    expect(aktuell().stimme.status).toBeNull();
    expect(aktuell().abdruck).toBeNull();
    expect(aktuell().einwilligung).toBeNull();
  });
});
