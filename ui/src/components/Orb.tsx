"use client";

import { memo, useEffect, useRef } from "react";
import { cssFarbe, type Rgba } from "@/lib/stimmfarben";
import { istOnline, laeuftWerkzeug, wamo, wartetAufEntscheidung, type LevelSample, type Presence } from "@/lib/wamo";

/**
 * Der Kern: WAMOs Präsenz als Raw-WebGL-Quad (ein Fragment-Shader, keine Abhängigkeit).
 * Regel: innen = WAMO (Graustufe über den WMC-Verlauf Schwarz, Anthrazit, Rot, Weiß eingefärbt),
 * außen = Mensch (Ring in Tinte, weitere Sprecher grau, verformt sich mit dem Nutzerpegel,
 * unten am stärksten). Denken: roter Komet. Handeln: 48 Teilstriche mit rotem Lauf-Segment.
 * Freigabe wartet: geschlossener roter Ring. Unterbrechung: Kern zieht sich kurz zusammen.
 *
 * Leistung: Pegel kommen pro Bild aus einem Ref, Zustände direkt aus dem Store (kein React-Render).
 * Bildrate 60 fps bei hört zu und spricht, 30 sonst, offline ein Standbild, nichts bei verborgener
 * Seite. DPR höchstens 2, adaptiv 1 bei langsamen Bildern. Kontextverlust wird behandelt.
 * Farben aus den CSS-Variablen (lib/stimmfarben.ts), auch beim Wechsel hell/dunkel.
 */

const STATE: Record<Presence, { awake: number; think: number }> = {
  offline: { awake: 0.85, think: 0 },
  connecting: { awake: 0.9, think: 0.7 },
  idle: { awake: 1, think: 0 },
  listening: { awake: 1, think: 0 },
  thinking: { awake: 1, think: 1 },
  speaking: { awake: 1, think: 0 },
};

const FPS: Record<Presence, number> = { offline: 0, connecting: 30, idle: 30, listening: 60, thinking: 30, speaking: 60 };

const VERT = "attribute vec2 aPos; void main(){ gl_Position = vec4(aPos, 0.0, 1.0); }";

// GLSL-Quelltext bewusst nur ASCII (auch in Kommentaren), manche Treiber lehnen sonst ab.
// Idee des Farbverlaufs: colorRamp im ElevenLabs-UI-Orb (MIT); hier eigene Umsetzung ohne three.js.
const FRAG = `
precision mediump float;
uniform vec2 uRes;
uniform float uTime, uUser, uBot, uBreath, uThink, uAwake, uAct, uListen, uCut, uApprove, uReduced;
uniform vec3 uAccent;
uniform vec3 uVoice;
uniform vec4 uRim;
const float TAU = 6.2831853;
const vec3 ANTHRA = vec3(0.102);

float hash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float noise(vec3 x) { vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z); }
float fbm(vec3 p) { float v = 0.0; float a = 0.5; for (int i = 0; i < 4; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; } return v; }

vec3 ramp(float l) {
  if (l < 0.45) return mix(vec3(0.0), ANTHRA, l / 0.45);
  if (l < 0.88) return mix(ANTHRA, uAccent, (l - 0.45) / 0.43);
  return mix(uAccent, vec3(1.0), (l - 0.88) / 0.12);
}
void over(inout vec4 dst, vec3 c, float a) { dst = vec4(c * a, a) + dst * (1.0 - a); }

void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
  float r = length(uv); float a = atan(uv.y, uv.x); float t = uTime;
  float R = 0.25 + 0.012 * uBreath + 0.055 * uBot - 0.02 * uCut;
  if (r > R + 0.2) { gl_FragColor = vec4(0.0); return; }

  // innen: WAMO. Fluss, beim Denken Wirbel; der WAMO-Pegel macht ihn heller und schneller
  float sw = uThink * 2.2 * (R - r);
  vec2 w = vec2(cos(sw) * uv.x - sin(sw) * uv.y, sin(sw) * uv.x + cos(sw) * uv.y);
  float m = fbm(vec3(w * 3.0 + vec2(t * 0.15, -t * 0.11), t * (0.25 + 0.6 * uBot)));
  float depth = pow(max(0.0, 1.0 - r / R), 0.85);
  float l = clamp((depth * (0.62 + 0.45 * m) * (0.8 + 0.75 * uBot) - 0.12 * uListen) * uAwake, 0.0, 1.0);
  float inside = smoothstep(R, R - 0.006, r);

  // aussen: Mensch. Ring verformt sich mit dem Nutzerpegel, unten am staerksten
  float rn = noise(vec3(cos(a) * 2.0 + t * 0.6, sin(a) * 2.0, t * 0.8));
  float ringR = R + 0.045 + (rn - 0.5) * 0.07 * uUser;
  float ringW = 0.003 + 0.009 * uUser;
  float ring = smoothstep(ringW + 0.003, ringW, abs(r - ringR));
  float bottom = 0.5 - 0.5 * sin(a);
  float ringA = ring * (0.28 + 0.72 * uListen * mix(0.35, 1.0, bottom) * (0.35 + 0.65 * uUser));

  float comet = (exp(-mod(t * 2.4 - a, TAU) * 1.6) + 0.05) * smoothstep(0.012, 0.0, abs(r - ringR)) * uThink;
  float tick = smoothstep(0.16, 0.10, abs(fract(a / TAU * 48.0) - 0.5)) * smoothstep(0.014, 0.008, abs(r - (R + 0.095)));
  float lit = exp(-mod(t * 1.6 - a, TAU) * 1.2);
  float pulse = mix(0.65 + 0.35 * sin(t * 5.2), 1.0, uReduced);
  float appr = smoothstep(0.005, 0.002, abs(r - (R + 0.13))) * uApprove * pulse;
  float glow = exp(-max(r - R, 0.0) * 15.0) * (0.08 + 0.55 * uBot) * uAwake;
  float flash = smoothstep(0.012, 0.0, abs(r - R)) * uCut;

  vec4 o = vec4(0.0);
  over(o, uAccent, glow * (1.0 - inside));
  over(o, uVoice, ringA);
  over(o, ramp(l), inside);
  over(o, uRim.rgb, uRim.a * smoothstep(0.004, 0.0, abs(r - R)));
  over(o, uVoice, tick * 0.3 * uAct);
  over(o, uAccent, tick * lit * uAct);
  over(o, uAccent, clamp(comet, 0.0, 1.0));
  over(o, uAccent, appr);
  over(o, vec3(1.0), flash);
  gl_FragColor = o;
}`;

