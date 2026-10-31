// Tests der Geräteverwaltung für Admins (lib/geraeteVerwaltung.ts): Prüfung der Antworten, Sortierung, Statustexte,
// Bestätigungsregeln, Meldungen und die Anzeige der Hinweise. Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  aktionenFuer,
  aktionKnopf,
  alsGeraete,
  bestaetigungFuer,
  codePruefen,
  CODE_ZU_ALT,
  ereignisText,
  eingabePruefen,
  erfolgText,
  fehlerBehandlung,
  fingerabdruckGruppen,
  fingerabdruckText,
  grundPruefen,
  hinweisTextAnzeige,
  istDieserLaptop,
  istGeraeteHinweis,
  sauber,
  sortiere,
  STATUS_INFO,
  VERGLEICH_HINWEIS,
  wartende,
  zeitText,
  type Eingabe,
  type Geraet,
  type GeraeteStatus,
} from "./geraeteVerwaltung.ts";

const FP = "SHA256:vxe+7I/Wv6VHjKAOQpvJtkZDcHvINq3CgURh2MotlRg";

function roh(ueber: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    kennung: "pc-anna-pc",
    name: "Annas Laptop",
    rechnername: "ANNA-PC",
    profil: { name: "Anna", aktiv: true },
    status: "beantragt",
    bindung: "admin",
    fingerabdruck: FP,
    erstellt: "2026-10-30T08:00:00+00:00",
    freigegeben: null,
    gesperrt: null,
    grund: null,
    zuletzt_gesehen: null,
    ereignisse: [],
    eigen: false,
    ...ueber,
  };
}

function geraet(ueber: Partial<Geraet> = {}): Geraet {
  const g = alsGeraete([roh()])[0];
  return { ...g, ...ueber };
}

const eingabe = (ueber: Partial<Eingabe> = {}): Eingabe => ({ grund: "", verglichen: true, code: "123456", ...ueber });

// ---------- Antworten prüfen ----------
test("alsGeraete übernimmt eine gültige Zeile", () => {
  const [g] = alsGeraete([roh()]);
  assert.equal(g.kennung, "pc-anna-pc");
  assert.equal(g.profilName, "Anna");
  assert.equal(g.fingerabdruck, FP);
  assert.equal(g.status, "beantragt");
  assert.equal(g.eigen, false);
});

test("alsGeraete verwirft Zeilen mit falscher Kennung oder falschem Status und behält den Rest", () => {
  const liste = alsGeraete([
    roh({ kennung: "../etc" }),
    roh({ kennung: "PC-ANNA" }),
    roh({ kennung: "pc-" + "a".repeat(30) }),
    roh({ status: "aktiv?" }),
    "text",
    null,
    roh({ kennung: "pc-ok" }),
  ]);
  assert.deepEqual(
    liste.map((g) => g.kennung),
    ["pc-ok"],
  );
  assert.deepEqual(alsGeraete("kein array"), []);
  assert.deepEqual(alsGeraete(undefined), []);
});

test("alsGeraete: ungültiger Fingerabdruck wird null, Texte werden bereinigt und gekürzt", () => {
  const g = alsGeraete([roh({ fingerabdruck: "SHA256:zu-kurz", name: "A\u0000B\u202eC" + "x".repeat(300), profil: { name: "<b>Anna</b>\n", aktiv: 1 } })])[0];
  assert.equal(g.fingerabdruck, null);
  assert.equal(g.name.length, 100);
  assert.ok(!/[\u0000\u202e]/.test(g.name));
  assert.equal(g.profilName, "<b>Anna</b>"); // als Text, nie als HTML angezeigt
  assert.equal(g.profilAktiv, false); // nur ein echtes true zählt
});

