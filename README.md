# WAMO

Sprachassistent der WAMOCON GmbH: Livegespräch mit natürlichen Unterbrechungen, Oberfläche im WMC-Design,
Werkzeuge für den Windows-PC, Entwicklungsaufträge mit ECC und REA, mehrere Nutzerprofile mit persönlichem und
gemeinsamem Gedächtnis.

| Ordner | Was | Läuft auf |
| --- | --- | --- |
| `ui/` | Next.js 16: Präsenzkern, Live-Untertitel, Stimmlinie, Werkzeug-Zeitleiste, Canvas, Freigabe-Karten, Anmeldung mit zweitem Faktor | PC (Desktop-App oder Browser) |
| `voice-core/` | Pipecat 1.12: Soniox STT, Claude Haiku 5.5, Soniox TTS; Mehrprofil-Anmeldung, Gedächtnis, Werkzeugkarte für ECC und REA | PC (Unterwegs) oder Sokrates-2 als Dienst `wamo-core` (Büro) |
| `host/` | MCP-Werkzeugserver für Windows mit Risikoklassen und Audit-Log; lokal über stdio, im Büro über den SSH-Rücktunnel mit Bearer-Token | PC (angemeldete Sitzung) |
| `desktop/` | Electron 44: App-Fenster, roter Stimmen-Rand je Monitor, Tray, Autostart, Betriebsart Büro/Unterwegs | PC |
| `supabase/` | Migrationen mit `__SCHEMA__`-Platzhalter | eigener WAMO-Supabase auf Sokrates-2 |
| `infra/sokrates2/` | Einrichtung von Supabase-Stack, TLS (eigene CA, Caddy), Firewall und Benutzer `wamo` (Claude Code, ECC, REA, gh, Vercel, Supabase-CLI) | Sokrates-2 |
| `scripts/` | Start der Desktop-App, Datenbankzugang, Nutzerverwaltung, Migrationen | PC |

## Stand (19.10.2026)

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
- Tests: voice-core 206 bestanden (inklusive TLS-Handshakes und TLS gegen den echten Pooler), host 23 bestanden; UI: Lint, Typen und Build grün.
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

### Offen

- Soniox: Der Schlüssel gehört zu einem US-Projekt, die Spracherkennung läuft deshalb vorerst über den US-Endpunkt.
  Für EU-Datenhaltung EU-Schlüssel anlegen und `SONIOX_STT_URL` und `SONIOX_TTS_URL` auf `*.eu.soniox.com` stellen.
- Weitere PCs: `infra/sokrates2/wamo-ca.crt` importieren (ein `certutil`-Befehl, siehe `infra/sokrates2/README.md`,
  Abschnitt 5.2). Seit 19.10.2026 gilt Stufe 2: HTTP 55321 ist im LAN geschlossen, nur noch HTTPS 55443.
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

