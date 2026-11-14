// Komponententest der Hauptansicht für Laborkonten gegen ein normales Admin-Konto (components/WamoAnsicht.tsx, lib/laborPlatz.ts): Das Laborkonto
// sieht keine Büro-Karte, keinen Verlauf, keine Vorgänge, keine Admin-Hinweise, keine Geräteeinrichtung und keinen Modellzugang, ruft dafür
// nichts beim Kern ab, hat in den Einstellungen nur die Mikrofonwahl und meldet erst nach Rückfrage ab. Freigaben bleiben sichtbar (dort
// entscheidet der Besucher). Das normale Admin-Konto sieht alles wie bisher. Nur Darstellung: der Kern sperrt weiter selbst.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { WamoAnsicht } from "@/components/WamoAnsicht";
import { geraeteAnzeige } from "@/lib/geraeteAnzeige";
import { KernFehler } from "@/lib/kernApi";
import type { WamoAktionen } from "@/lib/useWamo";
import { resetWamo, wamo, type Approval } from "@/lib/wamo";

const attrappe = vi.hoisted(() => ({ kernJson: vi.fn(), getSupabase: vi.fn() }));
vi.mock("@/lib/kernApi", async (original) => ({ ...(await original<typeof import("@/lib/kernApi")>()), kernJson: attrappe.kernJson }));
vi.mock("@/lib/supabase", async (original) => ({ ...(await original<typeof import("@/lib/supabase")>()), getSupabase: attrappe.getSupabase }));

const PLATZ = "labor-03@wamo.test";
const SCHAU = "labor-schau@wamo.test";
const ADMIN = "niko@wamocon.com";
const HINWEIS_ID = "0b7f3c1e-2d4a-4c55-9a10-1a2b3c4d5e6f";

const FREIGABE: Approval = {
  job: "labor",
  projekt: "",
  id: "0123456789abcdef0123456789abcdef",
  grund: "Ticket anlegen",
  detail: "Typ: Bug\nTitel: Anmeldung schlägt fehl",
  ziel: "Test-Jira, Platz 3",
};
const AKTIVITAET = { id: "c1", name: "ticket_anlegen", detail: "", status: "wartet" as const, turn: 0 };

beforeAll(() => {
  // jsdom kennt Element.scrollTo nicht, die Einstellungsseite ruft es beim Öffnen und Wechseln des Abschnitts
  if (typeof Element.prototype.scrollTo !== "function") Element.prototype.scrollTo = () => undefined;
  window.scrollTo = () => undefined;
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
  resetWamo();
  localStorage.clear();
  history.replaceState(null, "", "#");
  geraeteAnzeige.set(() => ({ zustand: "nicht_noetig", tunnel: null, kennung: null }));
  attrappe.getSupabase.mockReset().mockReturnValue(null);
  // Der Kern liefert für Hinweise einen offenen Hinweis, sonst Fehler (die bleiben still); es geht um die Aufrufe
  attrappe.kernJson.mockReset().mockImplementation(async (pfad: string) => {
    if (pfad === "/hinweise") {
      return { hinweise: [{ id: HINWEIS_ID, art: "teilung_erhalten", titel: "Neue Datei geteilt", text: "", erstellt_at: "2026-11-12T08:00:00Z", gelesen: false }] };
    }
    throw new KernFehler(503, "aus");
  });
});

afterEach(() => {
  history.replaceState(null, "", "#");
});

function aktionen(): WamoAktionen {
  return {
    connect: vi.fn(async () => undefined),
    disconnect: vi.fn(async () => undefined),
    decide: vi.fn(() => true),
    toggleMic: vi.fn(),
    vergessen: vi.fn(),
    entscheideAuftrag: vi.fn(() => true),
    pcSteuerung: vi.fn(() => true),
    schliesseFehler: vi.fn(),
    mikrofone: vi.fn(async () => []),
    mikrofonWaehlen: vi.fn(() => true),
  };
}

