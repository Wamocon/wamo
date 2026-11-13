# Testlabor 28.11.2026: Restplan ab Freitag, 13.11.2026

**Stand:** 13.11.2026, Abend. Dieser Plan ersetzt die Kalenderfolge in `docs/testlabor-wamo.md` (Abschnitt 4.2), die noch den Freeze am 20.11. und den Dienst der Kollegen kennt. Maßgeblich sind die Entscheidungen E13 bis E16 (WAMO baut den Dienst selbst, Entwicklung fertig am Sonntag 15.11.). Was belegt ist, steht mit Quelle im Text. Was nur ein Mensch klären oder ein echter Lauf zeigen kann, steht in Abschnitt 4 und 5.

## 1. Stand am Freitagabend (am 13.11. gemessen)

| Baustein | Stand | Beleg |
| --- | --- | --- |
| Kern | Slot b `v0.1.0-c751d04` aktiv, Slot a inaktiv. Der Laufzeitcode des Kerns ist seit diesem Release unverändert, es kamen nur Tests und Messwerkzeug dazu | `wamo-core-slots.sh status` am Server, `git diff c751d04 HEAD` für `voice-core` ohne Tests und `eval/` leer |
| Oberfläche | Commit `c08468e` live, seitdem keine Änderung in `ui/` | `https://192.168.178.136:55445/version.json`, `git diff c08468e HEAD -- ui desktop` leer |
| Labor-Dienst | Container gesund, Rauchtest 20 OK, 0 Fehler, Aufbewahrung bis 11.12.2026 | `wamo-labor-einrichten.sh pruefen` am 13.11. |
| Kern-Umgebung | `WAMO_LABOR_TEXT_SPEICHERN='an'`, `WAMO_LABOR_EINWILLIGUNG_MIN='1'`, Labordaten unter `/etc/wamo-labor/daten` | `core.env` (nur diese zwei Zeilen gelesen) |
| Laborprofile | `labor-01` bis `labor-09` gesperrt, Seed des zweiten Faktors bei allen neun in der Umschlag-Datei | `wamo_labor_faktoren.py pruefen` (ohne Netz) |
| Betreuer-Geräte | Namensliste mit einem Gerät, Timer aktiv, Plätze im Büro-LAN offen (Liste `plaetze-adressen` ohne Einträge) | `/etc/wamo-labor/*`, `systemctl is-active wamo-labor-geraete.timer` |
| Lasttest | Trockenlauf sauber, 12 Sprechproben erzeugt, 8 Seeds da. **Echtlauf nie gelaufen** | `wamo_labor_last.py lauf` (Trockenlauf), `clips` |
| Kartensatz | Entwurf für den Ausdruck liegt vor (17 Karten, Wortlaut deckt sich mit den Messfällen in `voice-core/eval/faelle_labor.py`) | `docs/labor-kartensatz.html` |

## 2. Besuchersicht mit REA (13.11., ohne Token, nur GET)

Frage: Was sieht und erreicht ein Besucher, der nur die Adresse `https://192.168.178.136:55447` kennt? Ausgeliefert wurden 12 Dateien (drei Seiten, acht JavaScript-Dateien, eine CSS-Datei). REA analysierte sie als Eingabe (`rea analyze-javascript-application`, Evidenz `ev_3b1c8c09f883bd6820688bcc66e158eb9e04a62836449caa99d93c0d197bb64c`, Herkunft "shipped-artifact", Vertrauen "derived").

**Beobachtet (REA):** 8 JavaScript-Dateien fehlerfrei geparst, genau zwei Netzaufrufe (`fetch` in `board.js` und `betreuer.js`), kein WebSocket, keine weiteren Ziele als `/api/browser/board` und `/api/betreuer/plaetze[/<platz>/<aktion>]`.

**Beobachtet (Textsuche, nicht REA):** kein `innerHTML`, `eval`, `document.write`, `localStorage`, `sessionStorage` und kein Cookie in den ausgelieferten Dateien. Das Platz-Token steht nur im Adressfragment und wird sofort mit `history.replaceState` entfernt. Das Betreuer-Token geht nur als `Authorization`-Kopf.

