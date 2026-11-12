// Komponententest der ziehbaren Seitenleisten (kommando/Rahmen.tsx, Seitengriff.tsx, useSeitenbreiten.ts): zwei Griffe als Trenner mit
// Namen und Werten, Ziehen mit dem Zeiger ändert die CSS-Variablen am Hauptbereich, Tastatur, Doppelklick, Platz für die Mitte,
// Speicher je Gerät (auch gesperrt oder voll), kein Hydrationsfehler, keine Griffe in Fokus und Zentrale.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetWamo } from "@/lib/wamo";
import { darstellungSetzen } from "./darstellung";
import { KommandoRahmen } from "./Rahmen";
import { SCHLUESSEL } from "./seitenbreite";

const rahmen = () => (
  <KommandoRahmen
    kopf={<header>Kopf</header>}
    meldungen={null}
    kernbereich={<p>Inhalt Mitte</p>}
    aktionen={null}
    bedienung={null}
    canvas={null}
    hinweise={<p>Inhalt links</p>}
    auftraege={<p>Inhalt rechts</p>}
    vorgaenge={null}
    erledigt={null}
    anhang={null}
  />
);

const links = () => screen.getByRole("separator", { name: "Breite der linken Seitenleiste" });
const rechts = () => screen.getByRole("separator", { name: "Breite der rechten Seitenleiste" });
const haupt = () => screen.getByRole("main");
const variable = (name: "--kz-links" | "--kz-rechts") => haupt().style.getPropertyValue(name);
const wert = (griff: HTMLElement) => Number(griff.getAttribute("aria-valuenow"));

/** Die Breite des Fensters, die die Oberfläche misst (jsdom liefert sonst 0 und damit "unbekannt"). */
function fensterBreite(px: number) {
  Object.defineProperty(document.documentElement, "clientWidth", { configurable: true, value: px });
}

/** Zeiger auf dem Griff: drücken bei x=von, bewegen nach x=nach, optional loslassen. */
function ziehe(griff: HTMLElement, von: number, nach: number, loslassen = true) {
  fireEvent.pointerDown(griff, { pointerId: 7, button: 0, clientX: von });
  fireEvent.pointerMove(griff, { pointerId: 7, clientX: nach });
  if (loslassen) fireEvent.pointerUp(griff, { pointerId: 7, clientX: nach });
}

/** Speicher, der bei jedem Zugriff wirft (gesperrte Website-Daten) oder nur beim Schreiben (voll). */
function speicherErsetzen(art: "gesperrt" | "voll") {
  const vorher = Object.getOwnPropertyDescriptor(window, "localStorage")!;
  const echt = window.localStorage;
  const werfen = () => {
    throw new Error(art === "voll" ? "QuotaExceededError" : "SecurityError");
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value:
      art === "voll"
        ? { getItem: (k: string) => echt.getItem(k), setItem: werfen, removeItem: werfen, clear: werfen }
        : { getItem: werfen, setItem: werfen, removeItem: werfen, clear: werfen },
  });
  return () => Object.defineProperty(window, "localStorage", vorher);
}

beforeEach(() => {
  resetWamo();
  localStorage.clear();
  darstellungSetzen("normal", false);
});

afterEach(() => {
  Reflect.deleteProperty(document.documentElement, "clientWidth");
  darstellungSetzen("normal", false);
});

