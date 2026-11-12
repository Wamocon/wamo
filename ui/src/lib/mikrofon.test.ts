// Tests für Mikrofon-Wächter, Meldungen, Gerätewahl und Sendespur (lib/mikrofon.ts).
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  STILLE_DBFS,
  STILLE_MS,
  anzeigePegel,
  fehlerName,
  geraeteListe,
  keineSpurText,
  kernFehltText,
  kernStilleText,
  messen,
  mikrofonFehlerText,
  neueStille,
  pruefeStille,
  sendeSpurErsetzen,
  spurEndeText,
  startGeraet,
  stilleAusblenden,
  stilleAusgeblendet,
  stilleText,
  stilleWiederAufnehmen,
  tonLog,
  windowsGesperrt,
  windowsGesperrtText,
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

test("Meldungen zu fehlender, beendeter und von Windows gesperrter Spur nennen Abhilfe, ohne Gedankenstriche", () => {
  const texte = [
    kernFehltText(5),
    spurEndeText("Headset (Test)"),
    spurEndeText(null),
    keineSpurText(),
    windowsGesperrtText(),
  ];
  for (const text of texte) {
    assert.ok(text.length > 40);
    assert.doesNotMatch(text, / [–—-] /, "kein Gedankenstrich");
  }
  assert.match(kernFehltText(5), /5 Sekunden/);
  assert.match(spurEndeText("Headset (Test)"), /Headset \(Test\)/);
  assert.match(keineSpurText(), /Mikrofon aus/);
  assert.match(windowsGesperrtText(), /Datenschutz und Sicherheit/);
});

test("Windows sperrt nur bei denied und restricted", () => {
  assert.equal(windowsGesperrt("denied"), true);
  assert.equal(windowsGesperrt("restricted"), true);
  for (const frei of ["granted", "not-determined", "unknown", undefined, null, 1]) assert.equal(windowsGesperrt(frei), false);
});

test("Positive Logzeile nennt Gerät und gerundete Spitze", () => {
  assert.equal(tonLog("Onboard MIC", -52.7), "Mikrofon liefert Ton: Onboard MIC, Spitze -53 dBFS");
  assert.match(tonLog(null, -60), /unbekanntes Gerät/);
  assert.ok(tonLog("x".repeat(500), -60).length < 200);
});

// ---------- "Ohne Mikrofon weiter" (12.11.2026: die Warnung kam bei jedem Gespräch wieder, obwohl das Gerät tot war) ----------
test("Ohne Mikrofon weiter: die Warnung bleibt für genau dieses Gerät aus, auch bei einem neuen Gespräch", () => {
  const geraet = "Onboard MIC (Intel® Smart Sound Technologie für digitale Mikrofone)";
  assert.equal(stilleAusgeblendet(geraet), false);
  stilleAusblenden(geraet);
  assert.equal(stilleAusgeblendet(geraet), true);
  assert.equal(stilleAusgeblendet("Headset (eMeet M220 Lite)"), false); // ein anderes Gerät warnt weiter
  stilleAusblenden(geraet); // zweimal ist nicht schlimmer
  stilleWiederAufnehmen(geraet);
  assert.equal(stilleAusgeblendet(geraet), false); // liefert es wieder Ton, warnt WAMO bei der nächsten Stille wieder
  stilleWiederAufnehmen(geraet); // ohne Eintrag passiert nichts
});

test("Ohne Mikrofon weiter: Bezeichnung wie in der Oberfläche (auf 120 Zeichen gekürzt), ohne Namen gilt ein gemeinsamer Eintrag", () => {
  const lang = "M".repeat(300);
  stilleAusblenden(lang);
  assert.equal(stilleAusgeblendet("M".repeat(120)), true); // useWamo speichert das Label gekürzt auf 120 Zeichen
  stilleWiederAufnehmen(lang);
  assert.equal(stilleAusgeblendet(lang), false);
  for (const ohne of [null, undefined, "", "   "]) {
    stilleAusblenden(ohne);
    assert.equal(stilleAusgeblendet(null), true, String(ohne));
    stilleWiederAufnehmen(undefined);
    assert.equal(stilleAusgeblendet(""), false, String(ohne));
  }
});

test("Ohne Mikrofon weiter: useWamo.ts fragt vor beiden Stille-Warnungen nach und nimmt sie bei neuem Ton wieder auf", () => {
  const quelle = readFileSync(new URL("./useWamo.ts", import.meta.url), "utf8");
  assert.match(quelle, /if \(stilleAusgeblendet\(mic\.track\.label\)\)[^\n]*\n\s*else setFehler\(\{ art: "mikrofon", text: stilleText\(/); // Wächter der Oberfläche
  assert.match(quelle, /msg\.zustand === "still"[^\n]*!stilleAusgeblendet\(/); // Meldung des Kerns
  assert.match(quelle, /ergebnis === "wieder_da"\) stilleWiederAufnehmen\(mic\.track\.label\)/);
  assert.match(quelle, /msg\.zustand === "fehlt" && st\.micOn\)/); // eine abgerissene Spur wird nie ausgeblendet
});

test("Ohne Mikrofon weiter: höchstens 20 Geräte, das älteste fällt zuerst heraus", () => {
  for (let i = 0; i < 25; i++) stilleAusblenden(`Gerät ${i}`);
  assert.equal(stilleAusgeblendet("Gerät 0"), false);
  assert.equal(stilleAusgeblendet("Gerät 4"), false);
  assert.equal(stilleAusgeblendet("Gerät 5"), true);
  assert.equal(stilleAusgeblendet("Gerät 24"), true);
  for (let i = 5; i < 25; i++) stilleWiederAufnehmen(`Gerät ${i}`);
});
