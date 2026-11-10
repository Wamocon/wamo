// Komponententest der Tafel "Vorgänge" (kommando/Vorgaenge.tsx): Karten je Zustand, Fortschrittsbalken (role="progressbar"),
// "Zur Freigabe" mit Fokusführung, "Zuletzt beendet" erst nach dem Öffnen, Zustand als Wort. Die Freigabekarten sind die echten
// aus Freigaben.tsx, damit die Kennung data-approval wirklich aus dem Code stammt und nicht aus dem Test.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Freigaben } from "@/components/Freigaben";
import { canvasWunsch } from "@/lib/canvasWahl";
import type { Vorgang, VorgangSchritt } from "@/lib/nachrichten";
import { vorgangVerlauf } from "@/lib/vorgaengeApi";
import { resetWamo, wamo, type Approval } from "@/lib/wamo";
import { RahmenKontext } from "./rahmenKontext";
import { VorgaengeTafel } from "./Vorgaenge";

const vorgang = (id: string, extra: Partial<Vorgang> = {}): Vorgang => ({
  id,
  titel: `Vorgang ${id}`,
  art: "aufgabe",
  status: "laeuft",
  gestartetMs: 1000,
  beendetMs: null,
  gesamt: 0.5,
  restzeitS: null,
  schritte: [],
  kriterien: [],
  runde: null,
  maxRunden: null,
  freigabeId: null,
  canvasId: null,
  ...extra,
});

const schritt = (nr: number, titel: string, status: VorgangSchritt["status"], fortschritt: number, detail: string | null = null): VorgangSchritt => ({
  nr,
  titel,
  status,
  fortschritt,
  detail,
});

const freigabe = (id: string, grund: string): Approval => ({ job: "pc", projekt: "", id, grund, detail: `powershell -File ${id}.ps1` });

function sitzung(...liste: Vorgang[]) {
  wamo.set((s) => ({ ...s, vorgaenge: liste }));
}

const karte = (titel: string) => screen.getByRole("article", { name: titel });
const kopf = (titel: string) => karte(titel).querySelector("header")!;

const angebot = vorgang("lauf1", {
  titel: "Angebot erstellen",
  art: "entwicklung",
  status: "laeuft",
  gesamt: 0.5,
  restzeitS: 90,
  runde: 2,
  maxRunden: 3,
  schritte: [
    schritt(1, "Daten lesen", "fertig", 1),
    schritt(2, "Entwurf schreiben", "laeuft", 0.4, "Abschnitt 2 von 5"),
    schritt(3, "Prüfen", "offen", 0),
  ],
  kriterien: [
    { text: "Summe stimmt", erfuellt: true, hinweis: null },
    { text: "Frist genannt", erfuellt: null, hinweis: null },
    { text: "Preis nicht geraten", erfuellt: false, hinweis: "fehlt in Zeile 3" },
  ],
});

beforeEach(() => {
  resetWamo();
  vorgangVerlauf.set(() => []);
});

