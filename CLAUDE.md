# WAMO: Hinweise für Claude Code

WAMO ist der Sprachassistent der WAMOCON GmbH. Stand, offene Punkte und Ausbaustufen stehen in `README.md`.

Monorepo: `ui/` (Next.js 16), `voice-core/` (Python, Pipecat 1.x), `host/` (Python, MCP v1, Windows),
`desktop/` (Electron), `supabase/` (SQL-Migrationen mit `__SCHEMA__`-Platzhalter), `scripts/`,
`labor-dienst/` (Python, FastAPI, SQLite: das Test-Jira des Testlabors, Paket `wamo-labor-dienst`, Umgebung `WAMO_LABOR_DIENST_*`,
Vertrag `docs/labor-dienst-vertrag.md`, Start `python -m wamo_labor_dienst`, Tests `python -m pytest labor-dienst/tests`;
die Labor-Daten dazu liegen unter `infra/sokrates2/labor-daten/`, auf dem Server in `/etc/wamo-labor/daten`, eingerichtet mit `wamo-labor-daten-einrichten.sh`).

## Namensvertrag (verbindlich, alle Teile gleichzeitig)

| Bereich | Wert |
| --- | --- |
| Anzeigename | `WAMO` (in Texten „WAMO“, grammatisch „der Assistent“) |
| Kennung, Paketpräfix | `wamo` (`wamo-voice-core`, `wamo-host`, `wamo-desktop`, `wamo-ui`) |
| Env-Präfix | `WAMO_`, Next.js öffentlich `NEXT_PUBLIC_WAMO_*` nur wenn nötig |
| DB-Schemas | `wamo_dev`, `wamo_test`, `wamo_prod` |
| DB-Rolle Voice-Core | `wamo_core` (Verbindung über `WAMO_DATABASE_URL`, nur lesen, einfügen, gezielte Updates) |
| DB-Rolle Verwaltung | `wamo_admin` (Verbindung über `WAMO_ADMIN_DATABASE_URL`, nur `voice-core/wamo_admin.py`) |
| Host | `host/wamo_host.py`, FastMCP-Name `wamo-host`, Audit `host/wamo_audit.jsonl` |
| Desktop-Brücke | `window.wamoDesktop` (preload.js und ui/src/lib/useWamo.ts) |
| Overlay-Fenstertitel | `WAMO Stimmen-Rand` (desktop/overlay.html und Filter in host/windows_control.py) |
| UI-Hook | `ui/src/lib/useWamo.ts`, Komponente `Wamo` |
| Skripte | `scripts/wamo-desktop.cmd`, `scripts/wamo-app.cmd`, Chrome-Profil `%LOCALAPPDATA%\wamo-chrome` |
| Oberfläche auf Sokrates-2 | Container `wamo-ui-blau` und `wamo-ui-gruen` (Images `wamo-ui:blau`, `wamo-ui:gruen`), Farbe in `etc/kern/wamo-ui-aktiv.inc`, Skript `wamo-ui-umschalten.sh`, Gesundheitsprobe `GET /version.json` |
| TOTP-Aussteller | `WAMO` |
| Repo-Selbstschutz | `SELF_REPO = "wamo"` in voice-core/dev_guard.py |
| Geräte | Register `devices` (Kennung `pc-<name>` für Laptops), Gerätedienst `wamo-geraete` (Benutzer, Socket `/run/wamo-geraete/geraete.sock`), Lesekonto `wamo-status` auf den Sparks |
| Tunnel je Laptop | Benutzer `wamo-t-NN` (NN = `tunnel_slot` 01 bis 64), Ordner `/run/wamo-pc/tNN/`, Kern-Schalter `WAMO_PC_REGISTER`, Header `X-WAMO-Geraet` und `X-WAMO-Profil`, Laptop-Dateien `%LOCALAPPDATA%\WAMO\geraet.json`, `tunnel.status`, Schlüssel `id_ed25519_wamo_pc` |
| Anker und Release-Dienst | Anker (root, außerhalb von `/opt/wamo*`): Dienst `wamo-anker`, Code `/usr/local/lib/wamo-anker`, Socket `/run/wamo-anker/anker.sock`, Sperre `/etc/wamo-anker/selbstaenderung.aus`. Release-Dienst (kein root): Benutzer `wamo-release`, Code `/opt/wamo-release/app`, Daten `/var/lib/wamo-release`, Protokoll `/var/log/wamo-release/audit.jsonl`, Socket `/run/wamo-release/ctl.sock`; DB-Rollen `wamo_release` und `wamo_anker`, Env `WAMO_RELEASE_*` (derzeit keine) |
| Freigabe-Dienst | Benutzer `wamo-anker`, Dienst `wamo-freigabe` (Code `/opt/wamo-freigabe/app`, Bibliotheken `/opt/wamo-freigabe/pylib`, Zugang `/etc/wamo-freigabe/db.zugang`, Socket `/run/wamo-freigabe/freigabe.sock`), Ausgangsregel `wamo_freigabe_ausgang`, Skripte `freigabe-einrichten.sh`, `freigabe-rueckweg.sh`, `freigabe_rauchtest.py`, `freigabe_tokentest.py` (echtes GoTrue-Token, als `wamocon`), Umgebung `WAMO_FREIGABE_*`; Socketordner `/run/wamo-freigabe` (`2750 wamo-anker:wamocon`, per `tmpfiles.d`, Peers 0 und 1000), Route `POST /wamo/release/<id>/freigeben` auf 55444 in `wamo-caddy/kern/wamo-core.caddy` (Rückweg `wamo-core-caddy.sh --route-zurueck`, Sicherung `wamo-core.caddy.vorher`) |
| Prüfer-Sandbox | Benutzer `wamo-pruefer` (Sandbox, fremder Code) und `wamo-pruefer-proxy` (Proxy), Code `/opt/wamo-pruefer/app`, Werkzeuge (Node 22, uv, ruff, pip-audit) `/opt/wamo-pruefer/werkzeuge`, Arbeitsordner `/srv/wamo-pruefer/lauf`, Konfiguration `/etc/wamo-pruefer`, Dienste `wamo-pruefer-ausgang` und `wamo-pruefer-proxy@pakete` (127.0.0.1:18080), nft-Tabelle `wamo_pruefer_ausgang`, bwrap-Kopie `/usr/local/lib/wamo-pruefer/bwrap` mit AppArmor-Profil `wamo-pruefer-bwrap`, Aufruf `wamo-pruefer-lauf.sh`, Annahme-Dienst (Weg vom Release-Dienst in die Sandbox, Root, Stände als Datenstrom) `wamo-pruefer-annahme.socket` (`/run/wamo-pruefer-annahme/annahme.sock`, nur `wamo-release`) mit Vorlage `wamo-pruefer-annahme@.service`, Zwischenablage `/srv/wamo-pruefer/annahme`, Client `pruefer_client.py`, Claude-Proxy `wamo-pruefer-proxy@claude` (127.0.0.1:18081, nur `api.anthropic.com`), Mustervorlage des Fremdverweis-Greps `/etc/wamo-anker/fremdverweise.txt` (nie im Repo), Skripte `pruefer-einrichten.sh`, `pruefer-rueckweg.sh`, `pruefer_rauchtest.py` |
| Migrationsprobe | Dienst `migprobe_dienst.py` (Code `/opt/wamo-pruefer/app`, nur root, Docker-Zugang), Units `wamo-migprobe.socket` (`/run/wamo-migprobe/probe.sock`, 0600 root) und `wamo-migprobe@.service`, Container `wamo-migprobe-*` (Image der Datenbank des Kerns, ohne Netz, nie nachgeladen), Aufruf nur durch den Annahme-Dienst der Prüfer-Sandbox, Lint `migration_lint.py` |
| Kern-Slots | Vorlage `wamo-core@.service` (Instanz `a` oder `b`), Socket `/run/wamo-core/core-<slot>.sock`, Umgebung `WAMO_SLOT`, Release-Ordner `/opt/wamo-core/releases/<name>` (mit `voice-core/` und eigenem `venv/`), Verweise `/opt/wamo-core/slots/a` und `b`, Skript `wamo-core-slots.sh` (`einrichten`, `release`, `slot`, `status`); die alte Unit `wamo-core.service` mit `core.sock` gilt bis zur Umstellung |
| Remote Desktop (Gateway) | Container `wamo-rd-web` und `wamo-rd-guacd` (Compose-Projekt `wamo-rd`, Ordner `/home/wamocon/wamo-rd`), Netze `wamo-rd-netz`, `wamo-rd-guacd-netz`, `wamo-rd-ziel`, Port 55446 (HTTPS über `wamo-caddy`), Ordner `/etc/wamo-rd` (`ziele.toml`, `ziele.env`, `json.key`) und `/var/lib/wamo-rd`, Benutzer `wamo-rd-gateway`, Konto `wamo-rd` auf den Sparks (Gruppe `wamo-rd-ts`), Firewall-Ketten `WAMO-FW-RD` und `WAMO-FW-RDIN`, Skripte `wamo-rd-einrichten.sh`, `wamo-rd-rueckweg.sh`, `wamo_rd_rauchtest.py` |
| Labor-Dienst (Betrieb) | Container `wamo-labor-dienst` (Compose-Projekt `wamo-labor` in `/opt/wamo-labor`, Image `wamo-labor-dienst:1`, Benutzer 10001 ohne Konto auf dem Host), Netze `wamo-labor-netz` (172.28.0.0/24, intern, legt `wamo-caddy` an, Caddy 172.28.0.2, Dienst 172.28.0.10) und `wamo-labor-lokal` (172.28.1.0/29, nur für die Veröffentlichung auf Loopback, Ausgang gesperrt), Port 55447 (HTTPS über `wamo-caddy`, Site `wamo-labor.caddy` aus `wamo-caddy/kern/wamo-labor.caddy`), `127.0.0.1:18765` nur für den Kern, Ordner `/etc/wamo-labor` (`kern-token` und `betreuer-token` je 0400 Besitzer 10001, `betreuer-adressen`, `plaetze-adressen`, `basis-image`), `/var/lib/wamo-labor` (Daten, 0700 Besitzer 10001) und `/var/backups/wamo-labor` (Stände vor dem Einrichten, ohne Token und Daten), Firewall-Ketten `WAMO-FW-LABOR` (Quellen für 55447), `WAMO-FW-LABAUS` (Ausgang) und `WAMO-FW-LABIN` (nur Antworten an den Host; der Ausgang des Dienstes hängt allein an `wamo-firewall`, nach `systemctl stop wamo-firewall` ist er offen), Skripte `wamo-labor-einrichten.sh` (Caddy in zwei Stufen), `wamo-labor-rueckweg.sh`, `wamo-labor-loeschlauf.sh` (Bestätigungswort `LABOR-LOESCHEN`) und `wamo_labor_rauchtest.py` (20 Proben, Attrappe in `wamo_labor_rauchtest_attrappe.py`), Umgebung des Dienstes `WAMO_LABOR_DIENST_*`, Einstellungen des Kerns `WAMO_LABOR_URL` und `WAMO_LABOR_TOKEN` (nur über `wamo-core-env`, wirken mit dem nächsten Kern-Release), Aufbewahrung bis 11.12.2026; Abschnitt 19 in `infra/sokrates2/README.md` |
| Entwicklungsaufträge im Büro (Kern) | Konzept `docs/entwicklung-im-betrieb.md`, Arbeitsliste `docs/entwicklung-im-betrieb-arbeitsliste.md`. Schalter und Einstellungen im Drop-in `/etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf` (root:root 0644, ohne Spiegel-Marke, nur `wamo-job-kern-einrichten.sh schalter` schreibt es): `WAMO_ENTWICKLUNG_BUERO` (`an` oder `aus`, Standard aus), `WAMO_ENTWICKLUNG_ORT` (im Büro nur `sokrates2`), `WAMO_ENTWICKLUNG_PROFILE` (eine oder mehrere `user_id`, Pflicht), `WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD` und `WAMO_ENTWICKLUNG_KOSTEN_TAG_USD` (im Büro Pflicht, fehlend heißt aus, das Skript schreibt beide immer), `WAMO_ENTWICKLUNG_BEFEHLE_BUERO` (Standard aus). `WAMO_ENTWICKLUNG` bleibt in den Units `aus` und zählt im Büro nicht. Code `voice-core/entwicklung_betrieb.py` mit `SCHRANKEN_VERSION` (genau eine Zeile `SCHRANKEN_VERSION = <n>`, `schalter` prüft sie im Zielrelease), `BueroZulassung` (Kurzkennung aus 8 Hexzeichen), `kern_projektname`, `GRUND_SAETZE`; Quittung `auftrag_quittung` und Statusfeld `entwicklung_grund` (nur Admins); Logik der Oberfläche `ui/src/lib/auftragStart.ts`; Audit und Kostenbremse über die Tabelle `actions` mit dem Werkzeug `entwicklungsauftrag` (Funktionen `aktion_abschliessen`, `tageskosten`, `veraltete_schliessen` in `memory.py`, keine Migration) |
| Kern-Kanal des Dispatchers | Dispatcher 7 (`wamo_job_dispatch.py`, `VERSION = "7"`) mit dem Modul `infra/sokrates2/wamo_job_kernkanal.py` (installiert nach `/opt/wamo/waechter/`, root:wamo 0640). Der Kern ruft mit dem Schlüssel `wamo-kern-jobs` auf (Kommentar der Zeile, Datei `id_ed25519_wamo_jobs` im Home von `wamo-core`). Genau eine Zeile in `/etc/ssh/authorized_keys.d/wamo-kern` (root:root 0644, ohne `expiry-time`), die PC-Datei `authorized_keys.d/wamo` bleibt unberührt; der sshd-Block `Match User wamo` hat `ExposeAuthInfo yes` und `AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u /etc/ssh/authorized_keys.d/%u-kern`. Regeldatei `/etc/wamo/kernkanal.conf` (root:wamo 0640, Schlüssel nach Schnittstelle I2, dazu `MAX_VERSUCH_STUNDE`), Sperrdatei `/etc/wamo/kernkanal.notaus`, Zähler `/srv/wamo/logs/kernkanal.jsonl` (Ereignisse `start`, `ende` und `versuch`). Ablehnung als Ereignis `kanal_gesperrt` mit einem Code aus `KANAL_GRUENDE` und Exit 2 (im Kern der Satz aus `GRUND_SAETZE`). Projektnamen `kern-<Name>`: Namensraum nur für den Kern-Kanal, Umlaute und ß werden wie jedes Zeichen außerhalb von a-z0-9- verworfen. Der Vertragstest `voice-core/test_kernkanal_vertrag.py` prüft beide Seiten gegeneinander. Weitere Tests: `voice-core/test_entwicklung_betrieb.py`, `voice-core/test_entwicklung_haertung.py`, `voice-core/test_kernkanal.py`, `voice-core/test_job_kern_skripte.py`, `scripts/test_wamo_kern_sokrates2.py`, `ui/src/lib/auftragStart.test.ts` |
| Skripte des Kern-Kanals | `wamo-job-kern-einrichten.sh` (`schluessel`, `kanal`, `schalter`, `notaus-aufheben`, `status`; Hilfsfunktion `als_kern`: jede Dateiarbeit im Baum `/var/lib/wamo-core/.ssh` läuft als `wamo-core`), `wamo-job-kern-rueckweg.sh` (`notaus`, `--entfernen`; der Notaus legt das Schlüsselpaar mit der Endung `.notaus-<Zeit>` beiseite, ein neues Paar entsteht nur mit `schluessel`), `wamo_job_kern_rauchtest.py` (installiert als `/opt/wamo-core/bin/wamo-job-kern-rauchtest`, alle Proben nur am Server prüfbar). Probelauf ohne root mit `WAMO_JOB_KERN_WURZEL`, `WAMO_JOB_KERN_PYTHON` und `WAMO_JOB_KERN_SUCHFRIST_S`. Sicherungen `/var/backups/wamo-job-kern/<JJJJMMTT-HHMMSS>/` (dort auch `wamo.conf.vorher`, die vorige sshd-Datei, die `wamo-job-einrichten.sh` für den Totmannschalter ablegt), Bedienspur `/var/log/wamo-job-kern/aktionen.jsonl`, Journal-Marke `wamo-job-kern`. Im Slot-Betrieb starten die Einrichtungsskripte die alte Unit `wamo-core.service` nie neu (Hilfsfunktion `slot_betrieb`, Hinweis auf `umschalten-kern`) |
| Labor-Daten (Testlabor) | Quelle `infra/sokrates2/labor-daten/` (acht Dateien plus README, nur LF), Server `/etc/wamo-labor/daten/` (root:root 0755, Dateien 0644; `/etc/wamo-labor` legt `wamo-labor-einrichten.sh` an; nie unter `/etc/wamo`, denn `wamo-core` kann `/etc/wamo` nicht durchqueren; der Kern bekommt den Ordner über `WAMO_LABOR_DATEN=/etc/wamo-labor/daten` in `core.env`, gesetzt vom Skript über `wamo-core-env`, wirksam mit dem nächsten Kern-Release; der Standard `STANDARD_VERZEICHNIS` im Lader bleibt unverändert), Lader `voice-core/labor_daten.py` (feste Dateiliste, zusammen höchstens 6000 Zeichen, Regelteil `persona-zusatz.md` höchstens 2000). Zwei Fassungen der Regeln: `persona-zusatz.md` (Standard, ohne "frag bei fehlenden Angaben nach") und `persona-zusatz.streng.md`, beide müssen mit allen Daten unter die Grenze passen. Skript `wamo-labor-daten-einrichten.sh` (nur root, `einrichten [--persona standard\|streng] [--release NAME] [--trocken]`, `status`, `rueckweg`; prüft vor und nach der Installation mit dem echten Lader als `wamo-core`, nimmt bei Ablehnung zurück, startet den Kern nie neu und setzt nur `WAMO_LABOR_DATEN`), Sicherungen `/var/backups/wamo-labor-daten/<JJJJMMTT-HHMMSS>/` (die Datei `VORHER` nennt das Ziel und schließt die Sicherung ab), Probelauf ohne root mit `WAMO_LABOR_DATEN_WURZEL`, `WAMO_LABOR_DATEN_LADER` und `WAMO_LABOR_DATEN_PYTHON`. Kontakte nur `@labor.test`, die Standard-Domäne des Mail-Filters (`DOMAIN_STANDARD` in `voice-core/labor_mail.py`, ein Test prüft die Übereinstimmung, der Kern bleibt unverändert); die Anmelde-Adressen der Laborprofile bleiben `@wamo.test`. Tests `voice-core/test_labor_daten_inhalt.py`, `voice-core/test_labor_daten_skript.py` |
| Weckwort (später) | „Hey WAMO“, DB-Standard `wake_word = 'wamo'` |

