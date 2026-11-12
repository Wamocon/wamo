// Komponententest der Karte Fernzugriff (components/einstellungen/Fernzugriff.tsx, lib/fernzugriff.ts), KP-2: Der Dialog fragt den Code
// der Authenticator-App nur noch, wenn der Kern ihn verlangt (403 mit zweiter_faktor_zu_alt). Dazu Befund M7: Der Knopf "Verbinden" wird
// nie vorfokussiert, und weder das Zeigen der Karte (so öffnet WAMO sie mit fernzugriff_oeffnen) noch das Öffnen des Dialogs startet
// eine Sitzung; es braucht immer den Klick der Person. Ausführen im Ordner ui/:  npm run test:komponenten
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { Fernzugriff } from "@/components/einstellungen/Fernzugriff";
import type { Uebersicht } from "@/lib/fernzugriff";

const api = vi.hoisted(() => ({ zieleLaden: vi.fn(), starten: vi.fn(), beenden: vi.fn(), codeBestaetigen: vi.fn() }));
vi.mock("@/lib/fernzugriffApi", () => ({ zieleLaden: api.zieleLaden, starten: api.starten, beenden: api.beenden }));
vi.mock("@/lib/geraeteVerwaltungApi", () => ({ codeBestaetigen: api.codeBestaetigen }));

const SITZUNG = "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d";
const UEBERSICHT: Uebersicht = {
  aktiv: true,
  ziele: [{ name: "sokrates2", anzeige: "Sokrates-2", status: "frei", eigene: false, belegtVon: null, seit: null }],
  meine: null,
  maxMinuten: 60,
  leerlaufMinuten: 10,
};
const START = {
  ok: true,
  start: {
    sitzung: SITZUNG,
    ziel: "sokrates2",
    anzeige: "Sokrates-2",
    url: "https://192.168.178.136:55446/?token=AB12cd34EF56gh78IJ90kl12#/client/Zm9v0123",
    endet: "2026-11-11T21:00:00.000Z",
    maxMinuten: 60,
    leerlaufMinuten: 10,
  },
};
const CODE_VERLANGT = {
  ok: false,
  text: "Deine Anmeldung ist älter als 24 Stunden. Bitte gib den Code deiner Authenticator-App noch einmal ein. Er darf höchstens zwei Minuten alt sein.",
  dialogOffen: true,
  neuLaden: false,
  feld: "code",
};
const BELEGT = { ok: false, text: "Sokrates-2 ist belegt von Bernd.", dialogOffen: false, neuLaden: true, feld: null };

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

beforeEach(() => {
  api.zieleLaden.mockReset().mockResolvedValue(UEBERSICHT);
  api.starten.mockReset();
  api.beenden.mockReset();
  api.codeBestaetigen.mockReset().mockResolvedValue({ ok: true });
  vi.spyOn(window, "open").mockReturnValue(null); // das leere Fenster, das der Klick sofort öffnet
});

async function dialogOeffnen(user: ReturnType<typeof userEvent.setup>) {
  render(<Fernzugriff />);
  await user.click(await screen.findByRole("button", { name: "Verbinden: Sokrates-2" }));
  return screen.findByRole("dialog");
}

