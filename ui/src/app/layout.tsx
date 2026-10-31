import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Roboto_Condensed } from "next/font/google";
import "./globals.css";

// WMC-Schrift ist Arial Narrow (lokal installiert). Roboto Condensed nur als Webfont-Ersatz,
// falls Arial Narrow und Liberation Sans Narrow fehlen: kein Vorladen, der Browser holt sie nur bei Bedarf.
const condensed = Roboto_Condensed({
  variable: "--font-condensed",
  subsets: ["latin"],
  weight: ["400", "700"],
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  title: "WAMO",
  description: "Sprachassistent der WAMOCON GmbH",
  applicationName: "WAMO",
  // iPhone: Zum Home-Bildschirm startet die Oberfläche als App ohne Browserleiste. Das Symbol (apple-icon.png) und das
  // Manifest (manifest.ts) erzeugen Next.js aus den Dateien im Ordner app. "default": die Statusleiste behält ihre
  // Systemfarbe (hell oder dunkel), die Kopfzeile reicht mit env(safe-area-inset-top) darunter (globals.css).
  appleWebApp: { capable: true, title: "WAMO", statusBarStyle: "default" },
  formatDetection: { telephone: false },
};

// viewport-fit=cover: die Seite nutzt den ganzen Bildschirm, die Layouts weichen der Notch und dem Home-Balken
// mit den Abständen env(safe-area-inset-*) aus (globals.css). Das Zoomen bleibt erlaubt (Barrierefreiheit).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // Farben der Kopfzeile (--bg in globals.css), hell und dunkel
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0e0e0e" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="de" className={condensed.variable}>
      <body>{children}</body>
    </html>
  );
}
