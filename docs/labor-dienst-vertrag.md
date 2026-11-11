# Testlabor 28.11.2026: Vertrag Kern zu Test-Jira-Dienst, Version 1

Übergabedatei von WAMO an die Kollegen, die den Test-Jira-Dienst bauen. Stand: Entwurf vom 11.11.2026 (vorläufige Version 1). Übergabe und Einfrieren: **Fr 13.11.2026, 12:00**.
Danach ändert sich der Vertrag nur noch im Dienst, nie im Kern, denn der Kern von WAMO ist ab Fr 20.11.2026 eingefroren. Quelle der Wahrheit ist dieses Papier, die Abnahme prüft jeden Satz davon.

## 1. Worum es geht

Am Samstag, 28.11.2026, bedienen bis zu 8 Besucher gleichzeitig je einen Platz mit dem Sprachassistenten WAMO. Sie legen Fehler als Ticket in einem **Test-Jira** an, ändern, verschieben und
kommentieren Tickets und lassen sich einen Testbericht erzeugen. Den Test-Jira baut ihr: Dienst, Board-Oberfläche, Container und Betreuer-Seite. WAMO spricht mit dem Dienst über genau die sieben Aufrufe
V1 bis V7 aus Abschnitt 3. Dieses Papier legt fest, was der Dienst dafür können und aushalten muss, und wie WAMO ihn abnimmt.

**Wer ruft auf.** Ausschließlich der Kern von WAMO (Server Sokrates-2). Der Browser eines Besuchers spricht nur mit der Board-Seite des Dienstes und nie mit `/api/kern/*`. Besucher kommen nie an den Bearer des Kerns.

**Was WAMO selbst sicherstellt.** Jede schreibende Aktion löst der Besucher mit einem Klick auf eine Freigabekarte aus. Der Kern prüft alle Werte vor dem Aufruf (Projekt, Typen, Prioritäten, Längen, Zeichen) und
ruft nie etwas auf, das nicht in diesem Vertrag steht. Es gibt kein Löschen und keine Mail. **Was WAMO nicht sicherstellt:** Der Kern kann durch Fehler oder Angriff gekapert werden. Deshalb prüft der Dienst alles noch
einmal selbst (Abschnitt 5). Der Schaden eines gekaperten Kerns muss auf die Testdaten begrenzt bleiben.

**Was nicht zu diesem Vertrag gehört.** Die Board-Oberfläche, die Betreuer-Seite und ihre Aufrufe (Abschnitt 6), das Aussehen (Empfehlung: WMC-Rot `#F40E0E` und Schwarz, kein Blau, kein Atlassian-Logo), Mailversand
(es gibt keinen), Löschen (es gibt keinen Weg).

## 2. Gemeinsame Regeln

| Regel | Festlegung |
| --- | --- |
| Transport | HTTPS. Die Adresse (`WAMO_LABOR_URL`) nennt ihr bis Mo 16.11. Zertifikat von der WAMO-CA (Datei `infra/sokrates2/wamo-ca.crt`) oder von einer Stelle, der der Kern vertraut. Klartext-HTTP nimmt der Kern nur für Loopback an. Die Adresse besteht nur aus Schema, Host und Port (keine Zugangsdaten, keine Abfrage, kein Pfad). Der Kern folgt keinen Umleitungen (3xx gilt als Fehler), damit der Bearer nirgends anders hingeht |
| Format | JSON in UTF-8, `Content-Type: application/json`. Anfragen mit anderem Inhaltstyp ergeben 422 `eingabe_ungueltig` |
| Größe | Anfrage höchstens 16 KB, Antwort höchstens 64 KB. Eine größere Anfrage ergibt 413 `zu_gross`, bevor sie gelesen wird |
| Wartezeit des Kerns | lesend 5 s, schreibend 8 s. Der Dienst soll in 1 s antworten (p95). Der Kern wiederholt einen Schreibaufruf höchstens einmal, nach Zeitüberschreitung, Verbindungsfehler oder 5xx, und nur mit demselben `Idempotency-Key`. Lesende Aufrufe wiederholt er nicht |
| Last | Bis zu 8 Plätze gleichzeitig. Je Sitzung ruft der Kern V1 alle 5 Sekunden, dazu lesend V2 und V3 im Takt des Gesprächs und vor jeder Änderung einmal V3. Schreibaufrufe gibt es nur nach einem Klick des Besuchers, höchstens 25 je Durchlauf. In der Spitze bleibt es unter 5 Aufrufen je Sekunde insgesamt |
| Kennungen | Alle Kennungen, Fehlercodes und Wertebezeichner sind ASCII |
| Antwortköpfe | Jede Antwort trägt `Cache-Control: no-store` und `X-Content-Type-Options: nosniff` |
| Pfade | Alles liegt unter `/api/kern/`. Genau die Pfade aus Abschnitt 3 gibt es. Ein bekannter Pfad mit anderer Methode ergibt 405 `nicht_erlaubt` (Kopf `Allow`), ein unbekannter Pfad 404 `nicht_erlaubt`. `X-HTTP-Method-Override` und Ähnliches wird ignoriert |
| Unbekannte Felder | In Anfragen und Abfragen gelten nur die genannten Felder. Jedes andere Feld ergibt 422 `eingabe_ungueltig` (nicht stillschweigend ignorieren) |

