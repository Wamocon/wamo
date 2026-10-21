/**
 * Zeichnung des Präsenzkerns (Canvas 2D, keine Abhängigkeit). Reine Funktion von Zustand und Farben,
 * damit PraesenzKern.tsx nur Schleife, Größe und Zustandsübernahme enthält.
 *
 * Aufbau von außen nach innen (Radien als Anteil des halben Durchmessers):
 *   0,97  gestrichelter Außenring, sehr zart
 *   0,86  Ring aus Bögen mit Lücken, darüber der rotierende Teilbogen in Akzentrot (Länge folgt dem Pegel)
 *   0,91  Strichmarken, beim Handeln läuft ein rotes Segment um
 *   0,80  Umlaufbahnen (Ellipsen) mit kleinen Partikeln
 *   0,60  innerer dünner Ring
 *   0,30  Kern: radialer Verlauf, hell innen, rot außen, mit weichem Glühen
 * Farben: nur WMC-Rot, Tinte (Schwarz oder Weiß je nach Theme) und Grau. Getrennt blendet alles nach Grau.
 */

export type Farbe = [number, number, number];

export type KernFarben = { rot: Farbe; linie: Farbe; grau: Farbe };

export type KernZustand = {
  /** Zeit in Sekunden, bereits mit dem Tempo des Zustands gewichtet */
  t: number;
  /** geglätteter Pegel 0 bis 1 (Glühen und Länge des Teilbogens) */
  pegel: number;
  /** Zusammenziehen und Aufblitzen nach einer Unterbrechung, 0 bis 1 */
  unterbrechung: number;
  /** 0 = lebendig, 1 = getrennt (grau) */
  grau: number;
  /** 0 bis 1: WAMO denkt (Bahnen schneller, Kern leicht unruhig) */
  denken: number;
  /** 0 bis 1: ein Werkzeug läuft (Strichmarken mit Lauf-Segment) */
  handeln: number;
  /** 0 bis 1: eine Freigabe wartet (geschlossener roter Ring, pulsiert) */
  freigabe: number;
  /** Puls der Freigabe-Anzeige, 0 bis 1 */
  freigabePuls: number;
  /** weitere Stimme im Raum: innerer Ring grau statt Tinte */
  fremd: boolean;
  /** 1 = normal, größer = mehr Bahnen und Partikel (Zentrale) */
  dichte: number;
};

const TAU = Math.PI * 2;

