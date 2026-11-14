"use client";

import { memo, useEffect, useRef } from "react";
import { cssFarbe, type Rgba } from "@/lib/stimmfarben";
import { istOnline, laeuftWerkzeug, wamo, type LevelSample, type Presence } from "@/lib/wamo";

/**
 * Die Lichtkugel im Kern: WAMOs Präsenz als Raw-WebGL-Quad (ein Fragment-Shader, keine Abhängigkeit).
 * Eine weiche Glut in WMC-Rot mit heller Mitte. Der Rand atmet, wächst mit der Stimme und wird vom Rauschen gewellt
 * (beim Sprechen und Denken stärker). Spricht die Person, hellt die Glut auf, bei einer weiteren Stimme in deren Ton.
 * Denken: Wirbel im Inneren. Handeln: drei kreisende Lichtpunkte mit Hof. Unterbrechung: die Kugel zieht sich kurz zusammen
 * und blitzt am Rand weiß auf. Alle übrigen Ringe (Skala, Segmente, Pegelbogen, Lichtbogen, Strichring, Freigabe) zeichnet
 * kommando/KernRinge.tsx als SVG darüber.
 *
 * Leistung: Pegel kommen pro Bild aus einem Ref, Zustände direkt aus dem Store (kein React-Render).
 * Bildrate 60 fps bei hört zu und spricht, 30 sonst, offline ein Standbild, nichts bei verborgener
 * Seite. DPR höchstens 2, adaptiv 1 bei langsamen Bildern. Kontextverlust wird behandelt; beim Aushängen gibt die Kugel
 * Programm, Puffer und Kontext frei.
 * Farben aus den CSS-Variablen (lib/stimmfarben.ts), auch beim Wechsel hell/dunkel.
 */

const STATE: Record<Presence, { awake: number; think: number; speed: number }> = {
  offline: { awake: 0.55, think: 0, speed: 0.35 },
  connecting: { awake: 0.8, think: 0.7, speed: 1 },
  idle: { awake: 1, think: 0, speed: 0.55 },
  listening: { awake: 1, think: 0, speed: 0.8 },
  thinking: { awake: 1, think: 1, speed: 1.4 },
  speaking: { awake: 1, think: 0, speed: 1 },
};

const FPS: Record<Presence, number> = { offline: 0, connecting: 30, idle: 30, listening: 60, thinking: 30, speaking: 60 };

const VERT = "attribute vec2 aPos; void main(){ gl_Position = vec4(aPos, 0.0, 1.0); }";