test("alsGeraete: Verlauf wird geprüft und auf zehn Einträge begrenzt", () => {
  const ereignisse = Array.from({ length: 15 }, () => ({ von: "beantragt", nach: "aktiv", durch: "Admin", grund: null, at: "2026-10-30T09:00:00+00:00" }));
  const g = alsGeraete([roh({ ereignisse: [...ereignisse, 5, "x"] })])[0];
  assert.equal(g.ereignisse.length, 10);
  const komisch = alsGeraete([roh({ ereignisse: [{ von: "irgendwas", nach: "aktiv", at: "morgen" }] })])[0];
  assert.deepEqual(komisch.ereignisse[0], { von: null, nach: "aktiv", durch: null, grund: null, at: null });
});

test("sauber kürzt, entfernt Steuerzeichen und macht Leeres zu null", () => {
  assert.equal(sauber("  a\r\nb\tc  ", 10), "a b c");
  assert.equal(sauber("", 10), null);
  assert.equal(sauber("   ", 10), null);
  assert.equal(sauber(5, 10), null);
  assert.equal(sauber("abcdef", 3), "abc");
});

// ---------- Sortierung ----------
test("sortiere: wartende zuerst (das älteste Gesuch oben), dann aktiv, gesperrt, widerrufen", () => {
  const mk = (kennung: string, status: GeraeteStatus, erstellt: string, profil: string) =>
    alsGeraete([roh({ kennung, status, erstellt, profil: { name: profil, aktiv: true } })])[0];
  const liste = [
    mk("pc-w", "widerrufen", "2026-10-01T00:00:00Z", "Anna"),
    mk("pc-a2", "aktiv", "2026-10-02T00:00:00Z", "Bernd"),
    mk("pc-b2", "beantragt", "2026-10-30T00:00:00Z", "Clara"),
    mk("pc-g", "gesperrt", "2026-10-03T00:00:00Z", "Anna"),
    mk("pc-a1", "aktiv", "2026-10-02T00:00:00Z", "Anna"),
    mk("pc-b1", "beantragt", "2026-10-28T00:00:00Z", "Dora"),
  ];
  const vorher = liste.map((g) => g.kennung);
  const sortiert = sortiere(liste);
  assert.deepEqual(
    sortiert.map((g) => g.kennung),
    ["pc-b1", "pc-b2", "pc-a1", "pc-a2", "pc-g", "pc-w"],
  );
  assert.deepEqual(
    liste.map((g) => g.kennung),
    vorher,
    "die Eingabe bleibt unverändert",
  );
  assert.deepEqual(wartende(sortiert).map((g) => g.kennung), ["pc-b1", "pc-b2"]);
});

test("sortiere kommt mit fehlenden Namen und Zeiten zurecht", () => {
  const a = geraet({ kennung: "pc-a", status: "beantragt", erstellt: null, profilName: null });
  const b = geraet({ kennung: "pc-b", status: "beantragt", erstellt: "2026-10-30T00:00:00Z" });
  assert.equal(sortiere([b, a]).length, 2);
});

// ---------- Statusanzeige ----------
test("jeder Status hat Wort, Form und Erklärung, und die Formen unterscheiden sich (nicht nur Farbe)", () => {
  const status = Object.keys(STATUS_INFO) as GeraeteStatus[];
  assert.equal(status.length, 4);
  assert.equal(new Set(status.map((s) => STATUS_INFO[s].form)).size, 4);
  assert.equal(new Set(status.map((s) => STATUS_INFO[s].wort)).size, 4);
  for (const s of status) assert.ok(STATUS_INFO[s].erklaerung.length > 10);
  assert.equal(STATUS_INFO.beantragt.wort, "Wartet auf Freigabe");
});