// Befund vom 12.11.2026: In der Desktop-App liefert window.open("", "_blank") immer null (der Hörer in desktop/main.js verweigert jedes
// Fenster und reicht nur http(s)-Adressen an den Browser weiter). Das leere Fenster gab es dort nie, die Adresse wurde nicht geöffnet,
// und die Sitzung endete nach etwa drei Minuten ohne Verbindung. Gemessen mit Electron: window.open("") und window.open(url) geben null.
describe("Fernzugriff: Fenster öffnen in der Desktop-App und im Browser", () => {
  afterEach(() => {
    delete (window as unknown as { wamoDesktop?: unknown }).wamoDesktop;
  });

  async function verbinden() {
    api.starten.mockResolvedValue(START);
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    await waitFor(() => expect(api.starten).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  }

  it("im Browser öffnet der Klick sofort ein leeres Fenster, nur dann erlaubt es der Browser", async () => {
    await verbinden();
    expect(window.open).toHaveBeenCalledTimes(1);
    expect(window.open).toHaveBeenCalledWith("", "_blank");
    expect(screen.getByText(/Fernzugriff auf Sokrates-2 gestartet/).textContent).toMatch(/blockiert/);
  });

  it("in der Desktop-App gibt es kein leeres Fenster: nach dem Start geht genau die Adresse des Gateways an den Browser", async () => {
    (window as unknown as { wamoDesktop: unknown }).wamoDesktop = { isDesktop: true };
    await verbinden();
    expect(vi.mocked(window.open).mock.calls.map((c) => c[0])).toEqual([START.start.url]);
    expect(window.open).toHaveBeenCalledWith(START.start.url, "_blank", "noopener,noreferrer");
    const meldung = screen.getByText(/Fernzugriff auf Sokrates-2 gestartet/).textContent ?? "";
    expect(meldung).toMatch(/im Browser/);
    expect(meldung).not.toMatch(/blockiert/);
  });

  it("in der Desktop-App kommt die Adresse erst nach dem Start und nie, wenn der Start scheitert", async () => {
    (window as unknown as { wamoDesktop: unknown }).wamoDesktop = { isDesktop: true };
    api.starten.mockResolvedValue(BELEGT);
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.open).not.toHaveBeenCalled();
  });

  it("wirft window.open in der Desktop-App, bleibt der Hinweis auf den Knopf Fenster öffnen", async () => {
    (window as unknown as { wamoDesktop: unknown }).wamoDesktop = { isDesktop: true };
    vi.mocked(window.open).mockImplementation(() => {
      throw new Error("nicht erlaubt");
    });
    await verbinden();
    expect(screen.getByText(/Fernzugriff auf Sokrates-2 gestartet/).textContent).toMatch(/Fenster öffnen/);
  });
});