Umschalten in der Oberfläche (unter den Schaltflächen, nur ohne laufendes Gespräch) oder im Tray-Menü der Desktop-App;
die Einstellung gilt dauerhaft je Gerät (Desktop: `%APPDATA%\wamo-desktop\einstellungen.json`, Browser: localStorage).
Die Statusleiste zeigt Betriebsart und Kern, der PC-Host den Zielrechner. Einrichtung des Tunnels auf diesem PC (ohne
Adminrechte, Rückweg `scripts\wamo-tunnel-entfernen.ps1`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wamo-tunnel-einrichten.ps1   # Token, Schlüssel, known_hosts, Aufgabe
python scripts\wamo_kern_sokrates2.py verteilen                                         # Schlüssel und Skripte nach Sokrates-2
# Server einrichten: infra/sokrates2/README.md, Abschnitt 10
python scripts\wamo_kern_sokrates2.py alles --email vorname.nachname@wamocon.com         # Werte nur über stdin
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wamo-tunnel-einrichten.ps1 -Starten
```

Die geplante Aufgabe „WAMO Tunnel“ startet bei jeder Anmeldung `scripts\wamo-tunnel.ps1`: Host und Tunnel laufen damit
unabhängig von der Desktop-App (auch für den Browser). Die PC-Steuerung bleibt trotzdem je Sitzung aus, bis ein Admin
sie einschaltet. Logs: `%LOCALAPPDATA%\WAMO\logs`.

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
welche REA-Werkzeuge der Lauf zuerst nutzt. ECC-Hooks bleiben aktiv. Freigabepflichtig sind unter anderem `git push`,
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
| `infra/sokrates2/claude-code/managed-settings.json` | Wächter-Hooks (vorher und nachher), nur verwaltete Hooks (ECC als verwaltetes Plugin), Sperrliste |
| `infra/sokrates2/wamo-job-einrichten.sh` | Installation nach `/opt/wamo` (root-eigen), AppArmor-Profil nur für eine bwrap-Kopie, sshd-Drop-in, Schlüssel mit `from=` |
| `infra/sokrates2/wamo_job_rauchtest.py` | Rauchtest: `sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp --attrappe` |

Der Voice-Core schickt den Auftrag per SSH (`~/.ssh/id_ed25519_wamo_jobs`, Hostschlüssel aus
`~/.ssh/known_hosts_wamo_jobs`, Ziel `WAMO_ENTWICKLUNG_SSH_ZIEL`, Standard `wamo@192.168.178.136`) und liest die
stream-json-Ausgabe wie lokal. stdin bleibt offen: Auflegen beendet ssh und damit den entfernten Auftrag. In die Sandbox
gelangen nur die Token der Auftragsart aus `/etc/wamo/geheim.env` (`ZUGANG_VARIABLEN` in `voice-core/werkzeugkarte.py`;
lesende Arten nur `GH_TOKEN_LESEN`), als Umgebungsvariablen, nie über die Befehlszeile. Bekäme ein Lauf mehr Token als
frühere Läufe im selben Projekt, braucht er eine Freigabe. Grenze: Die Token der Auftragsart sind in der Sandbox lesbar
und über HTTPS nutzbar. Token deshalb eng zuschneiden (Repos, Rechte, Ablaufdatum). Einzelheiten:
`infra/sokrates2/README.md`, Abschnitt 9.

## Modelle (Ausbaustufe P4)

Jeder Baustein lässt sich zwischen Claude und den eigenen Modellen auf den Sparks umschalten
(`voice-core/llm_anbieter.py`). Eigene Modelle laufen nur über das LiteLLM-Gateway (`LITELLM_BASE_URL`,
`LITELLM_API_KEY`), nie direkt über die Adressen der Knoten. Standard ist überall Claude.

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
- **Abnahme:** `voice-core/eval/` enthält 50 Gesprächszüge (30 mit erwartetem Werkzeugaufruf, 5 Muss-Fälle zu
  Injektion, PC aus, Nachricht und Sätzen an andere) mit simulierten Werkzeugen, ohne Datenbank und ohne PC. Ein
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
```

Nach Änderungen an den PC-Werkzeugen des Hosts den Schnappschuss erneuern:
`host\.venv\Scripts\python.exe voice-core\eval\pc_werkzeuge_holen.py`.

## Sicherheitsmodell

- Zugang nur mit Konto im WAMO-Supabase, zweitem Faktor und aktivem Profil; jede Unsicherheit führt zur Ablehnung.
- Der Voice-Core arbeitet mit der Datenbankrolle `wamo_core` (nur WAMO-Schemas), nie mit dem Service-Schlüssel.
- PC-Aktionen: `read` sofort, `change` protokolliert, `critical` nur nach Freigabe, `blocked` nie.
- Inhalte aus Web, Dateien und Gedächtnis sind Daten, nie Anweisungen an WAMO.
- Geheimnisse nur in `.env`, `ui/.env.local`, `/etc/wamo/geheim.env` (Aufträge) und `/etc/wamo/core.env` (Kern auf
  Sokrates-2, nur über `wamo-core-env` per stdin), nie im Repository. Das Host-Token liegt auf dem PC in
  `%LOCALAPPDATA%\WAMO\host-token` (nur für den Benutzer lesbar, für die PC-Werkzeuge selbst gesperrt).
- Anmeldung und Datenbank im LAN nur verschlüsselt und geprüft gegen die eigene CA (`WAMO_CA_FILE`); die WAMO-Ports
  auf Sokrates-2 nehmen nur Verbindungen aus Loopback und dem Büro-LAN an (HTTP 55321 zusätzlich aus dem Caddy-Netz).

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
