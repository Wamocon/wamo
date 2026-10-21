// Tests für den Ablauf der Geräteeinrichtung (lib/geraet.ts) mit falscher Desktop-Brücke und falschem Kern.
// Ausführen im Ordner ui/:  npm test   (node:test, Node lädt TypeScript selbst)
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  FEHLER_PAUSE_MS,
  WIEDERHOLUNG_MS,
  anzeigeText,
  einrichten,
  hinweisZeile,
  naechsteWartezeitMs,
  pruefeAktiveAntwort,
  pruefeStatusAntwort,
  type BindeAntwort,
  type GeraetBruecke,
  type KernJson,
} from "./geraet.ts";

const USER = "0b8a5f2e-3c1d-4e6f-9a7b-1c2d3e4f5a6b";
const PUBKEY = `ssh-ed25519 ${"A".repeat(68)}`;
const NONCE = "bm9uY2Utd2FtbyJ9bm9uY2Utd2FtbyJ9";
const SIGNATUR = "S".repeat(86);

function kernAntwort(ueberschreibe: Record<string, unknown> = {}, ziel: Record<string, unknown> = {}) {
  return {
    ergebnis: "neu_aktiv",
    status: "aktiv",
    name: "Laptop Anna",
    kennung: "pc-anna",
    slot: 5,
    stufe: "mitglied",
    user_id: USER,
    ziel: {
      benutzer: "wamo-t-05",
      host: "192.168.178.136",
      socket: "/run/wamo-pc/t05/pc.sock",
      hostschluessel: `ssh-ed25519 ${"B".repeat(68)}`,
      ...ziel,
    },
    ...ueberschreibe,
  };
}

type Aufruf = [string, ...unknown[]];

/** Falsche Brücke und falscher Kern; jede Antwort lässt sich überschreiben. Alle Aufrufe werden aufgezeichnet. */
function aufbau(
  opt: {
    vorbereiten?: unknown;
    signieren?: unknown;
    aktivieren?: unknown;
    herausforderung?: unknown;
    binden?: unknown;
    kernFehler?: { pfad: string; fehler: unknown };
  } = {},
) {
  const aufrufe: Aufruf[] = [];
  const werfen = (v: unknown) => (v instanceof Error ? Promise.reject(v) : Promise.resolve(v));
  const desktop: GeraetBruecke = {
    vorbereiten: () => {
      aufrufe.push(["vorbereiten"]);
      return werfen(opt.vorbereiten ?? { ok: true, pubkey: PUBKEY, hostname: "LAPTOP-07", fingerprint: `SHA256:${"F".repeat(43)}`, neu: true });
    },
    signieren: (anfrage) => {
      aufrufe.push(["signieren", anfrage]);
      return werfen(opt.signieren ?? { ok: true, signatur: SIGNATUR });
    },
    aktivieren: (antwort) => {
      aufrufe.push(["aktivieren", antwort]);
      return werfen(opt.aktivieren ?? { ok: true, status: { eingerichtet: true } });
    },
    status: () => Promise.resolve({ ok: true }),
  };
  const kernJson: KernJson = (pfad, methode = "GET", body) => {
    aufrufe.push(["kern", pfad, methode, body]);
    if (opt.kernFehler && opt.kernFehler.pfad === pfad) return Promise.reject(opt.kernFehler.fehler);
    if (pfad === "/geraet/herausforderung") return werfen("herausforderung" in opt ? opt.herausforderung : { nonce: NONCE, gueltig_s: 300 });
    if (pfad === "/geraet/binden") return werfen("binden" in opt ? opt.binden : kernAntwort());
    return Promise.reject(new Error(`unerwarteter Pfad ${pfad}`));
  };
  return { desktop, kernJson, aufrufe, namen: () => aufrufe.map((a) => (a[0] === "kern" ? `kern ${a[2]} ${a[1]}` : a[0])) };
}

