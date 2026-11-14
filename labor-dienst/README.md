# W.A.I.S. Labor-Dienst

Das Test-Jira des Testlabors am 28.11.2026. Bis zu acht Besucher legen an je einem Platz mit dem Assistenten W.A.I.S. Fehler als Ticket an, ändern,
verschieben und kommentieren Tickets. Der Dienst setzt den Vertrag Version 1 um (`docs/labor-dienst-vertrag.md`, Quelle der Wahrheit) und
ist ein eigener Teil des Monorepos: Paket `wamo-labor-dienst`, Python 3.11 oder neuer, FastAPI, uvicorn und SQLite aus der Standardbibliothek.
Dieser Stand ist der API-Teil mit Speicher und Betreuer-Funktionen. Die Webseiten (Board und Betreuer-Seite) kommen als zweiter Schritt.

## Start

```text
cd labor-dienst
pip install -r requirements.txt
WAMO_LABOR_DIENST_KERN_TOKEN_DATEI=... WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI=... python -m wamo_labor_dienst
```

Healthcheck des Containers: `python -m wamo_labor_dienst gesundheit` (Ausgang 0 nur bei `GET /gesundheit` mit Zustand ok).
Das Image entsteht auf dem Server aus den Quellen (`Dockerfile`: Nutzer ohne root, schreibt nur nach `/data`, lauffähig mit `--read-only`,
nichts wird zur Laufzeit installiert). Das Basisimage hat keinen Standardwert und wird mit festem Digest übergeben, ohne ihn bricht der Bau ab:
`docker build --build-arg BASIS=python:3.11-slim@sha256:<Digest> -t wamo-labor-dienst:<Stand> .` Die Abhängigkeiten stehen in `requirements.txt` mit
Version und den Hashes aller Dateien (erzeugt wie `pip-compile --generate-hashes`), das Image installiert sie mit `pip install --require-hashes`
(geprüft für Python 3.11 auf Linux x86_64). Neue Versionen: Pins ändern, Hashes mit `pip-compile --generate-hashes` neu erzeugen.

## Umgebung

Der Dienst liest nur Umgebungsvariablen und Dateien. Eine fehlende oder unbrauchbare Einstellung beendet den Start mit Ausgang 2 und einer
Meldung ohne Werte.

| Variable | Bedeutung | Standard |
| --- | --- | --- |
| `WAMO_LABOR_DIENST_KERN_TOKEN_DATEI` | Datei mit dem Bearer des Kerns (`WAMO_LABOR_TOKEN`, 43 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-, mindestens 16 verschiedene Zeichen). Auf Linux darf eine Tokendatei nur dem Besitzer gehören (Rechte 0600 oder 0400), sonst startet der Dienst nicht, bei Docker-Secrets also `mode: 0400` und den Benutzer des Dienstes als Besitzer setzen | Pflicht |
| `WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI` | Datei mit dem Betreuer-Token, muss ein anderer Wert sein | Pflicht |
| `WAMO_LABOR_DIENST_DATEN` | Ordner für Datenbanken und Protokoll (im Container `/data`) | `/data` |
| `WAMO_LABOR_DIENST_HOST`, `WAMO_LABOR_DIENST_PORT` | Adresse zum Lauschen (im Container `0.0.0.0`) | `127.0.0.1`, `8080` |
| `WAMO_LABOR_DIENST_BIS` | Ende der Aufbewahrung (JJJJ-MM-TT, zwischen `2026-11-28` (Labortag) und `2026-12-11`, alles andere ist ein Konfigurationsfehler), danach löscht der Dienst alle Daten. Geht die Uhr des Rechners vor dem 12.11.2026, startet der Dienst nicht (Ausgang 2) | `2026-12-11` |
| `WAMO_LABOR_DIENST_PLATZ_MB` | harte Obergrenze der Datenbankdatei je Platz (2 bis 64) | `16` |
| `WAMO_LABOR_DIENST_PROXYS` | Adressen vertrauter Proxys (durch Komma getrennt, zum Beispiel die von Caddy im internen Netz). Nur von ihnen zählt `X-Forwarded-For` für die Zählung der Fehlversuche, sonst zählt die Adresse der Verbindung | leer |