// GLSL-Quelltext bewusst nur ASCII (auch in Kommentaren), manche Treiber lehnen sonst ab.
const FRAG = `
precision mediump float;
uniform vec2 uRes;
uniform float uTime, uUser, uBot, uBreath, uThink, uAwake, uAct, uCut;
uniform vec3 uAccent;
uniform vec3 uHot;
uniform vec3 uHuman;
const float TAU = 6.2831853;

float hash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float noise(vec3 x) { vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z); }
float fbm(vec3 p) { float v = 0.0; float a = 0.5; for (int i = 0; i < 4; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; } return v; }

void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
  float r = length(uv); float a = atan(uv.y, uv.x); float t = uTime;
  float speak = clamp(max(uUser, uBot), 0.0, 1.0);

  // Rand: atmet, waechst mit der Stimme, wird vom Rauschen gewellt. Kleiner als die Fassung (r 0,295): dunkler Spalt zum Ring
  float n = fbm(vec3(cos(a) * 1.6 + t * 0.25, sin(a) * 1.6 - t * 0.2, t * 0.45));
  float radius = 0.16 + 0.012 * uBreath + 0.05 * speak + (n - 0.5) * (0.02 + 0.06 * speak + 0.035 * uThink) - 0.02 * uCut;
  float inside = smoothstep(radius, radius - 0.01, r);

  // Inneres: fliessende Tiefe, beim Denken als Wirbel
  float swirl = uThink * 1.8 * (0.35 - r);
  vec2 w = vec2(cos(swirl) * uv.x - sin(swirl) * uv.y, sin(swirl) * uv.x + cos(swirl) * uv.y);
  float m = fbm(vec3(w * 3.2 + vec2(t * 0.18, -t * 0.12), t * 0.35));
  float userShare = uUser / (uUser + uBot + 0.0001);
  vec3 voice = mix(uAccent, uHuman, userShare * 0.55);
  vec3 idle = mix(uAccent, uHot, 0.3 + 0.25 * sin(t * 0.4));
  vec3 col = mix(idle, voice, smoothstep(0.02, 0.18, speak));
  col = mix(col * 0.5, col * 1.2, m);
  col += mix(uHot, vec3(1.0), 0.55) * 0.75 * pow(max(0.0, 1.0 - r / max(radius, 0.001)), 2.2);

  // Handeln: drei kreisende Lichtpunkte mit Hof
  float dots = 0.0;
  for (int k = 0; k < 3; k++) {
    float ang = t * 1.4 + float(k) * 2.094;
    float d = length(uv - vec2(cos(ang), sin(ang)) * (radius + 0.07));
    dots += smoothstep(0.02, 0.0, d) + 0.35 * exp(-d * 45.0);
  }
  dots *= uAct;

  // Leuchten nach aussen und Aufblitzen bei einer Unterbrechung
  float glow = exp(-max(r - radius, 0.0) * 11.0) * (0.32 + 0.5 * speak + 0.2 * uThink);
  vec3 halo = mix(uAccent, voice, 0.6);
  float flash = smoothstep(0.012, 0.0, abs(r - radius)) * uCut;
  float alpha = inside + (1.0 - inside) * glow;
  vec3 outCol = mix(halo, col, inside);
  outCol += dots * mix(vec3(1.0), halo, 0.35);
  outCol = mix(outCol, vec3(1.0), clamp(flash, 0.0, 1.0));
  alpha = clamp(alpha + dots + flash, 0.0, 1.0) * uAwake;
  gl_FragColor = vec4(outCol * alpha, alpha);
}`;

const UNIFORMS = [
  "uRes", "uTime", "uUser", "uBot", "uBreath", "uThink", "uAwake", "uAct", "uCut",
  "uAccent", "uHot", "uHuman",
] as const;
type Uniform = (typeof UNIFORMS)[number];

type Gpu = {
  gl: WebGLRenderingContext;
  u: Record<Uniform, WebGLUniformLocation | null>;
  programm: WebGLProgram;
  puffer: WebGLBuffer | null;
};

/**
 * GPU-Mittel freigeben. Den Kontext selbst nur beim echten Aushängen (die Leinwand ist dann schon aus dem Dokument): Im
 * Entwicklungsbetrieb hängt React dieselbe Leinwand zur Probe aus und wieder ein, ein verlorener Kontext käme dort nicht wieder.
 */
function gpuFreigeben(gpu: Gpu, canvas: HTMLCanvasElement) {
  gpu.gl.deleteBuffer(gpu.puffer);
  gpu.gl.deleteProgram(gpu.programm);
  if (!canvas.isConnected) gpu.gl.getExtension("WEBGL_lose_context")?.loseContext();
}

function createGpu(canvas: HTMLCanvasElement): Gpu | null {
  const gl = canvas.getContext("webgl", {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false, // Kanten glättet schon smoothstep
    powerPreference: "low-power",
  });
  if (!gl) return null;
  const shader = (type: number, src: string) => {
    const s = gl.createShader(type);
    if (!s) return null;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return s;
  };
  const vs = shader(gl.VERTEX_SHADER, VERT);
  const fs = shader(gl.FRAGMENT_SHADER, FRAG);
  const prog = gl.createProgram();
  if (!vs || !fs || !prog) return null;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
  gl.useProgram(prog);
  const puffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, puffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "aPos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
  const u = {} as Gpu["u"];
  for (const name of UNIFORMS) u[name] = gl.getUniformLocation(prog, name);
  return { gl, u, programm: prog, puffer };
}

