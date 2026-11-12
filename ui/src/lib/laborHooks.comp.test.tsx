// Komponententest der drei Halte-Hooks der Hauptansicht mit dem Parameter "aktiv" (Laborsicht, lib/laborPlatz.ts): Ist er aus, ruft keiner
// etwas beim Kern ab (Verlauf, Modellzugang) und berührt keinen Zustand (Büro-Karte); ohne Angabe oder mit true arbeiten sie wie bisher.
// Der Parameter ersetzt einen bedingten Aufruf der Hooks (Regeln der Hooks): Die Reihenfolge der Hooks bleibt in jedem Render gleich.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bueroEpoche, useBueroStandHalten } from "@/components/kommando/useBueroStand";
import { KernFehler } from "@/lib/kernApi";
import { useModellzugangStandHalten } from "@/lib/modellzugangApi";
import { resetWamo } from "@/lib/wamo";
import { useVerlaufHalten } from "@/lib/vorgaengeApi";

const kern = vi.hoisted(() => ({ kernJson: vi.fn() }));
vi.mock("@/lib/kernApi", async (original) => ({ ...(await original<typeof import("@/lib/kernApi")>()), kernJson: kern.kernJson }));

const pfade = () => kern.kernJson.mock.calls.map((c) => c[0]);

beforeEach(() => {
  resetWamo();
  kern.kernJson.mockReset().mockRejectedValue(new KernFehler(503, "aus")); // Fehler bleiben still, es geht um den Aufruf
});

describe("useVerlaufHalten", () => {
  it("holt den Verlauf der Vorgänge, solange nichts anderes gesagt wird", async () => {
    renderHook(() => useVerlaufHalten());
    await waitFor(() => expect(pfade()).toContain("/vorgaenge"));
  });

  it("holt ihn mit aktiv = true ebenso", async () => {
    renderHook(() => useVerlaufHalten(true));
    await waitFor(() => expect(pfade()).toContain("/vorgaenge"));
  });

  it("holt ihn mit aktiv = false nie", async () => {
    const { unmount } = renderHook(() => useVerlaufHalten(false));
    await Promise.resolve();
    unmount();
    expect(kern.kernJson).not.toHaveBeenCalled();
  });
});

describe("useModellzugangStandHalten", () => {
  it("fragt den Stand ab, wenn er fehlt", async () => {
    renderHook(() => useModellzugangStandHalten(undefined));
    await waitFor(() => expect(pfade()).toContain("/modellzugang"));
  });

  it("fragt mit aktiv = false nie ab, auch nicht über den Datenkanal", async () => {
    const ueberKanal = vi.fn(() => true);
    const { unmount } = renderHook(() => useModellzugangStandHalten(ueberKanal, false));
    await Promise.resolve();
    unmount();
    expect(kern.kernJson).not.toHaveBeenCalled();
    expect(ueberKanal).not.toHaveBeenCalled();
  });
});

describe("useBueroStandHalten", () => {
  it("vergisst beim Aushängen den Stand des Profils (Epoche zählt hoch)", () => {
    const vorher = bueroEpoche();
    const { unmount } = renderHook(() => useBueroStandHalten());
    expect(bueroEpoche()).toBe(vorher);
    unmount();
    expect(bueroEpoche()).toBe(vorher + 1);
  });

  it("berührt mit aktiv = false nichts", () => {
    const vorher = bueroEpoche();
    const { unmount } = renderHook(() => useBueroStandHalten(false));
    unmount();
    expect(bueroEpoche()).toBe(vorher);
    expect(kern.kernJson).not.toHaveBeenCalled();
  });
});

describe("Wechsel von aktiv im laufenden Render", () => {
  it("ein späteres aktiv = true holt den Verlauf einmal, ein späteres false gibt ihn frei", async () => {
    const { rerender, unmount } = renderHook(({ aktiv }) => useVerlaufHalten(aktiv), { initialProps: { aktiv: false } });
    expect(kern.kernJson).not.toHaveBeenCalled();
    rerender({ aktiv: true });
    await waitFor(() => expect(pfade().filter((p) => p === "/vorgaenge")).toHaveLength(1));
    rerender({ aktiv: false });
    unmount();
    expect(pfade().filter((p) => p === "/vorgaenge")).toHaveLength(1);
  });
});
