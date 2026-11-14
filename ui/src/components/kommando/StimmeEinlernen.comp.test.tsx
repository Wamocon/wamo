// Komponententest der Einlernansicht (components/kommando/StimmeEinlernen.tsx, lib/stimme.ts): erst die Einwilligung (Text vom Kern, Haken, dann
// Start), danach Vorlesetext, Fortschrittsbalken, Statuszeile und Abbrechen oder Schließen; nach dem Schließen der Fokus auf dem öffnenden Knopf
// oder der Überschrift des Kerns (kommando/KernOderEinlernen.tsx).
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StimmeAbschnitt } from "@/components/einstellungen/StimmeAbschnitt";
import { KernOderEinlernen } from "@/components/kommando/KernOderEinlernen";
import { StimmeEinlernen } from "@/components/kommando/StimmeEinlernen";
import { abdruckAnsichtOeffnen, abdruckAnsichtSchliessen, applyServerMessage, nachrichtAbgelehnt, resetWamo, wamo } from "@/lib/wamo";

const EINWILLIGUNG = "W.A.I.S. speichert aus deiner Stimme einen Abdruck.\nDu kannst ihn jederzeit löschen.";

function aktionen() {
  return { start: vi.fn(() => true), abbrechen: vi.fn(() => true), loeschen: vi.fn(() => true) };
}

/** Sitzung steht, Stimmerkennung meldet "fehlt", die Ansicht ist geöffnet (Einwilligung); mit text=false kam kein Einwilligungstext. */
function oeffnen(text = true) {
  wamo.set((s) => ({ ...s, presence: "idle" }));
  applyServerMessage({ type: "stimme_status", status: "fehlt", modus: "streng" });
  if (text) applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: EINWILLIGUNG });
  abdruckAnsichtOeffnen();
}
const phase = (p: "start" | "aufnahme" | "rechnet" | "fertig" | "fehler", extra: Record<string, unknown> = {}) =>
  act(() => applyServerMessage({ type: "stimmabdruck", status: p, ...extra } as Parameters<typeof applyServerMessage>[0]));

beforeEach(() => resetWamo());