## Schnittstellen

Alle Antworten tragen `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` und eine CSP mit
`default-src 'self'` und `frame-ancestors 'none'`. Fehler haben immer die Form `{"fehler": "<Code>", "text": "<Satz>"}`.

* **Kern, `/api/kern/`** (Bearer des Kerns, Kopf `X-WAMO-Platz`): V1 bis V7 genau nach Vertrag, Pfade, Felder, Fehlercodes, Reihenfolge der
  Prüfungen (Abschnitt 2.2), Wertebereiche (2.3), Idempotenz 24 Stunden, 405 mit `Allow`, unbekannte Felder und Parameter 422, 413 vor dem Lesen.
  Durchlauf und Version werden in derselben Transaktion geprüft wie das Schreiben (D13). Es gibt keinen Weg zum Löschen.
* **Browser, `GET /api/browser/board`** (nur lesend, Kopf `X-Platz-Token`): das Board des eigenen Platzes mit Tickets, Kommentaren,
  Zeitpunkten (ISO, UTC) und dem Zustand des Durchlaufs (`offen`, `einwilligung_version`). Der Platz kommt immer aus dem Token. Ein Token in der
  Adresszeile gilt nie (Abfrage 422).
* **Betreuer, `/api/betreuer/`** (Bearer mit dem Betreuer-Token, ohne Token 401, falscher Token auch Platz-Token oder Bearer des Kerns 403):
  `GET plaetze` (Zustand aller 16 Plätze ohne Texte), `POST plaetze/<n>/durchlauf-beenden`, `neuer-durchlauf` (Körper
  `{"einwilligung_version": 1}`, Seed, neue `durchlauf_id`, Einwilligung mit Zeit), `zuruecksetzen` (Seed, Durchlauf bleibt), `daten-loeschen`
  (alles weg, Platz danach geschlossen und ohne Seed; Erfolg wird nur gemeldet, wenn auch die Datei bereinigt ist, sonst 503 und der Betreuer wiederholt) und `platz-token` (erzeugt oder erneuert, 128 Bit, wird nur einmal ausgegeben und nur als
  Hash gespeichert). **Besucherwechsel:** `neuer-durchlauf` und `daten-loeschen` entziehen dem alten Platz-Token sofort den Zugang. Danach holt der
  Betreuer mit `platz-token` den neuen Token für den Platz (`durchlauf-beenden` und `zuruecksetzen` lassen den Token stehen). Jeder Aufruf steht
  mit Zeit, Platz und Art im Protokoll.
* **`GET /gesundheit`** ohne Token: nur der Zustand, keine Daten.

Antwortformen der Schnittstellen für die Oberfläche (sie ändern sich nicht):

```text
GET /api/browser/board  (Kopf X-Platz-Token)
200 {"platz": 3, "durchlauf": {"offen": true, "einwilligung_version": 1},
     "tickets": [{"nummer": "LAB-12", "titel": "...", "typ": "bug", "prioritaet": "hoch", "status": "in_arbeit", "beschreibung": "...",
                  "version": 2, "im_durchlauf": false, "angelegt": "2026-11-28T09:30:00Z", "geaendert": "2026-11-28T09:41:12Z",
                  "kommentare": [{"kommentar_id": 1, "text": "...", "angelegt": "2026-11-28T09:41:12Z"}]}]}   Tickets nach Nummer aufsteigend
401 token_ungueltig (Kopf fehlt oder falsch), 422 eingabe_ungueltig (irgendeine Abfrage), 405 nicht_erlaubt (jede andere Methode)

GET /api/betreuer/plaetze  (Authorization: Bearer <Betreuer-Token>)
200 {"plaetze": [{"platz": 1, "durchlauf_id": "<UUID>", "offen": false, "einwilligung_version": null, "einwilligung_zeit": null,
                  "tickets": 6, "kommentare": 0, "hat_platz_token": false, "reserviert": false}, ...16 Einträge, Plätze 15 und 16 reserviert]}
POST /api/betreuer/plaetze/<n>/<aktion>   200 mit demselben Eintrag für den Platz (platz-token: {"platz": n, "platz_token": "<32 Hexzeichen>"})
Fehler: 401 token_ungueltig, 403 zugriff_verweigert, 404 platz_unbekannt oder nicht_erlaubt, 405 nicht_erlaubt, 413 zu_gross, 422 eingabe_ungueltig
```

