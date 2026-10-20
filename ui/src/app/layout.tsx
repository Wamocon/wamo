import type { Metadata } from "next";
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
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="de" className={condensed.variable}>
      <body>{children}</body>
    </html>
  );
}