**Beobachtet (HTTP):** `Content-Security-Policy: default-src 'none'; script-src 'self'; ...; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. Keine Quellkarten (404), keine `/docs`, `/openapi.json`, `/.git`, `/.env` (alle 404), Pfadtrick mit `..%2f` ergibt 404, `/gesundheit` ergibt 403, beide API-Pfade ohne oder mit falschem Token ergeben 401.

**Ergebnis:** keine Befunde. **Grenzen:** REA liest nur Text und beweist keine Laufzeit. Geprüft ist nur der Besucher ohne Token. Ob der Dienst mit einem gültigen Platz-Token fremde Plätze sperrt, belegt das Abnahmeskript (A1 bis A11) und nicht diese Prüfung.

## 3. Fahrplan

| Wann | Was | Wer | Bedingung |
| --- | --- | --- | --- |
| Fr 13.11. | Prüfstand, Besuchersicht mit REA, Kartensatz, Vorbereitung des Lasttests, Jira-Stand | WAMO | erledigt, der Jira-Stand wird am selben Abend nachgezogen |
| Sa 14.11. | Ruhe am Server. Konsole des Sprachmodells prüfen: Ausgabenlimit, Ratenlimit, Guthaben, Preise (nur damit rechnet der Trockenlauf Dollar). Soniox: mindestens 9 gleichzeitige Sitzungen bestätigen | Niko | Handarbeit in den Konsolen |
| So 15.11. | **Freeze** (E14). Ein Kern-Release ist nicht nötig, weil der Laufzeitcode seit `c751d04` unverändert ist. Das Etikett `labor-freeze` auf den Stand von main setzen | WAMO | Ja von Niko für das Etikett. Ab hier nur noch Sicherheitsfehler |
| Mo 16.11. | **Lasttest, Probelauf mit 1 Sitzung und 3 Minuten** (`--sitzungen 1 --minuten 3`). Er prüft zum ersten Mal Anmeldung, Browser mit virtuellem Mikrofon, Messung und Aufräumen. Läuft das Werkzeug erst am 24.11. zum ersten Mal, bleibt keine Zeit mehr für Korrekturen | WAMO, Niko | Ja von Niko. Das Werkzeug liegt nicht im Freeze, nur der Kern |
| Di 17.11. bis Mi 18.11. | Entscheidungen Q6 (Fassung der Regeln, Standard empfohlen und am Server), Q13 (Büro-Adressen für 55446, 55322 und 56543), Weg des Betreuer-Tokens, Namen und Telefonnummern (Runbook Abschnitt 8) | Niko | Abschnitt 4 |
| Do 19.11. | Erwartungswerte des Trainers zu W1 und W2 | Niko, Trainer | Pflicht vor dem Ausdruck |
| Fr 20.11. | Seminar-Angaben. Aufzeichnung des Schaufensters (zwei Stunden, Jira WMCNL-3158) | Niko | Kanal `recherche` ist für sein Profil bis 11.12. offen |
| Mo 23.11. | **Platz-Skript auf einem echten Platz-Laptop**: `einrichten`, CA, Kiosk-Konto, `pruefen`, `https://192.168.178.136:55447/board` öffnen. Das belegt, was der Rauchtest am Server nicht belegen kann | Niko, Kollegen (Geräte) | ein Laptop im Kiosk-Konto |
| Di 24.11. | **Lasttest voll**: Stufen 1, 2, 4, 8 mit je 15 Minuten, danach Murmelstufen (`--murmeln 20,10,5`), mit Preisen und `--kosten-obergrenze` | WAMO, Niko | Ja von Niko, Konsole geprüft (Sa 14.11.) |
| Mi 25.11. | Zweite Faktoren der Betreuer-Geräte neu einrichten (`wamo_labor_faktoren.py einrichten --profile ... --neu`), damit die Seeds des Mess-PCs ungültig werden. Betreuer-Token übergeben | WAMO, Betreuer | Weg aus Abschnitt 4 |
| Do 26.11. | Generalprobe, drei Stunden, vier Kollegen als Besucher, zwei Betreuer, acht Geräte | alle | Kartensatz gedruckt |
| Fr 27.11. | Daten-Freeze 12:00, Rückwegübung (Zeiten ins Runbook eintragen), Einweisung der Betreuer | WAMO, Betreuer | Morgencheckliste Teil A |
| Sa 28.11. | Labortag | alle | Morgencheckliste Teil B |
| Mo 30.11. | Nachlauf: `wamo_labor_nachlauf.py nachlauf --ausfuehren` | WAMO | Runbook Abschnitt 10 |
| bis Fr 11.12. | Laborkonten löschen und Sitzungen widerrufen, Umschlag-Datei und Sicherungen löschen. Ab 12.12.: Löschlauf des Dienstes | WAMO, Niko | Lücke 1 in Abschnitt 5 |