Die Tokens öffnen nie die Pfade der anderen: Der Bearer des Kerns nur `/api/kern/`, der Platz-Token nur `/api/browser/`, der Betreuer-Token nur
`/api/betreuer/`. Ein neuer Platz ist geschlossen und ohne Einwilligung, bis der Betreuer `neuer-durchlauf` aufruft.

## Speicher und Quoten

Je Platz eine SQLite-Datei (WAL) mit `max_page_count` (16 MB), dazu eine kleine Datei für die Token-Hashes. Quoten je Platz (D1): 100 Tickets,
300 Kommentare, 1 MB Nutztext (UTF-8, Seed zählt mit), 30 Schreibaufrufe in 60 Sekunden. Darüber antwortet der Dienst 429 `quote_erreicht`,
auch wenn die Datei voll ist, nie mit einem Absturz. Alle Abfragen sind parametrisiert, die Suche ist wörtlich (kein `LIKE`) und beachtet
Groß- und Kleinschreibung nicht, auch bei Umlauten. Der Speicher für Idempotenzschlüssel ist auf 200 je Platz begrenzt (wirkt nur bei Missbrauch).

**Einwilligungsversion:** `neuer-durchlauf` verlangt eine Version, die nicht kleiner ist als die höchste bisher erfasste (Vertrag Abschnitt 8, die
Nummer wächst nur). Sie gilt für alle Plätze zusammen und steht in `zentral.sqlite3`. Eine zu kleine Version ergibt 422, und es ändert sich nichts
(auch der Platz-Token bleibt). Die Version liegt zwischen 1 und 1000 (eine Obergrenze, damit ein Tippfehler nicht für immer alle kleineren Versionen sperrt).

Rücksetzweg für ein falsch erfasstes Maximum: `POST /api/betreuer/einwilligung-minimum-setzen` mit `{"einwilligung_version": <kleinere Zahl>, "bestaetigung": "senken"}`.
Er senkt das Maximum nur (eine gleiche oder größere Zahl ist 422), nur wenn kein Durchlauf läuft (sonst 409 `durchlauf_laeuft`, vorher alle Durchläufe
beenden) und steht mit Zeit, Art und Wert in der Betreuer-Spur. Befehl (der Token kommt aus der Datei, nie als Text in die Befehlszeile):

```text
curl -X POST -H "Authorization: Bearer $(cat <Datei mit dem Betreuer-Token>)" -H "Content-Type: application/json" \
     -d '{"einwilligung_version": 3, "bestaetigung": "senken"}' https://HOST/api/betreuer/einwilligung-minimum-setzen
```

## Bekannte Restrisiken

Was der Dienst bewusst nicht löst und wer es lösen muss:

* **Löschen nur auf SQLite-Ebene:** `daten-loeschen`, `zuruecksetzen` und die Aufbewahrung löschen in den Datenbanken (Zeilen überschrieben, Datei verkleinert,
  WAL-Datei zurückgeschrieben und geprüft). Dateisystemblöcke, Volume-Snapshots und Hostsicherungen liegen außerhalb des Dienstes. D10 verlangt, dass der Betrieb
  dafür sorgt (keine Snapshots oder Sicherungen des Volumes über den 11.12.2026 hinaus, sonst löschen).
* **Die Aufbewahrung greift nur bei laufendem Dienst:** Gelöscht wird beim Start und stündlich, solange der Dienst läuft. Nach dem 11.12.2026 den Dienst
  weiterlaufen lassen (oder neu starten) oder das Volume löschen. Ein Dienst, der vorher abgeschaltet wird, löscht nichts mehr.
