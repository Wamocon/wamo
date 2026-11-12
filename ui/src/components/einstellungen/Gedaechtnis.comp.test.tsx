// Komponententest des Abschnitts "Gedächtnis" (components/einstellungen/Gedaechtnis.tsx): Laden, Liste, Freigeben und Ablehnen, Fehler, Fokus.
// Der Abruf beim Kern (lib/gedaechtnisApi.ts) ist durch eine Attrappe ersetzt.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { vorschlaegeLaden, vorschlagEntscheiden } from "@/lib/gedaechtnisApi";
import type { Vorschlag } from "@/lib/gedaechtnis";
import { GedaechtnisAbschnitt } from "./Gedaechtnis";

vi.mock("@/lib/gedaechtnisApi", () => ({ vorschlaegeLaden: vi.fn(), vorschlagEntscheiden: vi.fn() }));

const ID1 = "5b3f1c2d-8e7a-4b6c-9d0e-1f2a3b4c5d6e";
const ID2 = "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const eins: Vorschlag = { id: ID1, art: "lesson", text: "WAMO kann Fernseher schalten.", autor: "Anna", angelegt: "2026-11-12T06:58:48Z" };
const zwei: Vorschlag = { id: ID2, art: "fact", text: "Die Sparks heißen Sokrates 1, Sokrates 2 und Caesar.", autor: "Niko", angelegt: "" };

function lade(liste: Vorschlag[]) {
  vi.mocked(vorschlaegeLaden).mockResolvedValue({ ok: true, vorschlaege: liste });
}

async function zeige(liste: Vorschlag[] = [eins, zwei]) {
  lade(liste);
  const user = userEvent.setup();
  render(<GedaechtnisAbschnitt />);
  await screen.findByText(/auf dich\.|Es gibt keine offenen Vorschläge/);
  return user;
}

const karte = (text: string) => within(screen.getByText(text).closest("li") as HTMLElement);