### 2.1 Köpfe der Anfrage

| Kopf | Wann | Inhalt |
| --- | --- | --- |
| `Authorization: Bearer <Token>` | immer | Geheimnis des Kerns (`WAMO_LABOR_TOKEN`), 256 Bit, nur in `core.env` des Kerns und in der Umgebung des Dienstes (Rechte 0600). Vergleich in konstanter Zeit. Der Wert steht nie in Protokollen, Adresszeilen, Chat oder Repo |
| `X-WAMO-Platz: <1 bis 16>` | immer | Der Kern bildet den Wert aus dem Profil des Besuchers, nie aus dem Gespräch. Der Dienst ordnet jede Anfrage diesem Platz zu. Fehlt der Kopf oder ist der Platz unbekannt: 404 `platz_unbekannt` |
| `X-WAMO-Durchlauf: <UUID>` | jeder schreibende Aufruf (V4 bis V7) | Kennung des Durchlaufs, den die Sitzung beim Start über V1 gelesen hat. Der Dienst prüft sie **atomar** in derselben Transaktion wie das Schreiben (D13) |
| `Idempotency-Key: <UUID>` | jeder schreibende Aufruf | Gleicher Schlüssel und gleiche Anfrage ergeben dieselbe Antwort (Antwortkopf `Idempotent-Replayed: true`). Gültig 24 Stunden. Gleicher Schlüssel mit anderer Anfrage: 422 `eingabe_ungueltig` |

UUID heißt: 36 Zeichen in der Form `8-4-4-4-12` mit Hexziffern, Groß- und Kleinschreibung gleich. Ein Schreibaufruf ohne `X-WAMO-Durchlauf` oder ohne `Idempotency-Key`, oder mit einem Wert in falscher Form, ergibt 422 `eingabe_ungueltig`.

### 2.2 Reihenfolge der Prüfungen

Alle Dienste müssen gleich antworten, wenn mehrere Fehler zusammentreffen. Die Reihenfolge, der erste Treffer gewinnt:

1. Pfad und Methode (404 oder 405 `nicht_erlaubt`)
2. Bearer (401 `token_ungueltig`)
3. Platz (404 `platz_unbekannt`)
4. Größe der Anfrage (413 `zu_gross`)
5. Form: JSON, Felder, Wertebereiche, Köpfe `X-WAMO-Durchlauf` und `Idempotency-Key` (422 `eingabe_ungueltig`)
6. Wiederholung: Ist der `Idempotency-Key` für diesen Platz bekannt, kommt die gespeicherte Antwort zurück (oder 422 bei anderer Anfrage), ohne die folgenden Schritte
7. Schreibrate (429 `quote_erreicht`, nur schreibende Aufrufe, der abgelehnte Aufruf zählt nicht mit)
8. Durchlauf (409 `durchlauf_beendet`)
9. Ticket vorhanden und diesem Platz zugehörig (404 `ticket_unbekannt`)
10. Version (409 `ticket_veraendert`)
11. Mengenquoten (429 `quote_erreicht`)

Gespeichert für Wiederholungen werden die Erfolgsantworten und die Fehler 404 und 409 der Schritte 8 bis 10. Fehler der Schritte 1 bis 5 (Form), 429 und 5xx werden nicht gespeichert:
Ein Wiederholen führt den Aufruf dann erneut aus.

### 2.3 Wertebereiche (Kern und Dienst prüfen beide)