const rgba = (c: Farbe, a: number) =>
  `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
const misch = (a: Farbe, b: Farbe, k: number): Farbe => [
  a[0] + (b[0] - a[0]) * k,
  a[1] + (b[1] - a[1]) * k,
  a[2] + (b[2] - a[2]) * k,
];
const WEISS: Farbe = [255, 255, 255];

/** Ellipsenbahnen: Halbachsen, Grundwinkel, Drehgeschwindigkeit (rad/s), Anzahl Partikel. */
const BAHNEN = [
  { a: 0.8, b: 0.3, w: 0.0, v: 0.1, n: 5 },
  { a: 0.76, b: 0.44, w: 0.9, v: -0.14, n: 4 },
  { a: 0.72, b: 0.56, w: 1.8, v: 0.08, n: 6 },
  { a: 0.82, b: 0.2, w: 2.7, v: -0.07, n: 3 },
  { a: 0.68, b: 0.63, w: 0.4, v: 0.12, n: 5 },
  { a: 0.78, b: 0.36, w: 2.2, v: -0.1, n: 4 },
  { a: 0.74, b: 0.5, w: 1.3, v: 0.06, n: 4 },
] as const;

/** Bögen des äußeren Rings (Start, Länge in Bogenmaß); dazwischen bleiben Lücken. */
const BOEGEN = [
  [0.1, 1.15],
  [1.5, 0.75],
  [2.45, 1.4],
  [4.1, 0.85],
  [5.15, 1.0],
] as const;

const MARKEN = 96;

export function zeichneKern(ctx: CanvasRenderingContext2D, groesse: number, z: KernZustand, f: KernFarben) {
  const u = groesse / 2; // halber Durchmesser
  const cx = u;
  const cy = u;
  const rot = misch(f.rot, f.grau, z.grau);
  const linie = f.linie;
  const lw = Math.max(1, groesse / 360); // Linienstärke wächst mit der Größe

  ctx.clearRect(0, 0, groesse, groesse);
  ctx.lineCap = "round";

  // ---- Kernradius und Glühen ----
  const atem = 1 + 0.045 * z.pegel + 0.012 * Math.sin(z.t * (2.2 + 4 * z.denken));
  const rk = u * 0.3 * atem * (1 - 0.13 * z.unterbrechung);
  const glueh = (0.22 + 0.5 * z.pegel) * (1 - 0.55 * z.grau);
  const g = ctx.createRadialGradient(cx, cy, rk * 0.4, cx, cy, rk * (2.3 + 0.9 * z.pegel));
  g.addColorStop(0, rgba(rot, glueh));
  g.addColorStop(0.45, rgba(rot, glueh * 0.35));
  g.addColorStop(1, rgba(rot, 0));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(cx, cy, u, 0, TAU);
  ctx.fill();

  // ---- gestrichelter Außenring ----
  ctx.save();
  ctx.strokeStyle = rgba(rot, 0.28);
  ctx.lineWidth = lw;
  ctx.setLineDash([lw * 1.5, lw * 5.5]);
  ctx.lineDashOffset = -z.t * 6 * lw;
  ctx.beginPath();
  ctx.arc(cx, cy, u * 0.97, 0, TAU);
  ctx.stroke();
  ctx.restore();

  // ---- Strichmarken (dunkel, dazu ein rotes Lauf-Segment beim Handeln) ----
  const r0 = u * 0.905;
  const r1 = u * 0.935;
  ctx.strokeStyle = rgba(linie, 0.34);
  ctx.lineWidth = lw;
  ctx.beginPath();
  for (let i = 0; i < MARKEN; i++) {
    const w = (i / MARKEN) * TAU;
    const lang = i % 8 === 0;
    const ra = lang ? r0 - u * 0.014 : r0;
    ctx.moveTo(cx + Math.cos(w) * ra, cy + Math.sin(w) * ra);
    ctx.lineTo(cx + Math.cos(w) * r1, cy + Math.sin(w) * r1);
  }
  ctx.stroke();
  if (z.handeln > 0.01) {
    ctx.lineWidth = lw * 1.8;
    const kopf = (z.t * 1.9) % TAU;
    for (let i = 0; i < MARKEN; i++) {
      const w = (i / MARKEN) * TAU;
      const abstand = (((kopf - w) % TAU) + TAU) % TAU; // hinter dem Kopf des Segments
      if (abstand > 0.9) continue;
      ctx.strokeStyle = rgba(rot, z.handeln * (1 - abstand / 0.9)); // Helligkeit fällt nach hinten ab
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(w) * r0, cy + Math.sin(w) * r0);
      ctx.lineTo(cx + Math.cos(w) * r1, cy + Math.sin(w) * r1);
      ctx.stroke();
    }
  }

  // ---- äußerer Ring aus Bögen mit Lücken (dreht gegen den Teilbogen) ----
  ctx.strokeStyle = rgba(linie, 0.55);
  ctx.lineWidth = lw * 1.3;
  const ringDreh = -z.t * 0.12;
  for (const [s, len] of BOEGEN) {
    ctx.beginPath();
    ctx.arc(cx, cy, u * 0.86, ringDreh + s, ringDreh + s + len);
    ctx.stroke();
  }

  // ---- Umlaufbahnen mit Partikeln ----
  const bahnen = Math.min(BAHNEN.length, Math.round(5 * z.dichte));
  for (let i = 0; i < bahnen; i++) {
    const b = BAHNEN[i];
    const dreh = b.w + b.v * z.t;
    ctx.strokeStyle = rgba(rot, (0.2 + 0.1 * z.pegel) * (1 - 0.5 * z.grau));
    ctx.lineWidth = lw;
    ctx.beginPath();
    ctx.ellipse(cx, cy, u * b.a, u * b.b, dreh, 0, TAU);
    ctx.stroke();
    const cd = Math.cos(dreh);
    const sd = Math.sin(dreh);
    for (let k = 0; k < b.n; k++) {
      const phi = (k / b.n) * TAU + z.t * (0.22 + 0.04 * i) * (i % 2 ? -1 : 1);
      const ex = u * b.a * Math.cos(phi);
      const ey = u * b.b * Math.sin(phi);
      const px = cx + ex * cd - ey * sd;
      const py = cy + ex * sd + ey * cd;
      const funkeln = 0.55 + 0.45 * Math.sin(z.t * 2.4 + i * 1.7 + k * 2.3);
      ctx.fillStyle = rgba(misch(rot, WEISS, 0.45), (0.5 + 0.5 * funkeln) * (1 - 0.4 * z.grau));
      ctx.beginPath();
      ctx.arc(px, py, lw * (1.05 + 0.5 * funkeln), 0, TAU);
      ctx.fill();
    }
  }

  // ---- innerer dünner Ring ----
  ctx.strokeStyle = rgba(z.fremd ? f.grau : linie, 0.7);
  ctx.lineWidth = lw * 1.2;
  ctx.beginPath();
  ctx.arc(cx, cy, u * 0.6, 0, TAU);
  ctx.stroke();

  // ---- Kern: hell innen, rot außen ----
  const hx = cx - rk * 0.18;
  const hy = cy - rk * 0.22;
  const k = ctx.createRadialGradient(hx, hy, rk * 0.02, cx, cy, rk);
  const innen = misch(misch(rot, WEISS, 0.92), f.grau, z.grau * 0.5);
  const mitte = misch(rot, WEISS, 0.5);
  const dunkel = misch([120, 0, 0], [40, 40, 40], z.grau);
  const kante = misch([84, 0, 0], [24, 24, 24], z.grau);
  k.addColorStop(0, rgba(innen, 1));
  k.addColorStop(0.2, rgba(misch(mitte, f.grau, z.grau), 1));
  k.addColorStop(0.55, rgba(rot, 1));
  k.addColorStop(0.88, rgba(dunkel, 1));
  k.addColorStop(1, rgba(kante, 1));
  ctx.fillStyle = k;
  ctx.beginPath();
  ctx.arc(cx, cy, rk, 0, TAU);
  ctx.fill();

  // ---- Teilbogen in Akzentrot: Länge und Helligkeit folgen dem Pegel ----
  const span = 0.9 + 2.3 * z.pegel; // 50 bis 180 Grad
  const dreh = z.t * (0.42 + 0.5 * z.denken) + 2.4;
  ctx.save();
  ctx.strokeStyle = rgba(rot, 0.28);
  ctx.lineWidth = lw * 7;
  ctx.beginPath();
  ctx.arc(cx, cy, u * 0.86, dreh, dreh + span);
  ctx.stroke();
  ctx.strokeStyle = rgba(rot, 1);
  ctx.lineWidth = lw * 3.2;
  ctx.beginPath();
  ctx.arc(cx, cy, u * 0.86, dreh, dreh + span);
  ctx.stroke();
  ctx.restore();

  // ---- Freigabe wartet: geschlossener roter Ring ----
  if (z.freigabe > 0.01) {
    ctx.strokeStyle = rgba(rot, z.freigabe * (0.45 + 0.55 * z.freigabePuls));
    ctx.lineWidth = lw * 2.2;
    ctx.beginPath();
    ctx.arc(cx, cy, u * 0.975, 0, TAU);
    ctx.stroke();
  }

  // ---- Unterbrechung: kurzes Aufblitzen am Rand des Kerns ----
  if (z.unterbrechung > 0.01) {
    ctx.strokeStyle = rgba(WEISS, z.unterbrechung);
    ctx.lineWidth = lw * 2;
    ctx.beginPath();
    ctx.arc(cx, cy, rk, 0, TAU);
    ctx.stroke();
  }
}