function zeige(email: string, opt: { rolle?: "admin" | "mitglied"; hash?: string } = {}) {
  if (opt.hash) history.replaceState(null, "", opt.hash);
  wamo.set((s) => ({ ...s, profil: { name: "Test Person", rolle: opt.rolle ?? (email === ADMIN ? "admin" : "mitglied") } }));
  const a = aktionen();
  const signOut = vi.fn();
  const ergebnis = render(<WamoAnsicht aktionen={a} sitzung={{ email, signOut }} />);
  return { a, signOut, ...ergebnis };
}

const pfade = () => attrappe.kernJson.mock.calls.map((c) => c[0] as string);
const tab = (name: string) => screen.queryAllByRole("tab", { name, hidden: true });
const verlaufKnoepfe = () => screen.queryAllByRole("button", { name: /Verlauf/, hidden: true });
const abmeldenKnopf = () => screen.getByText("Abmelden", { selector: ".profile-pop button" });
/** Warten, bis die Abrufe der Hauptansicht (Effekte beim Einhängen) durch sind. */
const ruhe = () => act(async () => void (await Promise.resolve()));

describe("Hauptansicht eines Laborkontos: was fehlt", () => {
  it("hat keine Reiter Vorgänge und Büro, keine Tafeln dazu, keinen Verlauf", async () => {
    zeige(PLATZ);
    await ruhe();
    expect(tab("Vorgänge")).toHaveLength(0);
    expect(tab("Büro")).toHaveLength(0);
    expect(document.getElementById("kz-p-vorgaenge")).toBeNull();
    expect(document.getElementById("kz-p-buero")).toBeNull();
    expect(document.getElementById("verlauf")).toBeNull();
    expect(verlaufKnoepfe()).toHaveLength(0);
    expect(screen.queryByText(/Noch keine Vorgänge/)).toBeNull();
    // ohne Verlauf kürzt der Block Gespräch lange Antworten nicht
    expect(document.querySelector(".kz-gespraech")?.hasAttribute("data-verlauf")).toBe(false);
  });

  it("nennt in der Reiterleiste nur die Reiter, die es gibt", async () => {
    zeige(PLATZ);
    await ruhe();
    expect(screen.getByRole("tablist", { name: "Aufträge und Erledigt", hidden: true })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: /Büro/, hidden: true })).toBeNull();
  });

  it("ruft Verlauf, Modellzugang, Büro und Hinweise nicht ab", async () => {
    zeige(PLATZ);
    await ruhe();
    for (const verboten of ["/vorgaenge", "/modellzugang", "/buero/status", "/hinweise"]) {
      expect(pfade(), verboten).not.toContain(verboten);
    }
  });

  it("hängt die Geräteeinrichtung nicht ein: ihr Stand bleibt unberührt und ihre Zeile fehlt", async () => {
    // Die Einrichtung setzt ihren Stand beim Einhängen zurück; bleibt er stehen, wurde sie nie eingehängt
    geraeteAnzeige.set(() => ({ zustand: "wartet", tunnel: null, kennung: null }));
    zeige(PLATZ);
    await ruhe();
    expect(geraeteAnzeige.get().zustand).toBe("wartet");
    expect(screen.queryByText(/wartet auf die Freigabe durch einen Admin/)).toBeNull();
  });

  it("zeigt in der linken Spalte keine Zeile zum Modellzugang, die ewig lädt", async () => {
    zeige(PLATZ);
    await ruhe();
    expect(screen.queryByText("Modellzugang")).toBeNull();
    expect(screen.queryByText("wird geladen")).toBeNull();
    // der Weg zu den Einstellungen bleibt
    expect(screen.getByRole("link", { name: "Einstellungen öffnen" })).toBeTruthy();
  });

  it("zeigt keine Zeitleiste der Werkzeuge, wohl aber die Freigabekarte (dort entscheidet der Besucher)", async () => {
    zeige(PLATZ);
    await ruhe();
    act(() => wamo.set((s) => ({ ...s, activities: [AKTIVITAET], approvals: [FREIGABE] })));
    expect(screen.queryByText("bereitet ein Ticket vor")).toBeNull();
    expect(screen.getByRole("heading", { name: "Ticket anlegen" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Freigeben" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ablehnen" })).toBeTruthy();
  });

  it("zeigt in den Meldungen der Einstellungen weder Hinweiszähler noch Geräteeinrichtung", async () => {
    zeige(PLATZ, { hash: "#einstellungen" });
    await ruhe();
    act(() => geraeteAnzeige.set(() => ({ zustand: "wartet", tunnel: null, kennung: null })));
    const meldungen = document.querySelector<HTMLElement>(".ez-meldungen");
    expect(meldungen).not.toBeNull();
    expect(within(meldungen!).queryByText(/wartet auf die Freigabe durch einen Admin/)).toBeNull();
    expect(within(meldungen!).queryByText(/offene[nr]? Hinweis/)).toBeNull();
  });
});

