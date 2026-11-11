# Änderungsprotokoll

Alle nennenswerten Änderungen an WAMO stehen in dieser Datei. Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionen folgen [Semantic Versioning](https://semver.org/lang/de/) in der Schreibweise X.Y.Z (Tags heißen vX.Y.Z).
Die Abschnitte der Versionen entstehen mit `scripts/wamo_version.py` aus den Commits, der Abschnitt Unveröffentlicht bleibt leer.

## [Unveröffentlicht]

### Hinzugefügt

- Start beim Tippen (Komfort-Paket KP-1A, nur Oberfläche, Schalter „Still starten beim Tippen“ je Gerät und Person, Standard aus): Der erste
  Tastendruck im Eingabefeld startet eine Sitzung ohne Mikrofon (neuer `StillMedienManager`, denn der Standardmanager der
  Transportbibliothek öffnet das Mikrofon beim Verbinden immer), Antworten kommen als Text, die erste Zeile wird gehalten und genau einmal
  gesendet. Nie auf Laborkonten, nie im verdeckten Tab, höchstens 6 Starts je 10 Minuten, nach einer Übernahme durch ein anderes Fenster
  erst nach „Hier übernehmen“. Leerlauf nach 20 Minuten nur für so gestartete Sitzungen und nie bei PC-Steuerung, Karte, Auftrag, Vorgang,
  Live-Bild oder Antwort. Betreiber: Eine stille Sitzung zählt für die Ruheprüfung und `ausleiten` als laufende Sitzung (README, Abschnitt
  „Start beim Tippen“).
- Entwicklungsaufträge im Betrieb, Büro (vorbereitet, standardmäßig aus, nicht ausgerollt; Zweig `feat/entwicklung-im-betrieb`, Konzept
  `docs/entwicklung-im-betrieb.md`, Schnittstellen und Pakete `docs/entwicklung-im-betrieb-arbeitsliste.md`). Kern: Im Büro zählt nur der
  neue Schalter `WAMO_ENTWICKLUNG_BUERO` (Standard aus), der alte `WAMO_ENTWICKLUNG` bleibt in den Units `aus` und wird dort ignoriert.
  Dazu `WAMO_ENTWICKLUNG_PROFILE` (Profilbindung, Pflicht), `WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD` und `WAMO_ENTWICKLUNG_KOSTEN_TAG_USD`
  (Pflicht, fehlend heißt aus) und `WAMO_ENTWICKLUNG_BEFEHLE_BUERO` (Standard aus). Jeder Fehler der Einstellung schaltet nur die
  Entwicklung aus, nie den Kern. Ein Auftrag braucht eine `BueroZulassung` (nur Admins mit Profil aus der Liste) und den eigenen
  Claude-Zugang des Profils, der Serverzugang gilt im Büro nie. Arten mit GitHub-Stufe verlangen ein Repo, das Projekt heißt
  `kern-<Name>` und wird aus dem Repo abgeleitet (Umlaute und ß werden wie jedes Zeichen außerhalb von a-z0-9- verworfen, nicht
  umgeschrieben). Jeder Start wird mit `auftrag_quittung` beantwortet (`ok` oder ein Grund als Satz), die Oberfläche meldet „gestartet“
  erst danach und zeigt Admins den Grund einer abgeschalteten Entwicklung in der Statusleiste (`entwicklung_grund`). Je Auftrag steht
  eine Zeile `entwicklungsauftrag` in `actions` (pending, approved, running, done oder failed, ohne Migration), sie zählt die Kosten je
  Auftrag und je Tag; ist die Datenbank nicht erreichbar, startet im Büro nichts.
  Dispatcher Version 7: neues Modul `infra/sokrates2/wamo_job_kernkanal.py`. Der Kanal (`lokal`, `pc`, `kern`) wird am benutzten
  SSH-Schlüssel erkannt (`ExposeAuthInfo`, zweite Schlüsseldatei `authorized_keys.d/wamo-kern`), im strikten Modus gilt alles Unklare
  als gesperrt. Im Kern-Kanal gelten eine Befehlsliste, die Regeln aus `/etc/wamo/kernkanal.conf` (Art, Repo, Dienste, Starts je
  Stunde und Tag, Laufzeit, Folgen, Abstand, Platz, `--max-turns` und `--max-budget-usd`), die Fork-Prüfung, der Ablauf und die
  Sperrdatei `kernkanal.notaus`. Abweisungen kommen als Ereignis `kanal_gesperrt` mit einem Code aus `KANAL_GRUENDE` und Exit 2. Der
  PC-Kanal verhält sich wie bisher und darf den Namensraum `kern-` nicht öffnen, `ping` meldet `kanal` und `kernkanal`.
  Skripte: `wamo-job-kern-einrichten.sh` (`schluessel`, `kanal`, `schalter`, `notaus-aufheben`, `status`), `wamo-job-kern-rueckweg.sh`
  (`notaus` in fester Reihenfolge, `--entfernen`) und der Rauchtest `wamo_job_kern_rauchtest.py`. Alle schreiben eine Bedienspur ohne
  Werte und starten nie einen Dienst neu, der Kern wechselt nur über `wamo-core-slots.sh umschalten-kern`. `wamo-job-einrichten.sh`
  verlangt jetzt Ruhe und sichert die vorige sshd-Konfiguration dauerhaft, `wamo-job-rueckweg.sh` verweigert, solange die Schlüsselzeile
  des Kerns besteht. Der Vertragstest `voice-core/test_kernkanal_vertrag.py` prüft Kern und Dispatcher gegeneinander (Grund-Codes,
  Projektnamen, `SCHRANKEN_VERSION`, Standardwerte, ssh-Optionen des Rauchtests). Keine Migration, `SELF_REPO` unverändert. Alle Proben des
  Rauchtests sind nur am Server prüfbar und dort noch nicht gelaufen.
- Dispatcher-Befehl `pruefen` (Version 6, standardmäßig AUS, nicht ausgerollt): Ein Befehl der Abnahme läuft im Projekt des Auftrags in
  einer Sandbox, die enger ist als der Auftragslauf (eigener Netz-Namensraum, kein Token, kein Claude-Zugang, kein Zustand, Zeitlimit
  120 s; `wamo-job.sh --modus pruefen`). Eingang ist eine stdin-Zeile `{"argv": [...]}`, Antwort eine JSON-Zeile mit `ok` nur bei einem
  echten Exit 0. Der Dispatcher erzwingt die Allowlist erneut (neues Modul `voice-core/abnahme_befehl.py`, nur Standardbibliothek, von
  `abnahme.py` und vom Dispatcher geteilt) und den Wächter, nimmt das Projekt nur aus `auftrag.json`, prüft Sperren, Selbsttest im Modus
  `pruefen` und Audit. Neue Form `uv run --no-sync --offline pytest ...` in der Allowlist (nur mit `.venv`, lokal nie ausgeführt).
  Die Einrichtung sichert die ersetzten Dateien (`/opt/wamo/vorher`), neuer Rückweg `wamo-job-rueckweg.sh`, Rauchtest
  `wamo_pruefen_rauchtest.py` (geschrieben, nicht ausgeführt).
- Befehlskriterien auf Sokrates-2, Kern-Seite von `pruefen` (vorbereitet, standardmäßig aus, nicht ausgerollt): Neues Modul
  `voice-core/abnahme_remote.py` mit `RemoteAusfuehrer` (ruft `ssh ... pruefen <job>`, eine stdin-Zeile `{"argv": [...]}`, stdin als
  Lebenslinie, eine JSON-Zeile Antwort mit Zeit- und Größengrenze) und `DispatcherPing` (Cache: 5 Sekunden je Aufruf, 5 Minuten gültig,
  Fehler heißt nicht möglich). Streng ausgewertet: erfüllt nur bei Prozess-Exit 0, `ok` true, einem echten `exit` 0, `abgebrochen` false
  und `v` gleich 1; alles andere ist nicht erfüllt mit einem Grund aus einer festen Liste. Die Ausgabe ist ein Datenfeld (höchstens 2 KiB)
  und geht nie an das Sprachmodell. Auflegen beendet ssh und ruft `cancel <job>`. `befehl:` geht auf Sokrates-2 nur mit
  `WAMO_ABNAHME_BEFEHLE=ja` und einem `ping`, der `pruefen` und dieselbe Prüfsumme der Allowlist meldet; sonst nennt die Ablehnung den
  Grund in einem Satz. `abnahme.pruefe_befehl` prüft mit einem entfernten Ausführer keine lokalen Pfade. Keine neue Umgebungsvariable.
  Nicht an einem echten Server geprüft (Messbefund und Reihenfolge des Ausrollens in der README, Abschnitt „Abnahme“).
- Komponententests für die Oberfläche: Neues, getrenntes Testsystem für React-Komponenten und Hooks (`npm run test:komponenten`,
  vitest mit jsdom und Testing Library, Dateien `*.comp.test.tsx`, Konfiguration `ui/vitest.config.ts`). `npm test` (`node --test`,
  Logiktests) bleibt unverändert. 90 Tests in vier Dateien: Tafel „Vorgänge“ (Karten je Zustand, Fortschrittsbalken mit
  `aria-valuenow` und `aria-valuetext`, „Zur Freigabe“ fokussiert die eigene Freigabekarte oder die Überschrift der Tafel,
  „Zuletzt beendet“ rendert erst nach dem Öffnen), Büro-Karte (Buttons, Enter, Leertaste, Escape mit Fokusrückgabe, Zustand als Wort
  und Form, Leerzustand, Hinweis auf unbekannte Geräte), Hook `useBueroStand` (Sichtbarkeit, Takt, Stopp bei 401 und 403, wachsender
  Abstand bei 503, Aufräumen) und Kopierknopf des Canvas. Neue devDependencies mit exakten Versionen (alle MIT): vitest, vite, jsdom,
  `@testing-library/react`, `/dom`, `/user-event`; die Produktionsabhängigkeiten ändern sich nicht. Am Produktivcode ändert sich nichts.
  Grenzen stehen in der README („Komponententests (Oberfläche)“).
- Lange Werkzeugausgaben kürzen (Paket 5): Der Kern verkleinert lange Ergebnisse von Werkzeugen (PowerShell-Listen, Testausgaben,
  Git-Ausgaben, JSON von Geräten, Webseitentext) verlustarm, bevor sie in den Kontext des Sprachmodells gelangen, ohne Fremdsoftware
  (`voice-core/ausgaben_kuerzen.py`). Zentrale Stelle ist ein Frame-Prozessor hinter der Aktivitätsanzeige: jedes Werkzeugergebnis
  kommt dort genau einmal an, Zeitleiste und Freigabekarten sehen das Original. Ab 3000 Zeichen (`WAMO_AUSGABE_SCHWELLE`, 0 = aus)
  fasst er gleiche Zeilen mit Zähler zusammen, behält Kopf, Schwanz und Zeilen mit Fehlern, ersetzt die Mitte durch eine Marke und
  entfernt in JSON leere Werte und kürzt lange Listen und Texte; Zahlen, Status und Fehlerfelder bleiben unverändert, das Ergebnis
  trägt `gekuerzt: true` und `ref`. Das Original liegt nur im Arbeitsspeicher der Sitzung (höchstens 20 Einträge und 1 MB), das neue
  Werkzeug `ausgabe_nachladen(ref, ab_zeile, anzahl)` liefert Teile gezielt zurück (nur lesend, höchstens 8000 Zeichen je Aufruf).
  Nie gekürzt werden Freigabe- und Schutzergebnisse (an jeder Stelle des Ergebnisses: `needs_approval`, `freigabe_id` oder ein
  Schutzstatus, auch tief in Listen oder eingebettet in Text), Werkzeuge mit eigener Kappung, Binärdaten und Ergebnisse unter der
  Schwelle. Zeilen mit Stichwort (error, blocked, gesperrt, critical und weitere) belegen höchstens die Hälfte des Budgets, die Felder
  `gekuerzt` und `ref` aus fremdem JSON werden umbenannt, JSON `Infinity` wirft nichts mehr.
  Eigene Marken lassen sich nicht fälschen (eingehende Texte werden gesäubert). 100 KB brauchen unter 5 ms; an den Beispielen der
  Tests sparen Verzeichnisliste, `git log`, Gerätestatus und lange Texte 90 bis 99 Prozent der Zeichen, pytest mit drei Fehlern etwa
  die Hälfte. Je Sitzung steht am Ende eine Zeile mit Zahlen im Log. Das ersetzt den zurückgestellten Befund „Git-Ausgabe streamend
  kappen“ nur teilweise: Der Host liest die Ausgabe weiter vollständig. Wirkt nach neuem Kern-Release; ungeprüft gegen echte Sitzungen.
- Geräte-Lücken schließen (Paket 3), alles nur lesend: Der Gerätedienst hat einen SNMPv3-Lesetreiber `snmp` für Switches und Access
  Points (nur authPriv mit SHA-2 und AES, eigener Lese-Benutzer, nie v1 oder v2c, nie SET; Name, Laufzeit, Ports oben und unten,
  optional Fehlerzähler; Ausgang UDP 161 in nft und `IPAddressAllow`; Bibliothek pysnmp 7.1.30 mit Prüfsumme im Lock, `cryptography`
  ab 47). Dazu die passive Erkennung unbekannter Geräte über die Hostliste der FRITZ!Box (TR-064 Hosts:1, kein Scan): Werkzeuge
  `unbekannt_zaehlen` (alle Rollen, nur eine Zahl) und `unbekannte_geraete` (nur Admin, gekürzt), optionale Datei
  `/etc/wamo-geraete/bekannt.toml` für Laptops und Handys, Zwischenspeicher 60 Sekunden, Namen und MAC-Adressen nie in Audit oder
  Protokoll. Die Büro-Karte zeigt bei Zahl größer 0 ruhig „n unbekannte Geräte im Netz“ (grau, mit Sprechtipp für Admins), der Kern
  füllt dafür das Feld `unbekannt` von `GET /wamo/api/buero/status` (jede Störung dort lässt nur die Zahl weg) und kennt die Klassen
  Switch und Access Point. Der Router-Status ist zusätzlich über TR-064 wählbar (`status_quelle = "tr064"`, Standard bleibt `upnp`).
  Neuer Abschnitt „Router schreibend (Entwurf, nicht gebaut)“ in `geraete/README.md`. Wirkt nach neuem Release von Gerätedienst, Kern
  und Oberfläche; ohne eingetragenen Switch und ohne gepflegte `bekannt.toml` ändert sich im Büro zunächst nur die Zahl der
  unbekannten Geräte.
- Büro-Karte (Paket 2): Neuer Reiter "Büro" in der Kommandozentrale mit einer Radialansicht aller gemeinsamen Bürogeräte. In der Mitte WAMO
  (Sokrates-2), je Gerätegruppe ein Ring (Server, Drucker und Scanner, Netz, Speicher, Medien, Steckdosen), je Gerät ein Punkt: weiß
  gefüllt in Ordnung, rot mit Strich und langsam pulsierend Störung, grau und hohl unbekannt. Ein Klick oder Enter öffnet eine
  Detailkarte mit Zustand in Klartext und Sprechtipp; Steuern bleibt der Sprache vorbehalten. Barrierefrei (Buttons, Fokus, Form
  neben Farbe, Liste unter der Grafik, reduzierte Bewegung). Der Kern liefert dafür `GET /wamo/api/buero/status`: ein schmales Modell
  je Gerät aus dem Gerätedienst (ohne Adressen, Seriennummern und Rohtexte, ohne die Geräteliste des Routers), jede Rolle, höchstens 6 Abrufe
  je Minute und Profil. Die Oberfläche ruft alle 30 Sekunden, nur bei sichtbarem Reiter. Wirkt nach neuem Kern-Release und neuer Oberfläche.
- Prüfbares Ziel mit Runden für Entwicklungsaufträge (Paket 4): `entwicklung_vorbereiten` nimmt `abnahme` (höchstens 5 Kriterien),
  `max_runden` (Standard 3, höchstens 5) und `zeitlimit_min` (Standard 30, höchstens 60) an. Der Auftrag läuft in Runden: arbeiten,
  prüfen, bei Nichterfüllung die nächste Runde mit genau den offenen Kriterien, bis alles erfüllt ist oder Runden oder Zeit erschöpft
  sind; dann endet der Vorgang als Fehler mit "Ziel nach n Runden nicht erreicht", nie als fertig. Aussagekriterien beurteilt ein kleines
  Modell mit strengem JSON, Befehlskriterien (`befehl: pytest -q`) laufen ohne Shell nach einer echten Allowlist je Programm (nur
  ausdrücklich aufgeführte Optionen, höchstens 12 Argumente zu je 200 Zeichen, keine `@datei`-, Netzwerk- und Laufwerkspfade), durch den
  Wächter, mit Zeit-, Ausgabe- und Umgebungsgrenze, nur in der Betriebsart `lokal` (auf Sokrates-2 fehlt dafür noch ein
  Dispatcher-Befehl) und nur mit `WAMO_ABNAHME_BEFEHLE=ja`. Standard aus, weil ein Befehl Projektcode (conftest, Makefile, Tests) ohne Hook
  mit den Rechten des Kerns ausführt: Das ist neu gegenüber dem Ablauf vor Paket 4 und soll eine bewusste Entscheidung des Betreibers sein,
  kein Nebeneffekt. Ohne den Schalter lehnt `entwicklung_vorbereiten` ein `befehl:` mit klarer Meldung ab, Aussagekriterien bleiben
  möglich. Die Startkarte zeigt die Kriterien (Befehle in Festbreite, nur als Text), damit die Person bestätigt, was sie sieht. Die
  Pfadprüfung läuft mit Frist im Arbeitsthread, nie in der Ereignisschleife. Bekannte Grenze, nicht umgebaut: Nach dem Ende des
  Hauptprozesses überleben Enkelprozesse (kein `killpg`, unter Windows kein Job Object). Freigaben bleiben
  unangetastet und zählen nicht gegen das Zeitlimit. Die Tafel "Vorgänge" zeigt "Runde n von m", "wird geprüft" und die Kriterien mit
  Hinweis ohne Änderung. Neu: `voice-core/abnahme.py`, Persona-Schalter `WAMO_PERSONA_ABNAHME` (Standard an).
- Aufgabenleiste mit Livefortschritt (Paket 1): Die Tafel "Vorgänge" zeigt je Boss-Aufgabe und je Entwicklungsauftrag eine Karte mit
  Status als Wort, Gesamtbalken mit Prozent, Restzeit (nur mit fester Zahl der Schritte, sonst ehrlich "wird geschätzt") und "Fertig in mm:ss",
  nummerierten Schritten mit eigenem Balken, Prüfkriterien und "Runde n von m". "Zur Freigabe" springt zur vorhandenen Freigabekarte,
  "Ergebnis ansehen" wählt das Element im Canvas, beendete Vorgänge stehen im einklappbaren Abschnitt "Zuletzt beendet". Der Kern meldet
  sie als Servernachricht `vorgang` (`voice-core/vorgaenge.py`, gedrosselt auf vier Nachrichten je Sekunde und Vorgang, Schritte
  eines Entwicklungsauftrags aus den Aufgabenwerkzeugen von Claude Code in `vorgang_stream.py`). Der Verlauf kommt beim Start einmal über
  `GET /wamo/api/vorgaenge` (die letzten 20 des eigenen Profils, neueste zuerst). Aufbewahrung: Der Stand (Schritte, Kriterien, Titel) bleibt
  30 Tage, danach leert ihn der Kern (`aufraeumen`, höchstens einmal je Stunde und Prozess, Zeilen löscht er nicht); ein Vorgang, der nach
  24 Stunden noch offen ist (der Kern ist beim Ende abgestürzt), gilt als abgebrochen. Ein Endstand wird nie von einem späteren Zwischenstand
  überschrieben. Die Balken sind `role="progressbar"`, der Zustand steht nie nur als Farbe da,
  `prefers-reduced-motion` schaltet die Bewegung ab. **Migration 0017 (Tabelle `vorgaenge`)** liegt seit dem 09.11.2026 auf `wamo_dev` und `wamo_test`: Ohne die Tabelle bleibt der
  Verlauf leer, die Anzeige der laufenden Sitzung geht auch ohne. Die Zeitleiste "Werkzeuge" und ihre Karte `boss_aufgabe` sind unverändert.
- Canvas: Nach dem Kopieren eines Codeblocks erscheint 1,5 Sekunden ein Chip "Kopiert" (Live-Region, `aria-live="polite"`); die Beschriftung des
  Knopfes bleibt "Kopieren". Schlägt das Kopieren fehl, steht dort "Nicht kopiert".
- Karte "Änderungen" (Schritt 4 und 1.5b des Gesamtplans): Der Release-Dienst nennt im `status` Commit, Baum, Prüfstand, Diff, Wirkungsflächen und eine
  Versionsempfehlung aus den Commit-Titeln (nur als Warnung). Die Karte zeigt sie und bietet den Dialog "Änderung freigeben" mit frischem Code
  (höchstens 45 Sekunden alt beim Senden) an; gesendet wird an den Freigabe-Dienst des Ankers mit genau Commit und Baum. Sonderfreigaben mit Pflicht-Haken,
  eigene Anfragen nicht selbst freigebbar, gesperrte nie. Anker und Freigabe-Dienst unverändert.
- Live-Bild vom Handy einschalten (Entscheidung Niko 04.11.2026): Der Admin gibt in der Ansicht einen frischen Code der
  Authenticator-App ein und bittet den eigenen Laptop um das Live-Bild. Der Kern prüft Token (gleiches Profil, Admin) und Alter
  des Codes (höchstens 120 s, `bild_fern.py`), der Host legt nur `live-bild-anfrage.json` ab (`live_bild_anfordern`, nur für den
  Kern), und die Desktop-App am Laptop prüft selbst noch einmal (Gerätekennung, Besitzer, höchstens 60 s alt, Stufe admin,
  Einwilligung am Laptop liegt schon vor, einmalig, nie ein Dialog) und schaltet erst dann ein, mit rotem Rahmen und Meldung
  am Laptop, höchstens 30 Minuten. Die Einwilligung am Laptop hat jetzt die Version 2 und nennt den Fernstart (Befund der
  unabhängigen Prüfung): Wer vorher eingewilligt hat, wird beim nächsten Einschalten am Laptop noch einmal gefragt, erst dann gilt der
  Fernstart. Jede Fernanfrage zählt nur einmal (Kennung), auch wenn sich die Datei nicht löschen ließ. Ohne frühere Einwilligung am Laptop bleibt alles aus. Wirkt nach neuem Kern-Release und neuem
  Desktop-Installer.
- Fernzugriff: Tastaturleiste für Handy und Laptop (Erweiterung `WAMO Eingabe` für Guacamole, `infra/sokrates2/wamo-rd/erweiterung/`).
  Ein Tastatur-Knopf öffnet ein Eingabefeld (am Handy erscheint dort die Bildschirmtastatur des Geräts) und die fehlenden Tasten
  Esc, Tab, Pfeile, Entf, Pos1, Ende, Bild auf und ab sowie einrastendes Strg, Alt und Win. Alles läuft über die vorhandene
  Verbindung, es entsteht kein neuer Weg nach draußen. Das Einrichtungsskript baut das Jar und Compose hängt es schreibgeschützt in
  `/etc/guacamole` ein (zwei Einbindungen im Webcontainer, der Rauchtest erwartet jetzt zwei).

### Behoben

- Gerätedienst, Fernseher: Der zweite Fernseher (Samsung 85 Zoll QLED GQ85Q70DATXZG, im WLAN, Adresse in `geraete.toml`) war nur als
  auskommentierte Vorlage vorhanden, WAMO fand deshalb nur den LG. Am 11.11.2026 am Server als `fernseher-2` eingetragen, Netzfreigabe des
  Dienstes neu erzeugt (`wamo-geraete-einrichten.sh`) und beide Fernseher gekoppelt (`wamo_geraete.koppeln`). Die Vorlage `geraete.toml.example`
  führt den Eintrag jetzt. Außerdem entschlüsselt der Treiber den Namen, den Tizen als HTML liefert (`85&quot; QLED`), vor der engen Zeichenliste:
  WAMO sagte "85quot QLED", jetzt "85 QLED" (Test `test_samsung_name_mit_html_zeichen_wird_lesbar`).
- Testlabor, Integration der Stränge K1, K2 und S (Zweig `labor/int`, nicht ausgerollt; Sicherheitsreview des Gesamtstands). Der Klick auf eine
  Labor-Freigabekarte (`freigabe` mit job `labor`) erreicht jetzt `LaborFreigaben`: `build_session` hängt den Entscheider in die Annahme
  (`LaborSitzung.freigabe`), vorher blieb jeder Klick wirkungslos. Die Erlaubnisliste der Werkzeuge wird für das Endergebnis geprüft, nach den
  Ticket- und Mail-Werkzeugen. Fällt der Dienst zwischen Annahme und Werkzeugen aus, bekommt die Person eine klare Ablehnung (503) statt einer
  Ausnahme, der Modellzugang wird geschlossen; das Ende des Durchlaufs erledigt offene Karten vor dem Schließen. Es gibt nur noch einen Weg zum
  Dienst der Kollegen: `labor_durchlauf.ClientQuelle` nutzt `labor_client.client_aus_umgebung` (Adresse, Token, Zertifikat, Antwort streng, keine
  Proxys aus der Umgebung, nie die Attrappe), die Mindestversion der Einwilligung gilt in den Werkzeugen wie bei der Annahme. Jeder Aufruf des
  Dienstes hat eine Gesamtfrist, die Durchlaufabfrage beim Klick höchstens 10 Sekunden. Fällt der Dienst länger als 60 Sekunden aus (12
  Fehlversuche der Wache), endet die Sitzung samt Textspeicherung. Ratenbegrenzung im Kern (`labor_limit.py`, Werte über Einstellungen
  änderbar): getippter Text mit 2 Sekunden Abstand und 40000 Zeichen je Sitzung (`WAMO_LABOR_TEXT_ABSTAND_SECS`, `WAMO_LABOR_TEXT_MAX_ZEICHEN`),
  höchstens 10 Websuchen je Sitzung (`WAMO_LABOR_SUCHEN_MAX`), höchstens 20 `/start` und Offer je Profil und Minute mit 429 und Code
  `labor_zu_viele_anfragen` (`WAMO_LABOR_ANFRAGEN_MAX`). Der Laborprompt sagt bei `WAMO_LABOR_TEXT_SPEICHERN=an` nicht mehr, es werde nichts
  gespeichert. Alle 13 Einstellungen `WAMO_LABOR_*` stehen in `ERLAUBT` von `infra/sokrates2/wamo_core_env.py` (nicht in `AUS_ENV` des
  PC-Skripts), `WAMO_LABOR_TOKEN` ist ein Geheimnis und wird nie angezeigt. Reihenfolge beim Ausrollen: erst die Migration 0018, dann der Kern.
  Ohne die Spalten `labor_art`, `labor_platz` und `labor_aktiv` bekommt jedes Profil 503, auch der Admin (gewollt, Ausfall statt Öffnung).
  Tests: Der Dienst der Kollegen ist in den Tests an einer Stelle die Attrappe (`labor_testhilfe.py`), ein Test führt eine ganze Laborsitzung von der
  Annahme über Werkzeuge und Freigabeklick bis zum Ende des Durchlaufs durch `build_session`. Das Schau-Profil ohne Platz hat laut Migration kein
  Test-Jira und damit nur die Basiswerkzeuge (Plan 3.1 nennt Ticket und Mail auch für das Schaufenster, offen für Niko).
- Ruhe-Prüfung von `wamo-job-einrichten.sh`: Die Benutzerdienste `xdg-document-portal` und `xdg-permission-store` von `wamo` (D-Bus-aktiviert,
  laufen seit Wochen) zählten als laufender Auftrag und brachten die Einrichtung mit Exit 3 zum Abbruch (nichts geändert). Beide Namen
  stehen jetzt in der Ausnahmeliste, jeder andere Prozess und jeder Auftrag neben den Portalen bricht weiter ab (Test).
- Rauchtest des Kern-Kanals (`wamo_job_kern_rauchtest.py`): Zwei Nein-Proben erwarteten einen Grund, den der Dispatcher am Server nicht
  meldet, obwohl er die Anfrage richtig abweist (Exit 2, Audit-Log `ABGEWIESEN ... kanal=kern`). Die Probe „Start mit Projekt wamo“ gilt auch
  mit der Abweisung des Selbstschutzes (der greift vor der Regel des Kanals), die Probe „sftp“ auch als stumme Abweisung (sshd leitet stderr
  eines Subsystems nicht an ssh weiter). Beides ist nur für diese zwei Proben zugelassen und mit Tests belegt; der Dispatcher ist unverändert.
- Prüfungen vor dem Zusammenführen (Sicherheit, Datenbank, Python, React), Befunde eingearbeitet: Ein Entwicklungsauftrag mit Abnahme gilt
  während der Prüfung als „prüfe Abnahme“ und erst nach dem Urteil als fertig, eine Ausnahme im Lauf beendet Auftrag und Vorgang als
  fehlgeschlagen statt sie hängen zu lassen. Der Kürzer für Werkzeugausgaben hat eine harte Obergrenze (256 KB Analyse, darüber Kopf und
  Schwanz) und lädt auch Texte ohne Zeilenumbruch zeichenweise nach (`ab_zeichen`). Die Erkennung unbekannter Geräte holt die Hostliste der
  FRITZ!Box über den bewährten Weg mit zwei Anfragen statt einer Anfrage je Eintrag, der SNMP-Treiber hat widerspruchsfreie Zeitgrenzen.
  Die Vorgangstabelle hat eine Konsistenzregel zwischen Status und Ende, den Fremdschlüssel auf `profiles` und ein stündliches Aufräumen
  (offene Vorgänge nach 24 Stunden abgebrochen, Stände nach 30 Tagen geleert). In der Oberfläche führt „Zur Freigabe“ nie zur Karte eines
  anderen Vorgangs, die Tafel „Vorgänge“ rendert nur bei sichtbarem Reiter und gedrosselt, und die Büro-Karte hört bei 401 und 403 auf zu
  pollen.
- Gerätedienst: Der Eintrag „WMC JBL ULTIMATE“ (192.168.178.90) steht nicht mehr in `geraete.toml.example`, in der Doku und in den
  Beispieldaten. Das Gerät war am 21.10.2026 bei der Erkundung sichtbar, ist im Büronetz aber nicht vorhanden und meldete dort dauerhaft
  eine Störung. Der Treiber `upnp-renderer` bleibt für künftige Lautsprecher erhalten. Auf Sokrates-2 ist der Eintrag am 09.11.2026
  aus `/etc/wamo-geraete/geraete.toml` entfernt (Sicherung `geraete.toml.vor-jbl-0911`); die Ausgangsregel für die Adresse bleibt
  bis zum nächsten Lauf von `wamo-geraete-einrichten.sh` bestehen und ist wirkungslos.
- Status-Konto auf Spark-Servern mit globaler `AllowUsers`-Liste (Befund vom 09.11.2026 auf Caesar): Das Einrichtungsskript
  `infra/sparks/wamo-status-einrichten.sh` richtete `wamo-status` ein, doch sshd wies das Konto mit „Permission denied (publickey)“
  ab, weil eine globale Liste nur Admin-Konten erlaubt. Der Drop-in setzt jetzt im Block `Match User wamo-status` eine eigene
  Zeile `AllowUsers wamo-status@192.168.178.136`. Sie ersetzt dort die globale Liste (mit `sshd -T -f` an einer Wegwerf-Konfiguration
  gemessen), gilt nur für dieses Konto und nur von Sokrates-2, andere Konten bleiben unverändert. Wirkt nach erneutem Lauf des Skripts
  auf dem Server und neuer Bestätigung.
- Test vom 04.11.2026 am Laptop: Der Fernzugriff auf die Sparks zeigte ein schwarzes Fenster. Ursache: Die CSP des Gateways
  (`object-src 'none'`, `frame-ancestors 'none'`) blockierte das `<object>` auf die leere Seite `blank.html`, mit dem Guacamole die
  Größe der Anzeige misst. Die Anzeige blieb 0x0 und der Tunnel wurde nie geöffnet. Jetzt `object-src 'self'`, `frame-src 'self'`
  und nur für diese eine leere Seite `SAMEORIGIN`; alle anderen Seiten bleiben gegen Einbetten geschützt.
- PC-Host: Reine Lesebefehle verlangten unnötig eine Freigabe (`*` als Muster von `-Filter`/`-Include`/`-Exclude` und in
  `Select-Object *`, `$env:USERPROFILE` und vier weitere feste Variablen, wenige Registry-Schlüssel ohne Zugangsdaten wie
  Remotedesktop und Richtlinien). Muster stehen nur in reinen Listenbefehlen, nie vor Inhalt lesenden Befehlen. Der Freigabetext
  sagt nicht mehr, der Befehl „verändere etwas“.
- Persona: `WAMO_PERSONA_RUECKFRAGEN` ist jetzt Standard (höchstens eine Rückfrage, Aufträge am Stück, kein erneutes Abrufen nach
  einer Freigabe, keine erfundenen Ursachen). Der Fernzugriff-Baustein nennt die Auslöser (Spark, Sokrates, Caesar, Remote Desktop)
  und verbietet die Prüfung am Laptop.
- Desktop-App: Die geplante Aufgabe "WAMO Tunnel" startete PowerShell alle 5 Minuten sichtbar. Ist Windows Terminal das
  Standardterminal (Windows 11), blitzte trotz `-WindowStyle Hidden` bei jedem Start ein Terminalfenster auf. `scripts/wamo-tunnel-einrichten.ps1`
  startet die Aufgabe jetzt über `conhost.exe --headless`, dabei entsteht kein Fenster. Auf bestehenden PCs wirkt das, sobald das
  Einrichtungsskript die Aufgabe das nächste Mal anlegt (neuer Desktop-Installer, Anmeldung in der App).

- Testlabor, Migration 0018 und Verwaltung (Befunde F1 bis F10 des Datenbank-Reviews, Zweig `labor/mig`; nichts davon ist ausgerollt, die
  Migration wurde noch nie angewendet und deshalb direkt geändert). `scripts/wamo_labor.py` meldet sich als Login-Rolle `wamo_admin` an
  (Passwort aus `WAMO_ADMIN_DATABASE_URL`, nur über die SSH-Eingabe zum Server und dort als `PGPASSWORD` von `docker exec`) statt per
  `set local role` als `postgres`: Das scheiterte, weil `postgres` im Supabase-Image kein Superuser ist und keine Rolle annehmen darf. Nur
  die Kontosuche in `auth.users` läuft als `postgres`. `labor_loeschen` lehnt in den ersten 3 Minuten nach der letzten Änderung des Profils
  ab (Sperrfrist: der Kern kennt ein Profil bis zu 2 Minuten aus dem Zwischenspeicher), prüft die Sitzungen vor dem Ergebnis noch einmal
  und sperrt die Zeile nur noch mit `for no key update`; das Skript wartet die Sperrfrist ab und ruft die Funktion erneut auf.
  `labor_zaehlen` und `labor_loeschen` behandeln `device_events` gleich, `feedback` geht im Zeitfenster über die Turns der gelöschten
  Sitzungen mit. Die Sitzungsgrenze der Löschfunktion steigt auf 135 Minuten (Standard des Kerns 120 plus mehr als 10, Test gegen
  `bot.py`). Der Selbsttest der Migration prüft alle Rollen aus `pg_roles` statt einer festen Liste, der Rückweg ist eine einzige
  Anweisung und bricht auch ohne `--single-transaction` ab. Die Köpfe von Migration und Rückweg nennen die Reihenfolge mit dem Kern
  (Migration vor dem Release, Rückweg erst nach der Rückstellung, sonst antwortet der neue Kern für alle Nutzer mit 503). GoTrue-Spuren
  (`auth.audit_log_entries`, `auth.sessions`) und `model_access` stehen mit Grund in der Löschliste, sie verschwinden mit den Konten am
  30.11.2026. Der SQL-Test braucht keinen Superuser mehr (`disable trigger user`), nimmt freie Werte (Laborplätze 14 bis 16, Tunnelplätze
  60 bis 63) und prüft Sperrfrist, späte Rückmeldungen und Ereignisse fremder Profile. Neu im Test: Wahrheitstabelle der CHECK-Bedingungen
  aus dem SQL, lexikalische Prüfung der gerenderten SQL-Dateien, Fremdschlüssel-Parser der Löschliste für alle Schreibweisen.

### Geändert

- Fernzugriff auf die Sparks ohne frischen Code je Start (KP-2, Entscheidung vom 11.11.2026: Der Code beim Anmelden genügt; nicht
  ausgerollt, Reihenfolge Migration, Kern, Oberfläche). Der Kern verlangt aal2 mit einem totp-Eintrag im Token, aber keinen Code, der
  höchstens 120 Sekunden alt ist. Rolle admin, aktives Profil, ein Zuschauer je Ziel, Raten, 60 Minuten Höchstdauer und die Verbindung
  (RDP, keine Zwischenablage, kein Laufwerk, kein Ton, keine Aufzeichnung) bleiben; Mitglieder und Laborprofile kommen weiter nicht heran,
  `geraete_admin_api.py`, die Release-Freigabe und die Geräteverwaltung behalten ihren eigenen frischen Code. Neuer Stufenschalter
  `WAMO_RD_ANMELDUNG_MAX_MIN` (Minuten, 1 bis 43200, in `ERLAUBT` von `wamo_core_env.py`): Ist die Anmeldung älter, lehnt der Kern mit 403 und
  `zweiter_faktor_zu_alt` ab und der Dialog zeigt das Codefeld. Leer oder 0 heißt aus (Standard); jeder ungültige Wert gilt als strengste
  Stufe (frischer Code wie vorher) und schreibt eine Fehlerzeile. Migration `0019_rd_anmeldung_genuegt` (mit `.down.sql`, statische Tests
  `test_migration_0019.py`, SQL-Test) ersetzt nur `rd_sitzung_beginnen`: Die Frist von 120 Sekunden und „ein Code, ein Start“ entfallen, neu sind
  eine Obergrenze von 31 Tagen und ein Hinweis (art `system`, ohne Adresse und Zugangsdaten) an alle aktiven Admins bei jedem Start (KP-2b).
  Wettlauf zweier Starts eines Profils: ein Lock je Profil um Datenbank und Gateway, nach dem Anmelden prüft der Kern die Datenbankzeile noch
  einmal und beendet die Gateway-Sitzung sofort, wenn ein anderer Kern sie abgelöst hat (Slot a und b), damit nie zwei Zuschauer auf einem Ziel
  bleiben. Der Dialog fragt den Code nur noch auf Verlangen des Kerns; der Fokus liegt beim Öffnen nie auf „Verbinden“, und das Werkzeug
  `fernzugriff_oeffnen` zeigt weiter nur den Abschnitt (Befund M7, Persona und Werkzeugtext angepasst, Tests im Kern und in der Oberfläche).
  **Restrisiko, ausdrücklich akzeptiert:** Ein gestohlenes Token startet jetzt allein Sitzungen, bis die Anmeldung abläuft (Kern 168
  Stunden, Browser 24 Stunden, App 7 Tage); der Hinweis an alle Admins ist eine Erkennung, kein Schutz (`docs/remote-desktop.md`, Abschnitt 3.6).
- Slot-Betrieb: Solange eine Instanz von `wamo-core@.service` läuft oder die Slot-Verweise stehen, startet keines der Einrichtungsskripte
  (`wamo-core-einrichten.sh`, `wamo-ui-einrichten.sh`, `wamo-rd-einrichten.sh`) die alte Unit `wamo-core.service` mehr neu. Sie nennen den
  Hinweis auf `wamo-core-slots.sh umschalten-kern` und enden mit Exit 0. `scripts/wamo_kern_sokrates2.py` verweigert `neustart`,
  `--neustart` und `alles` im Slot-Betrieb mit Exit 3, `status` zeigt Slots und Schalter (nur Namen). `wamo-core-slots.sh`,
  `wamo-core-vorpruefung.sh` und `wamo-core@.service` sind unverändert (der Anker prüft die Prüfsumme des Slot-Skripts).
- Auslagerung aus `voice-core/dev_agent.py` (reine Verschiebung von 21 Definitionen, ohne Änderung der Logik, Zweig
  `refactor/dev-agent-module`): `dev_agent_weg.py` (SSH-Ziel, Schlüssel, Hostschlüssel, `ssh_argv`, `ssh_problem`, `remote_status` und die
  Statustexte des Dispatchers) und `dev_agent_regeln.py` (Umgebungs-Allowlist, `permissions.deny`, Regeltexte für Claude Code). `dev_agent`
  importiert alle Namen, sie bleiben dort erreichbar. Wer die Lesbarkeit einer Datei in einem Test vortäuscht, ersetzt `_lesbar` in
  `dev_agent_weg`. Die Selbstschutz-Prüfungen (`project_slug`, `repo_pruefen`, `repo_aus_auftrag`, `SELF_REPO`) bleiben bewusst in `dev_agent.py`,
  ebenso der Dispatcher als eine Datei (Begründung in `docs/entwicklung-im-betrieb-arbeitsliste.md`, Abschnitt 7).

### Hinzugefügt

- Datenbankschicht für Release-Dienst und Freigabe-Dienst (`infra/sokrates2/pg/`): Verbindung mit TLS verify-full und festen
  Zeitgrenzen, strenge Prüfung von Zugangsdatei und CA-Datei, bereinigte `PG*`-Umgebung, Fehlerabbildung ohne Servertext, Adapter
  für beide Dienste. Geprüft gegen ein echtes Wegwerf-Postgres mit den echten Migrationen und Rollen (28 Tests, unabhängige
  Sicherheitsprüfung, Funde umgesetzt). Dabei gefunden und behoben: Migration `0016_release_freigaben_lesen` (der Trigger aus
  0013 verlangte für `FOR SHARE` ein Update-Recht, das der Release-Dienst bewusst nicht hat, er hätte nie auf `veroeffentlicht`
  schalten können). 0016 ist auf `wamo_dev` und `wamo_test` angewendet; die Login-Rollen `wamo_release_s2` und `wamo_anker_s2` mit
  root-eigenen Zugangsdateien richtet `scripts/wamo_dienst_db.py` ein und prüft sie über Supavisor mit TLS verify-full. Die Zeitgrenzen
  gelten als `SET LOCAL` in jeder Transaktion, weil Supavisor Startoptionen ignoriert. Noch nicht in den Diensten verdrahtet.
- Freigabe-Dienst des Ankers, Schritt 1.4a (`infra/sokrates2/anker/freigabe.py`): Regeln für die Freigabe einer Release-Anfrage durch
  einen Admin. Token (ES256, Algorithmus fest, Aussteller und Zielgruppe geprüft), frischer zweiter Faktor über den jüngsten
  `totp`-Eintrag (höchstens 60 Sekunden, nicht aus der Zukunft), Bindung an die gesehene Commit-ID und Tree-ID, Drosselung auf
  monotoner Uhr, einheitliche Antwort für Nicht-Admins, Abbildung der Trigger-Sätze aus Migration 0013 auf feste Codes. Ohne Netz
  und ohne Datenbank, mit Attrappe; 47 Tests, unabhängige Sicherheitsprüfung. Prozess, Caddy-Route und Datenbankanbindung folgen
  (Schritt 1.4b).
- Fernzugriff, weitere Ziele und Einrichtung (Ergänzung): Sokrates-1 und Caesar als Ziele; Passwort-Übergabe vom Ziel an das Gateway
  ohne Bildschirm und Argument (`wamo-rd-konto-einrichten.sh --fern` und `--uebernehmen`); `wamo-sudo-einrichten.sh` (sudo ohne
  Passwort nur für feste Befehlszeilen root-eigener Skripte); `wamo-rd-ziel-einrichten.sh` macht den TLS-Schlüssel für xrdp lesbar
  (Gruppe `ssl-cert`) und startet die Sitzung mit eigener `~/startwm.sh` (das Systemskript `90xbrlapi` hielt sie auf Servern ohne
  Braillezeile an); `xrdp-einhegen.sh` erkennt `ufw` auch bei deutscher Ausgabe.
- Fernzugriff auf die Sparks (Remote Desktop über das Gateway auf Sokrates-2, Stufen 3 und 4 von `docs/remote-desktop.md`):
  Kern-API `GET /wamo/api/rd/ziele`, `POST /wamo/api/rd/start` und `POST /wamo/api/rd/ende` (`voice-core/rd_api.py`, Schalter
  `WAMO_RD_AKTIV`, Standard nein). Nur Rolle admin mit frischem zweiten Faktor (Code höchstens 120 Sekunden alt) bei jedem Start;
  feste Zielliste, ein Zuschauer je Ziel, höchstens 60 Minuten Dauer, 10 Minuten Leerlauf, keine Zwischenablage, kein Laufwerk,
  keine Aufzeichnung. Der Kern baut die signierte Anweisung für Guacamole selbst, der Browser bekommt nur die Adresse mit dem
  Sitzungstoken. Migration 0015 (`rd_sitzungen`, Protokoll nur wer, Ziel, Start, Ende, Grund), Zugangsdienst-Gerüst in
  `infra/sokrates2/wamo-rd/zugang/` mit Prüfung gegen das echte Guacamole, Ausgangsregel `infra/sparks/wamo-rd-ausgang-einrichten.sh`
  (das Konto `wamo-rd` erreicht nur das Internet) und neuer Abschnitt Fernzugriff in den Einstellungen (nur Admin). Noch nicht
  ausgerollt.
- Gesprächsoptimierung (Recherche und Maßnahmen in `docs/gespraech-optimierung.md`): Schalter `WAMO_PERSONA_GESPRAECH`
  (Prompt-Block Gesprächsführung: Selbstkorrektur, Rückbezug, nicht wiederholen, eine Frage) und `WAMO_WARTESATZ_MODELL`
  mit `WAMO_WARTESATZ_MODELL_SECS` (ein kurzer Satz, wenn das Modell 2,5 s lang kein Zeichen liefert), beide Standard aus bis
  zur Messung; Kennzahl `folge_ton_ms` im Messweg (Antwortzeit nach dem Einwurf).
- Geräteverwaltung für Admins in den Einstellungen (Geräte und PC): Geräte freigeben, ablehnen, sperren, entsperren und
  widerrufen per Oberfläche statt per SQL. Bestätigungsdialog mit Fingerabdruck, frischem Code des zweiten Faktors (höchstens
  120 Sekunden alt) und Grund bei Sperre und Widerruf; Kern-API `GET /wamo/api/geraete/verwaltung` und
  `POST /wamo/api/geraete/verwaltung/aktion`; Migration 0014 mit den Funktionen `geraet_entscheiden` und
  `geraete_verwaltung_liste` (kein Update-Recht auf `devices` für den Kern).
- Hinweise an Admins nennen den Weg über die Karte Geräteverwaltung statt des nicht vorhandenen Skripts `wamo_geraete.py`
  (nur die Anzeige, die Texte der Migration 0002 bleiben unverändert) und führen mit einem Knopf zur Karte.

### Behoben

- Der Sperrbildschirm `LockApp.exe` vorne gilt als gesperrt (bisher meldete die Prüfung des Eingabedesktops „entsperrt“).

### Geändert

- Prompt für Entwicklungsaufträge nach der Messung vom 10.11.2026 (PR 73, `docs/gespraech-optimierung.md`, Abschnitte 8 und 9):
  Der Absatz `DEV_ABNAHME_PROMPT` lässt WAMO den Projektnamen und Einzelheiten selbst ableiten und die Karte sofort anlegen, statt danach zu
  fragen (Aufträge ohne Ziel: Rückfrage statt Karte 7 von 10 vorher, 1 von 10 nachher, p 0,020), und beschreibt bei einem unbestimmten Auftrag
  die eine Rückfrage mit dem Marker ●. Das Beispiel „befehl: pytest -q“ steht nur noch im neuen Absatz `DEV_ABNAHME_BEFEHLE_PROMPT` und nur, wenn der
  Kern Befehlskriterien zulässt (`dev_agent.abnahme_befehle_im_betrieb`, dieselbe Entscheidung wie für die Werkzeugbeschreibung, keine neue
  Umgebungsvariable); im Betrieb mit `WAMO_ABNAHME_BEFEHLE` leer kommt es nicht mehr vor. Die Beschreibung des Pflichtfelds `projekt` sagt, dass der
  Name aus dem Auftrag abzuleiten ist. Ursache der Stille bei „Mach die Seite schöner.“: Das Modell schreibt nur den Wartemarker ○ des
  Satzende-Protokolls; im Betrieb folgt nach 3 Sekunden der Pausenhinweis mit einer Rückfrage, im Messrahmen nicht. Das ist mit der Änderung
  **nicht behoben** (der gemessene Satz bleibt in 5 von 5 Läufen bei ○, zwei neue vage Sätze bekamen in 9 von 10 Läufen eine Rückfrage); bei
  Fehleraufträgen fragt WAMO etwas häufiger (nicht belastbar). Der Messrahmen kennt jetzt Stille, Erfolgskriterien und den Vergleich mit einer
  früheren Messung (`voice-core/eval/abnahme_persona.py`, Ablage `2026-11-10_abnahme_prompt_nachmessung.json`). Wirkt nach neuem Kern-Release,
  ungeprüft gegen echte Sprache.
- `webseite_oeffnen` lädt Seiten und Suchen im aktiven Tab des offenen Browsers (Chrome oder Edge) statt jedes Mal einen
  neuen Tab zu öffnen. Neuer Parameter `neuer_tab` (nur auf ausdrücklichen Wunsch); ohne nutzbares Fenster oder bei einem
  Fehler öffnet sich wie bisher ein neuer Tab, das Ergebnis nennt den Grund. Audit und Duplikatwache kennen `tab` und `weg`.
  Persona und Werkzeugbeschreibung angepasst, abschaltbar mit `WAMO_PERSONA_TAB=aus`.
- Desktop-App: Electron von 44.5.1 auf 44.10.1 angehoben (schließt den Hinweis zu den Zwischenzertifikaten der Web-PKI aus
  `npm audit`); Tests, Installer-Bau und Fuses unverändert grün.
- Dokumentation und Kern-Einrichtung nachgeführt: Die README führt oben im Abschnitt „Stand“ den Stand vom 10.11.2026 (fünf Pakete,
  Betrieb, offene Punkte) und markiert überholte Einträge der Liste „Offen“, `docs/remote-desktop.md` beschreibt das eingerichtete
  Gateway statt „nichts gebaut“ und folgt in Dateinamen und Routen dem Code. Die Schalter `WAMO_PERSONA_ABNAHME`,
  `WAMO_ABNAHME_BEFEHLE` und `WAMO_AUSGABE_SCHWELLE` stehen jetzt in den Listen erlaubter Kern-Variablen
  (`scripts/wamo_kern_sokrates2.py`, `infra/sokrates2/wamo_core_env.py`) und in beiden `.env.example` (leer heißt Standard, Befehlskriterien
  bleiben aus), ein Test in `voice-core/test_env_schalter.py` prüft das. Auf Sokrates-2 wirken die neuen Namen erst, wenn
  `wamo-core-einrichten.sh` den Hilfsdienst `wamo-core-env` erneuert hat; bis dahin lehnt der alte Hilfsdienst eine Übertragung mit
  diesen Namen als Ganzes ab.
- Testlabor, Strang K1 (Kern Sicherheit, Zweig `labor/k1`, Plan `docs/testlabor-wamo.md` 3.1 und 3.6; nicht ausgerollt, nichts davon wirkt ohne die
  Migration 0018 und ohne Laborprofile). Ein Laborprofil (`labor_art` platz oder schaufenster, Rolle mitglied, `aktiv = false` dauerhaft) ist
  nur über `labor_aktiv` freigeschaltet; ältere Kerne lehnen es mit 403 ab. Der Kern setzt die Einschränkung als Erlaubnisliste durch
  (`voice-core/labor.py`): Das Gate prüft stimmige Felder, ein unbekannter Wert in `labor_art` ergibt platz, die `AccessMiddleware` lässt nur
  `/start`, die Offer-Pfade und `/wamo/api/labor/<Name>` durch (exakter Regex je Methode, keine Normalisierung, Rest 403 `labor_pfad_gesperrt`) und
  prüft bei `/start` und jedem Offer Durchlauf und Einwilligung (`labor_durchlauf.py`, Aufruf V1 des Vertrags, ohne Dienst keine Sitzung). Ein
  eigener RTVI-Verarbeiter (`labor_rtvi.py`) verwirft vor Pipecat jede Client-Nachricht außer `client-ready`, `send-text` (höchstens 4000
  Zeichen) und der Freigabe mit job `labor`. `build_session` nimmt für Laborprofile einen eigenen Zweig: Werkzeuge nur `aktuelle_zeit`,
  `websuche` und `canvas_zeigen` (Prüfung im Kontext und am LLM, sonst keine Sitzung), kein PC-Weg, kein Gerätezugang, kein Entwicklungsagent, kein
  Boss, kein Lernen, kein Nachladen, Laborprompt (`labor_prompt.py`) statt Normalprompt, alle 5 Sekunden eine Prüfung des Durchlaufs mit dem
  Ereignis `labor_durchlauf_ende`. `LaborSpeicher` (`labor_speicher.py`) speichert den Gesprächstext nur bei `WAMO_LABOR_TEXT_SPEICHERN=an`, mit
  gültiger Einwilligung (`WAMO_LABOR_EINWILLIGUNG_MIN`) und nur in `sessions`, `turns`, `actions` und `canvas_items`, der Vermerk
  `labor_einwilligung` steht vor jedem Text; Gedächtnis gibt es nie. Der Lader (`labor_daten.py`) liest Regeln, Kontakte, Vorlagen und Wissen aus
  `WAMO_LABOR_DATEN` mit `O_NOFOLLOW`, Besitzer root, ohne Schreibrecht für Dritte, fester Namensliste und Grenzen (6000 Zeichen insgesamt, 2000 für
  den Regelteil), Regeln und Daten getrennt. Beweistests T1 bis T5, T8, T9 und T12 (Kernanteil) in `test_labor_*.py`; offen bleiben Migration,
  Löschfunktion, Zugangsquelle `labor`, Labor-Freigabe und Ticket-Werkzeuge (Stränge S und K2) sowie die Servertests T11 und T13.

### Hinzugefügt

- Testlabor 28.11.2026, Strang S (Datenbank, Zugang, Verwaltung, Oberfläche, Messen; Plan `docs/testlabor-wamo.md`, Zweig `labor/s`,
  nicht angewendet und nicht ausgerollt). Migration `0018_labor_profile.sql` mit `.down.sql`: Spalten `profiles.labor_art`, `labor_platz`
  und `labor_aktiv`. Ein Laborprofil bleibt dauerhaft `aktiv = false` und hat die Rolle `mitglied`, auch das Schau-Profil; freigeschaltet wird
  allein über `labor_aktiv`, das nur ein Kern mit Laborlogik liest. `labor_art` und `labor_platz` sind nach dem Anlegen für jede Rolle
  unveränderlich (Trigger), `wamo_core`, `anon`, `authenticated`, `service_role` und PUBLIC schreiben die Spalten nie (Selbsttest für INSERT
  und UPDATE). Die Funktionen `labor_loeschen` (gesamter Text eines gesperrten Laborprofils oder nur ein Zeitfenster) und `labor_zaehlen`
  sind nur für `wamo_admin` ausführbar; die Löschliste steht als Daten in `voice-core/labor_loeschliste.py`, ein Test prüft sie gegen alle
  Migrationen auf Vollständigkeit (T10), der Rückweg bricht bei jedem Laborprofil ab. Neue Zugangsquelle `labor` in `zugang_laden`
  (Parameter `labor_art`, Server-Schlüssel, Haiku, nie für Entwicklungsaufträge). `scripts/wamo_labor.py` (anlegen, aktivieren, sperren,
  status, loeschen; Trockenlauf als Standard, Schreiben nur als `wamo_admin`, Passwörter nur in eine Umschlag-Datei) und
  `wamo_admin.py labor-zaehlen` und `labor-loeschen`. Die Oberfläche lädt bei `labor_durchlauf_ende` neu und blendet die Quelle `labor` aus.
  Eval-Fälle für die 17 Karten in `voice-core/eval/faelle_labor.py`.

### Hinzugefügt

- Testlabor, Strang K2 (Kern Werkzeuge und Karte; Plan `docs/testlabor-wamo.md`, Zweig `labor/k2`, nicht ausgerollt, wirkt nur für Profile mit
  `labor_art`, die es erst mit Migration 0018 gibt). `docs/labor-dienst-vertrag.md` ist die Übergabedatei für die Kollegen, die den Test-Jira-Dienst
  bauen: Vertrag Version 1 (Aufrufe V1 bis V7 unter `/api/kern/`, Köpfe, Reihenfolge der Prüfungen, Wertebereiche, Fehlercodes), Sicherheitsanforderungen
  D1 bis D13, Abnahmekriterien A1 bis A11 und Termine. Neue Module in `voice-core/`: `labor_vertrag.py` (Festlegungen, Textnormierung, Längenmaß in
  UTF-16-Einheiten), `labor_attrappe.py` (Nachbildung des Dienstes im Prozess, nur für Tests, mit Variante `verwundbar`), `labor_client.py`
  (Dienstclient mit `durchlauf_id` und Idempotenzschlüssel in jedem Schreibaufruf, einmaliger Wiederholung mit demselben Schlüssel und festen Sätzen
  je Fehlercode; lehnt die Adresse der Attrappe ab), `labor_freigaben.py` (`LaborFreigaben`: Job `labor`, Antrag gebunden an Sitzung, Profil, Durchlauf,
  Aktion, Werte und Ticketversion, Frist 120 s, genau einmal, höchstens 3 offene und 25 Anträge je Durchlauf, Karte höchstens 1900 UTF-16-Einheiten
  sonst keine Karte), `labor_ticket.py` und `labor_mail.py` (Prüfungen im Code, Domänenfilter mit `re.fullmatch`), `labor_werkzeuge.py` (die sechs
  Werkzeuge `ticket_suchen`, `ticket_anlegen`, `ticket_aendern`, `ticket_verschieben`, `ticket_kommentieren`, `mail_senden` als `LaborSitzung`).
  `mail_senden` ist eine reine Bestätigung, es gibt keinen Versandweg. In `bot.py` hängen nur vier schmale Stellen (Laborsitzung bauen, Klick
  `freigabe` mit `job: "labor"`, Aufräumen, Ja-Wächter). `PcFreigaben`, `pc_host.py` und `darf_pc_freigeben` bleiben unverändert (Hash- und
  Verhaltenstest `test_labor_pc_unveraendert.py`). Oberfläche: `ui/src/lib/laborKarte.ts` gibt der Freigabekarte für Labor-Aktionen ihre Texte
  („Aktion freigeben“, „Du entscheidest, nicht WAMO.“), die PC-Karte bleibt wie sie war.

### Behoben

- Testlabor, Befunde der Zweitprüfung am Kerncode (Zweig `labor/fix2`, nicht ausgerollt, wirkt nur für Laborprofile). Klickweg: Eine Nachricht
  `freigabe` mit `job: "labor"` und einer erfundenen, aber formal gültigen Kennung endete in einer Meldung an das Sprachmodell (Modellzug, Sprachausgabe,
  Logzeile, Kontextzuwachs) ohne Abstand und Obergrenze. Jetzt wird jede Kennung, die nicht zu einer offenen Karte gehört, nur ohne Inhalt geloggt und nie
  gemeldet, und der `LaborRTVIProcessor` begrenzt die Klicks auf Freigabekarten (Abstand 0,5 Sekunden, höchstens 100; neue Einstellungen
  `WAMO_LABOR_KLICK_ABSTAND_SECS` und `WAMO_LABOR_KLICK_MAX`, ungültige Werte ergeben den Standard, in `ERLAUBT` von `wamo_core_env.py`). Text, Klicks und
  Websuchen zählen je Profil und Durchlauf statt je Verbindung (`labor_limit.LimitRegister`, begrenzter Speicher): eine Neuverbindung setzt sie nicht mehr
  zurück. Die Wache misst einen Ausfall des Dienstes an der Uhr (60 Sekunden ohne gelungene Prüfung) statt an zwölf Fehlversuchen, die bei hängendem Dienst
  bis zu 132 Sekunden dauerten. Beim Ende des Durchlaufs sperrt `LaborSpeicher.stoppen()` die Textspeicherung sofort, auch wenn der Abbruch der Pipeline
  still scheitert. Der SSL-Kontext des Dienstclients wird einmal je CA-Pfad gebaut und geteilt (bisher je Wache-Takt, `/start` und Offer neu, etwa 7
  Millisekunden auf der Ereignisschleife; eine geänderte CA-Datei gilt erst nach einem Neustart). `bot()` beendet bei jeder Ausnahme beim Aufbau, auch bei
  einer Verletzung der Erlaubnisliste, die Verbindung mit einem festen Satz, und der Laborabschnitt schließt dann Dienstclient und Modellzugang. Neu getestet:
  der Klickweg über `LaborRTVIProcessor` und die RTVI-Strecke.

## [0.1.0] - 2026-10-22

Erstes Release: der Stand, auf dem alle Pakete (`ui`, `desktop`, `voice-core`, `host`, `geraete`) stehen.

### Hinzugefügt

- Voice-Core auf Pipecat mit Spracherkennung und Sprachausgabe über Soniox und dem Gespräch mit Claude, Unterbrechungen
  im Livegespräch, Mehrprofil-Anmeldung mit Token und zweitem Faktor.
- Oberfläche im WMC-Design (hell und dunkel) mit Live-Untertiteln, Stimmlinie, Werkzeug-Zeitleiste, Canvas und Freigabe-Karten;
  Betrieb als Desktop-App (Electron) oder im Browser als Container auf Sokrates-2.
- PC-Host als MCP-Werkzeugserver für Windows mit Risikoklassen, Audit-Log und Freigabeweg für kritische Aktionen.
- Mehrere Profile mit den Rollen admin und mitglied, persönliches und gemeinsames Gedächtnis mit Freigabe durch Admins.
- Modellzugang je Profil (eigener Claude-Schlüssel oder nur Hausmodelle), Modellwahl durch den Boss, Claude-Code-Token
  und Anzeige des Kontingents.
- Entwicklungsaufträge in einer Sandbox auf Sokrates-2 mit ECC und REA.
- Geräte und Tunnel je Laptop, Teilen von Inhalten.
- Eigener Supabase-Stack auf Sokrates-2 mit den Migrationen bis 0013 und der Prüfung der Rechte nach jedem Anwenden.
- Einstellungsseite der Oberfläche mit Konto, Modellzugang, Verbrauch, Sprache, Darstellung, Geräten, Teilen und Info;
  der Claude-Code-Token lässt sich dort auch ohne laufendes Gespräch hinterlegen, ein Schlüssel im Gesagten wird entfernt.
- Stimme und Sprachverhalten: Stopp an der hörbaren Wortgrenze, Dämpfen beim Einwurf, Erkennung von Rückmeldelauten,
  Echo-Modus und ein Messweg mit Hörproben (`scripts/wamo_stimme.py`).
- Grundlagen der Selbstüberarbeitung (Konzept in `docs/selbstueberarbeitung.md`): Notanker, Gerüst des Release-Dienstes,
  Migration 0013 für Anfragen und Freigaben mit Vier-Augen-Regel, Versionsskript sowie die Oberfläche in zwei Farben
  (blau und grün) mit Umschalten ohne Lücke und Rückweg. Notanker und Release-Dienst sind noch nicht in Betrieb.

[Unveröffentlicht]: https://github.com/Wamocon/wamo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Wamocon/wamo/releases/tag/v0.1.0
