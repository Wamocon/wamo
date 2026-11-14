/**
 * Kurzform eines Modellnamens für die Kopfzeile (Modul "Modelle"). Die Herstellerangabe "Claude" entfällt (WAMO ist
 * das Produkt, die Familie und die Version tragen die Information), dann bleiben ganze Wörter, bis die Grenze erreicht
 * ist. Ein Schnitt mitten im Wort kommt nie vor: Passt schon das erste Wort nicht, bleibt es ganz. Den vollen Namen
 * zeigt das Menü und der title des Moduls. Reine Funktion, ohne React (modellKurz.test.ts).
 */
export function modellKurzname(name: string | null | undefined, max = 14): string {
  const text = (name ?? "").trim().replace(/^claude\s+/i, "");
  if (!text) return "";
  if (text.length <= max) return text;
  // Teile abwechselnd Wort und Trennzeichen (Leerzeichen, Bindestrich, Unterstrich, Schrägstrich), die Trennzeichen bleiben erhalten
  const teile = text.split(/([\s_/-]+)/);
  let kurz = teile[0];
  for (let i = 1; i + 1 < teile.length; i += 2) {
    const weiter = kurz + teile[i] + teile[i + 1];
    if (weiter.length > max) break;
    kurz = weiter;
  }
  return kurz;
}