describe("Tafel Vorgänge: Karten", () => {
  it("zeigt nichts, solange es keinen Vorgang gibt (den Leerzustand zeigt die Seitenleiste)", () => {
    const { container } = render(<VorgaengeTafel />);
    expect(container.innerHTML).toBe("");
  });

  it("zeigt je laufendem Vorgang eine Karte, die nach ihrem Titel heißt, mit Art und Status als Wort", () => {
    sitzung(angebot);
    render(<VorgaengeTafel />);
    expect(screen.getByRole("heading", { level: 3, name: "Angebot erstellen" })).toBeTruthy();
    expect(kopf("Angebot erstellen").textContent).toContain("Entwicklung");
    expect(kopf("Angebot erstellen").textContent).toContain("läuft");
  });

  it("zeigt Schritte nummeriert in einer Liste, der laufende Schritt trägt aria-current", () => {
    sitzung(angebot);
    render(<VorgaengeTafel />);
    const schritte = within(karte("Angebot erstellen")).getByRole("list", { name: "Schritte" });
    const zeilen = within(schritte).getAllByRole("listitem");
    expect(zeilen).toHaveLength(3);
    expect(zeilen[1].textContent).toContain("Entwurf schreiben");
    expect(zeilen[1].textContent).toContain("Abschnitt 2 von 5");
    expect(zeilen.map((z) => z.getAttribute("aria-current"))).toEqual([null, "step", null]);
  });

  it("nennt Prüfkriterien mit Wort: erfüllt, nicht erfüllt, noch offen", () => {
    sitzung(angebot);
    render(<VorgaengeTafel />);
    const liste = within(karte("Angebot erstellen")).getByRole("list", { name: "Prüfkriterien" });
    const zeilen = within(liste).getAllByRole("listitem").map((li) => li.textContent);
    expect(zeilen[0]).toContain("Summe stimmt");
    expect(zeilen[0]).toContain("erfüllt");
    expect(zeilen[1]).toContain("noch offen");
    expect(zeilen[2]).toContain("nicht erfüllt");
    expect(zeilen[2]).toContain("fehlt in Zeile 3");
  });

  it("zeigt einen wartenden Vorgang mit dem Wort wartet auf Freigabe und dem Knopf Zur Freigabe", () => {
    sitzung(vorgang("w1", { titel: "Skript starten", status: "wartet_freigabe", gesamt: 0.25, freigabeId: "abcd0001" }), angebot);
    render(<VorgaengeTafel />);
    expect(kopf("Skript starten").textContent).toContain("wartet auf Freigabe");
    const knopf = within(karte("Skript starten")).getByRole("button", { name: "Zur Freigabe: Skript starten" });
    expect(knopf.textContent).toBe("Zur Freigabe");
    // der laufende Vorgang wartet nicht: kein Knopf
    expect(within(karte("Angebot erstellen")).queryByRole("button", { name: /Zur Freigabe/ })).toBeNull();
  });

  it("legt fertige und fehlgeschlagene Vorgänge unter Zuletzt beendet, der zuletzt beendete zuerst, mit Status als Wort", () => {
    sitzung(
      vorgang("f1", { titel: "Rechnung prüfen", status: "fertig", gestartetMs: 1000, beendetMs: 43_000, gesamt: 1 }),
      vorgang("e1", { titel: "Backup kopieren", status: "fehler", gestartetMs: 5000, beendetMs: 90_000, gesamt: 0.3 }),
      angebot,
    );
    render(<VorgaengeTafel />);
    expect(screen.getByText("Zuletzt beendet")).toBeTruthy();
    expect(screen.getByText("(2)")).toBeTruthy();
    // beendete Vorgänge sind keine aktiven Karten
    expect(screen.queryByRole("article", { name: "Rechnung prüfen" })).toBeNull();
    const titel = screen.getAllByText(/^(Rechnung prüfen|Backup kopieren)$/).map((el) => el.textContent);
    expect(titel).toEqual(["Backup kopieren", "Rechnung prüfen"]);
    expect(screen.getByText("Aufgabe, Fertig in 00:42")).toBeTruthy();
    expect(screen.getByText("Aufgabe, fehlgeschlagen")).toBeTruthy();
  });

  it("sagt, dass gerade nichts läuft, wenn es nur beendete Vorgänge gibt", () => {
    sitzung(vorgang("f1", { status: "fertig", beendetMs: 5000, gesamt: 1 }));
    render(<VorgaengeTafel />);
    expect(screen.getByText("Gerade läuft kein Vorgang.")).toBeTruthy();
    expect(screen.queryAllByRole("article")).toHaveLength(0);
  });

  it("nimmt Vorgänge aus dem Verlauf der Datenbank unter Zuletzt beendet auf, als abgebrochen", () => {
    vorgangVerlauf.set(() => [vorgang("alt1", { titel: "Gestern begonnen", status: "abgebrochen", beendetMs: null, gestartetMs: 500 })]);
    render(<VorgaengeTafel />);
    expect(screen.getByText("Gestern begonnen")).toBeTruthy();
    expect(screen.getByText("Aufgabe, abgebrochen")).toBeTruthy();
  });
});

