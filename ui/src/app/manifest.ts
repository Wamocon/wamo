import type { MetadataRoute } from "next";
import { LANGFORM } from "@/lib/anzeigename";

/**
 * Web-App-Manifest: macht die Oberfläche als App installierbar (iPhone: Teilen, Zum Home-Bildschirm; Android und
 * Desktop: Installieren). Es wird ohne Anmeldung ausgeliefert und enthält nur Namen, Farben und Symbole.
 * Symbole: scripts/wamo_icons_erzeugen.py (WMC-Wortmarke in WMC-Rot auf Weiß). Dunkel ist der Standard der Oberfläche, deshalb
 * startet die installierte App mit dem dunklen Grund (--bg in globals.css). Die Leistenfarbe im Betrieb stellt lib/thema.ts auf
 * die gewählte Seite Hell oder Dunkel ein (viewport.themeColor in layout.tsx).
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "W.A.I.S.",
    short_name: "W.A.I.S.",
    description: `${LANGFORM}, Sprachassistent der WAMOCON GmbH`,
    lang: "de",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#07070a",
    theme_color: "#07070a",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
