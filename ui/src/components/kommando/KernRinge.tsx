"use client";

import { memo, useEffect, useRef } from "react";
import { useSelect } from "@/lib/store";
import { bogenStrich, BOGEN_RADIUS, glaetten, KANTE, MITTE, pegelAuswahl, skalaPfad } from "@/lib/kernRinge";
import { istOnline, laeuftWerkzeug, wamo, wartetAufEntscheidung, type LevelSample } from "@/lib/wamo";

/**
 * Die HUD-Ringe um den Kern (SVG über der Lichtkugel, Gestaltung in kernRinge.css):
 * - Skalenring außen (120 Striche, dreht langsam), vier Ringsegmente (dreht gegenläufig), Punktering und Fassung um den Kern
 * - Pegelbogen: wächst mit der Stimme (WAMO rot, Mensch hell, weitere Stimmen grau)
 * - denkt nach: ein kurzer Lichtbogen läuft um
 * - handelt: ein roter Strichring und zwei Umlaufbahnen (in der Zentrale drei)
 * - Freigabe wartet: der Strichring pulsiert, ohne zu drehen
 * Farben stehen nur im CSS (Variablen), deshalb folgen die Ringe dem Wechsel hell und dunkel von selbst.
 * Der Pegel kommt pro Bild aus einem Ref, nur der Bogen schreibt ins DOM (kein React-Render). Die Schleife ruht, solange der
 * Bogen leer ist und kein Ton kommt (auch mit laufender Sitzung; ein leichter Takt weckt sie bei Ton), mit verborgener Seite und
 * solange die Ringe nicht zu sehen sind. prefers-reduced-motion: der Bogen springt in drei Stufen, höchstens 12 Bilder je Sekunde
 * (die Drehungen schaltet das CSS ab).
 */

const SKALA = skalaPfad(); // fest, einmal beim Laden
/** Darunter gilt der Bogen als leer und der Pegel als still */
const RUHE = 0.002;
/** So oft prüft der Pegelwecker, ob Ton kommt, solange die Schleife ruht */
const PEGEL_TAKT_MS = 100;

export const KernRinge = memo(function KernRinge({
  levels,
  dichte = 1,
}: {
  levels: { current: LevelSample };
  dichte?: number;
}) {
  const presence = useSelect(wamo, (s) => s.presence);
  const handelt = useSelect(wamo, (s) => laeuftWerkzeug(s) && istOnline(s));
  const freigabe = useSelect(wamo, wartetAufEntscheidung);
  const bogenRef = useRef<SVGCircleElement>(null);

  useEffect(() => {
    const bogen = bogenRef.current;
    if (!bogen) return;
    const reduziert = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let gezeigt = 0;
    let strich = "";
    let wer = "";
    let letzte = -1e9;
    let sichtbar = true; // die Ringe sind im Bild (IntersectionObserver, wo vorhanden)
    const ruht = () => document.hidden || !sichtbar;
    const tonDa = () => pegelAuswahl(levels.current, false).ziel >= RUHE;

    const bild = (jetzt: number) => {
      raf = 0;
      if (ruht()) return; // verborgen oder nicht zu sehen: nichts zeichnen, visibilitychange und der Beobachter wecken
      const red = reduziert.matches;
      if (red && jetzt - letzte < 1000 / 12) {
        raf = requestAnimationFrame(bild);
        return;
      }
      letzte = jetzt;
      const { ziel, wer: neu } = pegelAuswahl(levels.current, red);
      gezeigt = red ? ziel : glaetten(gezeigt, ziel);
      if (gezeigt < RUHE) gezeigt = 0;
      const s = bogenStrich(gezeigt);
      if (s !== strich) {
        strich = s;
        bogen.style.strokeDasharray = s;
      }
      if (neu !== wer) {
        wer = neu;
        bogen.dataset.wer = neu;
      }
      // weiter nur, solange der Bogen etwas zeigt oder Ton kommt; sonst ruht die Schleife, auch mit laufender Sitzung
      if (gezeigt > 0 || ziel >= RUHE) raf = requestAnimationFrame(bild);
    };
    const wecke = () => {
      if (!raf && !ruht()) raf = requestAnimationFrame(bild);
    };

    wecke();
    const abmelden = wamo.subscribe(wecke);
    document.addEventListener("visibilitychange", wecke);
    reduziert.addEventListener("change", wecke);
    // Pegel ändern sich ohne Store: ein leichter Takt stößt die ruhende Schleife an, sobald Ton kommt
    const pegelWecker = setInterval(() => {
      if (!raf && tonDa()) wecke();
    }, PEGEL_TAKT_MS);
    const sicht =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver((eintraege) => {
            sichtbar = eintraege.some((e) => e.isIntersecting);
            wecke();
          });
    sicht?.observe(bogen);
    return () => {
      cancelAnimationFrame(raf);
      raf = 0;
      abmelden();
      clearInterval(pegelWecker);
      sicht?.disconnect();
      document.removeEventListener("visibilitychange", wecke);
      reduziert.removeEventListener("change", wecke);
    };
  }, [levels]);

  return (
    <svg
      className="kr-ringe"
      viewBox={`0 0 ${KANTE} ${KANTE}`}
      data-presence={presence}
      data-acting={handelt}
      data-freigabe={freigabe}
      aria-hidden="true"
      focusable="false"
    >
      <g className="kr-dreht-langsam">
        <path className="kr-skala" d={SKALA} />
      </g>
      <circle className="kr-befehl" cx={MITTE} cy={MITTE} r={184} />
      <g className="kr-dreht-gegen">
        <circle className="kr-segmente" cx={MITTE} cy={MITTE} r={BOGEN_RADIUS} />
      </g>
      <circle
        ref={bogenRef}
        className="kr-pegel"
        cx={MITTE}
        cy={MITTE}
        r={BOGEN_RADIUS}
        transform={`rotate(112 ${MITTE} ${MITTE})`}
      />
      <circle className="kr-punkte" cx={MITTE} cy={MITTE} r={150} />
      <g className="kr-bahnen">
        <ellipse cx={MITTE} cy={MITTE} rx={156} ry={58} transform={`rotate(-28 ${MITTE} ${MITTE})`} />
        <ellipse cx={MITTE} cy={MITTE} rx={156} ry={58} transform={`rotate(32 ${MITTE} ${MITTE})`} />
        {dichte > 1 && <ellipse cx={MITTE} cy={MITTE} rx={156} ry={40} transform={`rotate(92 ${MITTE} ${MITTE})`} />}
      </g>
      <g className="kr-komet">
        <circle cx={MITTE} cy={MITTE} r={160} />
      </g>
      <circle className="kr-fassung" cx={MITTE} cy={MITTE} r={118} />
    </svg>
  );
});