test("ohne Desktop-Brücke oder ohne Benutzer ist nichts zu tun", async () => {
  const a = aufbau();
  assert.deepEqual(await einrichten({ desktop: undefined, kernJson: a.kernJson, userId: USER }), { zustand: "nicht_noetig" });
  assert.deepEqual(await einrichten({ desktop: null, kernJson: a.kernJson, userId: USER }), { zustand: "nicht_noetig" });
  assert.deepEqual(await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: undefined }), { zustand: "nicht_noetig" });
  assert.deepEqual(await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: "" }), { zustand: "nicht_noetig" });
  assert.equal(a.aufrufe.length, 0);
  const r = await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: "kein-uuid" });
  assert.equal(r.zustand, "fehler");
  assert.equal(a.aufrufe.length, 0);
});

test("aktives Gerät: Schritte in fester Reihenfolge, Bindung mit den richtigen Feldern, geprüfte Antwort an die App", async () => {
  const a = aufbau({ binden: { ...kernAntwort(), unbekannt: "wird nicht weitergereicht", ziel: { ...kernAntwort().ziel, extra: "x" } } });
  const r = await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER });
  assert.deepEqual(r, { zustand: "eingerichtet" });
  assert.deepEqual(a.namen(), ["vorbereiten", "kern GET /geraet/herausforderung", "signieren", "kern POST /geraet/binden", "aktivieren"]);
  assert.deepEqual(a.aufrufe[2], ["signieren", { nonce: NONCE, userId: USER }]);
  assert.deepEqual(a.aufrufe[3], [
    "kern",
    "/geraet/binden",
    "POST",
    { pubkey: PUBKEY, hostname: "LAPTOP-07", nonce: NONCE, signatur: SIGNATUR },
  ]);
  const weitergereicht = a.aufrufe[4][1] as BindeAntwort;
  assert.deepEqual(weitergereicht, {
    ergebnis: "neu_aktiv",
    status: "aktiv",
    name: "Laptop Anna",
    kennung: "pc-anna",
    slot: 5,
    stufe: "mitglied",
    user_id: USER,
    ziel: { benutzer: "wamo-t-05", host: "192.168.178.136", socket: "/run/wamo-pc/t05/pc.sock", hostschluessel: `ssh-ed25519 ${"B".repeat(68)}` },
  });
});

test("bekanntes Gerät (zweiter Aufruf) und Slot null werden wie ein aktives behandelt", async () => {
  const a = aufbau({ binden: kernAntwort({ ergebnis: "bekannt", slot: null, stufe: "admin" }) });
  assert.deepEqual(await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER }), { zustand: "eingerichtet" });
  assert.equal((a.aufrufe[4][1] as BindeAntwort).slot, null);
  assert.equal((a.aufrufe[4][1] as BindeAntwort).stufe, "admin");
});

test("beantragt: wartet, ohne die App zu aktivieren", async () => {
  for (const ergebnis of ["neu_beantragt", "bekannt"]) {
    const a = aufbau({ binden: { ergebnis, status: "beantragt", name: "Laptop", kennung: null, slot: null, stufe: "mitglied", user_id: USER, ziel: null } });
    assert.deepEqual(await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER }), { zustand: "wartet" });
    assert.ok(!a.namen().includes("aktivieren"));
  }
});

test("gesperrt und widerrufen: Fehler mit deutschem Text, nichts wird aktiviert", async () => {
  for (const status of ["gesperrt", "widerrufen"]) {
    const a = aufbau({ binden: kernAntwort({ status, ziel: null }) });
    const r = await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER });
    assert.equal(r.zustand, "fehler");
    assert.match(r.text ?? "", /Admin/);
    assert.ok(!a.namen().includes("aktivieren"));
  }
});

