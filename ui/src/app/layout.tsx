import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import localFont from "next/font/local";
import { LANGFORM } from "@/lib/anzeigename";
import { INIT_SKRIPT, LEISTENFARBE } from "@/lib/thema";
import "./globals.css";

// Drei Schriften, je eine Aufgabe (globals.css): Unbounded für Marke, Überschriften und große Zahlen, JetBrains Mono für
// Beschriftungen, Zähler und Code, Atkinson Hyperlegible für Fließtext.
// Die Dateien liegen im Repo (app/fonts, Herkunft und Lizenz dort): Bau und Betrieb holen nichts bei Google.
const display = localFont({
  variable: "--font-display",
  src: [
    { path: "./fonts/unbounded-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "./fonts/unbounded-latin-700-normal.woff2", weight: "700", style: "normal" },
    { path: "./fonts/unbounded-latin-800-normal.woff2", weight: "800", style: "normal" },
  ],
  display: "swap",
});

const hud = localFont({
  variable: "--font-hud",
  src: [
    { path: "./fonts/jetbrains-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/jetbrains-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "./fonts/jetbrains-mono-latin-600-normal.woff2", weight: "600", style: "normal" },
    { path: "./fonts/jetbrains-mono-latin-700-normal.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
});

const body = localFont({
  variable: "--font-body",
  src: [
    { path: "./fonts/atkinson-hyperlegible-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/atkinson-hyperlegible-latin-700-normal.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
});

export const metadata: Metadata = {
  title: "W.A.I.S.",
  description: `${LANGFORM}, Sprachassistent der WAMOCON GmbH`,
  applicationName: "W.A.I.S.",
  // iPhone: Zum Home-Bildschirm startet die Oberfläche als App ohne Browserleiste. Das Symbol (apple-icon.png) und das
  // Manifest (manifest.ts) erzeugen Next.js aus den Dateien im Ordner app. "black-translucent": die Statusleiste liegt
  // über dem dunklen Hintergrund, die Kopfzeile reicht mit env(safe-area-inset-top) darunter (globals.css).
  appleWebApp: { capable: true, title: "W.A.I.S.", statusBarStyle: "black-translucent" },
  formatDetection: { telephone: false },
};

// viewport-fit=cover: die Seite nutzt den ganzen Bildschirm, die Layouts weichen der Notch und dem Home-Balken
// mit den Abständen env(safe-area-inset-*) aus (globals.css). Das Zoomen bleibt erlaubt (Barrierefreiheit).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // Dunkel ist der Standard, Hell ein Schalter je Gerät (lib/thema.ts). Die Farbe der Browserleiste gilt für den Standard,
  // das Skript im Kopf der Seite und lib/thema.ts stellen sie auf die gewählte Seite um. Beide Schemata sind vorgesehen,
  // die Wahl trifft das CSS (color-scheme in globals.css).
  themeColor: LEISTENFARBE.dunkel,
  colorScheme: "dark light",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    // suppressHydrationWarning: das Skript im Kopf setzt data-theme vor dem ersten Zeichnen, der Server kennt die Wahl nicht
    <html lang="de" className={`${display.variable} ${hud.variable} ${body.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: INIT_SKRIPT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
