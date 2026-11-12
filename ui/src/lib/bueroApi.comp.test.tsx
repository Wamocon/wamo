// Test des Abrufs und des Schaltens der Büro-Karte (lib/bueroApi.ts): Der Netzabruf (lib/kernApi.ts) ist durch eine Attrappe ersetzt.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bueroAttrappeSetzen, bueroLaden, bueroSteuern } from "@/lib/bueroApi";
import { KernFehler, kernJson } from "@/lib/kernApi";

vi.mock("@/lib/kernApi", async (original) => ({ ...(await original<typeof import("@/lib/kernApi")>()), kernJson: vi.fn() }));

describe("bueroSteuern", () => {
  beforeEach(() => {
    vi.mocked(kernJson).mockReset();
    bueroAttrappeSetzen(null);
  });

  it("schickt POST /buero/steuern mit Kennung und Aktion und sonst nichts", async () => {
    vi.mocked(kernJson).mockResolvedValue({ ok: true, geraet: "tv-1", aktion: "an" });
    await bueroSteuern("tv-1", "an");
    expect(kernJson).toHaveBeenCalledTimes(1);
    expect(kernJson).toHaveBeenCalledWith("/buero/steuern", "POST", { geraet: "tv-1", aktion: "an" });
  });

  it.each([[null], [undefined], ["ok"], [{}], [{ ok: false }], [{ ok: "true" }], [[true]]])("eine Antwort ohne ok: true (%j) ist ein Fehler 502", async (antwort) => {
    vi.mocked(kernJson).mockResolvedValue(antwort);
    await expect(bueroSteuern("tv-1", "aus")).rejects.toMatchObject({ status: 502 });
  });

  it("Fehler des Kerns gehen unverändert an den Aufrufer (Status und ruhiger Satz)", async () => {
    vi.mocked(kernJson).mockRejectedValue(new KernFehler(409, "Das geht mit diesem Gerät nicht"));
    await expect(bueroSteuern("tv-1", "an")).rejects.toMatchObject({ status: 409, message: "Das geht mit diesem Gerät nicht" });
  });

  it("in der Vorschau (Attrappe gesetzt) geht nichts ins Netz", async () => {
    bueroAttrappeSetzen(async () => ({ geraete: [], unbekannt: null, stand: "" }));
    await bueroSteuern("tv-1", "an");
    expect(kernJson).not.toHaveBeenCalled();
  });
});

describe("bueroLaden", () => {
  beforeEach(() => {
    vi.mocked(kernJson).mockReset();
    bueroAttrappeSetzen(null);
  });

  it("liest GET /buero/status und übernimmt aktionen nur aus der festen Liste", async () => {
    vi.mocked(kernJson).mockResolvedValue({
      geraete: [{ id: "tv-1", name: "TV", gruppe: "medien", ampel: "ok", text: "aus", tipp: "", aktionen: ["an", "lautstaerke", "x"] }],
      unbekannt: null,
      stand: "2026-11-12T08:00:00Z",
    });
    const stand = await bueroLaden();
    expect(kernJson).toHaveBeenCalledWith("/buero/status");
    expect(stand.geraete[0].aktionen).toEqual(["an"]);
  });
});