| Feld | Regel |
| --- | --- |
| Projekt | fest `LAB`. Ticketnummer `LAB-<1 bis 4 Ziffern>`, keine führende Null, Wert 1 bis 9999 |
| `typ` | `bug`, `aufgabe`, `story` |
| `prioritaet` | `niedrig`, `mittel`, `hoch`, `hoechste` |
| `status` | `zu_erledigen`, `in_arbeit`, `erledigt`. Alle Übergänge sind erlaubt, auch auf den gleichen Status |
| `titel` | 3 bis 120 Zeichen, eine Zeile |
| `beschreibung` | 0 bis 1500 Zeichen, Zeilenumbruch erlaubt |
| Kommentar `text` | 1 bis 500 Zeichen, Zeilenumbruch erlaubt |
| Zeichen | Unicode in NFC. Länge zählt Unicode-Zeichen (Codepunkte), nicht Bytes. Verboten sind alle Steuerzeichen (außer `\n` in Beschreibung und Kommentar, `\r` ist nie erlaubt), Formatzeichen (auch Bidi-Steuerzeichen und Nullbreitenzeichen), Zeilen- und Absatztrenner (U+2028, U+2029), private und nicht zugewiesene Zeichen. Ein Verstoß ergibt 422 mit dem Feldnamen im Text. Der Dienst **normalisiert nicht**, er lehnt ab |
| Anzeigebezeichnungen | Niedrig, Mittel, Hoch, Höchste, Zu erledigen, In Arbeit, Erledigt wählt der Dienst für seine Oberfläche. Über die Leitung gehen nur die ASCII-Bezeichner |

## 3. Aufrufe V1 bis V7

Alle Fehlerantworten haben das Format aus Abschnitt 4. Erfolgsantworten sind JSON-Objekte wie unten beschrieben, ohne weitere Felder.

**Ticket** (in V2 bis V7 immer genau diese Felder):

```json
{"nummer": "LAB-12", "titel": "Text", "typ": "bug", "prioritaet": "hoch", "status": "in_arbeit",
 "beschreibung": "Text", "version": 3, "im_durchlauf": false}
```

`version` ist eine ganze Zahl ab 1. **Jede erfolgreiche Änderung** (V5, V6, V7) erhöht sie um genau eins, auch wenn die Werte gleich bleiben. `im_durchlauf` ist wahr für Tickets, die im aktuellen Durchlauf dieses
Platzes über V4 angelegt wurden, sonst falsch (Seed-Tickets sind immer falsch).

### V1 Durchlauf lesen

`GET /api/kern/durchlauf`, keine Abfrage, kein Inhalt.

- 200 `{"durchlauf_id": "<UUID>", "offen": true, "einwilligung_version": 1, "einwilligung_zeit": "2026-11-28T09:30:00Z"}`. `offen` ist wahr, solange der Betreuer den Durchlauf nicht beendet hat. `einwilligung_version` ist eine ganze
  Zahl (die Versionsnummer des Datenschutzhinweises) oder `null`, wenn noch keine Einwilligung erfasst ist. `einwilligung_zeit` ist die Zeit der Erfassung (ISO 8601, UTC, mit `Z`) oder `null`
- 401 `token_ungueltig`, 404 `platz_unbekannt`

Der Kern ruft V1 beim Start einer Sitzung, bei jedem Verbindungsaufbau und danach alle 5 Sekunden, solange die Sitzung läuft. Ist der Dienst nicht erreichbar, nimmt der Kern keine Sitzung an. Das ist Absicht.

### V2 Tickets suchen

`GET /api/kern/tickets` mit diesen Abfrageparametern (alle optional):

| Parameter | Regel |
| --- | --- |
| `q` | höchstens 100 Zeichen. Sucht die Zeichenkette **wörtlich** (ohne Beachtung der Groß- und Kleinschreibung) in Titel und Beschreibung. `%` und `_` sind gewöhnliche Zeichen (D3). Leer oder fehlend heißt kein Textfilter |
| `nummer` | genau eine Nummer `LAB-<n>` |
| `typ`, `prioritaet`, `status` | je ein Wert aus Abschnitt 2.3 |
| `nur_durchlauf` | `true` oder `false` (Standard `false`). Bei `true` nur Tickets mit `im_durchlauf` wahr, für den Testbericht |
| `limit` | 1 bis 10, Standard 10 |

