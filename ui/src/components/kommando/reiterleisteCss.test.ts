// Test der Reiterleisten und Tafeln im Rahmen (kommando/Rahmen.tsx) über das CSS: Die Reiterleiste bleibt in jeder Breite und auf
// jedem Reiter sichtbar und bedienbar, und am Handy zeigt jeder Reiter nur seine eigene Tafel.
// Fehlerbild vorher: Ab 1100 px scrollt die rechte Reiterleiste seitlich (seitengriff.css) und ist damit ein Scrollbereich. Als
// Flex-Element der rechten Spalte hatte sie so keine Mindesthöhe mehr und schrumpfte auf 2 px, sobald eine Tafel höher als die
// Spalte war (Büro-Karte mit Liste): Die Reiter waren weg, es gab keinen Weg zurück. Am Handy blieb der Kern mit laufender Aufgabe
// auf jedem Reiter stehen (aufgabenanzeige.css) und schob die gewählte Tafel unter den Rand.
// Der Test rechnet für eine Eigenschaft die Kaskade nach (Gewicht des Selektors, bei Gleichstand die spätere Regel), mit allen
// Stylesheets in der Reihenfolge aus globals.css. Es zählen nur Breitenbedingungen der @media-Blöcke; Blöcke mit anderen Bedingungen
// (Höhe, Zeiger, Bewegung) gelten hier als nicht zutreffend. Zustände wie :hover oder :has gelten ebenfalls als nicht zutreffend.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

type Regel = { media: string | null; selektoren: string[]; inhalt: string; rang: number };
type Element = { klassen: string[]; vorfahren: string[]; zustand: Record<string, string> };

const ohneKommentare = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "");
const lesen = (url: URL) => ohneKommentare(readFileSync(url, "utf8"));
const GLOBALS = new URL("../../app/globals.css", import.meta.url);

/** Regeln einer Datei samt @media-Bedingung; rang hält die Reihenfolge über alle Dateien. */
function regelnAus(text: string, start: number): Regel[] {
  const aus: Regel[] = [];
  const flach = (stueck: string, media: string | null) => {
    for (const m of stueck.matchAll(/([^{}@;]+)\{([^{}]*)\}/g)) {
      aus.push({ media, selektoren: m[1].split(",").map((s) => s.trim()), inhalt: m[2], rang: start + aus.length });
    }
  };
  let pos = 0;
  while (pos < text.length) {
    const at = text.indexOf("@media", pos);
    if (at < 0) {
      flach(text.slice(pos), null);
      break;
    }
    flach(text.slice(pos, at), null);
    const auf = text.indexOf("{", at);
    let tiefe = 1;
    let j = auf + 1;
    while (tiefe > 0 && j < text.length) {
      if (text[j] === "{") tiefe++;
      else if (text[j] === "}") tiefe--;
      j++;
    }
    flach(text.slice(auf + 1, j - 1), text.slice(at + "@media".length, auf).trim());
    pos = j;
  }
  return aus;
}

/** Alle Regeln in der Reihenfolge der Kaskade: zuerst die Dateien aus @import, danach globals.css selbst. */
const ALLE: Regel[] = (() => {
  const globals = lesen(GLOBALS);
  const texte = [...globals.matchAll(/@import\s+"(\.\.\/[^"]+\.css)"/g)].map((m) => lesen(new URL(m[1], GLOBALS)));
  texte.push(globals);
  return texte.flatMap((text, i) => regelnAus(text, i * 1_000_000));
})();

/** Gilt der @media-Block bei dieser Breite? Nur min-width und max-width zählen, jede andere Bedingung gilt als nicht zutreffend. */
function giltBei(media: string | null, breite: number): boolean {
  if (media === null) return true;
  const rest = media.replace(/\((min|max)-width:\s*(\d+)px\)/g, "").replace(/\band\b|\bscreen\b|\ball\b|\s/g, "");
  if (rest !== "") return false;
  return [...media.matchAll(/\((min|max)-width:\s*(\d+)px\)/g)].every((m) => (m[1] === "min" ? breite >= +m[2] : breite <= +m[2]));
}