test("Fingerabdruck in lesbaren Gruppen", () => {
  const g = fingerabdruckGruppen(FP);
  assert.ok(g);
  assert.equal(g.praefix, "SHA256:");
  assert.equal(g.gruppen.length, 11);
  assert.equal(g.gruppen[0], "vxe+");
  assert.equal(g.gruppen[10].length, 3);
  assert.equal(g.gruppen.join(""), FP.slice(7));
  assert.equal(fingerabdruckText(FP), "SHA256: vxe+ 7I/W v6VH jKAO QpvJ tkZD cHvI Nq3C gURh 2Mot lRg");
  assert.equal(fingerabdruckGruppen(null), null);
  assert.equal(fingerabdruckGruppen("SHA256:kurz"), null);
  assert.equal(fingerabdruckText(null), "unbekannt");
});

test("Zeit auf Deutsch", () => {
  assert.equal(zeitText("2026-10-30T08:05:00Z", "UTC"), "30.10.26, 08:05 Uhr");
  assert.equal(zeitText(null), "unbekannt");
  assert.equal(zeitText("kein datum"), "unbekannt");
});

test("Verlauf als Satz", () => {
  const t = "2026-10-30T08:05:00Z";
  assert.equal(ereignisText({ von: null, nach: "beantragt", durch: null, grund: null, at: t }, "UTC"), "30.10.26, 08:05 Uhr: beantragt");
  assert.equal(ereignisText({ von: null, nach: "aktiv", durch: null, grund: "automatisch gebunden", at: null }), "automatisch gebunden und aktiv, automatisch gebunden");
  assert.equal(ereignisText({ von: "beantragt", nach: "aktiv", durch: "Admin Eins", grund: null, at: null }), "freigegeben von Admin Eins");
  assert.equal(ereignisText({ von: "gesperrt", nach: "aktiv", durch: "Admin Eins", grund: null, at: null }), "entsperrt von Admin Eins");
  assert.equal(ereignisText({ von: "aktiv", nach: "gesperrt", durch: "A", grund: "Laptop verloren", at: null }), "gesperrt von A, Laptop verloren");
  assert.equal(ereignisText({ von: "beantragt", nach: "widerrufen", durch: "A", grund: "unbekannt", at: null }), "abgelehnt von A, unbekannt");
  assert.equal(ereignisText({ von: "aktiv", nach: "widerrufen", durch: "A", grund: null, at: null }), "widerrufen von A");
  assert.equal(ereignisText({ von: "aktiv", nach: "aktiv", durch: null, grund: "Rechnername A -> B", at: null }), "Rechnername geändert, Rechnername A -> B");
});

test("dieses Gerät wird an der Kennung erkannt", () => {
  assert.equal(istDieserLaptop({ kennung: "pc-a" }, "pc-a"), true);
  assert.equal(istDieserLaptop({ kennung: "pc-a" }, "pc-b"), false);
  assert.equal(istDieserLaptop({ kennung: "pc-a" }, null), false);
});

// ---------- Aktionen ----------
test("welche Aktionen es je Status gibt (genau die erlaubten Übergänge der Datenbank)", () => {
  assert.deepEqual(aktionenFuer({ status: "beantragt" }), ["freigeben", "widerrufen"]);
  assert.deepEqual(aktionenFuer({ status: "aktiv" }), ["sperren", "widerrufen"]);
  assert.deepEqual(aktionenFuer({ status: "gesperrt" }), ["entsperren", "widerrufen"]);
  assert.deepEqual(aktionenFuer({ status: "widerrufen" }), []);
  assert.equal(aktionKnopf({ status: "beantragt" }, "widerrufen"), "Ablehnen");
  assert.equal(aktionKnopf({ status: "aktiv" }, "widerrufen"), "Widerrufen");
  assert.equal(aktionKnopf({ status: "beantragt" }, "freigeben"), "Freigeben");
});

test("Freigeben: Fingerabdruck erneut anzeigen, Vergleich am Laptop verlangen, kein Grund nötig", () => {
  const b = bestaetigungFuer(geraet(), "freigeben", { dieserLaptop: false });
  assert.equal(b.fingerabdruck, FP);
  assert.equal(b.vergleichNoetig, true);
  assert.equal(b.grundNoetig, false);
  assert.equal(b.endgueltig, false);
  assert.ok(b.beschreibung.includes(VERGLEICH_HINWEIS));
  assert.ok(b.beschreibung.includes("pc-anna-pc") && b.beschreibung.includes("Anna"));
  assert.deepEqual(b.warnungen, []);
});

