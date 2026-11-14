// Komponententest des Moduls "Stimme" in der Kopfzeile (components/Statusleiste.tsx, lib/stimme.ts): "Eigen 0,78", "Fremd", "Unklar",
// "Nicht eingelernt", unsichtbar ohne Stand oder bei ausgeschalteter Stimmerkennung und für Laborkonten (Prop stimme).
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { Kopfzeile } from "@/components/Kopfzeile";
import { StatusDetails } from "@/components/Statusleiste";
import { applyServerMessage, resetWamo, wamo } from "@/lib/wamo";

beforeEach(() => {
  resetWamo();
  wamo.set((s) => ({ ...s, presence: "idle" }));
});

const modul = (container: HTMLElement) => container.querySelector<HTMLElement>('[data-feld="stimme"]');
const status = (s: "aus" | "fehlt" | "laedt" | "kaputt" | "bereit", modus: "aus" | "beobachten" | "streng" = "streng") =>
  act(() => applyServerMessage({ type: "stimme_status", status: s, modus }));
const ergebnis = (e: "eigen" | "fremd" | "unklar", wert: number | null) =>
  act(() => applyServerMessage({ type: "stimme_ergebnis", ergebnis: e, wert, modus: "streng" }));

describe("Modul Stimme in der Kopfzeile", () => {
  it("fehlt, solange der Kern nichts meldet oder die Stimmerkennung aus ist", () => {
    const { container } = render(<StatusDetails kompakt stimme />);
    expect(modul(container)).toBeNull();
    status("aus", "aus");
    expect(modul(container)).toBeNull();
  });

  it("zeigt Beschriftung und Wert: Stimme, Eigen 0,78", () => {
    const { container } = render(<StatusDetails kompakt stimme />);
    status("bereit");
    ergebnis("eigen", 0.78);
    const m = modul(container)!;
    expect(m.querySelector("dt")?.textContent).toBe("Stimme");
    expect(m.querySelector("dt")?.classList.contains("sr-only")).toBe(false);
    expect(m.querySelector("dd")?.textContent).toBe("Eigen 0,78");
    expect(m.getAttribute("data-an")).toBe("ja");
    expect(m.querySelector("dd")?.getAttribute("title")).toBe("Nur deine Stimme darf Aufträge auslösen");
  });

  it("zeigt Fremd, Unklar, Bereit und Nicht eingelernt", () => {
    const { container } = render(<StatusDetails kompakt stimme />);
    status("bereit", "beobachten");
    expect(modul(container)!.querySelector("dd")?.textContent).toBe("Bereit");
    ergebnis("fremd", 0.31);
    expect(modul(container)!.querySelector("dd")?.textContent).toBe("Fremd 0,31");
    expect(modul(container)!.getAttribute("data-an")).toBe("warn");
    ergebnis("unklar", 0.55);
    expect(modul(container)!.querySelector("dd")?.textContent).toBe("Unklar 0,55");
    status("fehlt");
    expect(modul(container)!.querySelector("dd")?.textContent).toBe("Nicht eingelernt");
    expect(modul(container)!.getAttribute("data-an")).toBe("warn");
  });

  it("ist für Laborkonten unsichtbar (Prop stimme aus), auch wenn der Kern etwas meldet", () => {
    const { container } = render(<StatusDetails kompakt stimme={false} />);
    status("bereit");
    ergebnis("eigen", 0.9);
    expect(modul(container)).toBeNull();
    expect(container.textContent).not.toMatch(/Eigen/);
  });

  it("steht ohne die Prop gar nicht da (Geräte-Einstellungen)", () => {
    const { container } = render(<StatusDetails kompakt={false} />);
    status("bereit");
    expect(modul(container)).toBeNull();
  });

  it("im ausführlichen Menü heißt die Zeile Stimmerkennung (Stimme ist dort schon die Sprachausgabe)", () => {
    const { container } = render(<StatusDetails kompakt={false} stimme />);
    status("bereit");
    ergebnis("eigen", 0.78);
    const m = modul(container)!;
    expect(m.querySelector("dt")?.textContent).toBe("Stimmerkennung");
    expect(m.querySelector("dd")?.textContent).toBe("Eigen 0,78");
  });
});

describe("Modul Stimme in der Kopfzeile je Konto", () => {
  const kopf = (email: string) =>
    render(<Kopfzeile email={email} onAbmelden={() => undefined} verlaufOffen={false} onVerlauf={() => undefined} />);

  it("ein normales Konto sieht es", () => {
    const { container } = kopf("niko@wamocon.com");
    status("bereit");
    ergebnis("eigen", 0.78);
    expect(container.querySelector('.status-items [data-feld="stimme"] dd')?.textContent).toBe("Eigen 0,78");
  });

  it("ein Laborkonto sieht es nie, auch nicht im Menü", () => {
    const { container } = kopf("labor-03@wamo.test");
    status("bereit");
    ergebnis("eigen", 0.78);
    expect(container.querySelector('[data-feld="stimme"]')).toBeNull();
  });
});
