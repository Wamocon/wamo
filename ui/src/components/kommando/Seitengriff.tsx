"use client";

import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { tastenBreite, ziehBreite } from "./seitenbreite";
import type { GriffEigenschaften } from "./useSeitenbreiten";

const NAME = { links: "Breite der linken Seitenleiste", rechts: "Breite der rechten Seitenleiste" } as const;

/** Ein laufendes Ziehen: welcher Zeiger, wo er begann, die Breite dazu und die zuletzt gezeigte. */
type Ziehen = { zeiger: number; startX: number; start: number; letzte: number };

/**
 * Zeiger und Tastatur des Griffs. Zeiger: Die Breite folgt dem Weg des Zeigers (Vorschau ohne Speichern), beim Loslassen oder
 * Abbrechen gilt sie endgültig; hat sich nichts geändert, wird nichts gespeichert. Tastatur: Pfeile, Pos1 und Ende; Alt, Strg und
 * Befehlstaste mit Pfeil bleiben dem Browser (zum Beispiel Alt und Pfeil links für "Zurück").
 */
function useGriffBedienung({ seite, wert, grenzen, onAendern, onZiehen }: GriffEigenschaften) {
  const ziehen = useRef<Ziehen | null>(null);

  const beginnen = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || ziehen.current) return;
    ziehen.current = { zeiger: e.pointerId, startX: e.clientX, start: wert, letzte: wert };
    try {
      e.currentTarget.setPointerCapture(e.pointerId); // der Griff behält den Zeiger, auch über Canvas und Tafeln hinweg
    } catch {
      // ohne Capture (ältere Umgebung, Zeiger schon weg) zieht der Griff nur, solange der Zeiger über ihm bleibt
    }
    onZiehen(true);
  };

  const bewegen = (e: PointerEvent<HTMLDivElement>) => {
    const z = ziehen.current;
    if (!z || e.pointerId !== z.zeiger) return;
    z.letzte = ziehBreite(seite, z.start, e.clientX - z.startX, grenzen);
    onAendern(z.letzte, false);
  };

  const beenden = (e: PointerEvent<HTMLDivElement>) => {
    const z = ziehen.current;
    if (!z || e.pointerId !== z.zeiger) return;
    ziehen.current = null;
    onZiehen(false);
    if (z.letzte !== z.start) onAendern(z.letzte, true);
  };

  const taste = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const neu = tastenBreite(seite, e.key, e.shiftKey, wert, grenzen);
    if (neu === null) return;
    e.preventDefault(); // Pfeile sowie Pos1 und Ende sollen nicht die Seite scrollen
    if (neu !== wert) onAendern(neu, true);
  };

  return {
    onPointerDown: beginnen,
    onPointerMove: bewegen,
    onPointerUp: beenden,
    onPointerCancel: beenden,
    onLostPointerCapture: beenden,
    onKeyDown: taste,
  };
}

/**
 * Trenner zwischen zwei Spalten, mit dem sich die Breite der Seitenleiste ändern lässt (WAI-ARIA-Muster "Window Splitter"):
 * Maus, Stift und Finger über Pointer Events mit Pointer Capture, Pfeil links und rechts um 16 px (mit Umschalt 48 px), Pos1 und
 * Ende auf Mindest- und Höchstbreite, Doppelklick stellt den Standard her. Aussehen, Platz und Sichtbarkeit regelt seitengriff.css.
 */
export function Seitengriff(eigenschaften: GriffEigenschaften) {
  const { seite, wert, grenzen, onStandard } = eigenschaften;
  const bedienung = useGriffBedienung(eigenschaften);
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={NAME[seite]}
      aria-valuenow={wert}
      aria-valuemin={grenzen.min}
      aria-valuemax={grenzen.max}
      aria-valuetext={`${wert} Pixel`}
      tabIndex={0}
      className="kz-griff"
      data-seite={seite}
      onDoubleClick={onStandard}
      {...bedienung}
    />
  );
}
