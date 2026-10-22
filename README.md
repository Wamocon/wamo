# WAMO

Sprachassistent der WAMOCON GmbH: Livegespräch mit natürlichen Unterbrechungen, Oberfläche im WMC-Design,
Werkzeuge für den Windows-PC, Entwicklungsaufträge mit ECC und REA, mehrere Nutzerprofile mit persönlichem und
gemeinsamem Gedächtnis.

| Ordner | Was | Läuft auf |
| --- | --- | --- |
| `ui/` | Next.js 16: Präsenzkern, Live-Untertitel, Stimmlinie, Werkzeug-Zeitleiste, Canvas, Freigabe-Karten, Anmeldung mit zweitem Faktor | PC (Desktop-App oder Browser) oder als Container `wamo-ui` auf Sokrates-2 (Browser jedes Büro-Geräts) |
| `voice-core/` | Pipecat 1.12: Soniox STT, Claude Haiku 5.5, Soniox TTS; Mehrprofil-Anmeldung, Gedächtnis, Werkzeugkarte für ECC und REA | PC (Unterwegs) oder Sokrates-2 als Dienst `wamo-core` (Büro) |
| `host/` | MCP-Werkzeugserver für Windows mit Risikoklassen und Audit-Log; lokal über stdio, im Büro über den SSH-Rücktunnel mit Bearer-Token | PC (angemeldete Sitzung) |
| `desktop/` | Electron 44: App-Fenster, roter Stimmen-Rand je Monitor, Tray, Autostart, Betriebsart Büro/Unterwegs | PC |
| `supabase/` | Migrationen mit `__SCHEMA__`-Platzhalter | eigener WAMO-Supabase auf Sokrates-2 |
| `infra/sokrates2/` | Einrichtung von Supabase-Stack, TLS (eigene CA, Caddy), Firewall und Benutzer `wamo` (Claude Code, ECC, REA, gh, Vercel, Supabase-CLI) | Sokrates-2 |
| `scripts/` | Start der Desktop-App, Datenbankzugang, Nutzerverwaltung, Migrationen | PC |

## Stand (20.10.2026)

### Fertig und geprüft

- Eigener Supabase-Stack `supabase-wamo` auf Sokrates-2 (API über HTTPS 55443, Postgres über Pooler 55322 mit TLS),
  getrennt von den übrigen Diensten auf Sokrates-2, Registrierung geschlossen. Schemas `wamo_dev` und `wamo_test` angewendet.
- TLS mit eigener CA (`WAMO interne CA`, auf WAMO-Adressen begrenzt): Caddy `wamo-caddy` vor der API, Tokens mit
  HTTPS-Aussteller, Supavisor mit TLS und `sslmode=verify-full`. Firewall für die WAMO-Ports (`DOCKER-USER`,
  systemd-Unit `wamo-firewall`): nur Loopback, Büro-LAN und für HTTP das Caddy-Netz. Ende-zu-Ende-Test über HTTPS 12 von 12.
- Mehrprofil-Anmeldung: Token (ES256), zweiter Faktor (aal2) und aktives WAMO-Profil. Ende-zu-Ende-Test gegen den echten
  Stack mit 12 von 12 Prüfungen bestanden.
- Gedächtnis je Profil (persönlich) plus gemeinsames Firmengedächtnis mit Freigabe durch Admins.
- Werkzeugkarte: Entwicklungsaufträge wählen je Auftragsart passende ECC-Skills, Agenten und REA-Werkzeuge.
- Oberfläche im WMC-Design (hell und dunkel), Desktop-App umbenannt und gehärtet.
- Tests (20.10.2026): voice-core 563 bestanden (12 übersprungen, inklusive TLS-Handshakes und Eval-Testsatz), host 93
  bestanden (inklusive Echttest der Bildschirmsperre in einer interaktiven Sitzung); UI: 16 Unit-Tests (`npm test`,
  Node lädt TypeScript selbst), Lint, Typen und Build grün; Desktop: 13 Tests (`npm test`, inklusive echtem
  Vollbild-Wächter, der mit seinem Elternprozess endet).
- Sokrates-2: Benutzer `wamo` mit Claude Code, Node 22, ECC-Plugin, REA-Server, gh, Vercel- und Supabase-CLI.

- Gesprächstest Ende zu Ende (Browser ohne Bildschirm, Testfrage als Mikrofon): Anmeldung mit zweitem Faktor,
  Verbindung nach 1,4 s, Begrüßung über Soniox-Stimme, Frage wörtlich erkannt, Claude ruft das Zeitwerkzeug auf und
  antwortet richtig, Verlauf und Live-Untertitel vollständig, Gespräch in `turns` protokolliert.
  ECC-Reviews (Python, Sicherheit, Datenbank, TypeScript) eingearbeitet.
- Claude mit Organisationsschlüssel: Workspace über `ANTHROPIC_WORKSPACE_ID` (Header `anthropic-workspace-id`).
- PC-Steuerung je Sitzung per Schalter, wahlweise Autostart je Gerät (nur Admins, Standard aus).
- Phase P3, Betriebsart Büro: Voice-Core als Dienst `wamo-core` auf Sokrates-2 hinter HTTPS 55444 (Unix-Socket, kein
  TCP-Port), eigene Datenbankrolle `wamo_core_s2`, PC-Werkzeuge über einen SSH-Rücktunnel (Benutzer `wamo-tunnel`,
  Unix-Socket, Bearer-Token im Host). Gesprächstest gegen Sokrates-2 bestanden (Anmeldung, Begrüßung, Uhrzeit-Werkzeug,
  `system_info` über den Tunnel), Tunnel weg: WAMO sagt „PC nicht erreichbar“ und spricht weiter, danach wieder verbunden.
  Erster Ton nach Sprechende (je 20 Fragen): Büro p50 1,32 s, p95 2,00 s; Unterwegs p50 1,16 s, p95 2,62 s.
  Einzelheiten: `infra/sokrates2/README.md`, Abschnitt 10.
- Übernahmen und Freigaben (20.10.2026, Einzelheiten im Abschnitt „Sitzungen, Anmeldung und Freigaben“):
  weiches Ausblenden bei Unterbrechung wirkt jetzt (vorher endeten Unterbrechungen hart), gesperrter PC wird erkannt
  und klar gemeldet, eine Sitzung je Profil, Stimmen-Rand blendet sich bei Vollbild aus, Freigabeweg für kritische
  PC-Aktionen per Karte in der Oberfläche, Höchstalter der Anmeldung und „Angemeldet bleiben“.

- Modellzugang je Profil (21.10.2026, Abschnitt „Modellzugang je Profil“): eigener Claude-Schlüssel (verschlüsselt in
  der Datenbank, Standard Haiku) oder nur Hausmodelle über LiteLLM und SearXNG ohne Rückfall auf Claude; Migration 0010
  in `wamo_dev` und `wamo_test` angewendet.