* **Uhrabhängigkeit:** Aufbewahrung, Schreibrate, Gültigkeit der Idempotenzschlüssel, Anzeigezeit des neuen Tokens und die Zeitpunkte in den Tickets nutzen die
  Uhr des Rechners. Der Dienst prüft sie beim Start (nicht vor dem 12.11.2026) und löscht nie nach einem Sprung der Wanduhr von mehr als einem Tag gegen die
  monotone Uhr. Eine dauerhaft falsche Uhr, die schon beim Start nach dem 11.12.2026 steht, löscht trotzdem (es lässt sich nicht von einem Neustart danach
  unterscheiden), und ein Rechner, der länger als einen Tag ruht (Suspend), verschiebt die Wanduhr gegen die monotone Uhr: Dann löscht die Aufbewahrung nicht,
  bis der Dienst neu gestartet wird.
* **Einwilligungsmaximum und Rücksetzweg:** Die Version des Datenschutzhinweises darf nur wachsen (1 bis 1000). Ein falsch erfasstes Maximum lässt sich nur mit
  der Funktion `einwilligung-minimum-setzen` senken (nur ohne laufenden Durchlauf, mit ausdrücklicher Bestätigung, mit Eintrag in der Spur, Befehl siehe oben).
* **Der Betreuer-Token ist ein Generalschlüssel:** Wer ihn hat, liest mit `platz-token` jeden Platz (er erzeugt sich einen Token und öffnet das Board), setzt jeden
  Platz zurück und löscht jeden Platz. Die Betreuer-Spur (`betreuer.jsonl`) lässt sich mit gültigem Token bis 5 MB füllen, danach schreibt der Dienst still
  keine Einträge mehr (nie überschreiben). Der Token wird nur beim Start aus der Datei gelesen, ein Wechsel geht nur per Neustart.
* **Der Platz-Token bleibt gültig** nach `durchlauf-beenden` und `zuruecksetzen` (der Besucher sieht weiter sein Board). Ungültig wird er nur durch
  `neuer-durchlauf`, `daten-loeschen`, ein neues `platz-token` und das Ende der Aufbewahrung.
* **`neuer-durchlauf` verlangt in der Schnittstelle keine Bestätigung:** Ein versehentlicher Aufruf setzt das Board eines Besuchers zurück und entzieht dessen
  Platz-Token. Die Betreuer-Seite verlangt dafür das Eintippen der Platznummer, ein Skript oder `curl` nicht.
* **Plätze 15 und 16:** `neuer-durchlauf` entzieht den Token dort ebenso wie bei jedem Platz, `daten-loeschen` auch. Mit „nicht gesperrt“ ist nur gemeint, dass der
  Dienst die Ausgabe eines Platz-Tokens für die Abnahmeplätze nicht verbietet (die Abnahme braucht ihn). Der Betreuer gibt für sie keinen Besucher-Token aus.
* **Ein Prozess:** Token-Index, Raten, Fehlerzähler und Zwischenspeicher liegen im Speicher dieses einen Prozesses (mehrere Prozesse hinter einem Verteiler
  würden die Grenzen je Prozess zählen).
* **Fristen und Verbindungen:** Die Frist für Kopfzeilen (10 Sekunden) und die Höchstzahl der Verbindungen (64) schützen den Dienst selbst, sie ersetzen nicht
  die Fristen von Caddy davor.

## Weboberflächen

Drei statische Seiten, die der Dienst selbst ausliefert (kein Build-Schritt, keine Pakete, kein CDN, keine externen Schriften, kein Atlassian-Logo, WMC-Corporate-Design
ohne Blau, Schrift Arial Narrow mit Rückfall auf Arial):

| Adresse | Seite |
| --- | --- |
| `/` und `/board` | Board-Seite für Besucher (nur lesend), Link `/board#t=<Platz-Token>` |
| `/betreuer` | Betreuer-Seite, Link `/betreuer#b=<Betreuer-Token>` oder Eingabefeld |
| `/datenschutz` | Datenschutzhinweis für die Besucher (Version 1), öffentlich wie das Board, ohne Token, mit Knopf Drucken |
| `/web/<Name>` | Dateien der Seiten aus einer festen Liste (`stil.css`, `dom.js`, `gemeinsam.js`, `board-logik.js`, `board.js`, `betreuer-logik.js`, `betreuer.js`, `datenschutz-logik.js`, `datenschutz.js`) |

