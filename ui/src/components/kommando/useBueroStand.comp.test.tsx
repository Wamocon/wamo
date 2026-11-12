// Komponententest des Hooks useBueroStand (Abfrage der Büro-Karte): Sichtbarkeit, Takt, Stopp, Abstand, Aufräumen.
// Der Netzabruf (lib/bueroApi.ts) ist durch eine Attrappe ersetzt, die Uhr ist falsch (vi.useFakeTimers).
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bueroLaden } from "@/lib/bueroApi";
import { KernFehler } from "@/lib/kernApi";
import type { BueroStand } from "./bueroKarteModell";
import { bueroEpoche, bueroNachSchalten, bueroStandVergessen, useBueroStand, useBueroStandHalten } from "./useBueroStand";

vi.mock("@/lib/bueroApi", () => ({ bueroLaden: vi.fn() }));

const laden = vi.mocked(bueroLaden);

const STAND: BueroStand = {
  geraete: [{ id: "srv-1", name: "Server", gruppe: "server", ampel: "ok", text: "läuft", tipp: "" }],
  unbekannt: null,
  stand: "2026-11-10T08:00:00Z",
};

const SEK = 1000;

/** Setzt document.visibilityState (jsdom: "visible") und meldet es wie der Browser mit dem Ereignis visibilitychange. */
function sichtbarkeitSetzen(wert: "visible" | "hidden", ereignis = true) {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => wert });
  if (ereignis) document.dispatchEvent(new Event("visibilitychange"));
}

/** Lässt die falsche Uhr vorrücken und die Antworten der Attrappe ankommen, im act, weil der Hook dabei seinen Zustand setzt. */
const vor = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

/** Ein Abruf, der erst antwortet, wenn der Test es will (um Zeitpunkte mitten im Abruf zu prüfen). */
function aufgeschoben() {
  let ok!: (stand: BueroStand) => void;
  const p = new Promise<BueroStand>((res) => {
    ok = res;
  });
  return { p, ok };
}

