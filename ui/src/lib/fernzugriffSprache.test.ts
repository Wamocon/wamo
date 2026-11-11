// Tests für die Servernachricht "fernzugriff_oeffnen": WAMO bittet die Oberfläche, den Fernzugriff zu zeigen. Die Nachricht trägt nichts
// außer ihrem Typ; die Sitzung startet nur der Nutzer, mit einem echten Klick auf Verbinden (Befund M7: Seit KP-2 gibt es meist kein
// Codefeld mehr, das war früher die Schranke). Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseServerMessage } from "./nachrichten.ts";

test("die Nachricht wird erkannt und trägt nur den Typ", () => {
  assert.deepEqual(parseServerMessage({ type: "fernzugriff_oeffnen" }), { type: "fernzugriff_oeffnen" });
});

test("Zusatzfelder aus dem Kern kommen nie bis zur Oberfläche (kein Ziel, keine Adresse, kein Token)", () => {
  const roh = { type: "fernzugriff_oeffnen", ziel: "sokrates2", url: "https://192.168.178.136:55446/x", token: "geheim", sitzung: "abc" };
  const m = parseServerMessage(roh);
  assert.deepEqual(m, { type: "fernzugriff_oeffnen" });
  assert.ok(!JSON.stringify(m).includes("geheim"));
});

test("ähnliche Typen und fehlerhafte Formen werden weiter ignoriert", () => {
  for (const typ of ["fernzugriff", "fernzugriff_starten", "Fernzugriff_oeffnen", "fernzugriff_oeffnen ", ""]) {
    assert.equal(parseServerMessage({ type: typ }), null, typ);
  }
  for (const roh of [null, undefined, "fernzugriff_oeffnen", 5, [], { typ: "fernzugriff_oeffnen" }]) {
    assert.equal(parseServerMessage(roh), null);
  }
});

test("die Behandlung in useWamo öffnet nur den Abschnitt und startet nichts", async () => {
  const { readFile } = await import("node:fs/promises");
  const quelle = await readFile(new URL("./useWamo.ts", import.meta.url), "utf8");
  const stelle = quelle.slice(quelle.indexOf('msg.type === "fernzugriff_oeffnen"'));
  const zweig = stelle.slice(0, stelle.indexOf("} else if", 10));
  assert.match(zweig, /einstellungenOeffnen\("fernzugriff"\)/);
  assert.doesNotMatch(zweig, /starten\(|fernzugriffApi|fetch\(/);
});

test("Befund M7: die Behandlung der Nachricht startet nie, öffnet keinen Dialog und fragt keinen Code", async () => {
  const { readFile } = await import("node:fs/promises");
  const quelle = await readFile(new URL("./useWamo.ts", import.meta.url), "utf8");
  const stelle = quelle.slice(quelle.indexOf('msg.type === "fernzugriff_oeffnen"'));
  const zweig = stelle.slice(0, stelle.indexOf("} else if", 10));
  // nur den Abschnitt zeigen: genau eine Anweisung im Zweig, keiner der Wege, die eine Sitzung beginnen oder den Dialog öffnen
  const anweisungen = zweig.split(/\r?\n/).slice(1).map((z) => z.trim()).filter((z) => z && !z.startsWith("//") && z !== "}");
  assert.deepEqual(anweisungen, ['einstellungenOeffnen("fernzugriff");']);
  assert.doesNotMatch(zweig, /codeBestaetigen|requestSubmit|\.click\(|\.submit\(|showModal|rd\/start|kernJson/);
  // die Nachricht trägt keine Nutzdaten (nachrichten.ts)
  const nachrichten = await readFile(new URL("./nachrichten.ts", import.meta.url), "utf8");
  assert.match(nachrichten, /case "fernzugriff_oeffnen":\s*return \{ type: "fernzugriff_oeffnen" \};/);
});

test("Befund M7: der Dialog setzt den Fokus nie auf Verbinden und hat kein autoFocus", async () => {
  const { readFile } = await import("node:fs/promises");
  const quelle = await readFile(new URL("../components/einstellungen/Fernzugriff.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(quelle, /autoFocus|autofocus/i);
  assert.doesNotMatch(quelle, /requestSubmit|\.submit\(\)/);
  assert.match(quelle, /startFokus\(/);
});
