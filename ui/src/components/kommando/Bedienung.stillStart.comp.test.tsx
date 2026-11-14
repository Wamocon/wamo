// Komponententest der Eingabepille beim Start durch Tippen (kommando/Bedienung.tsx, KP-1A): Das Feld ist ohne Sitzung nur mit eingeschaltetem
// Schalter aktiv, der erste Tastendruck startet einmal, die erste Zeile wird gehalten und genau einmal gesendet, Fehler und Abbruch lassen sie
// im Feld stehen, die Statuszeile nennt den Zustand in Worten, der Mikrofonknopf einer stillen Sitzung heißt "Sprechen".
// Ausführen im Ordner ui/:  npm run test:komponenten
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nutzerSetzen } from "@/lib/nutzer";
import { STILL_AUS, TEXT_STILL_BEREIT, TEXT_VERBINDET } from "@/lib/stillStart";
import { resetWamo, wamo } from "@/lib/wamo";
import { Bedienung } from "./Bedienung";

const NUTZER = "0b7f3c1e-2d4a-4c55-9a10-1a2b3c4d5e6f";

function schalter(an: boolean, email = "niko@wamocon.com") {
  nutzerSetzen({ id: NUTZER, email });
  if (an) localStorage.setItem(`wamo.stillStart.${NUTZER}`, "an");
  else localStorage.setItem(`wamo.stillStart.${NUTZER}`, "aus");
}

/** stillStarten verhält sich wie das echte: Die Verbindung beginnt (presence connecting, still). */
function zeichne(opt: { startet?: boolean } = {}) {
  const a = {
    senden: vi.fn<(t: string) => boolean>().mockReturnValue(true),
    stillStarten: vi.fn<() => boolean>(() => {
      if (opt.startet === false) return false;
      wamo.set((s) => ({ ...s, presence: "connecting", micOn: false, stillStart: { still: true, perTippen: true, uebernommen: false } }));
      return true;
    }),
    eingabeMelden: vi.fn<() => void>(),
    onMikro: vi.fn<() => void>(),
  };
  render(
    <Bedienung
      onMikro={a.onMikro}
      senden={a.senden}
      stillStarten={a.stillStarten}
      eingabeMelden={a.eingabeMelden}
      verlaufOffen={false}
      onVerlauf={() => undefined}
    />,
  );
  return a;
}

const feld = () => screen.getByRole("textbox", { name: "Nachricht an W.A.I.S." }) as HTMLInputElement;

/** Der Kern ist bereit und nimmt Text an (wie nach onBotReady). */
const verbunden = () =>
  act(() => {
    wamo.set((s) => ({ ...s, presence: "idle", textMoeglich: true }));
  });