const UNIFORMS = [
  "uRes", "uTime", "uUser", "uBot", "uBreath", "uThink", "uAwake", "uAct", "uListen",
  "uCut", "uApprove", "uReduced", "uAccent", "uVoice", "uRim",
] as const;
type Uniform = (typeof UNIFORMS)[number];

type Gpu = { gl: WebGLRenderingContext; u: Record<Uniform, WebGLUniformLocation | null> };

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
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "aPos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
  const u = {} as Gpu["u"];
  for (const name of UNIFORMS) u[name] = gl.getUniformLocation(prog, name);
  return { gl, u };
}

export const Orb = memo(function Orb({ levels }: { levels: { current: LevelSample } }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let gpu = createGpu(canvas);
    if (!gpu) return;

    // Zustand aus dem Store, ohne React-Render
    const st = { presence: "offline" as Presence, acting: false, approval: false };
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    const farben = { wamo: [0.957, 0.055, 0.055, 1] as Rgba, user: [0, 0, 0, 1] as Rgba, other: [0.42, 0.42, 0.42, 1] as Rgba, grau: [0.42, 0.42, 0.42, 1] as Rgba, rim: [0, 0, 0, 0] as Rgba };
    const leseFarben = () => {
      const css = getComputedStyle(canvas);
      farben.wamo = cssFarbe(css, "--voice-wamo");
      farben.user = cssFarbe(css, "--voice-user");
      farben.other = cssFarbe(css, "--voice-other");
      farben.grau = cssFarbe(css, "--wmc-gray");
      farben.rim = cssFarbe(css, "--kern-rim");
    };
    leseFarben();

    const smooth = { user: 0, bot: 0, awake: STATE.offline.awake, think: 0, act: 0, listen: 0, approve: 0, off: 1 };
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
      ease("act", st.acting ? 1 : 0, 3);
      ease("listen", p === "listening" ? 1 : 0, 3);
      ease("approve", st.approval ? 1 : 0, 3);
      ease("off", p === "offline" ? 1 : 0, 3);
      if (!reduced.matches) clock += dt * (0.55 + 0.6 * smooth.bot + 0.6 * smooth.think);
      const breath = reduced.matches || p === "offline" ? 0.5 : 0.5 + 0.5 * Math.sin((now / 5000) * Math.PI * 2);
      const mix = (a: Rgba, b: Rgba, k: number) => [0, 1, 2].map((i) => a[i] + (b[i] - a[i]) * k);
      const voice = (lv.speaker ?? 0) > 0 ? farben.other : farben.user;

      gl.uniform2f(u.uRes, canvas.width, canvas.height);
      gl.uniform1f(u.uTime, clock);
      gl.uniform1f(u.uUser, smooth.user);
      gl.uniform1f(u.uBot, smooth.bot);
      gl.uniform1f(u.uBreath, breath);
      gl.uniform1f(u.uThink, smooth.think);
      gl.uniform1f(u.uAwake, smooth.awake);
      gl.uniform1f(u.uAct, smooth.act);
      gl.uniform1f(u.uListen, smooth.listen);
      gl.uniform1f(u.uCut, cut);
      gl.uniform1f(u.uApprove, smooth.approve);
      gl.uniform1f(u.uReduced, reduced.matches ? 1 : 0);
      gl.uniform3fv(u.uAccent, mix(farben.wamo, farben.grau, smooth.off));
      gl.uniform3fv(u.uVoice, voice.slice(0, 3));
      gl.uniform4fv(u.uRim, farben.rim);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };

    const ruhig = () =>
      st.presence === "offline" &&
      !st.approval &&
      smooth.off > 0.995 &&
      smooth.user < 0.005 &&
      smooth.bot < 0.005 &&
      smooth.think < 0.005 &&
      smooth.act < 0.005 &&
      smooth.listen < 0.005 &&
      smooth.approve < 0.005 &&
      Math.abs(smooth.awake - STATE.offline.awake) < 0.005;

    const frame = (now: number) => {
      raf = 0;
      if (!gpu || document.hidden) return; // verborgen: nichts zeichnen, visibilitychange weckt
      const cutAge = now - (levels.current.cutAt ?? -1e9);
      let fps = FPS[st.presence];
      if (st.acting || st.approval) fps = Math.max(fps, 30);
      if (cutAge < 300) fps = 60;
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
      st.approval = wartetAufEntscheidung(s);
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
    };
    const onRestored = () => {
      gpu = createGpu(canvas);
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
    };
  }, [levels]);

  return <canvas ref={canvasRef} className="orb" aria-hidden="true" />;
});
