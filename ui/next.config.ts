import type { NextConfig } from "next";
import { version } from "./package.json";

const dev = process.env.NODE_ENV === "development";

/** Herkunft (origin) einer Adresse aus der Umgebung, leer bei fehlender oder ungültiger Angabe. */
function herkunft(url: string | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

// Erlaubte Gegenstellen: eigene Seite, Anmeldung (Supabase) und Voice-Core (Start, Offer, ICE).
const connect = [
  "'self'",
  herkunft(process.env.NEXT_PUBLIC_SUPABASE_URL),
  herkunft(process.env.NEXT_PUBLIC_VOICE_CORE_URL ?? "http://localhost:7860"),
].filter(Boolean);

// Next.js setzt Inline-Skripte (Hydration) und Inline-Styles (next/font, React-Styles, Mermaid-SVG),
// deshalb 'unsafe-inline'. 'unsafe-eval' nur in der Entwicklung (React Refresh).
const csp = [
  "default-src 'self'",
  // blob: für das Audio-Worklet des Mikrofons (WavMediaManager erzeugt es selbst als Blob, kein fremder Code)
  `script-src 'self' 'unsafe-inline' blob:${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src ${connect.join(" ")}`,
  "media-src 'self' blob:",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Version für die Fußzeile der Anmeldeseite (zur Build-Zeit eingesetzt)
  env: { WAMO_VERSION: version },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          {
            key: "Permissions-Policy",
            value: "microphone=(self), camera=(), geolocation=(), screen-wake-lock=(self), payment=(), usb=()",
          },
        ],
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
