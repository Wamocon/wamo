"use client";

import { memo, useEffect, useRef } from "react";
import { cssFarbe } from "@/lib/stimmfarben";
import { istOnline, laeuftWerkzeug, wamo, wartetAufEntscheidung, type LevelSample, type Presence } from "@/lib/wamo";
import { zeichneKern, type Farbe, type KernFarben } from "./kernZeichnen";

/**
 * Der Präsenzkern der Kommandozentrale (Canvas 2D, Zeichnung in kernZeichnen.ts).
 * Zustände: ruhig (idle), hört zu, denkt (Bahnen schneller), spricht (Pulsieren mit dem Pegel),
 * getrennt (grau). Dazu: Werkzeug läuft (Lauf-Segment), Freigabe wartet (roter Ring), Unterbrechung.
 *
 * Leistung: Pegel kommen pro Bild aus einem Ref, Zustände direkt aus dem Store (kein React-Render).
 * Bildrate 60 bei hört zu und spricht, 30 sonst, getrennt ein Standbild, nichts bei verborgener Seite.
 * prefers-reduced-motion: keine Drehung und kein Zittern, der Pegel nur in drei Stufen, höchstens 12 Bilder je Sekunde.
 * Farben aus den CSS-Variablen (--voice-wamo, --kz-linie, --wmc-gray), auch beim Wechsel hell, dunkel und Nacht.
 */

const ZIEL: Record<Presence, { tempo: number; basis: number; denken: number }> = {
  offline: { tempo: 0.18, basis: 0.04, denken: 0 },
  connecting: { tempo: 1.5, basis: 0.24, denken: 0.5 },
  idle: { tempo: 0.7, basis: 0.17, denken: 0 },
  listening: { tempo: 1.0, basis: 0.21, denken: 0 },
  thinking: { tempo: 3.2, basis: 0.38, denken: 1 },
  speaking: { tempo: 1.4, basis: 0.28, denken: 0 },
};

const FPS: Record<Presence, number> = { offline: 0, connecting: 30, idle: 30, listening: 60, thinking: 45, speaking: 60 };

const nach255 = (c: [number, number, number, number]): Farbe => [c[0] * 255, c[1] * 255, c[2] * 255];

