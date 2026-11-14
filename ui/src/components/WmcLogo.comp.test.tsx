// Komponententest des WMC-Logos (components/WmcLogo.tsx): eine Fassung für den Hellmodus (schwarz und rot) und eine für den
// Dunkelmodus (weiß und rot, ohne weiße Platte). Welche sichtbar ist, entscheidet globals.css über data-theme.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WmcLogo } from "@/components/WmcLogo";

const quelle = (bild: Element) => decodeURIComponent(bild.getAttribute("src") ?? "");

describe("WmcLogo", () => {
  it("hat je eine Fassung für hell und dunkel mit der passenden Datei", () => {
    const { container } = render(<WmcLogo />);
    const hell = container.querySelector("img.logo-hell");
    const dunkel = container.querySelector("img.logo-dunkel");
    expect(hell).not.toBeNull();
    expect(dunkel).not.toBeNull();
    expect(quelle(hell!)).toContain("/wmc-logo.png");
    expect(quelle(dunkel!)).toContain("/wmc-logo-dunkel.png");
  });

  it("nennt den Namen genau einmal für Hilfstechnik", () => {
    const { container } = render(<WmcLogo gross />);
    const mitText = [...container.querySelectorAll("img")].filter((b) => (b.getAttribute("alt") ?? "") !== "");
    expect(mitText).toHaveLength(1);
    expect(mitText[0].getAttribute("alt")).toBe("WAMOCON IT-Testmanagement");
    const ohneText = [...container.querySelectorAll("img")].find((b) => b.getAttribute("alt") === "");
    expect(ohneText?.getAttribute("aria-hidden")).toBe("true");
  });
});
