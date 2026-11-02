// Tests für die Servernachricht "fernzugriff_oeffnen": WAMO bittet die Oberfläche, den Fernzugriff zu zeigen. Die Nachricht trägt nichts
// außer ihrem Typ; die Sitzung startet nur der Nutzer im Dialog, mit frischem Code. Ausführen im Ordner ui/:  npm test
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