describe("Eingabepille, Start beim Tippen", () => {
  beforeEach(() => {
    resetWamo();
    localStorage.clear();
    nutzerSetzen({ id: null, email: null });
  });

  describe("Feld ohne Sitzung", () => {
    it("Schalter aus (Standard): das Feld bleibt gesperrt wie bisher", () => {
      schalter(false);
      zeichne();
      expect(feld().disabled).toBe(true);
      expect(feld().placeholder).toBe("Sprich einfach mit W.A.I.S.");
    });

    it("Schalter an: das Feld ist ohne Sitzung aktiv und sagt, dass Tippen W.A.I.S. startet", () => {
      schalter(true);
      zeichne();
      expect(feld().disabled).toBe(false);
      expect(feld().placeholder).toMatch(/Tippe/);
    });

    it("Laborkonto: das Feld bleibt gesperrt, auch bei eingeschaltetem Schalter", () => {
      schalter(true, "labor-03@wamo.test");
      zeichne();
      expect(feld().disabled).toBe(true);
    });
  });

  describe("erster Tastendruck", () => {
    it("startet genau einmal, nicht bei jedem Zeichen", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.type(feld(), "Hallo W.A.I.S.");
      expect(a.stillStarten).toHaveBeenCalledTimes(1);
      expect(feld().value).toBe("Hallo W.A.I.S.");
      expect(a.eingabeMelden).toHaveBeenCalled();
    });

    it("Leerzeichen allein starten nichts", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.type(feld(), "   ");
      expect(a.stillStarten).not.toHaveBeenCalled();
    });

    it("Fokus allein startet nichts (auch nicht Tab oder Pfeiltasten)", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.click(feld());
      await userEvent.keyboard("{ArrowLeft}{ArrowRight}{Home}{End}");
      expect(a.stillStarten).not.toHaveBeenCalled();
    });

    it("ohne Schalter startet das Tippen nichts (das Feld ist gesperrt)", async () => {
      schalter(false);
      const a = zeichne();
      await userEvent.type(feld(), "Hallo");
      expect(a.stillStarten).not.toHaveBeenCalled();
    });
  });

  describe("erste Zeile halten und einmal senden", () => {
    it("Enter während des Verbindens hält die Zeile, zeigt 'Verbinde' und sendet erst, wenn die Sitzung steht", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.type(feld(), "Wie spät ist es?{Enter}");
      expect(a.senden).not.toHaveBeenCalled();
      expect(feld().value).toBe("Wie spät ist es?"); // bleibt im Feld, bis sie wirklich gesendet ist
      expect(screen.getByText(TEXT_VERBINDET)).toBeTruthy();
      expect(feld().readOnly).toBe(true);
      verbunden();
      expect(a.senden).toHaveBeenCalledTimes(1);
      expect(a.senden).toHaveBeenCalledWith("Wie spät ist es?");
      expect(feld().value).toBe("");
      expect(feld().readOnly).toBe(false);
    });

    it("kein Doppelversand: weitere Änderungen des Zustands senden nichts noch einmal", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.type(feld(), "eins{Enter}");
      verbunden();
      act(() => {
        wamo.set((s) => ({ ...s, presence: "thinking" }));
      });
      act(() => {
        wamo.set((s) => ({ ...s, presence: "idle", textMoeglich: true }));
      });
      act(() => {
        wamo.set((s) => ({ ...s, ansage: "x" }));
      });
      expect(a.senden).toHaveBeenCalledTimes(1);
    });

    it("zweimal Enter während des Wartens hält trotzdem nur eine Zeile", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.type(feld(), "eins{Enter}{Enter}");
      verbunden();
      expect(a.senden).toHaveBeenCalledTimes(1);
    });

    it("Enter ohne vorherigen Start (Einfügen und Absenden) startet und hält die Zeile", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.click(feld());
      await userEvent.paste("eingefügt");
      expect(a.stillStarten).toHaveBeenCalledTimes(1); // das Einfügen löst onChange aus
      await userEvent.keyboard("{Enter}");
      expect(a.senden).not.toHaveBeenCalled();
      verbunden();
      expect(a.senden).toHaveBeenCalledWith("eingefügt");
    });

    it("Verbindungsfehler: die Zeile bleibt im Feld, nichts wird gesendet, die Anzeige 'Verbinde' endet", async () => {
      schalter(true);
      const a = zeichne();
      await userEvent.type(feld(), "bleibt{Enter}");
      act(() => {
        wamo.set((s) => ({ ...s, presence: "offline", stillStart: STILL_AUS, error: { art: "verbindung", text: "Kern nicht erreichbar" } }));
      });
      expect(a.senden).not.toHaveBeenCalled();
      expect(feld().value).toBe("bleibt");
      expect(feld().readOnly).toBe(false);
      expect(screen.queryByText(TEXT_VERBINDET)).toBeNull();
      // ein später doch noch erreichter Kern sendet die Zeile nicht nachträglich von selbst
      verbunden();
      expect(a.senden).not.toHaveBeenCalled();
    });

    it("lehnt der Start ab (zum Beispiel verdeckter Tab), wird nichts gehalten", async () => {
      schalter(true);
      const a = zeichne({ startet: false });
      await userEvent.type(feld(), "x{Enter}");
      verbunden();
      expect(a.senden).not.toHaveBeenCalled();
      expect(feld().value).toBe("x");
    });
  });

  describe("stille Sitzung läuft", () => {
    it("Statuszeile nennt in Worten, dass das Mikrofon aus ist, und der Mikrofonknopf heißt Sprechen", () => {
      schalter(true);
      zeichne();
      act(() => {
        wamo.set((s) => ({ ...s, presence: "idle", textMoeglich: true, micOn: false, stillStart: { still: true, perTippen: true, uebernommen: false } }));
      });
      expect(screen.getByText(TEXT_STILL_BEREIT)).toBeTruthy();
      expect(screen.getByRole("button", { name: /Mikrofon einschalten und sprechen/ })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Mikrofon stumm schalten" })).toBeNull();
    });

    it("ein Klick auf den Mikrofonknopf ruft onMikro (der Hook macht daraus Sprechen)", async () => {
      schalter(true);
      const a = zeichne();
      act(() => {
        wamo.set((s) => ({ ...s, presence: "idle", textMoeglich: true, micOn: false, stillStart: { still: true, perTippen: true, uebernommen: false } }));
      });
      await userEvent.click(screen.getByRole("button", { name: /Mikrofon einschalten und sprechen/ }));
      expect(a.onMikro).toHaveBeenCalledTimes(1);
    });

    it("ein normales Gespräch behält den Knopf 'Mikrofon stumm schalten'", () => {
      schalter(true);
      zeichne();
      act(() => {
        wamo.set((s) => ({ ...s, presence: "idle", textMoeglich: true }));
      });
      expect(screen.getByRole("button", { name: "Mikrofon stumm schalten" })).toBeTruthy();
    });

    it("eine Freigabekarte hat mit ihrem Hinweis Vorrang vor der Statuszeile", () => {
      schalter(true);
      zeichne();
      act(() => {
        wamo.set((s) => ({
          ...s,
          presence: "idle",
          textMoeglich: true,
          micOn: false,
          stillStart: { still: true, perTippen: true, uebernommen: false },
          approvals: [{ job: "pc", projekt: "x", id: "1", grund: "g", detail: "d" }],
        }));
      });
      expect(screen.getByText(/Freigaben gibst du nur mit Klick auf die Karte/)).toBeTruthy();
      expect(screen.queryByText(TEXT_STILL_BEREIT)).toBeNull();
    });
  });
});