* **Board-Seite:** zeigt Platznummer, Zustand des Durchlaufs, die Tickets des Platzes als Spalten nach Status (Zu erledigen, In Arbeit, Erledigt) mit Schlüssel,
  Titel, Typ, Priorität und Zahl der Kommentare, ein Klick öffnet Beschreibung und Kommentare. Sie fragt alle 3 Sekunden (nur bei sichtbarem Tab), nimmt sich
  bei 429 zurück (Retry-After) und beendet die Abfragen bei 401 mit „Dieser Platz-Link gilt nicht mehr, bitte beim Betreuer melden“. Einen Testbericht liefert
  die Schnittstelle nicht, die Seite zeigt deshalb nur eine Zusammenfassung (Tickets, neue Tickets, Kommentare). Der Platz-Token kommt aus dem URL-Fragment
  (`#t=...`, nie aus der Abfrage), bleibt nur im Speicher der Seite und wird mit `history.replaceState` aus der Adresszeile entfernt. Ein Neuladen braucht deshalb
  den Link erneut.
* **Betreuer-Seite:** Übersicht der 16 Plätze und die Funktionen der Betreuer-Schnittstelle. Neuer Durchlauf, Zurücksetzen und Daten löschen verlangen in der
  Oberfläche das Eintippen der Platznummer (das mildert die fehlende Bestätigung der Schnittstelle). Neuer Durchlauf verlangt zusätzlich den Haken „Der Besucher
  hat den Datenschutzhinweis (Version 1) gelesen und bestätigt.“: „Ausführen“ wird erst frei, wenn der Haken gesetzt UND die Platznummer getippt ist. Die Version
  kommt aus der Konstante `DATENSCHUTZ_VERSION` in `datenschutz-logik.js`, steht im Dialog nur lesend und wird als `einwilligung_version` gesendet (kein freies Feld
  mehr). Kennt der Dienst schon eine höhere Version, ist die Seite veraltet: „Ausführen“ bleibt gesperrt, bis sie neu geladen ist. Ein Link im Dialog und in der
  Werkzeugleiste öffnet den Hinweis in einem neuen Tab (`rel="noopener noreferrer"`). Der Haken ist die Bestätigung des Betreuers in der Oberfläche, kein
  technischer Nachweis: Die Schnittstelle selbst ist unverändert und nimmt jede gültige Version an (1 bis 1000, nur wachsend).
  Nach neuer Durchlauf und Daten löschen holt die Seite den neuen Platz-Token (der alte gilt nicht mehr) und zeigt ihn mit dem Besucher-Link nur einmal mit
  Kopier-Knöpfen, nach 60 Sekunden wird er ausgeblendet. Der Betreuer-Token bleibt nur im Speicher der Seite, nie in einem Speicher des Browsers.
* **Datenschutzhinweis (`/datenschutz`):** Der Text steht nur im Dokument `docs/labor-datenschutz-entwurf.md` (Abschnitt 1, „Text für die Besucher“) und ist fest in
  `datenschutz.html` eingesetzt (die Seite fragt keinen Dienst und kennt weder Token noch Platzdaten). `tests/test_datenschutz.py` vergleicht die Seite mit dem Dokument:
  jeder Absatz und alle sechs Punkte in der Reihenfolge, und die Version stimmt in Seite, Dokument und Konstante überein. Ändert sich der Text inhaltlich, steigt die
  Nummer an allen drei Stellen gemeinsam. Große Schrift (ab 1,45 rem), ruhige Gestaltung im WMC-Design, Knopf Drucken ohne Inline-Skript (`datenschutz.js`, Druckansicht
  ohne Kopfzeilenknöpfe). Gleiche Schutzköpfe und Auslieferung wie die anderen Seiten (nur GET, feste Liste, alles andere 404). Im Betrieb erreicht sie das Büro-LAN
  über dieselbe Pfadliste wie das Board (`infra/sokrates2/wamo-caddy/kern/wamo-labor.caddy`).