- 200 `{"tickets": [Ticket, ...], "gesamt": 7}`. Die Liste ist nach Nummer absteigend sortiert (neueste zuerst) und hat höchstens `limit` Einträge. `gesamt` ist die Zahl aller Treffer ohne Begrenzung. Mehrere Filter gelten zusammen (UND)
- 422 `eingabe_ungueltig` bei unbekanntem oder doppeltem Parameter, falschem Wert oder zu langem `q`. 401, 404 `platz_unbekannt`
- Der Dienst schreibt UTF-8 ohne Maskierung als `\uXXXX`. Würde die Antwort für 10 Tickets über 60 KB wachsen, liefert er weniger Tickets (den Anfang der Liste), `gesamt` bleibt die echte Zahl

### V3 Ticket lesen

`GET /api/kern/tickets/{nummer}`

- 200 Ticket plus `"kommentare": [{"kommentar_id": 5, "text": "..."}]` mit den letzten 20 Kommentaren, ältester zuerst
- 404 `ticket_unbekannt` (auch für eine Nummer, die nur einem anderen Platz gehört, D6), 422 `eingabe_ungueltig` bei einer Nummer in falscher Form, 401, 404 `platz_unbekannt`

### V4 Ticket anlegen

`POST /api/kern/tickets` mit `{"typ": "bug", "prioritaet": "hoch", "titel": "Text", "beschreibung": "Text"}`. Alle vier Felder sind Pflicht, `beschreibung` darf leer sein.

- 201 Ticket mit `version` 1, `status` `zu_erledigen`, `im_durchlauf` wahr. Die Nummer vergibt der Dienst aus dem Zähler des Platzes (Seed: ab 30)
- 409 `durchlauf_beendet`, 422 `eingabe_ungueltig`, 429 `quote_erreicht`, 413, 401, 404 `platz_unbekannt`

### V5 Ticket ändern

`PATCH /api/kern/tickets/{nummer}` mit `{"erwartete_version": 3, "felder": {"titel": "Neu", "prioritaet": "hoch"}}`. `felder` enthält mindestens eines von `titel`, `beschreibung`, `typ`, `prioritaet`, nur gesetzte Felder
ändern sich. Jedes andere Feld (zum Beispiel `status`, `nummer`, `geloescht`) ergibt 422.

- 200 Ticket mit erhöhter `version`
- 404 `ticket_unbekannt`, 409 `ticket_veraendert` (die gespeicherte Version ist nicht `erwartete_version`) oder `durchlauf_beendet`, 422, 429, 413, 401, 404 `platz_unbekannt`

### V6 Status setzen

`POST /api/kern/tickets/{nummer}/status` mit `{"erwartete_version": 3, "status": "erledigt"}`.

- 200 Ticket mit erhöhter `version`. Fehler wie V5

### V7 Kommentar anlegen

`POST /api/kern/tickets/{nummer}/kommentare` mit `{"erwartete_version": 3, "text": "Text"}`.

- 201 `{"kommentar_id": 5, "ticket": Ticket}` mit erhöhter `version`. Fehler wie V5

**Es gibt keinen Aufruf zum Löschen.** Kein `DELETE`, kein Feld, das Zeilen verschwinden lässt, kein Aufruf, der Kommentare entfernt (D7).

## 4. Fehlerformat und Codes

Jeder Fehler hat die Form `{"fehler": "<Code>", "text": "<ein kurzer deutscher Satz ohne Interna>"}`. Der Kern zeigt dem Besucher nie den `text`, sondern einen festen eigenen Satz je Code. Der Satz darf trotzdem keine Pfade,
Namen von Tabellen, Stapelspuren oder Versionen enthalten.

| Code | Status | Wann |
| --- | --- | --- |
| `token_ungueltig` | 401 | Bearer fehlt oder falsch |
| `platz_unbekannt` | 404 | Kopf `X-WAMO-Platz` fehlt, hat die falsche Form oder der Platz ist dem Dienst unbekannt |
| `ticket_unbekannt` | 404 | Ticket gibt es für diesen Platz nicht |
| `durchlauf_beendet` | 409 | `X-WAMO-Durchlauf` ist nicht der aktuelle Durchlauf des Platzes, oder dieser Durchlauf ist nicht mehr offen |
| `ticket_veraendert` | 409 | `erwartete_version` stimmt nicht mit der gespeicherten Version überein |
| `eingabe_ungueltig` | 422 | Form, Feld, Wert, Länge oder Zeichen falsch. Der Text nennt den Feldnamen |
| `zu_gross` | 413 | Anfrage über 16 KB |
| `quote_erreicht` | 429 | Eine Quote aus D1 ist erreicht |
| `nicht_erlaubt` | 405 oder 404 | Methode oder Pfad nicht im Vertrag |
| `dienst_fehler` | 5xx | Interner Fehler, ohne Einzelheiten. Nach einem solchen Fehler ist der Zustand des Platzes unverändert (alles oder nichts) |

