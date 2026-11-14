// Komponententest der Fehleranzeige (components/FehlerAnzeige.tsx): Bei einer Mikrofon-Warnung gibt es den Knopf "Ohne Mikrofon weiter".
// Anlass 12.11.2026: Das eingebaute Mikrofon lieferte unter Windows nur Nullen, die Warnung kam bei jedem Gespräch wieder und ließ sich nur
// schließen. Der Knopf schließt sie und merkt sich das Gerät (lib/mikrofon.ts), bis es wieder Ton liefert. Ausführen im Ordner ui/:  npm run test:komponenten
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FehlerAnzeige } from "@/components/FehlerAnzeige";
import { stilleAusgeblendet, stilleWiederAufnehmen } from "@/lib/mikrofon";
import { wamo } from "@/lib/wamo";

const GERAET = "Onboard MIC (Intel® Smart Sound Technologie für digitale Mikrofone)";
const MIKROFON_FEHLER = { art: "mikrofon" as const, text: `Das Mikrofon („${GERAET}“) liefert keinen Ton, W.A.I.S. hört dich nicht.` };

function zeigen() {
  const onSchliessen = vi.fn();
  render(
    <FehlerAnzeige
      onRetry={vi.fn()}
      onAbmelden={vi.fn()}
      onSchliessen={onSchliessen}
      onMikrofone={async () => []}
      onMikrofonWaehlen={() => true}
    />,
  );
  return onSchliessen;
}

beforeEach(() => {
  wamo.set((s) => ({ ...s, error: MIKROFON_FEHLER, mikrofon: GERAET }));
});

afterEach(() => {
  stilleWiederAufnehmen(GERAET);
  wamo.set((s) => ({ ...s, error: null, mikrofon: null }));
});

describe("Fehleranzeige: Mikrofon-Warnung", () => {
  it("bietet bei einer Mikrofon-Warnung den Knopf Ohne Mikrofon weiter an, neben Schließen", () => {
    zeigen();
    expect(screen.getByRole("button", { name: "Ohne Mikrofon weiter" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Schließen" })).toBeTruthy();
  });

  it("der Klick schließt die Warnung und merkt sich das aktuelle Gerät", async () => {
    const onSchliessen = zeigen();
    expect(stilleAusgeblendet(GERAET)).toBe(false);
    await userEvent.setup().click(screen.getByRole("button", { name: "Ohne Mikrofon weiter" }));
    expect(onSchliessen).toHaveBeenCalledTimes(1);
    expect(stilleAusgeblendet(GERAET)).toBe(true);
  });

  it("Schließen allein merkt sich nichts: die Warnung darf beim nächsten Mal wieder kommen", async () => {
    const onSchliessen = zeigen();
    await userEvent.setup().click(screen.getByRole("button", { name: "Schließen" }));
    expect(onSchliessen).toHaveBeenCalledTimes(1);
    expect(stilleAusgeblendet(GERAET)).toBe(false);
  });

  it("der Knopf erklärt per Hilfetext, was er tut, ohne Gedankenstrich", () => {
    zeigen();
    const hilfe = screen.getByRole("button", { name: "Ohne Mikrofon weiter" }).getAttribute("title") ?? "";
    expect(hilfe).toMatch(/tippen/);
    expect(hilfe).toMatch(/wieder Ton/);
    expect(hilfe).not.toMatch(/[–—]/);
  });

  it("andere Meldungen haben den Knopf nicht", () => {
    wamo.set((s) => ({ ...s, error: { art: "hinweis", text: "Die Antwort lässt auf sich warten." } }));
    zeigen();
    expect(screen.queryByRole("button", { name: "Ohne Mikrofon weiter" })).toBeNull();
    expect(screen.getByRole("button", { name: "Schließen" })).toBeTruthy();
  });

  it("ohne Fehler zeigt die Anzeige nichts", () => {
    wamo.set((s) => ({ ...s, error: null }));
    zeigen();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Schließen" })).toBeNull();
  });
});