* **Sicherheit:** Alle Texte aus dem Dienst gehen über `textContent` oder Textknoten, nie als HTML (`dom.js` lässt nur Attribute aus einer festen Liste zu). Kein
  Inline-Skript, kein Inline-Stil, keine Ereignisattribute. Die Seiten tragen `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self';
  img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, dazu `nosniff`, `no-referrer`, `Cache-Control: no-store`
  und eine Permissions-Policy, die Kamera, Mikrofon, Standort, Zahlung und USB mit leerer Liste sperrt. Ausgeliefert wird nur per GET aus der festen Liste (kein
  Pfad-Trick, kein Verzeichnislisting, alles andere 404), die Dateien liegen nach dem Start im Speicher. Die Seiten enthalten weder Token noch Platzdaten, alle
  Daten kommen über die Schnittstellen mit Token. Die Anmeldung der Schnittstellen bleibt unverändert: Wer `/betreuer` erreicht, legt der Betrieb (Caddy) fest.
* **Prüfen:** `python -m pytest -p no:cacheprovider -q tests/test_web.py tests/test_web_inhalt.py tests/test_datenschutz.py tests/test_javascript.py` und die reinen Funktionen mit
  `node --test tests/js` (Node, keine Pakete).
* **Abnahmeskript:** A1 akzeptiert bei `--board-url` `default-src 'self'` (Vertrag D4) und die strengere Richtlinie der Seiten mit `default-src 'none'`
  und eigenen `script-src` und `style-src`. `--board-url` kann deshalb direkt auf `/board` zeigen.

**Lokale Vorführung mit Demo-Daten** (nur Loopback, temporärer Ordner, Wegwerf-Tokens, die Adressen mit den Tokens stehen in der Ausgabe):

```text
cd labor-dienst
python -m wamo_labor_dienst.demo --port 8765
```

Platz 1 und 2 haben einen offenen Durchlauf mit Tickets in allen Spalten und Kommentaren (auch ein Ticket mit HTML-artigem Text, der als Text erscheint),
Platz 3 hat einen beendeten Durchlauf, alle anderen Plätze sind geschlossen. Alle drei haben die Einwilligung in Version 1 erfasst, damit der Dialog Neuer Durchlauf
bedienbar ist. Den Datenschutzhinweis zeigt `http://127.0.0.1:8765/datenschutz` (ohne Token). Mit Strg+C beenden, die Demo-Daten verschwinden dabei.

## Grenzen für Lesen, Fehlversuche und Verbindungen

* **Board:** höchstens etwa 2 Abfragen je Sekunde und Platz-Token (kurzzeitig 5), alle Plätze zusammen höchstens 20 je Sekunde, darüber 429
  `quote_erreicht` mit `Retry-After: 1`. Die Antwort wird je Platz eine Sekunde zwischengespeichert, jede Änderung des Platzes macht sie sofort ungültig.
* **Fehlversuche:** Falsche Bearer, Betreuer-Tokens und Platz-Token werden je Quelle gezählt (120 je Minute), danach antwortet der Dienst 429 mit
  `Retry-After: 60`. Ein richtiger Zugang wird nie gesperrt. Ein unbekannter Platz-Token wird nur im Speicher erkannt (kein Datenbankzugriff, kein
  Faden aus dem gemeinsamen Pool). Hinter Caddy gehört dessen Adresse in `WAMO_LABOR_DIENST_PROXYS`, sonst haben alle Besucher dieselbe Quelle.
* **Verbindungen:** höchstens 64 gleichzeitig (darüber antwortet uvicorn 503). Eine Verbindung, deren Kopfzeilen nicht binnen 10 Sekunden vollständig
  sind (ab dem Verbindungsaufbau und nach jeder Antwort), wird geschlossen, ein Inhalt, der länger als 10 Sekunden braucht, ergibt 408. Das ersetzt nicht
  die Fristen von Caddy davor.