describe("Hauptansicht eines Laborkontos: was bleibt", () => {
  it("zeigt Kopfzeile mit Platz, die Eingabe und die Reiter Kern, Aufträge, Canvas und Hinweise", async () => {
    zeige(PLATZ);
    await ruhe();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("WAMO Testlabor, Platz 3");
    expect(screen.getByRole("textbox", { name: "Nachricht an WAMO" })).toBeTruthy();
    for (const name of ["Kern", "Aufträge", "Canvas", "Hinweise"]) expect(tab(name).length, name).toBeGreaterThan(0);
  });

  it("das Schaufenster heißt nur Testlabor und hat dieselbe schlanke Ansicht", async () => {
    zeige(SCHAU);
    await ruhe();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("WAMO Testlabor");
    expect(tab("Vorgänge")).toHaveLength(0);
    expect(verlaufKnoepfe()).toHaveLength(0);
  });
});

describe("Hauptansicht eines Laborkontos: Einstellungen nur mit Mikrofon", () => {
  it("die Gliederung hat nur Sprache und Ton", async () => {
    zeige(PLATZ, { hash: "#einstellungen" });
    await ruhe();
    const nav = screen.getByRole("navigation", { name: "Abschnitte der Einstellungen" });
    expect(within(nav).getAllByRole("link").map((l) => l.textContent)).toEqual([expect.stringMatching(/^Sprache und Ton/)]);
  });

  it("die Gliederung beschreibt den Abschnitt in der Laborsicht kurz mit Mikrofon wählen", async () => {
    zeige(PLATZ, { hash: "#einstellungen" });
    await ruhe();
    const nav = screen.getByRole("navigation", { name: "Abschnitte der Einstellungen" });
    expect(within(nav).getByText("Mikrofon wählen", { selector: ".ez-nav-kurz" })).toBeTruthy();
    expect(within(nav).queryByText(/Hören und Stimme/)).toBeNull();
  });

  it("der Abschnitt selbst trägt dieselbe Kurzzeile", async () => {
    zeige(PLATZ, { hash: "#einstellungen/sprache" });
    await ruhe();
    expect(document.querySelector(".ez-abschnitt-kurz")?.textContent).toBe("Mikrofon wählen");
  });

  it("zeigt in diesem Abschnitt die Mikrofonwahl, aber nicht Start beim Tippen oder Hören und Sprechen", async () => {
    zeige(PLATZ, { hash: "#einstellungen/sprache" });
    await ruhe();
    expect(screen.getByRole("combobox", { name: "Mikrofon" })).toBeTruthy();
    expect(screen.queryByText("Start beim Tippen")).toBeNull();
    expect(screen.queryByText("Hören und Sprechen")).toBeNull();
  });

  it("ein Link auf einen gesperrten Abschnitt zeigt ihn nicht, auch Konto mit Abmelden nicht", async () => {
    for (const abschnitt of ["modellzugang", "konto", "geraete", "info", "fernzugriff", "verbrauch"]) {
      const { unmount } = zeige(PLATZ, { hash: `#einstellungen/${abschnitt}` });
      await ruhe();
      const seite = document.querySelector<HTMLElement>(".ez");
      expect(seite, abschnitt).not.toBeNull();
      expect(seite!.querySelector("#ez-h-abschnitt"), abschnitt).toBeNull();
      expect(within(seite!).queryByRole("button", { name: "Abmelden" }), abschnitt).toBeNull();
      unmount();
    }
    expect(pfade()).not.toContain("/modellzugang");
  });
});

