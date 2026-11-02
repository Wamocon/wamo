// Kern-Slots (Schritt 2.2): Slot aus der Antwort lesen, Start-Adresse bilden, 409 einer auslaufenden Instanz auswerten.
import assert from "node:assert/strict";
import { test } from "node:test";
import { aktiverSlot, alsSlot, slotAus409, startUrl } from "./kernSlot.ts";

const antwort = (status: number, text: string) => async () => ({ ok: status >= 200 && status < 300, status, text: async () => text });

test("nur a und b sind ein Slot", () => {
  assert.equal(alsSlot("a"), "a");
  assert.equal(alsSlot(" b\n"), "b");
  for (const x of ["", "c", "ab", "A", "<html>", "a/b", "../a", null, undefined, 1, {}, "a\u0000"]) assert.equal(alsSlot(x), null, String(x));
});

test("Start-Adresse mit und ohne Slot", () => {
  assert.equal(startUrl("https://192.168.178.136:55444", "b"), "https://192.168.178.136:55444/i/b/start");
  assert.equal(startUrl("https://192.168.178.136:55444/", "a"), "https://192.168.178.136:55444/i/a/start");
  assert.equal(startUrl("https://192.168.178.136:55444", null), "https://192.168.178.136:55444/start");
  assert.equal(startUrl("http://localhost:7860", null), "http://localhost:7860/start");
});

test("Der WebRTC-Transport bildet aus der Start-Adresse die Adressen der Sitzung, der Slot bleibt im Pfad", () => {
  // so bildet @pipecat-ai/small-webrtc-transport die Adresse des Offers (siehe Konzept A.2)
  const offer = startUrl("https://kern:55444", "b").replace("/start", "/sessions/abc123/api/offer");
  assert.equal(offer, "https://kern:55444/i/b/sessions/abc123/api/offer");
});

test("409 einer auslaufenden Instanz nennt den neuen Slot", () => {
  assert.equal(slotAus409(409, { slot: "b" }), "b");
  assert.equal(slotAus409(409, { slot: "a" }), "a");
  assert.equal(slotAus409(409, { slot: "x" }), null);
  assert.equal(slotAus409(409, null), null);
  assert.equal(slotAus409(409, "b"), null);
  assert.equal(slotAus409(401, { slot: "b" }), null);
  assert.equal(slotAus409(null, { slot: "b" }), null);
  assert.equal(slotAus409(undefined, undefined), null);
});

test("aktiver Slot: gute Antwort, Fehler, Unsinn und Zeitüberschreitung", async () => {
  assert.equal(await aktiverSlot("https://kern", antwort(200, "b")), "b");
  assert.equal(await aktiverSlot("https://kern/", antwort(200, "a\n")), "a");
  assert.equal(await aktiverSlot("https://kern", antwort(401, "b")), null); // älterer Server: die Zugangskontrolle antwortet
  assert.equal(await aktiverSlot("https://kern", antwort(404, "")), null);
  assert.equal(await aktiverSlot("https://kern", antwort(200, "<html>b</html>")), null);
  assert.equal(
    await aktiverSlot("https://kern", async () => {
      throw new TypeError("Failed to fetch");
    }),
    null,
  );
  const haengt = (_url: string, init?: RequestInit) =>
    new Promise<never>((_ok, nein) => init?.signal?.addEventListener("abort", () => nein(new DOMException("abgebrochen", "AbortError"))));
  assert.equal(await aktiverSlot("https://kern", haengt, 20), null);
});

test("die Anfrage ist ohne Token, ohne Cookies und ohne Zwischenspeicher", async () => {
  let gesehen: { url: string; init?: RequestInit } | null = null;
  await aktiverSlot("https://kern:55444", async (url, init) => {
    gesehen = { url, init };
    return { ok: true, status: 200, text: async () => "a" };
  });
  assert.ok(gesehen);
  const g = gesehen as { url: string; init?: RequestInit };
  assert.equal(g.url, "https://kern:55444/wamo/slot");
  assert.equal(g.init?.method, "GET");
  assert.equal(g.init?.cache, "no-store");
  assert.equal(g.init?.credentials, "omit");
  assert.equal(new Headers(g.init?.headers).get("authorization"), null);
});

test("keine Gedankenstriche im Modul", async () => {
  const { readFileSync } = await import("node:fs");
  const q = readFileSync(new URL("./kernSlot.ts", import.meta.url), "utf8");
  assert.ok(!q.includes(String.fromCharCode(0x2014)) && !q.includes(String.fromCharCode(0x2013)));
});