test("Fehler des Kerns: sein Text wird angezeigt (gekürzt, ohne Steuerzeichen); ohne Text ein fester", async () => {
  for (const pfad of ["/geraet/herausforderung", "/geraet/binden"]) {
    let a = aufbau({ kernFehler: { pfad, fehler: new Error("Dieser Schlüssel gehört zu einem anderen Konto.") } });
    assert.deepEqual(await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER }), {
      zustand: "fehler",
      text: "Dieser Schlüssel gehört zu einem anderen Konto.",
    });
    a = aufbau({ kernFehler: { pfad, fehler: new Error(`${"x".repeat(300)}\u0000\n`) } });
    const lang = await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER });
    assert.equal(lang.text?.length, 200);
    a = aufbau({ kernFehler: { pfad, fehler: "kein Fehlerobjekt" } });
    assert.equal((await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER })).zustand, "fehler");
  }
  assert.equal(anzeigeText("a\u0000b\nc", "x"), "a b c");
  assert.equal(anzeigeText("   ", "Ersatz"), "Ersatz");
  assert.equal(anzeigeText({ text: 1 }, "Ersatz"), "Ersatz");
});

test("Fehler der Desktop-App werden mit ihrem Text gemeldet", async () => {
  const schritte = ["vorbereiten", "signieren", "aktivieren"] as const;
  for (const schritt of schritte) {
    const a = aufbau({ [schritt]: { ok: false, text: "Die Einrichtung des Laptops läuft nur in der Betriebsart Büro." } });
    const r = await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER });
    assert.deepEqual(r, { zustand: "fehler", text: "Die Einrichtung des Laptops läuft nur in der Betriebsart Büro." }, schritt);
    // danach nichts mehr
    assert.equal(a.namen().at(-1), schritt);
    const b = aufbau({ [schritt]: new Error("Brücke weg") });
    assert.equal((await einrichten({ desktop: b.desktop, kernJson: b.kernJson, userId: USER })).zustand, "fehler", schritt);
    const c = aufbau({ [schritt]: "unsinn" });
    assert.equal((await einrichten({ desktop: c.desktop, kernJson: c.kernJson, userId: USER })).zustand, "fehler", schritt);
    const d = aufbau({ [schritt]: { ok: false } });
    assert.equal((await einrichten({ desktop: d.desktop, kernJson: d.kernJson, userId: USER })).zustand, "fehler", schritt);
  }
});

test("ungültige Werte von der Brücke stoppen den Ablauf vor dem Kern", async () => {
  const gut = { ok: true, pubkey: PUBKEY, hostname: "LAPTOP-07", fingerprint: `SHA256:${"F".repeat(43)}`, neu: false };
  const schlecht = [
    { ...gut, pubkey: "ssh-rsa AAAA" },
    { ...gut, pubkey: `${PUBKEY}\n` },
    { ...gut, pubkey: `${PUBKEY} kommentar` },
    { ...gut, pubkey: 5 },
    { ...gut, hostname: "pc_1" },
    { ...gut, hostname: "pc.lan" },
    { ...gut, hostname: "" },
    { ...gut, fingerprint: "MD5:abc" },
  ];
  for (const vorbereiten of schlecht) {
    const a = aufbau({ vorbereiten });
    assert.equal((await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER })).zustand, "fehler", JSON.stringify(vorbereiten));
    assert.ok(!a.namen().some((n) => n.startsWith("kern")));
  }
  for (const signatur of ["kurz", "S".repeat(87), `${"S".repeat(85)}=`, 5, null]) {
    const a = aufbau({ signieren: { ok: true, signatur } });
    assert.equal((await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER })).zustand, "fehler");
    assert.ok(!a.namen().includes("kern POST /geraet/binden"));
  }
});

test("ungültige Herausforderung des Kerns wird abgelehnt", async () => {
  for (const herausforderung of [null, "text", {}, { nonce: 5 }, { nonce: "kurz" }, { nonce: `${NONCE}\n${USER}` }, { nonce: "a".repeat(201) }, { nonce: "a b c d e f g h" }]) {
    const a = aufbau({ herausforderung });
    assert.equal((await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER })).zustand, "fehler", JSON.stringify(herausforderung));
    assert.ok(!a.namen().includes("signieren"));
  }
});

