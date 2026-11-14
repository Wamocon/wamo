"use client";

import { useEffect, useSyncExternalStore } from "react";
import { leisteAbgleichen, themaAbonnieren, themaWirksam, type Thema } from "@/lib/thema";

/**
 * Das gerade wirksame Thema (aus data-theme, also auch die erzwungene Nacht). Vor dem Laden im Browser: dunkel.
 * Beim Einhängen gleicht der Hook einmal die Farbe der Browserleiste ab, denn nach der Hydration kann ein zweites
 * meta theme-color mit der Farbe des Standards im Kopf stehen (lib/thema.ts).
 */
export function useThema(): Thema {
  useEffect(() => {
    leisteAbgleichen();
  }, []);
  return useSyncExternalStore(themaAbonnieren, themaWirksam, () => "dunkel");
}