## Architektur-Entscheidungen (19.10.2026)

1. **Eigener Supabase-Stack auf Sokrates-2** (192.168.178.136, Compose-Projekt `supabase-wamo` in
   `/home/wamocon/supabase-wamo`, Container `wamo-supabase-*`, eingerichtet mit `infra/sokrates2/supabase-wamo-einrichten.sh`).
   Getrennt von allen anderen Diensten auf Sokrates-2: eigene Konten, eigene Geheimnisse, Registrierung geschlossen.
   API und Auth `https://192.168.178.136:55443` (ES256, Prüfung lokal über JWKS; Caddy `wamo-caddy` vor Envoy
   `:55321`, eigene CA `infra/sokrates2/wamo-ca.crt`, im Voice-Core über `WAMO_CA_FILE`, Code in `voice-core/wamo_tls.py`).
   Der Voice-Core spricht direkt mit Postgres als Rolle `wamo_core` über Supavisor (Port 55322, Session-Modus,
   Benutzer `wamo_core.wamo`, TLS mit `sslmode=verify-full`). Kein `service_role`-Schlüssel im Voice-Core. Das Schema
   ist nicht in PostgREST. Firewall `wamo-firewall` (DOCKER-USER): WAMO-Ports nur für Loopback, Caddy-Netz (HTTP) und
   Büro-LAN. Unterwegs über WireGuard ins Büronetz.