test("Freigeben des eigenen Geräts: dieselbe Bestätigung plus Warnung, nie ohne Anzeige", () => {
  const b = bestaetigungFuer(geraet({ eigen: true }), "freigeben", { dieserLaptop: true });
  assert.equal(b.vergleichNoetig, true);
  assert.equal(b.fingerabdruck, FP);
  assert.ok(b.warnungen.some((w) => w.includes("eigenen Profils")));
  // ohne Häkchen geht nichts durch, auch nicht mit gültigem Code
  const ohne = eingabePruefen(b, eingabe({ verglichen: false }));
  assert.equal(ohne.ok, false);
  assert.equal(!ohne.ok && ohne.feld, "verglichen");
  // ohne bekannten Fingerabdruck kann man nicht freigeben
  const blind = bestaetigungFuer(geraet({ fingerabdruck: null }), "freigeben", { dieserLaptop: false });
  assert.equal(blind.fingerabdruck, null);
  const r = eingabePruefen(blind, eingabe());
  assert.equal(r.ok, false);
});

test("Entsperren verlangt ebenfalls den Vergleich; ein deaktiviertes Profil wird gemeldet", () => {
  const b = bestaetigungFuer(geraet({ status: "gesperrt", profilAktiv: false }), "entsperren", { dieserLaptop: false });
  assert.equal(b.vergleichNoetig, true);
  assert.ok(b.warnungen.some((w) => w.includes("deaktiviert")));
});

test("Sperren und Widerrufen brauchen einen Grund; Widerrufen ist endgültig und heißt beim Antrag Ablehnen", () => {
  const s = bestaetigungFuer(geraet({ status: "aktiv" }), "sperren", { dieserLaptop: false });
  assert.equal(s.grundNoetig, true);
  assert.equal(s.vergleichNoetig, false);
  assert.equal(s.endgueltig, false);
  const w = bestaetigungFuer(geraet({ status: "aktiv" }), "widerrufen", { dieserLaptop: false });
  assert.equal(w.endgueltig, true);
  assert.equal(w.knopf, "Endgültig widerrufen");
  const a = bestaetigungFuer(geraet({ status: "beantragt" }), "widerrufen", { dieserLaptop: false });
  assert.equal(a.titel, "Antrag ablehnen");
  assert.equal(a.knopf, "Ablehnen");
  assert.equal(a.grundNoetig, true);
});

test("Sperren des Laptops, an dem man sitzt, warnt deutlich; eigenes anderes Gerät warnt knapper", () => {
  const hier = bestaetigungFuer(geraet({ status: "aktiv", eigen: true }), "sperren", { dieserLaptop: true });
  assert.ok(hier.warnungen.some((x) => x.includes("an dem du gerade arbeitest")));
  const anderes = bestaetigungFuer(geraet({ status: "aktiv", eigen: true }), "sperren", { dieserLaptop: false });
  assert.ok(anderes.warnungen.some((x) => x.includes("eines deiner eigenen Geräte")));
  const fremd = bestaetigungFuer(geraet({ status: "aktiv" }), "sperren", { dieserLaptop: false });
  assert.deepEqual(fremd.warnungen, []);
});

