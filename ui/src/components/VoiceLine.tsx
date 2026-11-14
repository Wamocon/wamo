"use client";

import { memo, useEffect, useRef } from "react";
import { HISTORY, type LevelHistory } from "@/lib/wamo";

const SLOTS = HISTORY; // ~12 s bei 20 Hz
const NACH_SCHNITT = 12; // 600 ms nach einer Unterbrechung: WAMO-Balken blass

/**
 * Die Stimmlinie: ein laufendes Band über die letzten Sekunden.
 * Oberhalb der Mittellinie der Mensch (Tinte, weitere Sprecher grau), unterhalb WAMO (WMC-Rot).
 * Eine Unterbrechung erscheint als senkrechter Strich in Tinte mit rotem Rand; die WAMO-Balken
 * danach werden 600 ms lang blass gezeichnet (WAMO hört auf).
 * Gezeichnet wird im eigenen Animationsframe aus einem Ref, nur bei neuen Abtastwerten.
 * Farben aus den CSS-Variablen (globals.css), sie folgen dem Wechsel hell/dunkel.
 */
export const VoiceLine = memo(function VoiceLine({ history }: { history: { current: LevelHistory } }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    let width = 0;
    let height = 0;
    let drawn = -1;
    let raf = 0;
    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      ({ width, height } = canvas.getBoundingClientRect());
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawn = -1;
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();
    const css = getComputedStyle(canvas); // live: folgt auch dem Wechsel hell/dunkel
    // Gezeichnet wird nur bei neuen Abtastwerten: nach einem Wechsel von Hell und Dunkel einmal neu zeichnen
    const beiTheme = new MutationObserver(() => {
      drawn = -1;
    });
    beiTheme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const h = history.current;
      if (document.hidden || !h || h.seq === drawn) return;
      drawn = h.seq;

      const userColor = css.getPropertyValue("--voice-user").trim();
      const otherColor = css.getPropertyValue("--voice-other").trim();
      const botColor = css.getPropertyValue("--voice-wamo").trim();
      const cutColor = css.getPropertyValue("--cut").trim();
      const mid = height / 2;
      const step = width / SLOTS;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = css.getPropertyValue("--rule").trim();
      ctx.fillRect(0, mid - 0.5, width, 1);

      const offset = SLOTS - h.samples.length;
      let seitSchnitt = Infinity;
      const schnitte: number[] = [];
      h.samples.forEach((s, i) => {
        const x = (offset + i) * step;
        if (s.cut) {
          seitSchnitt = 0;
          schnitte.push(x);
        } else seitSchnitt++;
        const u = Math.min(1, s.user * 2.2) * (mid - 4);
        const b = Math.min(1, s.bot * 2.2) * (mid - 4);
        if (u > 0.5) {
          ctx.fillStyle = s.speaker > 0 ? otherColor : userColor;
          ctx.fillRect(x, mid - u - 1, Math.max(1, step - 1), u);
        }
        if (b > 0.5) {
          ctx.globalAlpha = seitSchnitt < NACH_SCHNITT ? 0.4 : 1;
          ctx.fillStyle = botColor;
          ctx.fillRect(x, mid + 1, Math.max(1, step - 1), b);
          ctx.globalAlpha = 1;
        }
      });
      // Schnitte zuletzt, damit sie über den Balken liegen: Strich in Tinte mit rotem Rand
      for (const x of schnitte) {
        ctx.fillStyle = botColor;
        ctx.fillRect(x - 2.5, 0, 5, height);
        ctx.fillStyle = cutColor;
        ctx.fillRect(x - 1, 0, 2, height);
      }
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      beiTheme.disconnect();
    };
  }, [history]);

  return (
    <canvas
      ref={ref}
      className="voice-line"
      role="img"
      aria-label="Stimmlinie: oben deine Stimme, unten WAMO"
    />
  );
});