/** Gewicht eines Selektors (Kennungen zählen 100, Klassen, Attribute und Pseudoklassen je 1; :not zählt nur mit seinem Inhalt). */
function gewicht(sel: string): number {
  const s = sel.replace(/::[\w-]+/g, "").replace(/:(not|is|has)\(/g, " ");
  return (s.match(/#[\w-]/g) ?? []).length * 100 + (s.match(/\.[\w-]|\[|:[\w-]/g) ?? []).length;
}

const attribute = (teil: string) => [...teil.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)].map((m) => ({ name: m[1], wert: m[2] }));

/** Trifft der Selektor das Element? Klassen der Vorfahren und Attribute (am Element oder an Vorfahren) werden geprüft. */
function trifft(sel: string, el: Element): boolean {
  const verneint = [...sel.matchAll(/:not\(([^)]*)\)/g)].map((m) => m[1]);
  const positiv = sel.replace(/:not\([^)]*\)/g, "");
  if (/:(?!root\b)[\w-]/.test(positiv.replace(/::[\w-]+/g, ""))) return false; // :hover, :focus, :has und Co. gelten hier nicht
  if (/::/.test(positiv)) return false; // Pseudoelemente sind nicht das Element
  const teile = positiv.split(/\s*[>+~]\s*|\s+/).filter(Boolean);
  const letztes = teile.pop() ?? "";
  const klassen = (teil: string) => [...teil.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
  if (klassen(letztes).length === 0 || !klassen(letztes).every((k) => el.klassen.includes(k))) return false;
  if (!teile.every((t) => klassen(t).every((k) => el.vorfahren.includes(k)))) return false;
  const gilt = (a: { name: string; wert?: string }) => a.name in el.zustand && (a.wert === undefined || el.zustand[a.name] === a.wert);
  if (!attribute(positiv).every(gilt)) return false;
  return verneint.every((n) => attribute(n).length === 0 || !attribute(n).every(gilt));
}

/** Wert einer Eigenschaft nach der Kaskade (null: keine Regel setzt sie). Mehrere Namen: Kurzform und Langform, die spätere gewinnt. */
function wert(namen: string[], el: Element, breite: number): { wert: string; selektor: string } | null {
  let sieger: { wert: string; selektor: string; gewicht: number; rang: number } | null = null;
  for (const r of ALLE) {
    if (!giltBei(r.media, breite)) continue;
    for (const name of namen) {
      const m = r.inhalt.match(new RegExp(`(?:^|;)\\s*${name}\\s*:\\s*([^;!]+)`));
      if (!m) continue;
      for (const selektor of r.selektoren.filter((s) => trifft(s, el))) {
        const g = gewicht(selektor);
        if (!sieger || g > sieger.gewicht || (g === sieger.gewicht && r.rang >= sieger.rang)) {
          sieger = { wert: `${name}: ${m[1].trim()}`, selektor, gewicht: g, rang: r.rang };
        }
      }
    }
  }
  return sieger && { wert: sieger.wert, selektor: sieger.selektor };
}

/** flex-shrink aus "flex-shrink: 0", "flex: none", "flex: 0 0 auto" oder "flex: 1"; ohne Regel gilt der Standard 1. */
function schrumpf(w: { wert: string } | null): number {
  if (!w) return 1;
  const [name, roh] = w.wert.split(/:\s*/);
  if (name === "flex-shrink") return Number(roh);
  if (roh === "none") return 0;
  if (roh === "auto" || roh === "initial") return 1;
  const teile = roh.split(/\s+/);
  return teile.length >= 2 && /^\d/.test(teile[1]) ? Number(teile[1]) : 1;
}

const SEITE: Element = {
  klassen: ["kz-reiterleiste", "kz-reiter-seite"],
  vorfahren: ["shell", "kz", "kz-main", "kz-rechts"],
  zustand: { role: "tablist", "data-ansicht": "normal", "data-rechts": "buero", "data-mobil": "kern" },
};
const MOBIL: Element = {
  klassen: ["kz-reiterleiste", "kz-reiter-mobil"],
  vorfahren: ["shell", "kz", "kz-main"],
  zustand: { role: "tablist", "data-ansicht": "normal", "data-rechts": "auftraege", "data-mobil": "buero" },
};
const kern = (mobil: string): Element => ({
  klassen: ["kz-kernbereich"],
  vorfahren: ["shell", "kz", "kz-main", "kz-mitte"],
  zustand: { "data-ansicht": "normal", "data-rechts": "auftraege", "data-mobil": mobil, "data-aufgabe": "true", "data-p": "kern" },
});

for (const breite of [1100, 1200, 1280, 1700]) {
  test(`rechte Reiterleiste bei ${breite} px schrumpft nie, auch wenn die Tafel höher als die Spalte ist`, () => {
    const w = wert(["flex", "flex-shrink"], SEITE, breite);
    assert.equal(schrumpf(w), 0, `flex-shrink der Reiterleiste ist nicht 0 (${w ? `${w.selektor} { ${w.wert} }` : "keine Regel"})`);
  });

  test(`rechte Reiterleiste bei ${breite} px bleibt beim Scrollen der Spalte oben stehen, auf eigenem Grund`, () => {
    assert.equal(wert(["position"], SEITE, breite)?.wert, "position: sticky");
    assert.ok(wert(["top"], SEITE, breite), "sticky braucht top");
    const grund = wert(["background", "background-color"], SEITE, breite);
    assert.ok(grund && !/transparent|none/.test(grund.wert), "ohne eigenen Grund scheint die Tafel durch die Reiter");
  });

  test(`rechte Spalte bei ${breite} px hält beim Fokussieren Abstand zur stehenden Reiterleiste`, () => {
    const spalte: Element = { klassen: ["kz-rechts"], vorfahren: ["shell", "kz", "kz-main"], zustand: { "data-ansicht": "normal" } };
    const w = wert(["scroll-padding-top", "scroll-padding-block-start"], spalte, breite);
    assert.ok(w && /\d/.test(w.wert), "scroll-padding-top fehlt: fokussierte Elemente lägen unter der Reiterleiste");
  });
}

for (const breite of [390, 600, 984, 1099]) {
  test(`Reiterleiste am Handy und Tablet (${breite} px) bleibt oben stehen und schrumpft nicht`, () => {
    assert.equal(wert(["position"], MOBIL, breite)?.wert, "position: sticky");
    assert.equal(wert(["display"], MOBIL, breite)?.wert, "display: flex");
    assert.equal(schrumpf(wert(["flex", "flex-shrink"], MOBIL, breite)), 0);
  });

  for (const reiter of ["buero", "auftraege", "vorgaenge", "canvas", "hinweise"]) {
    test(`am Handy (${breite} px) zeigt der Reiter ${reiter} den Kern nicht, auch nicht während einer Aufgabe`, () => {
      const w = wert(["display"], kern(reiter), breite);
      assert.equal(w?.wert, "display: none", `es gewinnt ${w ? `${w.selektor} { ${w.wert} }` : "keine Regel"}`);
    });
  }

  test(`am Handy (${breite} px) zeigt der Reiter Kern den Kernbereich mit laufender Aufgabe`, () => {
    const w = wert(["display"], kern("kern"), breite);
    assert.ok(w && w.wert !== "display: none", `es gewinnt ${w ? `${w.selektor} { ${w.wert} }` : "keine Regel"}`);
  });
}

test("ab 1100 px steht die rechte Reiterleiste und ihre Tafel für Büro ist sichtbar", () => {
  const tafel: Element = { klassen: ["kz-tafel"], vorfahren: SEITE.vorfahren, zustand: { ...SEITE.zustand, "data-p": "buero" } };
  assert.equal(wert(["display"], tafel, 1280)?.wert, "display: flex");
  assert.notEqual(wert(["display"], SEITE, 1280)?.wert, "display: none");
});
