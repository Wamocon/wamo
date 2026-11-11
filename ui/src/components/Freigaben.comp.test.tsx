// Komponententest der Startkarte für Entwicklungsaufträge (components/Freigaben.tsx, lib/auftragStart.ts): Verwerfen ist gesperrt, sobald
// der Auftrag unterwegs ist (der Kern ignoriert "nein" bei einem gestarteten Auftrag), der Grund erscheint genau einmal.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Auftraege } from "@/components/Freigaben";
import { auftraegeNachFrist, auftraegeNachQuittung, auftraegeNachStart } from "@/lib/auftragStart";
import type { Auftrag } from "@/lib/nachrichten";
import { resetWamo, wamo } from "@/lib/wamo";

const A: Auftrag = { job: "j1", projekt: "kern-testrepo", art: "Analyse", auftrag: "Prüfe das Repo", abnahme: [], repo: "acme/testrepo" };

function zeige(liste: Auftrag[]) {
  wamo.set((s) => ({ ...s, auftraege: liste }));
}

const knopf = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

function aufbauen() {
  const onDecide = vi.fn(() => true);
  render(<Auftraege onDecide={onDecide} />);
  return onDecide;
}

describe("Startkarte eines Entwicklungsauftrags", () => {
  beforeEach(() => resetWamo());

  it("vor dem Klick: Starten und Verwerfen sind aktiv und senden die Entscheidung", () => {
    zeige([A]);
    const onDecide = aufbauen();
    expect(knopf("Verwerfen").disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Starten" }));
    fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
    expect(onDecide).toHaveBeenNthCalledWith(1, A, true);
    expect(onDecide).toHaveBeenNthCalledWith(2, A, false);
  });

  it("das Repo steht auf der Karte, ohne Repo bricht nichts", () => {
    zeige([A, { ...A, job: "j2", repo: undefined, art: "Recherche" }]);
    aufbauen();
    expect(screen.getByText("acme/testrepo")).toBeTruthy();
    expect(screen.getAllByText("Repo")).toHaveLength(1);
  });

  it("nach dem Klick auf Starten (Quittung steht aus) ist Verwerfen gesperrt und die Karte sagt warum", () => {
    zeige(auftraegeNachStart([A], "j1", 0));
    const onDecide = aufbauen();
    const verwerfen = knopf("Verwerfen");
    expect(verwerfen.disabled).toBe(true);
    expect(knopf("Wird gestartet …").disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toMatch(/nicht mehr verwerfen/);
    fireEvent.click(verwerfen);
    expect(onDecide).not.toHaveBeenCalled();
  });

  it("auch nach 15 Sekunden ohne Quittung ist Verwerfen gesperrt, Starten geht noch einmal", () => {
    zeige(auftraegeNachFrist(auftraegeNachStart([A], "j1", 0), 20_000));
    const onDecide = aufbauen();
    expect(knopf("Verwerfen").disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toMatch(/keine Rückmeldung/i);
    expect(screen.getByRole("status").textContent).toMatch(/nicht mehr verwerfen/);
    fireEvent.click(screen.getByRole("button", { name: "Noch einmal senden" }));
    expect(onDecide).toHaveBeenCalledWith(expect.objectContaining({ job: "j1" }), true);
  });

  it("vom Kern abgelehnt: die Karte bleibt, der Grund steht genau einmal da, Verwerfen und erneutes Starten gehen", () => {
    const grund = "Der Kanal ist geschlossen, es startet kein Auftrag.";
    zeige(auftraegeNachQuittung(auftraegeNachStart([A], "j1", 0), { job: "j1", ok: false, grund }).auftraege);
    const onDecide = aufbauen();
    expect(screen.getAllByText(new RegExp(grund.replace(/[.]/g, "\\.")))).toHaveLength(1);
    // Die Karte sagt den Grund selbst an (role alert); die Live-Region der Ansagen sagt ihn nicht noch einmal (lib/auftragStart.ts)
    const alarme = screen.getAllByRole("alert").filter((el) => el.textContent?.includes(grund));
    expect(alarme).toHaveLength(1);
    expect(knopf("Verwerfen").disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
    expect(onDecide).toHaveBeenCalledWith(expect.objectContaining({ job: "j1" }), false);
    expect(knopf("Erneut starten").disabled).toBe(false);
  });

  it("meldet der Kern Entwicklung aus, bleibt Starten gesperrt", () => {
    zeige([A]);
    wamo.set((s) => ({ ...s, betrieb: { modus: "buero", modelle: {}, pc: "aus", entwicklung: "aus" } }));
    aufbauen();
    expect(knopf("Starten").disabled).toBe(true);
    expect(knopf("Verwerfen").disabled).toBe(false);
  });
});
