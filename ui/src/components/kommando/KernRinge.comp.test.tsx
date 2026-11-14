// Komponententest der HUD-Ringe um den Kern (components/kommando/KernRinge.tsx, lib/kernRinge.ts): Aufbau der Ringe,
// Zustände aus dem Store (data-presence, data-acting), Pegelbogen aus levelsRef (Länge und Sprecher), die Dichte und die ruhende
// Bildschleife (leerer Bogen ohne Pegel, Ringe nicht zu sehen).
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KernRinge } from "@/components/kommando/KernRinge";
import { levelsRef, resetWamo, wamo } from "@/lib/wamo";

/** Bilder von Hand ablaufen lassen: jede Funktion aus requestAnimationFrame läuft genau einmal je Aufruf von bild(). */
let wartend: FrameRequestCallback[] = [];
const bild = () => {
  const jetzt = wartend;
  wartend = [];
  act(() => jetzt.forEach((f) => f(performance.now())));
};

/** Attrappe des IntersectionObserver: sichtbarkeit(false) meldet, dass die Ringe aus dem Bild sind. */
const sicht = { melden: null as null | ((e: { isIntersecting: boolean }[]) => void), aktiv: 0 };
class SichtAttrappe {
  constructor(melden: (e: { isIntersecting: boolean }[]) => void) {
    sicht.melden = melden;
  }
  observe() {
    sicht.aktiv += 1;
  }
  disconnect() {
    sicht.aktiv -= 1;
  }
}
const sichtbarkeit = (ja: boolean) => act(() => sicht.melden?.([{ isIntersecting: ja }]));
/** Zeit für den Pegelwecker (setInterval), der die ruhende Schleife bei Ton wieder anstößt */
const warten = (ms = 150) => act(() => void vi.advanceTimersByTime(ms));