## 4. Was Niko entscheiden oder liefern muss

| Nr | Was | Frist | Empfehlung |
| --- | --- | --- | --- |
| N1 | Ja zum Etikett `labor-freeze` | So 15.11. | Ja. Es ändert nichts am Server |
| N2 | Ja zum Lasttest, zuerst der Probelauf mit 1 Sitzung | Mo 16.11. | Ja. Kosten: wenige Züge, geschätzt weit unter einem Dollar (nicht gemessen) |
| N3 | Preise und Ratenlimit aus der Konsole für den Lasttest | vor dem vollen Lauf | Eintragen, dann gilt `--kosten-obergrenze`. Der volle Plan schätzt rund 5,9 Millionen Eingabe-Tokens und 0,1 Millionen Ausgabe-Tokens, das Werkzeug rechnet Dollar nur mit Preisen |
| N4 | Q6: Fassung der Regeln | Mi 18.11. | Standard (steht am Server, die Messrunde 1 empfiehlt sie) |
| N5 | Q13: Büro-Adressen für 55446, 55322 und 56543 in `LAN_PORTS_ADMIN` | Mi 18.11. | Liste der Admin-Geräte nennen, die Firewall-Änderung folgt nach Ja mit `--trocken` und Rückweg |
| N6 | Weg des Betreuer-Tokens zu den Betreuern (das Einrichtungsskript zeigt ihn nie an) | Mi 18.11. | Persönliche Übergabe durch Niko am Gerät, nie Chat, Mail oder Ausdruck |
| N7 | Namen, Telefonnummern, Raum, Zeiten (Runbook Abschnitt 8 und 11) | Mi 25.11. | Vor der Generalprobe |
| N8 | W1 und W2 vom Trainer, Seminar-Angaben, Soniox-Vertragspartner GmbH, Aufbewahrung über den 11.12. | 19.11. bis 20.11. | wie in der Morgencheckliste |

## 5. Lücken, für die es noch kein Werkzeug gibt

1. **Laborkonten löschen und Sitzungen widerrufen** (Runbook Abschnitt 10 Punkt 6). Es gibt kein Skript. Der Dienstschlüssel von GoTrue bleibt auf Sokrates-2, deshalb ein Skript am Server nach dem Muster von `wamo-labor-loeschlauf.sh` (Trockenlauf als Standard, Bestätigungswort, Nachweis). Bau zwischen Mo 30.11. und Fr 04.12., denn es wird erst nach dem Labortag gebraucht.
2. **Betreuer-Token-Übergabe.** Nach N6 entscheiden, ob dafür ein Skript nötig ist.
3. **Aufzeichnung für das Schaufenster.** Themenliste und Erzähltext fehlen (Runbook Abschnitt 7.2).
4. **Erster echter Lasttest.** Alles in `docs/labor-lasttest.md` über Kosten, Dauer und Grenzen ist geschätzt, bis ein Lauf Zahlen liefert.

## 6. Risiken, die dieser Plan kennt

- Alle Plätze teilen einen Schlüssel für das Sprachmodell (Ratenlimit und Guthaben). Der Lasttest ist die einzige Messung dafür.
- Ein Platz-Laptop mit WAMO-CA und Kiosk-Konto wurde noch nicht gesehen. Der Rauchtest am Server kann das nicht belegen.
- Die Gäste-Laptops stehen vermutlich im flachen Büronetz. Das Netzkonzept liegt bei den Kollegen (Konzept 1.1).
- Ohne Freigabe für die Spracheingabe durch Soniox (mindestens 9 Sitzungen) hilft der Rückfall nur mit Texteingabe oder der Aufzeichnung.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
