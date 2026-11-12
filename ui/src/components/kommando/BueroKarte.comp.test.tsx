// Komponententest der Büro-Karte (kommando/BueroKarte.tsx): Punkte als Buttons, Tastatur, Escape und Fokus, Zustand nie nur
// über Farbe, Leerzustand und Hinweis auf unbekannte Geräte. Der Abruf (useBueroStand) ist durch eine Attrappe ersetzt,
// sein eigenes Verhalten prüft useBueroStand.comp.test.tsx.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bueroSteuern } from "@/lib/bueroApi";
import { KernFehler } from "@/lib/kernApi";
import { resetWamo, wamo } from "@/lib/wamo";
import { BueroKarte } from "./BueroKarte";
import { beschriftung, UNBEKANNT_TIPP, type BueroGeraet, type BueroStand } from "./bueroKarteModell";
import { bueroNachSchalten, useBueroStand, type BueroZustand } from "./useBueroStand";

vi.mock("./useBueroStand", () => ({ useBueroStand: vi.fn(), bueroNachSchalten: vi.fn() }));
vi.mock("@/lib/bueroApi", () => ({ bueroSteuern: vi.fn() }));

const server: BueroGeraet = { id: "srv-1", name: "Sokrates-2", gruppe: "server", ampel: "ok", text: "läuft, Last niedrig", tipp: "Sag: Wie geht es dem Server?" };
const drucker: BueroGeraet = { id: "drucker-1", name: "Drucker Empfang", gruppe: "drucker", ampel: "stoerung", text: "Papierstau", tipp: "Sag: Was ist am Drucker?" };
const fernseher: BueroGeraet = { id: "tv-1", name: "Fernseher", gruppe: "medien", ampel: "unbekannt", text: "nicht gekoppelt", tipp: "" };

const stand = (geraete: BueroGeraet[] = [server, drucker, fernseher], unbekannt: number | null = null): BueroStand => ({
  geraete,
  unbekannt,
  stand: "2026-11-10T08:00:00Z",
});

function zustand(z: Partial<BueroZustand> & { stand: BueroStand | null }) {
  vi.mocked(useBueroStand).mockReturnValue({ fehler: null, geladen: true, ...z });
}

const punkt = (g: BueroGeraet) => screen.getByRole("button", { name: beschriftung(g) });
const detail = (g: BueroGeraet) => screen.queryByRole("region", { name: g.name });
const formVon = (el: Element) => el.querySelector("svg")!;