export const Orb = memo(function Orb({ levels }: { levels: { current: LevelSample } }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let gpu = createGpu(canvas);
    canvas.dataset.gpu = gpu ? "an" : "aus"; // "aus": kernRinge.css zeigt dann die Kugel aus einem Farbverlauf
    if (!gpu) return;

    // Zustand aus dem Store, ohne React-Render
    const st = { presence: "offline" as Presence, acting: false };
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    const farben = {
      wamo: [0.957, 0.055, 0.055, 1] as Rgba,
      heiss: [1, 0.34, 0.28, 1] as Rgba,
      mensch: [1, 0.95, 0.93, 1] as Rgba,
      fremd: [0.8, 0.8, 0.85, 1] as Rgba,
      grau: [0.42, 0.42, 0.42, 1] as Rgba,
    };
    const leseFarben = () => {
      const css = getComputedStyle(canvas);
      farben.wamo = cssFarbe(css, "--voice-wamo");
      farben.heiss = cssFarbe(css, "--kern-heiss");
      farben.mensch = cssFarbe(css, "--kern-mensch");
      farben.fremd = cssFarbe(css, "--kern-fremd");
      farben.grau = cssFarbe(css, "--wmc-gray");
    };
    leseFarben();

    const smooth = { user: 0, bot: 0, awake: STATE.offline.awake, think: 0, speed: STATE.offline.speed, act: 0, off: 1 };
    let raf = 0;
    let clock = 0;
    let lastDraw = 0;
    let still = false;
    let slow = 0;
    let dprCap = 2;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, dprCap);
      canvas.width = Math.max(1, Math.round(canvas.clientWidth * dpr));
      canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
      gpu?.gl.viewport(0, 0, canvas.width, canvas.height);
      wake();
    };

    const draw = (now: number, dt: number, cut: number) => {
      if (!gpu) return;
      const { gl, u } = gpu;
      const p = st.presence;
      const target = STATE[p];
      const lv = levels.current;
      const ease = (k: keyof typeof smooth, to: number, rate: number) => {
        smooth[k] += (to - smooth[k]) * Math.min(1, rate * dt);
      };
      let user = Math.min(1, (lv.user ?? 0) * 3.2);
      const bot = Math.min(1, (lv.bot ?? 0) * 3.2);
      if (reduced.matches) user = Math.round(user * 2) / 2; // drei Stufen statt Zittern
      ease("user", user, user > smooth.user ? 18 : 4);
      ease("bot", bot, bot > smooth.bot ? 18 : 4);
      ease("awake", target.awake, 3);
      ease("think", target.think, 3);
      ease("speed", target.speed, 2);
      ease("act", st.acting ? 1 : 0, 4);
      ease("off", p === "offline" ? 1 : 0, 3);
      if (!reduced.matches) clock += dt * smooth.speed;
      const breath = reduced.matches || p === "offline" ? 0.5 : 0.5 + 0.5 * Math.sin((now / 5000) * Math.PI * 2);
      const mix = (a: Rgba, b: Rgba, k: number) => [0, 1, 2].map((i) => a[i] + (b[i] - a[i]) * k);

      gl.uniform2f(u.uRes, canvas.width, canvas.height);
      gl.uniform1f(u.uTime, clock);
      gl.uniform1f(u.uUser, smooth.user);
      gl.uniform1f(u.uBot, smooth.bot);
      gl.uniform1f(u.uBreath, breath);
      gl.uniform1f(u.uThink, smooth.think);
      gl.uniform1f(u.uAwake, smooth.awake);
      gl.uniform1f(u.uAct, smooth.act);
      gl.uniform1f(u.uCut, cut);
      gl.uniform3fv(u.uAccent, mix(farben.wamo, farben.grau, smooth.off));
      gl.uniform3fv(u.uHot, mix(farben.heiss, farben.grau, smooth.off));
      gl.uniform3fv(u.uHuman, (lv.speaker ?? 0) > 0 ? farben.fremd.slice(0, 3) : farben.mensch.slice(0, 3));
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };

    const ruhig = () =>
      st.presence === "offline" &&
      smooth.off > 0.995 &&
      smooth.user < 0.005 &&
      smooth.bot < 0.005 &&
      smooth.think < 0.005 &&
      smooth.act < 0.005 &&
      Math.abs(smooth.awake - STATE.offline.awake) < 0.005 &&
      Math.abs(smooth.speed - STATE.offline.speed) < 0.005;

    const frame = (now: number) => {
      raf = 0;
      if (!gpu || document.hidden) return; // verborgen: nichts zeichnen, visibilitychange weckt
      const cutAge = now - (levels.current.cutAt ?? -1e9);
      let fps = FPS[st.presence];
      if (st.acting) fps = Math.max(fps, 30);
      if (cutAge < 300) fps = 60;
      if (reduced.matches && fps > 0) fps = Math.min(fps, 12);
      if (fps === 0) {
        if (ruhig()) {
          // offline: ein Standbild, danach keine Bilder mehr
          if (!still) {
            draw(now, 0, 0);
            still = true;
          }
          return;
        }
        fps = 30; // bis der Übergang ausgeklungen ist
      }
      still = false;
      raf = requestAnimationFrame(frame);
      if (now - lastDraw < 1000 / fps - 1) return;
      const dt = Math.min(0.1, (now - lastDraw) / 1000);
      lastDraw = now;
      // adaptiv: viele verspätete Bilder (z. B. Iris Xe mit mehreren Monitoren) -> DPR 1
      slow = dt > 1 / fps + 0.015 ? slow + 1 : Math.max(0, slow - 1);
      if (slow > 45 && dprCap > 1) {
        dprCap = 1;
        resize();
      }
      draw(now, dt, Math.max(0, 1 - cutAge / 250));
    };

    function wake() {
      still = false;
      if (!raf && gpu && !document.hidden) raf = requestAnimationFrame(frame);
    }

    const leseStore = () => {
      const s = wamo.get();
      st.presence = s.presence;
      st.acting = laeuftWerkzeug(s) && istOnline(s);
      wake();
    };
    leseStore();
    const unsubscribe = wamo.subscribe(leseStore);

    const onTheme = () => {
      leseFarben();
      wake();
    };
    dark.addEventListener("change", onTheme);
    const themeObserver = new MutationObserver(onTheme);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    reduced.addEventListener("change", wake);
    document.addEventListener("visibilitychange", wake);
    // Pegel ändern sich ohne Store: solange gesprochen wird, hält die Bildrate die Schleife wach
    const pegelWecker = setInterval(() => {
      if (still && (levels.current.user > 0.02 || levels.current.bot > 0.02)) wake();
    }, 250);

    const onLost = (e: Event) => {
      e.preventDefault(); // erlaubt die Wiederherstellung
      cancelAnimationFrame(raf);
      raf = 0;
      gpu = null;
      canvas.dataset.gpu = "aus";
    };
    const onRestored = () => {
      gpu = createGpu(canvas);
      canvas.dataset.gpu = gpu ? "an" : "aus";
      resize();
    };
    canvas.addEventListener("webglcontextlost", onLost);
    canvas.addEventListener("webglcontextrestored", onRestored);

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    return () => {
      cancelAnimationFrame(raf);
      clearInterval(pegelWecker);
      unsubscribe();
      observer.disconnect();
      themeObserver.disconnect();
      dark.removeEventListener("change", onTheme);
      reduced.removeEventListener("change", wake);
      document.removeEventListener("visibilitychange", wake);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      if (gpu) gpuFreigeben(gpu, canvas); // nach dem Abmelden der Ereignisse: ein loseContext meldet sich nicht mehr hier
      gpu = null;
    };
  }, [levels]);

  return <canvas ref={canvasRef} className="orb" aria-hidden="true" />;
});
