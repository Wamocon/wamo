// Komponententest des Abschnitts "Helligkeit" in den Einstellungen (components/einstellungen/Abschnitte.tsx, lib/thema.ts):
// Dunkel und Hell als Auswahl, gespeichert je Gerät, in der Darstellung Nacht gesperrt.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { darstellungSetzen } from "@/components/kommando/darstellung";
import { DarstellungAbschnitt } from "@/components/einstellungen/Abschnitte";
import { THEMA_SCHLUESSEL, themaAnwenden, themaNacht } from "@/lib/thema";
import { resetWamo } from "@/lib/wamo";

const radio = (name: string) => screen.getByRole("radio", { name }) as HTMLInputElement;

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

describe("Einstellungen: Helligkeit", () => {
  it("bietet Dunkel (Standard) und Hell als Auswahl", () => {
    render(<DarstellungAbschnitt />);
    expect(screen.getByRole("heading", { name: "Helligkeit" })).toBeTruthy();
    expect(radio("Dunkel").checked).toBe(true);
    expect(radio("Hell").checked).toBe(false);
  });

  it("wechselt auf Hell, setzt data-theme und merkt sich die Wahl", () => {
    render(<DarstellungAbschnitt />);
    fireEvent.click(radio("Hell"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(radio("Hell").checked).toBe(true);
    expect(localStorage.getItem(THEMA_SCHLUESSEL)).toBe("hell");
  });

  it("sperrt die Auswahl in der Darstellung Nacht und sagt warum", () => {
    render(<DarstellungAbschnitt />);
    act(() => {
      darstellungSetzen("nacht", false);
      themaNacht(true);
    });
    expect(radio("Hell").matches(":disabled")).toBe(true);
    expect(screen.getByText("In der Darstellung Nacht bleibt die Oberfläche dunkel.")).toBeTruthy();
  });

  it("lässt die Auswahl der Ansicht (Normal, Fokus, Zentrale, Nacht) unberührt", () => {
    render(<DarstellungAbschnitt />);
    expect(screen.getByRole("radio", { name: /Normal/ })).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Nacht/ })).toBeTruthy();
  });
});