export const PraesenzKern = memo(function PraesenzKern({
  levels,
  dichte = 1,
}: {
  levels: { current: LevelSample };
  dichte?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dichteRef = useRef(dichte);
  const weckeRef = useRef<(() => void) | null>(null);
  // Die Dichte wechselt zwischen Normal und Zentrale; die Schleife läuft dabei weiter (kein Neustart, kein graues Aufblitzen)
  useEffect(() => {
    dichteRef.current = dichte;
    weckeRef.current?.(); // auch das Standbild (getrennt) neu zeichnen
  }, [dichte]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const st = { presence: "offline" as Presence, acting: false, approval: false };
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    const farben: KernFarben = { rot: [244, 14, 14], linie: [0, 0, 0], grau: [107, 107, 107] };
    const leseFarben = () => {
      const css = getComputedStyle(canvas);
      farben.rot = nach255(cssFarbe(css, "--voice-wamo"));
      farben.linie = nach255(cssFarbe(css, "--kz-linie"));
      farben.grau = nach255(cssFarbe(css, "--wmc-gray"));
    };
    leseFarben();

    const sm = { pegel: 0, tempo: ZIEL.offline.tempo, grau: 1, denken: 0, handeln: 0, freigabe: 0 };
    let t = 0; // Zeit in Sekunden, mit Tempo gewichtet
    let raf = 0;
    let letztes = 0;
    let ruhend = false;
    let groesse = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      groesse = canvas.clientWidth;
      canvas.width = Math.max(1, Math.round(groesse * dpr));
      canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
      ctx.setTransform(canvas.width / Math.max(1, groesse), 0, 0, canvas.height / Math.max(1, canvas.clientHeight), 0, 0);
      wecke();
    };

    const weich = (k: keyof typeof sm, ziel: number, rate: number, dt: number) => {
      sm[k] += (ziel - sm[k]) * Math.min(1, rate * dt);
    };

    const zeichne = (jetzt: number, dt: number) => {
      const p = st.presence;
      const z = ZIEL[p];
      const lv = levels.current;
      const bewegt = !reduced.matches;
      const roh = Math.min(1, Math.max(p === "listening" ? lv.user : p === "speaking" ? lv.bot : Math.max(lv.user, lv.bot), 0) * 3.2);
      const live = bewegt ? roh : Math.round(roh * 2) / 2; // drei Stufen statt Zittern
      const atmen = bewegt && p !== "offline" ? 0.05 * Math.sin(jetzt / 1100) : 0;
      weich("pegel", Math.max(z.basis + atmen, live), live > sm.pegel ? 16 : 4, dt);
      weich("tempo", z.tempo, 2.5, dt);
      weich("grau", p === "offline" ? 1 : 0, 3, dt);
      weich("denken", z.denken, 3, dt);
      weich("handeln", st.acting ? 1 : 0, 3, dt);
      weich("freigabe", st.approval ? 1 : 0, 3, dt);
      if (bewegt) t += dt * sm.tempo;
      const schnitt = jetzt - (lv.cutAt ?? -1e9);
      zeichneKern(
        ctx,
        groesse,
        {
          t,
          pegel: sm.pegel,
          unterbrechung: Math.max(0, 1 - schnitt / 250),
          grau: sm.grau,
          denken: sm.denken,
          handeln: sm.handeln,
          freigabe: sm.freigabe,
          freigabePuls: bewegt ? 0.5 + 0.5 * Math.sin(jetzt / 320) : 1,
          fremd: (lv.speaker ?? 0) > 0,
          dichte: dichteRef.current,
        },
        farben,
      );
    };

    const ruhig = () =>
      st.presence === "offline" &&
      !st.approval &&
      sm.grau > 0.995 &&
      sm.pegel < ZIEL.offline.basis + 0.005 &&
      sm.denken < 0.005 &&
      sm.handeln < 0.005 &&
      sm.freigabe < 0.005 &&
      Math.abs(sm.tempo - ZIEL.offline.tempo) < 0.01;

    const bild = (jetzt: number) => {
      raf = 0;
      if (document.hidden || !groesse) return; // verborgen: nichts zeichnen, visibilitychange weckt
      let fps = FPS[st.presence];
      if (st.acting || st.approval) fps = Math.max(fps, 30);
      if (jetzt - (levels.current.cutAt ?? -1e9) < 300) fps = 60;
      if (reduced.matches && fps > 0) fps = Math.min(fps, 12);
      if (fps === 0) {
        if (ruhig()) {
          // getrennt: ein Standbild, danach keine Bilder mehr
          if (!ruhend) {
            zeichne(jetzt, 0);
            ruhend = true;
          }
          return;
        }
        fps = 30; // bis der Übergang ausgeklungen ist
      }
      ruhend = false;
      raf = requestAnimationFrame(bild);
      if (jetzt - letztes < 1000 / fps - 1) return;
      const dt = Math.min(0.1, (jetzt - letztes) / 1000);
      letztes = jetzt;
      zeichne(jetzt, dt);
    };

    function wecke() {
      ruhend = false;
      if (!raf && !document.hidden) raf = requestAnimationFrame(bild);
    }

    weckeRef.current = wecke;
    const leseStore = () => {
      const s = wamo.get();
      st.presence = s.presence;
      st.acting = laeuftWerkzeug(s) && istOnline(s);
      st.approval = wartetAufEntscheidung(s);
      wecke();
    };
    leseStore();
    const abmelden = wamo.subscribe(leseStore);

    const beiTheme = () => {
      leseFarben();
      wecke();
    };
    dark.addEventListener("change", beiTheme);
    const beobachter = new MutationObserver(beiTheme);
    beobachter.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    reduced.addEventListener("change", wecke);
    document.addEventListener("visibilitychange", wecke);
    // Pegel ändern sich ohne Store: solange gesprochen wird, hält der Wecker die Schleife wach
    const pegelWecker = setInterval(() => {
      if (ruhend && (levels.current.user > 0.02 || levels.current.bot > 0.02)) wecke();
    }, 250);

    // Der ResizeObserver meldet die Größe schon beim Start. Ein anderer Monitor ändert nur die Pixeldichte:
    // dafür eine Medienabfrage auf den aktuellen Wert, die sich nach jedem Wechsel neu anmeldet.
    const groessenBeobachter = new ResizeObserver(resize);
    groessenBeobachter.observe(canvas);
    let dpiAbfrage: MediaQueryList | null = null;
    const beiDpi = () => {
      resize();
      beobachteDpi();
    };
    function beobachteDpi() {
      dpiAbfrage?.removeEventListener("change", beiDpi);
      dpiAbfrage = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      dpiAbfrage.addEventListener("change", beiDpi);
    }
    beobachteDpi();

    return () => {
      weckeRef.current = null;
      cancelAnimationFrame(raf);
      clearInterval(pegelWecker);
      abmelden();
      groessenBeobachter.disconnect();
      dpiAbfrage?.removeEventListener("change", beiDpi);
      beobachter.disconnect();
      dark.removeEventListener("change", beiTheme);
      reduced.removeEventListener("change", wecke);
      document.removeEventListener("visibilitychange", wecke);
    };
  }, [levels]);

  return <canvas ref={canvasRef} className="kz-kern" aria-hidden="true" />;
});
