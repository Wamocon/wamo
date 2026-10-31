import type { MetadataRoute } from "next";

/**
 * Web-App-Manifest: macht die Oberfläche als App installierbar (iPhone: Teilen, Zum Home-Bildschirm; Android und
 * Desktop: Installieren). Es wird ohne Anmeldung ausgeliefert und enthält nur Namen, Farben und Symbole.
 * Symbole: scripts/wamo_icons_erzeugen.py (WMC-Wortmarke in WMC-Rot auf Weiß). Weiß als Hintergrund und Leistenfarbe
 * passt zur hellen Kopfzeile; im dunklen System übernimmt viewport.themeColor in layout.tsx die Leistenfarbe.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "WAMO",
    short_name: "WAMO",
    description: "Sprachassistent der WAMOCON GmbH",
    lang: "de",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#ffffff",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