describe("useBueroStand", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-10T08:00:00Z"));
    laden.mockReset();
    bueroStandVergessen(); // Stand, Takt und laufender Abruf gehören dem Modul, nicht dem Test
    Reflect.deleteProperty(document, "visibilityState");
  });
  afterEach(() => {
    bueroStandVergessen();
    Reflect.deleteProperty(document, "visibilityState");
    vi.useRealTimers();
  });

  describe("Sichtbarkeit", () => {
    it("fragt beim Einhängen einer sichtbaren Seite sofort ab und liefert den Stand", async () => {
      laden.mockResolvedValue(STAND);
      const { result } = renderHook(() => useBueroStand());
      expect(result.current).toEqual({ stand: null, fehler: null, geladen: false });
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(1);
      expect(result.current).toEqual({ stand: STAND, fehler: null, geladen: true });
    });

    it("fragt bei verborgener Seite nicht ab, auch nach Minuten nicht", async () => {
      sichtbarkeitSetzen("hidden", false);
      laden.mockResolvedValue(STAND);
      const { result } = renderHook(() => useBueroStand());
      await vor(10 * 60 * SEK);
      expect(laden).not.toHaveBeenCalled();
      expect(result.current.geladen).toBe(false);
    });

    it("fragt beim Sichtbarwerden ab, wenn die Seite beim Einhängen verborgen war (Ereignis visibilitychange)", async () => {
      sichtbarkeitSetzen("hidden", false);
      laden.mockResolvedValue(STAND);
      const { result } = renderHook(() => useBueroStand());
      await vor(60 * SEK);
      expect(laden).not.toHaveBeenCalled();
      sichtbarkeitSetzen("visible");
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(1);
      expect(result.current.stand).toEqual(STAND);
    });

    it("ruht, sobald die Seite verborgen wird, und holt beim Zurückkehren nach, was fällig ist", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(1);
      sichtbarkeitSetzen("hidden");
      await vor(5 * 60 * SEK);
      expect(laden).toHaveBeenCalledTimes(1);
      sichtbarkeitSetzen("visible");
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(2); // der letzte Versuch liegt länger als 30 Sekunden zurück: sofort
    });

    it("hält beim Zurückkehren die 30 Sekunden seit dem letzten Versuch ein", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      await vor(10 * SEK);
      sichtbarkeitSetzen("hidden");
      await vor(10 * SEK);
      sichtbarkeitSetzen("visible"); // 20 Sekunden nach dem Abruf
      await vor(9 * SEK + 999);
      expect(laden).toHaveBeenCalledTimes(1);
      await vor(1);
      expect(laden).toHaveBeenCalledTimes(2);
    });
  });

  describe("Takt", () => {
    it("fragt alle 30 Sekunden ab", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      await vor(30 * SEK - 1);
      expect(laden).toHaveBeenCalledTimes(1);
      await vor(1);
      expect(laden).toHaveBeenCalledTimes(2);
      await vor(30 * SEK);
      expect(laden).toHaveBeenCalledTimes(3);
    });

    it("lässt bei einem Fehler den letzten Stand stehen und meldet den Status", async () => {
      laden.mockResolvedValueOnce(STAND).mockRejectedValue(new KernFehler(503, "nicht erreichbar"));
      const { result } = renderHook(() => useBueroStand());
      await vor(0);
      await vor(30 * SEK);
      expect(result.current).toEqual({ stand: STAND, fehler: 503, geladen: true });
    });

    it("keine doppelten Abrufe bei Reiterwechsel: Aushängen und Einhängen innerhalb der 30 Sekunden fragt nicht neu", async () => {
      laden.mockResolvedValue(STAND);
      const erste = renderHook(() => useBueroStand());
      await vor(0);
      erste.unmount();
      for (let i = 0; i < 5; i++) {
        await vor(4 * SEK);
        const reiter = renderHook(() => useBueroStand());
        // der Stand des letzten Besuchs steht sofort da, ohne Ladeanzeige
        expect(reiter.result.current).toEqual({ stand: STAND, fehler: null, geladen: true });
        await vor(0);
        reiter.unmount();
      }
      expect(laden).toHaveBeenCalledTimes(1);
    });

    it("setzt den Takt nach einem Reiterwechsel nicht von vorn an: der nächste Abruf folgt 30 Sekunden nach dem letzten", async () => {
      laden.mockResolvedValue(STAND);
      const erste = renderHook(() => useBueroStand());
      await vor(0);
      erste.unmount();
      await vor(10 * SEK);
      renderHook(() => useBueroStand());
      await vor(20 * SEK - 1);
      expect(laden).toHaveBeenCalledTimes(1);
      await vor(1);
      expect(laden).toHaveBeenCalledTimes(2);
    });

    it("teilt einen laufenden Abruf mit jeder weiteren Instanz", async () => {
      const offen = aufgeschoben();
      laden.mockReturnValue(offen.p);
      const a = renderHook(() => useBueroStand());
      const b = renderHook(() => useBueroStand());
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(1);
      await act(async () => {
        offen.ok(STAND);
      });
      expect(a.result.current.stand).toEqual(STAND);
      expect(b.result.current.stand).toEqual(STAND);
      expect(laden).toHaveBeenCalledTimes(1);
    });
  });

  describe("Stopp bei 401 und 403", () => {
    it.each([401, 403])("hört nach %i auf zu fragen", async (status) => {
      laden.mockRejectedValue(new KernFehler(status, "nicht erlaubt"));
      const { result } = renderHook(() => useBueroStand());
      await vor(0);
      expect(result.current).toEqual({ stand: null, fehler: status, geladen: true });
      await vor(10 * 60 * SEK);
      expect(laden).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    });

    it.each([401, 403])("fragt nach %i erst wieder, wenn die Seite wieder sichtbar wird", async (status) => {
      laden.mockRejectedValueOnce(new KernFehler(status, "nicht erlaubt")).mockResolvedValue(STAND);
      const { result } = renderHook(() => useBueroStand());
      await vor(0);
      await vor(10 * 60 * SEK);
      expect(laden).toHaveBeenCalledTimes(1);
      sichtbarkeitSetzen("hidden");
      await vor(60 * SEK);
      expect(laden).toHaveBeenCalledTimes(1); // verborgen: weiter Ruhe
      sichtbarkeitSetzen("visible");
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(2);
      expect(result.current).toEqual({ stand: STAND, fehler: null, geladen: true });
      await vor(30 * SEK); // und der Takt läuft wieder
      expect(laden).toHaveBeenCalledTimes(3);
    });

    it("fragt nach 401 wieder, wenn sich jemand neu anmeldet (Abmelden vergisst den Zustand, die Karte hängt neu ein)", async () => {
      laden.mockRejectedValueOnce(new KernFehler(401, "abgelaufen")).mockResolvedValue(STAND);
      const alt = renderHook(() => useBueroStand());
      await vor(0);
      alt.unmount();
      bueroStandVergessen();
      const neu = renderHook(() => useBueroStand());
      expect(neu.result.current).toEqual({ stand: null, fehler: null, geladen: false });
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(2);
      expect(neu.result.current.stand).toEqual(STAND);
    });
  });

  describe("Abstand bei Fehlern", () => {
    const ausfaelle: [string, () => unknown][] = [
      ["503", () => new KernFehler(503, "Dienst nicht erreichbar")],
      ["nicht erreichbar (Status 0)", () => new KernFehler(0, "Kern nicht erreichbar")],
      ["ein Fehler ohne Status", () => new TypeError("Failed to fetch")],
    ];

    it.each(ausfaelle)("wächst bei %s: 30, 60, 120, 240, dann höchstens 300 Sekunden", async (_name, fehler) => {
      laden.mockImplementation(() => Promise.reject(fehler()));
      renderHook(() => useBueroStand());
      await vor(0);
      let aufrufe = 1;
      expect(laden).toHaveBeenCalledTimes(aufrufe);
      for (const abstand of [30, 60, 120, 240, 300, 300]) {
        await vor(abstand * SEK - 1);
        expect(laden).toHaveBeenCalledTimes(aufrufe); // eine Millisekunde vor dem Abstand: noch Ruhe
        await vor(1);
        aufrufe += 1;
        expect(laden).toHaveBeenCalledTimes(aufrufe);
      }
    });

    it.each([429, 500, 502])("wächst bei %i nicht: bleibt bei 30 Sekunden", async (status) => {
      laden.mockRejectedValue(new KernFehler(status, "Fehler"));
      renderHook(() => useBueroStand());
      await vor(0);
      for (let n = 2; n <= 5; n++) {
        await vor(30 * SEK);
        expect(laden).toHaveBeenCalledTimes(n);
      }
    });

    it("kehrt nach einem gelungenen Abruf zu 30 Sekunden zurück", async () => {
      laden
        .mockRejectedValueOnce(new KernFehler(503, "weg"))
        .mockRejectedValueOnce(new KernFehler(503, "weg"))
        .mockResolvedValue(STAND);
      const { result } = renderHook(() => useBueroStand());
      await vor(0); // Fehler 1, danach 30 Sekunden
      await vor(30 * SEK); // Fehler 2, danach 60 Sekunden
      await vor(60 * SEK); // gelungen
      expect(laden).toHaveBeenCalledTimes(3);
      expect(result.current.fehler).toBeNull();
      await vor(30 * SEK - 1);
      expect(laden).toHaveBeenCalledTimes(3);
      await vor(1);
      expect(laden).toHaveBeenCalledTimes(4);
    });

    it("beginnt das Wachstum beim Sichtbarwerden der Seite von vorn", async () => {
      laden.mockRejectedValue(new KernFehler(503, "weg"));
      renderHook(() => useBueroStand());
      await vor(0); // Fehler 1
      await vor(30 * SEK); // Fehler 2
      await vor(60 * SEK); // Fehler 3, nun 120 Sekunden bis zum nächsten
      expect(laden).toHaveBeenCalledTimes(3);
      await vor(10 * SEK);
      sichtbarkeitSetzen("hidden");
      sichtbarkeitSetzen("visible"); // 10 Sekunden nach dem letzten Versuch: wieder der normale Takt
      await vor(20 * SEK - 1);
      expect(laden).toHaveBeenCalledTimes(3);
      await vor(1);
      expect(laden).toHaveBeenCalledTimes(4);
    });
  });

  describe("Aufräumen", () => {
    it("hängt beim Aushängen denselben Listener ab, den es eingehängt hat, und lässt keinen Zeitgeber zurück", async () => {
      const dazu = vi.spyOn(document, "addEventListener");
      const weg = vi.spyOn(document, "removeEventListener");
      laden.mockResolvedValue(STAND);
      const { unmount } = renderHook(() => useBueroStand());
      await vor(0);
      expect(vi.getTimerCount()).toBe(1); // der nächste Abruf in 30 Sekunden
      const eingehaengt = dazu.mock.calls.filter(([art]) => art === "visibilitychange");
      expect(eingehaengt).toHaveLength(1);
      unmount();
      const abgehaengt = weg.mock.calls.filter(([art]) => art === "visibilitychange");
      expect(abgehaengt).toHaveLength(1);
      expect(abgehaengt[0][1]).toBe(eingehaengt[0][1]);
      expect(vi.getTimerCount()).toBe(0);
    });

    it("fragt nach dem Aushängen nie mehr, weder nach Zeit noch nach dem Ereignis visibilitychange", async () => {
      laden.mockResolvedValue(STAND);
      const { unmount } = renderHook(() => useBueroStand());
      await vor(0);
      unmount();
      sichtbarkeitSetzen("hidden");
      sichtbarkeitSetzen("visible");
      await vor(10 * 60 * SEK);
      expect(laden).toHaveBeenCalledTimes(1);
    });

    it("setzt nach dem Aushängen keinen Zustand mehr und plant nichts neu, wenn die Antwort erst dann eintrifft", async () => {
      const fehlerAusgabe = vi.spyOn(console, "error").mockImplementation(() => undefined);
      const offen = aufgeschoben();
      laden.mockReturnValue(offen.p);
      const { result, unmount } = renderHook(() => useBueroStand());
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(1);
      unmount();
      await act(async () => {
        offen.ok(STAND);
      });
      expect(result.current).toEqual({ stand: null, fehler: null, geladen: false }); // der letzte Stand vor dem Aushängen
      expect(vi.getTimerCount()).toBe(0);
      await vor(10 * 60 * SEK);
      expect(laden).toHaveBeenCalledTimes(1);
      expect(fehlerAusgabe).not.toHaveBeenCalled();
    });
  });

  describe("useBueroStandHalten", () => {
    it("vergisst den Stand, wenn die Hauptansicht aushängt (Abmelden): der nächste Besuch beginnt leer und fragt neu", async () => {
      laden.mockResolvedValue(STAND);
      const halten = renderHook(() => useBueroStandHalten());
      const karte = renderHook(() => useBueroStand());
      await vor(0);
      expect(karte.result.current.stand).toEqual(STAND);
      karte.unmount();
      halten.unmount();
      const naechster = renderHook(() => useBueroStand());
      expect(naechster.result.current).toEqual({ stand: null, fehler: null, geladen: false });
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(2);
    });
  });

  describe("bueroNachSchalten", () => {
    const NEU: BueroStand = { ...STAND, geraete: [{ ...STAND.geraete[0], text: "neu" }] };

    it("holt nach einem Schaltvorgang den Stand nach 2,5 Sekunden neu und gibt ihn an die Karte", async () => {
      laden.mockResolvedValueOnce(STAND).mockResolvedValue(NEU);
      const karte = renderHook(() => useBueroStand());
      await vor(0);
      expect(karte.result.current.stand).toEqual(STAND);
      bueroNachSchalten("lauter");
      await vor(2499);
      expect(laden).toHaveBeenCalledTimes(1);
      await vor(1);
      expect(laden).toHaveBeenCalledTimes(2);
      expect(karte.result.current.stand).toEqual(NEU);
      await vor(20 * SEK); // Lauter: kein zweiter Abruf, nur der normale Takt nach 30 Sekunden vom zweiten
      expect(laden).toHaveBeenCalledTimes(2);
    });

    it("beim Einschalten kommt nach 10 Sekunden noch ein Abruf (der Fernseher braucht länger)", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      bueroNachSchalten("an");
      await vor(2500);
      expect(laden).toHaveBeenCalledTimes(2);
      await vor(7500);
      expect(laden).toHaveBeenCalledTimes(3);
    });

    it("Schaltvorgänge kurz hintereinander teilen sich einen Abruf (Zeitpunkte nah beieinander entfallen)", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      bueroNachSchalten("lauter");
      await vor(1000);
      bueroNachSchalten("lauter"); // Ziel 3,5 s statt 2,5 s: näher als 2 s, entfällt
      await vor(1000);
      bueroNachSchalten("lauter"); // Ziel 4,5 s: genau 2 s nach dem ersten, kommt dazu
      await vor(5000);
      expect(laden).toHaveBeenCalledTimes(3);
    });

    it("höchstens drei Neu-Abrufe je Minute, damit der Takt unter den 6 des Kerns bleibt", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      for (let i = 0; i < 8; i++) {
        bueroNachSchalten("lauter");
        await vor(3000);
      }
      // 1 Abruf beim Einhängen, 3 Neu-Abrufe (Grenze), dazu der Takt nach 30 Sekunden
      await vor(10 * SEK);
      expect(laden.mock.calls.length).toBeLessThanOrEqual(1 + 3 + 1);
      expect(laden.mock.calls.length).toBeGreaterThanOrEqual(1 + 3);
    });

    it("das Einschalten behält seinen Abruf nach 10 Sekunden, auch wenn danach ein anderes Gerät geschaltet wird", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      bueroNachSchalten("an"); // Abrufe bei 2,5 und 10 Sekunden
      await vor(4000);
      bueroNachSchalten("lauter"); // ersetzt nichts, ergänzt höchstens
      await vor(10 * SEK);
      const nach = laden.mock.calls.length;
      expect(nach).toBeGreaterThanOrEqual(1 + 3); // Einhängen, 2,5 s, 6,5 s und 10 s
    });

    it("nach dem frischen Stand zählt der Takt neu: kein überflüssiger Abruf zur alten Zeit", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0); // Abruf 1 bei 0, Takt bei 30 s
      await vor(20 * SEK);
      bueroNachSchalten("lauter"); // Neu-Abruf bei 22,5 s, danach Takt bei 52,5 s
      await vor(2500);
      expect(laden).toHaveBeenCalledTimes(2);
      await vor(10 * SEK); // 32,5 s: der alte Takt bei 30 s wäre jetzt fällig gewesen
      expect(laden).toHaveBeenCalledTimes(2);
      await vor(20 * SEK); // 52,5 s
      expect(laden).toHaveBeenCalledTimes(3);
    });

    it("läuft gerade ein Takt-Abruf (Stand von vor dem Schalten), folgt danach ein frischer", async () => {
      const erster = aufgeschoben();
      laden.mockReturnValueOnce(erster.p).mockResolvedValue(NEU);
      const karte = renderHook(() => useBueroStand());
      await vor(0);
      bueroNachSchalten("lauter");
      await vor(2500); // der Takt-Abruf hängt noch, der Neu-Abruf wartet auf ihn
      expect(laden).toHaveBeenCalledTimes(1);
      await act(async () => {
        erster.ok(STAND);
      });
      await vor(0);
      expect(laden).toHaveBeenCalledTimes(2); // frisch, nicht dasselbe Ergebnis noch einmal
      expect(karte.result.current.stand).toEqual(NEU);
    });

    it("beim Abmelden (Stand vergessen) fällt der geplante Abruf weg", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      bueroNachSchalten("an");
      bueroStandVergessen();
      await vor(15 * SEK);
      expect(laden).toHaveBeenCalledTimes(1);
    });

    it("wurde nach dem Schalten abgemeldet (Epoche geändert), passiert nichts", async () => {
      laden.mockResolvedValue(STAND);
      renderHook(() => useBueroStand());
      await vor(0);
      const vorher = bueroEpoche();
      bueroStandVergessen();
      bueroNachSchalten("an", vorher);
      await vor(15 * SEK);
      expect(laden).toHaveBeenCalledTimes(1);
    });

    it("meldet sich jemand ab, während der Neu-Abruf läuft, geht dessen Ergebnis an keine Karte", async () => {
      const spaet = aufgeschoben();
      laden.mockResolvedValueOnce(STAND).mockReturnValueOnce(spaet.p);
      const karte = renderHook(() => useBueroStand());
      await vor(0);
      bueroNachSchalten("lauter");
      await vor(2500); // Neu-Abruf läuft
      bueroStandVergessen();
      await act(async () => {
        spaet.ok(NEU);
      });
      expect(karte.result.current.stand).toEqual(STAND);
    });

    it("eine ausgehängte Karte bekommt nichts mehr", async () => {
      laden.mockResolvedValueOnce(STAND).mockResolvedValue(NEU);
      const karte = renderHook(() => useBueroStand());
      await vor(0);
      karte.unmount();
      bueroNachSchalten("lauter");
      await vor(3000);
      expect(karte.result.current.stand).toEqual(STAND);
    });
  });
});
