"use client";

import { useEffect, useSyncExternalStore } from "react";
import { getSupabase } from "@/lib/supabase";

/**
 * Wer ist an diesem Gerät angemeldet (Kennung und E-Mail aus der Supabase-Sitzung)? Der stille Start beim Tippen braucht beides: die
 * Kennung für den Schalter je Person (lib/einstellungen.ts), die E-Mail für die Sperre der Laborkonten (lib/stillStart.ts). Der Wert liegt
 * in einem Modulspeicher, damit auch Funktionen außerhalb von React ihn lesen können (useWamo, stillStarten). Es wird nichts gespeichert.
 */

export type Nutzer = { id: string | null; email: string | null };

const KEINER: Nutzer = Object.freeze({ id: null, email: null });
let aktuell: Nutzer = KEINER;
const hoerer = new Set<() => void>();

export function nutzerLesen(): Nutzer {
  return aktuell;
}

/** Setzt den Nutzer (Anmeldung, Abmeldung, Tests). Gleiche Werte melden nichts. */
export function nutzerSetzen(n: Nutzer): void {
  if (n.id === aktuell.id && n.email === aktuell.email) return;
  aktuell = n.id === null && n.email === null ? KEINER : { id: n.id, email: n.email };
  hoerer.forEach((h) => h());
}

function abonnieren(h: () => void) {
  hoerer.add(h);
  return () => {
    hoerer.delete(h);
  };
}

/** Hält den Nutzer aus der Supabase-Sitzung aktuell (ohne die aufrufende Komponente bei einem Wechsel neu zu rendern). */
export function useNutzerHalten(): void {
  useEffect(() => {
    const supabase = getSupabase();
    if (!supabase) return;
    let aktiv = true;
    const uebernehmen = (user: { id?: string; email?: string } | null | undefined) =>
      nutzerSetzen({ id: user?.id ?? null, email: user?.email ?? null });
    void supabase.auth
      .getSession()
      .then(({ data }) => {
        if (aktiv) uebernehmen(data.session?.user);
      })
      .catch(() => undefined);
    const { data } = supabase.auth.onAuthStateChange((_ereignis, session) => uebernehmen(session?.user));
    return () => {
      aktiv = false;
      data.subscription.unsubscribe();
    };
  }, []);
}

/** Nutzer für die Oberfläche (rendert bei einem Wechsel neu). */
export function useNutzer(): Nutzer {
  useNutzerHalten();
  return useSyncExternalStore(abonnieren, nutzerLesen, () => KEINER);
}
