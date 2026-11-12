# WAMO Labor-Dienst

Das Test-Jira des Testlabors am 28.11.2026. Bis zu acht Besucher legen an je einem Platz mit dem Assistenten WAMO Fehler als Ticket an, ändern,
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
nichts wird zur Laufzeit installiert, das Basisimage wird mit festem Digest gebaut).

## Umgebung

Der Dienst liest nur Umgebungsvariablen und Dateien. Eine fehlende oder unbrauchbare Einstellung beendet den Start mit Ausgang 2 und einer
Meldung ohne Werte.

| Variable | Bedeutung | Standard |
| --- | --- | --- |
| `WAMO_LABOR_DIENST_KERN_TOKEN_DATEI` | Datei mit dem Bearer des Kerns (`WAMO_LABOR_TOKEN`, 32 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-) | Pflicht |
| `WAMO_LABOR_DIENST_BETREUER_TOKEN_DATEI` | Datei mit dem Betreuer-Token, muss ein anderer Wert sein | Pflicht |
| `WAMO_LABOR_DIENST_DATEN` | Ordner für Datenbanken und Protokoll (im Container `/data`) | `/data` |
| `WAMO_LABOR_DIENST_HOST`, `WAMO_LABOR_DIENST_PORT` | Adresse zum Lauschen (im Container `0.0.0.0`) | `127.0.0.1`, `8080` |
| `WAMO_LABOR_DIENST_BIS` | Ende der Aufbewahrung (JJJJ-MM-TT, höchstens `2026-12-11`, ein späteres Datum ist ein Konfigurationsfehler), danach löscht der Dienst alle Daten. Geht die Uhr des Rechners vor dem 12.11.2026, startet der Dienst nicht (Ausgang 2) | `2026-12-11` |
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
Groß- und Kleinschreibung nicht, auch bei Umlauten. Der Speicher für Idempotenzschlüssel ist auf 2000 je Platz begrenzt (wirkt nur bei Missbrauch).

## Grenzen für Lesen, Fehlversuche und Verbindungen

* **Board:** höchstens etwa 2 Abfragen je Sekunde und Platz-Token (kurzzeitig 5), alle Plätze zusammen höchstens 20 je Sekunde, darüber 429
  `quote_erreicht` mit `Retry-After: 1`. Die Antwort wird je Platz eine Sekunde zwischengespeichert, jede Änderung des Platzes macht sie sofort ungültig.
* **Fehlversuche:** Falsche Bearer, Betreuer-Tokens und Platz-Token werden je Quelle gezählt (120 je Minute), danach antwortet der Dienst 429 mit
  `Retry-After: 60`. Ein richtiger Zugang wird nie gesperrt. Ein unbekannter Platz-Token wird nur im Speicher erkannt (kein Datenbankzugriff, kein
  Faden aus dem gemeinsamen Pool). Hinter Caddy gehört dessen Adresse in `WAMO_LABOR_DIENST_PROXYS`, sonst haben alle Besucher dieselbe Quelle.
* **Verbindungen:** höchstens 64 gleichzeitig (darüber antwortet uvicorn 503). Eine Verbindung, deren Kopfzeilen nicht binnen 10 Sekunden vollständig
  sind (ab dem Verbindungsaufbau und nach jeder Antwort), wird geschlossen, ein Inhalt, der länger als 10 Sekunden braucht, ergibt 408. Das ersetzt nicht
  die Fristen von Caddy davor.

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
beide Protokolldateien), wenn das Datum nach `WAMO_LABOR_DIENST_BIS` liegt. Der Dienst legt keine Sicherungen an.

## Prüfen

```text
cd labor-dienst
python -m pytest -p no:cacheprovider -q tests
```

Die Tests laufen ohne Netz und Server, außer den Integrationstests: Sie starten den Dienst auf Loopback und lassen das Abnahmeskript von WAMO
(`scripts/wamo_labor_abnahme.py`) und den Dienstclient des Kerns (`voice-core/labor_client.py`) gegen ihn laufen. Mit gemeinsamer falscher Uhr
dauert auch A4 nur Sekunden. Mit `WAMO_LABOR_ECHTZEIT=1` läuft die Abnahme zusätzlich mit echter Schreibrate (20 bis 25 Minuten).

Abnahme am echten Dienst (Aufruf aus dem Ordner `scripts`, mit der Python-Umgebung von `voice-core`):

```text
python wamo_labor_abnahme.py https://HOST[:PORT] --ja --token-datei <Datei mit dem Bearer des Kerns> --ca-datei <wamo-ca.crt>
```

Vorher legt der Betreuer für die Plätze 15 und 16 einen neuen Durchlauf mit Einwilligung an (`neuer-durchlauf`) und setzt sie danach zurück
(`zuruecksetzen`). Für die Betreuer- und Platz-Proben gibt es `--betreuer-pfad`, `--platz-pfad` und `WAMO_LABOR_PLATZ_TOKEN`. A10 (Netz) und der
Browserlauf bleiben von Hand am Server.