describe("StimmeEinlernen: ohne geöffnete Ansicht", () => {
  it("zeigt nichts", () => {
    const { container } = render(<StimmeEinlernen aktionen={aktionen()} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("StimmeEinlernen: Einwilligung", () => {
  it("zeigt den Text vom Kern mit Zeilenumbruch, den Haken und den Start (gesperrt, bis der Haken sitzt)", () => {
    oeffnen();
    render(<StimmeEinlernen aktionen={aktionen()} />);
    expect(screen.getByRole("heading", { level: 2, name: "Stimme einlernen" })).toBeTruthy();
    expect(screen.getByText("Einwilligung", { selector: ".hud-badge" })).toBeTruthy();
    const text = document.querySelector(".kz-einlernen-einwilligung") as HTMLElement;
    expect(text.textContent).toBe(EINWILLIGUNG);
    const haken = screen.getByRole("checkbox", { name: /Ich habe den Text gelesen und willige ein/ }) as HTMLInputElement;
    expect(haken.checked).toBe(false);
    expect((screen.getByRole("button", { name: "Einlernen starten" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("schickt den Start erst nach dem Haken und mit der Version des Textes", () => {
    oeffnen();
    const a = aktionen();
    render(<StimmeEinlernen aktionen={a} />);
    const start = screen.getByRole("button", { name: "Einlernen starten" }) as HTMLButtonElement;
    fireEvent.click(start);
    expect(a.start).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(start.disabled).toBe(false);
    fireEvent.click(start);
    expect(a.start).toHaveBeenCalledExactlyOnceWith("stimme-1");
  });

  it("nimmt den Haken zurück, sperrt den Start wieder", () => {
    oeffnen();
    render(<StimmeEinlernen aktionen={aktionen()} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("checkbox"));
    expect((screen.getByRole("button", { name: "Einlernen starten" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("wartet ehrlich auf den Text vom Kern: ohne ihn gibt es weder Haken noch Start", () => {
    oeffnen(false);
    render(<StimmeEinlernen aktionen={aktionen()} />);
    expect(screen.getByText(/Einwilligungstext/)).toBeTruthy();
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Einlernen starten" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("ein neuer Einwilligungstext (neue Version) nimmt den Haken zurück, der Start gilt dann dem neuen Text", () => {
    oeffnen();
    const a = aktionen();
    render(<StimmeEinlernen aktionen={a} />);
    const haken = screen.getByRole("checkbox") as HTMLInputElement;
    fireEvent.click(haken);
    expect(haken.checked).toBe(true);
    act(() => applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-2", text: "Neuer Text." }));
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    const start = screen.getByRole("button", { name: "Einlernen starten" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(start);
    expect(a.start).toHaveBeenCalledExactlyOnceWith("stimme-2");
  });

  it("lehnt die Oberfläche den Einwilligungstext ab, steht ein Hinweis statt 'wird vom Kern geladen'", () => {
    oeffnen(false);
    render(<StimmeEinlernen aktionen={aktionen()} />);
    expect(screen.getByText(/wird vom Kern geladen/)).toBeTruthy();
    act(() => nachrichtAbgelehnt({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: "x".repeat(5000) }));
    expect(screen.queryByText(/wird vom Kern geladen/)).toBeNull();
    expect(screen.getByRole("alert").textContent).toMatch(/Einwilligungstext .*ungültig/);
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Einlernen starten" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("Abbrechen schließt, ohne zu starten", () => {
    oeffnen();
    const a = aktionen();
    render(<StimmeEinlernen aktionen={a} />);
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(a.abbrechen).toHaveBeenCalledTimes(1);
    expect(a.start).not.toHaveBeenCalled();
  });

  it("sagt, wenn der Start nicht hinausging", () => {
    oeffnen();
    const a = { ...aktionen(), start: vi.fn(() => false) };
    render(<StimmeEinlernen aktionen={a} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Einlernen starten" }));
    expect(screen.getByRole("alert").textContent).toMatch(/nicht gestartet/);
  });

  it("setzt den Fokus beim Öffnen auf die Überschrift", () => {
    oeffnen();
    render(<StimmeEinlernen aktionen={aktionen()} />);
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 2 }));
  });
});

describe("StimmeEinlernen: Fokus nach Schließen", () => {
  /** Aktionen wie in useWamo: Abbrechen und Schließen machen die Ansicht zu. */
  function schliessende() {
    return { ...aktionen(), abbrechen: vi.fn(() => (abdruckAnsichtSchliessen(), true)) };
  }
  const kernTitel = () => screen.getByRole("heading", { level: 2, name: "Gesprächszustand" });
  /** Wie ein Klick mit Maus oder Tastatur: der Knopf hat den Fokus, dann verschwindet er mit der Ansicht. */
  function klicken(name: string) {
    const knopf = screen.getByRole("button", { name });
    knopf.focus();
    fireEvent.click(knopf);
  }

  it("Abbrechen in der Einwilligung: ohne öffnenden Knopf geht der Fokus auf die Überschrift des Kerns", () => {
    oeffnen();
    render(<KernOderEinlernen stimmabdruck={schliessende()} />);
    klicken("Abbrechen");
    expect(wamo.get().abdruck).toBeNull();
    expect(document.activeElement).toBe(kernTitel());
  });

  it("Schließen nach dem Ergebnis: der Fokus geht zurück auf den Knopf, der das Einlernen geöffnet hat", () => {
    wamo.set((s) => ({ ...s, presence: "idle" }));
    applyServerMessage({ type: "stimme_status", status: "fehlt", modus: "streng" });
    applyServerMessage({ type: "stimmabdruck_einwilligung", version: "stimme-1", text: EINWILLIGUNG });
    const a = schliessende();
    render(
      <>
        <StimmeAbschnitt aktionen={a} />
        <KernOderEinlernen stimmabdruck={a} />
      </>,
    );
    klicken("Stimme einlernen");
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 2, name: "Stimme einlernen" }));
    act(() => wamo.set((s) => ({ ...s, abdruck: { phase: "fertig", sekunden: 20, ziel: 20 } })));
    klicken("Schließen");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Stimme einlernen" }));
  });

  it("steht der Fokus woanders (zum Beispiel im Eingabefeld), bleibt er dort, wenn der Kern die Ansicht schließt", () => {
    oeffnen();
    render(
      <>
        <input aria-label="Nachricht" />
        <KernOderEinlernen stimmabdruck={schliessende()} />
      </>,
    );
    const feld = screen.getByRole("textbox", { name: "Nachricht" });
    feld.focus();
    act(() => applyServerMessage({ type: "stimmabdruck", status: "abgebrochen" }));
    expect(document.activeElement).toBe(feld);
  });
});

describe("StimmeEinlernen: Aufnahme", () => {
  function laufen() {
    oeffnen();
    wamo.set((s) => ({ ...s, abdruck: { phase: "start", sekunden: 0, ziel: 20 } }));
    const a = aktionen();
    render(<StimmeEinlernen aktionen={a} />);
    return a;
  }

  it("zeigt den Vorlesetext und den Balken mit Sekunden, die Statuszeile ist eine höfliche Live-Region", () => {
    laufen();
    phase("aufnahme", { sekunden: 8.4, ziel: 20 });
    expect(screen.getByText(/Einst stritten sich Nordwind und Sonne/)).toBeTruthy();
    const balken = screen.getByRole("progressbar", { name: "Aufgenommene Sprache in Sekunden" });
    expect(balken.getAttribute("aria-valuenow")).toBe("8");
    expect(balken.getAttribute("aria-valuemax")).toBe("20");
    expect(balken.getAttribute("aria-valuetext")).toBe("8 von 20 Sekunden");
    expect((balken.firstElementChild as HTMLElement).style.width).toBe("42%");
    expect(screen.getByText("8 / 20 s", { selector: ".hud-badge" })).toBeTruthy();
    const status = document.querySelector(".kz-einlernen-status") as HTMLElement;
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toMatch(/Ich höre zu/);
  });

  it("zeigt in der Einwilligung nicht den Vorlesetext", () => {
    oeffnen();
    render(<StimmeEinlernen aktionen={aktionen()} />);
    expect(screen.queryByText(/Einst stritten sich Nordwind und Sonne/)).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("bietet bis zum Ergebnis Abbrechen an", () => {
    const a = laufen();
    phase("aufnahme", { sekunden: 3, ziel: 20 });
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(a.abbrechen).toHaveBeenCalledTimes(1);
  });

  it("zeigt das Ergebnis mit Schließen", () => {
    const a = laufen();
    phase("fertig", { sekunden: 20, ziel: 20 });
    expect(screen.getByText("Fertig", { selector: ".hud-badge" })).toBeTruthy();
    expect(document.querySelector(".kz-einlernen-status")?.textContent).toMatch(/erkennt jetzt deine Stimme/);
    expect(screen.queryByRole("button", { name: "Abbrechen" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
    expect(a.abbrechen).toHaveBeenCalledTimes(1);
  });

  it("zeigt bei einem Fehler den Satz des Kerns und bietet einen neuen Anlauf über die Einwilligung an", () => {
    laufen();
    phase("fehler", { text: "Zu wenig Sprache." });
    expect(screen.getByText("Fehler", { selector: ".hud-badge" })).toBeTruthy();
    expect(within(document.querySelector(".kz-einlernen") as HTMLElement).getByText("Zu wenig Sprache.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Noch einmal" }));
    expect(wamo.get().abdruck?.phase).toBe("einwilligung");
  });
});
