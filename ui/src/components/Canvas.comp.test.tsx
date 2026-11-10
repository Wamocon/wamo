// Komponententest des Canvas: Kopierknopf mit Bestätigungs-Chip (components/Canvas.tsx, lib/kopieren.ts).
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CanvasBereich } from "@/components/Canvas";
import { KOPIERT_MS } from "@/lib/kopieren";
import { resetWamo, wamo } from "@/lib/wamo";

const CODE = "select * from geraete;";

function codeImCanvas() {
  wamo.set((s) => ({ ...s, canvas: [{ kind: "code", title: "Abfrage", text: CODE, language: "sql" }] }));
}

/** Zwischenablage als Attrappe: Der Aufruf gelingt oder wird abgelehnt (zum Beispiel ohne Erlaubnis). */
function zwischenablage(ergebnis: "ok" | "abgelehnt") {
  const writeText = vi.fn(() => (ergebnis === "ok" ? Promise.resolve() : Promise.reject(new DOMException("verweigert", "NotAllowedError"))));
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

/** Die Live-Region des Chips: der einzige Bereich mit aria-live in der Kopfzeile des Codes. */
const chip = (container: HTMLElement) => container.querySelector<HTMLElement>('[aria-live="polite"]')!;

async function kopierenKlicken() {
  // Der Klick startet einen asynchronen Aufruf (writeText): act wartet, bis der Zustand gesetzt ist
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Kopieren" }));
  });
}

describe("Canvas, Kopierknopf", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetWamo();
    codeImCanvas();
  });
  afterEach(() => {
    vi.useRealTimers();
    resetWamo();
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("hat die Live-Region von Anfang an im Dokument und leer (sonst wird die erste Bestätigung überhört)", () => {
    const { container } = render(<CanvasBereich />);
    const live = chip(container);
    expect(live).not.toBeNull();
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.textContent).toBe("");
  });

  it("zeigt nach dem Kopieren den Chip Kopiert und hat den Text in die Zwischenablage gegeben", async () => {
    const writeText = zwischenablage("ok");
    const { container } = render(<CanvasBereich />);
    await kopierenKlicken();
    expect(writeText).toHaveBeenCalledExactlyOnceWith(CODE);
    expect(chip(container).textContent).toBe("Kopiert");
    expect(chip(container).getAttribute("aria-live")).toBe("polite");
  });

  it("blendet den Chip nach 1,5 Sekunden wieder aus, die Live-Region bleibt stehen", async () => {
    zwischenablage("ok");
    const { container } = render(<CanvasBereich />);
    const live = chip(container);
    await kopierenKlicken();
    act(() => {
      vi.advanceTimersByTime(KOPIERT_MS - 1);
    });
    expect(live.textContent).toBe("Kopiert");
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(live.textContent).toBe("");
    expect(chip(container)).toBe(live); // derselbe Knoten: nur der Text wechselt, die Region wird nicht neu eingefügt
  });

  it("belässt die Beschriftung des Knopfs auf Kopieren, vorher, während der Bestätigung und danach", async () => {
    zwischenablage("ok");
    render(<CanvasBereich />);
    const knopf = screen.getByRole("button", { name: "Kopieren" });
    expect(knopf.textContent).toBe("Kopieren");
    await kopierenKlicken();
    expect(screen.getByRole("button", { name: "Kopieren" })).toBe(knopf);
    expect(knopf.textContent).toBe("Kopieren");
    act(() => {
      vi.advanceTimersByTime(KOPIERT_MS);
    });
    expect(knopf.textContent).toBe("Kopieren");
  });

  it("meldet Nicht kopiert, wenn die Zwischenablage nicht erlaubt ist, und markiert den Chip als Fehler", async () => {
    zwischenablage("abgelehnt");
    const { container } = render(<CanvasBereich />);
    await kopierenKlicken();
    expect(chip(container).textContent).toBe("Nicht kopiert");
    expect(chip(container).getAttribute("data-art")).toBe("fehler");
    expect(screen.getByRole("button", { name: "Kopieren" }).textContent).toBe("Kopieren");
  });

  it("beginnt die 1,5 Sekunden bei einem zweiten Klick neu", async () => {
    zwischenablage("ok");
    const { container } = render(<CanvasBereich />);
    await kopierenKlicken();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    await kopierenKlicken();
    act(() => {
      vi.advanceTimersByTime(1000); // 2 s seit dem ersten, 1 s seit dem zweiten Klick
    });
    expect(chip(container).textContent).toBe("Kopiert");
    act(() => {
      vi.advanceTimersByTime(KOPIERT_MS - 1000);
    });
    expect(chip(container).textContent).toBe("");
  });

  it("lässt beim Schließen keinen Zeitgeber zurück", async () => {
    zwischenablage("ok");
    const { unmount } = render(<CanvasBereich />);
    await kopierenKlicken();
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