describe("Tafel Vorgänge: Zustand steht als Wort da, nicht nur als Farbe", () => {
  it.each([
    ["geplant", "geplant"],
    ["laeuft", "läuft"],
    ["wartet_freigabe", "wartet auf Freigabe"],
    ["pruefen", "wird geprüft"],
  ] as const)("zeigt den Status %s als Wort: %s", (status, wort) => {
    sitzung(vorgang("s1", { titel: "Prüfling", status }));
    render(<VorgaengeTafel />);
    const status_ = kopf("Prüfling").querySelector("[data-status]")!;
    expect(status_.textContent).toBe(wort);
    // die Marke daneben (Form und Farbe) ist Beigabe und für Bildschirmleser unsichtbar
    expect(status_.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
  });

  it("zeigt auch den Zustand jedes Schritts im Namen des Balkens, nicht nur an der Farbe", () => {
    sitzung(angebot);
    render(<VorgaengeTafel />);
    expect(screen.getByRole("progressbar", { name: "Daten lesen" }).getAttribute("aria-valuetext")).toBe("100 Prozent, erledigt");
    expect(screen.getByRole("progressbar", { name: "Entwurf schreiben" }).getAttribute("aria-valuetext")).toBe("40 Prozent, läuft");
    expect(screen.getByRole("progressbar", { name: "Prüfen" }).getAttribute("aria-valuetext")).toBe("0 Prozent, offen");
  });
});

describe("Tafel Vorgänge: Fortschrittsbalken", () => {
  it("gibt dem Gesamtbalken Rolle, Wertebereich, aktuellen Wert und einen sprechenden Text mit Runde und Restzeit", () => {
    sitzung(angebot);
    render(<VorgaengeTafel />);
    const balken = screen.getByRole("progressbar", { name: "Angebot erstellen" });
    expect(balken.getAttribute("aria-valuemin")).toBe("0");
    expect(balken.getAttribute("aria-valuemax")).toBe("100");
    expect(balken.getAttribute("aria-valuenow")).toBe("50");
    expect(balken.getAttribute("aria-valuetext")).toBe("50 Prozent, Runde 2 von 3, noch etwa 01:30");
  });

  it("nennt bei einem Vorgang ohne Schätzung keine erfundene Restzeit", () => {
    sitzung(vorgang("s1", { titel: "Ohne Schätzung", gesamt: 0.2, restzeitS: null }));
    render(<VorgaengeTafel />);
    expect(screen.getByRole("progressbar", { name: "Ohne Schätzung" }).getAttribute("aria-valuetext")).toBe("20 Prozent, Restzeit wird geschätzt");
  });

  it("nennt beim Warten den Status statt einer Restzeit", () => {
    sitzung(vorgang("w1", { titel: "Wartet", status: "wartet_freigabe", gesamt: 0.25, freigabeId: "abcd0001" }));
    render(<VorgaengeTafel />);
    const balken = screen.getByRole("progressbar", { name: "Wartet" });
    expect(balken.getAttribute("aria-valuenow")).toBe("25");
    expect(balken.getAttribute("aria-valuetext")).toBe("25 Prozent, wartet auf Freigabe");
  });

  it("hält den Wert zwischen 0 und 100, auch bei Unsinn aus dem Netz", () => {
    sitzung(vorgang("a", { titel: "Zu viel", gesamt: 1.7 }), vorgang("b", { titel: "Zu wenig", gesamt: -0.4 }), vorgang("c", { titel: "Keine Zahl", gesamt: Number.NaN }));
    render(<VorgaengeTafel />);
    expect(screen.getByRole("progressbar", { name: "Zu viel" }).getAttribute("aria-valuenow")).toBe("100");
    expect(screen.getByRole("progressbar", { name: "Zu wenig" }).getAttribute("aria-valuenow")).toBe("0");
    expect(screen.getByRole("progressbar", { name: "Keine Zahl" }).getAttribute("aria-valuenow")).toBe("0");
  });

  it("gibt jedem Schrittbalken Wert und Namen seines Schritts, auch wenn zwei Karten gleich benannte Schritte haben", () => {
    const gleich = (id: string, titel: string) => vorgang(id, { titel, schritte: [schritt(1, "Prüfen", "laeuft", 0.6)] });
    sitzung(gleich("a", "Erste Karte"), gleich("b", "Zweite Karte"));
    render(<VorgaengeTafel />);
    const balken = screen.getAllByRole("progressbar", { name: "Prüfen" });
    expect(balken).toHaveLength(2);
    for (const b of balken) {
      expect(b.getAttribute("aria-valuenow")).toBe("60");
      expect(b.getAttribute("aria-valuetext")).toBe("60 Prozent, läuft");
    }
    // zwei verschiedene Beschriftungselemente: jeder Balken heißt nach dem Schritt seiner eigenen Karte
    expect(new Set(balken.map((b) => b.getAttribute("aria-labelledby"))).size).toBe(2);
    for (const k of ["Erste Karte", "Zweite Karte"]) expect(within(karte(k)).getAllByRole("progressbar")).toHaveLength(2);
  });

  it("zeichnet den Fortschritt auch als Füllung, ohne dass der Wert allein daran hängt", () => {
    sitzung(vorgang("s1", { titel: "Halb", gesamt: 0.5 }));
    render(<VorgaengeTafel />);
    const fuell = screen.getByRole("progressbar", { name: "Halb" }).firstElementChild as HTMLElement;
    expect(fuell.style.transform).toBe("translateX(-50%)");
  });
});

describe("Tafel Vorgänge: Zuletzt beendet erst nach dem Öffnen", () => {
  const fertig = vorgang("f1", {
    titel: "Rechnung prüfen",
    status: "fertig",
    gestartetMs: 1000,
    beendetMs: 43_000,
    gesamt: 1,
    schritte: [schritt(1, "Beleg lesen", "fertig", 1), schritt(2, "Summe prüfen", "fertig", 1)],
    kriterien: [{ text: "Summe stimmt", erfuellt: true, hinweis: null }],
  });

  it("rendert den Inhalt geschlossener Zeilen nicht: weder Balken noch Schritte noch Kriterien", () => {
    sitzung(fertig);
    render(<VorgaengeTafel />);
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
    expect(screen.queryByRole("list", { name: "Schritte" })).toBeNull();
    expect(screen.queryByRole("list", { name: "Prüfkriterien" })).toBeNull();
    expect(screen.queryByText("Beleg lesen")).toBeNull();
  });

  it("zeigt den Inhalt nach dem Öffnen der Zeile und nimmt ihn beim Schließen wieder heraus", async () => {
    const user = userEvent.setup();
    sitzung(fertig);
    render(<VorgaengeTafel />);
    const zeile = screen.getByText("Rechnung prüfen").closest("details")!;
    expect(zeile.open).toBe(false);
    await user.click(screen.getByText("Rechnung prüfen"));
    await waitFor(() => expect(within(zeile).getAllByRole("progressbar").length).toBe(3));
    expect(screen.getByRole("progressbar", { name: "Rechnung prüfen" }).getAttribute("aria-valuetext")).toBe("100 Prozent, Fertig in 00:42");
    expect(within(zeile).getByRole("list", { name: "Schritte" })).toBeTruthy();
    expect(within(zeile).getByText("Summe stimmt")).toBeTruthy();
    await user.click(screen.getByText("Rechnung prüfen"));
    await waitFor(() => expect(within(zeile).queryAllByRole("progressbar")).toHaveLength(0));
  });

  it("öffnet nur die gewählte Zeile, die anderen bleiben leer", async () => {
    const user = userEvent.setup();
    sitzung(fertig, vorgang("e1", { titel: "Backup kopieren", status: "fehler", beendetMs: 90_000, schritte: [schritt(1, "Dateien sammeln", "fehler", 0.3)] }));
    render(<VorgaengeTafel />);
    await user.click(screen.getByText("Backup kopieren"));
    await waitFor(() => expect(screen.getByRole("progressbar", { name: "Dateien sammeln" })).toBeTruthy());
    expect(screen.queryByText("Beleg lesen")).toBeNull();
  });

  it("lässt den Abschnitt Zuletzt beendet selbst aufgeklappt, nur seine Zeilen sind zu", () => {
    sitzung(fertig);
    render(<VorgaengeTafel />);
    expect(screen.getByText("Zuletzt beendet").closest("details")!.open).toBe(true);
    expect(screen.getByText("Rechnung prüfen").closest("details")!.open).toBe(false);
  });

  it("bietet Ergebnis ansehen nur an, solange das Element im Canvas liegt, und wählt es dort", async () => {
    const user = userEvent.setup();
    const zeigeCanvas = vi.fn();
    wamo.set((s) => ({ ...s, canvas: [{ id: "c1", kind: "dokument", title: "Angebot", text: "Text" }] }));
    sitzung(
      vorgang("f1", { titel: "Mit Ergebnis", status: "fertig", beendetMs: 9000, gesamt: 1, canvasId: "c1" }),
      vorgang("f2", { titel: "Ergebnis weg", status: "fertig", beendetMs: 8000, gesamt: 1, canvasId: "c-weg" }),
    );
    render(
      <RahmenKontext.Provider value={{ zeigeCanvas, zeigeAuftraege: () => undefined }}>
        <VorgaengeTafel />
      </RahmenKontext.Provider>,
    );
    await user.click(screen.getByText("Ergebnis weg"));
    await waitFor(() => expect(screen.getByRole("progressbar", { name: "Ergebnis weg" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Ergebnis ansehen/ })).toBeNull();
    await user.click(screen.getByText("Mit Ergebnis"));
    const knopf = await screen.findByRole("button", { name: "Ergebnis ansehen: Mit Ergebnis" });
    const vorher = canvasWunsch.get().n;
    await user.click(knopf);
    expect(canvasWunsch.get()).toEqual({ id: "c1", n: vorher + 1 });
    expect(zeigeCanvas).toHaveBeenCalledTimes(1);
  });
});

describe("Tafel Vorgänge: Zur Freigabe führt den Fokus", () => {
  const ersteKarte = freigabe("abcd0001", "Skript starten");
  const zweiteKarte = freigabe("ffff0002", "Mail senden");
  let zeigeAuftraege: ReturnType<typeof vi.fn>;

  /** Rahmen wie in Rahmen.tsx: die Tafel Aufträge (mit Überschrift als Ziel) neben der Tafel Vorgänge. */
  function Seite() {
    return (
      <RahmenKontext.Provider value={{ zeigeCanvas: () => undefined, zeigeAuftraege: zeigeAuftraege as () => void }}>
        <div id="kz-p-auftraege">
          <h2 id="kz-h-auftraege" tabIndex={-1}>
            Aufträge und Freigaben
          </h2>
          <Freigaben onDecide={() => true} />
        </div>
        <VorgaengeTafel />
      </RahmenKontext.Provider>
    );
  }

  const wartend = (id: string, titel: string, freigabeId: string | null) => vorgang(id, { titel, status: "wartet_freigabe", gesamt: 0.25, freigabeId });
  const knopf = (titel: string) => screen.getByRole("button", { name: `Zur Freigabe: ${titel}` });
  const ueberschrift = (grund: string) => screen.getByRole("heading", { level: 3, name: grund });

  beforeEach(() => {
    zeigeAuftraege = vi.fn();
    wamo.set((s) => ({ ...s, approvals: [ersteKarte, zweiteKarte] }));
  });

  it("fokussiert die Überschrift der EIGENEN Freigabekarte, nicht die erste und nicht die eines anderen Vorgangs", async () => {
    const user = userEvent.setup();
    sitzung(wartend("w1", "Skript-Vorgang", "abcd0001"), wartend("w2", "Mail-Vorgang", "ffff0002"));
    render(<Seite />);
    // beim Einhängen holt sich die erste Karte den Fokus; der Vorgang der zweiten Karte muss ihn ihr wegnehmen
    await user.click(knopf("Mail-Vorgang"));
    await waitFor(() => expect(document.activeElement).toBe(ueberschrift("Mail senden")));
    expect(document.activeElement!.closest("[data-approval]")!.getAttribute("data-approval")).toBe("ap-pc-ffff0002");
    await user.click(knopf("Skript-Vorgang"));
    await waitFor(() => expect(document.activeElement).toBe(ueberschrift("Skript starten")));
  });

  it("wechselt zuerst zur Tafel Aufträge, dann erst zur Karte (am Handy ist sie ein eigener Reiter)", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    sitzung(wartend("w2", "Mail-Vorgang", "ffff0002"));
    render(<Seite />);
    await user.click(knopf("Mail-Vorgang"));
    expect(zeigeAuftraege).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(scroll).toHaveBeenCalledTimes(1));
    expect(scroll.mock.contexts[0]).toBe(document.querySelector('[data-approval="ap-pc-ffff0002"]'));
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
  });

  it("scrollt bei reduzierter Bewegung ohne weiches Scrollen", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    sitzung(wartend("w2", "Mail-Vorgang", "ffff0002"));
    render(<Seite />);
    await user.click(knopf("Mail-Vorgang"));
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ behavior: "auto", block: "center" }));
  });

  it.each([
    ["die Frist der Karte abgelaufen ist (Karte fehlt)", "ffff9999"],
    ["der Vorgang keine Kennung mehr trägt", null],
    ["die Kennung nur das Ende einer fremden Kennung ist (abcd0001 endet auf 0001)", "0001"],
  ])("fokussiert die Überschrift der Tafel und keine fremde Karte, wenn %s", async (_fall, freigabeId) => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    sitzung(wartend("w9", "Verfallener Vorgang", freigabeId));
    render(<Seite />);
    expect(document.activeElement).toBe(ueberschrift("Skript starten")); // Ausgangslage: eine fremde Karte hat den Fokus
    await user.click(knopf("Verfallener Vorgang"));
    const tafel = screen.getByRole("heading", { level: 2, name: "Aufträge und Freigaben" });
    await waitFor(() => expect(document.activeElement).toBe(tafel));
    expect(scroll.mock.contexts[0]).toBe(document.getElementById("kz-p-auftraege"));
    expect(wamo.get().toast?.text).toBe("Freigabekarte nicht gefunden");
    expect(wamo.get().ansage).toBe("Freigabekarte nicht gefunden"); // und Bildschirmleser hören es
  });

  it("fokussiert auch ohne jede Freigabekarte die Überschrift der Tafel", async () => {
    const user = userEvent.setup();
    wamo.set((s) => ({ ...s, approvals: [] }));
    sitzung(wartend("w1", "Skript-Vorgang", "abcd0001"));
    render(<Seite />);
    await user.click(knopf("Skript-Vorgang"));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { level: 2, name: "Aufträge und Freigaben" })));
    expect(wamo.get().toast?.text).toBe("Freigabekarte nicht gefunden");
  });
});
