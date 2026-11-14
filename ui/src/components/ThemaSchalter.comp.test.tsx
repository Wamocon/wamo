// Komponententest des Hell-Dunkel-Umschalters (components/ThemaSchalter.tsx, lib/thema.ts): aria-pressed, Wirkung auf data-theme,
// Speichern je Gerät, Verhalten in der Darstellung Nacht, die Farbe der Browserleiste (auch mit zwei Metas nach der Hydration)
// und der Platz in der Kopfzeile.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { darstellungSetzen } from "@/components/kommando/darstellung";
import { Kopfzeile } from "@/components/Kopfzeile";
import { ThemaSchalter } from "@/components/ThemaSchalter";
import { LEISTENFARBE, THEMA_SCHLUESSEL, themaAnwenden, themaNacht } from "@/lib/thema";
import { resetWamo } from "@/lib/wamo";

const wurzel = document.documentElement;
const schalter = () => screen.getByRole("button", { name: "Hellmodus" });

beforeEach(() => {
  resetWamo();
  localStorage.removeItem(THEMA_SCHLUESSEL);
  themaAnwenden("dunkel");
  darstellungSetzen("normal", false);
});
afterEach(() => {
  themaNacht(false);
  themaAnwenden("dunkel");
  darstellungSetzen("normal", false);
});

describe("ThemaSchalter", () => {
  it("ist ein Umschalter mit aria-pressed und zeigt anfangs dunkel (nicht gedrückt)", () => {
    render(<ThemaSchalter />);
    expect(schalter().getAttribute("aria-pressed")).toBe("false");
    expect(schalter().getAttribute("title")).toBe("Hellmodus einschalten");
    expect(wurzel.getAttribute("data-theme")).toBe("dark");
  });

  it("schaltet auf hell: data-theme, aria-pressed und die Wahl im Speicher folgen", () => {
    render(<ThemaSchalter />);
    fireEvent.click(schalter());
    expect(wurzel.getAttribute("data-theme")).toBe("light");
    expect(schalter().getAttribute("aria-pressed")).toBe("true");
    expect(schalter().getAttribute("title")).toBe("Hellmodus ausschalten");
    expect(localStorage.getItem(THEMA_SCHLUESSEL)).toBe("hell");
  });

  it("schaltet wieder zurück auf dunkel", () => {
    render(<ThemaSchalter />);
    fireEvent.click(schalter());
    fireEvent.click(schalter());
    expect(wurzel.getAttribute("data-theme")).toBe("dark");
    expect(schalter().getAttribute("aria-pressed")).toBe("false");
    expect(localStorage.getItem(THEMA_SCHLUESSEL)).toBe("dunkel");
  });

  it("folgt einer Änderung von außen (anderer Teil der Seite)", async () => {
    render(<ThemaSchalter />);
    await act(async () => {
      themaAnwenden("hell");
    });
    expect(schalter().getAttribute("aria-pressed")).toBe("true");
  });

  it("bleibt in der Darstellung Nacht gesperrt und dunkel, danach gilt die gespeicherte Wahl wieder", () => {
    localStorage.setItem(THEMA_SCHLUESSEL, "hell");
    themaAnwenden("hell");
    render(<ThemaSchalter />);
    expect(schalter().getAttribute("aria-pressed")).toBe("true");
    act(() => {
      darstellungSetzen("nacht", false);
      themaNacht(true); // so erzwingt kommando/Rahmen.tsx dunkel
    });
    expect(wurzel.getAttribute("data-theme")).toBe("dark");
    // gesperrt über aria-disabled statt disabled: der Knopf bleibt fokussierbar, und der Grund steht als Beschreibung daran
    expect((schalter() as HTMLButtonElement).disabled).toBe(false);
    expect(schalter().getAttribute("aria-disabled")).toBe("true");
    expect(schalter().getAttribute("title")).toBe("In der Darstellung Nacht bleibt die Oberfläche dunkel");
    const beschreibung = document.getElementById(schalter().getAttribute("aria-describedby") ?? "");
    expect(beschreibung?.textContent).toBe("In der Darstellung Nacht bleibt die Oberfläche dunkel");
    fireEvent.click(schalter()); // tut nichts
    expect(wurzel.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(THEMA_SCHLUESSEL)).toBe("hell");
    act(() => {
      darstellungSetzen("normal", false);
      themaNacht(false);
    });
    expect(wurzel.getAttribute("data-theme")).toBe("light");
    expect(schalter().hasAttribute("aria-disabled")).toBe(false);
    expect(schalter().hasAttribute("aria-describedby")).toBe(false);
  });
});

describe("Farbe der Browserleiste (meta theme-color)", () => {
  const leisten = () => [...document.querySelectorAll('meta[name="theme-color"]')].map((m) => m.getAttribute("content"));
  /** Zustand nach der Hydration im Hellmodus: das Skript im Kopf hat das erste Meta umgestellt, React hat ein zweites angelegt. */
  function nachHydration() {
    for (const farbe of [LEISTENFARBE.hell, LEISTENFARBE.dunkel]) {
      const m = document.createElement("meta");
      m.setAttribute("name", "theme-color");
      m.setAttribute("content", farbe);
      document.head.appendChild(m);
    }
    wurzel.setAttribute("data-theme", "light");
  }
  afterEach(() => {
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.remove());
  });

  it("nach dem Einhängen gleicht useThema jedes Meta an das wirksame Thema an", () => {
    nachHydration();
    expect(leisten()).toEqual([LEISTENFARBE.hell, LEISTENFARBE.dunkel]);
    render(<ThemaSchalter />);
    expect(leisten()).toEqual([LEISTENFARBE.hell, LEISTENFARBE.hell]);
  });

  it("ein Wechsel des Themas stellt jedes Meta um", () => {
    nachHydration();
    render(<ThemaSchalter />);
    fireEvent.click(schalter());
    expect(leisten()).toEqual([LEISTENFARBE.dunkel, LEISTENFARBE.dunkel]);
    act(() => themaAnwenden("hell"));
    expect(leisten()).toEqual([LEISTENFARBE.hell, LEISTENFARBE.hell]);
  });
});

describe("ThemaSchalter in der Kopfzeile", () => {
  it("steht in der Kopfzeile neben dem Zahnrad", () => {
    render(<Kopfzeile email="niko@wamocon.com" onAbmelden={() => undefined} verlaufOffen={false} onVerlauf={() => undefined} />);
    const knopf = schalter();
    expect(knopf.closest(".topbar-end")).not.toBeNull();
    expect(knopf.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });
});
