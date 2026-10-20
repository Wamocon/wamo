// Stimmen-Rand "Rote Kante": ein Saum entlang aller Bildschirmkanten, über allen Programmen,
// nicht anklickbar. Im Ruhezustand unsichtbar und ohne Bilder (0 fps); sichtbar nur bei Stimme,
// Arbeit oder Freigabe. Zustände kommen mit den Pegeln aus der Oberfläche (main.js prüft sie):
// * WAMO spricht: rotes Band an allen Kanten (Breite und Deckkraft folgen dem Pegel), rote Haarlinie
// * Mensch spricht: Licht steigt von der Unterkante auf (heller Kern mit dunklem Unterschatten,
//   damit es auch über hellen Programmen sichtbar ist); weitere Sprecher grau
// * denkt nach: ein roter Lichtpunkt läuft am Rand entlang (4 s je Runde)
// * handelt: rote Eckwinkel und ein gestricheltes Lauflicht auf der Haarlinie
// * Freigabe wartet: rote Haarlinie ringsum, Eckwinkel pulsieren, bis entschieden ist
// * Unterbrechung: die Haarlinie blitzt einmal weiß (höchstens ein Blitz je Ereignis, WCAG 2.3.1)
// Bei reduzierter Bewegung: kein Fluss, kein Lauf, kein Puls, kein Blitz; Formen bleiben unterscheidbar.
//
// Farben kommen aus den CSS-Variablen der Oberfläche (--overlay-*), einmalig über die Brücke.
// Breite: WAMO_VEIL_CM in der Desktop-App (Standard 4,55 cm). Leistung: halbe Auflösung,
// Mitte wird sofort verworfen, 20 bis 30 Bilder/s nur solange etwas zu sehen ist.

const RENDER_SCALE = 0.5;
const VEIL_DIP = Number(new URLSearchParams(location.search).get("veil")) || 172;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const level = { user: 0, other: 0, bot: 0 };
const target = { user: 0, other: 0, bot: 0 };
const w = { think: 0, act: 0, approve: 0 };
let zustand = "idle";
let cut = 0;
let farben = null; // { wamo, mensch, andere } je [r, g, b] 0 bis 255

window.overlay.onLevels((s) => {
  const speaker = s.speaker || 0;
  target.user = speaker === 0 ? s.user : 0;
  target.other = speaker > 0 ? s.user : 0;
  target.bot = s.bot;
  if (s.cut && !reducedMotion) cut = 1;
  zustand = s.zustand || "idle";
  // Nur aufwecken, wenn es etwas zu zeigen gibt; das Ausklingen hält die Schleife selbst am Leben
  const aktiv = zustand === "thinking" || zustand === "acting" || zustand === "approval";
  if (aktiv || cut > 0 || target.user > 0.003 || target.other > 0.003 || target.bot > 0.003) wake();
});

window.overlay.onFarben((f) => {
  farben = f;
  wake();
});

const VERT = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }`;

// GLSL-Quelltext bewusst nur ASCII (auch in Kommentaren), manche Treiber lehnen sonst ab.
const FRAG = `
precision mediump float;
uniform vec2 uRes;
uniform float uTime, uVeil, uScale;
uniform float uWamo, uUser, uOther, uCut;
uniform float uThink, uAct, uApprove, uReduced;
uniform vec3 uRed, uLight, uGrey;

float hash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(hash(i), hash(i + vec3(1, 0, 0)), f.x), mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
    mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x), mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y),
    f.z);
}
float fbm(vec3 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 3; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; }
  return v;
}
void over(inout vec4 d, vec3 c, float a) { d = vec4(c * a, a) + d * (1.0 - a); }

