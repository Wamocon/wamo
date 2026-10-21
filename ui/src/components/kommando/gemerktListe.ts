import type { Memo } from "@/lib/wamo";

/**
 * Reine Logik der Gedächtnis-Karte (links): die Einträge, die in dieser Sitzung als "Gemerkt" erschienen
 * sind. Der Store hält sie nur 8 Sekunden; die Karte bewahrt sie länger. Ohne React und ohne Browser,
 * damit sie mit node:test geprüft werden kann (gemerktListe.test.ts).
 */

export const GEMERKT_MAX = 8;

const gleich = (a: Memo, b: Memo) => a.text === b.text && a.status === b.status && a.bereich === b.bereich;

/**
 * Liste mit den aktuellen Store-Einträgen abgleichen: gleiche Kennung (key) wird aktualisiert (z. B.
 * vorgeschlagen zu aktiv), neue Einträge kommen nach vorn, derselbe Text im selben Bereich steht nur einmal.
 * Gibt dieselbe Liste zurück, wenn sich nichts ändert (kein unnötiges Neurendern).
 */
export function gemerktAbgleichen(alt: readonly Memo[], aktuell: readonly Memo[]): readonly Memo[] {
  let geaendert = false;
  const behalten = alt.map((a) => {
    const neu = aktuell.find((m) => m.key === a.key);
    if (neu && !gleich(neu, a)) {
      geaendert = true;
      return neu;
    }
    return a;
  });
  const dazu = aktuell.filter(
    (m) => !behalten.some((a) => a.key === m.key || (a.bereich === m.bereich && a.text === m.text)),
  );
  if (!dazu.length && !geaendert) return alt;
  return [...dazu, ...behalten].slice(0, GEMERKT_MAX);
}

/** Eintrag nach "Rückgängig" aus der Liste nehmen. */
export function gemerktEntfernen(liste: readonly Memo[], key: number): readonly Memo[] {
  return liste.some((m) => m.key === key) ? liste.filter((m) => m.key !== key) : liste;
}
