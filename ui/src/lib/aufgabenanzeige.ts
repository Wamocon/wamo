import type { Activity, ActivityStatus } from "./nachrichten.ts";

/**
 * Aufgabenanzeige: alle Werkzeugschritte, die WAMO für eine Bitte ausführt, als eine Aufgabe mit Beginn, Dauer und Fortschritt.
 * Eine neue Aufgabe beginnt mit dem ersten Schritt eines neuen Gesprächszugs (die Person hat etwas gesagt oder getippt).
 * Läuft da noch ein Schritt des alten Auftrags, gehört der neue Schritt noch dazu.
 * Zeiten: dieselbe Uhr wie die Aktivitäten im Store (performance.now), in Millisekunden.
 */
export type Aufgabe = {
  /** Beginn des ersten Schritts */
  start: number;
  /** Gesprächszug, zu dem die Schritte gehören (-1: noch keine Aufgabe) */
  zug: number;
  schritte: Activity[];
};

export const AUFGABE_LEER: Aufgabe = { start: 0, zug: -1, schritte: [] };

const OFFEN: readonly ActivityStatus[] = ["laeuft", "wartet"];

export function istOffen(schritt: Activity): boolean {
  return OFFEN.includes(schritt.status);
}

/** Nächster Stand nach einer Aktivitätsmeldung (gleiche Kennung = Statuswechsel eines Schritts). */
export function naechsteAufgabe(
  m: Aufgabe,
  a: Activity,
  jetzt: number,
): Aufgabe {
  const i = m.schritte.findIndex((s) => s.id === a.id);
  if (i >= 0) {
    const schritte = [...m.schritte];
    schritte[i] = {
      ...schritte[i],
      ...a,
      startedAt: schritte[i].startedAt,
      endedAt: jetzt,
    };
    return { ...m, schritte };
  }
  const schritt: Activity = { ...a, startedAt: jetzt, endedAt: jetzt };
  const zug = a.turn ?? m.zug;
  if (zug !== m.zug && !m.schritte.some(istOffen))
    return { start: jetzt, zug, schritte: [schritt] };
  return {
    start: m.schritte.length ? m.start : jetzt,
    zug,
    schritte: [...m.schritte, schritt],
  };
}

/** Aufgabe aus einer fertigen Liste von Aktivitäten (neueste zuerst, wie im Store), zum Beispiel für die Vorschau. */
export function aufgabeAus(liste: readonly Activity[]): Aufgabe {
  let m = AUFGABE_LEER;
  for (const a of [...liste].reverse()) {
    m = naechsteAufgabe(m, a, a.startedAt ?? a.endedAt ?? 0);
    if (a.endedAt !== undefined) {
      m = {
        ...m,
        schritte: m.schritte.map((s) =>
          s.id === a.id ? { ...s, endedAt: a.endedAt } : s,
        ),
      };
    }
  }
  return m;
}

export type AufgabeZustand = "laeuft" | "wartet" | "fehler" | "fertig";

/** Gesamtzustand: wartet eine Freigabe, läuft etwas, ging etwas schief oder ist alles erledigt? */
export function aufgabeZustand(m: Aufgabe): AufgabeZustand {
  if (m.schritte.some((s) => s.status === "wartet")) return "wartet";
  if (m.schritte.some((s) => s.status === "laeuft")) return "laeuft";
  if (m.schritte.some((s) => s.status === "fehler" || s.status === "abgelehnt"))
    return "fehler";
  return "fertig";
}

/** Dauer bis jetzt (läuft noch) oder bis zum letzten Statuswechsel (abgeschlossen), in Millisekunden. */
export function aufgabeDauer(m: Aufgabe, jetzt: number): number {
  if (!m.schritte.length) return 0;
  const ende = m.schritte.some(istOffen)
    ? jetzt
    : Math.max(...m.schritte.map((s) => s.endedAt ?? m.start));
  return Math.max(0, ende - m.start);
}