beforeEach(() => {
  resetWamo();
  wartend = [];
  sicht.melden = null;
  sicht.aktiv = 0;
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"] });
  vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => wartend.push(f));
  vi.stubGlobal("cancelAnimationFrame", () => {
    wartend = [];
  });
  vi.stubGlobal("IntersectionObserver", SichtAttrappe);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const wurzel = () => document.querySelector<HTMLElement>(".kr-ringe") as HTMLElement;
const bogen = () => document.querySelector<SVGElement>(".kr-pegel") as SVGElement;

describe("KernRinge: Aufbau", () => {
  it("zeichnet die Ringe als SVG, das für Screenreader verborgen und nicht klickbar ist", () => {
    render(<KernRinge levels={levelsRef} />);
    const svg = document.querySelector("svg.kr-ringe") as SVGElement;
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    for (const klasse of ["kr-skala", "kr-befehl", "kr-segmente", "kr-pegel", "kr-punkte", "kr-bahnen", "kr-komet", "kr-fassung"]) {
      expect(svg.querySelector(`.${klasse}`), klasse).not.toBeNull();
    }
    expect(svg.querySelectorAll(".kr-bahnen ellipse").length).toBe(2);
  });

  it("zeigt in der Zentrale (Dichte 1,5) eine dritte Umlaufbahn", () => {
    render(<KernRinge levels={levelsRef} dichte={1.5} />);
    expect(document.querySelectorAll(".kr-bahnen ellipse").length).toBe(3);
  });
});

describe("KernRinge: Zustände aus dem Store", () => {
  it("übernimmt die Präsenz als data-presence", () => {
    render(<KernRinge levels={levelsRef} />);
    expect(wurzel().getAttribute("data-presence")).toBe("offline");
    act(() => wamo.set((s) => ({ ...s, presence: "thinking" })));
    expect(wurzel().getAttribute("data-presence")).toBe("thinking");
  });

  it("zeigt Handeln nur online und nur, solange ein Werkzeug läuft", () => {
    render(<KernRinge levels={levelsRef} />);
    const laeuft = { id: "1", name: "web_suche", detail: "", status: "laeuft" as const };
    act(() => wamo.set((s) => ({ ...s, presence: "speaking", activities: [laeuft] })));
    expect(wurzel().getAttribute("data-acting")).toBe("true");
    act(() => wamo.set((s) => ({ ...s, activities: [{ ...laeuft, status: "fertig" as const }] })));
    expect(wurzel().getAttribute("data-acting")).toBe("false");
    act(() => wamo.set((s) => ({ ...s, presence: "offline", activities: [laeuft] })));
    expect(wurzel().getAttribute("data-acting")).toBe("false");
  });
});

describe("KernRinge: Pegelbogen", () => {
  it("bleibt ohne Ton leer und wächst mit der Stimme von W.A.I.S.", () => {
    act(() => wamo.set((s) => ({ ...s, presence: "speaking" })));
    render(<KernRinge levels={levelsRef} />);
    bild();
    expect(bogen().style.strokeDasharray.startsWith("0.0")).toBe(true);
    levelsRef.current = { user: 0, bot: 0.3, cut: false, speaker: 0 };
    warten();
    for (let i = 0; i < 12; i++) bild();
    const laenge = parseFloat(bogen().style.strokeDasharray);
    expect(laenge).toBeGreaterThan(100);
    expect(bogen().getAttribute("data-wer")).toBe("bot");
  });

  it("färbt den Bogen nach dem Sprecher: Mensch, weitere Stimme, W.A.I.S.", () => {
    act(() => wamo.set((s) => ({ ...s, presence: "listening" })));
    render(<KernRinge levels={levelsRef} />);
    levelsRef.current = { user: 0.3, bot: 0, cut: false, speaker: 0 };
    bild();
    expect(bogen().getAttribute("data-wer")).toBe("user");
    levelsRef.current = { user: 0.3, bot: 0, cut: false, speaker: 1 };
    bild();
    expect(bogen().getAttribute("data-wer")).toBe("fremd");
    levelsRef.current = { user: 0, bot: 0.3, cut: false, speaker: 0 };
    bild();
    expect(bogen().getAttribute("data-wer")).toBe("bot");
  });

  it("hält die Schleife an, wenn nichts läuft, und räumt beim Aushängen auf", () => {
    const { unmount } = render(<KernRinge levels={levelsRef} />);
    bild(); // offline und leer: ein Bild, dann ruht die Schleife
    bild();
    expect(wartend.length).toBe(0);
    expect(sicht.aktiv).toBe(1);
    unmount();
    expect(wartend.length).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    expect(sicht.aktiv).toBe(0);
  });

  it("ruht auch mit laufender Sitzung, solange der Bogen leer ist und kein Pegel kommt, und wacht mit der Stimme auf", () => {
    act(() => wamo.set((s) => ({ ...s, presence: "listening" })));
    render(<KernRinge levels={levelsRef} />);
    bild();
    expect(wartend.length).toBe(0); // kein Bild je Bildschirmtakt ohne Ton
    warten(1000);
    expect(wartend.length).toBe(0);
    levelsRef.current = { user: 0.3, bot: 0, cut: false, speaker: 0 };
    warten();
    expect(wartend.length).toBe(1);
    bild();
    expect(parseFloat(bogen().style.strokeDasharray)).toBeGreaterThan(0);
    // Ton weg: der Bogen läuft aus, dann ruht die Schleife wieder
    levelsRef.current = { user: 0, bot: 0, cut: false, speaker: 0 };
    for (let i = 0; i < 200 && wartend.length; i++) bild();
    expect(wartend.length).toBe(0);
    expect(bogen().style.strokeDasharray.startsWith("0.0")).toBe(true);
  });

  it("ruht, solange die Ringe nicht zu sehen sind, und zeichnet weiter, sobald sie wieder sichtbar sind", () => {
    act(() => wamo.set((s) => ({ ...s, presence: "speaking" })));
    levelsRef.current = { user: 0, bot: 0.3, cut: false, speaker: 0 };
    render(<KernRinge levels={levelsRef} />);
    bild();
    expect(wartend.length).toBe(1);
    sichtbarkeit(false);
    bild();
    expect(wartend.length).toBe(0);
    warten(1000);
    act(() => wamo.set((s) => ({ ...s, presence: "thinking" })));
    expect(wartend.length).toBe(0);
    sichtbarkeit(true);
    expect(wartend.length).toBe(1);
  });
});
