"use client";

import { useSyncExternalStore } from "react";
import { themaAbonnieren, themaWirksam, type Thema } from "@/lib/thema";

/** Das gerade wirksame Thema (aus data-theme, also auch die erzwungene Nacht). Vor dem Laden im Browser: dunkel. */
export function useThema(): Thema {
  return useSyncExternalStore(themaAbonnieren, themaWirksam, () => "dunkel");
}
