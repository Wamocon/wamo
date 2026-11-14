// Komponententest des Hell-Dunkel-Umschalters (components/ThemaSchalter.tsx, lib/thema.ts): aria-pressed, Wirkung auf data-theme,
// Speichern je Gerät, Verhalten in der Darstellung Nacht und der Platz in der Kopfzeile.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { darstellungSetzen } from "@/components/kommando/darstellung";
import { Kopfzeile } from "@/components/Kopfzeile";
import { ThemaSchalter } from "@/components/ThemaSchalter";
import { THEMA_SCHLUESSEL, themaAnwenden, themaNacht } from "@/lib/thema";
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
    expect((schalter() as HTMLButtonElement).disabled).toBe(true);
    expect(schalter().getAttribute("title")).toBe("In der Darstellung Nacht bleibt die Oberfläche dunkel");
    act(() => {
      darstellungSetzen("normal", false);
      themaNacht(false);
    });
    expect(wurzel.getAttribute("data-theme")).toBe("light");
    expect((schalter() as HTMLButtonElement).disabled).toBe(false);
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
