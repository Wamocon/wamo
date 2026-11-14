// Komponententest der Lichtkugel (components/Orb.tsx): Ohne WebGL steht data-gpu="aus" (kernRinge.css zeigt dann den Farbverlauf) und es läuft
// nichts. Mit WebGL (Attrappe des Kontexts) zeichnet sie, folgt dem Store und räumt beim Aushängen alles ab: Bildschleife, Takt, Store,
// Beobachter und Ereignisse. Bilder laufen von Hand (requestAnimationFrame ist ersetzt).
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Orb } from "@/components/Orb";
import { levelsRef, resetWamo, wamo } from "@/lib/wamo";

let wartend: FrameRequestCallback[] = [];
const bild = () => {
  const jetzt = wartend;
  wartend = [];
  act(() => jetzt.forEach((f) => f(performance.now() + 1000)));
};

/** Attrappe eines WebGL-Kontexts: jede Methode wird mitgeschrieben; nur was Orb.tsx auswertet, liefert einen Wert. */
function attrappeGl() {
  const aufrufe: string[] = [];
  const werte: Record<string, (...a: unknown[]) => unknown> = {
    getProgramParameter: () => true,
    createShader: () => ({ art: "shader" }),
    createProgram: () => ({ art: "programm" }),
    createBuffer: () => ({ art: "puffer" }),
    getUniformLocation: () => ({}),
    getAttribLocation: () => 0,
    getExtension: (name) => (name === "WEBGL_lose_context" ? { loseContext: () => void aufrufe.push("loseContext") } : null),
  };
  const gl = new Proxy(
    {},
    {
      get: (_, k) => {
        const name = String(k);
        return (...a: unknown[]) => {
          aufrufe.push(name);
          return werte[name]?.(...a);
        };
      },
    },
  );
  return { gl, aufrufe };
}

const beobachter = { aktiv: 0 };
class ResizeAttrappe {
  constructor(public melden: () => void) {}
  observe() {
    beobachter.aktiv += 1;
  }
  disconnect() {
    beobachter.aktiv -= 1;
  }
}

const leinwand = () => document.querySelector("canvas.orb") as HTMLCanvasElement;

beforeEach(() => {
  resetWamo();
  wartend = [];
  beobachter.aktiv = 0;
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"] });
  vi.stubGlobal("requestAnimationFrame", (f: FrameRequestCallback) => wartend.push(f));
  vi.stubGlobal("cancelAnimationFrame", () => {
    wartend = [];
  });
  vi.stubGlobal("ResizeObserver", ResizeAttrappe);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Orb ohne WebGL", () => {
  it("setzt data-gpu=aus, ist für Screenreader verborgen und startet weder Bildschleife noch Takt", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const { unmount } = render(<Orb levels={levelsRef} />);
    expect(leinwand().dataset.gpu).toBe("aus");
    expect(leinwand().getAttribute("aria-hidden")).toBe("true");
    expect(wartend.length).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    act(() => wamo.set((s) => ({ ...s, presence: "speaking" })));
    expect(wartend.length).toBe(0);
    expect(() => unmount()).not.toThrow();
  });
});

describe("Orb mit WebGL", () => {
  function zeigen() {
    const a = attrappeGl();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(a.gl as unknown as RenderingContext);
    const r = render(<Orb levels={levelsRef} />);
    return { ...r, aufrufe: a.aufrufe };
  }

  it("setzt data-gpu=an und zeichnet", () => {
    const { aufrufe } = zeigen();
    expect(leinwand().dataset.gpu).toBe("an");
    act(() => wamo.set((s) => ({ ...s, presence: "speaking" })));
    bild();
    bild();
    expect(aufrufe).toContain("drawArrays");
  });

  it("räumt beim Aushängen ab: keine Bilder, kein Takt, kein Store, keine Beobachter, keine Ereignisse mehr", () => {
    const echt = wamo.subscribe;
    let abos = 0;
    vi.spyOn(wamo, "subscribe").mockImplementation((melden) => {
      abos += 1;
      const ab = echt(melden);
      return () => {
        abos -= 1;
        ab();
      };
    });
    const sichtbarkeit = { an: 0 };
    const add = document.addEventListener.bind(document);
    const remove = document.removeEventListener.bind(document);
    vi.spyOn(document, "addEventListener").mockImplementation((typ, f, o) => {
      if (typ === "visibilitychange") sichtbarkeit.an += 1;
      add(typ, f, o);
    });
    vi.spyOn(document, "removeEventListener").mockImplementation((typ, f, o) => {
      if (typ === "visibilitychange") sichtbarkeit.an -= 1;
      remove(typ, f, o);
    });

    const { unmount } = zeigen();
    const canvas = leinwand();
    act(() => wamo.set((s) => ({ ...s, presence: "speaking" })));
    expect(wartend.length).toBeGreaterThan(0);
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    expect([abos, beobachter.aktiv, sichtbarkeit.an]).toEqual([1, 1, 1]);
    unmount();
    expect(wartend.length).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
    expect([abos, beobachter.aktiv, sichtbarkeit.an]).toEqual([0, 0, 0]);
    // ein verlorener Kontext nach dem Aushängen ändert nichts mehr an der Leinwand
    canvas.dispatchEvent(new Event("webglcontextlost"));
    expect(canvas.dataset.gpu).toBe("an");
  });
});