## 5. Sicherheitsanforderungen D1 bis D13

Die Werte sind Untergrenzen der Strenge. Ihr dürft verschärfen und nicht lockern. **Frist:** Ihr bestätigt bis Mo 16.11., dass sie umsetzbar sind. Die Abnahme läuft Mo 23.11., das schriftliche Review liegt vor dem 24.11. vor.
Ein Fehler in A1, A2, A6 oder A7 (Abschnitt 7) heißt: Der Dienst geht nicht an Besucher.

| Nr | Anforderung | Nachweis bei der Abnahme |
| --- | --- | --- |
| D1 | Quoten je Platz: höchstens 100 Tickets, 300 Kommentare, 1 MB Nutztext insgesamt (Titel, Beschreibungen, Kommentare), 30 Schreibaufrufe je Minute. Bei SQLite ist `max_page_count` so gesetzt, dass die Datenbankdatei nicht über eine feste Größe wächst (Empfehlung 16 MB je Platz). Über der Grenze antwortet der Dienst 429 `quote_erreicht`, nie mit einem Absturz | A4 |
| D2 | Eigenes internes Netz nur mit `wamo-caddy`, kein Weg nach außen, kein SMTP, keine DNS-Auflösung nach außen. Erreichbar für Besucher nur über Caddy, für den Kern nur mit dem Bearer | A10, von Hand mit euch am Server |
| D3 | Alle Abfragen sind parametrisiert, nirgends werden Zeichenketten zu SQL zusammengesetzt. Die Suche mit `LIKE` maskiert `%`, `_` und das Maskierungszeichen (`ESCAPE`), damit sie nur wörtlich suchen | A2, A3 |
| D4 | Nutzertext erscheint nie als HTML: `textContent` statt `innerHTML`, kein Markdown, kein HTML. Strenge CSP (`default-src 'self'`, kein Inline-Skript), `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. Eigene Herkunft (Adresse und Port), nicht die der WAMO-Oberfläche, deren Browser-Speicher Sitzungstoken enthält | A1 |
| D5 | Größenlimits: Anfrage höchstens 16 KB (Kern) und 64 KB (Browser), Feldlängen wie in Abschnitt 2.3, Kopfgröße begrenzt, zu große Anfragen ergeben 413 `zu_gross` | A5 |
| D6 | Platztrennung: Jede Abfrage verlangt den Platz, der Platz kommt aus dem Token oder dem Kopf des Kerns und nie aus der Nutzereingabe. Wer Platz A mit der Nummer von Platz B liest, ändert oder kommentiert, bekommt 404 | A6 |
| D7 | Kein Löschweg für Besucher und den Kern: kein DELETE, kein Feld, das Zeilen verschwinden lässt. Einzige Ausnahmen sind die Betreuer-Funktionen (D9) | A7 |
| D8 | Tokens: Platz-Token 128 Bit, nur als Hash gespeichert. Betreuer-Token getrennt. Bearer des Kerns in konstanter Zeit verglichen. Tokens nie in Adresszeilen-Protokollen | A9 |
| D9 | Betreuer-Funktionen (Durchlauf beenden und starten, Einwilligung mit Versionsnummer erfassen, Platz zurücksetzen, Daten eines Platzes oder Durchlaufs löschen) hinter eigenem Token, eigene Pfade, nicht mit dem Platz-Token und nicht mit dem Bearer des Kerns erreichbar | A9 |
| D10 | Protokolle ohne Ticket-, Kommentar- oder Einwilligungstexte (nur Metadaten), Aufbewahrung höchstens bis **05.12.2026**, danach gelöscht. Auch Sicherungen des Dienstes folgen dieser Frist. Ihr weist es mit dem Review nach | Review |
| D11 | Container: Nutzer ohne root, `read_only`, `cap_drop: ALL`, `no-new-privileges`, Speicher- und CPU-Grenzen (Empfehlung 256 MB, 0,5 CPU), Image mit festem Digest | Review |
| D12 | Sicherheitsreview des Dienstes durch jemanden, der ihn nicht gebaut hat, schriftlich, vor dem 24.11. (Mo 23.11. abends). Ohne Review kein Einsatz am Labortag | Review |
| D13 | Konsistenz: Der Dienst prüft `durchlauf_id` und `erwartete_version` atomar in derselben Transaktion wie das Schreiben (409 `durchlauf_beendet`, 409 `ticket_veraendert`). Wiederholt der Kern mit demselben Idempotenzschlüssel, kommt dieselbe Antwort, und es entsteht nichts doppelt | A8 |

## 6. Betreuer-Funktionen (nur im Dienst, nicht Teil von V1 bis V7)

Sie liegen hinter einem eigenen Betreuer-Token (D9), ihr entwerft sie. WAMO braucht von ihnen nur die Wirkung auf V1:

1. **Durchlauf beenden.** Danach liefert V1 `offen` falsch, jeder Schreibaufruf mit der alten `durchlauf_id` ergibt 409 `durchlauf_beendet`. Der Kern beendet die Sitzung innerhalb von 5 Sekunden.
2. **Neuer Teilnehmer, Einwilligung liegt vor.** Der Betreuer erfasst die Einwilligung mit der Versionsnummer des Datenschutzhinweises und der Zeit. Das legt einen **neuen Durchlauf** mit neuer `durchlauf_id` an (V1 liefert `offen` wahr und die
   Einwilligung). Der Dienst setzt Board und Ablage des Platzes dabei auf den Ausgangsstand (Seed). Erst dann nimmt der Kern wieder eine Sitzung dieses Platzes an. Ohne gültige Einwilligung bleibt `einwilligung_version` `null`.
3. **Platz zurücksetzen** auf den Seed, **Daten eines Platzes oder Durchlaufs löschen** (Frist aus D10).

**Seed je Platz.** LAB-11 erledigt, LAB-12 in Arbeit, LAB-20 zu erledigen (damit die Verwechslung von zwölf und zwanzig bei einer Sprachaufgabe sichtbar wird), drei weitere Tickets, Zähler ab 30. Der Dienst setzt den Seed bei jedem
neuen Durchlauf eines Platzes zurück. Der Seed ist Datenpflege und darf nach dem Freeze geändert werden. Die Plätze 15 und 16 sind für die Abnahme reserviert und kein Besucher bekommt sie.

## 7. Abnahme

WAMO nimmt den Dienst mit einem Skript ab (`scripts/wamo_labor_abnahme.py`, kommt von WAMO). Es läuft nur gegen die Abnahmeplätze 15 und 16, die Tokens kommen aus der Umgebung. Es zerstört nichts: Mengenprüfungen brechen
an einer Schwelle ab. Das Skript besteht zuerst gegen eine saubere Nachbildung des Vertrags und scheitert gegen eine absichtlich verwundbare, erst dann gilt es als Prüfmittel. Ausgabe als Tabelle, Ausgang 0 nur bei Grün.

| Nr | Prüfung |
| --- | --- |
| A1 | **XSS.** Tickets mit `<script>`, `"><img onerror>` und `javascript:` in Titel, Beschreibung und Kommentar. Die Antworten tragen CSP, `nosniff` und `frame-ancestors`. Die Rohseite enthält keinen unmaskierten Nutzertext. Ein Browserlauf zeigt keinen Dialog und keine Skriptausführung |
| A2 | **SQL-Injektion** in `q`, `nummer`, Titel und Kopf (`' OR 1=1 --`, `'; DROP TABLE tickets;--`): keine fremden Treffer, kein 5xx |
| A3 | **Prozent und Unterstrich.** Die Suche nach `%` und `_` findet nur wörtliche Treffer ("100% fertig", "a_b") |
| A4 | **Quoten.** Anlegen bis zur Grenze und eins darüber ergibt 429. Ebenso Schreibrate, Gesamtbytes, Datenbankgröße |
| A5 | **Größenlimits.** Anfrage über 16 KB ergibt 413, jede Feldlänge plus eins 422, zu große Köpfe werden abgelehnt |
| A6 | **Platztrennung.** Platz 15 legt an, Platz 16 liest, ändert und kommentiert diese Nummer und erwartet 404. Ein Platz-Token gegen den Pfad eines anderen Platzes ergibt 403 oder 404. Ein gefälschter Platzkopf ohne Bearer ergibt 401 |
| A7 | **Kein Löschweg.** `DELETE` und alle anderen Methoden auf alle Pfade, `X-HTTP-Method-Override`, Felder wie `geloescht`: erwartet 405, 404 oder 422. Die Zeilenzahl vorher und nachher ist gleich |
| A8 | **Durchlauf und Version.** Schreibaufruf mit alter `durchlauf_id` ergibt 409 `durchlauf_beendet`, mit falscher Version 409 `ticket_veraendert`, derselbe Idempotenzschlüssel liefert dieselbe Antwort |
| A9 | **Zugang.** Ohne oder mit falschem Bearer 401. Betreuer-Pfade mit Platz-Token 403 |
| A10 | **Netz.** Dienst aus einem fremden Container nicht erreichbar, kein ausgehender Weg (von Hand mit euch am Server) |
| A11 | **Vertrag.** Jeder Aufruf, jedes Feld und jeder Fehlercode von V1 bis V7 in der Reihenfolge aus Abschnitt 2.2 |

