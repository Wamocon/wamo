import { createStore } from "@/lib/store";
import type { GeraeteStand } from "@/lib/einstellungenAbschnitte";

/**
 * Stand der Geräteeinrichtung (components/GeraetEinrichtung.tsx schreibt, die Einstellungsseite liest). Ein Store statt
 * zwei Kopien des Zustands: die Komponente arbeitet unsichtbar im Hintergrund, die Seite zeigt nur, was sie meldet.
 * Enthält nie Schlüssel, nur Zustand, Kennung des Geräts (pc-name) und den Zustand des Tunnels.
 */
export const GERAET_STILL: GeraeteStand = { zustand: "nicht_noetig", tunnel: null, kennung: null };

export const geraeteAnzeige = createStore<GeraeteStand>(GERAET_STILL);
