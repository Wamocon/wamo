// Komponententest der Kurzformen in der Kopfzeile (components/Statusleiste.tsx): Das Modul "Modelle" zeigt kompakt nur den gekürzten Namen
// des Gesprächsmodells (ganze Wörter, ohne Herstellerangabe), das Menü behält die volle Fassung. Das Modul "PC" hält die Latenz sichtbar
// außerhalb der langen Bezeichnung, die in schmalen Kopfzeilen entfällt.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { StatusDetails } from "@/components/Statusleiste";
import { resetWamo, wamo } from "@/lib/wamo";

function betrieb(llm: string, extra: Record<string, unknown> = {}) {
  wamo.set((s) => ({
    ...s,
    presence: "idle",
    profil: { name: "Niko", rolle: "admin" },
    betrieb: { modus: "buero", modelle: { llm, stt: "Soniox", tts: "Soniox Nina" }, pc: "verbunden", pcName: "WMC-PC", pcLatenzMs: 12, ...extra },
  }));
}

const dd = (c: HTMLElement, feld: string) => c.querySelector<HTMLElement>(`[data-feld="${feld}"] dd`)!;

describe("Kopfzeile, Modul Modelle", () => {
  beforeEach(() => resetWamo());

  it("kompakt: nur der gekürzte Name des Gesprächsmodells, der volle Text steht im title", () => {
    betrieb("Claude Haiku 5.5");
    const { container } = render(<StatusDetails kompakt />);
    const modul = dd(container, "modelle");
    expect(modul.textContent).toBe("Haiku 5.5");
    expect(modul.getAttribute("title")).toContain("Gespräch: Claude Haiku 5.5");
    expect(modul.getAttribute("title")).toContain("Hören: Soniox");
  });

  it("kompakt: ein langer Name verliert ganze Wörter, nie Buchstaben", () => {
    betrieb("Qwen3-235B-A22B-Instruct-2507");
    const { container } = render(<StatusDetails kompakt />);
    expect(dd(container, "modelle").textContent).toBe("Qwen3-235B");
  });

  it("kompakt: ohne Meldung steht weiter 'Modelle unbekannt'", () => {
    const { container } = render(<StatusDetails kompakt />);
    expect(dd(container, "modelle").textContent).toBe("Modelle unbekannt");
  });

  it("ausführlich (Menü): Gespräch, Hören und Stimme mit den vollen Namen", () => {
    betrieb("Claude Haiku 5.5");
    const { container } = render(<StatusDetails kompakt={false} />);
    expect(container.textContent).toContain("Claude Haiku 5.5");
    expect(container.textContent).toContain("Soniox Nina");
  });
});

describe("Kopfzeile, Modul PC", () => {
  beforeEach(() => resetWamo());

  it("verbunden: Name und 'verbunden' stehen in der langen Bezeichnung, die Latenz daneben als eigener Teil", () => {
    betrieb("Claude Haiku 5.5");
    const { container } = render(<StatusDetails kompakt />);
    const pc = dd(container, "pc");
    expect(pc.querySelector(".status-label")?.textContent).toContain("WMC-PC verbunden");
    expect(pc.querySelector(".status-label .latency")).toBeNull();
    expect(pc.querySelector(".latency")?.textContent).toContain("12 ms");
  });
});