## 8. Termine

| Was | Frist |
| --- | --- |
| Rückfragen und Änderungswünsche zum Entwurf an WAMO (Niko) | bis Fr 13.11. 12:00 |
| Übergabe der Version 1 an euch, Vertrag eingefroren | Fr 13.11. 12:00 |
| Bestätigung "umsetzbar" (Vertrag und D1 bis D13) | Mo 16.11. |
| Adresse und Verbindungsart des Dienstes (`WAMO_LABOR_URL`) | Mo 16.11. (steht am 20.11. fest) |
| Vorversion: alle Endpunkte V1 bis V7 lauffähig, Oberfläche noch nicht nötig | Mi 18.11. |
| Datenschutz: Hinweistext, Einwilligungstext, Versionsnummer (ganze Zahl, nur wachsend), Altersgrenze, verantwortliche Person, Löschfrist | Mi 18.11. |
| Dienst lauffähig am Server | **Mo 23.11. 12:00** |
| Schriftliches Sicherheitsreview des Dienstes | Mo 23.11. abends |
| Korrekturen nach der Abnahme, letzter Lauf des Abnahmeskripts | Di 24.11. 12:00 |

Die Vorversion vom 18.11. prüft WAMO am Do 19.11. mit einem Integrationstest, damit Abweichungen vor dem Freeze auffallen. Abweichungen korrigiert ihr im Dienst.

