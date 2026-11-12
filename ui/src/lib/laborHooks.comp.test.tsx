// Komponententest der drei Halte-Hooks der Hauptansicht mit dem Parameter "aktiv" (Laborsicht, lib/laborPlatz.ts): Ist er aus, ruft keiner
// etwas beim Kern ab (Verlauf, Modellzugang) und berührt keinen Zustand (Büro-Karte); ohne Angabe oder mit true arbeiten sie wie bisher.
// Der Parameter ersetzt einen bedingten Aufruf der Hooks (Regeln der Hooks): Die Reihenfolge der Hooks bleibt in jedem Render gleich.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bueroEpoche, useBueroStandHalten } from "@/components/kommando/useBueroStand";
import { KernFehler } from "@/lib/kernApi";
import { useModellzugangStandHalten } from "@/lib/modellzugangApi";
import { VORGANG_ARTEN, VORGANG_STATUS, type Vorgang } from "@/lib/nachrichten";
import { resetWamo, wamo } from "@/lib/wamo";
import { useVerlaufHalten, verlaufLeeren, vorgangVerlauf } from "@/lib/vorgaengeApi";

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

const EIN_VORGANG: Vorgang = {
  id: "v1",
  titel: "Ticket vorbereiten",
  art: VORGANG_ARTEN[0],
  status: VORGANG_STATUS[1],
  gestartetMs: 1,
  beendetMs: null,
  gesamt: 0.5,
  restzeitS: null,
  schritte: [],
  kriterien: [],
  runde: null,
  maxRunden: null,
  freigabeId: null,
  canvasId: null,
};

/** Eine Zeile der Schnittstelle GET /wamo/api/vorgaenge (Format wie in vorgaenge.test.ts), gelesen wird sie zur Kennung "v1". */
const ANTWORT_ZEILE = {
  id: "v1",
  titel: "Ticket vorbereiten",
  art: "aufgabe",
  status: "fertig",
  gestartet_ms: 1_000,
  beendet_ms: 5_000,
  gesamt: 1,
  restzeit_s: null,
  schritte: [],
  kriterien: [],
  runde: null,
  max_runden: null,
  freigabe_id: null,
  ergebnis: null,
};

describe("Wechsel von aktiv im laufenden Render: Verlauf", () => {
  beforeEach(() => verlaufLeeren());

  it("ein späteres aktiv = true holt den Verlauf einmal", async () => {
    const { rerender } = renderHook(({ aktiv }) => useVerlaufHalten(aktiv), { initialProps: { aktiv: false } });
    expect(kern.kernJson).not.toHaveBeenCalled();
    rerender({ aktiv: true });
    await waitFor(() => expect(pfade().filter((p) => p === "/vorgaenge")).toHaveLength(1));
  });

  it("ein späteres aktiv = false gibt ihn frei: der Verlauf des Profils ist leer, ohne dass etwas unmountet", async () => {
    const { rerender } = renderHook(({ aktiv }) => useVerlaufHalten(aktiv), { initialProps: { aktiv: true } });
    await waitFor(() => expect(pfade()).toContain("/vorgaenge"));
    act(() => vorgangVerlauf.set(() => [EIN_VORGANG]));
    expect(vorgangVerlauf.get()).toHaveLength(1);
    rerender({ aktiv: false });
    expect(vorgangVerlauf.get()).toEqual([]);
    expect(pfade().filter((p) => p === "/vorgaenge")).toHaveLength(1); // kein neuer Abruf
  });

  /** Ein Abruf, der erst antwortet, wenn der Test es will (Antwort im Format der Schnittstelle). */
  function spaeteAntwort() {
    let antwort!: () => void;
    kern.kernJson.mockReset().mockReturnValue(new Promise((ok) => (antwort = () => ok({ vorgaenge: [ANTWORT_ZEILE] }))));
    return () => act(async () => void (antwort(), await Promise.resolve()));
  }

  it("Gegenprobe: bleibt der Hook aktiv, trägt die Antwort den Verlauf ein", async () => {
    const liefern = spaeteAntwort();
    renderHook(({ aktiv }) => useVerlaufHalten(aktiv), { initialProps: { aktiv: true } });
    await waitFor(() => expect(pfade()).toContain("/vorgaenge"));
    await liefern();
    await waitFor(() => expect(vorgangVerlauf.get().map((v) => v.id)).toEqual(["v1"]));
  });

  it("eine Antwort, die nach dem Ausschalten eintrifft, trägt nichts mehr ein", async () => {
    const liefern = spaeteAntwort();
    const { rerender } = renderHook(({ aktiv }) => useVerlaufHalten(aktiv), { initialProps: { aktiv: true } });
    await waitFor(() => expect(pfade()).toContain("/vorgaenge"));
    rerender({ aktiv: false });
    await liefern();
    expect(vorgangVerlauf.get()).toEqual([]);
  });
});

describe("Wechsel von aktiv im laufenden Render: Modellzugang", () => {
  const anzahl = () => pfade().filter((p) => p === "/modellzugang").length;

  it("ein späteres aktiv = true fragt den Stand einmal ab", async () => {
    const { rerender } = renderHook(({ aktiv }) => useModellzugangStandHalten(undefined, aktiv), { initialProps: { aktiv: false } });
    expect(anzahl()).toBe(0);
    rerender({ aktiv: true });
    await waitFor(() => expect(anzahl()).toBe(1));
  });

  it("ein späteres aktiv = false fragt nicht mehr, auch wenn der Stand fehlt; ein erneutes true holt die Lücke nach", async () => {
    const { rerender } = renderHook(({ aktiv }) => useModellzugangStandHalten(undefined, aktiv), { initialProps: { aktiv: true } });
    await waitFor(() => expect(anzahl()).toBe(1));
    await waitFor(() => expect(wamo.get().modellzugang).not.toBeNull()); // die Antwort (hier ein Fehlerstand) ist im Speicher
    rerender({ aktiv: false });
    act(() => wamo.set((s) => ({ ...s, modellzugang: null }))); // der Stand fehlt wieder, aber niemand soll fragen
    await Promise.resolve();
    expect(anzahl()).toBe(1);
    rerender({ aktiv: true });
    await waitFor(() => expect(anzahl()).toBe(2));
  });
});

describe("Wechsel von aktiv im laufenden Render: Büro-Karte", () => {
  it("ein späteres aktiv = false vergisst den Stand des Profils sofort, ein erneutes true ändert daran nichts", () => {
    const vorher = bueroEpoche();
    const { rerender, unmount } = renderHook(({ aktiv }) => useBueroStandHalten(aktiv), { initialProps: { aktiv: true } });
    rerender({ aktiv: false });
    expect(bueroEpoche()).toBe(vorher + 1);
    rerender({ aktiv: true });
    expect(bueroEpoche()).toBe(vorher + 1);
    unmount();
    expect(bueroEpoche()).toBe(vorher + 2);
  });

  it("ein späteres aktiv = true berührt den Stand erst beim Aushängen", () => {
    const vorher = bueroEpoche();
    const { rerender, unmount } = renderHook(({ aktiv }) => useBueroStandHalten(aktiv), { initialProps: { aktiv: false } });
    rerender({ aktiv: true });
    expect(bueroEpoche()).toBe(vorher);
    unmount();
    expect(bueroEpoche()).toBe(vorher + 1);
  });
});