describe("Hauptansicht eines Laborkontos: Abmelden fragt nach", () => {
  it("fragt zuerst, trennt und meldet noch nicht ab", async () => {
    const { a, signOut } = zeige(PLATZ);
    await ruhe();
    fireEvent.click(abmeldenKnopf());
    expect(screen.getByRole("dialog", { name: "Wirklich abmelden?" })).toBeTruthy();
    expect(a.disconnect).not.toHaveBeenCalled();
    expect(signOut).not.toHaveBeenCalled();
  });

  it("Abbrechen lässt alles, wie es ist", async () => {
    const user = userEvent.setup();
    const { a, signOut } = zeige(PLATZ);
    await ruhe();
    await user.click(abmeldenKnopf());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(a.disconnect).not.toHaveBeenCalled();
    expect(signOut).not.toHaveBeenCalled();
  });

  it("Escape schließt nur die Rückfrage, nicht die Einstellungsseite dahinter", async () => {
    const user = userEvent.setup();
    const { signOut } = zeige(PLATZ, { hash: "#einstellungen" });
    await ruhe();
    expect(document.querySelector(".ez")).not.toBeNull();
    await user.click(abmeldenKnopf());
    expect(screen.getByRole("dialog", { name: "Wirklich abmelden?" })).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector(".ez")).not.toBeNull(); // die Einstellungen sind noch offen
    expect(window.location.hash).toBe("#einstellungen");
    expect(signOut).not.toHaveBeenCalled();
    // das nächste Escape gehört wieder der Seite und schließt sie
    await user.keyboard("{Escape}");
    await waitFor(() => expect(document.querySelector(".ez")).toBeNull());
  });

  it("Ja, abmelden beendet das Gespräch und meldet ab", async () => {
    const user = userEvent.setup();
    const { a, signOut } = zeige(PLATZ);
    await ruhe();
    await user.click(abmeldenKnopf());
    await user.click(screen.getByRole("button", { name: "Ja, abmelden" }));
    expect(a.disconnect).toHaveBeenCalledTimes(1);
    expect(signOut).toHaveBeenCalledTimes(1);
  });
});

