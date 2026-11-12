// Komponententest der Bausteine für den Start beim Tippen (components/StillStart.tsx, components/einstellungen/StillStart.tsx, KP-1A):
// der Geräteschalter in den Einstellungen (Standard an, ausdrückliche Wahl je Person, nie für Laborkonten), der Hinweis
// "WAMO läuft in einem anderen Fenster" mit "Hier übernehmen" und die Knöpfe einer stillen Sitzung.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StillStartEinstellung } from "@/components/einstellungen/StillStart";
import { StillAktionen, UebernehmenHinweis } from "@/components/StillStart";
import { nutzerSetzen } from "@/lib/nutzer";
import { HINWEIS_ANDERES_FENSTER, STILL_AUS } from "@/lib/stillStart";
import { resetWamo, wamo } from "@/lib/wamo";

const NUTZER = "0b7f3c1e-2d4a-4c55-9a10-1a2b3c4d5e6f";
const ANDERER = "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const schluessel = (id: string) => `wamo.stillStart.${id}`;
const kasten = () => screen.getByRole("checkbox", { name: /Still starten beim Tippen/ }) as HTMLInputElement;

describe("Einstellung 'Still starten beim Tippen'", () => {
  beforeEach(() => {
    resetWamo();
    localStorage.clear();
    nutzerSetzen({ id: NUTZER, email: "niko@wamocon.com" });
  });

  it("Standard ist an, solange die Person nichts gewählt hat", () => {
    render(<StillStartEinstellung />);
    expect(kasten().checked).toBe(true);
    expect(kasten().disabled).toBe(false);
    expect(localStorage.getItem(schluessel(NUTZER))).toBeNull();
  });

  it("Aus- und Einschalten speichert die Wahl je Gerät und Person und gilt für niemand sonst", async () => {
    render(<StillStartEinstellung />);
    await userEvent.click(kasten());
    expect(kasten().checked).toBe(false);
    expect(localStorage.getItem(schluessel(NUTZER))).toBe("aus");
    expect(localStorage.getItem(schluessel(ANDERER))).toBeNull();
    await userEvent.click(kasten());
    expect(kasten().checked).toBe(true);
    expect(localStorage.getItem(schluessel(NUTZER))).toBe("an");
  });

  it("eine andere Person am selben Gerät sieht ihren eigenen Wert", () => {
    localStorage.setItem(schluessel(NUTZER), "aus");
    nutzerSetzen({ id: ANDERER, email: "kollege@wamocon.com" });
    render(<StillStartEinstellung />);
    expect(kasten().checked).toBe(true);
  });

  it("der Hinweis sagt, was bei mehreren Geräten passiert und wo man es ausschaltet", () => {
    render(<StillStartEinstellung />);
    expect(screen.getByText(/Schalte es dort aus, wo du das nicht willst/)).toBeTruthy();
    expect(screen.getByText(/löst eine laufende Sitzung auf einem anderen Gerät ab/)).toBeTruthy();
    expect(screen.getByText(/20 Minuten/)).toBeTruthy();
  });

  it("Laborkonto: gesperrt, aus und mit Erklärung, auch wenn im Speicher 'an' steht", () => {
    nutzerSetzen({ id: NUTZER, email: "labor-12@wamo.test" });
    localStorage.setItem(schluessel(NUTZER), "an");
    render(<StillStartEinstellung />);
    expect(kasten().disabled).toBe(true);
    expect(kasten().checked).toBe(false);
    expect(screen.getByText(/Laborplätzen nicht verfügbar/)).toBeTruthy();
  });

  it("ohne Anmeldung (keine Kennung) ist der Schalter gesperrt", () => {
    nutzerSetzen({ id: null, email: null });
    render(<StillStartEinstellung />);
    expect(kasten().disabled).toBe(true);
  });

  it("gesperrter Speicher: der Schalter wirkt bis zum Neuladen, ohne zu werfen", async () => {
    const vorher = Object.getOwnPropertyDescriptor(window, "localStorage")!;
    const gesperrt = () => {
      throw new Error("gesperrt");
    };
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: { getItem: gesperrt, setItem: gesperrt, removeItem: gesperrt, clear: gesperrt },
    });
    try {
      nutzerSetzen({ id: "ee000000-0000-4000-8000-000000000000", email: "x@wamocon.com" });
      render(<StillStartEinstellung />);
      expect(kasten().checked).toBe(false); // der Speicher ist unlesbar: aus, nicht der Standard
      await userEvent.click(kasten());
      expect(kasten().checked).toBe(true); // die Wahl gilt bis zum Neuladen
    } finally {
      Object.defineProperty(window, "localStorage", vorher);
    }
  });
});

describe("Hinweis 'WAMO läuft in einem anderen Fenster'", () => {
  beforeEach(() => {
    resetWamo();
    localStorage.clear();
    nutzerSetzen({ id: NUTZER, email: "niko@wamocon.com" });
  });

  it("erscheint nur nach einer Übernahme, mit Schalter an und ohne Sitzung", () => {
    localStorage.setItem(schluessel(NUTZER), "an");
    render(<UebernehmenHinweis onUebernehmen={() => undefined} />);
    expect(screen.queryByText(HINWEIS_ANDERES_FENSTER)).toBeNull();
    act(() => {
      wamo.set((s) => ({ ...s, stillStart: { ...STILL_AUS, uebernommen: true } }));
    });
    expect(screen.getByText(HINWEIS_ANDERES_FENSTER)).toBeTruthy();
    act(() => {
      wamo.set((s) => ({ ...s, presence: "connecting" }));
    });
    expect(screen.queryByText(HINWEIS_ANDERES_FENSTER)).toBeNull();
  });

  it("'Hier übernehmen' ist ein Knopf und löst erst auf Klick aus", async () => {
    localStorage.setItem(schluessel(NUTZER), "an");
    const uebernehmen = vi.fn();
    wamo.set((s) => ({ ...s, stillStart: { ...STILL_AUS, uebernommen: true } }));
    render(<UebernehmenHinweis onUebernehmen={uebernehmen} />);
    expect(uebernehmen).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Hier übernehmen" }));
    expect(uebernehmen).toHaveBeenCalledTimes(1);
  });

  it("ohne eingeschalteten Schalter bleibt die Oberfläche unverändert (kein neuer Hinweis)", () => {
    localStorage.setItem(schluessel(NUTZER), "aus");
    wamo.set((s) => ({ ...s, stillStart: { ...STILL_AUS, uebernommen: true } }));
    render(<UebernehmenHinweis onUebernehmen={() => undefined} />);
    expect(screen.queryByText(HINWEIS_ANDERES_FENSTER)).toBeNull();
  });
});

describe("Knöpfe einer stillen Sitzung", () => {
  beforeEach(() => resetWamo());

  it("zeigt 'Gespräch starten' (Sprechen) und 'Beenden'", async () => {
    const sprechen = vi.fn();
    const beenden = vi.fn();
    render(<StillAktionen onSprechen={sprechen} onBeenden={beenden} />);
    await userEvent.click(screen.getByRole("button", { name: "Gespräch starten" }));
    await userEvent.click(screen.getByRole("button", { name: "Beenden" }));
    expect(sprechen).toHaveBeenCalledTimes(1);
    expect(beenden).toHaveBeenCalledTimes(1);
  });
});