test("Eingabe prüfen: Reihenfolge der Meldungen und Bereinigung", () => {
  const sperren = bestaetigungFuer(geraet({ status: "aktiv" }), "sperren", { dieserLaptop: false });
  const ohneGrund = eingabePruefen(sperren, eingabe());
  assert.equal(!ohneGrund.ok && ohneGrund.feld, "grund");
  const kurz = eingabePruefen(sperren, eingabe({ grund: "ab" }));
  assert.equal(kurz.ok, false);
  const ohneCode = eingabePruefen(sperren, eingabe({ grund: "Laptop verloren", code: "12" }));
  assert.equal(!ohneCode.ok && ohneCode.feld, "code");
  const gut = eingabePruefen(sperren, eingabe({ grund: "  Laptop   verloren ", code: "123 456" }));
  assert.deepEqual(gut, { ok: true, grund: "Laptop verloren", code: "123456" });
  // Freigeben braucht keinen Grund; ein freiwilliger wird mitgenommen
  const frei = bestaetigungFuer(geraet(), "freigeben", { dieserLaptop: false });
  assert.deepEqual(eingabePruefen(frei, eingabe()), { ok: true, grund: null, code: "123456" });
  const verglichenFehlt = eingabePruefen(frei, eingabe({ verglichen: false, code: "" }));
  assert.equal(!verglichenFehlt.ok && verglichenFehlt.feld, "verglichen"); // zuerst der Vergleich
});

test("Grund und Code einzeln", () => {
  assert.deepEqual(grundPruefen("", false), { ok: true, wert: null });
  assert.deepEqual(grundPruefen("  x  y ", false), { ok: true, wert: "x y" });
  assert.equal(grundPruefen("ab", true).ok, false);
  assert.equal(grundPruefen("abc", true).ok, true);
  assert.equal(grundPruefen("x".repeat(201), false).ok, false);
  assert.equal(grundPruefen("x".repeat(200), true).ok, true);
  assert.equal(grundPruefen("a\u0000b", true).ok, false);
  assert.equal(grundPruefen("a\u202eb", true).ok, false);
  assert.equal(codePruefen("123456"), "123456");
  assert.equal(codePruefen(" 123 456 "), "123456");
  for (const x of ["12345", "1234567", "abcdef", "12 34 5a", "", "１２３４５６"]) assert.equal(codePruefen(x), null);
});

// ---------- Meldungen ----------
test("Erfolgsmeldungen nennen das Gerät", () => {
  const g = { name: "Annas Laptop", kennung: "pc-anna-pc", status: "aktiv" as GeraeteStatus };
  assert.equal(erfolgText(g, "freigeben", "ok"), "Annas Laptop (pc-anna-pc) ist freigegeben.");
  assert.equal(erfolgText(g, "entsperren", "ok"), "Annas Laptop (pc-anna-pc) ist wieder freigegeben.");
  assert.equal(erfolgText(g, "sperren", "ok"), "Annas Laptop (pc-anna-pc) ist gesperrt.");
  assert.equal(erfolgText(g, "widerrufen", "ok"), "Die Freigabe von Annas Laptop (pc-anna-pc) ist widerrufen.");
  assert.equal(erfolgText({ ...g, status: "beantragt" }, "widerrufen", "ok"), "Der Antrag für Annas Laptop (pc-anna-pc) ist abgelehnt.");
  assert.equal(erfolgText(g, "sperren", "schon"), "Annas Laptop (pc-anna-pc) ist schon gesperrt. Es wurde nichts geändert.");
});