- WAMO Boss und Modellkarte (22.10.2026, Abschnitt „Boss und Modellkarte“): Der Boss wählt das Modell nach Aufgabenklasse,
  gemessener Güte, Antwortzeit, Erreichbarkeit und Modus des Profils (Automatisch oder Nur Hausmodelle); das Gespräch
  startet ohne Zusatzlatenz mit dem besten schnellen Modell, Fachaufgaben gehen asynchron an Spezialisten
  (`aufgabe_verteilen`, Karte unter Vorgänge). Zweiter Schlüsseltyp: Claude-Code-Token für Entwicklungsaufträge.
  Modellwahl und Token sind auch ohne Gespräch einstellbar (`/wamo/api/modellzugang`). Migration 0011 in `wamo_dev` und
  `wamo_test` angewendet.

- Browser-Betrieb (21.10.2026, Variante A): Die Oberfläche läuft als Container `wamo-ui` auf Sokrates-2 hinter
  `wamo-caddy` unter `https://192.168.178.136:55445` (Zertifikat der WAMO-CA, nur Büro-LAN und Loopback). Jedes
  Gerät im Büro öffnet WAMO im Browser, nach dem einmaligen Import der WAMO-CA; der Browser dort kennt nur die
  Betriebsart Büro, PC-Steuerung gibt es nur in der Desktop-App. Test mit Chromium: Seite in 0,23 s geladen, Anmeldung mit
  zweitem Faktor, Verbindung nach 0,9 s, Begrüßung nach 4,0 s, Frage erkannt und beantwortet, keine CSP-Verstöße.
  Einzelheiten und die Umstellung auf Variante B (eigene Domain, öffentliches Zertifikat):
  `infra/sokrates2/README.md`, Abschnitt 12.

### Offen

- Modellzugang ausrollen: `WAMO_SCHLUESSEL_KEK` erzeugen und in `core.env` setzen, Schlüssel der Person zuordnen
  oder hinterlegen, Migration 0010 in `wamo_prod`, Hausmodelle (`WAMO_LLM_MODELL`, `WAMO_LLM_VISION_MODELL`) im Kern
  eintragen. Solange der Kern auf Sokrates-2 den alten Stand hat, nutzt jedes Profil dort noch den Schlüssel des Kerns.

- Boss ausrollen: Migration 0011 in `wamo_prod` (vorher `database-reviewer`), Kern auf Sokrates-2 neu verteilen und neu
  starten, danach `WAMO_LLM_MODELL=gemma4-31b` aus `core.env` entfernen (der Boss entscheidet, die Variable bleibt nur
  als Übersteuerung). Der Dispatcher muss den Token über die zweite stdin-Zeile annehmen (siehe „Boss und Modellkarte“).
  Hausmodelle im Gespräch kaltstartfrei halten (Aufwärmanfrage oder `keep_alive`), sonst kostet der erste Satz nach einer
  Pause rund 9 Sekunden.

- Ausrollen der Übernahmen und Freigaben im Büro (Kern, PC-Tunnel, GoTrue-Sitzungsgrenzen): Die Skripte sind
  vorbereitet, aber noch nicht angewendet. Bis dahin gibt es im Büro keinen Freigabeweg (fehlender Freigabe-Schlüssel:
  nichts wird freigegeben), unterwegs wirkt alles nach dem nächsten Start der Desktop-App.

- Soniox: Der Schlüssel gehört zu einem US-Projekt, die Spracherkennung läuft deshalb vorerst über den US-Endpunkt.
  Für EU-Datenhaltung EU-Schlüssel anlegen und `SONIOX_STT_URL` und `SONIOX_TTS_URL` auf `*.eu.soniox.com` stellen.
- Weitere PCs: `infra/sokrates2/wamo-ca.crt` importieren (ein `certutil`-Befehl, siehe `infra/sokrates2/README.md`,
  Abschnitt 5.2 und die Anleitung für den Browser in Abschnitt 12.3). Seit 19.10.2026 gilt Stufe 2: HTTP 55321 ist im LAN geschlossen, nur noch HTTPS 55443.
- Zugangsschlüssel auf Sokrates-2 enger fassen (Repo-gebundene GitHub-Token, Lese-Token, Jira-Konto nur lesend),
  bevor Entwicklungsaufträge eingeschaltet werden.
- P3: Den PC an das eigene Admin-Profil binden, sobald das Konto angelegt ist
  (`python scripts\wamo_kern_sokrates2.py profil --email … --neustart`); bis dahin hat im Büro niemand PC-Werkzeuge.
  Büro bleibt bis zur Abnahme durch Niko Wahlmöglichkeit, Standard ist Unterwegs.
- Ausbaustufen: P4 eigene Sprachmodelle über LiteLLM, P5 Spracherkennung und Sprachausgabe auf den Sparks.
- P4: Umschaltung, SearXNG und Testsatz sind fertig, die Claude-Referenz ist gemessen (Haiku 5.5 besteht die
  Freigabe selbst nicht: Marker 92 %, erstes Token p95 1,33 s). Die Abnahme der Modelle im Haus steht aus, solange
  `LITELLM_API_KEY` in der `.env` fehlt (siehe Abschnitt „Modelle“). Das Gateway spricht nur HTTP im Büro-LAN.

## Einrichtung auf einem Windows-PC

Voraussetzungen: Git, Node.js 22, `uv` (`winget install astral-sh.uv`), SSH-Zugang zu Sokrates-2 (`~/.ssh/config`, Alias `sokrates2`).

```powershell
cd D:\IDEA\Projekt\wamo
copy .env.example .env                    # SONIOX_API_KEY und ANTHROPIC_API_KEY eintragen
python scripts\wamo_db_rolle.py           # Datenbankzugang wamo_core (TLS), schreibt .env und ui\.env.local
certutil -user -addstore Root infra\sokrates2\wamo-ca.crt   # einmal je PC: WAMO-CA für Browser und Desktop-App
python scripts\wamo_db_rolle.py --rolle wamo_admin   # nur auf Admin-PCs: Zugang für wamo_admin.py
python scripts\wamo_nutzer.py anlegen --email vorname.nachname@wamocon.com --name Vorname --rolle admin
cd voice-core; uv sync; cd ..\host; uv sync; cd ..\ui; npm install; cd ..\desktop; npm install; cd ..
scripts\wamo-desktop.cmd                  # startet Voice-Core, Oberfläche und Desktop-App
```

Beim ersten Anmelden richtet die Oberfläche die Authenticator-App ein (QR-Code). Ohne zweiten Faktor kein Zugang.

