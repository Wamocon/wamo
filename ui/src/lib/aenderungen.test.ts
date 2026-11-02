// Karte "Änderungen" (Schritt 1.5): die strenge Prüfung der Antwort des Kerns, der Weg einer Anfrage und die Texte der Lage.
import assert from "node:assert/strict";
import { test } from "node:test";
import { abgeschlossenText, alsStatus, ansicht, SCHRITT_TITEL, schrittZeilen, WEG, type Stand } from "./aenderungen.ts";

const ID = "0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7b";

function stand(aenderung: Record<string, unknown> = {}): unknown {
  return {
    verfuegbar: true, modus: "geruest", version: "0.1", bereit: true, defekt: null, angehalten: null,
    anker: { erreichbar: true, sperre: "aus" }, datenbank: "angeschlossen", arbeiter: "frei", audit_kette: "ok", installation: "ok",
    anfrage: null, abgeschlossen: [], zeit: "2026-11-02T13:00:00Z", ...aenderung,
  };
}

const anfrage = (status: string, quelle = "selbst", rest: Record<string, unknown> = {}) => ({
  id: ID, status, quelle, schritt: 3, schritte: 15, seit: "2026-11-02T12:00:00Z", frist_bis: "2026-11-02T14:00:00Z", versuch: 1, fertig: false, version: null, klasse: null, ...rest,
});

const alsStand = (x: unknown): Stand => {
  const s = alsStatus(x);
  assert.ok(s && s.verfuegbar);
  return s;
};

test("eine gute Antwort wird übernommen", () => {
  const s = alsStand(stand({ anfrage: anfrage("pruefung1"), abgeschlossen: [{ id: ID, status: "abgebrochen", grund: "zeit" }] }));
  assert.equal(s.modus, "geruest");
  assert.equal(s.anker.sperre, "aus");
  assert.equal(s.anfrage?.status, "pruefung1");
  assert.deepEqual(s.abgeschlossen, [{ id: ID, status: "abgebrochen", grund: "zeit" }]);
});

test("der Kern meldet 'nicht verfügbar': mit Grund, ohne dass etwas Fremdes durchkommt", () => {
  assert.deepEqual(alsStatus({ verfuegbar: false, grund: "nicht_verfuegbar" }), { verfuegbar: false, grund: "nicht_verfuegbar" });
  assert.deepEqual(alsStatus({ verfuegbar: false, grund: "Böser Text /etc" }), { verfuegbar: false, grund: "unbekannt" });
});

test("alles, was keine Antwort des Kerns ist, ergibt null", () => {
  for (const x of [null, undefined, 5, "text", [], {}, { verfuegbar: "ja" }, { verfuegbar: 1 }]) assert.equal(alsStatus(x), null, String(x));
});

test("falsche Formen werden zu sicheren Werten, nie zu Text im Bildschirm", () => {
  const s = alsStand(
    stand({ modus: "Gerüst!", bereit: "ja", defekt: "x".repeat(300), anker: "text", datenbank: "egal", arbeiter: 7, audit_kette: "kaputt", installation: "?",
            anfrage: { id: "../../etc/passwd" }, abgeschlossen: [{ id: "x" }, 5, { id: ID, status: "A B", grund: 3 }], version: ["0.1"], zeit: 12 }),
  );
  assert.equal(s.modus, "unbekannt");
  assert.equal(s.bereit, false);
  assert.equal(s.defekt?.length, 80);
  assert.deepEqual(s.anker, { erreichbar: false, sperre: "unbekannt" });
  assert.equal(s.datenbank, "unbekannt");
  assert.equal(s.arbeiter, "unbekannt");
  assert.equal(s.audit_kette, "defekt");
  assert.equal(s.installation, "abweichung");
  assert.equal(s.anfrage, null);
  assert.deepEqual(s.abgeschlossen, [{ id: ID, status: null, grund: null }]);
  assert.equal(s.version, null);
  assert.equal(s.zeit, null);
});

test("Steuerzeichen in Texten werden entfernt", () => {
  const s = alsStand(stand({ defekt: "Fehler\u0000\u001b[31m rot\nzweite Zeile", angehalten: "halt\u0007" }));
  assert.doesNotMatch(s.defekt ?? "", /[\u0000-\u001f]/);
  assert.doesNotMatch(s.angehalten ?? "", /[\u0000-\u001f]/);
});

test("nur die letzten fünf abgeschlossenen Anfragen bleiben", () => {
  const liste = Array.from({ length: 8 }, (_, i) => ({ id: `0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7${i}`, status: "bewaehrt", grund: null }));
  assert.equal(alsStand(stand({ abgeschlossen: liste })).abgeschlossen.length, 5);
});

test("der Weg einer Anfrage: davor fertig, jetzt aktuell, danach offen", () => {
  const z = schrittZeilen(alsStand(stand({ anfrage: anfrage("pruefung1") })).anfrage);
  assert.equal(z.length, WEG.selbst.length);
  assert.deepEqual(z.map((s) => s.zustand), ["fertig", "fertig", "fertig", "aktuell", ...Array(WEG.selbst.length - 4).fill("offen")]);
  const m = schrittZeilen(alsStand(stand({ anfrage: anfrage("wartet_freigabe", "main") })).anfrage);
  assert.equal(m.length, WEG.main.length);
  assert.equal(m.find((s) => s.zustand === "aktuell")?.name, "wartet_freigabe");
});