test("fehlerhafte oder böse Bindungsantwort geht nie an die Desktop-App", async () => {
  const schlecht: Record<string, unknown> = {
    "kein Objekt": "aktiv",
    "Liste": [],
    "Status fehlt": { ...kernAntwort(), status: undefined },
    "Status unbekannt": kernAntwort({ status: "freigegeben" }),
    "Ergebnis unbekannt": kernAntwort({ ergebnis: "ok" }),
    "Stufe root": kernAntwort({ stufe: "root" }),
    "fremde user_id": kernAntwort({ user_id: "11111111-2222-4333-8444-555555555555" }),
    "user_id groß": kernAntwort({ user_id: USER.toUpperCase() }),
    "user_id Zahl": kernAntwort({ user_id: 5 }),
    "Kennung groß": kernAntwort({ kennung: "pc-ANNA" }),
    "Kennung fehlt": kernAntwort({ kennung: null }),
    "Kennung lang": kernAntwort({ kennung: `pc-${"a".repeat(21)}` }),
    "Slot 0": kernAntwort({ slot: 0 }),
    "Slot 65": kernAntwort({ slot: 65 }),
    "Slot Text": kernAntwort({ slot: "5" }),
    "Slot Bruch": kernAntwort({ slot: 1.5 }),
    "Name lang": kernAntwort({ name: "x".repeat(101) }),
    "Name Steuerzeichen": kernAntwort({ name: "a\nb" }),
    "Name Zahl": kernAntwort({ name: 5 }),
    "ohne Ziel": kernAntwort({ ziel: null }),
    "Benutzer 00": kernAntwort({}, { benutzer: "wamo-t-00", socket: "/run/wamo-pc/t00/pc.sock" }),
    "Benutzer 65": kernAntwort({}, { benutzer: "wamo-t-65", socket: "/run/wamo-pc/t65/pc.sock" }),
    "Benutzer root": kernAntwort({}, { benutzer: "root" }),
    "Benutzer Option": kernAntwort({}, { benutzer: "wamo-t-05 -oProxyCommand=x" }),
    "Host Name": kernAntwort({}, { host: "evil.example.com" }),
    "Host 256": kernAntwort({}, { host: "192.168.178.256" }),
    "Host Option": kernAntwort({}, { host: "1.2.3.4 -oProxyCommand=x" }),
    "Host Umbruch": kernAntwort({}, { host: "192.168.178.136\n" }),
    "Socket anderer Benutzer": kernAntwort({}, { socket: "/run/wamo-pc/t06/pc.sock" }),
    "Socket aufwärts": kernAntwort({}, { socket: "/run/wamo-pc/t05/../t06/pc.sock" }),
    "Socket Umbruch": kernAntwort({}, { socket: "/run/wamo-pc/t05/pc.sock\n" }),
    "Schlüssel RSA": kernAntwort({}, { hostschluessel: `ssh-rsa ${"B".repeat(68)}` }),
    "Schlüssel kurz": kernAntwort({}, { hostschluessel: `ssh-ed25519 ${"B".repeat(67)}` }),
    "Schlüssel lang": kernAntwort({}, { hostschluessel: `ssh-ed25519 ${"B".repeat(69)}` }),
    "Schlüssel Umbruch": kernAntwort({}, { hostschluessel: `ssh-ed25519 ${"B".repeat(68)}\n` }),
    "Schlüssel Kommentar": kernAntwort({}, { hostschluessel: `ssh-ed25519 ${"B".repeat(68)} x` }),
  };
  for (const [name, binden] of Object.entries(schlecht)) {
    const a = aufbau({ binden });
    const r = await einrichten({ desktop: a.desktop, kernJson: a.kernJson, userId: USER });
    assert.equal(r.zustand, "fehler", name);
    assert.ok(!a.namen().includes("aktivieren"), name);
  }
  assert.equal(pruefeAktiveAntwort(kernAntwort(), USER)?.kennung, "pc-anna");
  assert.equal(pruefeAktiveAntwort(kernAntwort(), "11111111-2222-4333-8444-555555555555"), null);
});