test("Fehlerbehandlung je Status", () => {
  const alt = fehlerBehandlung({ status: 403, text: "Bitte gib den Code noch einmal ein.", code: CODE_ZU_ALT });
  assert.deepEqual([alt.dialogOffen, alt.neuLaden, alt.feld], [true, false, "code"]);
  const ungueltig = fehlerBehandlung({ status: 400, text: "Bitte einen Grund angeben (mindestens 3 Zeichen)" });
  assert.deepEqual([ungueltig.dialogOffen, ungueltig.feld], [true, "grund"]);
  const konflikt = fehlerBehandlung({ status: 409, text: "Das geht nicht: pc-a ist gesperrt." });
  assert.deepEqual([konflikt.dialogOffen, konflikt.neuLaden], [false, true]);
  const unbekannt = fehlerBehandlung({ status: 404, text: "Ein Gerät mit dieser Kennung gibt es nicht." });
  assert.equal(unbekannt.neuLaden, true);
  const keineAntwort = fehlerBehandlung({ status: 0, text: "Zeitüberschreitung" });
  assert.equal(keineAntwort.neuLaden, true);
  assert.ok(keineAntwort.text.includes("Ob die Aktion gelaufen ist"), "bei fehlender Antwort wird nichts behauptet");
  assert.equal(fehlerBehandlung({ status: 503, text: "Datenbank gerade nicht erreichbar" }).neuLaden, true);
  const verboten = fehlerBehandlung({ status: 403, text: "Dir fehlt die Berechtigung" });
  assert.deepEqual([verboten.dialogOffen, verboten.neuLaden], [false, false]);
  assert.equal(fehlerBehandlung({ status: 429, text: "Zu viele Anfragen" }).dialogOffen, false);
  assert.equal(fehlerBehandlung({ status: 409, text: "" }).text, "Unbekannter Fehler.");
});

// ---------- Hinweise ----------
test("Hinweistexte aus Migration 0002 nennen die Karte statt des fehlenden Skripts", () => {
  const beantragt =
    "Admin-Profile gibt ein Admin frei. Kennung pc-admin1-pc, Fingerabdruck SHA256:abc. Freigeben: wamo_geraete.py freigeben --kennung pc-admin1-pc.";
  const neu = hinweisTextAnzeige(beantragt);
  assert.ok(!neu.includes("wamo_geraete.py"));
  assert.ok(neu.includes("Freigeben oder ablehnen: Einstellungen, Geräte und PC, Karte Geräteverwaltung."));
  assert.ok(neu.startsWith("Admin-Profile gibt ein Admin frei. Kennung pc-admin1-pc, Fingerabdruck SHA256:abc."));
  const gebunden = hinweisTextAnzeige("Automatisch bei der Anmeldung gebunden. Sperren: wamo_geraete.py sperren --kennung pc-x-1.");
  assert.ok(gebunden.endsWith("Sperren: Einstellungen, Geräte und PC, Karte Geräteverwaltung."));
  assert.ok(!hinweisTextAnzeige("Mehr: wamo_geraete.py widerrufen --kennung pc-y.").includes("wamo_geraete"));
  // andere Texte bleiben, wie sie sind
  assert.equal(hinweisTextAnzeige("Anna hat ein Dokument geteilt."), "Anna hat ein Dokument geteilt.");
  assert.equal(hinweisTextAnzeige(""), "");
});

test("welche Hinweise zur Geräteverwaltung führen", () => {
  for (const art of ["geraet_beantragt", "geraet_gebunden", "geraet_konflikt"]) assert.equal(istGeraeteHinweis(art), true);
  for (const art of ["geraet_gesperrt", "teilung_erhalten", "scan_fertig", "system", ""]) assert.equal(istGeraeteHinweis(art), false);
});

// ---------- Verbotsproben ----------
test("Texte der Oberfläche enthalten keine Gedankenstriche", async () => {
  const { readFileSync } = await import("node:fs");
  const quelle = readFileSync(new URL("./geraeteVerwaltung.ts", import.meta.url), "utf8");
  const striche = [8211, 8212, 8210, 8213].map((c) => String.fromCharCode(c));
  assert.ok(!striche.some((z) => quelle.includes(z)), "lange Striche sind nirgends erlaubt");
  // Zeichenketten der Quelle: das Innere von "..." und `...` (die Datei hat keine maskierten Anführungszeichen)
  const texte = [...quelle.split('"').filter((_, i) => i % 2 === 1), ...quelle.split("`").filter((_, i) => i % 2 === 1)].join(" | ");
  assert.ok(texte.length > 1000, "die Texte wurden gefunden");
  assert.ok(!texte.includes(" - "), "kein Gedankenstrich im Fließtext");
});
