// Komponententest des Hinweises "Gemerkt" (components/GemerktHinweis.tsx): Für Admins führt ein gemeinsamer Vorschlag, der auf Freigabe wartet,
// mit "Jetzt prüfen" zum Abschnitt "Gedächtnis" der Einstellungen. Alle anderen sehen den Link nie.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GemerktHinweis } from "@/components/GemerktHinweis";
import { resetWamo, wamo, zeigeMemo } from "@/lib/wamo";

function profil(rolle: "admin" | "mitglied" | null) {
  act(() => {
    wamo.set((s) => ({ ...s, profil: rolle ? { name: "Test", rolle } : null }));
  });
}

describe("GemerktHinweis", () => {
  beforeEach(() => {
    resetWamo();
    vi.useRealTimers();
  });

  it("Admin: ein gemeinsamer Vorschlag trägt den Link Jetzt prüfen zum Abschnitt Gedächtnis", () => {
    profil("admin");
    render(<GemerktHinweis onVergessen={() => undefined} />);
    act(() => zeigeMemo({ bereich: "gemeinsam", status: "vorgeschlagen", text: "Etwas für alle." }));
    expect(screen.getByText("Für alle vorgeschlagen, wartet auf Freigabe")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Jetzt prüfen" });
    expect(link.getAttribute("href")).toBe("#einstellungen/gedaechtnis");
  });

  it("Mitglied und unbekannte Rolle: kein Link, denn sie können nicht freigeben", () => {
    for (const rolle of ["mitglied", null] as const) {
      resetWamo();
      profil(rolle);
      const { unmount } = render(<GemerktHinweis onVergessen={() => undefined} />);
      act(() => zeigeMemo({ bereich: "gemeinsam", status: "vorgeschlagen", text: "Etwas für alle." }));
      expect(screen.getByText("Für alle vorgeschlagen, wartet auf Freigabe")).toBeTruthy();
      expect(screen.queryByRole("link", { name: "Jetzt prüfen" })).toBeNull();
      unmount();
    }
  });

  it("kein Link bei einem aktiven gemeinsamen oder einem persönlichen Eintrag", () => {
    profil("admin");
    render(<GemerktHinweis onVergessen={() => undefined} />);
    act(() => zeigeMemo({ bereich: "gemeinsam", status: "aktiv", text: "Gilt sofort." }));
    act(() => zeigeMemo({ bereich: "persoenlich", status: "aktiv", text: "Nur für mich." }));
    expect(screen.getByText("Gemerkt · gemeinsam")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Jetzt prüfen" })).toBeNull();
  });
});