* **Betrieb (Caddy und Compose):** Das 503, das uvicorn bei Überschreiten von `limit_concurrency` sendet, ist Klartext ohne die Schutzköpfe des Dienstes. Caddy
  davor fängt Fehler des Dienstes ab und liefert eine eigene Antwort mit denselben Köpfen (kein Code im Dienst nötig). Die Warnzeilen von uvicorn (zum Beispiel
  „Invalid HTTP request received“) gehen auf stderr: In der späteren Compose-Datei begrenzt `logging` mit `driver: json-file` und `options: max-size: 10m`,
  `max-file: 3` ihre Größe (D10: Die Container-Protokolle zählen zur Aufbewahrung).

## Protokoll und Aufbewahrung

Das Protokoll liegt in `<Daten>/protokoll/` und enthält nur Metadaten: Zeit, Bereich, Methode, Status, Platz, Dauer und bei Betreuer-Aufrufen die
Art. Nie Pfade, Abfragen, Köpfe, Tokens oder Texte. Das Zugriffsprotokoll des Webservers ist aus. Zwei Dateien:

* `dienst.jsonl` (alle Anfragen und Fehler, rotiert bei 10 MB mit einer Sicherung). Antworten, die ein Angreifer ohne Zugang erzeugen kann (400, 401,
  403, 404, 405, 413, 429, 431, auch abgelehnte Betreuer-Aufrufe), werden abgetastet: je Art, Bereich und Status höchstens 5 Zeilen je Minute, der
  Rest wird gezählt und als eine Zeile `abgetastet` mit der Zahl festgehalten.
* `betreuer.jsonl` (die Spur der Betreuer-Aufrufe mit gültigem Token): rotiert nie und überschreibt nie, nur eine Obergrenze von 5 MB. Eine Flut
  unberechtigter Anfragen kann sie nicht wegspülen.

Geschrieben wird in einem eigenen Faden hinter einer begrenzten Warteschlange (die Ereignisschleife wartet nie auf die Platte, ist die Schlange
voll, wird gezählt und verworfen). Beim Start und danach stündlich löscht der Dienst alle Daten (Tickets, Kommentare, Einwilligungen, Platz-Token,
beide Protokolldateien), wenn das Datum nach `WAMO_LABOR_DIENST_BIS` liegt und die Uhr seit dem Start plausibel läuft: Die Wanduhr darf der monotonen Uhr
höchstens einen Tag vorauslaufen, sonst löscht der Dienst nichts und schreibt `Uhrsprung` ins Protokoll (ein Sprung der Uhr nach vorn leert keine Plätze).
Der Dienst legt keine Sicherungen an.

## Prüfen

```text
cd labor-dienst
python -m pytest -p no:cacheprovider -q tests
```

Die Tests laufen ohne Netz und Server, außer den Integrationstests: Sie starten den Dienst auf Loopback und lassen das Abnahmeskript von W.A.I.S.
(`scripts/wamo_labor_abnahme.py`) und den Dienstclient des Kerns (`voice-core/labor_client.py`) gegen ihn laufen. Mit gemeinsamer falscher Uhr
dauert auch A4 nur Sekunden. Mit `WAMO_LABOR_ECHTZEIT=1` läuft die Abnahme zusätzlich mit echter Schreibrate (20 bis 25 Minuten).

Abnahme am echten Dienst (Aufruf aus dem Ordner `scripts`, mit der Python-Umgebung von `voice-core`):

```text
python wamo_labor_abnahme.py https://HOST[:PORT] --ja --token-datei <Datei mit dem Bearer des Kerns> --ca-datei <wamo-ca.crt>
```

Vorher legt der Betreuer für die Plätze 15 und 16 einen neuen Durchlauf mit Einwilligung an (`neuer-durchlauf`) und setzt sie danach zurück
(`zuruecksetzen`). Für die Betreuer- und Platz-Proben gibt es `--betreuer-pfad`, `--platz-pfad` und `WAMO_LABOR_PLATZ_TOKEN`. A10 (Netz) und der
Browserlauf bleiben von Hand am Server.