### Betriebsart Büro und Unterwegs

| Betriebsart | Kern | PC-Werkzeuge |
| --- | --- | --- |
| Unterwegs (Standard) | auf diesem PC, `http://localhost:7860` (`NEXT_PUBLIC_VOICE_CORE_URL`), die Desktop-App startet ihn | Host per stdio |
| Büro | Sokrates-2, `https://192.168.178.136:55444` (`NEXT_PUBLIC_WAMO_KERN_BUERO` in `ui\.env.local`, nur https) | Host per SSH-Rücktunnel, nur für das an diesen PC gebundene Profil |

Umschalten in der Oberfläche (Einstellungen, Abschnitt „Geräte und PC“, nur ohne laufendes Gespräch) oder im Tray-Menü der Desktop-App;
die Einstellung gilt dauerhaft je Gerät (Desktop: `%APPDATA%\wamo-desktop\einstellungen.json`, Browser: localStorage).
Die Statusleiste zeigt Betriebsart und Kern, der PC-Host den Zielrechner. Einrichtung des Tunnels auf diesem PC (ohne
Adminrechte, Rückweg `scripts\wamo-tunnel-entfernen.ps1`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wamo-tunnel-einrichten.ps1   # Token, Freigabe-Schlüssel, SSH-Schlüssel, known_hosts, Aufgabe
python scripts\wamo_kern_sokrates2.py verteilen                                         # Schlüssel und Skripte nach Sokrates-2
# Server einrichten: infra/sokrates2/README.md, Abschnitt 10
python scripts\wamo_kern_sokrates2.py alles --email vorname.nachname@wamocon.com         # Werte nur über stdin
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wamo-tunnel-einrichten.ps1 -Starten
```

#### Browser auf Sokrates-2

Die Oberfläche gibt es zusätzlich als Dienst auf Sokrates-2 (`https://192.168.178.136:55445`). Gebaut wird sie mit
`NEXT_PUBLIC_WAMO_NUR_BUERO=ja` (Build-Argument, `infra/sokrates2/wamo-ui/Dockerfile`); ein solcher Build kennt keinen
lokalen Kern, nennt ihn auch nicht in der CSP und bietet im Browser nur die Betriebsart Büro an. Die Umschaltung
Büro/Unterwegs entfällt dort, bei der PC-Steuerung steht „nur in der Desktop-App verfügbar“. Erkannt wird die Desktop-App an
`window.wamoDesktop` (`ui/src/lib/geraet.ts`), nie an der Adresse. Die Anmeldung gilt im Browser 24 Stunden
(`NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER`). Adresse und Zertifikat stehen nur in `/home/wamocon/wamo-ui/wamo-ui.conf`
auf dem Server, nicht im Code.

```powershell
python scripts\wamo_ui_sokrates2.py verteilen        # Quellen der Oberfläche und Skripte nach ~/wamo-infra (ohne .env, node_modules, .next)
python scripts\wamo_ui_sokrates2.py einrichten       # Firewall, Caddy, Image bauen, Container, erlaubte Herkunft des Kerns, Prüfung
python scripts\wamo_ui_sokrates2.py status           # Zustand; bauen = nur Image und Container, pruefen = Header, TLS, Härtung
```

#### Einstellungen in der Oberfläche

Alle Formulare und Schalter liegen auf der Einstellungsseite (Zahnrad in der Kopfzeile oder „Einstellungen öffnen“ in der
linken Seitenleiste, `ui/src/components/einstellungen/`). Sie ist keine eigene Route, sondern eine Ansicht der Hauptseite über
den Hash `#einstellungen/<abschnitt>`: Das passt zur Desktop-App, Links und „Zurück“ des Browsers gehen, und ein laufendes
Gespräch bleibt unberührt (die Kommandozentrale wird nur verborgen, Meldungen stehen oben in der Seite). Ab 860 px zwei
Spalten (Gliederung links), darunter eine Liste, die in den Abschnitt führt. Escape schließt wie „Zurück“, lässt aber
Eingaben in Textfeldern in Ruhe.

| Abschnitt (Kennung) | Inhalt | Sichtbar für |
| --- | --- | --- |
| Konto und Profil (`konto`) | E-Mail, aktives Profil, Rolle, Abmelden | alle |
| Modellzugang (`modellzugang`) | API-Schlüssel (Gespräch), Claude-Code-Token (Entwicklungsaufträge), Modus, geführte Anleitung | alle |
| Verbrauch (`verbrauch`) | Kontingent des Max-Abos aus der Servernachricht `kontingent`: Sitzungslimit, Wochenlimit, Fable limit | Admin |
| Sprache und Ton (`sprache`) | Mikrofonwahl, Spracherkennung und Sprachausgabe laut Betriebsmeldung | alle |
| Darstellung (`darstellung`) | Normal, Fokus, Zentrale, Nacht | alle |
| Geräte und PC (`geraete`) | Verbindungen, PC-Steuerung samt Autostart (nur Admin, nur Desktop-App), Betriebsart, Stand der Geräteeinrichtung | alle, PC-Steuerung nur Admin |
| Teilen (`teilen`) | Geteilte Arbeit: mit mir, von mir, Dateien | alle |
| Info (`info`) | Version, Kern, Hinweise an Admins | alle, Hinweise nur Admin |

Der Abschnitt Verbrauch ist **Format ungeprüft**: Das Datenformat des Kerns (`voice-core/kontingent.py`) ist noch an keinem
echten Token bestätigt, die Seite sagt das dort auch (`ui/src/lib/verbrauch.ts`). In der Entwicklung zeigt
`/?vorschau=1&rolle=admin&kontingent=beispiel#einstellungen/verbrauch` Beispieldaten (nur `next dev` mit
`NEXT_PUBLIC_WAMO_VORSCHAU=1`).

Die geplante Aufgabe „WAMO Tunnel“ startet bei jeder Anmeldung `scripts\wamo-tunnel.ps1`: Host und Tunnel laufen damit
unabhängig von der Desktop-App (auch für den Browser). Die PC-Steuerung bleibt trotzdem je Sitzung aus, bis ein Admin
sie einschaltet. Logs: `%LOCALAPPDATA%\WAMO\logs`.

### Sitzungen, Anmeldung und Freigaben

- **Eine Sitzung je Profil** (`voice-core/sitzungen.py`, `WAMO_SITZUNG_EINZELN`, Standard an): Eine neue Sitzung
  desselben Profils löst die laufende ab. Die alte verliert sofort die PC-Sperre (danach lehnt sie PC-Werkzeuge,
  Freigaben und erneutes Einschalten ab), ihre Oberfläche zeigt „Sitzung beendet: Eine neue Sitzung dieses Profils hat
  übernommen.“, dann wird sie getrennt. Andere Profile bleiben unberührt, der Büro-Kern bedient mehrere gleichzeitig.
- **Gesperrter PC**: Der Host prüft vor Fensterwechsel, Tippen, Tasten, Scrollen, Klicken, Bildschirmfoto und dem
  Öffnen von Programmen, Webseiten und Ordnern, ob der Bildschirm gesperrt ist (`OpenInputDesktop`, Desktop „Default“),
  und meldet dann `status: "locked"` mit Audit-Eintrag. Auch Lesen (`powershell_lesen`, `fenster_auflisten`) ruht
  dann, denn am gesperrten PC sieht niemand, wer ins Mikrofon spricht; nur `system_info` meldet weiter den Stand.
  Die Statusleiste zeigt „Bildschirm gesperrt“, WAMO sagt, dass nur die Person selbst entsperren kann. Quelle sind die
  Werkzeugergebnisse und die Tunnel-Probe (`sperre_pruefen`, nur für den Kern); unterwegs fragt der Kern bei
  gesperrtem Bildschirm alle 10 s nach.
- **Freigaben kritischer PC-Aktionen**: Verändernde PowerShell-Befehle, Senden in Mail- und Messenger-Fenstern (Enter,
  Strg+Enter, Alt+S, Leertaste, Zeilenumbruch beim Tippen), Klicks in diesen Fenstern und sehr lange Suchanfragen führt
  der Host nicht aus, sondern legt eine Anfrage an (Befehle und Webseiten 5 Minuten gültig, Senden, Tasten und Klicks
  2 Minuten; höchstens 5 offen, gleiche Anfragen nur einmal). Die Oberfläche zeigt sie als Karte mit Zielanwendung und
  genauem Text (⏎ markiert jedes Enter), den Anlass des Modells getrennt und nur als Hinweis; was die Karte nicht ganz
  zeigen kann, wird gar nicht erst angefragt. Solange eine Anfrage offen ist oder eine freigegebene Aktion läuft,
  ruhen am PC alle Eingaben (`status: "wartet"`), und die eigene Oberfläche bedient WAMO nie: Das Modell kann die Karte
  weder anklicken noch per Tastatur bestätigen. Ausgeführt wird nur ins selbe Fenster mit demselben Titel (Chat,
  Betreff), sonst gar nicht. Freigeben darf nur ein Admin mit
  eingeschalteter PC-Steuerung, dessen Sitzung die PC-Sperre hält; der Klick geht über die angemeldete Verbindung an
  den Kern, der Kern ruft im Host `freigabe_entscheiden` mit einem zweiten Geheimnis neben dem Bearer-Token auf.
  Das Werkzeug sieht das Sprachmodell nie (auch nicht über den Tunnel), die Aktion läuft genau einmal, jeder Schritt
  steht in der Tabelle `actions` (pending, approved oder rejected, running, done oder failed). Das Ergebnis bekommt
  WAMO als Daten. Lokal erzeugt der Kern das Geheimnis je Host-Start, im Büro liegt es in
  `%LOCALAPPDATA%\WAMO\host-freigabe` und als `WAMO_PC_HOST_FREIGABE` in `/etc/wamo/core.env`.
- **Höchstalter der Anmeldung** (gemessen ab Passwort und Code, Claim `amr`): Der Kern lehnt Tokens ohne `amr` ab und
  erzwingt `WAMO_ANMELDUNG_MAX_STUNDEN` (Standard 168), auch für gecachte Tokens; eine laufende Sitzung endet beim
  Überschreiten, die Oberfläche meldet ab. Die Anmeldemaske hat „Angemeldet bleiben“ (in der Desktop-App
  vorausgewählt; ohne Haken liegt die Sitzung nur im sessionStorage) und prüft das Alter beim Laden und jede Minute:
  Desktop 7 Tage, Browser `NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER` (Standard 24 Stunden). Unterschieden wird über
  `window.wamoDesktop`, nicht über die Adresse. GoTrue begrenzt Sitzungen zusätzlich (Zeitbox 168 Stunden,
  Inaktivität 72 Stunden; `infra/sokrates2/supabase-wamo-einrichten.sh`, Schritt 3b).
- **Stimmen-Rand bei Vollbild** (`desktop/vollbild.js`): Ein PowerShell-Wächter fragt alle 1,5 s
  `SHQueryUserNotificationState` ab; bei Vollbild-Programm, Direct3D-Vollbild und Präsentation (Werte 2, 3, 4) blendet
  sich der Rand aus, erst nach zwei gleichen Werten. Wert 7 (Store-App) zählt nicht, weil Store-Apps unter Windows 10
  und 11 auch im Fenster laufen. Der Wächter endet mit der App und startet nach einem Absturz nach 5 s neu.
- **Weicher Stopp**: Nach einer Unterbrechung blendet die Oberfläche WAMOs Stimme in der nächsten Wortpause aus,
  spätestens nach 600 ms (`ui/src/lib/weicherStopp.ts`, im 20-Hz-Takt mit dem selbst gemessenen Pegel).
- **Mikrofon-Wächter** (`ui/src/lib/mikrofon.ts`, `voice-core/audio_monitor.py`): Die Oberfläche misst die eigene Spur und
  warnt nach 3 s digitaler Stille, bei einer beendeten Spur ohne Ersatz, ohne Spur 5 s nach „Bot bereit“ und bei
  Mikrofonsperre durch Windows (Datenschutz für Desktop-Apps, `systemPreferences.getMediaAccessStatus`), jeweils mit
  Gerätename, Abhilfe und Gerätewahl. Der Kern meldet per Servernachricht `mikrofon`: `still` nach 10 s nur digitaler
  Stille, `fehlt` nach 5 s ganz ohne Audiopakete (die Sendespur ist abgerissen), `ok` bei Rückkehr. Wechselt das
  Standardgerät (Dock, Headset), legt die Oberfläche die neue Spur auf die Verbindung (`sendeSpurErsetzen`); der
  SmallWebRTC-Transport tut das mit dem WavMediaManager nicht, die alte, beendete Spur blieb am Sender und es kam
  gar nichts mehr an. Protokoll der Desktop-App: `desktop/logs/oberflaeche.log` (Zeilen `[WAMO] Mikrofon …`, beim
  Start der Windows-Mikrofonzugriff), `desktop/logs/berechtigungen.log` (abgelehnte Mikrofon-Anfragen; mit
  `WAMO_BERECHTIGUNG_LOG=1` alle). Berechtigungen: `desktop/berechtigungen.js`, geprüft gegen ein echtes Electron
  (`desktop/berechtigungen.electron.test.js`).
- **Prüfung in 30 Sekunden**: App starten, „Gespräch starten“, einen Satz sagen. Dann zeigt
  `desktop\logs\oberflaeche.log` zuletzt `[WAMO] Mikrofon liefert Ton: <Gerät>, Spitze -xx dBFS` (über -100), die
  Stimmlinie bewegt sich, und im Kern steht `Mikrofon: Pegel Median … dBFS` deutlich über -100. Die Windows-Liste
  der letzten Mikrofonnutzer (`HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged`)
  ist nur ein Zusatz: In Testläufen aus der Entwicklungsumgebung trugen sich selbst Chrome, ffmpeg und Electron bei
  echter Aufnahme nicht ein, ein fehlender Eintrag beweist also nicht, dass das Mikrofon nie geöffnet wurde.
- **Keine doppelten Antworten** (`voice-core/turn_wache.py`): Pipecats Assistent-Aggregator merkt sich ein
  Werkzeugergebnis, das während WAMOs Sprechen eintrifft, und schiebt den Verlauf beim nächsten „Bot stopped speaking“
  noch einmal an, auch wenn der neue Zug der Person schon beantwortet wird. Die Turn-Wache vor und hinter dem Modell
  verwirft solche Anfragen (gleicher Verlauf wie zuletzt, oder der Verlauf endet mit WAMOs Antwort).

## Nutzer und Gedächtnis

| Befehl | Zweck |
| --- | --- |
| `python scripts\wamo_nutzer.py anlegen --email … --name … --rolle admin\|mitglied` | Konto (falls neu, Passwort wird verdeckt abgefragt) und Profil |
| `python scripts\wamo_nutzer.py sperren --email …` | Profil sperren |
| `python scripts\wamo_nutzer.py liste` | Profile anzeigen |
| `uv run python wamo_admin.py vorschlaege` (in `voice-core/`) | offene Vorschläge für das gemeinsame Gedächtnis |
| `uv run python wamo_admin.py freigeben <id> --admin <uuid>` | Vorschlag freigeben (`ablehnen` entsprechend) |

- **Persönlich:** Eigenschaften, Vorlieben und Verhalten eines Profils, nur für dieses Profil sichtbar.
- **Gemeinsam:** Wissen über Firma, Kunden, Projekte und Abläufe. Wirkt erst nach Freigabe durch einen Admin;
  ausdrücklich gemerkte Einträge eines Admins gelten sofort.
- PC-Werkzeuge und Entwicklungsaufträge stehen vorerst nur Profilen mit Rolle `admin` zur Verfügung.

## Entwicklungsaufträge mit ECC und REA

WAMO startet Aufträge als Claude-Code-Lauf. Die Auftragsart (`planung`, `umsetzung`, `review`, `test`, `sicherheit`,
`reverse_engineering` und weitere, siehe `voice-core/werkzeugkarte.py`) bestimmt, welche ECC-Skills und Agenten und
welche REA-Werkzeuge der Lauf zuerst nutzt. ECC-Skills und -Agenten stehen in der Sandbox zur Verfügung, ECC-Hooks sind
dort bewusst aus: Mehrere davon führen Programme aus dem Projektordner aus (`node_modules/.bin`), das wäre ein Weg für
gepflanzten Code. Die Qualitätsprüfung ruft der Auftrag stattdessen ausdrücklich zum Abschluss auf (`verification-loop`,
`/quality-gate`, siehe `ABSCHLUSS_SKILLS` in der Werkzeugkarte), und der Wächter prüft jeden Schritt. Freigabepflichtig sind unter anderem `git push`,
Merges, Produktions-Deployments, Schreibzugriffe auf Produktionsdatenbanken und verändernde REA-Werkzeuge
(Frida, ADB, Debugger). Der Wächter (`voice-core/dev_guard.py`) entscheidet vor jedem Schritt; eine Freigabe gilt
genau einmal und wird erst verbraucht, wenn der Schritt wirklich lief.

### Sandbox auf Sokrates-2 (Standard)

Mit `WAMO_ENTWICKLUNG=an` laufen Aufträge standardmäßig auf Sokrates-2 (`WAMO_ENTWICKLUNG_ORT=sokrates2`), nicht
als Windows-Benutzer. `WAMO_ENTWICKLUNG_ORT=lokal` geht nur zusammen mit `WAMO_ENTWICKLUNG_LOKAL_BESTAETIGT=ja` und
schreibt eine Warnung ins Log.

| Baustein | Aufgabe |
| --- | --- |
| `infra/sokrates2/wamo_job_dispatch.py` | Forced Command des SSH-Schlüssels `wamo-jobs`: nur `ping`, `start`, `status`, `pending`, `approve`, `cancel`, streng geprüft; Selbsttest mit Kanarienvögeln vor jedem Lauf |
| `infra/sokrates2/wamo-job.sh` | bubblewrap-Sandbox als `wamo`: nur der Projektordner `/srv/wamo/projekte/<projekt>` ist dauerhaft beschreibbar, `/etc` nur aus einer Allowlist, eigener PID-Namensraum, keine Geräte, Zeit- und Ressourcengrenzen über `systemd-run --user` |
| `infra/sokrates2/abschottung.py` | Landlock (keine TCP-Verbindungen zu Diensten des Hosts, keine abstrakten Unix-Sockets außerhalb) und seccomp (u. a. MPTCP, SCTP, io_uring, TCP Fast Open) |
| `infra/sokrates2/wamo-egress.nft` | Ausgangsfilter für uid `wamo`: nur Internet über TCP 80 und 443, Loopback nur 20000 bis 20999, kein IPv6, kein LAN |
| `infra/sokrates2/claude-code/managed-settings.json` | Wächter-Hooks (vorher und nachher), nur verwaltete Hooks (allein der Wächter; ECC ohne Hooks als lokale Kopie, `infra/sokrates2/wamo-job-ecc.py`), Sperrliste |
| `infra/sokrates2/wamo-job-einrichten.sh` | Installation nach `/opt/wamo` (root-eigen), AppArmor-Profil nur für eine bwrap-Kopie, sshd-Drop-in, Schlüssel mit `from=` |
| `infra/sokrates2/wamo_job_rauchtest.py` | Rauchtest: `sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp --attrappe` |

Der Voice-Core schickt den Auftrag per SSH (`~/.ssh/id_ed25519_wamo_jobs`, Hostschlüssel aus
`~/.ssh/known_hosts_wamo_jobs`, Ziel `WAMO_ENTWICKLUNG_SSH_ZIEL`, Standard `wamo@192.168.178.136`) und liest die
stream-json-Ausgabe wie lokal. stdin bleibt offen: Auflegen beendet ssh und damit den entfernten Auftrag. In die Sandbox
gelangen nur die Token der Auftragsart aus `/etc/wamo/geheim.env` (`ZUGANG_VARIABLEN` in `voice-core/werkzeugkarte.py`;
lesende Arten nur `GH_TOKEN_LESEN`), als Umgebungsvariablen, nie über die Befehlszeile. Bekäme ein Lauf mehr Token als
frühere Läufe im selben Projekt, braucht er eine Freigabe. Grenze: Die Token der Auftragsart sind in der Sandbox lesbar
und über HTTPS nutzbar. Token deshalb eng zuschneiden (Repos, Rechte, Ablaufdatum). Für GitHub gibt es dazu den
App-Modus (`WAMO_GH_APP_ID`, `WAMO_GH_APP_INSTALLATION_ID`, `WAMO_GH_APP_SCHLUESSEL` in `/etc/wamo/geheim.env`): Der
Dispatcher erzeugt je Lauf außerhalb der Sandbox ein kurzlebiges Installations-Token nur mit den Rechten der Auftragsart
(und, wenn der Auftrag ein Repo nennt, nur für dieses) und widerruft es am Laufende; ohne diese Variablen gelten
`GH_TOKEN` und `GH_TOKEN_LESEN` wie bisher. Einzelheiten und Anleitung: `infra/sokrates2/README.md`, Abschnitte 2 und 9.

## Modellzugang je Profil

Jedes Profil hat genau eine von zwei Arten (`voice-core/modellzugang.py`, Tabelle `model_access`, Migration 0010):

| Art | Wann | Gespräch, Lernen, Zusammenfassung, Bildschirm | Websuche |
| --- | --- | --- | --- |
| Eigener Claude-Schlüssel | Die Person hat ihren Schlüssel hinterlegt (oder ist das Profil in `WAMO_CLAUDE_SCHLUESSEL_PROFIL`) | Claude mit dem Schlüssel dieses Profils, Standardmodell Haiku (ein Admin kann ein anderes festlegen); fällt Claude aus, übernehmen die Hausmodelle mit Hinweis | Anthropic-Websuche |
| Nur Hausmodelle | Kein eigener Schlüssel, Fehler beim Laden des Zugangs oder vom Admin festgelegt | LiteLLM (`WAMO_LLM_MODELL`, `WAMO_LLM_VISION_MODELL`), nie Anthropic, auch nicht als Rückfall | SearXNG |

- **Hinterlegen** in der Oberfläche (Einstellungen, Abschnitt „Modellzugang“, maskiertes Feld): Der Kern prüft den Schlüssel mit einer
  kleinen Testanfrage (höchstens 5 Versuche je 10 Minuten), verschlüsselt ihn mit AES-256-GCM (Profil-ID als
  zusätzliche Daten, Hauptschlüssel `WAMO_SCHLUESSEL_KEK` nur in `core.env` bzw. `.env` des Kerns) und speichert nur das
  Chiffrat. Zurück zur Oberfläche gehen nur „hinterlegt“ und die letzten vier Zeichen. Ersetzen und Löschen gehen
  ebenso. Eine Änderung gilt ab der nächsten Sitzung. Schlüssel stehen nie in Logs (ein Filter schwärzt `sk-ant-…`),
  `actions` oder `turns`.
- **Admin:** `uv run python wamo_admin.py modellzugang` zeigt die Zugänge, `modellzugang-setzen --user-id <uuid>
  --nur-haus an` legt ein Profil auf Hausmodelle fest (der Trigger löscht dabei den Schlüssel; die Verwaltung kann
  nie einen setzen oder lesen).
- **Der Schlüssel in `ANTHROPIC_API_KEY`** gehört einer Person und gilt nur für das Profil in
  `WAMO_CLAUDE_SCHLUESSEL_PROFIL`, nie für andere. Besser ist, ihn mit `uv run python modellzugang.py hinterlegen
  --user-id <uuid>` zu hinterlegen und die Variable zu entfernen.
- **Je Sitzung ein eigener Client:** Es gibt keinen Anthropic-Client und keinen Schlüssel, der Profile teilt. Der
  Verbrauch steht als Tokenzahlen je Profil im Log (`Verbrauch profil=… anbieter=… modell=… ein=… aus=…`), ohne Inhalt.
- **Stimme:** Jedes Profil trägt `stt` und `tts` (`soniox` oder `haus`) als Schnittstelle für Phase P5; ein Admin setzt
  sie mit `modellzugang-setzen --stt haus --tts haus`.
- **Modus je Profil** (Migration 0011, Spalte `modell_modus`): „Automatisch“ oder „Nur Hausmodelle“, gewählt in der Karte
  Modellzugang und gültig ab der nächsten Sitzung. Der Zwang eines Admins (`nur_haus`) bleibt stärker. Im Modus Nur
  Hausmodelle geht nichts an Anthropic, auch nicht als Rückfall, und Claude-Code-Aufträge sind aus.
- **Zweiter Schlüsseltyp, Claude-Code-Token** (`sk-ant-oat…` aus `claude setup-token`, Max-Tarif): nur für
  Entwicklungsaufträge, nie für das Gespräch. Der Kern prüft nur das Format (eine Testanfrage würde das Kontingent
  verbrauchen), verschlüsselt ihn wie den API-Schlüssel (AES-256-GCM, Zweck als zusätzliche Daten, zwei getrennte Felder
  in der Oberfläche) und gibt ihn nie zurück. `modellzugang.claude_code_zugang_fuer(user_id)` liefert dem Dispatcher
  `{"typ": "oauth" | "api", "wert": …}` für genau diese Sitzung und diesen Lauf: zuerst der Token (Modell Sonnet), dann
  der API-Schlüssel (Modell Haiku), sonst `None`. Die Oberfläche erkennt die Art am Anfang des Werts und bietet bei einem
  falschen Feld an, ihn ins richtige zu verschieben.
- **Ohne Gespräch:** `GET` und `POST /wamo/api/modellzugang` (Anmelde-Token, zweiter Faktor, aktives Profil, Profil nur aus
  der Identität, Body höchstens 8 KB) tun dasselbe wie der Datenkanal; die Karte nutzt sie, wenn keine Sitzung läuft.
  Fehler kommen als konkreter Satz (401 Schlüssel abgelehnt, Format, Zeitüberschreitung, Kern nicht erreichbar).
- **Schlüssel nie in Texten:** Eine Schutzstufe im Kern (`voice-core/schluessel_filter.py`) ersetzt `sk-ant-…` (auch
  `sk-ant-oat…`) schon hinter der Spracherkennung durch „[Schlüssel entfernt]“, bevor Verlauf, Modell, Lernen,
  Gedächtnis und Log etwas sehen. WAMO nimmt Schlüssel nie per Stimme an und fragt nie danach; er erklärt nur, wo die Karte ist.
- **Entwicklungsaufträge** laufen mit dem Token (Sonnet), sonst mit dem API-Schlüssel des Profils (Haiku, Allowlist in
  `werkzeugkarte.py`); das Profil des Kern-Schlüssels nutzt übergangsweise weiter den Zugang der Sandbox
  (`/etc/wamo/geheim.env`). Alle anderen Profile haben keine Entwicklungsaufträge, bis eine Hausvariante mit `wamo-coder`
  gebaut und gemessen ist. Der Dispatcher bekommt den Wert nie auf der Befehlszeile, sondern nur über eine zweite
  stdin-Zeile (`{"typ","wert"}`, Kopfzeile `zugang_folgt`).

## Modelle (Ausbaustufe P4)

Jeder Baustein lässt sich zwischen Claude und den eigenen Modellen auf den Sparks umschalten
(`voice-core/llm_anbieter.py`). Eigene Modelle laufen nur über das LiteLLM-Gateway (`LITELLM_BASE_URL`,
`LITELLM_API_KEY`), nie direkt über die Adressen der Knoten. Standard ist für Profile mit eigenem Claude-Schlüssel
Claude (Haiku); Profile ohne Schlüssel nutzen immer die Hausmodelle, von den folgenden Einstellungen gelten für sie
nur die Modellnamen.

| Baustein | Anbieter | Modell | Standard |
| --- | --- | --- | --- |
| Gespräch | `WAMO_LLM_ANBIETER` | `WAMO_LLM_MODELL` | Claude, `CLAUDE_MODEL_FAST` (Haiku 5.5) |
| Lernen | `WAMO_LLM_LERNEN` | `WAMO_LLM_LERNEN_MODELL` (leer bei litellm: Modell des Gesprächs) | Claude, `CLAUDE_MODEL_FAST` |
| Bildschirm | `WAMO_LLM_VISION` | `WAMO_LLM_VISION_MODELL` | Claude, `VISION_MODEL` (Sonnet 5.5) |
| Zusammenfassung | `WAMO_LLM_ZUSAMMENFASSUNG` | `WAMO_LLM_ZUSAMMENFASSUNG_MODELL` | leer: wie das Gespräch |
| Websuche | `WAMO_SUCHE` (`anthropic` oder `searxng`) | `WAMO_SEARXNG_URL` | Anthropic-Websuche |

- **Rückfall:** Fehlt Schlüssel, Adresse oder Modell, ist das Gateway nicht erreichbar oder bietet es das Modell nicht
  an, steht der Grund als Fehler im Log und der Baustein nutzt Claude. Die Oberfläche zeigt das am Modellnamen des
  Gesprächs („Claude Haiku 5.5 (Rückfall)“, eigene Modelle mit „(LiteLLM)“). `WAMO_LLM_RUECKFALL` steuert das je
  Baustein: leer (alle), `aus` (keiner) oder eine Liste wie `gespraech,zusammenfassung`. Ohne Rückfall bricht der
  Baustein ab, für das Gespräch endet die Sitzung mit einer Meldung im Log; die Sprache läuft weiter über Soniox.
- **Grenze:** Geprüft wird beim Sitzungsstart (Lernen und Bildschirm vor jedem Aufruf, dort auch mit Rückfall bei
  Ausfall). Fällt das Gateway mitten im Gespräch aus, wechselt das laufende Gespräch nicht zu Claude; kurze
  Zeitgrenzen (Verbindung 3 s, Pause im Stream 20 s, keine stillen Wiederholungen) verhindern, dass WAMO hängt.
- **Werkzeuge:** Das Gespräch über LiteLLM nutzt Pipecats OpenAI-Dienst mit derselben Persona, denselben Werkzeugen und
  demselben Satzende-Protokoll. Modelle mit Denkphase bekommen ihre Abschaltung über `WAMO_LLM_EXTRA` (JSON-Objekt;
  Unbekanntes geht als `extra_body` mit). Pipecat liest je Stream-Abschnitt nur einen Werkzeugaufruf; parallele
  Aufrufe eines Modells im selben Abschnitt gehen verloren.
- **Websuche im Haus:** Mit `WAMO_SUCHE=searxng` fragt WAMO die eigene SearXNG-Instanz (`voice-core/suche.py`). Die
  Treffer werden auf fünf kurze Auszüge gekürzt, von Steuer- und unsichtbaren Zeichen und Markern befreit, nur mit der
  Domain versehen und vor und nach den Treffern als unvertrauenswürdige Daten gekennzeichnet. Auch das Ergebnis der
  Anthropic-Suche trägt diese Kennzeichnung. Fehlt bei `searxng` die Adresse, schlägt die Suche fehl, statt still die
  Cloud-Suche zu nehmen. Kamen in einem Zug Web-, Bildschirm- oder PC-Inhalte vor, speichert `merken` Persönliches nur
  noch, wenn die Person es ausdrücklich sagt („merk dir“).
- **Abnahme:** `voice-core/eval/` enthält 51 Gesprächszüge (31 mit erwartetem Werkzeugaufruf, 6 Muss-Fälle zu
  Injektion, PC aus, Bildschirm gesperrt, Nachricht und Sätzen an andere) mit simulierten Werkzeugen, ohne Datenbank
  und ohne PC. Läufe vor dem 20.10.2026 gelten im Bericht als veraltet (anderer Testsatz). Ein
  Modell taugt fürs Gespräch nur bei mindestens 95 % richtigen Werkzeugaufrufen, mindestens 98 % korrekten
  Satzende-Markern, erstem Token p95 höchstens 0,6 s, ohne Verstoß in den Muss-Fällen und ohne Infrastrukturfehler,
  und das in drei Läufen hintereinander (pass^3, p95 über alle drei). Ergebnisse unter `voice-core/eval/ergebnisse/`
  (je Lauf eine JSON-Datei, nur anhängen; Übersicht in `bericht.md`, rechnet die Freigabe jedes Mal neu und markiert
  Läufe mit anderem Testsatz, Prompt oder anderer Bewertung als veraltet).

```powershell
cd voice-core
uv run python eval/abnahme.py --anbieter claude                                     # Referenz, wenige Cent
uv run python eval/abnahme.py --liste                                               # Modelle des Gateways
uv run python eval/abnahme.py --anbieter litellm --modell <alias> --wiederholungen 3   # ein Modell im Haus
uv run python eval/abnahme.py --anbieter litellm --alle --wiederholungen 3             # alle Chat-Modelle
uv run python eval/bausteine.py --anbieter litellm --modell <alias>                    # Lernen, Zusammenfassung, Bild
```

Die Bausteine Lernen, Zusammenfassung und Bildschirm prüft `voice-core/eval/bausteine.py` nach Qualität, nicht nach
Latenz: gelernte Einträge (Fakten, keine Passwörter, keine Daten über andere, keine Anweisungen aus Mails, nichts
doppelt), eine Zusammenfassung mit Namen, Zahlen und Terminen und ein erzeugtes Bildschirmfoto mit Text und einem
Knopf an bekannter Stelle. Ergebnisse unter `voice-core/eval/ergebnisse/bausteine/`.

Nach Änderungen an den PC-Werkzeugen des Hosts den Schnappschuss erneuern:
`host\.venv\Scripts\python.exe voice-core\eval\pc_werkzeuge_holen.py`.

## Boss und Modellkarte

**Modellkarte** (`voice-core/modellkarte.py`, nur Daten): je Modell Alias, Anbieter, Fähigkeiten mit gemessenen Werten
(Anteil bestandener Fälle, Zahl der Fälle, Datum, Quelldatei unter `voice-core/eval/ergebnisse/`), Latenzklasse
(schnell, mittel, langsam, Grenzen je Fähigkeit in `LATENZ_GRENZEN`), Datenschutz und Status (`standard`, `fachmodell`,
`gesperrt`, `verweis`). `test_modellkarte.py` rechnet jede Zahl aus den Ergebnisdateien nach. Das Fachmodell Recht
(`sokrates-steuer`) wählt der Boss nie von selbst, ein ungeprüftes Modell (`sokrates-fable-qwen27`) nie ohne eigene
Messung und Freigabe. Fehlende Werte misst `voice-core/eval/modellkarte_messen.py` mit kleinen, nacheinander laufenden
Tests (`code`, `analyse`, `bewerter`, `einbettung`, `latenz`, `kennenlernen`; Claude Haiku nur als kleine Referenz).

**Boss** (`voice-core/boss.py`, Regelwerk auf der gemessenen Karte, keine Modellfrage im Gespräch):

| Aufgabe | Wahl |
| --- | --- |
| Gespräch mit Werkzeugen | Haiku, wenn das Profil Claude darf; sonst das beste livetaugliche Hausmodell (Güte mit Abzug je Latenzklasse; ausgeschlossen, wenn der Prompt ohne Cache über 10 s braucht) |
| Code und Entwicklung | Claude Code, wenn ein Token oder API-Schlüssel hinterlegt ist; sonst keine (bis eine Hausvariante mit `wamo-coder` gemessen ist) |
| Lernen, Zusammenfassung, Bildschirm, Analyse, Bewertung | bestes Modell nach Güte und Antwortzeit; bei gleicher Güte gewinnt das Hausmodell |
| Unklare Aufgabe | kleines Modell ordnet ein (Hausprofil: bestes Hausmodell, Claude-Profil: Haiku) |
| Vertrauliches, Hausprofil, Nur Hausmodelle | immer Hausmodelle, nie Claude, auch nicht als Rückfall |

- **Erreichbarkeit:** Vor der Wahl prüft der Boss das Gateway und fragt das gewählte Hausmodell mit einer kleinen
  Probe (Zeitgrenze 10 s, Ergebnis 120 s gemerkt, Fehler 20 s; `WAMO_BOSS_PROBE=aus` schaltet sie ab). Antwortet es
  nicht innerhalb von 3 s (die erste Probe darf ein Kaltstart sein, die zweite nicht), nimmt der Boss das nächstbeste und sagt es klar (Karte und Statusleiste zeigen Modell, Modus und Grund).
  Erreicht kein erlaubtes Modell die Mindestgüte, nimmt er das beste verfügbare ab der Notfallgüte, sonst endet das
  Gespräch mit einer verständlichen Meldung. `WAMO_LLM_*` bleibt als Übersteuerung.
- **Aufgaben verteilen:** Das Werkzeug `aufgabe_verteilen(art, auftrag)` startet eine Aufgabe asynchron auf einem
  Spezialisten (höchstens 2 parallel, 30 je Sitzung, 3 Versuche, 240 s). Unter Vorgänge steht eine Karte, das Ergebnis
  kommt als unvertrauenswürdige Daten zurück (Marker entfernt, Ende gekennzeichnet) und das Protokoll enthält keine Inhalte.
- **Antwortzeit messen:** Kaltstart (Modell wurde aus dem Speicher verdrängt, etwa 9 Sekunden), erster Folgeaufruf und
  Prompt ohne Cache (etwa 7,5 Sekunden) sind getrennte Werte der Karte. Die langen Werte im Live-Gespräch (über eine
  Minute) ließen sich nicht reproduzieren, vermutlich Verdrängung oder Auslastung der Sparks.
- **Aufwärmen (Vorschlag, nicht eingerichtet):** Das Gesprächsmodell mit `keep_alive` (etwa 30 Minuten) auf den Sparks
  halten oder beim Anmelden eine kurze Aufwärmanfrage über LiteLLM schicken und alle 5 Minuten wiederholen, solange
  eine Sitzung offen ist.

## Sicherheitsmodell

- Zugang nur mit Konto im WAMO-Supabase, zweitem Faktor und aktivem Profil; jede Unsicherheit führt zur Ablehnung.
- Der Voice-Core arbeitet mit der Datenbankrolle `wamo_core` (nur WAMO-Schemas), nie mit dem Service-Schlüssel.
- PC-Aktionen: `read` sofort, `change` protokolliert, `critical` nur nach Freigabe per Karte in der Oberfläche (Admin,
  PC-Steuerung an, PC-Sperre gehalten, zweites Geheimnis im Host, befristet, genau einmal), `blocked` nie. Solange
  eine Freigabe offen ist, ruhen alle Eingaben; die eigene Oberfläche bedient WAMO nie. Bei gesperrtem Bildschirm keine
  Eingaben, Klicks, Fotos, kein Öffnen und kein Lesen.
- Inhalte aus Web, Dateien und Gedächtnis sind Daten, nie Anweisungen an WAMO.
- Geheimnisse nur in `.env`, `ui/.env.local`, `/etc/wamo/geheim.env` (Aufträge) und `/etc/wamo/core.env` (Kern auf
  Sokrates-2, nur über `wamo-core-env` per stdin), nie im Repository. Host-Token und Freigabe-Schlüssel liegen auf dem
  PC in `%LOCALAPPDATA%\WAMO\host-token` und `host-freigabe` (nur für den Benutzer lesbar, für die PC-Werkzeuge selbst
  gesperrt); der Host entfernt den Freigabe-Schlüssel aus seiner Umgebung, Kindprozesse erben ihn nicht.
- Anmeldung und Datenbank im LAN nur verschlüsselt und geprüft gegen die eigene CA (`WAMO_CA_FILE`); die WAMO-Ports
  auf Sokrates-2 nehmen nur Verbindungen aus Loopback und dem Büro-LAN an (HTTP 55321 zusätzlich aus dem Caddy-Netz).

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