describe("GedaechtnisAbschnitt", () => {
  beforeEach(() => {
    vi.mocked(vorschlaegeLaden).mockReset();
    vi.mocked(vorschlagEntscheiden).mockReset().mockResolvedValue({ ok: true, ergebnis: "freigegeben" });
  });

  it("zeigt erst den Ladehinweis, dann die Vorschläge mit Art, Autor und vollem Text", async () => {
    let fertig: (v: { ok: true; vorschlaege: Vorschlag[] }) => void = () => undefined;
    vi.mocked(vorschlaegeLaden).mockReturnValue(
      new Promise((r) => {
        fertig = r;
      }),
    );
    render(<GedaechtnisAbschnitt />);
    expect(screen.getByText("Lade die Vorschläge ...")).toBeTruthy();
    await act(async () => fertig({ ok: true, vorschlaege: [eins, zwei] }));
    expect(await screen.findByText("WAMO kann Fernseher schalten.")).toBeTruthy();
    expect(screen.getByText("Lektion")).toBeTruthy();
    expect(screen.getByText("von Anna")).toBeTruthy();
    expect(screen.getByText("2 Vorschläge warten auf dich.")).toBeTruthy();
    expect(screen.getByText(/Lies jeden Eintrag genau/)).toBeTruthy();
  });

  it("jeder Vorschlag hat zwei gleich gewichtete Knöpfe", async () => {
    await zeige();
    for (const t of ["WAMO kann Fernseher schalten.", "Die Sparks heißen Sokrates 1, Sokrates 2 und Caesar."]) {
      const k = karte(t).getAllByRole("button");
      expect(k.map((b) => b.textContent)).toEqual(["Für alle freigeben", "Ablehnen"]);
      expect(k[0].className).toBe(k[1].className);
    }
  });

  it("ohne Vorschläge steht der Leerzustand da und keine Liste", async () => {
    await zeige([]);
    expect(screen.getByText("Es gibt keine offenen Vorschläge.")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("ein Fehler beim Laden steht als Hinweis da, ohne Liste", async () => {
    vi.mocked(vorschlaegeLaden).mockResolvedValue({ ok: false, text: "Die Vorschläge sind gerade nicht verfügbar" });
    render(<GedaechtnisAbschnitt />);
    expect((await screen.findByRole("alert")).textContent).toBe("Die Vorschläge sind gerade nicht verfügbar");
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("der Text eines Vorschlags erscheint nur als Text, nie als HTML", async () => {
    const boese: Vorschlag = { ...eins, text: "<img src=x onerror=alert(1)> und <b>fett</b>" };
    await zeige([boese]);
    expect(screen.getByText("<img src=x onerror=alert(1)> und <b>fett</b>")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("li b")).toBeNull();
  });

  it("Freigeben: Aufruf mit Kennung und Entscheidung, der Vorschlag verschwindet, die Meldung steht im Statusbereich", async () => {
    const user = await zeige();
    await user.click(karte("WAMO kann Fernseher schalten.").getByRole("button", { name: "Für alle freigeben" }));
    expect(vorschlagEntscheiden).toHaveBeenCalledWith(ID1, "freigeben");
    const meldung = await screen.findByText("Freigegeben. Der Eintrag gilt für alle Profile in neuen Gesprächen.");
    expect(meldung.getAttribute("role")).toBe("status");
    expect(screen.queryByText("WAMO kann Fernseher schalten.")).toBeNull();
    expect(screen.getByText("1 Vorschlag wartet auf dich.")).toBeTruthy();
  });

  it("Ablehnen: Aufruf mit ablehnen und der passende Satz", async () => {
    vi.mocked(vorschlagEntscheiden).mockResolvedValue({ ok: true, ergebnis: "abgelehnt" });
    const user = await zeige();
    await user.click(karte("Die Sparks heißen Sokrates 1, Sokrates 2 und Caesar.").getByRole("button", { name: "Ablehnen" }));
    expect(vorschlagEntscheiden).toHaveBeenCalledWith(ID2, "ablehnen");
    expect(await screen.findByText("Abgelehnt. Der Eintrag wird nie benutzt.")).toBeTruthy();
    expect(screen.queryByText("Die Sparks heißen Sokrates 1, Sokrates 2 und Caesar.")).toBeNull();
  });

  it("war der Vorschlag schon entschieden (nicht_offen), lädt die Liste neu", async () => {
    vi.mocked(vorschlagEntscheiden).mockResolvedValue({ ok: true, ergebnis: "nicht_offen" });
    const user = await zeige();
    lade([zwei]);
    await user.click(karte("WAMO kann Fernseher schalten.").getByRole("button", { name: "Für alle freigeben" }));
    expect(await screen.findByText("Dieser Vorschlag war schon entschieden. Die Liste ist neu geladen.")).toBeTruthy();
    expect(vorschlaegeLaden).toHaveBeenCalledTimes(2);
  });

  it("ein Fehler beim Entscheiden lässt den Vorschlag stehen, nennt den Grund und gibt die Knöpfe wieder frei", async () => {
    vi.mocked(vorschlagEntscheiden).mockResolvedValue({ ok: false, text: "Vorschläge prüfen dürfen nur Admins." });
    const user = await zeige();
    await user.click(karte("WAMO kann Fernseher schalten.").getByRole("button", { name: "Ablehnen" }));
    expect(await screen.findByText("Vorschläge prüfen dürfen nur Admins.")).toBeTruthy();
    expect(screen.getByText("WAMO kann Fernseher schalten.")).toBeTruthy();
    for (const b of screen.getAllByRole("button", { name: /freigeben|Ablehnen/ })) expect(b.getAttribute("aria-disabled")).toBeNull();
  });

  it("ein Doppelklick entscheidet einmal, und während des Speicherns sind alle Knöpfe aria-disabled", async () => {
    let fertig: (v: { ok: true; ergebnis: "freigegeben" }) => void = () => undefined;
    vi.mocked(vorschlagEntscheiden).mockReturnValue(
      new Promise((r) => {
        fertig = r;
      }),
    );
    const user = await zeige();
    await user.dblClick(karte("WAMO kann Fernseher schalten.").getByRole("button", { name: "Für alle freigeben" }));
    expect(vorschlagEntscheiden).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Wird gespeichert ...")).toBeTruthy();
    for (const b of screen.getAllByRole("button", { name: /freigeben|Ablehnen|Wird gespeichert/ })) expect(b.getAttribute("aria-disabled")).toBe("true");
    await user.click(karte("Die Sparks heißen Sokrates 1, Sokrates 2 und Caesar.").getByRole("button", { name: "Ablehnen" }));
    expect(vorschlagEntscheiden).toHaveBeenCalledTimes(1);
    await act(async () => fertig({ ok: true, ergebnis: "freigegeben" }));
    expect(await screen.findByText(/^Freigegeben\./)).toBeTruthy();
  });

  it("nach dem Entscheiden geht der Fokus an den nächsten Vorschlag, beim letzten an Neu laden", async () => {
    const user = await zeige();
    await user.click(karte("WAMO kann Fernseher schalten.").getByRole("button", { name: "Für alle freigeben" }));
    await screen.findByText(/^Freigegeben\./);
    expect(document.activeElement).toBe(karte("Die Sparks heißen Sokrates 1, Sokrates 2 und Caesar.").getAllByRole("button")[0]);
    vi.mocked(vorschlagEntscheiden).mockResolvedValue({ ok: true, ergebnis: "abgelehnt" });
    await user.click(karte("Die Sparks heißen Sokrates 1, Sokrates 2 und Caesar.").getByRole("button", { name: "Ablehnen" }));
    await screen.findByText(/^Abgelehnt\./);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Neu laden" }));
  });

  it("Neu laden holt die Liste noch einmal", async () => {
    const user = await zeige();
    lade([zwei]);
    await user.click(screen.getByRole("button", { name: "Neu laden" }));
    expect(await screen.findByText("1 Vorschlag wartet auf dich.")).toBeTruthy();
    expect(vorschlaegeLaden).toHaveBeenCalledTimes(2);
  });
});
