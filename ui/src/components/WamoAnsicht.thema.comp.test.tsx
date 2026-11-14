// Komponententest der Darstellung Nacht gegen den Hellmodus in der Hauptansicht (components/kommando/Rahmen.tsx, lib/thema.ts): Nacht erzwingt dunkel,
// danach gilt wieder die gespeicherte Wahl, und eine Wahl während der Nacht wird nur gespeichert.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { darstellungSetzen } from "@/components/kommando/darstellung";
import { WamoAnsicht } from "@/components/WamoAnsicht";
import { geraeteAnzeige } from "@/lib/geraeteAnzeige";
import { KernFehler } from "@/lib/kernApi";
import { THEMA_SCHLUESSEL, themaAnwenden, themaNacht, themaWaehlen } from "@/lib/thema";
import type { WamoAktionen } from "@/lib/useWamo";
import { resetWamo, wamo } from "@/lib/wamo";

const attrappe = vi.hoisted(() => ({ kernJson: vi.fn(), getSupabase: vi.fn() }));
vi.mock("@/lib/kernApi", async (original) => ({ ...(await original<typeof import("@/lib/kernApi")>()), kernJson: attrappe.kernJson }));
vi.mock("@/lib/supabase", async (original) => ({ ...(await original<typeof import("@/lib/supabase")>()), getSupabase: attrappe.getSupabase }));

const wurzel = document.documentElement;

beforeAll(() => {
  if (typeof Element.prototype.scrollTo !== "function") Element.prototype.scrollTo = () => undefined;
  window.scrollTo = () => undefined;
});

beforeEach(() => {
  resetWamo();
  localStorage.clear();
  history.replaceState(null, "", "#");
  geraeteAnzeige.set(() => ({ zustand: "nicht_noetig", tunnel: null, kennung: null }));
  attrappe.getSupabase.mockReset().mockReturnValue(null);
  attrappe.kernJson.mockReset().mockImplementation(async () => {
    throw new KernFehler(503, "aus");
  });
  themaAnwenden("dunkel");
  darstellungSetzen("normal", false);
});
afterEach(() => {
  darstellungSetzen("normal", false);
  themaNacht(false);
  themaAnwenden("dunkel");
});

function zeige() {
  wamo.set((s) => ({ ...s, profil: { name: "Test Person", rolle: "admin" } }));
  const aktionen: WamoAktionen = {
    connect: vi.fn(async () => undefined),
    disconnect: vi.fn(async () => undefined),
    decide: vi.fn(() => true),
    toggleMic: vi.fn(),
    vergessen: vi.fn(),
    entscheideAuftrag: vi.fn(() => true),
    pcSteuerung: vi.fn(() => true),
    schliesseFehler: vi.fn(),
    mikrofone: vi.fn(async () => []),
    mikrofonWaehlen: vi.fn(() => true),
  };
  return render(<WamoAnsicht aktionen={aktionen} sitzung={{ email: "niko@wamocon.com", signOut: vi.fn() }} />);
}

describe("Hauptansicht: Nacht und Hellmodus", () => {
  it("Nacht erzwingt dunkel, danach gilt der gespeicherte Hellmodus wieder", () => {
    themaWaehlen("hell");
    zeige();
    expect(wurzel.getAttribute("data-theme")).toBe("light");
    act(() => darstellungSetzen("nacht", false));
    expect(wurzel.getAttribute("data-theme")).toBe("dark");
    act(() => darstellungSetzen("normal", false));
    expect(wurzel.getAttribute("data-theme")).toBe("light");
  });

  it("eine Wahl während der Nacht wird nur gespeichert und gilt erst danach", () => {
    zeige();
    act(() => darstellungSetzen("nacht", false));
    act(() => themaWaehlen("hell"));
    expect(localStorage.getItem(THEMA_SCHLUESSEL)).toBe("hell");
    expect(wurzel.getAttribute("data-theme")).toBe("dark");
    act(() => darstellungSetzen("normal", false));
    expect(wurzel.getAttribute("data-theme")).toBe("light");
  });

  it("ohne gespeicherte Wahl bleibt es nach der Nacht dunkel (Standard)", () => {
    zeige();
    act(() => darstellungSetzen("nacht", false));
    act(() => darstellungSetzen("normal", false));
    expect(wurzel.getAttribute("data-theme")).toBe("dark");
  });
});
