"use client";

import { memo } from "react";
import { Orb } from "@/components/Orb";
import type { LevelSample } from "@/lib/wamo";
import { KernRinge } from "./KernRinge";

/**
 * Der Präsenzkern der Kommandozentrale: die Lichtkugel (Orb.tsx, WebGL) in HUD-Ringen (KernRinge.tsx, SVG).
 * Zustände: ruhig (idle), hört zu, denkt (Lichtbogen läuft um), spricht (Pegelbogen und Glut mit dem Pegel),
 * getrennt (grau). Dazu: Werkzeug läuft (Strichring und Umlaufbahnen), Freigabe wartet (roter Ring), Unterbrechung.
 * Der Kern ist keine Schaltfläche: Gespräch starten und beenden, Mikrofon und Eingabe stehen darunter.
 *
 * Fehlt WebGL (data-gpu="aus" am Canvas), zeigt kernRinge.css eine Kugel aus einem Farbverlauf, die Ringe arbeiten weiter.
 * Die Schleifen der beiden Teile (Kugel und Pegelbogen) lesen die Pegel pro Bild aus levels und die Zustände aus dem Store,
 * dazu gibt es keinen React-Render im Worttakt. Farben nur aus den CSS-Variablen, auch beim Wechsel hell, dunkel und Nacht.
 * dichte: 1 normal, größer in der Zentrale (eine dritte Umlaufbahn).
 */
export const PraesenzKern = memo(function PraesenzKern({
  levels,
  dichte = 1,
}: {
  levels: { current: LevelSample };
  dichte?: number;
}) {
  return (
    <div className="kr" aria-hidden="true">
      <Orb levels={levels} />
      <span className="kr-kugel" />
      <KernRinge levels={levels} dichte={dichte} />
    </div>
  );
});
