// Komponententest der Kopfzeile (components/Kopfzeile.tsx, lib/laborPlatz.ts): Ein normales Konto behält Wortmarke, Verlauf und "Abmelden"
// ohne Rückfrage. Ein Laborkonto sieht "WAMO Testlabor, Platz N", keinen Verlauf, und "Abmelden" fragt erst nach (Dialog, Escape und
// "Angemeldet bleiben" brechen ab, der Fokus kehrt zum Profil-Chip zurück, nur "Ja, abmelden" meldet ab).
// Ausführen im Ordner ui/:  npm run test:komponenten
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { Kopfzeile } from "@/components/Kopfzeile";
import { resetWamo } from "@/lib/wamo";

const NORMAL = "niko@wamocon.com";
const PLATZ = "labor-03@wamo.test";
const SCHAU = "labor-schau@wamo.test";

beforeAll(() => {
  // jsdom kennt <dialog> ohne showModal und close: Die Attrappe setzt nur das Attribut open
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    },
  });
});

beforeEach(() => resetWamo());

function zeige(email: string | null) {
  const onAbmelden = vi.fn();
  const onVerlauf = vi.fn();
  render(<Kopfzeile email={email} onAbmelden={onAbmelden} verlaufOffen={false} onVerlauf={onVerlauf} />);
  return { onAbmelden, onVerlauf };
}

/** Der Knopf "Abmelden" im Profil-Popover (jsdom zeigt Popover-Inhalt immer, der Knopf ist per Text eindeutig). */
const abmeldenKnopf = () => screen.getByText("Abmelden", { selector: "button" });
const chip = () => document.querySelector<HTMLElement>(".profile-chip");

describe("Kopfzeile eines normalen Kontos", () => {
  it("zeigt die Wortmarke WAMO und den Verlauf, wie bisher", () => {
    const { onVerlauf } = zeige(NORMAL);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("WAMO");
    fireEvent.click(screen.getByRole("button", { name: "Verlauf" }));
    expect(onVerlauf).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Verlauf öffnen", hidden: true })).toBeTruthy(); // im Menü (Popover)
  });

  it("meldet ohne Rückfrage ab", () => {
    const { onAbmelden } = zeige(NORMAL);
    fireEvent.click(abmeldenKnopf());
    expect(onAbmelden).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("ohne E-Mail (noch nicht bekannt) gilt dasselbe", () => {
    const { onAbmelden } = zeige(null);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("WAMO");
    fireEvent.click(abmeldenKnopf());
    expect(onAbmelden).toHaveBeenCalledTimes(1);
  });
});

describe("Kopfzeile eines Laborkontos", () => {
  it("nennt den Platz in der Überschrift", () => {
    zeige(PLATZ);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("WAMO Testlabor, Platz 3");
  });

  it("nennt beim Schaufenster nur das Testlabor", () => {
    zeige(SCHAU);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("WAMO Testlabor");
  });

  it("hat keinen Verlauf, weder als Knopf noch im Menü", () => {
    zeige(PLATZ);
    expect(screen.queryAllByRole("button", { name: /Verlauf/, hidden: true })).toHaveLength(0);
    expect(screen.queryByText(/Verlauf/)).toBeNull();
  });

  it("behält Einstellungen, Menü und Profil", () => {
    zeige(PLATZ);
    expect(screen.getByRole("link", { name: "Einstellungen" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Status und Menü" })).toBeTruthy();
    expect(chip()).not.toBeNull();
  });

  it("fragt vor dem Abmelden nach und meldet noch nicht ab", () => {
    const { onAbmelden } = zeige(PLATZ);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(abmeldenKnopf());
    const dialog = screen.getByRole("dialog", { name: "Wirklich abmelden?" });
    expect(dialog.textContent).toMatch(/Gespräch/);
    expect(screen.getByRole("button", { name: "Ja, abmelden" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Angemeldet bleiben" })).toBeTruthy();
    expect(onAbmelden).not.toHaveBeenCalled();
  });

  it("legt den Fokus in den Dialog, auf die ungefährliche Antwort", () => {
    zeige(PLATZ);
    fireEvent.click(abmeldenKnopf());
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Angemeldet bleiben" }));
  });

  it("verbindet den Dialog mit Titel und Text für Screenreader", () => {
    zeige(PLATZ);
    fireEvent.click(abmeldenKnopf());
    const dialog = screen.getByRole("dialog");
    const beschreibung = dialog.getAttribute("aria-describedby");
    expect(beschreibung && document.getElementById(beschreibung)?.textContent).toMatch(/neu anmelden/);
  });

  it("Escape bricht ab: kein Abmelden, Dialog zu, Fokus zurück beim Profil-Chip", async () => {
    const user = userEvent.setup();
    const { onAbmelden } = zeige(PLATZ);
    await user.click(abmeldenKnopf());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onAbmelden).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(chip());
  });

  it("\"Angemeldet bleiben\" bricht ab: kein Abmelden, Dialog zu, Fokus zurück beim Profil-Chip", async () => {
    const user = userEvent.setup();
    const { onAbmelden } = zeige(PLATZ);
    await user.click(abmeldenKnopf());
    await user.click(screen.getByRole("button", { name: "Angemeldet bleiben" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onAbmelden).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(chip());
  });

  it("\"Ja, abmelden\" meldet genau einmal ab", async () => {
    const user = userEvent.setup();
    const { onAbmelden } = zeige(PLATZ);
    await user.click(abmeldenKnopf());
    await user.click(screen.getByRole("button", { name: "Ja, abmelden" }));
    expect(onAbmelden).toHaveBeenCalledTimes(1);
  });

  it("ein zweites Abmelden nach dem Abbruch fragt wieder", async () => {
    const user = userEvent.setup();
    const { onAbmelden } = zeige(PLATZ);
    await user.click(abmeldenKnopf());
    await user.keyboard("{Escape}");
    await user.click(abmeldenKnopf());
    expect(screen.getByRole("dialog", { name: "Wirklich abmelden?" })).toBeTruthy();
    expect(onAbmelden).not.toHaveBeenCalled();
  });

  it("die Texte der Rückfrage haben keinen Gedankenstrich", () => {
    zeige(PLATZ);
    fireEvent.click(abmeldenKnopf());
    expect(screen.getByRole("dialog").textContent ?? "").not.toMatch(/[–—]|\s-\s/);
  });
});