describe("Hauptansicht eines normalen Admin-Kontos: unverändert alles", () => {
  it("zeigt Wortmarke, Reiter Vorgänge und Büro, Verlauf und die Zeile zum Modellzugang", async () => {
    zeige(ADMIN);
    await ruhe();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("WAMO");
    expect(tab("Vorgänge")).toHaveLength(2);
    expect(tab("Büro")).toHaveLength(2);
    expect(screen.getByRole("tablist", { name: "Aufträge, Vorgänge, Erledigt und Büro", hidden: true })).toBeTruthy();
    expect(document.getElementById("kz-p-vorgaenge")).not.toBeNull();
    expect(document.getElementById("kz-p-buero")).not.toBeNull();
    expect(document.getElementById("verlauf")).not.toBeNull();
    // Kopfzeile und Schnellaktionen haben je einen Knopf "Verlauf", dazu "Verlauf öffnen" im Menü
    expect(screen.getAllByRole("button", { name: "Verlauf" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Verlauf öffnen", hidden: true })).toBeTruthy();
    expect(screen.getByText("Modellzugang", { selector: "dt" })).toBeTruthy();
    expect(document.querySelector(".kz-gespraech")?.getAttribute("data-verlauf")).toBe("true");
  });

  it("ruft Verlauf, Modellzugang und Hinweise ab", async () => {
    zeige(ADMIN);
    await waitFor(() => {
      expect(pfade()).toContain("/vorgaenge");
      expect(pfade()).toContain("/modellzugang");
      expect(pfade()).toContain("/hinweise");
    });
  });

  it("hängt die Geräteeinrichtung ein: sie setzt ihren Stand beim Einhängen zurück", async () => {
    geraeteAnzeige.set(() => ({ zustand: "wartet", tunnel: null, kennung: null }));
    zeige(ADMIN);
    await ruhe();
    expect(geraeteAnzeige.get().zustand).toBe("nicht_noetig");
  });

  it("holt die Büro-Karte, sobald ihr Reiter gewählt ist", async () => {
    const user = userEvent.setup();
    zeige(ADMIN);
    await ruhe();
    expect(pfade()).not.toContain("/buero/status");
    await user.click(document.getElementById("kz-tab-m-buero")!);
    await waitFor(() => expect(pfade()).toContain("/buero/status"));
  });

  it("zeigt in der Tafel Vorgänge die Zeitleiste der Werkzeuge", async () => {
    const user = userEvent.setup();
    zeige(ADMIN);
    await ruhe();
    act(() => wamo.set((s) => ({ ...s, activities: [AKTIVITAET], approvals: [FREIGABE] })));
    await user.click(document.getElementById("kz-tab-m-vorgaenge")!);
    expect(screen.getByText("bereitet ein Ticket vor")).toBeTruthy();
  });

  it("zeigt den offenen Hinweis als Karte und in den Einstellungen als Zähler", async () => {
    zeige(ADMIN, { hash: "#einstellungen" });
    expect(await screen.findAllByText("Neue Datei geteilt")).not.toHaveLength(0);
    const meldungen = document.querySelector<HTMLElement>(".ez-meldungen")!;
    expect(await within(meldungen).findByText(/Ein offener Hinweis/)).toBeTruthy();
    act(() => geraeteAnzeige.set(() => ({ zustand: "wartet", tunnel: null, kennung: null })));
    expect(within(meldungen).getByText(/wartet auf die Freigabe durch einen Admin/)).toBeTruthy();
  });

  it("hat in den Einstellungen alle Abschnitte", async () => {
    zeige(ADMIN, { hash: "#einstellungen" });
    await ruhe();
    const nav = screen.getByRole("navigation", { name: "Abschnitte der Einstellungen" });
    const titel = within(nav).getAllByRole("link").map((l) => l.querySelector(".ez-nav-titel")?.textContent);
    expect(titel).toEqual([
      "Konto und Profil",
      "Modellzugang",
      "Verbrauch",
      "Sprache und Ton",
      "Stimme",
      "Gedächtnis",
      "Darstellung",
      "Geräte und PC",
      "Fernzugriff",
      "Änderungen",
      "Teilen",
      "Info",
    ]);
  });

  it("zeigt bei Sprache und Ton auch Start beim Tippen und Hören und Sprechen", async () => {
    zeige(ADMIN, { hash: "#einstellungen/sprache" });
    await ruhe();
    expect(document.querySelector(".ez-abschnitt-kurz")?.textContent).toBe("Mikrofon, Hören und Stimme");
    expect(screen.getByText("Start beim Tippen")).toBeTruthy();
    expect(screen.getByText("Hören und Sprechen")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Mikrofon" })).toBeTruthy();
  });

  it("meldet ohne Rückfrage ab, im Profil-Popover und in den Einstellungen", async () => {
    const user = userEvent.setup();
    const { a, signOut, unmount } = zeige(ADMIN);
    await ruhe();
    await user.click(abmeldenKnopf());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(a.disconnect).toHaveBeenCalledTimes(1);
    expect(signOut).toHaveBeenCalledTimes(1);
    unmount();

    const zweites = zeige(ADMIN, { hash: "#einstellungen/konto" });
    await ruhe();
    await user.click(within(document.querySelector<HTMLElement>(".ez")!).getByRole("button", { name: "Abmelden" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(zweites.signOut).toHaveBeenCalledTimes(1);
  });
});