test("ein Zustand außerhalb des Weges oder eine Anfrage ohne Quelle ergibt keinen Weg", () => {
  assert.deepEqual(schrittZeilen(alsStand(stand({ anfrage: anfrage("review", "main") })).anfrage), []);   // review gibt es nur bei "selbst"
  assert.deepEqual(schrittZeilen(alsStand(stand({ anfrage: anfrage("pruefung1", "anderes") })).anfrage), []);
  assert.deepEqual(schrittZeilen(null), []);
});

test("jeder Zustand der Wege hat einen Titel", () => {
  for (const weg of Object.values(WEG)) for (const n of weg) assert.ok(SCHRITT_TITEL[n], n);
  assert.ok(SCHRITT_TITEL.zurueckgenommen && SCHRITT_TITEL.abgebrochen);
});

test("Lage: die Reihenfolge der Gründe", () => {
  // nicht erreichbar vor allem
  assert.equal(ansicht(null, "Kern nicht erreichbar.").lageText, "Kern nicht erreichbar.");
  assert.equal(ansicht({ verfuegbar: false, grund: "nicht_verfuegbar" }).lage, "nicht_erreichbar");
  assert.match(ansicht({ verfuegbar: false, grund: "kein_zugang" }).lageText, /Rechte des Sockets/);
  assert.match(ansicht({ verfuegbar: false, grund: "etwas Neues" }).lageText, /nicht erreichbar/);
  // Störung vor Sperre vor Arbeit
  assert.equal(ansicht(alsStand(stand({ defekt: "Zustandsdatei", anker: { erreichbar: true, sperre: "an" } }))).lage, "stoerung");
  assert.equal(ansicht(alsStand(stand({ audit_kette: "defekt" }))).lage, "stoerung");
  assert.equal(ansicht(alsStand(stand({ anker: { erreichbar: true, sperre: "an" }, anfrage: anfrage("pruefung1") }))).lage, "gesperrt");
  assert.match(ansicht(alsStand(stand({ anker: { erreichbar: false, sperre: "unbekannt" } }))).lageText, /nicht erreichbar/);
  assert.equal(ansicht(alsStand(stand({ anker: { erreichbar: true, sperre: "defekt" } }))).lage, "gesperrt");   // eine kaputte Sperre gilt als gesetzt
  // frei
  assert.equal(ansicht(alsStand(stand())).lage, "bereit");
  assert.equal(ansicht(alsStand(stand())).lageText, "Keine offene Änderung.");
  const arbeit = ansicht(alsStand(stand({ anfrage: anfrage("pruefung1") })));
  assert.equal(arbeit.lage, "arbeit");
  assert.match(arbeit.lageText, /Schritt 4 von 15/);
  assert.match(arbeit.lageText, /Prüfung 1/);
  const wartet = ansicht(alsStand(stand({ anfrage: anfrage("wartet_freigabe") })));
  assert.equal(wartet.lage, "wartet");
  assert.equal(wartet.wartetAufFreigabe, true);
  assert.equal(ansicht(alsStand(stand({ anfrage: anfrage("pruefung1") }))).wartetAufFreigabe, false);
});

test("bei einer Sperre wartet nichts auf Freigabe (es geht ohnehin nichts weiter)", () => {
  const a = ansicht(alsStand(stand({ anker: { erreichbar: true, sperre: "an" }, anfrage: anfrage("wartet_freigabe") })));
  assert.equal(a.lage, "gesperrt");
  assert.equal(a.wartetAufFreigabe, false);
});

test("Hinweise: Gerüstmodus, angehalten, Installation, fehlender Diff", () => {
  const h = ansicht(alsStand(stand({ angehalten: "Wartung", installation: "abweichung", anfrage: anfrage("pruefung1") }))).hinweise.join(" ");
  assert.match(h, /Gerüstmodus/);
  assert.match(h, /angehalten: Wartung/);
  assert.match(h, /Prüfsumme/);
  assert.match(h, /Diff, Wirkungsflächen und Versionsvorschlag liefert der Release-Dienst noch nicht/);
  assert.deepEqual(ansicht(alsStand(stand({ modus: "betrieb" }))).hinweise, []);
});

test("die Zeilen nennen Sperre, Datenbank, Arbeiter und Protokoll", () => {
  const z = Object.fromEntries(ansicht(alsStand(stand({ datenbank: "fehlt", arbeiter: "haengt" }))).zeilen.map((x) => [x.name, x.wert]));
  assert.equal(z["Release-Dienst"], "läuft, Version 0.1");
  assert.equal(z["Sperre des Notankers"], "aufgehoben");
  assert.equal(z["Datenbank"], "fehlt");
  assert.equal(z["Arbeiter"], "hängt");
  assert.equal(z["Protokoll (Hashkette)"], "in Ordnung");
});

test("die Anfrage zeigt Quelle, Kurzkennung und Weg", () => {
  const a = ansicht(alsStand(stand({ anfrage: anfrage("pruefung1", "main") }))).anfrage;
  assert.equal(a?.kurz, "0b9f2c1e");
  assert.match(a?.quelleText ?? "", /main/);
  assert.ok((a?.schritte.length ?? 0) > 0);
});

test("abgeschlossene Anfragen als Zeile", () => {
  assert.equal(abgeschlossenText({ id: ID, status: "abgebrochen", grund: "zeit" }), "0b9f2c1e: Abgebrochen (Grund: zeit)");
  assert.equal(abgeschlossenText({ id: ID, status: null, grund: null }), "0b9f2c1e: unbekannt");
});

test("keine Gedankenstriche in den Texten der Karte", async () => {
  const { readFileSync } = await import("node:fs");
  const q = readFileSync(new URL("./aenderungen.ts", import.meta.url), "utf8");
  assert.ok(!q.includes("—") && !q.includes("–"));
});
