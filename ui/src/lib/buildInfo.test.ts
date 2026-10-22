// Tests für die Gesundheitsprobe der Oberfläche (lib/buildInfo.ts): nur geprüfte Werte, nie ein Geheimnis.
// Ausführen im Ordner ui/:  npm test   (node:test, Node lädt TypeScript selbst)
import assert from "node:assert/strict";
import { test } from "node:test";
import { UNBEKANNT, buildInfo } from "./buildInfo.ts";

const COMMIT = "0123456789abcdef0123456789abcdef01234567";

test("gültige Werte werden unverändert übernommen", () => {
  const info = buildInfo({ version: "0.1.0", commit: COMMIT, gebaut: "2026-10-22T10:00:00Z", sauber: "ja" });
  assert.deepEqual(info, { name: "wamo-ui", version: "0.1.0", commit: COMMIT, gebaut: "2026-10-22T10:00:00Z", sauber: true });
});

test("Reihenfolge und kompakte Schreibweise sind der Vertrag mit dem Umschalt-Skript", () => {
  const text = JSON.stringify(buildInfo({ version: "12.0.7", commit: COMMIT, gebaut: "2026-10-22T10:00:00Z", sauber: "nein" }));
  assert.equal(
    text,
    `{"name":"wamo-ui","version":"12.0.7","commit":"${COMMIT}","gebaut":"2026-10-22T10:00:00Z","sauber":false}`,
  );
});

test("fehlende Werte werden unbekannt, sauber ist dann falsch", () => {
  const info = buildInfo({});
  assert.equal(info.version, UNBEKANNT);
  assert.equal(info.commit, UNBEKANNT);
  assert.equal(info.gebaut, UNBEKANNT);
  assert.equal(info.sauber, false);
});

test("ungültige Werte werden nicht durchgereicht, auch kein Geheimnis in einer falsch gesetzten Variable", () => {
  const geheim = "sk-ant-api03-GEHEIM";
  const info = buildInfo({ version: geheim, commit: geheim, gebaut: geheim, sauber: geheim });
  assert.equal(info.version, UNBEKANNT);
  assert.equal(info.commit, UNBEKANNT);
  assert.equal(info.gebaut, UNBEKANNT);
  assert.equal(info.sauber, false);
  assert.ok(!JSON.stringify(info).includes("GEHEIM"));
});

test("Muster sind eng: Großbuchstaben, zu kurze Commit-IDs, führende Nullen, Zusätze", () => {
  assert.equal(buildInfo({ commit: COMMIT.toUpperCase() }).commit, UNBEKANNT);
  assert.equal(buildInfo({ commit: COMMIT.slice(0, 39) }).commit, UNBEKANNT);
  assert.equal(buildInfo({ commit: `${COMMIT}\n` }).commit, UNBEKANNT);
  assert.equal(buildInfo({ version: "01.2.3" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: "1.2.3-rc1" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: "1.2" }).version, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-10-22 10:00:00" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-10-22T10:00:00+02:00" }).gebaut, UNBEKANNT);
});

test("sauber ist nur bei genau ja wahr", () => {
  assert.equal(buildInfo({ sauber: "ja" }).sauber, true);
  assert.equal(buildInfo({ sauber: "JA" }).sauber, false);
  assert.equal(buildInfo({ sauber: "true" }).sauber, false);
  assert.equal(buildInfo({ sauber: "" }).sauber, false);
});