describe("BueroKarte", () => {
  beforeEach(() => {
    resetWamo();
    vi.mocked(useBueroStand).mockReset();
    vi.mocked(bueroSteuern).mockReset().mockResolvedValue(undefined);
    vi.mocked(bueroNachSchalten).mockReset();
  });

  describe("Punkte", () => {
    it("zeigt jedes Gerät als Button, benannt nach Name, Zustandswort und Klartext", () => {
      zustand({ stand: stand() });
      render(<BueroKarte />);
      const gruppe = screen.getByRole("group", { name: /Büro-Karte/ });
      const knoepfe = within(gruppe).getAllByRole("button");
      expect(knoepfe.map((k) => k.getAttribute("aria-label"))).toEqual([
        "Sokrates-2, in Ordnung: läuft, Last niedrig",
        "Drucker Empfang, Störung: Papierstau",
        "Fernseher, unbekannt: nicht gekoppelt",
      ]);
      for (const k of knoepfe) expect(k.getAttribute("aria-expanded")).toBe("false");
    });

    it("macht die Punkte mit Tab erreichbar, einen nach dem anderen", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      render(<BueroKarte />);
      await user.tab();
      expect(document.activeElement).toBe(punkt(server));
      await user.tab();
      expect(document.activeElement).toBe(punkt(drucker));
      await user.tab();
      expect(document.activeElement).toBe(punkt(fernseher));
    });
  });

  describe("Tastatur", () => {
    it("öffnet mit Enter die Detailkarte mit Zustand in Klartext und Sprechtipp", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      render(<BueroKarte />);
      expect(detail(drucker)).toBeNull();
      punkt(drucker).focus();
      await user.keyboard("{Enter}");
      const karte = detail(drucker)!;
      expect(karte).not.toBeNull();
      expect(within(karte).getByText("Störung")).toBeTruthy();
      expect(within(karte).getByText("Papierstau")).toBeTruthy();
      expect(within(karte).getByText("Sag: Was ist am Drucker?")).toBeTruthy();
      expect(punkt(drucker).getAttribute("aria-expanded")).toBe("true");
      expect(punkt(drucker).getAttribute("aria-controls")).toBe(karte.id);
      expect(karte.parentElement!.getAttribute("aria-live")).toBe("polite"); // wird angesagt, ohne den Fokus zu nehmen
    });

    it("öffnet mit der Leertaste die Detailkarte", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      render(<BueroKarte />);
      punkt(fernseher).focus();
      await user.keyboard(" ");
      expect(detail(fernseher)).not.toBeNull();
      expect(punkt(fernseher).getAttribute("aria-expanded")).toBe("true");
    });

    it("schließt die Detailkarte, wenn derselbe Punkt noch einmal betätigt wird, und wechselt bei einem anderen Punkt", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      render(<BueroKarte />);
      punkt(drucker).focus();
      await user.keyboard("{Enter}");
      await user.keyboard("{Enter}");
      expect(detail(drucker)).toBeNull();
      await user.keyboard("{Enter}");
      punkt(server).focus();
      await user.keyboard("{Enter}");
      expect(detail(drucker)).toBeNull();
      expect(detail(server)).not.toBeNull();
    });

    it("schließt mit Escape aus der Detailkarte heraus und gibt den Fokus an den Punkt zurück", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      render(<BueroKarte />);
      punkt(drucker).focus();
      await user.keyboard("{Enter}");
      screen.getByRole("button", { name: "Detailkarte zu Drucker Empfang schließen" }).focus();
      await user.keyboard("{Escape}");
      expect(detail(drucker)).toBeNull();
      expect(document.activeElement).toBe(punkt(drucker));
      expect(punkt(drucker).getAttribute("aria-expanded")).toBe("false");
    });

    it("schließt mit Escape auch, wenn der Fokus noch auf dem Punkt liegt", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      render(<BueroKarte />);
      punkt(server).focus();
      await user.keyboard("{Enter}");
      await user.keyboard("{Escape}");
      expect(detail(server)).toBeNull();
      expect(document.activeElement).toBe(punkt(server));
    });

    it("gibt den Fokus auch beim Schließen mit dem Knopf an den Punkt zurück", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      render(<BueroKarte />);
      await user.click(punkt(fernseher));
      await user.click(screen.getByRole("button", { name: "Detailkarte zu Fernseher schließen" }));
      expect(detail(fernseher)).toBeNull();
      expect(document.activeElement).toBe(punkt(fernseher));
    });

    it("hält Escape bei offener Detailkarte von äußeren Bereichen fern, bei geschlossener lässt es ihn durch", async () => {
      const user = userEvent.setup();
      const aussen = vi.fn();
      zustand({ stand: stand() });
      render(
        <div onKeyDown={(e) => e.key === "Escape" && aussen()}>
          <BueroKarte />
        </div>,
      );
      punkt(drucker).focus();
      await user.keyboard("{Enter}");
      await user.keyboard("{Escape}"); // schließt nur die Detailkarte
      expect(aussen).not.toHaveBeenCalled();
      await user.keyboard("{Escape}"); // nichts offen: gehört dem Bereich darüber
      expect(aussen).toHaveBeenCalledTimes(1);
    });

    it("schließt die Detailkarte, wenn das Gerät aus den Daten verschwindet", async () => {
      const user = userEvent.setup();
      zustand({ stand: stand() });
      const { rerender } = render(<BueroKarte />);
      await user.click(punkt(drucker));
      expect(detail(drucker)).not.toBeNull();
      zustand({ stand: stand([server, fernseher]) });
      rerender(<BueroKarte />);
      expect(detail(drucker)).toBeNull();
    });
  });

  describe("Zustand nie nur über Farbe", () => {
    it("nennt den Zustand als Wort: im Namen des Punkts, in der Liste, in der Legende und in der Ansage", () => {
      zustand({ stand: stand() });
      render(<BueroKarte />);
      expect(punkt(drucker).getAttribute("aria-label")).toContain("Störung");
      const liste = screen.getByRole("heading", { name: "Alle Geräte als Liste" }).parentElement!;
      expect(within(liste).getByText("Störung: Papierstau")).toBeTruthy();
      expect(within(liste).getByText("in Ordnung: läuft, Last niedrig")).toBeTruthy();
      const legende = screen.getByRole("list", { name: "Bedeutung der Zeichen" });
      expect(within(legende).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["in Ordnung", "Störung", "unbekannt"]);
      expect(screen.getByRole("status").textContent).toBe("3 Geräte, 1 Störung, 1 unbekannt");
    });

    it("gibt jedem Zustand eine eigene Form: Kreis voll, Kreis mit Strich, Kreis hohl", () => {
      zustand({ stand: stand() });
      render(<BueroKarte />);
      const ok = formVon(punkt(server));
      const stoerung = formVon(punkt(drucker));
      const unbekannt = formVon(punkt(fernseher));
      expect(ok.querySelector(".bk-kreis-voll")).not.toBeNull();
      expect(ok.querySelector(".bk-strich")).toBeNull();
      expect(stoerung.querySelector(".bk-kreis-strich")).not.toBeNull();
      expect(stoerung.querySelector(".bk-strich")).not.toBeNull(); // der Strich im Kreis
      expect(unbekannt.querySelector(".bk-kreis-hohl")).not.toBeNull();
      expect(unbekannt.querySelector(".bk-strich")).toBeNull();
      // Die Zeichen sind Beigabe: Bildschirmleser lesen sie nicht vor
      for (const svg of [ok, stoerung, unbekannt]) expect(svg.getAttribute("aria-hidden")).toBe("true");
    });

    it("zeigt in der Legende dieselben drei Formen", () => {
      zustand({ stand: stand() });
      render(<BueroKarte />);
      const formen = within(screen.getByRole("list", { name: "Bedeutung der Zeichen" }))
        .getAllByRole("listitem")
        .map((li) => li.querySelector("circle.bk-kreis")!.getAttribute("class"));
      expect(formen).toEqual(["bk-kreis bk-kreis-voll", "bk-kreis bk-kreis-strich", "bk-kreis bk-kreis-hohl"]);
    });
  });

  describe("Leerzustand und Fehler", () => {
    it.each([[503], [0], [null]])("zeigt bei Status %s ohne Daten: Gerätedienst nicht erreichbar, und keine Punkte", (fehler) => {
      zustand({ stand: null, fehler });
      render(<BueroKarte />);
      expect(screen.getByText("Gerätedienst nicht erreichbar")).toBeTruthy();
      expect(screen.queryAllByRole("button")).toHaveLength(0);
      expect(screen.getByRole("status").textContent).toContain("Gerätedienst nicht erreichbar");
    });

    it("sagt bei fehlender Berechtigung etwas anderes als bei fehlender Verbindung", () => {
      zustand({ stand: null, fehler: 403 });
      render(<BueroKarte />);
      expect(screen.queryByText("Gerätedienst nicht erreichbar")).toBeNull();
      expect(screen.getByText("Die Büro-Karte ist gerade nicht verfügbar")).toBeTruthy();
    });

    it("zeigt vor dem ersten Abruf die Ladeanzeige, nicht den Leerzustand", () => {
      zustand({ stand: null, geladen: false });
      render(<BueroKarte />);
      expect(screen.getByText("Geräte werden geladen …")).toBeTruthy();
      expect(screen.queryByText("Gerätedienst nicht erreichbar")).toBeNull();
    });

    it("zeigt bei einem Stand ohne Geräte, dass noch keine eingerichtet sind", () => {
      zustand({ stand: stand([]) });
      render(<BueroKarte />);
      expect(screen.getByText("Noch keine Geräte eingerichtet")).toBeTruthy();
      expect(screen.queryAllByRole("button")).toHaveLength(0);
    });

    it("zeigt bei gescheiterter Aktualisierung weiter die Geräte und sagt, dass sie nicht mehr aktuell sind", () => {
      zustand({ stand: stand(), fehler: 503 });
      render(<BueroKarte />);
      expect(punkt(drucker)).toBeTruthy();
      expect(screen.getByText(/Aktualisierung gerade nicht möglich, Stand \d\d:\d\d Uhr\. Es wird weiter versucht\./)).toBeTruthy();
    });
  });

  describe("Hinweis auf unbekannte Geräte im Netz", () => {
    it("erscheint mit der Zahl, wenn sie größer als 0 ist", () => {
      zustand({ stand: stand(undefined, 3) });
      render(<BueroKarte />);
      expect(screen.getByText("3 unbekannte Geräte im Netz")).toBeTruthy();
    });

    it("steht bei genau einem Gerät im Singular", () => {
      zustand({ stand: stand(undefined, 1) });
      render(<BueroKarte />);
      expect(screen.getByText("1 unbekanntes Gerät im Netz")).toBeTruthy();
    });

    it.each([[0], [null]])("fehlt bei der Zahl %s", (anzahl) => {
      zustand({ stand: stand(undefined, anzahl) });
      render(<BueroKarte />);
      expect(screen.queryByText(/im Netz/)).toBeNull();
    });

    it("nennt den Sprechtipp nur Admins", () => {
      zustand({ stand: stand(undefined, 2) });
      const { unmount } = render(<BueroKarte />);
      expect(screen.queryByText(UNBEKANNT_TIPP)).toBeNull(); // ohne Profil und für Mitglieder
      unmount();
      wamo.set((s) => ({ ...s, profil: { name: "Mitglied", rolle: "mitglied" } }));
      const mitglied = render(<BueroKarte />);
      expect(screen.queryByText(UNBEKANNT_TIPP)).toBeNull();
      mitglied.unmount();
      wamo.set((s) => ({ ...s, profil: { name: "Admin", rolle: "admin" } }));
      render(<BueroKarte />);
      expect(screen.getByText(UNBEKANNT_TIPP)).toBeTruthy();
    });
  });

  describe("Schalten (Knöpfe der Detailkarte)", () => {
    const tvAn: BueroGeraet = { id: "tv-1", name: "Fernseher LG", gruppe: "medien", ampel: "ok", text: "an", tipp: "Sag: Schalte den Fernseher aus", aktionen: ["aus", "lauter", "leiser", "stumm", "laut"] };
    const tvAus: BueroGeraet = { id: "tv-2", name: "Fernseher Samsung", gruppe: "medien", ampel: "ok", text: "aus", tipp: "", aktionen: ["an"] };
    const dose: BueroGeraet = { id: "steckdose-1", name: "Steckdose 1", gruppe: "steckdosen", ampel: "ok", text: "an, 12 Watt", tipp: "", aktionen: ["aus"] };

    const oeffnen = async (g: BueroGeraet, geraete: BueroGeraet[] = [tvAn, tvAus, dose, server]) => {
      const user = userEvent.setup();
      zustand({ stand: stand(geraete) });
      render(<BueroKarte />);
      await user.click(punkt(g));
      return user;
    };
    const knopf = (name: string | RegExp) => screen.getByRole("button", { name });

    it("zeigt in der Detailkarte genau die Knöpfe, die der Kern anbietet", async () => {
      await oeffnen(tvAn);
      const gruppe = screen.getByRole("group", { name: "Fernseher LG schalten" });
      expect(within(gruppe).getAllByRole("button").map((b) => b.textContent)).toEqual(["Ausschalten", "Lauter", "Leiser", "Stumm", "Ton an"]);
    });

    it("ein Fernseher im Standby bietet nur Einschalten an", async () => {
      await oeffnen(tvAus);
      const gruppe = screen.getByRole("group", { name: "Fernseher Samsung schalten" });
      expect(within(gruppe).getAllByRole("button").map((b) => b.textContent)).toEqual(["Einschalten"]);
    });

    it("ohne aktionen (Mitglied, nicht gekoppelt, kein steuerbares Gerät) gibt es keine Knöpfe und der Sprechtipp bleibt", async () => {
      await oeffnen(server);
      expect(screen.queryByRole("group", { name: /schalten/ })).toBeNull();
      expect(screen.getByText("Sag: Wie geht es dem Server?")).toBeTruthy();
    });

    it("ein Tipp schaltet: Aufruf mit Kennung und Aktion, Meldung, danach holt die Karte den Stand neu", async () => {
      const user = await oeffnen(tvAus);
      await user.click(knopf("Einschalten"));
      expect(bueroSteuern).toHaveBeenCalledTimes(1);
      expect(bueroSteuern).toHaveBeenCalledWith("tv-2", "an");
      expect(bueroNachSchalten).toHaveBeenCalledWith("an");
      expect(await screen.findByText(/Einschalten gesendet/)).toBeTruthy();
    });

    it("ein Doppelklick zählt einmal", async () => {
      const user = await oeffnen(tvAn);
      await user.dblClick(knopf("Lauter"));
      expect(bueroSteuern).toHaveBeenCalledTimes(1);
    });

    it("während ein Befehl läuft, sind alle Knöpfe aus", async () => {
      let fertig: () => void = () => undefined;
      vi.mocked(bueroSteuern).mockImplementation(
        () =>
          new Promise<void>((r) => {
            fertig = r;
          }),
      );
      const user = await oeffnen(tvAn);
      await user.click(knopf("Lauter"));
      const gruppe = screen.getByRole("group", { name: "Fernseher LG schalten" });
      for (const b of within(gruppe).getAllByRole("button")) expect((b as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByText("Wird gesendet …")).toBeTruthy();
      await act(async () => fertig());
      expect(await screen.findByText("Lauter gesendet.")).toBeTruthy();
      for (const b of within(gruppe).getAllByRole("button")) expect((b as HTMLButtonElement).disabled).toBe(false);
    });

    it("Steckdose ausschalten verlangt einen zweiten Tipp", async () => {
      const user = await oeffnen(dose);
      await user.click(knopf("Ausschalten"));
      expect(bueroSteuern).not.toHaveBeenCalled();
      expect(screen.getByText("Sicher? Noch einmal tippen")).toBeTruthy();
      await user.click(knopf("Sicher? Noch einmal tippen"));
      expect(bueroSteuern).toHaveBeenCalledWith("steckdose-1", "aus");
    });

    it("die Rückfrage der Steckdose verfällt nach ein paar Sekunden", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      try {
        const user = await oeffnen(dose);
        await user.click(knopf("Ausschalten"));
        expect(screen.getByText("Sicher? Noch einmal tippen")).toBeTruthy();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(4100);
        });
        expect(screen.queryByText("Sicher? Noch einmal tippen")).toBeNull();
        expect(bueroSteuern).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });

    it.each([
      [new KernFehler(403, "Bürogeräte schalten dürfen nur Admins"), "Bürogeräte schalten dürfen nur Admins."],
      [new KernFehler(409, "Das geht mit diesem Gerät nicht"), "Das geht mit diesem Gerät nicht"],
      [new KernFehler(502, "Das Gerät hat nicht geantwortet. Ist es erreichbar?"), "Das Gerät hat nicht geantwortet. Ist es erreichbar?"],
      [new KernFehler(0, "Kern nicht erreichbar"), "Der Kern ist gerade nicht erreichbar. Bitte gleich noch einmal versuchen."],
      [new Error("irgendwas"), "Der Kern ist gerade nicht erreichbar. Bitte gleich noch einmal versuchen."],
    ])("Fehler %#: ruhige Meldung, kein Neuladen, Knöpfe wieder frei", async (fehler, text) => {
      vi.mocked(bueroSteuern).mockRejectedValue(fehler);
      const user = await oeffnen(tvAn);
      await user.click(knopf("Leiser"));
      expect(await screen.findByText(text)).toBeTruthy();
      expect(bueroNachSchalten).not.toHaveBeenCalled();
      expect((knopf("Leiser") as HTMLButtonElement).disabled).toBe(false);
    });

    it("die Meldung steht in einem Statusbereich für Bildschirmleser", async () => {
      const user = await oeffnen(tvAn);
      await user.click(knopf("Stumm"));
      const meldung = await screen.findByText("Stumm gesendet.");
      expect(meldung.getAttribute("role")).toBe("status");
      expect(meldung.getAttribute("aria-live")).toBe("polite");
    });

    it("beim Wechsel zu einem anderen Gerät beginnt die Meldung neu", async () => {
      const user = await oeffnen(tvAn);
      await user.click(knopf("Stumm"));
      await screen.findByText("Stumm gesendet.");
      await user.click(punkt(tvAus));
      expect(screen.queryByText("Stumm gesendet.")).toBeNull();
    });
  });
});
