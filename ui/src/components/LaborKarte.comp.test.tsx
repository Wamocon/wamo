// Komponententest der Freigabekarte für Labor-Aktionen (components/Freigaben.tsx, lib/laborKarte.ts): jede Zeile des Auszuführenden steht
// auf der Karte, der Hinweis "Du entscheidest, nicht WAMO.", beide Knöpfe gleichgewichtig, die PC-Karte bleibt wie sie war.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ActivityTimeline } from "@/components/ActivityFeed";
import { Freigaben } from "@/components/Freigaben";
import { resetWamo, wamo, type Approval } from "@/lib/wamo";

const DETAIL = ["Typ: Bug", "Priorität: Hoch", "Titel: Anmeldung schlägt fehl", "Beschreibung:", "> Nach dem Klick", "> passiert nichts."].join("\n");
const LABOR: Approval = {
  job: "labor",
  projekt: "",
  id: "0123456789abcdef0123456789abcdef",
  grund: "Ticket anlegen",
  detail: DETAIL,
  von: "Gast",
  ziel: "Test-Jira, Platz 3",
};
const PC: Approval = { job: "pc", projekt: "WMC-PC", id: "0123456789abcdef", grund: "Nachricht tippen und senden", detail: "Bin gleich da⏎", ziel: "Chat | Teams" };

function zeige(...karten: Approval[]) {
  wamo.set((s) => ({ ...s, approvals: karten }));
  const onDecide = vi.fn(() => true);
  render(<Freigaben onDecide={onDecide} />);
  return onDecide;
}

describe("Freigabekarte für Labor-Aktionen", () => {
  beforeEach(() => resetWamo());

  it("zeigt Überschrift, Hinweis, Ziel und jede Zeile des Auszuführenden", () => {
    zeige(LABOR);
    expect(screen.getByText("Aktion freigeben", { selector: ".eyebrow" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Ticket anlegen" })).toBeTruthy();
    expect(screen.getByText("Du entscheidest, nicht WAMO.")).toBeTruthy();
    expect(screen.getByText("Ziel:")).toBeTruthy();
    expect(screen.getByText(/Test-Jira, Platz 3/, { selector: ".approval-ziel" })).toBeTruthy();
    expect(screen.getByText(/angefordert von Gast/)).toBeTruthy();
    const detail = screen.getByRole("region", { name: "Was genau ausgeführt wird" });
    expect(detail.textContent).toBe(DETAIL);
    expect(detail.textContent?.split("\n")).toHaveLength(6);
  });

  it("sagt die Aktion an, ohne vom PC zu sprechen", () => {
    zeige(LABOR);
    const alarm = screen.getByRole("alert");
    expect(alarm.textContent).toBe("Aktion freigeben: Ticket anlegen, Ziel: Test-Jira, Platz 3");
    expect(document.body.textContent).not.toMatch(/PC/);
  });

  it("Freigeben und Ablehnen sind gleichgewichtig und senden die Entscheidung", () => {
    const onDecide = zeige(LABOR);
    const ja = screen.getByRole("button", { name: "Freigeben" });
    const nein = screen.getByRole("button", { name: "Ablehnen" });
    expect(ja.className).toBe(nein.className);
    fireEvent.click(ja);
    fireEvent.click(nein);
    expect(onDecide).toHaveBeenNthCalledWith(1, expect.objectContaining({ job: "labor", id: LABOR.id }), true);
    expect(onDecide).toHaveBeenNthCalledWith(2, expect.objectContaining({ job: "labor", id: LABOR.id }), false);
  });

  it("Text im Markup-Stil bleibt Text", () => {
    const boese = { ...LABOR, detail: 'Titel: <img src=x onerror="alert(1)">\n> <script>alert(2)</script>' };
    zeige(boese);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByRole("region", { name: "Was genau ausgeführt wird" }).textContent).toContain("<script>alert(2)</script>");
  });

  it("die PC-Karte bleibt unverändert", () => {
    zeige(PC);
    expect(screen.getByText(/^PC-Aktion freigeben/, { selector: ".eyebrow" })).toBeTruthy();
    expect(screen.getByText("Ziel (Fenster und Programm):")).toBeTruthy();
    expect(screen.queryByText("Du entscheidest, nicht WAMO.")).toBeNull();
  });

  it("Karten beider Jobs mit gleicher Kennung bleiben getrennt", () => {
    const onDecide = zeige(LABOR, { ...PC, id: LABOR.id });
    expect(screen.getAllByRole("button", { name: "Freigeben" })).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Freigeben" })[1]);
    expect(onDecide).toHaveBeenCalledWith(expect.objectContaining({ job: "pc" }), true);
  });
});

describe("Zeitleiste der Labor-Werkzeuge", () => {
  beforeEach(() => resetWamo());

  const AKTIVITAET = { id: "c1", name: "ticket_anlegen", detail: "", status: "wartet" as const, turn: 0 };

  it("bis zum Klick steht dort, dass die Freigabe wartet, und der Werkzeugname sagt vorbereiten statt anlegen", () => {
    wamo.set((s) => ({ ...s, activities: [AKTIVITAET], approvals: [LABOR] }));
    render(<ActivityTimeline />);
    expect(screen.getByText("bereitet ein Ticket vor")).toBeTruthy();
    expect(screen.getByText("wartet auf Freigabe")).toBeTruthy();
    expect(document.querySelector("li")?.getAttribute("data-status")).toBe("wartet");
  });

  it("ist die Karte weg ohne Klick, gilt die Aktion als nicht ausgeführt", () => {
    wamo.set((s) => ({ ...s, activities: [AKTIVITAET], approvals: [] }));
    render(<ActivityTimeline />);
    expect(document.querySelector("li")?.getAttribute("data-status")).toBe("abgelehnt");
    expect(screen.getByText(/Freigabe nicht mehr offen/)).toBeTruthy();
  });
});