test("Wartezeiten: wartet alle 60 s, Fehler nach 3 in Folge nur noch alle 5 Minuten, sonst nie wieder", () => {
  assert.equal(naechsteWartezeitMs("wartet", 0), WIEDERHOLUNG_MS);
  assert.equal(WIEDERHOLUNG_MS, 60_000);
  assert.equal(FEHLER_PAUSE_MS, 300_000);
  assert.equal(naechsteWartezeitMs("fehler", 1), 60_000);
  assert.equal(naechsteWartezeitMs("fehler", 2), 60_000);
  assert.equal(naechsteWartezeitMs("fehler", 3), 300_000);
  assert.equal(naechsteWartezeitMs("fehler", 50), 300_000);
  assert.equal(naechsteWartezeitMs("eingerichtet", 0), null);
  assert.equal(naechsteWartezeitMs("nicht_noetig", 0), null);
  // nie ohne Pause: jede Wartezeit ist mindestens 60 s
  for (const z of ["wartet", "fehler"] as const) for (let n = 0; n < 10; n++) assert.ok((naechsteWartezeitMs(z, n) ?? 0) >= 60_000);
});

test("Statusantwort der Desktop-App wird geprüft", () => {
  assert.deepEqual(pruefeStatusAntwort({ ok: true, eingerichtet: true, kennung: "pc-anna", stufe: "admin", tunnel: "verbunden" }), {
    eingerichtet: true,
    kennung: "pc-anna",
    stufe: "admin",
    tunnel: "verbunden",
  });
  assert.deepEqual(pruefeStatusAntwort({ ok: true, eingerichtet: false, kennung: null, stufe: null, tunnel: "unbekannt" }), {
    eingerichtet: false,
    kennung: null,
    stufe: null,
    tunnel: "unbekannt",
  });
  assert.equal(pruefeStatusAntwort({ ok: true, eingerichtet: true, kennung: "PC", stufe: "root", tunnel: "host" })?.kennung, null);
  assert.equal(pruefeStatusAntwort({ ok: true, eingerichtet: true, kennung: "PC", stufe: "root", tunnel: "host" })?.stufe, null);
  for (const schlecht of [null, "x", { ok: false, text: "nein" }, { ok: true }, { ok: true, eingerichtet: "ja", tunnel: "aus" }, { ok: true, eingerichtet: true, tunnel: "kaputt" }, { eingerichtet: true, tunnel: "aus" }]) {
    assert.equal(pruefeStatusAntwort(schlecht), null, JSON.stringify(schlecht));
  }
});

test("Hinweiszeile: nur bei wartet, Fehler und nicht verbundenem Tunnel", () => {
  assert.equal(hinweisZeile("wartet", undefined, null), "Dein Laptop wartet auf die Freigabe durch einen Admin.");
  assert.equal(hinweisZeile("fehler", "Text des Kerns", null), "Text des Kerns");
  assert.ok(hinweisZeile("fehler", undefined, null));
  assert.equal(hinweisZeile("eingerichtet", undefined, "aus"), "Die Verbindung deines Laptops zu WAMO wird aufgebaut.");
  assert.equal(hinweisZeile("eingerichtet", undefined, "host"), "Die Verbindung deines Laptops zu WAMO wird aufgebaut.");
  assert.equal(hinweisZeile("eingerichtet", undefined, "unbekannt"), "Die Verbindung deines Laptops zu WAMO wird aufgebaut.");
  assert.equal(hinweisZeile("eingerichtet", undefined, "verbunden"), null);
  assert.equal(hinweisZeile("eingerichtet", undefined, null), null);
  assert.equal(hinweisZeile("nicht_noetig", undefined, "aus"), null);
  // keine Gedankenstriche in den Texten
  for (const z of [hinweisZeile("wartet", undefined, null), hinweisZeile("eingerichtet", undefined, "aus")]) assert.ok(!/[–—]/.test(z ?? ""));
});