void main() {
  vec2 p = gl_FragCoord.xy;
  float W = uRes.x; float H = uRes.y;
  float dx = min(p.x, W - p.x); float dy = min(p.y, H - p.y);
  if (min(dx, dy) > uVeil * 1.2) { gl_FragColor = vec4(0.0); return; }   // Mitte frei
  float k = uVeil * 0.35;
  float d = -k * log(exp(-dx / k) + exp(-dy / k));                       // weiche Kante, runde Ecken

  // Umfangskoordinate s in [0,1): unten links beginnend, gegen den Uhrzeigersinn
  float P = 2.0 * (W + H); float s;
  if (dy <= dx) s = (p.y < H * 0.5) ? p.x : W + H + (W - p.x);
  else          s = (p.x > W * 0.5) ? W + p.y : 2.0 * W + H + (H - p.y);
  s /= P;

  float n = fbm(vec3(p / (uVeil * 1.4), uTime * 0.25));
  // etwa 2 DIP Haarlinie; harter Kantenabstand, sonst fuellt die weiche Ecke einen breiten Bogen
  float hair = 1.0 - smoothstep(1.0 * uScale, 2.5 * uScale, min(dx, dy));

  // WAMO spricht: rotes Band an allen Kanten
  float wb = uVeil * (0.25 + 0.55 * uWamo) * (0.8 + 0.4 * n);
  float wamo = 1.0 - smoothstep(0.0, wb, d);
  wamo = wamo * wamo * (0.10 + 0.50 * uWamo) * smoothstep(0.005, 0.06, uWamo);  // ohne WAMO-Stimme kein Band
  float wamoHair = hair * smoothstep(0.02, 0.15, uWamo) * 0.9;

  // Mensch spricht: Licht steigt von der Unterkante auf (heller Kern, dunkler Unterschatten)
  float voice = max(uUser, uOther);
  float fromBottom = 1.0 - smoothstep(0.0, H * 0.55, p.y);
  float ub = uVeil * (0.20 + 0.60 * voice) * (0.8 + 0.4 * n);
  float shade = (1.0 - smoothstep(0.0, ub, d)) * fromBottom * 0.22 * voice;
  float core = (1.0 - smoothstep(0.0, ub * 0.55, d)) * fromBottom * (0.15 + 0.55 * voice) * step(0.01, voice);
  vec3 vcol = mix(uLight, uGrey, uOther / (uUser + uOther + 1e-4));

  // denkt nach: ein roter Lichtpunkt laeuft um (4 s je Runde); reduziert: statisches Segment oben Mitte
  float behind = fract(fract(uTime / 4.0) - s);
  float runner = exp(-behind * 18.0);
  float topMid = (1.0 - smoothstep(0.02, 0.03, abs(s - 0.625))) * step(dx, dy);
  float comet = mix(runner, topMid, uReduced) * (hair + 0.5 * (1.0 - smoothstep(0.0, 6.0 * uScale, d))) * uThink;

  // handelt / Freigabe: Eckwinkel 64 x 3 DIP, Lauflicht auf der Haarlinie
  float cl = 64.0 * uScale; float cw = 3.0 * uScale;
  float corner = (1.0 - smoothstep(cl - uScale, cl, max(dx, dy))) * (1.0 - smoothstep(cw - uScale, cw, min(dx, dy)));
  float dash = mix(step(0.5, fract(s * P / (24.0 * uScale) - uTime * 1.5)), 0.0, uReduced);
  float act = uAct * (hair * dash * 0.6 + corner * 0.9);
  float pulse = mix(0.55 + 0.45 * (0.5 + 0.5 * sin(uTime * 5.2)), 1.0, uReduced);
  float appr = uApprove * (hair * 0.8 + corner * pulse);

  vec4 o = vec4(0.0);
  over(o, vec3(0.0), shade);
  over(o, vcol, core);
  over(o, uRed, clamp(wamo + wamoHair + comet + act + appr, 0.0, 1.0));
  over(o, vec3(1.0), clamp(uCut * hair, 0.0, 1.0));
  gl_FragColor = o;                                                    // vormultipliziertes Alpha
}`;

const canvas = document.getElementById("edge");
const gl = canvas.getContext("webgl", {
  alpha: true,
  premultipliedAlpha: true,
  antialias: false,
  powerPreference: "low-power",
});

function compile(type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}

const rgb = (c) => c.map((v) => v / 255);

let raf = 0;
let wake = () => {};

if (!gl) {
  console.warn("WebGL nicht verfügbar: Stimmen-Rand aus");
} else {
  const u = {};
  let bereit = false; // false nach Kontextverlust, bis der Kontext wiederhergestellt ist

  // Programm, Puffer und Uniforms anlegen (beim Start und nach webglcontextrestored)
  function setup() {
    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(prog, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    for (const name of [
      "uRes", "uTime", "uVeil", "uScale", "uWamo", "uUser", "uOther", "uCut",
      "uThink", "uAct", "uApprove", "uReduced", "uRed", "uLight", "uGrey",
    ]) {
      u[name] = gl.getUniformLocation(prog, name);
    }
    bereit = true;
  }
  setup();

  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault(); // erlaubt die Wiederherstellung
    bereit = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  });
  canvas.addEventListener("webglcontextrestored", () => {
    setup();
    resize();
  });

  let scale = 1;
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    scale = dpr * RENDER_SCALE;
    canvas.width = Math.round(window.innerWidth * scale);
    canvas.height = Math.round(window.innerHeight * scale);
    gl.viewport(0, 0, canvas.width, canvas.height);
    cleared = false;
    wake();
  }

  let last = 0;
  let clock = 0;
  let cleared = false;

  const clear = () => {
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
  };

  function frame(now) {
    raf = 0;
    if (!bereit) return; // Kontext verloren: warten auf webglcontextrestored
    for (const k of ["user", "other", "bot"]) {
      const t = Math.min(1, target[k] * 3.2); // Pegel kommen eher leise an
      level[k] += (t - level[k]) * (t > level[k] ? 0.3 : 0.06);
    }
    w.think += ((zustand === "thinking" ? 1 : 0) - w.think) * 0.08;
    w.act += ((zustand === "acting" ? 1 : 0) - w.act) * 0.08;
    w.approve += ((zustand === "approval" ? 1 : 0) - w.approve) * 0.08;
    cut *= 0.93;
    const voice = Math.max(level.user, level.other, level.bot);
    const busy = w.think + w.act + w.approve;
    if (!farben || (voice < 0.01 && busy < 0.01 && cut < 0.01)) {
      // Ruhe: einmal leeren, dann keine Bilder mehr, bis neue Pegel oder Zustände kommen
      if (!cleared) {
        clear();
        cleared = true;
      }
      return;
    }
    cleared = false;
    raf = requestAnimationFrame(frame);
    const fps = voice > 0.03 || cut > 0.02 || w.approve > 0.5 ? 30 : 20;
    if (now - last < 1000 / fps) return;
    const dt = Math.min(0.1, (now - (last || now)) / 1000);
    last = now;
    if (!reducedMotion) clock += dt;

    gl.uniform2f(u.uRes, canvas.width, canvas.height);
    gl.uniform1f(u.uTime, clock);
    gl.uniform1f(u.uVeil, VEIL_DIP * scale);
    gl.uniform1f(u.uScale, scale);
    gl.uniform1f(u.uWamo, level.bot);
    gl.uniform1f(u.uUser, level.user);
    gl.uniform1f(u.uOther, level.other);
    gl.uniform1f(u.uCut, cut);
    gl.uniform1f(u.uThink, w.think);
    gl.uniform1f(u.uAct, w.act);
    gl.uniform1f(u.uApprove, w.approve);
    gl.uniform1f(u.uReduced, reducedMotion ? 1 : 0);
    gl.uniform3fv(u.uRed, rgb(farben.wamo));
    gl.uniform3fv(u.uLight, rgb(farben.mensch));
    gl.uniform3fv(u.uGrey, rgb(farben.andere));
    clear();
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  wake = () => {
    if (!raf) raf = requestAnimationFrame(frame);
  };
  window.addEventListener("resize", resize);
  resize();
}