describe("Fernzugriff: Verbinden ohne Code", () => {
  it("die Karte zeigen startet nie eine Sitzung und öffnet keinen Dialog (so öffnet WAMO sie mit fernzugriff_oeffnen)", async () => {
    render(<Fernzugriff />);
    expect(await screen.findByRole("button", { name: "Verbinden: Sokrates-2" })).toBeTruthy();
    await new Promise((r) => setTimeout(r, 50));
    expect(api.starten).not.toHaveBeenCalled();
    expect(api.codeBestaetigen).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByLabelText(/Code aus der Authenticator-App/)).toBeNull();
  });

  it("die Einleitung sagt: Verbinden genügt, der Code nur bei zu alter Anmeldung, jeder Start geht an alle Admins", async () => {
    render(<Fernzugriff />);
    const text = (await screen.findByRole("region", { name: "Fernzugriff" })).textContent ?? "";
    expect(text).toMatch(/„Verbinden“/);
    expect(text).toMatch(/Ist deine Anmeldung zu alt, fragt der Dialog den Code/);
    expect(text).toMatch(/allen Admins gemeldet/);
    expect(text).not.toMatch(/Jede Sitzung braucht/);
  });

  it("der Dialog hat kein Codefeld, der Fokus liegt auf Abbrechen und nie auf Verbinden, und es startet nichts ohne Klick", async () => {
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    expect(within(dialog).queryByLabelText(/Code aus der Authenticator-App/)).toBeNull();
    expect(within(dialog).queryByRole("textbox")).toBeNull();
    const verbinden = within(dialog).getByRole("button", { name: "Verbinden" });
    expect(document.activeElement).not.toBe(verbinden);
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Abbrechen" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(api.starten).not.toHaveBeenCalled();
  });

  it("Enter oder Leertaste im frisch geöffneten Dialog startet nichts (der Fokus liegt auf Abbrechen)", async () => {
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull()); // Enter auf Abbrechen schließt
    expect(api.starten).not.toHaveBeenCalled();
    expect(dialog.isConnected).toBe(false);
    await user.click(await screen.findByRole("button", { name: "Verbinden: Sokrates-2" }));
    await screen.findByRole("dialog");
    await user.keyboard(" ");
    expect(api.starten).not.toHaveBeenCalled();
  });

  it("der Klick auf Verbinden startet ohne den Code zu bestätigen", async () => {
    api.starten.mockResolvedValue(START);
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    await waitFor(() => expect(api.starten).toHaveBeenCalledTimes(1));
    expect(api.starten.mock.calls[0][0]).toBe("sokrates2");
    expect(api.codeBestaetigen).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText(/Fernzugriff auf Sokrates-2 gestartet/)).toBeTruthy();
  });

  it("ein Klick, der Teil einer Klickfolge ist (Doppelklick auf die Karte), startet nichts; ein Einzelklick danach startet", async () => {
    api.starten.mockResolvedValue(START);
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    const verbinden = within(dialog).getByRole("button", { name: "Verbinden" });
    fireEvent.click(verbinden, { detail: 2 }); // der zweite Klick des Doppelklicks trifft den eben geöffneten Dialog
    await new Promise((r) => setTimeout(r, 50));
    expect(api.starten).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeTruthy();
    await user.click(verbinden); // ein bewusster Einzelklick startet wie bisher
    await waitFor(() => expect(api.starten).toHaveBeenCalledTimes(1));
  });

  it("verlangt der Kern den Code, erscheint das Codefeld mit dem Satz des Kerns und der Fokus geht ins Feld", async () => {
    api.starten.mockResolvedValueOnce(CODE_VERLANGT);
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    const feld = await within(dialog).findByLabelText(/Code aus der Authenticator-App/);
    expect(document.activeElement).toBe(feld);
    expect(within(dialog).getByRole("alert").textContent).toMatch(/Deine Anmeldung ist älter als 24 Stunden/);
    expect(within(dialog).getByText(/höchstens zwei Minuten alt/, { selector: "span" })).toBeTruthy();
    expect(api.codeBestaetigen).not.toHaveBeenCalled(); // der erste Start ging ohne Code
    expect(api.starten).toHaveBeenCalledTimes(1);
  });

  it("mit Codefeld: erst der Code bei Supabase, dann der Start; ein falsches Format ruft nichts auf", async () => {
    api.starten.mockResolvedValueOnce(CODE_VERLANGT).mockResolvedValueOnce(START);
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    const feld = await within(dialog).findByLabelText(/Code aus der Authenticator-App/);
    await user.type(feld, "12ab");
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    expect(within(dialog).getByRole("alert").textContent).toMatch(/sechsstelligen Code/);
    expect(api.codeBestaetigen).not.toHaveBeenCalled();
    expect(api.starten).toHaveBeenCalledTimes(1);
    await user.clear(feld);
    await user.type(feld, "123 456");
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    await waitFor(() => expect(api.starten).toHaveBeenCalledTimes(2));
    expect(api.codeBestaetigen).toHaveBeenCalledWith("123456");
    expect(api.codeBestaetigen.mock.invocationCallOrder[0]).toBeLessThan(api.starten.mock.invocationCallOrder[1]);
  });

  it("ein anderer Fehler (belegt) zeigt kein Codefeld und schließt den Dialog", async () => {
    api.starten.mockResolvedValue(BELEGT);
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByLabelText(/Code aus der Authenticator-App/)).toBeNull();
    expect(screen.getByText(/ist belegt von Bernd/)).toBeTruthy();
  });

  it("ein einmal gezeigtes Codefeld bleibt, auch wenn der Code abgelehnt wird; der Start läuft nicht ohne bestätigten Code", async () => {
    api.starten.mockResolvedValueOnce(CODE_VERLANGT);
    api.codeBestaetigen.mockResolvedValue({ ok: false, text: "Der Code passt nicht. Nimm den aktuellen aus der App." });
    const user = userEvent.setup();
    const dialog = await dialogOeffnen(user);
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    const feld = await within(dialog).findByLabelText(/Code aus der Authenticator-App/);
    await user.type(feld, "123456");
    await user.click(within(dialog).getByRole("button", { name: "Verbinden" }));
    await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toMatch(/Der Code passt nicht/));
    expect(within(dialog).getByLabelText(/Code aus der Authenticator-App/)).toBeTruthy();
    expect(api.starten).toHaveBeenCalledTimes(1);
  });
});
