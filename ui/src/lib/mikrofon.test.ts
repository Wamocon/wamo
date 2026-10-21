// Tests für Mikrofon-Wächter, Meldungen, Gerätewahl und Sendespur (lib/mikrofon.ts).
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  STILLE_DBFS,
  STILLE_MS,
  anzeigePegel,
  fehlerName,
  geraeteListe,
  kernStilleText,
  messen,
  mikrofonFehlerText,
  neueStille,
  pruefeStille,
  sendeSpurErsetzen,
  startGeraet,
  stilleText,
} from "./mikrofon.ts";

test("messen: exakt null ist -Infinity, Spitze und RMS stimmen", () => {
  assert.equal(messen(new Float32Array(512)).spitzeDb, Number.NEGATIVE_INFINITY);
  const m = messen([0.5, -0.5, 0.5, -0.5]);
  assert.ok(Math.abs(m.spitzeDb - 20 * Math.log10(0.5)) < 1e-9);
  assert.ok(Math.abs(m.rms - 0.5) < 1e-9);
  assert.equal(messen([]).rms, 0);
  assert.equal(anzeigePegel(0.8), 1);
  assert.equal(anzeigePegel(0.1), 0.2);
});

test("Stille: erst nach STILLE_MS ununterbrochener Stille genau eine Warnung", () => {
  const z = neueStille();
  const still = Number.NEGATIVE_INFINITY;
  assert.equal(pruefeStille(z, still, 0, true), null);
  assert.equal(pruefeStille(z, still, STILLE_MS - 1, true), null);
  assert.equal(pruefeStille(z, still, STILLE_MS, true), "still");
  assert.equal(pruefeStille(z, still, STILLE_MS + 500, true), null, "nur einmal je stiller Phase");
});

test("Stille: ein Fenster mit Ton setzt die Uhr zurück", () => {
  const z = neueStille();
  pruefeStille(z, -120, 0, true);
  assert.equal(pruefeStille(z, -60, 2900, true), null);
  assert.equal(pruefeStille(z, -120, 3000, true), null);
  assert.equal(pruefeStille(z, -120, 5900, true), null);
  assert.equal(pruefeStille(z, -120, 6000, true), "still");
});

test("Stille: Leises Raumrauschen (über der Schwelle) gilt nicht als still", () => {
  const z = neueStille();
  for (let t = 0; t <= 10_000; t += 50) assert.equal(pruefeStille(z, STILLE_DBFS + 5, t, true), null);
});

test("Stille: wieder Ton nach der Warnung meldet wieder_da", () => {
  const z = neueStille();
  pruefeStille(z, -200, 0, true);
  assert.equal(pruefeStille(z, -200, 3000, true), "still");
  assert.equal(pruefeStille(z, -40, 3100, true), "wieder_da");
  assert.equal(pruefeStille(z, -40, 3200, true), null);
});

test("Stille: stumm geschaltet zählt nicht und hebt die Warnung auf", () => {
  const z = neueStille();
  for (let t = 0; t <= 10_000; t += 50) assert.equal(pruefeStille(z, -200, t, false), null);
  pruefeStille(z, -200, 20_000, true);
  assert.equal(pruefeStille(z, -200, 23_000, true), "still");
  assert.equal(pruefeStille(z, -200, 23_050, false), "wieder_da");
});

test("Meldungen nennen Gerät und Abhilfe, ohne Gedankenstriche", () => {
  const t = stilleText("Mikrofon (DisplayLink Audio)");
  assert.match(t, /DisplayLink Audio/);
  assert.match(t, /anderes Mikrofon/);
  assert.match(kernStilleText(10), /10 Sekunden/);
  for (const name of ["NotAllowedError", "NotFoundError", "NotReadableError", "AbortError", "SecurityError", undefined]) {
    const text = mikrofonFehlerText(name);
    assert.ok(text.length > 30);
    assert.doesNotMatch(text, / [–—-] /, "kein Gedankenstrich");
  }
  assert.match(mikrofonFehlerText("NotAllowedError"), /Datenschutz/);
  assert.doesNotMatch(stilleText(null), /„/);
});

test("fehlerName liest DeviceError (sourceError) und DOMException", () => {
  assert.equal(fehlerName({ name: "DeviceError", details: { sourceError: { name: "NotAllowedError" } } }), "NotAllowedError");
  assert.equal(fehlerName({ name: "NotFoundError" }), "NotFoundError");
  assert.equal(fehlerName(null), undefined);
  assert.equal(fehlerName("x"), undefined);
});

test("Geräteliste ohne Aliase, Startgerät nur, wenn noch angeschlossen", () => {
  const liste = geraeteListe([
    { kind: "audioinput", deviceId: "default", label: "Standard - Onboard" },
    { kind: "audioinput", deviceId: "communications", label: "Kommunikation - Onboard" },
    { kind: "audioinput", deviceId: "abc", label: "Onboard MIC" },
    { kind: "audioinput", deviceId: "def", label: "" },
    { kind: "audiooutput", deviceId: "xyz", label: "Lautsprecher" },
  ]);
  assert.deepEqual(liste, [
    { id: "abc", label: "Onboard MIC" },
    { id: "def", label: "Mikrofon" },
  ]);
  assert.equal(startGeraet(liste, "abc"), "abc");
  assert.equal(startGeraet(liste, "weg"), null);
  assert.equal(startGeraet(liste, null), null);
});

function falscherTransport(spurAmSender: unknown) {
  const ersetzt: unknown[] = [];
  const sender = {
    track: spurAmSender,
    replaceTrack: async (t: unknown) => {
      ersetzt.push(t);
      sender.track = t;
    },
  };
  return {
    ersetzt,
    transport: { pc: { getTransceivers: () => [{ sender, receiver: { track: { kind: "audio" } } }] } },
  };
}

test("Sendespur: neue Spur wird auf den Audio-Sender gelegt, gleiche nicht", async () => {
  const alt = { id: "alt" } as unknown as MediaStreamTrack;
  const neu = { id: "neu" } as unknown as MediaStreamTrack;
  const { transport, ersetzt } = falscherTransport(alt);
  assert.equal(await sendeSpurErsetzen(transport, neu), "ersetzt");
  assert.deepEqual(ersetzt, [neu]);
  assert.equal(await sendeSpurErsetzen(transport, neu), "gleich");
  assert.equal(ersetzt.length, 1);
});

test("Sendespur: ohne Verbindung oder andere Transportversion passiert nichts", async () => {
  const spur = {} as MediaStreamTrack;
  assert.equal(await sendeSpurErsetzen({}, spur), "kein_sender");
  assert.equal(await sendeSpurErsetzen(null, spur), "kein_sender");
  assert.equal(await sendeSpurErsetzen({ pc: { getTransceivers: () => [] } }, spur), "kein_sender");
});