## 9. Beispiele (Platz 3, Durchlauf `5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aa`)

Ticket anlegen (V4):

```text
POST /api/kern/tickets
Authorization: Bearer <WAMO_LABOR_TOKEN>
X-WAMO-Platz: 3
X-WAMO-Durchlauf: 5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aa
Idempotency-Key: 8f14e45f-ceea-467a-9575-8c1a1f0c9e11
Content-Type: application/json

{"typ": "bug", "prioritaet": "hoch", "titel": "Anmeldung schlägt fehl", "beschreibung": "Nach dem Klick passiert nichts."}

201 {"nummer": "LAB-30", "titel": "Anmeldung schlägt fehl", "typ": "bug", "prioritaet": "hoch", "status": "zu_erledigen",
     "beschreibung": "Nach dem Klick passiert nichts.", "version": 1, "im_durchlauf": true}
```

Dieselbe Anfrage mit demselben `Idempotency-Key` noch einmal: 201 mit derselben Antwort und dem Kopf `Idempotent-Replayed: true`, es entsteht kein zweites Ticket.

Status setzen mit veralteter Version (V6), das Ticket wurde zwischendurch geändert (gespeichert ist Version 2):

```text
POST /api/kern/tickets/LAB-12/status
{"erwartete_version": 1, "status": "erledigt"}

409 {"fehler": "ticket_veraendert", "text": "Das Ticket hat sich geändert."}
```

Schreibaufruf nach dem Ende des Durchlaufs (V4 bis V7):

```text
409 {"fehler": "durchlauf_beendet", "text": "Der Durchlauf ist beendet."}
```

Suche nach dem Zeichen Prozent (V2), das nur wörtlich gesucht wird:

```text
GET /api/kern/tickets?q=%25&limit=5

200 {"tickets": [{"nummer": "LAB-31", "titel": "100% fertig", ...}], "gesamt": 1}
```

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
