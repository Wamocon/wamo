# Änderungsprotokoll

Alle nennenswerten Änderungen an WAMO stehen in dieser Datei. Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
die Versionen folgen [Semantic Versioning](https://semver.org/lang/de/) in der Schreibweise X.Y.Z (Tags heißen vX.Y.Z).
Die Abschnitte der Versionen entstehen mit `scripts/wamo_version.py` aus den Commits, der Abschnitt Unveröffentlicht bleibt leer.

## [Unveröffentlicht]

### Hinzugefügt

- Lange Werkzeugausgaben kürzen (Paket 5): Der Kern verkleinert lange Ergebnisse von Werkzeugen (PowerShell-Listen, Testausgaben,
  Git-Ausgaben, JSON von Geräten, Webseitentext) verlustarm, bevor sie in den Kontext des Sprachmodells gelangen, ohne Fremdsoftware
  (`voice-core/ausgaben_kuerzen.py`). Zentrale Stelle ist ein Frame-Prozessor hinter der Aktivitätsanzeige: jedes Werkzeugergebnis
  kommt dort genau einmal an, Zeitleiste und Freigabekarten sehen das Original. Ab 3000 Zeichen (`WAMO_AUSGABE_SCHWELLE`, 0 = aus)
  fasst er gleiche Zeilen mit Zähler zusammen, behält Kopf, Schwanz und Zeilen mit Fehlern, ersetzt die Mitte durch eine Marke und
  entfernt in JSON leere Werte und kürzt lange Listen und Texte; Zahlen, Status und Fehlerfelder bleiben unverändert, das Ergebnis
  trägt `gekuerzt: true` und `ref`. Das Original liegt nur im Arbeitsspeicher der Sitzung (höchstens 20 Einträge und 1 MB), das neue
  Werkzeug `ausgabe_nachladen(ref, ab_zeile, anzahl)` liefert Teile gezielt zurück (nur lesend, höchstens 8000 Zeichen je Aufruf).
  Nie gekürzt werden Freigabe- und Schutzergebnisse, Werkzeuge mit eigener Kappung, Binärdaten und Ergebnisse unter der Schwelle.
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

- `webseite_oeffnen` lädt Seiten und Suchen im aktiven Tab des offenen Browsers (Chrome oder Edge) statt jedes Mal einen
  neuen Tab zu öffnen. Neuer Parameter `neuer_tab` (nur auf ausdrücklichen Wunsch); ohne nutzbares Fenster oder bei einem
  Fehler öffnet sich wie bisher ein neuer Tab, das Ergebnis nennt den Grund. Audit und Duplikatwache kennen `tab` und `weg`.
  Persona und Werkzeugbeschreibung angepasst, abschaltbar mit `WAMO_PERSONA_TAB=aus`.
- Desktop-App: Electron von 44.5.1 auf 44.10.1 angehoben (schließt den Hinweis zu den Zwischenzertifikaten der Web-PKI aus
  `npm audit`); Tests, Installer-Bau und Fuses unverändert grün.

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
