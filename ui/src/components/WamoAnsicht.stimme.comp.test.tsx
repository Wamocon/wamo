// Komponententest der Stimmerkennung in der Hauptansicht (components/WamoAnsicht.tsx, lib/laborPlatz.ts): Ein normales Konto sieht das Modul "Stimme"
// in der Kopfzeile, die Einlernansicht in der Mitte statt des Kerns und den Abschnitt Stimme in den Einstellungen. Ein Laborkonto sieht nichts davon,
// auch wenn der Kern etwas meldet. Nur Darstellung: der Kern sperrt weiter selbst.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, screen, within } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { WamoAnsicht } from "@/components/WamoAnsicht";
import { geraeteAnzeige } from "@/lib/geraeteAnzeige";
import { KernFehler } from "@/lib/kernApi";
import type { WamoAktionen } from "@/lib/useWamo";
import { abdruckAnsichtOeffnen, applyServerMessage, resetWamo, wamo } from "@/lib/wamo";

const attrappe = vi.hoisted(() => ({ kernJson: vi.fn(), getSupabase: vi.fn() }));
vi.mock("@/lib/kernApi", async (original) => ({ ...(await original<typeof import("@/lib/kernApi")>()), kernJson: attrappe.kernJson }));
vi.mock("@/lib/supabase", async (original) => ({ ...(await original<typeof import("@/lib/supabase")>()), getSupabase: attrappe.getSupabase }));

const PLATZ = "labor-03@wamo.test";
const ADMIN = "niko@wamocon.com";

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
});

function aktionen(): WamoAktionen {
  return {
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
    stimmabdruck: { start: vi.fn(() => true), abbrechen: vi.fn(() => true), loeschen: vi.fn(() => true) },
  };
}

/** Sitzung steht, die Stimmerkennung meldet einen Stand und eine Äußerung, der Einwilligungstext ist da. */
function sitzungMitStimme() {
  wamo.set((s) => ({ ...s, presence: "idle" }));
  applyServerMessage({ type: "stimme_status", status: "bereit", modus: "streng" });
  applyServerMessage({ type: "stimme_ergebnis", ergebnis: "eigen", wert: 0.78, modus: "streng" });
  applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: "WAMO speichert einen Abdruck." });
}

function zeige(email: string, hash = "") {
  if (hash) history.replaceState(null, "", hash);
  wamo.set((s) => ({ ...s, profil: { name: "Test Person", rolle: email === ADMIN ? "admin" : "mitglied" } }));
  return render(<WamoAnsicht aktionen={aktionen()} sitzung={{ email, signOut: vi.fn() }} />);
}
const ruhe = () => act(async () => void (await Promise.resolve()));
const kern = () => screen.queryByRole("region", { name: "Gesprächszustand", hidden: true });
const einlernen = () => screen.queryByRole("heading", { level: 2, name: "Stimme einlernen", hidden: true });

describe("Hauptansicht eines normalen Kontos", () => {
  it("zeigt das Modul Stimme in der Kopfzeile", async () => {
    const { container } = zeige(ADMIN);
    sitzungMitStimme();
    await ruhe();
    expect(container.querySelector('.topbar .status-items [data-feld="stimme"] dd')?.textContent).toBe("Eigen 0,78");
  });

  it("zeigt statt des Kerns die Einlernansicht, solange sie offen ist, und danach wieder den Kern", async () => {
    zeige(ADMIN);
    sitzungMitStimme();
    await ruhe();
    expect(kern()).not.toBeNull();
    expect(einlernen()).toBeNull();
    act(() => void abdruckAnsichtOeffnen());
    expect(einlernen()).not.toBeNull();
    expect(kern()).toBeNull();
    act(() => wamo.set((s) => ({ ...s, abdruck: null })));
    expect(einlernen()).toBeNull();
    expect(kern()).not.toBeNull();
  });

  it("bietet in den Einstellungen den Abschnitt Stimme an", async () => {
    zeige(ADMIN, "#einstellungen/stimme");
    sitzungMitStimme();
    await ruhe();
    const nav = screen.getByRole("navigation", { name: "Abschnitte der Einstellungen" });
    expect(within(nav).getByText("Stimme", { selector: ".ez-nav-titel" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Neu einlernen" })).toBeTruthy();
  });
});

const ignoriert = () =>
  act(() => applyServerMessage({ type: "stimme_ergebnis", ergebnis: "fremd", wert: 0.12, modus: "streng", ignoriert: true }));

describe("Fremde Stimme ignoriert (strenger Modus)", () => {
  it("zeigt einem normalen Konto kurz 'Fremde Stimme ignoriert' und sagt es per role=status an", async () => {
    const { container } = zeige(ADMIN);
    sitzungMitStimme();
    await ruhe();
    ignoriert();
    const toast = container.querySelector(".hinweis-toast");
    expect(toast?.textContent).toBe("Fremde Stimme ignoriert");
    expect(screen.getAllByRole("status").some((e) => e.textContent === "Fremde Stimme ignoriert")).toBe(true);
    expect(container.querySelector('.topbar .status-items [data-feld="stimme"] dd')?.textContent).toBe("Fremd · ignoriert");
  });

  it("meldet jede verworfene Äußerung neu, auch zwei gleiche hintereinander", async () => {
    const { container } = zeige(ADMIN);
    sitzungMitStimme();
    await ruhe();
    ignoriert();
    const erster = container.querySelector(".hinweis-toast");
    act(() => wamo.set((s) => ({ ...s, toast: null })));
    expect(container.querySelector(".hinweis-toast")).toBeNull();
    ignoriert();
    expect(container.querySelector(".hinweis-toast")?.textContent).toBe("Fremde Stimme ignoriert");
    expect(erster).not.toBeNull();
  });

  it("zeigt einer Äußerung, die nicht ignoriert wurde, keinen Hinweis", async () => {
    const { container } = zeige(ADMIN);
    sitzungMitStimme();
    await ruhe();
    act(() => applyServerMessage({ type: "stimme_ergebnis", ergebnis: "fremd", wert: 0.31, modus: "beobachten" }));
    expect(container.querySelector(".hinweis-toast")).toBeNull();
  });

  it("ein Laborkonto bekommt weder Modul noch Hinweis", async () => {
    const { container } = zeige(PLATZ);
    sitzungMitStimme();
    await ruhe();
    ignoriert();
    expect(container.querySelector(".hinweis-toast")).toBeNull();
    expect(container.querySelector('[data-feld="stimme"]')).toBeNull();
    expect(screen.queryAllByRole("status").some((e) => /ignoriert/.test(e.textContent ?? ""))).toBe(false);
  });
});

describe("Hauptansicht eines Laborkontos", () => {
  it("zeigt weder Modul noch Einlernansicht, auch wenn der Kern meldet und die Ansicht offen wäre", async () => {
    const { container } = zeige(PLATZ);
    sitzungMitStimme();
    await ruhe();
    act(() => wamo.set((s) => ({ ...s, abdruck: { phase: "einwilligung", sekunden: 0, ziel: 20 } })));
    expect(container.querySelector('[data-feld="stimme"]')).toBeNull();
    expect(einlernen()).toBeNull();
    expect(kern()).not.toBeNull();
  });

  it("hat in den Einstellungen keinen Abschnitt Stimme", async () => {
    zeige(PLATZ, "#einstellungen/stimme");
    sitzungMitStimme();
    await ruhe();
    const nav = screen.getByRole("navigation", { name: "Abschnitte der Einstellungen" });
    expect(within(nav).queryByText("Stimme", { selector: ".ez-nav-titel" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Neu einlernen" })).toBeNull();
  });
});