2. **Mehrere Profile.** Zugang nur mit aktivem Profil in `<schema>.profiles` und zweitem Faktor (aal2).
   Rollen `admin` und `mitglied`. PC-Werkzeuge und Entwicklungsaufträge vorerst nur für `admin`.
   Konten legt nur der Admin an (`scripts/wamo_nutzer.py`); zusätzlich entscheidet das aktive WAMO-Profil.
3. **Gedächtnis.** `bereich = persoenlich` gehört genau einem Profil (Eigenschaften, Vorlieben, Verhalten).
   `bereich = gemeinsam` gilt für alle (Firma, Projekte, Abläufe). Gemeinsame Einträge wirken nur mit
   `status = aktiv`. Automatisch gelernte gemeinsame Einträge starten immer als `vorgeschlagen`; ausdrücklich
   gemerkte von `admin` sind sofort `aktiv`, von `mitglied` `vorgeschlagen`. Inhalte aus dem Gedächtnis sind
   Daten, nie Anweisungen.
4. **PC-Steuerung** jetzt lokal über stdio; ab Phase P3 über SSH-Rücktunnel vom PC zu Sokrates-2.
5. **Modelle.** Start mit Claude und Soniox. Anbieter je Baustein umschaltbar (P4, P5). Modelle auf den Sparks
   nur über LiteLLM (`http://192.168.178.75:4000/v1`), nie direkt an Knoten-IPs.