describe("Griffe der Seitenleisten", () => {
  it("zwei Trenner mit deutschem Namen, Ausrichtung, Werten und Tastaturfokus", () => {
    render(rahmen());
    for (const [griff, nutzen, min, max] of [
      [links(), 300, 200, 520],
      [rechts(), 380, 260, 640],
    ] as const) {
      expect(griff.getAttribute("aria-orientation")).toBe("vertical");
      expect(griff.getAttribute("aria-valuenow")).toBe(String(nutzen));
      expect(griff.getAttribute("aria-valuemin")).toBe(String(min));
      expect(griff.getAttribute("aria-valuemax")).toBe(String(max));
      expect(griff.getAttribute("aria-valuetext")).toBe(`${nutzen} Pixel`);
      expect(griff.tabIndex).toBe(0);
    }
  });

  it("die Griffe stehen in der Lesereihenfolge zwischen den Spalten", () => {
    render(rahmen());
    const folge = [screen.getByText("Inhalt links"), links(), screen.getByText("Inhalt Mitte"), rechts(), screen.getByText("Inhalt rechts")];
    for (let i = 0; i < folge.length - 1; i++) {
      expect(folge[i].compareDocumentPosition(folge[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("ohne gespeicherte Wahl setzt der Rahmen keine Variablen (es gilt der Standard aus dem CSS)", () => {
    render(rahmen());
    expect(variable("--kz-links")).toBe("");
    expect(variable("--kz-rechts")).toBe("");
    expect(haupt().hasAttribute("data-zieht")).toBe(false);
  });

  it("in Fokus und Zentrale gibt es keine Griffe", () => {
    for (const d of ["fokus", "zentrale"] as const) {
      darstellungSetzen(d, false);
      const { unmount } = render(rahmen());
      expect(screen.queryAllByRole("separator")).toHaveLength(0);
      unmount();
    }
  });
});

describe("Ziehen mit dem Zeiger", () => {
  it("die linke Breite folgt dem Zeiger und wird erst beim Loslassen gespeichert", () => {
    render(rahmen());
    ziehe(links(), 300, 360, false);
    expect(variable("--kz-links")).toBe("360px");
    expect(wert(links())).toBe(360);
    expect(haupt().getAttribute("data-zieht")).toBe("links");
    expect(localStorage.getItem(SCHLUESSEL.links)).toBeNull();
    fireEvent.pointerUp(links(), { pointerId: 7, clientX: 360 });
    expect(haupt().hasAttribute("data-zieht")).toBe(false);
    expect(variable("--kz-links")).toBe("360px");
    expect(localStorage.getItem(SCHLUESSEL.links)).toBe("360");
    expect(localStorage.getItem(SCHLUESSEL.rechts)).toBeNull();
  });

  it("die rechte Breite wächst, wenn der Zeiger nach links geht", () => {
    render(rahmen());
    ziehe(rechts(), 900, 850);
    expect(variable("--kz-rechts")).toBe("430px");
    expect(localStorage.getItem(SCHLUESSEL.rechts)).toBe("430");
    expect(variable("--kz-links")).toBe("");
  });

  it("die Breite bleibt in den Grenzen: links 200 bis 520, rechts 260 bis 640", () => {
    render(rahmen());
    ziehe(links(), 300, 5000);
    expect(variable("--kz-links")).toBe("520px");
    ziehe(links(), 520, -5000);
    expect(variable("--kz-links")).toBe("200px");
    ziehe(rechts(), 900, -5000);
    expect(variable("--kz-rechts")).toBe("640px");
    ziehe(rechts(), 900, 5000);
    expect(variable("--kz-rechts")).toBe("260px");
  });

  it("der Griff fängt den Zeiger (Pointer Capture)", () => {
    render(rahmen());
    const fangen = vi.fn();
    links().setPointerCapture = fangen;
    ziehe(links(), 300, 320);
    expect(fangen).toHaveBeenCalledWith(7);
  });

  it("ein Klick ohne Bewegung ändert nichts und schreibt nichts", () => {
    render(rahmen());
    ziehe(links(), 300, 300);
    expect(variable("--kz-links")).toBe("");
    expect(localStorage.getItem(SCHLUESSEL.links)).toBeNull();
    expect(haupt().hasAttribute("data-zieht")).toBe(false);
  });

  it("andere Tasten der Maus oder fremde Zeiger ziehen nicht", () => {
    render(rahmen());
    fireEvent.pointerDown(links(), { pointerId: 7, button: 2, clientX: 300 });
    fireEvent.pointerMove(links(), { pointerId: 7, clientX: 400 });
    expect(variable("--kz-links")).toBe("");
    fireEvent.pointerDown(links(), { pointerId: 7, button: 0, clientX: 300 });
    fireEvent.pointerMove(links(), { pointerId: 8, clientX: 400 });
    expect(variable("--kz-links")).toBe("");
    fireEvent.pointerUp(links(), { pointerId: 7, clientX: 300 });
  });

  it("Abbruch des Zeigers beendet das Ziehen und behält die gezeigte Breite", () => {
    render(rahmen());
    ziehe(links(), 300, 340, false);
    fireEvent.pointerCancel(links(), { pointerId: 7 });
    expect(haupt().hasAttribute("data-zieht")).toBe(false);
    expect(localStorage.getItem(SCHLUESSEL.links)).toBe("340");
  });
});

describe("Platz für die Mitte", () => {
  it("die Mitte behält mindestens 360 px: die linke Breite endet früher", () => {
    fensterBreite(1280);
    localStorage.setItem(SCHLUESSEL.rechts, "600");
    render(rahmen());
    expect(links().getAttribute("aria-valuemax")).toBe("320"); // 1280 - 360 - 600
    ziehe(links(), 300, 5000);
    expect(variable("--kz-links")).toBe("320px");
    expect(rechts().getAttribute("aria-valuemax")).toBe("600"); // 1280 - 360 - 320 liegt unter der festen Obergrenze 640
  });

  it("ein kleineres Fenster nimmt die Breiten zurück, ein größeres stellt sie wieder her", () => {
    localStorage.setItem(SCHLUESSEL.links, "500");
    localStorage.setItem(SCHLUESSEL.rechts, "600");
    fensterBreite(1280);
    render(rahmen());
    expect(wert(links())).toBe(500);
    expect(wert(rechts())).toBe(420); // 1280 - 360 - 500
    expect(variable("--kz-rechts")).toBe("420px");
    fensterBreite(2000);
    fireEvent(window, new Event("resize"));
    expect(wert(rechts())).toBe(600);
    expect(variable("--kz-rechts")).toBe("600px");
  });
});

describe("Tastatur", () => {
  it("links: Pfeil rechts wird breiter, Pfeil links schmaler (16 px), mit Umschalt 48 px, jeweils gespeichert", async () => {
    const nutzer = userEvent.setup();
    render(rahmen());
    links().focus();
    await nutzer.keyboard("{ArrowRight}");
    expect(wert(links())).toBe(316);
    await nutzer.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(wert(links())).toBe(364);
    await nutzer.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(wert(links())).toBe(332);
    await nutzer.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(wert(links())).toBe(284);
    expect(variable("--kz-links")).toBe("284px");
    expect(localStorage.getItem(SCHLUESSEL.links)).toBe("284");
  });

  it("rechts: Pfeil links macht die Leiste breiter, weil der Griff nach links wandert", async () => {
    const nutzer = userEvent.setup();
    render(rahmen());
    rechts().focus();
    await nutzer.keyboard("{ArrowLeft}");
    expect(wert(rechts())).toBe(396);
    await nutzer.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(wert(rechts())).toBe(348);
  });

  it("Pos1 springt auf die Mindestbreite, Ende auf die Höchstbreite", async () => {
    const nutzer = userEvent.setup();
    render(rahmen());
    links().focus();
    await nutzer.keyboard("{Home}");
    expect(wert(links())).toBe(200);
    await nutzer.keyboard("{End}");
    expect(wert(links())).toBe(520);
    rechts().focus();
    await nutzer.keyboard("{Home}");
    expect(wert(rechts())).toBe(260);
    await nutzer.keyboard("{End}");
    expect(wert(rechts())).toBe(640);
  });

  it("an der Grenze bleibt die Breite stehen", async () => {
    const nutzer = userEvent.setup();
    render(rahmen());
    links().focus();
    await nutzer.keyboard("{End}{ArrowRight}{Shift>}{ArrowRight}{/Shift}");
    expect(wert(links())).toBe(520);
  });

  it("andere Tasten und Tastenkombinationen (zum Beispiel Alt und Pfeil links für Zurück) bleiben unberührt", () => {
    render(rahmen());
    for (const eingabe of [{ key: "ArrowUp" }, { key: "Enter" }, { key: "ArrowLeft", altKey: true }, { key: "ArrowLeft", ctrlKey: true }]) {
      const ereignis = fireEvent.keyDown(links(), eingabe);
      expect(ereignis, JSON.stringify(eingabe)).toBe(true); // nicht abgebrochen: der Browser behandelt sie weiter
    }
    expect(wert(links())).toBe(300);
    expect(localStorage.getItem(SCHLUESSEL.links)).toBeNull();
  });

  it("eine behandelte Pfeiltaste verhindert das Scrollen der Seite", () => {
    render(rahmen());
    expect(fireEvent.keyDown(links(), { key: "ArrowRight" })).toBe(false);
  });
});

describe("Doppelklick", () => {
  it("setzt die Breite auf den Standard zurück und entfernt die gespeicherte Wahl", () => {
    localStorage.setItem(SCHLUESSEL.links, "450");
    localStorage.setItem(SCHLUESSEL.rechts, "500");
    render(rahmen());
    expect(wert(links())).toBe(450);
    fireEvent.doubleClick(links());
    expect(wert(links())).toBe(300);
    expect(variable("--kz-links")).toBe("");
    expect(localStorage.getItem(SCHLUESSEL.links)).toBeNull();
    expect(wert(rechts())).toBe(500); // die andere Seite bleibt
    expect(localStorage.getItem(SCHLUESSEL.rechts)).toBe("500");
    fireEvent.doubleClick(rechts());
    expect(wert(rechts())).toBe(380);
  });
});

describe("Speicher je Gerät", () => {
  it("gespeicherte Breiten gelten nach dem Mounten", () => {
    localStorage.setItem(SCHLUESSEL.links, "340");
    localStorage.setItem(SCHLUESSEL.rechts, "450");
    render(rahmen());
    expect(variable("--kz-links")).toBe("340px");
    expect(variable("--kz-rechts")).toBe("450px");
    expect(wert(links())).toBe(340);
    expect(wert(rechts())).toBe(450);
  });

  it("Werte außerhalb des Bereichs werden geklemmt, Ungültiges verworfen", () => {
    localStorage.setItem(SCHLUESSEL.links, "9999");
    localStorage.setItem(SCHLUESSEL.rechts, "abc");
    render(rahmen());
    expect(wert(links())).toBe(520);
    expect(wert(rechts())).toBe(380);
    expect(variable("--kz-rechts")).toBe("");
  });

  it("gesperrter Speicher: die Seite läuft, die Wahl gilt bis zum Neuladen", () => {
    const zurueck = speicherErsetzen("gesperrt");
    try {
      render(rahmen());
      expect(wert(links())).toBe(300);
      ziehe(links(), 300, 350);
      expect(wert(links())).toBe(350);
      fireEvent.doubleClick(links());
      expect(wert(links())).toBe(300);
    } finally {
      zurueck();
    }
  });

  it("voller Speicher: Lesen geht, Schreiben schlägt still fehl, die Wahl gilt bis zum Neuladen", () => {
    localStorage.setItem(SCHLUESSEL.rechts, "420");
    const zurueck = speicherErsetzen("voll");
    try {
      render(rahmen());
      expect(wert(rechts())).toBe(420);
      ziehe(rechts(), 900, 880);
      expect(wert(rechts())).toBe(440);
      fireEvent.keyDown(rechts(), { key: "ArrowLeft" });
      expect(wert(rechts())).toBe(456);
    } finally {
      zurueck();
    }
    expect(localStorage.getItem(SCHLUESSEL.rechts)).toBe("420");
  });
});

describe("erstes Laden", () => {
  it("der Server zeichnet immer die Standardbreiten, ohne Variablen", () => {
    localStorage.setItem(SCHLUESSEL.links, "400");
    const html = renderToString(rahmen());
    expect(html).not.toContain("--kz-links");
    expect(html).toContain('aria-valuenow="300"');
  });

  it("Hydration mit gespeicherter Breite meldet keinen Fehler, der Wert gilt danach", () => {
    const html = renderToString(rahmen()); // wie auf dem Server: ohne Speicher, immer die Standardbreiten
    localStorage.setItem(SCHLUESSEL.links, "400");
    const fehler = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const behaelter = document.body.appendChild(document.createElement("div"));
    behaelter.innerHTML = html;
    render(rahmen(), { container: behaelter, hydrate: true });
    expect(fehler).not.toHaveBeenCalled();
    expect(variable("--kz-links")).toBe("400px");
    expect(wert(links())).toBe(400);
    behaelter.remove();
  });
});
