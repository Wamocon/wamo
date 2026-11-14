// Komponententest des Blocks "Gespräch" in der linken Spalte (components/kommando/Gespraech.tsx, lib/gespraech.ts):
// Überschrift mit Abzeichen online oder offline, Stimmlinie samt Legende, und die letzten Zeilen von Person und WAMO.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { Gespraech } from "@/components/kommando/Gespraech";
import { leereCaption, logEintrag, resetWamo, wamo } from "@/lib/wamo";

beforeEach(() => resetWamo());

const block = () => screen.getByRole("region", { name: /Gespräch/ });

describe("Gespräch: Kopf und Stimmlinie", () => {
  it("trägt die Überschrift Gespräch und zeigt offline, solange keine Sitzung läuft", () => {
    render(<Gespraech />);
    expect(screen.getByRole("heading", { level: 2, name: /Gespräch/ })).toBeTruthy();
    expect(within(block()).getByText("Offline")).toBeTruthy();
  });

  it("zeigt online, sobald WAMO bei der Person ist", () => {
    render(<Gespraech />);
    act(() => wamo.set((s) => ({ ...s, presence: "idle" })));
    expect(within(block()).getByText("Online")).toBeTruthy();
    expect(within(block()).queryByText("Offline")).toBeNull();
  });

  it("enthält die Stimmlinie und die Legende Du und WAMO (die Legende nur zum Sehen)", () => {
    render(<Gespraech />);
    expect(screen.getByRole("img", { name: /Stimmlinie/ })).toBeTruthy();
    const legende = block().querySelector(".legend") as HTMLElement;
    expect(legende.getAttribute("aria-hidden")).toBe("true");
    expect(legende.textContent).toBe("DuWAMO");
  });
});

describe("Gespräch: letzte Zeilen", () => {
  it("sagt ehrlich, dass noch nichts gesagt wurde", () => {
    render(<Gespraech />);
    expect(within(block()).getByText("Noch nichts gesagt.")).toBeTruthy();
  });

  it("zeigt den laufenden Beitrag der Person und den Zug von WAMO", () => {
    render(<Gespraech />);
    act(() =>
      wamo.set((s) => ({
        ...s,
        presence: "speaking",
        caption: { ...leereCaption, userDone: "Zeig mir die Testfälle", text: "Vier Testfälle sind offen.", spoken: 2 },
      })),
    );
    const person = block().querySelector(".kz-zeile-mensch") as HTMLElement;
    const wamoZeile = block().querySelector(".kz-zeile-wamo") as HTMLElement;
    expect(person.textContent).toBe("DuZeig mir die Testfälle");
    expect(wamoZeile.textContent).toBe("WAMOVier Testfälle sind offen.");
  });

  it("behält die Zeilen nach dem Zugende aus dem Verlauf", () => {
    render(<Gespraech />);
    act(() =>
      wamo.set((s) => ({
        ...s,
        presence: "idle",
        log: [logEintrag({ wer: "nutzer", text: "Was ist offen?" }), logEintrag({ wer: "wamo", text: "Zwei Aufträge." })],
      })),
    );
    expect(block().querySelector(".kz-zeile-mensch")?.textContent).toBe("DuWas ist offen?");
    expect(block().querySelector(".kz-zeile-wamo")?.textContent).toBe("WAMOZwei Aufträge.");
  });

  it("zeigt nach einer Unterbrechung nur, was gesprochen wurde", () => {
    render(<Gespraech />);
    act(() =>
      wamo.set((s) => ({
        ...s,
        caption: { ...leereCaption, text: "Eins zwei drei vier", spoken: 2, interrupted: true },
      })),
    );
    expect(block().querySelector(".kz-zeile-wamo")?.textContent).toBe("WAMOEins zwei");
  });

  it("liest nicht bei jedem Wort vor: die Zeilen sind keine Live-Region", () => {
    render(<Gespraech />);
    expect(block().querySelector("[aria-live]")).toBeNull();
  });
});

describe("Gespräch: lange Antworten", () => {
  it("mit Verlauf darf die Spalte kürzen (data-verlauf, der volle Text steht im Verlauf)", () => {
    render(<Gespraech />);
    expect(block().getAttribute("data-verlauf")).toBe("true");
  });

  it("ohne Verlauf (Laborkonto) kürzt sie nie, denn der Rest stünde nirgends", () => {
    render(<Gespraech mitVerlauf={false} />);
    expect(block().hasAttribute("data-verlauf")).toBe(false);
  });
});