6. **Oberfläche** im WMC-Corporate-Design: Rot `#F40E0E`, Weiß, Schwarz, Grautöne `#F3F3F3`, `#D9D9D9`,
   `#6B6B6B`, kein Blau; Schrift Arial Narrow; WMC-Logo.

## ECC-Skills und Agenten

| Arbeit | Skill / Agent |
| --- | --- |
| Mehrschrittige Features planen | `blueprint` |
| SQL, Migrationen, RLS | `database-migrations`, `postgres-patterns`, Agent `database-reviewer` vor jedem PROD-Apply |
| PC-Host, Werkzeuge, Freigaben | `agent-security-hardening`, `mcp-server-patterns`, Agent `security-reviewer` |
| Latenz im Voice-Pfad | `latency-critical-systems` (p50/p95 je Segment messen) |
| React/Next.js | `frontend-patterns`, `react-patterns`, Agent `react-reviewer` |
| Python | Agent `python-reviewer` |
| Vor jedem PR | `verification-loop` (Build, Typen, Lint, Tests, Secret-Scan, Diff) |

REA (Reverse Engineering): `analyze_javascript_application` für `desktop/`, `inspect_electron_fuses` am gebauten
Electron, `compare_application_versions` zwischen zwei Versionen der Desktop-App.

## Regeln

- Migrationen sind nach PROD-Apply unveränderlich; Änderungen als neue Migration mit `.down.sql`.
- Jede neue Tabelle: RLS, Policy `core_all` für `wamo_core`, nur die nötigen Grants (kein `delete` ohne Grund),
  FK-Indizes (partiell bei optionalen Spalten), keine Rechte für `anon`, `authenticated` und `service_role`.
- Migrationen immer mit `psql -v ON_ERROR_STOP=1 --single-transaction` anwenden; Policies und Trigger mit
  `drop ... if exists` davor, damit ein zweiter Lauf nicht abbricht.
- Geheimnisse nur in `.env` (nie committen). Kein `service_role`-Key in `ui/` oder im Voice-Core.
- Neue PC-Werkzeuge im Host: Risikoklasse festlegen, im Host prüfen (nicht im Prompt), Audit-Eintrag.
- Sprachausgaben von WAMO: kurz, kein Markdown (siehe `voice-core/persona.py`).
- Deutsche Texte mit echten Umlauten und ohne Gedankenstriche im Fließtext; Bezeichner, Pfade und Env-Namen in ASCII.
