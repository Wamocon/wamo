// Tests für die Gesundheitsprobe der Oberfläche (lib/buildInfo.ts): nur geprüfte Werte, nie ein Geheimnis.
// Ausführen im Ordner ui/:  npm test   (node:test, Node lädt TypeScript selbst)
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { UNBEKANNT, buildInfo } from "./buildInfo.ts";

const COMMIT = "0123456789abcdef0123456789abcdef01234567";
const ZEIT = "2026-10-22T10:00:00Z";

test("gültige Werte werden unverändert übernommen", () => {
  const info = buildInfo({ version: "0.1.0", commit: COMMIT, gebaut: ZEIT, sauber: "ja" });
  assert.deepEqual(info, { name: "wamo-ui", version: "0.1.0", commit: COMMIT, gebaut: ZEIT, sauber: true });
});

test("Reihenfolge und kompakte Schreibweise sind der Vertrag mit dem Umschalt-Skript", () => {
  const text = JSON.stringify(buildInfo({ version: "12.0.7", commit: COMMIT, gebaut: ZEIT, sauber: "nein" }));
  assert.equal(text, `{"name":"wamo-ui","version":"12.0.7","commit":"${COMMIT}","gebaut":"${ZEIT}","sauber":false}`);
  assert.deepEqual(Object.keys(buildInfo({})), ["name", "version", "commit", "gebaut", "sauber"]);
});

test("fehlende Werte werden unbekannt, sauber ist dann falsch", () => {
  const info = buildInfo({});
  assert.equal(info.version, UNBEKANNT);
  assert.equal(info.commit, UNBEKANNT);
  assert.equal(info.gebaut, UNBEKANNT);
  assert.equal(info.sauber, false);
});

test("der Standardwert des Dockerfiles (unbekannt) bleibt unbekannt", () => {
  const info = buildInfo({ version: "0.1.0", commit: "unbekannt", gebaut: "unbekannt", sauber: "nein" });
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

test("Muster sind eng: Großbuchstaben, zu kurze Commit-IDs, führende Nullen, Zusätze, Umbrüche, Leerzeichen", () => {
  assert.equal(buildInfo({ commit: COMMIT.toUpperCase() }).commit, UNBEKANNT);
  assert.equal(buildInfo({ commit: COMMIT.slice(0, 39) }).commit, UNBEKANNT);
  assert.equal(buildInfo({ commit: `${COMMIT}\n` }).commit, UNBEKANNT);
  assert.equal(buildInfo({ commit: ` ${COMMIT}` }).commit, UNBEKANNT);
  assert.equal(buildInfo({ version: "01.2.3" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: "1.2.3-rc1" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: "1.2" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: "10000.0.0" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: "0.1.0\n" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: " 0.1.0" }).version, UNBEKANNT);
  assert.equal(buildInfo({ version: "١.٢.٣" }).version, UNBEKANNT); // arabisch-indische Ziffern
  assert.equal(buildInfo({ gebaut: "2026-10-22 10:00:00" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-10-22T10:00:00+02:00" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-10-22T10:00:00.000Z" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: `${ZEIT}\n` }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-99-99T99:99:99Z" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-13-01T00:00:00Z" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-10-32T00:00:00Z" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-10-22T24:00:00Z" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-10-22T23:59:60Z" }).gebaut, UNBEKANNT);
  assert.equal(buildInfo({ gebaut: "2026-12-31T23:59:59Z" }).gebaut, "2026-12-31T23:59:59Z");
});

test("sauber ist nur bei genau ja wahr und nur mit gültiger Commit-ID", () => {
  assert.equal(buildInfo({ commit: COMMIT, sauber: "ja" }).sauber, true);
  assert.equal(buildInfo({ commit: COMMIT, sauber: "JA" }).sauber, false);
  assert.equal(buildInfo({ commit: COMMIT, sauber: "ja\n" }).sauber, false);
  assert.equal(buildInfo({ commit: COMMIT, sauber: "true" }).sauber, false);
  assert.equal(buildInfo({ commit: COMMIT, sauber: "" }).sauber, false);
  assert.equal(buildInfo({ sauber: "ja" }).sauber, false);
  assert.equal(buildInfo({ commit: "kaputt", sauber: "ja" }).sauber, false);
});

// Vertrag mit infra/sokrates2/wamo-ui-umschalten.sh: Das Skript liest die Antwort mit einem strengen Muster (VERSION_JSON_RE).
// Dieser Test holt das Muster aus dem Skript und prüft, dass jede Antwort der Route darauf passt. Im Docker-Kontext (nur ui/)
// gibt es das Skript nicht; dann entfällt der Test.
const SKRIPT = new URL("../../../infra/sokrates2/wamo-ui-umschalten.sh", import.meta.url);
test("jede Antwort der Route passt auf das Muster des Umschalt-Skripts", { skip: !existsSync(SKRIPT) }, () => {
  const text = readFileSync(SKRIPT, "utf8");
  const m = /^VERSION_JSON_RE='(.+)'$/m.exec(text);
  assert.ok(m, "VERSION_JSON_RE im Skript nicht gefunden");
  // bash-ERE und JavaScript-Regex stimmen für dieses Muster überein (Klammern, Zeichenklassen, \{ und \})
  const muster = new RegExp(m[1]);
  const faelle = [
    { version: "0.1.0", commit: COMMIT, gebaut: ZEIT, sauber: "ja" },
    { version: "12.0.7", commit: COMMIT, gebaut: ZEIT, sauber: "nein" },
    { version: "0.1.0", commit: "unbekannt", gebaut: "unbekannt", sauber: "nein" },
  ];
  for (const roh of faelle) {
    const antwort = JSON.stringify(buildInfo(roh));
    assert.ok(muster.test(antwort), `passt nicht: ${antwort}`);
  }
  // Ohne gültige Version (Build ohne package.json-Version) ist die Antwort für das Skript ungültig: Es schaltet nicht um
  assert.ok(!muster.test(JSON.stringify(buildInfo({}))));
  // und das Muster lehnt Abweichungen ab
  assert.ok(!muster.test(`${JSON.stringify(buildInfo({ version: "0.1.0" }))} `));
  assert.ok(!muster.test(JSON.stringify({ ...buildInfo({ version: "0.1.0" }), extra: 1 })));
});
