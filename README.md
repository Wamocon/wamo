# W.A.I.S.

W.A.I.S. (WAMOCON’s Artificial Intelligence System, deutsch „WAMOCONs künstliches intelligentes System“, kurz „Wais“, gesprochen wie das englische „ways“) ist der Sprachassistent der WAMOCON GmbH: Livegespräch mit natürlichen Unterbrechungen, Oberfläche im WMC-Design,
Werkzeuge für den Windows-PC, Entwicklungsaufträge mit ECC und REA, mehrere Nutzerprofile mit persönlichem und
gemeinsamem Gedächtnis.

**Name.** Geschrieben wird der Assistent überall, wo Menschen ihn lesen, als „W.A.I.S.“ (mit Schlusspunkt). In gesprochenen
Antworten, Ansagen und im Verlauf heißt er kurz „Wais“; so verlangt es die Persona, denn „W.A.I.S.“ läse die Stimme als einzelne
Buchstaben. Die deutsche Stimme liest „Wais“ allerdings wie „weiß“. Deshalb schreibt `voice-core/aussprache.py` den Namen nur für
die Sprachausgabe um: „Wais“ und „W.A.I.S.“ werden „Ways“ (so trifft die Stimme die Aussprache, Hörprobe vom 14.11.2026),
„WAMOCON’s“ wird „Wamokons“, und den buchstabierten Namen („W, A, I, S“, auch „W-A-I-S“, „W. A. I. S.“ und „W A I S“)
spricht die Stimme als „Weh Ej, Ei Es“ (gemischt deutsch und englisch, `NAME_BUCHSTABIERT_TTS`). Das hängt als Text-Transform am TTS-Dienst,
Verlauf und Untertitel behalten „Wais“. Alle Schreibweisen stehen als Konstanten in `voice-core/persona.py` (`NAME_ANZEIGE`,
`NAME_KURZ`, `NAME_LANG`, `NAME_LANG_DE`, `NAME_TTS`, `NAME_BUCHSTABIERT`, `NAME_BUCHSTABIERT_TTS`), die der Oberfläche in `ui/src/lib/anzeigename.ts`. Die Spracherkennung
kennt „Wais“ und „W.A.I.S.“ als Begriffe; als Anrede zählen auch „Waze“, „Ways“, „WAIS“, „Weis“ und der frühere Name „WAMO“.
Der interne Name bleibt WAMO: Ordner, Pakete, Umgebungsvariablen `WAMO_*`, Dienste, Datenbankschemas, Dateinamen, Log-Meldungen
und der Repo-Name heißen weiter `wamo`. In den technischen Abschnitten dieser Datei steht deshalb „WAMO“ für W.A.I.S.

| Ordner | Was | Läuft auf |
| --- | --- | --- |
| `ui/` | Next.js 16: Präsenzkern, Live-Untertitel, Stimmlinie, Werkzeug-Zeitleiste, Canvas, Freigabe-Karten, Anmeldung mit zweitem Faktor | PC (Desktop-App oder Browser) oder als Container `wamo-ui` auf Sokrates-2 (Browser jedes Büro-Geräts) |
| `voice-core/` | Pipecat 1.12: Soniox STT, Claude Haiku 5.5, Soniox TTS; Mehrprofil-Anmeldung, Gedächtnis, Werkzeugkarte für ECC und REA | PC (Unterwegs) oder Sokrates-2 als Dienst `wamo-core` (Büro) |
| `host/` | MCP-Werkzeugserver für Windows mit Risikoklassen und Audit-Log; lokal über stdio, im Büro über den SSH-Rücktunnel mit Bearer-Token | PC (angemeldete Sitzung) |
| `desktop/` | Electron 44: App-Fenster, roter Stimmen-Rand je Monitor, Tray, Autostart, Betriebsart Büro/Unterwegs | PC |
| `supabase/` | Migrationen mit `__SCHEMA__`-Platzhalter | eigener WAMO-Supabase auf Sokrates-2 |
| `infra/sokrates2/` | Einrichtung von Supabase-Stack, TLS (eigene CA, Caddy), Firewall und Benutzer `wamo` (Claude Code, ECC, REA, gh, Vercel, Supabase-CLI) | Sokrates-2 |
| `scripts/` | Start der Desktop-App, Datenbankzugang, Nutzerverwaltung, Migrationen, Versionsskript `wamo_version.py` | PC |

## Stand

### Testlabor 28.11.2026 (Betriebsunterlagen, Stand 13.11.2026)

Für den Labortag liegen drei Betriebsunterlagen vor: das Runbook für die Betreuer `docs/labor-runbook.md`, die Checkliste für Freitag und Samstag `docs/labor-morgencheckliste.md`
und das Nachlauf-Skript `scripts/wamo_labor_nachlauf.py` (Sperren, Löschen, Nachweis). Dazu kommen der Restplan mit Terminen und offenen Entscheidungen `docs/labor-restplan.md` und der
Kartensatz zum Ausdrucken `docs/labor-kartensatz.html` (17 Karten). Konzept: `docs/testlabor-wamo.md`, Betrieb des Dienstes: `infra/sokrates2/README.md` (Abschnitt 19).

### Stand 11.11.2026

Entwicklungsaufträge im Betrieb (Zweig `feat/entwicklung-im-betrieb`, Basis `2a90999`): vorbereitet, noch nicht gemergt und nicht
ausgerollt, alles standardmäßig aus. Der Kern auf Sokrates-2 soll Aufträge selbst starten dürfen, über einen eigenen Kern-Kanal des
Dispatchers (Version 7) mit eigenem SSH-Schlüssel. Konzept: `docs/entwicklung-im-betrieb.md`, Schnittstellen und Pakete:
`docs/entwicklung-im-betrieb-arbeitsliste.md`, Server: `infra/sokrates2/README.md` (Abschnitt 18).

- Kern: eigener Schalter `WAMO_ENTWICKLUNG_BUERO` (der alte `WAMO_ENTWICKLUNG` zählt im Büro nicht) mit Profilbindung
  (`WAMO_ENTWICKLUNG_PROFILE`) und Pflicht-Kostengrenzen je Auftrag und je Tag (`WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD`,
  `WAMO_ENTWICKLUNG_KOSTEN_TAG_USD`). Zulassung nur für Admins, Repo-Pflicht für Arten mit GitHub-Stufe, Quittung `auftrag_quittung`
  mit Grund, Audit über die Tabelle `actions`, Grund der abgeschalteten Entwicklung für Admins in der Statusleiste.
- Dispatcher 7: Der Kanal (`lokal`, `pc`, `kern`) wird am benutzten Schlüssel erkannt. Im Kern-Kanal gelten Befehlsliste, Regeln
  aus `/etc/wamo/kernkanal.conf`, Projektnamen `kern-<Name>`, Fork-Prüfung, Grenzen, Ablauf und Notaus. Abweisungen kommen als
  Ereignis `kanal_gesperrt` mit festem Code.
- Skripte: `wamo-job-kern-einrichten.sh`, `wamo-job-kern-rueckweg.sh` und der Rauchtest `wamo_job_kern_rauchtest.py`. Im
  Slot-Betrieb startet kein Einrichtungsskript die alte Unit `wamo-core.service` mehr neu, sie nennen `umschalten-kern`.
- Prüfung: Der Vertragstest `voice-core/test_kernkanal_vertrag.py` prüft Kern und Dispatcher gegeneinander (Grund-Codes,
  Projektnamen, Release-Nachweis `SCHRANKEN_VERSION`, Standardwerte, ssh-Optionen). Keine Migration, `SELF_REPO` unverändert,
  `wamo-core-slots.sh`, `wamo-core-vorpruefung.sh` und `wamo-core@.service` unverändert.

Offen: der Pull Request mit Prüfstrecke (Merge erst nach Nikos Ja), der Server-Rollout in Stufen (Arbeitsliste, Abschnitt 5) und
die Schritte von Niko (eigener Claude-Zugang in der Karte Modellzugang, GitHub-App und Testrepo, Token widerrufen, Branch-Schutz,
ausdrückliches Ja vor den Stufen 4 und 5). Der Rauchtest und alle Proben sind nur am Server prüfbar und dort noch nicht gelaufen.

### Stand 10.11.2026

Ausgerollt ist Version 0.1.0 (Tag `v0.1.0`), `main` steht bei Commit `3185349` (PR 69 und 70). Am 09.11.2026 kamen fünf Pakete dazu:

1. Aufgabenleiste „Vorgänge“ mit Livefortschritt (Abschnitt „Vorgänge: Aufgabenleiste mit Livefortschritt“).
2. Reiter „Büro“ mit der Radialkarte der Bürogeräte (Abschnitt „Büro-Karte (Bürogeräte im Überblick)“).
3. Gerätedienst: SNMPv3-Lesetreiber, passive Erkennung unbekannter Geräte, Router-Status wahlweise über TR-064 (Abschnitt „Geräte-Lücken schließen (Paket 3)“).
4. Prüfbares Ziel mit Runden für Entwicklungsaufträge, `WAMO_ABNAHME_BEFEHLE` Standard aus, `WAMO_PERSONA_ABNAHME` Standard an (Abschnitt „Abnahme: prüfbares Ziel mit Runden“).
5. Lange Werkzeugausgaben kürzen ab `WAMO_AUSGABE_SCHWELLE` (Standard 3000 Zeichen), Nachladen mit `ausgabe_nachladen` (Abschnitt „Lange Werkzeugausgaben kürzen“).

Betrieb: Kern über die Slots a und b (`infra/sokrates2/wamo-core-slots.sh`), Oberfläche als `wamo-ui-blau` und `wamo-ui-gruen`, Gerätedienst
`wamo-geraete`, Migration 0017 (Tabelle `vorgaenge`) auf `wamo_dev` und `wamo_test` angewendet.

Offen:

- TR-064-Zugang der FRITZ!Box für die Erkennung unbekannter Geräte (ohne ihn zeigt die Büro-Karte keine Zahl) und Pflege von `/etc/wamo-geraete/bekannt.toml`.
- Sprachmessung der Persona-Schalter (`WAMO_PERSONA_ABNAHME` ungemessen, `WAMO_PERSONA_GESPRAECH` und `WAMO_WARTESATZ_MODELL` bis zur Messung aus).
- Befehlskriterien auf Sokrates-2 über den Dispatcher-Befehl `pruefen`: Dispatcher-Seite und Kern-Seite sind gebaut (`infra/sokrates2/wamo_job_dispatch.py`, `voice-core/abnahme_remote.py`), standardmäßig aus und nicht ausgerollt, noch ohne Rauchtest auf dem Server (Abschnitt „Abnahme: prüfbares Ziel mit Runden“); Router schreibend nur als Entwurf (`geraete/README.md`).
- Die Härtung der Desktop-App läuft in einem eigenen Pull Request und ist in diesem Stand nicht enthalten.
- Die Einträge unter „Offen (Stand 20.10.2026)“ gelten weiter, soweit dort nicht als überholt markiert; 0010, 0011 und 0013 auf `wamo_prod` sind im Repo nicht als angewendet belegt.

### Fertig und geprüft (Stand 20.10.2026)

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

### Offen (Stand 20.10.2026, teilweise überholt, siehe Stand 10.11.2026)

- Selbstüberarbeitung, Stufe 0 (Konzept `docs/selbstueberarbeitung.md`): Migration 0013 (Freigaben und Rollen: Tabellen
  `release_anfragen`, `release_freigaben`, `release_ereignisse`, `release_stand`, Rollen `wamo_release` und `wamo_anker`,
  Prüfsumme `sha256` in `schema_migrations`) liegt mit Rückweg und SQL-Test bereit. ~~Sie ist aber noch auf keinem Schema
  angewendet.~~ **Überholt für `wamo_dev` und `wamo_test`:** Dort ist 0013 angewendet (Beleg: Migration 0016 bricht ohne 0013 ab und ist
  laut `CHANGELOG.md` auf beiden Schemas angewendet). Offen bleibt `wamo_prod`, erst nach `database-reviewer` und Freigabe (zweimal
  anwenden, der zweite Lauf ändert nichts, danach `supabase/tests/0013_release_test.sql`, letzte Zeile „0013 OK“):
  `bash scripts/render-migration.sh <schema> supabase/migrations/0013_release_freigaben.sql | ssh sokrates2 "docker exec -i wamo-supabase-db psql -U postgres -v ON_ERROR_STOP=1 --single-transaction"`.
  Die Rollen sind NOLOGIN; ~~Login und DSN richtet erst Schritt 0.5 ein.~~ **Überholt:** Die Login-Rollen `wamo_release_s2` und
  `wamo_anker_s2` richtet `scripts/wamo_dienst_db.py` ein. Version und Changelog: `python scripts/wamo_version.py`
  (`vorschlag`, `changelog`, `anwenden X.Y.Z --trocken`, `pruefen`) leitet die Version aus Conventional Commits ab und hält alle
  Versionsdateien samt `CHANGELOG.md` gleich; ~~das erste Tag `v0.1.0` setzt Niko von Hand~~ **überholt:** Das Tag `v0.1.0` besteht
  (Release 0.1.0 vom 22.10.2026 im `CHANGELOG.md`); das Skript setzt weiterhin kein Tag und macht keinen Push.

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
  nichts wird freigegeben), unterwegs wirkt alles nach dem nächsten Start der Desktop-App. *Hinweis, siehe Stand 10.11.2026:* Die
  Kern-Umschaltung über Slots wurde am 02.11.2026 live geübt (`docs/selbstueberarbeitung.md`, Schritt 2.4), und der Fernzugriff wurde
  am 04.11.2026 am Laptop getestet (`CHANGELOG.md`, Abschnitt „Behoben“);
  wie weit die GoTrue-Sitzungsgrenzen und der Freigabe-Schlüssel im Büro angewendet sind, ist im Repo nicht belegt, der Absatz bleibt deshalb stehen.

- Soniox: Der Schlüssel gehört seit 02.11.2026 zu einem EU-Projekt. Erkennung und Sprachausgabe laufen über `stt-rt.eu.soniox.com` und
  `tts-rt.eu.soniox.com` (Standard in `voice-core/bot.py`). Die US-Adressen lehnen EU-Schlüssel mit 401 ab, die Region gilt je Projekt.
- Weitere PCs: `infra/sokrates2/wamo-ca.crt` importieren (ein `certutil`-Befehl, siehe `infra/sokrates2/README.md`,
  Abschnitt 5.2 und die Anleitung für den Browser in Abschnitt 12.3). Seit 19.10.2026 gilt Stufe 2: HTTP 55321 ist im LAN geschlossen, nur noch HTTPS 55443.
- Zugangsschlüssel auf Sokrates-2 enger fassen (Repo-gebundene GitHub-Token, Lese-Token, Jira-Konto nur lesend),
  bevor Entwicklungsaufträge eingeschaltet werden. *Stand 11.11.2026:* Die Auflage bleibt, ist aber nach Stufen geordnet
  (`docs/entwicklung-im-betrieb.md`, Abschnitt 5): Eine Recherche ohne GitHub (Stufe 3a) braucht keinen GitHub-Zugang. Arten mit
  GitHub-Stufe starten erst, wenn die GitHub-App ohne das WAMO-Repo eingerichtet und `GH_TOKEN` samt `GH_TOKEN_LESEN` gelöscht und
  widerrufen sind (Server-Schritt S8, Schritte N1 bis N3 von Niko). Jira und Vercel bleiben gesperrt, bis ein Jira-Lesekonto und ein
  Vercel-Token nur für Vorschauen bereitstehen (vor Stufe 5).
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

### Oberfläche: Kommandozentrale, Darstellungen, Hellmodus und Schriften

Die Hauptansicht ist eine Kommandozentrale im WMC-Corporate-Design (`ui/src/components/kommando/`). Links steht der Block „Gespräch“
(Stimmlinie, Legende, letzte Zeilen von Person und W.A.I.S.) mit den Karten Gedächtnis, geteilte Arbeit und Einstellungen, in der Mitte der
Kern als Lichtkugel in HUD-Ringen mit Zustandsschild, Untertiteln und Eingabepille, rechts die Reiter Aufträge, Vorgänge, Erledigt und
Büro. Neben dem Kern zeigt die Aufgabenanzeige den laufenden Auftrag mit Schritten und Fortschritt, solange W.A.I.S. handelt. Der Verlauf ist
eine Schublade, die Einstellungen sind eine zweite Ansicht derselben Seite (Hash `#einstellungen`).

- **Darstellungen:** Normal, Fokus, Zentrale und Nacht (Umschalter über dem Kern, auch in den Einstellungen). Nacht erzwingt dunkel.
- **Hellmodus:** Dunkel ist der Standard, Hell wählbar (Sonne und Mond in der Kopfzeile und Block „Helligkeit“ in den Einstellungen,
  Wahl je Gerät im `localStorage`). Beide Themen sind Rollenvariablen in `ui/src/app/globals.css` (`:root` und
  `:root[data-theme="light"]`), nirgends stehen feste Farben in Komponenten.
- **Farben:** WMC-Rot `#F40E0E` als Hauptfarbe für Flächen, Linien, Glut und große Schrift; kleine rote Schrift nimmt `--core-schrift`
  (im Hellmodus dunkler). Dazu Grautöne, kein Blau. Bedienelemente (Knöpfe, Felder, Schalter, Chips) haben mit `--steuer-rand` einen
  Rand mit mindestens 3:1, Glas- und Kartenkanten (`--kante`) bleiben zart. Der Test `ui/src/lib/knopfKontrast.test.ts` prüft die
  Kontraste beider Themen und durchsucht alle Stylesheets.
- **Schriften** (lokal, OFL, `ui/src/app/fonts`): Unbounded für Wortmarke, Titel und große Zahlen, JetBrains Mono für Beschriftungen in
  Versalien und Zähler, Atkinson Hyperlegible für Fließtext.
- **Kopfzeile:** Die Statusmodule stehen nach Rangfolge in einer Zeile (Büro, Modelle, PC, Mikro, Stimme, dann PC-Steuerung,
  Entwicklung, Netz). Was nicht passt, fällt als ganzes Modul weg, nie ein Wort; unter 1600 px zeigt der Menüknopf alle Module
  (`kommando/kopfzeile.css`, `lib/modellKurz.ts`).
- **Stimme:** Das Modul „Stimme“ zeigt Eigen, Fremd, Fremd · ignoriert (strenger Modus), Unklar oder Nicht eingelernt. Einlernen,
  Neu einlernen und Löschen des Stimmabdrucks gibt es in den Einstellungen (Abschnitt Stimme) und in der Einlernansicht in der Mitte
  (zuerst die Einwilligung). Für Laborkonten ist das alles unsichtbar.
- **Stylesheets:** `globals.css` hält Variablen und Grundregeln, `kommando.css` den Aufbau der Kommandozentrale, `hud.css` das Aussehen
  und die gemeinsamen Bausteine (Beschriftung, Abzeichen, Karte, Meldung, Knöpfe, Felder, Schalter), dazu je ein Teil-Stylesheet für
  Kopfzeile, Freigaben, Canvas, Verlauf, Anmeldung, Teilen, Einstellungen und weitere.
- **Vorschau ohne Server:** `NEXT_PUBLIC_WAMO_VORSCHAU=1 npx next dev` und die Modi im Kopf von `ui/src/components/Vorschau.tsx`.

### Komponententests (Oberfläche)

Neben den Logiktests der Oberfläche (`npm test`, `node --test`, nur reine Logik ohne DOM und ohne JSX) gibt es ein getrenntes System
für React-Komponenten und Hooks. Beide laufen im Ordner `ui/` und stören sich nicht.

| | Logiktests | Komponententests |
| --- | --- | --- |
| Befehl | `npm test` | `npm run test:komponenten` |
| Dateien | `src/**/*.test.ts` | `src/**/*.comp.test.tsx`, neben dem getesteten Code |
| Werkzeug | `node --test` | vitest mit jsdom, Testing Library (react, dom, user-event) |
| Konfiguration | keine | `ui/vitest.config.ts` (mit Begründung der Wahl), `ui/vitest.setup.ts` |

Die Pakete (vitest 4.1.12, vite 7.3.7, jsdom 30.1.3, `@testing-library/react`, `/dom` und `/user-event`, alle MIT-Lizenz) stehen nur
in den devDependencies, mit exakten Versionen. Sie gehören nicht zum Build: `next build` bündelt nur, was von `src/app` aus
erreichbar ist, der Typprüfer prüft die Testdateien aber mit. Voraussetzung für die Komponententests ist Node 22.22.2 oder neuer
(wegen jsdom 30); der Bau des Images braucht die Werkzeuge nicht und läuft auch mit älterem Node 22 durch, `npm ci` meldet dann nur eine
Warnung zur Engine-Angabe.

Abgedeckt sind die Tafel „Vorgänge“ (Karten je Zustand, `role="progressbar"` mit Wert und Text, „Zur Freigabe“ mit Fokusführung,
„Zuletzt beendet“ erst nach dem Öffnen), die Büro-Karte (Punkte als Buttons, Enter, Leertaste, Escape, Zustand als Wort und Form,
Leerzustand, Hinweis auf unbekannte Geräte), der Hook `useBueroStand` (Abruf nur bei sichtbarer Seite, Takt von 30 Sekunden,
Stopp bei 401 und 403, wachsender Abstand bei 503, Aufräumen) und der Kopierknopf des Canvas (Chip „Kopiert“ mit `aria-live`).

Grenzen, damit niemand mehr in die Tests liest, als darin steht:

- jsdom ist kein Browser: Es gibt kein Layout und keine Farben. „Zustand nie nur über Farbe“ wird an Wort, Form (SVG) und
  Attributen im DOM geprüft, nicht an Pixeln. Scrollen und `prefers-reduced-motion` prüfen die Tests nur als Aufruf
  (`scrollIntoView` ist eine Attrappe), ebenso Animationen.
- Bildschirmleser selbst laufen nicht mit. Geprüft werden Rollen, zugängliche Namen und aria-Attribute.
- Netz, Anmeldung und Sprachverbindung sind Attrappen oder bleiben ungetestet (`kernApi`, Supabase, WebRTC).
- Dass ein Hook nach dem Aushängen keinen Zustand mehr setzt, zeigt React 19 nicht mehr als Warnung an. Der Test prüft es nur
  mittelbar (keine Zeitgeber, keine weiteren Abrufe, keine Fehlerausgabe).
- Ein Test mit echtem Browser (Tastatur, Fokus, Zoom, Bildschirmleser) bleibt ein eigener Schritt.

Neue Komponententests: Datei `NAME.comp.test.tsx` neben die Komponente legen, Zustand über den Store (`wamo.set`) oder eine
Attrappe (`vi.mock`) vorgeben, Zeitabläufe mit `vi.useFakeTimers`, Eingaben mit `user-event`.

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

#### Windows-Installer (Desktop-App ohne Repository)

Ein Laptop bekommt WAMO ohne Klonen und ohne `npm install`: `WAMO-Setup-<Version>.exe` (NSIS, pro Benutzer nach
`%LOCALAPPDATA%\Programs\WAMO`, ohne Adminrechte, Kennung `de.wamocon.wamo`, Symbol `desktop/assets/wamo.ico`). Gebaut wird nur lokal:

```powershell
cd desktop
npm ci
npm run dist -- --config.directories.output=D:\tmp\wamo-dist   # ohne Angabe: desktop\dist (gitignored)
node ..\scripts\wamo-dist-pruefen.cjs D:\tmp\wamo-dist\win-unpacked --liste   # Inhalt prüfen, optional --verboten=wort1,wort2
```

Was der Installer enthält (Größe etwa 117 MB, entpackt 340 MB): die App in `app.asar` (ohne Tests und Testdaten), in
`resources\` den Host (`host\*.py`, `pyproject.toml`, `uv.lock`, ohne `.venv` und Tests), die drei Tunnelskripte
(`scripts\wamo-tunnel*.ps1`), die WAMO-CA (`wamo-ca.crt`) und `uv.exe` mit Lizenztext. Kein Voice-Core, kein Oberflächen-Build,
keine `.env`.

- **Form der Installation** (`desktop/installation.js`): Nicht paketiert (Entwickler) gilt alles wie bisher aus dem Repository.
  Installiert gibt es nur die Betriebsart **Büro**: Die App lädt `https://192.168.178.136:55445`, die Adressen sind fest und nicht
  über die Umgebung änderbar, „Unterwegs“ steht im Tray ausgegraut („in dieser Installation nicht verfügbar“) und der Befehl der
  Oberfläche dazu bleibt wirkungslos. Einstellungen, Logs und Einzelinstanz-Sperre liegen unter `%APPDATA%\WAMO`
  (getrennt vom Entwicklermodus `%APPDATA%\wamo-desktop`).
- **Python-Umgebung des Hosts** (`desktop/host-umgebung.js`): entsteht beim ersten Start im Hintergrund und spätestens vor dem
  Einrichten des Tunnels mit `uv sync --locked --no-dev --no-build --managed-python --python 3.11` in `resources\host\.venv`, dort
  erwartet sie `scripts\wamo-tunnel.ps1`. Sicherheit: Jedes Paket kommt mit der Prüfsumme aus `host/uv.lock`, es wird nie
  Quelltext gebaut, der Interpreter ist ein von uv geladener (dessen Downloads laufen gegen eingebaute Prüfsummen), nichts wird
  aus `PYTHON*`- oder `UV_*`-Variablen übernommen. Cache und Interpreter liegen unter `%LOCALAPPDATA%\WAMO\uv-cache` und `\python`.
  Einmalig braucht der Laptop Netz zu PyPI und GitHub (etwa 25 Sekunden bis 1 Minute). Ohne Netz bleibt der Host aus, das
  Einrichten des Tunnels meldet es, der nächste Start versucht es erneut. `uv.exe` (0.13.1, MIT) bringt der Bau mit: Version,
  Adresse und SHA-256 stehen in `desktop/build/uv-pin.json`, `scripts/wamo-dist-vorbereiten.cjs` prüft die Prüfsumme vor dem
  Auspacken und bricht sonst ab (keine Downloads ohne Prüfsumme).
- **Vertrauen in die WAMO-CA** (`desktop/zertifikat.js`): Gewählt ist ein Vertrauen nur innerhalb der App statt
  `certutil -user -addstore Root`. Der Windows-Weg zeigt eine Sicherheitsabfrage, wirkt für alle Programme des Benutzers und
  bleibt nach dem Deinstallieren bestehen. Die App prüft stattdessen selbst (`session.setCertificateVerifyProc`): nur wenn
  Chromium genau „Aussteller unbekannt“ meldet, nur für die Namen aus der Namensbeschränkung der CA (und nur, wenn sie zur
  Oberfläche gehören), nur wenn die Kette per Signatur zur mitgelieferten CA mit festem SHA-256-Fingerabdruck führt (höchstens ein
  Zwischenzertifikat), das Serverzertifikat gültig, kein CA-Zertifikat, für Server erlaubt ist und den Namen nennt. Alles andere
  behält das Ergebnis von Chromium. Nie `--ignore-certificate-errors`. Anfragen der App aus dem Hauptprozess prüfen dieselbe Kette
  (`pruefeSocket`), weil das Node in Electron die IP-Namensbeschränkung der CA nicht lesen kann. Wechselt die CA, braucht jede
  Installation ein Update mit dem neuen Fingerabdruck (`WAMO_CA_SHA256`, der Test vergleicht ihn mit `infra/sokrates2/wamo-ca.crt`).
- **Electron-Fuses** (am gebauten Programm mit REA gelesen): `RunAsNode` aus, `EnableNodeOptionsEnvironmentVariable` aus,
  `EnableNodeCliInspectArguments` aus, `EnableCookieEncryption` an, `EnableEmbeddedAsarIntegrityValidation` an,
  `OnlyLoadAppFromAsar` an. `GrantFileProtocolExtraPrivileges` bleibt an, weil `start.html` und `overlay.html` per `file://` aus der
  asar geladen werden (mit „aus“ scheitert das am gebauten Programm mit `ERR_FILE_NOT_FOUND`).
- **Autostart**: wie bisher nur über „Mit Windows starten“ im Tray, der Installer legt keinen Autostart an. Die Deinstallation
  beendet und entfernt die geplante Aufgabe „WAMO Tunnel“ (`wamo-tunnel-entfernen.ps1`); Schlüssel und Token unter
  `%LOCALAPPDATA%\WAMO` und `%USERPROFILE%\.ssh` bleiben stehen, ein Admin widerruft das Gerät im Register.
- **Signierung (offen)**: Es gibt noch kein Codesignatur-Zertifikat. Der Installer und `WAMO.exe` sind unsigniert, Windows
  SmartScreen zeigt beim ersten Start eine Warnung („Weitere Informationen“, „Trotzdem ausführen“), und bei der Installation pro
  Benutzer könnte ein Prozess des Benutzers die `WAMO.exe` austauschen (die asar-Integrität schützt nur die asar). Später
  signieren: Zertifikat besorgen (OV oder besser EV, oder Azure Trusted Signing), Variablen `CSC_LINK` und `CSC_KEY_PASSWORD` (bzw.
  die Signierwerte von electron-builder) nur in der Umgebung des Baus setzen, nie committen; `npm run dist` signiert dann
  `WAMO.exe`, `uv.exe` und den Installer von selbst. Bis dahin die SHA-256 des Installers getrennt weitergeben.
- **Aktualisierung (offen)**: Die Version steht in `desktop/package.json`; ein neuer Installer über die alte Installation
  aktualisiert (feste GUID). Einen automatischen Updater gibt es in diesem Paket nicht (`publish` ist `null`). Das Audit-Protokoll
  des Hosts (`host\wamo_audit.jsonl`) liegt im Installationsordner und geht bei Update oder Deinstallation verloren; ein fester
  Ort per `WAMO_AUDIT_LOG` braucht eine Änderung in `scripts/wamo-tunnel.ps1`.
- **Electron 44.10.1** (exakt gepinnt, vorher 44.5.1): schließt den Hinweis von `npm audit` zu den Zwischenzertifikaten der
  Web-PKI (Fix ab 44.9.1). Die Linie 44.x hat keine neuere Version; die Notizen der Versionen 44.6 bis 44.10 nennen keine
  Breaking Changes, 44.9.0 behebt zusätzlich, dass `setCertificateVerifyProc` bei gleichzeitigen ersten Anfragen an einen Host
  mitunter nicht griff. Nach dem Anheben geprüft: `npm test` (116 grün), Installer gebaut, `wamo-dist-pruefen.cjs` ohne Funde,
  Fuses unverändert, Start der entpackten App ohne Absturz. Offene Funde von `npm audit` betreffen nur Bauwerkzeuge
  (electron-builder und Unterpakete), nicht die ausgelieferte App.

#### Texteingabe und Verbindungsfrist

Die Eingabepille der Kommandozentrale sendet getippten Text wie eine gesprochene Bitte (`useWamo.textSenden`, RTVI
`send-text`, Antwort mit Stimme; die Nachricht unterbricht eine laufende Antwort wie ein Einwurf). Das Feld ist nur
aktiv, wenn ein Gespräch läuft und der Kern Protokoll 2 oder neuer meldet (`textMoeglich`); sonst steht es ehrlich
gesperrt da. Ein Schlüssel (`sk-ant-…`, auch gesprochen geschrieben) wird schon im Browser entfernt (`ui/src/lib/textEingabe.ts`),
zusätzlich filtert ihn der Kern (`voice-core/schluessel_filter.py`). Getippter Text ersetzt nie eine Freigabe: Karten
beantwortet nur der Klick. Kommt nach dem Verbinden 20 Sekunden lang keine Betriebsmeldung (Büro-Kern ohne WireGuard),
bricht die Oberfläche ab und zeigt eine Meldung mit „Erneut verbinden“ (`ui/src/lib/verbindungsfrist.ts`); steht die
Mikrofonfrage des Browsers noch offen, wird weiter gewartet.

#### App auf dem iPhone

Die Oberfläche ist als App installierbar (`ui/src/app/manifest.ts`, Symbole aus `scripts/wamo_icons_erzeugen.py`, Abstände
für Notch und Home-Balken über `env(safe-area-inset-*)` in `globals.css`). Das Manifest enthält nur Namen, Farben und Symbole.
Einrichtung am iPhone (nur Betriebsart Büro, also im Büro-LAN oder über WireGuard; die PC-Steuerung bleibt der Desktop-App vorbehalten):

1. `infra/sokrates2/wamo-ca.crt` aufs iPhone bringen (AirDrop oder Mail an sich selbst) und öffnen: „Profil geladen“.
2. Einstellungen, Allgemein, VPN und Geräteverwaltung: das Profil „WAMO CA“ installieren.
3. Einstellungen, Allgemein, Info, Zertifikatsvertrauenseinstellungen: dem Zertifikat der WAMO-CA vollständig vertrauen (Schalter an).
   Ohne diesen Schritt gibt es weder Anmeldung noch WebRTC.
4. In Safari `https://192.168.178.136:55445` öffnen, anmelden (zweiter Faktor), dann Teilen, „Zum Home-Bildschirm“.

**Offen:** Das Mikrofon und WebRTC im Standalone-Modus von iOS sind nur an einem echten Gerät prüfbar und noch nicht
geprüft. Bis dahin gilt die App-Installation nicht als Lösung; Hilfsweise bleibt Safari im normalen Tab. Symbole
neu erzeugen: `python scripts/wamo_icons_erzeugen.py` (Pillow nötig).

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
| Geräte und PC (`geraete`) | Verbindungen, Geräteverwaltung (Freigeben, Sperren, Widerrufen), PC-Steuerung samt Autostart (nur Admin, nur Desktop-App), Betriebsart, Stand der Geräteeinrichtung | alle, Geräteverwaltung und PC-Steuerung nur Admin |
| Teilen (`teilen`) | Geteilte Arbeit: mit mir, von mir, Dateien | alle |
| Info (`info`) | Version, Kern, Hinweise an Admins | alle, Hinweise nur Admin |

Der Abschnitt Verbrauch ist **Format ungeprüft**: Das Datenformat des Kerns (`voice-core/kontingent.py`) ist noch an keinem
echten Token bestätigt, die Seite sagt das dort auch (`ui/src/lib/verbrauch.ts`). In der Entwicklung zeigt
`/?vorschau=1&rolle=admin&kontingent=beispiel#einstellungen/verbrauch` Beispieldaten (nur `next dev` mit
`NEXT_PUBLIC_WAMO_VORSCHAU=1`).

Die geplante Aufgabe „WAMO Tunnel“ startet bei jeder Anmeldung `scripts\wamo-tunnel.ps1`: Host und Tunnel laufen damit
unabhängig von der Desktop-App (auch für den Browser). Die PC-Steuerung bleibt trotzdem je Sitzung aus, bis ein Admin
sie einschaltet. Logs: `%LOCALAPPDATA%\WAMO\logs`.

#### Vorgänge: Aufgabenleiste mit Livefortschritt

Die Tafel „Vorgänge“ (rechte Seite, am Handy der Reiter „Vorgänge“) zeigt, was WAMO gerade abarbeitet. Ein Vorgang ist eine
Boss-Aufgabe (`boss.py`) oder ein Entwicklungsauftrag (`dev_agent.py`, Schritte aus den Aufgabenwerkzeugen von Claude Code in
`vorgang_stream.py`). Der Kern führt sie in einer Registry je Sitzung (`voice-core/vorgaenge.py`) und schickt bei jeder Änderung
die Servernachricht

```json
{"type": "vorgang", "vorgang": {"id": "...", "titel": "...", "art": "aufgabe|entwicklung|geraet|sonstiges",
 "status": "geplant|laeuft|wartet_freigabe|pruefen|fertig|fehler|abgebrochen", "gestartet_ms": 0, "beendet_ms": null,
 "gesamt": 0.0, "restzeit_s": null, "schritte": [{"nr": 1, "titel": "...", "status": "...", "fortschritt": 0.0, "detail": null}],
 "kriterien": [{"text": "...", "erfuellt": true, "hinweis": null}], "runde": null, "max_runden": null,
 "freigabe_id": null, "ergebnis": {"canvas_id": "..."}}}
```

Jede Nachricht trägt den ganzen Stand; die Oberfläche ersetzt den Vorgang gleicher Kennung (`ui/src/lib/vorgaenge.ts`, Prüfung in
`nachrichten.ts`). Fortschritt wird auf vier Nachrichten je Sekunde und Vorgang gedrosselt, ein Statuswechsel geht sofort. Der
Gesamtfortschritt ist das Mittel der Schrittfortschritte; eine Restzeit gibt es erst ab 10 Prozent und nie ohne feste Zahl der Schritte,
sie wird nicht erfunden.

Je Vorgang zeigt eine Karte den Titel, den Status als Wort, den Gesamtbalken mit Prozent und Restzeit (bei fertig „Fertig in mm:ss“),
die nummerierten Schritte mit eigenem Balken (der laufende ist hervorgehoben), die Prüfkriterien (erfüllt, nicht erfüllt, noch offen),
„Runde n von m“ und zwei Knöpfe: „Zur Freigabe“ springt zur vorhandenen Freigabekarte (Tafel „Aufträge“, Attribut `data-approval`) und
fokussiert sie, „Ergebnis ansehen“ wählt das Element im Canvas, solange es dort liegt. Beendete Vorgänge stehen im einklappbaren
Abschnitt „Zuletzt beendet“. Alle Balken sind `role="progressbar"` mit `aria-valuenow` und `aria-valuetext`, der Zustand steht immer
als Wort da, `prefers-reduced-motion` schaltet die Bewegung ab, die Ansage für Bildschirmleser kommt einmal aus dem Zustand
(`ansage`). Die Zeitleiste „Werkzeuge“ darunter ist unverändert.

Verlauf: Beim Start fragt die Tafel einmal `GET /wamo/api/vorgaenge` ab (`voice-core/vorgaenge_api.py`, die letzten 20 Vorgänge des
eigenen Profils, Token mit zweitem Faktor wie bei allen Schnittstellen unter `/wamo/api`). Die Antwort läuft durch dieselbe Prüfung wie
eine Nachricht. Ein Vorgang ohne Ende gilt im Verlauf als abgebrochen, ein Vorgang der laufenden Sitzung geht dem Verlauf immer vor, und
ein Fehler bei der Abfrage zeigt einfach keinen Verlauf. Gespeichert wird nur bei einem Statuswechsel (Tabelle `vorgaenge`).

**Migration 0017** (`supabase/migrations/0017_vorgaenge.sql`, Rückweg `.down.sql`, SQL-Test unter `supabase/tests/`) ist seit dem
09.11.2026 auf `wamo_dev` und `wamo_test` angewendet (zweiter Lauf ohne Änderung, SQL-Test „0017 OK“); ein `wamo_prod` gibt es nicht.
Ohne die Tabelle (zum Beispiel in einem neuen Schema) speichert der Kern nichts (still, mit einem Hinweis im Protokoll) und der Verlauf
bleibt leer; die Anzeige der laufenden Sitzung funktioniert auch ohne. In der Entwicklung zeigt `/?vorschau=1&vorgaenge=1` fünf Beispielvorgänge (nur
`next dev` mit `NEXT_PUBLIC_WAMO_VORSCHAU=1`). Nicht an einem echten Lauf geprüft: Die Schritte aus dem Stream von Claude Code
sind nur mit aufgezeichneten Streams getestet (`voice-core/testdaten/vorgang_stream.jsonl`).

### Geräte, Tunnel je Gerät und Geräteverwaltung

Jeder persönliche Laptop hat einen eigenen Rücktunnel zu Sokrates-2: ein Gerät, ein ed25519-Schlüssel, ein Tunnelplatz
(Benutzer `wamo-t-NN`, Ordner `/run/wamo-pc/tNN/`, NN von 01 bis 64). Die Desktop-App bindet den Laptop bei der Anmeldung
(`POST /wamo/api/geraet/binden`, Migration 0002). Ein Mitglied ohne früheres Gerät wird dabei sofort `aktiv`; jedes Admin-Profil
und jeder Gerätewechsel steht danach auf `beantragt`, bis ein Admin entscheidet. Status: `beantragt`, `aktiv`, `gesperrt`,
`widerrufen` (endgültig). Nur `aktiv` zählt für den Abgleich der SSH-Schlüssel (`pc_abgleich_liste`).

**Geräteverwaltung** (Einstellungen, Geräte und PC, Karte „Geräteverwaltung“, nur Admin): Das Entscheiden geschieht in der
Oberfläche, nicht mehr in der Datenbank. Wartende Geräte stehen oben mit Kennung, Rechnername, Profil und dem Fingerabdruck in
Vierergruppen; darunter die aktiven und gesperrten Geräte, widerrufene (letzte 90 Tage) eingeklappt. Der eigene Laptop trägt die
Marke „Dieses Gerät“. Die Status zeigen Wort und Form (Raute wartet, Kreis aktiv, Quadrat gesperrt, Kreuz widerrufen).

- **Aktionen:** freigeben (beantragt zu aktiv), ablehnen oder widerrufen (endgültig, Grund Pflicht), sperren (Grund Pflicht),
  entsperren. Jede Aktion geht durch einen Bestätigungsdialog mit dem Fingerabdruck und dem Hinweis „Vergleiche ihn mit dem
  Fingerabdruck am Laptop“; Freigeben und Entsperren verlangen das ausdrückliche Bestätigen des Vergleichs. Ein Admin darf sein
  eigenes Gerät freigeben (sonst käme ein einzelner Admin nie an sein erstes Gerät), nur mit derselben Anzeige und einer Warnung.
- **Frischer zweiter Faktor:** Der Dialog fragt bei jeder Aktion den Code der Authenticator-App ab (`mfa.challengeAndVerify`).
  Der Kern nimmt ändernde Aktionen nur an, wenn der letzte `totp`-Zeitstempel im Claim `amr` höchstens 120 Sekunden alt ist
  (`voice-core/geraete_admin_api.py`); die Datenbank prüft dieselbe Frist noch einmal.
- **Kern-API** (`/wamo/api`, wie alle Routen mit Token, aal2 und aktivem Profil, zusätzlich Rolle admin): `GET /geraete/verwaltung`
  (Liste ohne Schlüsselmaterial, mit Verlauf der letzten zehn Ereignisse) und `POST /geraete/verwaltung/aktion`
  (`kennung`, `aktion`, `grund`, `fingerabdruck`). Grenzen je Admin: Liste 60 je Minute, Aktionen 10 je 10 Minuten. Eine doppelte
  Aktion (zwei Admins gleichzeitig, Doppelklick) ist folgenlos und wird als „schon erledigt“ gemeldet.
- **Datenbank (Migration 0014):** `wamo_core` bekommt kein Update-Recht auf `devices`, sondern nur die Ausführung zweier Funktionen,
  `geraet_entscheiden` (feste Übergänge, Akteur muss aktiver Admin sein, Zeile gesperrt, Verlauf über die Trigger aus 0002) und
  `geraete_verwaltung_liste`. Die Trigger aus 0002 bleiben maßgeblich. Prüfung: `supabase/tests/0014_geraete_verwaltung_test.sql`.
- **Grenze der Prüfung:** Die Datenbank sieht kein Token. Rolle, Anmeldung und die Frische des Codes prüft der Kern; die Datenbank
  prüft Akteur und Zeit erneut, kann sie aber nicht beglaubigen. Ein übernommener Kern könnte beides selbst setzen. Dagegen
  melden sich jede Freigabe und jedes Entsperren mit Kennung, Fingerabdruck und Akteur bei allen Admins (Hinweis `system`),
  und der Verlauf in `device_events` hält jede Änderung fest. Die Frist von 120 Sekunden gilt für ein Zeitfenster, nicht für
  einen einzelnen Code. Noch nicht an einem echten GoTrue erprobt: dass ein erneutes `challengeAndVerify` den Zeitstempel im Claim
  `amr` erneuert (die Oberfläche prüft das nach dem Bestätigen und meldet es sonst).
- **Hinweise:** Die Texte der Migration 0002 verweisen auf ein Skript `wamo_geraete.py`, das es nicht gibt. Die Datenbank bleibt
  unverändert (Migrationen sind nach dem Anwenden unveränderlich); die Oberfläche zeigt stattdessen den Weg über die Karte
  und bietet an den Hinweisen für Admins den Knopf „Zur Geräteverwaltung“.
- **Gemeinsame Geräte** (Drucker, Scanner und Ähnliches) und Notfälle ohne Oberfläche laufen weiter über `wamo_admin` (SQL).

### Büro-Karte (Bürogeräte im Überblick)

Der Reiter „Büro“ der Kommandozentrale zeigt alle gemeinsamen Bürogeräte aus dem Gerätedienst (`geraete/README.md`) als Radialansicht:
in der Mitte WAMO (Sokrates-2), je Gerätegruppe ein Ring (von innen: Server, Drucker und Scanner, Netz, Speicher, Medien,
Steckdosen), je Gerät ein Punkt. Weiß und gefüllt heißt in Ordnung, rot mit Strich und langsam pulsierend heißt Störung, grau und
hohl heißt unbekannt oder nicht eingerichtet; der Zustand steht nie nur in der Farbe, sondern auch in Form und Wort. Ein Klick oder
Enter auf einen Punkt öffnet eine Detailkarte mit Name, Zustand in Klartext (zum Beispiel „bereit, Toner 80 Prozent“, „Internet
verbunden, 86 Mbit runter“) und einem Sprechtipp („Sag: Schalte den Fernseher aus“). Admins finden in der Detailkarte außerdem Knöpfe
zum Schalten (Fernseher: Einschalten, Ausschalten, Lauter, Leiser, Stumm, Ton an; Steckdosen: Ein und Aus, Ausschalten mit zweitem Tipp;
Lautsprecher: Lauter, Leiser, Stumm, Ton an), nur das, was zum Zustand passt (ein Fernseher im Standby bietet nur „Einschalten“). Für alle
anderen bleibt es bei der Ansicht und der Sprache. Unter der Zeichnung steht dieselbe Auskunft als Liste (für Screenreader und zum Lesen); jeder Punkt ist ein
Button mit Tastaturbedienung, das Pulsieren entfällt bei reduzierter Bewegung. Die Karte läuft auf dem Handy (ab 360 px) wie am PC.

- **Kern-API:** `GET /wamo/api/buero/status` (`voice-core/buero_api.py`, Token, aal2 und aktives Profil wie bei allen Routen, jede Rolle).
  Der Kern ruft den Gerätedienst mit der Identität des Profils (`status`, `geraet = "alle"`, und `unbekannt_zaehlen`; deren
  Zwischenspeicher von 20 und 60 Sekunden bleiben wirksam) und kürzt auf ein schmales Modell je Gerät: `id`, `name`, `gruppe`,
  `ampel` (`ok`, `stoerung`, `unbekannt`), `text` (höchstens 80 Zeichen), `tipp` und optional `kennzahlen` (nur Zahlen mit festen
  Namen). Dazu `unbekannt` (Anzahl der Geräte im Netz, die WAMO nicht kennt, nur die Zahl; `null`, wenn es keine brauchbare Zahl
  gibt, die Karte bleibt dann ganz) und `stand`. Adressen, Seriennummern, Meldungstexte der Geräte und die Geräteliste des Routers (nur Admin)
  gibt die Route nie aus; der Klartext entsteht aus eigenen Wörtern, nur der Name kommt vom Gerät und wird gesäubert und begrenzt.
  Grenze: 6 Abrufe je Minute und Profil. Ohne Gerätedienst (zum Beispiel unterwegs) antwortet die Route mit 503, die Karte zeigt dann
  ruhig „Gerätedienst nicht erreichbar“.
- **Schalten:** `POST /wamo/api/buero/steuern` mit `{"geraet": "<kennung>", "aktion": "an|aus|lauter|leiser|stumm|laut"}`, nur Admins
  (403 sonst), nur diese sechs Aktionen (Lautstärke als Zahl und Eingang gibt es hier nie), höchstens 20 Schaltvorgänge je Minute und
  Profil. Der Kern ruft den Gerätedienst (`steuern`) mit der Identität des Profils; der Dienst prüft Rechte und Lautstärkegrenzen. Der
  Knopf ist eine ausdrückliche Handlung, deshalb gibt es keine Freigabekarte. Jeder Versuch steht in `actions` (Werkzeug
  `buerogeraet_steuern`, Grund „Bürogerät (Knopf in der Oberfläche)“). Fehler sind ruhige Sätze ohne Gerätetexte (409, 429, 502, 503).
  Im Stand (`GET`) tragen steuerbare Geräte für Admins das Feld `aktionen`. Seit 12.11.2026 hängen außerdem die Bürogeräte-Werkzeuge
  (`geraete_werkzeuge.py`) in jeder Büro-Sitzung (vorher waren sie gebaut, aber in `bot.py` nicht eingehängt): WAMO kann Geräte per Sprache
  und Text steuern, Freigabekarten tragen den Job `geraet`.
- **Oberfläche:** `ui/src/components/kommando/BueroKarte.tsx` (SVG ohne neue Abhängigkeit), reine Logik in `bueroKarteModell.ts`,
  Abruf in `useBueroStand.ts`: alle 30 Sekunden und nur, solange der Reiter sichtbar und die Seite nicht verborgen ist.
- **Vorschau ohne Gerätedienst:** `npm run dev` mit `NEXT_PUBLIC_WAMO_VORSCHAU=1`, dann `/?vorschau=1&buero=beispiel` (weitere Werte:
  `stoerung`, `alles`, `leer`, `offline`, `fehler`) und den Reiter „Büro“ öffnen.
- **Tests:** `cd voice-core; uv run pytest -q test_buero_api.py` und `cd ui; npm test` (`bueroKarteModell.test.ts`).

### Geräte-Lücken schließen (Paket 3)

Der Gerätedienst (`geraete/README.md`) kann drei Dinge mehr, alle nur lesend:

- **SNMPv3 für Switches und Access Points** (Treiber `snmp`): Name, Laufzeit, Ports oben und unten, auf Wunsch Fehlerzähler. Nur
  authPriv mit SHA-2 und AES, eigener Lese-Benutzer, nie SNMPv1 oder v2c, nie Schreiben; Ausgang UDP 161 zu genau der eingetragenen
  Adresse. Derzeit ist kein Switch eingetragen, der Dienst meldet dann „nicht eingerichtet“.
- **Passive Erkennung unbekannter Geräte** ohne Scan: Der Dienst liest die Hostliste der FRITZ!Box (TR-064) und vergleicht sie mit
  den bekannten Geräten und der optionalen Datei `/etc/wamo-geraete/bekannt.toml` (Laptops, Handys). Die Büro-Karte zeigt bei
  Zahl größer 0 ruhig „n unbekannte Geräte im Netz“ (grau, kein Alarmrot); die Liste sieht nur ein Admin („Welche Geräte kenne ich
  nicht?“), als Tabelle auf dem Canvas. Namen und MAC-Adressen stehen nie in Audit oder Protokoll.
- **FRITZ!Box-Status über TR-064** als wählbare Quelle (`status_quelle = "tr064"`); Standard bleibt UPnP.

Der Router bleibt ausschließlich lesbar. Mögliche Schreibaktionen stehen nur als Entwurf („Router schreibend, nicht gebaut“) in
`geraete/README.md`. Gegen echte Geräte (Switch, FRITZ!Box) ist noch nichts getestet, das geschieht beim Ausrollen.

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
- **Bedienung ohne Mausübernahme** (`host/uia_control.py`, Windows UI Automation über `comtypes`, MIT, exakt gepinnt):
  `elemente_auflisten` (read) nummeriert die bedienbaren Elemente eines Fensters (Name, Typ, Zustand; höchstens 8 Ebenen
  und 120 Einträge; Passwortfelder ohne Wert und gesperrt), `element_klicken` (change, per Invoke-, Toggle- oder
  Auswahl-Muster), `element_text_setzen` (change, Value-Muster, ersetzt den Inhalt, nie in Passwortfelder und
  Adressleisten) und `element_lesen` (read). Das Fenster muss nicht vorne liegen, Maus und Fokus der Person bleiben
  unberührt. Davor stehen dieselben Wächter wie bei Maus und Tastatur: Zielfenster-Prüfung (keine Konsolen, Admin-,
  Anmelde-, Passwortfenster, nie die eigene Oberfläche), Sperr-Erkennung, Ruhen bei offener Freigabe, Rate-Grenze
  (60 je Minute und Werkzeug), Audit ohne Textinhalte. Klicks und Text in Mail- und Messenger-Fenstern sowie Klicks auf
  Elemente mit Namen wie Senden, Bezahlen, Löschen, Veröffentlichen gehen nur über die Freigabe-Karte (Elementname und
  Fenstertitel stehen darauf). Die Nummern gelten nur für die letzte Liste desselben Fensters, höchstens 3 Minuten, und
  verfallen, sobald sich der Aufbau ändert; vor jeder Aktion wird der Baum frisch gelesen und das Element
  wiedererkannt. Das Sprachmodell soll zuerst auflisten und dann klicken; `auf_bildschirm_klicken` und `maus_klicken`
  bleiben der Rückfall für Zeichenflächen. Weitere Host-Werkzeuge dieser Stufe: `ordner_anlegen` (change, nur unter
  Projekte, Dokumente, Desktop und Downloads im Benutzerordner, nie in AppData oder versteckten Ordnern; `system_info`
  nennt Rechnername, Benutzer-, Projekte-Ordner), das Vorholen von Fenstern ohne Alt-Druck mit Nachprüfung des Fokus
  (verweigert Windows es, meldet der Host das, statt „ist vorne“ zu behaupten) und 20 s Zeitgrenze für Lesebefehle.
- **Seiten im aktiven Tab laden** (31.10.2026, `host/wamo_host.py`, `host/uia_control.py`): `webseite_oeffnen` startet den
  Browser nicht mehr bei jedem Aufruf neu (das öffnete immer einen neuen Tab, in der Sitzung vom 31.10. zwei für eine
  Suche). Standard ist der AKTIVE Tab des obersten nutzbaren Fensters des gewünschten Browsers (`chrome` oder `edge`):
  Fenster nach vorn mit Nachprüfung des Fokus (kein Alt-Druck), Adressleiste per UI Automation setzen und fokussieren
  (kein Debug-Port), Kontrolle, dass sie genau die geprüfte Adresse zeigt, Sperre und Zielfenster erneut prüfen, eine
  zweite Kontrolle von Wert und Tastaturfokus unmittelbar vor dem Tastendruck, dann Enter per SendInput. Die Adresse
  durchläuft dieselbe Prüfung wie bisher (`validate_url`, nur http und https, keine privaten Ziele, Freigabe-Karte bei
  sehr langer Suchanfrage). `neuer_tab=true` gilt nur auf ausdrücklichen Wunsch und behält das alte Verhalten. Nie benutzt
  werden Fenster eines anderen Programms als gewünscht, die eigene Oberfläche, Anmelde-, Zahlungs-, Mail- und
  Messenger-Seiten. Gibt es kein nutzbares Fenster oder scheitert der Weg (Fokus verweigert, Adressleiste nicht
  bedienbar, Anzeige weicht ab, Fenster gewechselt), öffnet sich wie früher ein neuer Tab, und das Ergebnis sagt
  „im neuen Tab geöffnet, weil ...“; das Ergebnis nennt sonst Browser, Fenstertitel und Monitor („im aktiven Tab
  geladen“). Bei gesperrtem Bildschirm startet auch der Rückfall nie. Audit: `tab` (aktuell oder neu), `weg` (uia oder
  start) und bei einem Rückfall ein fester Code, nie ein Titel oder Seiteninhalt; die Duplikatwache unterscheidet beides.
  Wie jede Eingabe ruht der Weg über die Adressleiste bei offener Freigabe (Rate-Grenze 12 je Minute). Eine Suche auf einer
  offenen Seite macht WAMO per Such-URL im selben Tab oder über das Suchfeld (`elemente_auflisten`, `element_text_setzen`,
  Enter). Persona-Schalter `WAMO_PERSONA_TAB` (Standard an; `aus` bringt die alte Formulierung zurück). Zusätzlich gilt der
  Sperrbildschirm `LockApp.exe` vorne jetzt als gesperrt (er liegt auf dem Desktop „Default“, der Desktopname allein
  meldete „entsperrt“). Grenzen: Es wird nur das oberste nutzbare Fenster versucht, und ob die Seite danach wirklich
  geladen ist, prüft `browser_lesen`.
- **Browser lesen statt raten** (31.10.2026, `host/uia_control.py`, `host/wamo_host.py`): `browser_lesen` (read) liefert für
  das oberste oder benannte Fenster von Chrome, Edge, Firefox oder Brave Titel und Adresse des aktiven Tabs, die Titel
  der Tabs (höchstens 20), den Seitentext in Lesereihenfolge (Überschriften mit `#`, Absätze, Listen mit `-`, Links als
  `[Text]`, Schaltflächen und Eingabefelder mit Namen und Inhalt) und den Monitor des Fensters (Zählung wie
  `bildschirmfoto`; `fenster_auflisten` und `elemente_auflisten` nennen ihn ebenfalls). Der Text kommt in Abschnitten
  von etwa 6000 Zeichen (`abschnitt=2` liest weiter, `gekuerzt` und `abschnitte` sagen, ob es mehr gibt), standardmäßig
  nur der sichtbare Teil (`nur_sichtbar=false` für die ganze Seite). Ohne `fenster` gilt das oberste Browserfenster, auch
  wenn ein anderes Fenster vorne liegt, nie die eigene Oberfläche. Das Ergebnis trägt die Marke `vertrauen:
  unvertrauenswuerdig` samt Hinweis und Schlusszeile, und weil `browser_lesen` in `LLM_TOOLS` steht, färbt
  `gate_pc_tools` den Zug als Fremdinhalt (kein Lernen). Die Wächter sind dieselben wie bei den UIA-Werkzeugen (Sperr-
  Erkennung, Zielfenster-Prüfung, Rate-Grenze 60 je Minute), dazu: keine Passwort-, Anmelde-, Zugangsdaten- und
  Zahlungsseiten, Passwort-, Karten-, PIN- und IBAN-Felder nie mit Wert (auch unter harmlosem Namen, wenn der Wert wie eine
  Kartennummer aussieht), Adressen ohne Zugangsdaten, Fragment und geheim klingende Parameter, Tabs und Adressleiste nur
  aus der Browser-Oberfläche (eine Seite kann sie nicht vortäuschen), keine Cookies, kein Skript, kein Debugging-Port.
  Das Audit nennt Programm, Domain und Zahlen, nie Titel, Text oder Werte. Gemessen mit selbst gestarteten Instanzen
  (Chrome 155, Edge 156, eigenes Profil, ohne `--force-renderer-accessibility`): Chrome und Edge liefern die Seite nach
  dem ersten UIA-Zugriff von selbst; der erste Zugriff kann leer sein, deshalb wiederholt der Host bis zu 7 s (Chrome kalt
  0,5 bis 1,9 s, Edge kalt 5,9 s, danach 0,2 bis 0,8 s). Amazon-Suche: 2750 Knoten, 61 000 Zeichen in 11 Abschnitten,
  der sichtbare Teil 4200 Zeichen. Die Baumtiefe 8 reichte für Webseiten nie (das Dokument liegt bei Tiefe 7, der Inhalt
  bis Tiefe 14): `elemente_auflisten` liefert deshalb unter einem Webdokument bis zu 80 Ebenen, gelesen mit einem
  Zwischenspeicher (gemessen 0,25 s statt 3 s je Seite), und listet die Fensterknöpfe des Browsers (Schließen beendet alle
  Tabs) nicht mehr auf. `bildschirmfoto` hat zusätzlich `fenster` (nur dieses Fenster, auch auf einem anderen Monitor
  oder hinter einem anderen Fenster; was darüber liegt, ist mit auf dem Bild), `bildschirm_ansehen` reicht es durch.
  Reihenfolge für das Modell: erst `browser_lesen` (Text), dann `elemente_auflisten` zum Bedienen, ein Bild nur, wenn Text
  nicht reicht (Bilder, Diagramme, Layout). **Grenzen:** Firefox ist nicht gemessen (kein Firefox auf dem Testrechner;
  die Schicht sucht den Dokumentknoten über den Steuerelementtyp, UIA in Firefox ist dünner), die Absatzgrenzen im Text
  werden aus den Abständen auf dem Bildschirm geschätzt (Chrome und Edge liefern Absätze als lose Textstücke), Inhalte
  in Canvas, Bildern und PDF-Viewern fehlen (dafür bleibt `bildschirm_ansehen` mit `fenster`), eine ganz neue Seite liefert
  kurz nur den Rahmen, und die Barrierefreiheit des Browsers bleibt nach dem ersten Zugriff eingeschaltet (etwas mehr
  Speicher im Browser). Verdeckte oder minimierte Fenster gelten als „nicht sichtbar“: dann liefert `browser_lesen` die
  ganze Seite statt nichts.
- **GitHub im Host** (31.10.2026, `host/wamo_host.py`): `github_repo_anlegen` (Risikoklasse critical) legt ein Repository in
  der Organisation an, nie im privaten Konto: `WAMO_GITHUB_ORG` aus der Umgebung des Hosts, sonst `Wamocon`, geprüft
  gegen eine Allowlist-Regex. Es passiert nichts ohne die Freigabe-Karte (Organisation, Name, Sichtbarkeit und
  Beschreibung stehen im Detail, öffentlich ist rot hervorgehoben und nur mit ausdrücklichem Wunsch), genau einmal, mit
  Zeitgrenze 60 s, höchstens 5 Anfragen je Minute. Standard ist privat. `git_klonen` (change, ohne Karte) klont nur
  nach `Projekte\<name>` (Regeln wie `ordner_anlegen`), nur aus der Organisation und aus Konten in
  `WAMO_GITHUB_KLONEN_VON` (Liste mit Komma, Standard leer); liegt dort schon ein Klon mit demselben Remote, ist das
  ein Erfolg (`vorhanden`), ein anderes Remote im selben Ordner ist ein Fehler. Die Remote-Adresse wird als Text aus
  `.git/config` gelesen, in fremden Ordnern läuft kein git. Zeitgrenze 90 s (Tunnel: 100 s), 6 je Minute. Beide nutzen das
  `gh` des Laptops, also die Anmeldung der Person: Der Host hält kein Token und reicht `GH_TOKEN`/`GITHUB_TOKEN` aus
  seiner Umgebung nicht weiter. Ausgaben von gh und git erreichen das Modell nur als feste Sätze oder gefilterte, auf
  300 Zeichen gekürzte Fehlerzeile (Tokens und Passwörter in Adressen entfernt); das Audit nennt Repository und
  Sichtbarkeit, die Beschreibung nur als Prüfsumme. Beide ruhen bei gesperrtem Bildschirm und bei offener Freigabe.
  Nicht enthalten: Vercel, Supabase, Admin-Helfer.
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
- **Lichtrahmen und Klickwelle** (31.10.2026): Handelt WAMO am Laptop (Fenster nach vorne, Element klicken, Text setzen,
  Klicken, Scrollen, Tippen, Tasten, auch nach einer Freigabe), liegt ein roter Rahmen (`#F40E0E`) ums Zielfenster, bei
  Klicks läuft eine Welle vom Klickpunkt aus; nach etwa 1,5 s verschwindet er. Der Host liefert im Ergebnis nur ganze
  Zahlen mit Obergrenzen (`markierung`: `fenster`, `monitor`, bei Klicks `punkt`, Bildschirmpixel, Position
  minus 32768 bis 32767, Größe 1 bis 32768), nie Titel, Programm oder Inhalt, und nur nach gelungenen Aktionen: bei Sperre,
  offener Freigabe und Ablehnung fehlt sie. Der Kern prüft die Zahlen noch einmal (`voice-core/activity.py`), hängt die
  Gerätekennung des Laptops an (Tunnel je Gerät, sonst den Rechnernamen) und schickt `markierung` an die Oberfläche
  (`ui/src/lib/nachrichten.ts`), die sie bei gesperrtem Bildschirm oder offener Freigabe verwirft und sonst über
  `window.wamoDesktop.markierung` an die App gibt. `desktop/lichtrahmen.js` prüft erneut und zeichnet nur auf dem Laptop,
  dessen Kennung in `geraet.json` steht (ohne Kennung: gleicher Rechnername, ohne Namen nie); bei allen anderen Profilen und Geräten passiert nichts. Gezeichnet wird in `desktop/markierung.js` im
  Stimmen-Rand: nicht anklickbar, nicht in Bildschirmfotos für das Bildmodell (Inhaltsschutz der Rand-Fenster), aus bei Vollbild (`vollbild.js`) und bei ausgeschaltetem Rand-Haken, aus bei offener
  Freigabe und ohne Gespräch. Bei `prefers-reduced-motion` gibt es kein Ein- und Ausblenden und keine wachsende Welle,
  nur einen stillen Rahmen und einen ruhenden Ring. Das Sprachmodell sieht die Zahlen im Werkzeugergebnis, mehr nicht.
- **Live-Bild des eigenen Laptops** (31.10.2026, Paket E, Entscheidung des Nutzers mit Auflagen): Admin sieht den Bildschirm
  seines eigenen, gebundenen Laptops als Einzelbilder (JPEG, höchstens etwa 2 je Sekunde, 1280 Pixel Kante, höchstens
  110 KB) in einer Ansicht der Kommandozentrale (Chip „Live-Bild“, Karte im Canvas-Bereich) mit Monitorwahl (Reihenfolge von
  links, Hinweis bei nur einem Monitor), Lupe und Restzeit bis zum automatischen Ende. Erzwungen wird es an drei Stellen:
  *Am Laptop* (`desktop/livebild.js`, Tray-Menü „Live-Bild einschalten …“): Standard aus, nur die Person am Laptop schaltet
  ein, nach einem Einwilligungsdialog mit Inhalt, Zweck, Dauer und Datenschutz (Hinweis auf Kundendaten und fremde Chats);
  die Einwilligung gilt je Laptop und Besitzer und wird bei neuer Bindung neu verlangt. Der Schalter ist die Datei
  `%LOCALAPPDATA%\WAMO\live-bild.json` (`an`, `bis` höchstens 30 Minuten, Herzschlag alle 2 s, `sichtbar`); beim Start,
  Beenden, Ablauf und Wechsel der Bindung steht dort `aus`. Solange er an ist, zeigt jeder Monitor einen roten Rahmen mit
  Plakette (`livebild-hinweis.*`, bewusst nicht vor Bildschirmfotos geschützt, also in jedem Bild zu sehen) und das
  Tray-Symbol ist rot. *Im Host* (`host/live_bild.py`, Werkzeuge `bild_holen`, `live_bild_status`, `live_bild_beenden`, nie in
  `LLM_TOOLS`): Der Host prüft bei jedem Bild selbst Schalterdatei (Kennung und Besitzer wie `geraet.json`, Stufe `admin`,
  Einwilligung, Ablauf, Herzschlag höchstens 6 s alt, Hinweis sichtbar), zählt unabhängig davon die Hinweisfenster der App
  (eines je Monitor), und liefert nichts bei gesperrtem Bildschirm, offener Freigabe, Vollbild, Passwortfenster vorne oder
  einem Passwortmanager irgendwo sichtbar; ein Bild wird verworfen, wenn sich währenddessen etwas ändert. Höchstens ein
  Zuschauer (12 s Frist), 0,35 s Abstand, 150 Bilder je Minute. Befehle, die `live-bild` nennen, sind gesperrt, die Fenster der
  App (`wamo.exe`, `electron.exe`, Titel „WAMO …“) bedient der Host nie. *Im Kern* (`voice-core/bildstrom.py`): nur Admin mit
  gebundenem Gerät (Tunnel je Gerät), PC-Steuerung an und gehaltener Sperre; ein Bild nach dem anderen (die Oberfläche
  bestätigt jedes, ohne Bestätigung nach 10 s Ende), 15 Minuten Höchstdauer, Ende bei Sperre, Trennen, Abmelden, Ablösung,
  Entzug des Geräts und ausgeschalteter PC-Steuerung; WAMO sagt Anfang und Ende. Tunnelgrenzen: eigene Zählung
  (`BILD_AUFRUFE_JE_MINUTE = 200`), die 120 allgemeinen Aufrufe je Minute und 4 MB bleiben, Bildantworten höchstens 256 KB, Zeitgrenze 8 s.
  Weg der Bilder: Servernachricht `bild` der bestehenden, angemeldeten Sitzung (kein neuer Port, keine zweite Anmeldung,
  gebunden an Sitzung, Rolle und Gerät); der Rückstau durch die Bestätigung hält Untertitel und Gespräch im selben
  Datenkanal frei. Keine Aufzeichnung: Bilder liegen nur im Arbeitsspeicher (Host, Kern, Browser), nie in Datenbank,
  Gedächtnis, Verlauf, Audit, Log (`WAMO_LOG_LEVEL=TRACE` wird auf DEBUG begrenzt), Store oder Datei und nie bei einem Sprachmodell;
  das bestehende Werkzeug `bildschirmfoto` bleibt unverändert. Restrisiken: Passwortfelder in Fenstern, die nicht
  Passwortmanager sind, lassen sich per UI Automation nicht erkennen; ein von einem Admin auf einer Freigabekarte bestätigter,
  verschleierter PowerShell-Befehl könnte die Schalterdatei schreiben (der Host zählt dann trotzdem die Hinweisfenster);
  Fernklick und Fernsteuerung (Paket F) gibt es nicht. Noch offen: Probelauf mit der installierten App an einem echten Laptop
  (Hinweisfenster-Erkennung, Datenkanal mit Bildern von rund 150 KB) und eine Betriebsvereinbarung beziehungsweise Information der Beschäftigten.
- **Weicher Stopp**: Nach einer Unterbrechung blendet die Oberfläche WAMOs Stimme in der nächsten Wortpause aus,
  spätestens nach 600 ms (`ui/src/lib/weicherStopp.ts`, im 20-Hz-Takt mit dem selbst gemessenen Pegel). Mit
  `WAMO_STOPP_GRENZE` (Standard an) wählt der Kern die Wortgrenze selbst und schickt `leiser_stopp` mit `sofort`.
- **Innehalten statt Abbrechen** (14.11.2026, `voice-core/wiedergabe.py` und `voice-core/soft_interrupt.py`, Schalter `WAMO_INNEHALTEN`,
  Standard an, Messung steht aus). Ein einzelnes echtes Wort der Person, während WAMO spricht („Danke“, „Du“), lässt WAMO an der
  nächsten Wortgrenze innehalten, wie ein Mensch, der merkt, dass der andere ansetzt. Kommt nach `WAMO_INNEHALTEN_WEITER_SECS`
  (Standard 0,7) Sekunden Stille nichts nach, spricht WAMO genau dort weiter (frühestens 0,6 Sekunden nach dem Anhalten,
  `MIN_HALT_SECS`); höchstens `WAMO_INNEHALTEN_MAX_SECS` (Standard 2,5) Sekunden lang hält es inne. Zwei echte Wörter oder ein Stoppwort („Stopp“, „Warte“, „Moment“, „Nein“, „Abbrechen“) brechen ab
  wie bisher, Rückmeldelaute („mhm“, „ja genau“) halten WAMO nicht an, ein „Danke“ allein lässt nur innehalten. Ein einzelnes Wort,
  das WAMO eben selbst gesprochen hat, und erkanntes Echo lassen WAMO nie innehalten. Technik: Je Sitzung hängt ein `AusgabeHalt`
  in `write_audio_frame` des Tonausgangs (Pipecat 1.12 schreibt den Ton in Happen von 40 ms und wartet jeweils, bis die
  WebRTC-Spur den Happen abgeholt hat, die Stelle heißt in der installierten Version genauso und verhält sich gleich). Der Halt
  lässt den nächsten Happen warten (die Spur sendet Stille, nichts geht verloren) und macht an derselben Stelle weiter. Eine
  Pause dauert nie länger als 4 s (`MAX_PAUSE_SECS`), denn Pipecat hielte ein Schreiben ab 10 s für eine tote Verbindung. Der
  `PlaybackTracker` meldet ein Wort erst, wenn sein Ton wirklich hinausgeht (gemessen am geschriebenen Ton statt nach dem
  Zeitstempel, der dem Ton bis zu 0,5 s vorauseilte). Zusammenspiel mit dem übrigen Sprachverhalten: Während eines Halts gibt es
  weder `leiser_stopp` noch ein Dämpfen; eine schon gedämpfte Stimme nimmt das Gate beim Weitersprechen zurück (danach 8 s keine
  neue Dämpfung). Bestätigt die Erkennung einen Einwurf, steht der Ton schon an der Wortgrenze: Der Halt hält an, der Abbruch folgt
  sofort, ohne Ausblenden in der Oberfläche und ohne Wartezeit für den Vorsprung der Zeitstempel, und der Verlauf braucht kein
  Kürzen auf das Gehörte. Ohne Halt (`WAMO_INNEHALTEN=aus`, Sprachausgabe im Haus mit ganzen Sätzen ohne Wortzeiten, ein Ausgang
  ohne `write_audio_frame`) gilt alles wie vorher: Dämpfen, Stopp an der hörbaren Wortgrenze mit `leiser_stopp`, Zeitgrenze.
  Offen: `SOFT_INTERRUPT_LAG_SECS` und die Dauer von „Verstummen“ sind nur ohne Halt gemessen, mit Halt zu messen (Messszenario
  `ein_wort` für das Innehalten, `einwurf` für das Verstummen).
- **Stimme und Sprachverhalten** (22.10.2026, Messweg `scripts/wamo_stimme.py` und `scripts/wamo-stimme-messen.cjs`,
  Befunde und Zahlen: Abschnitt „Stimme messen“ unten). Schalter im Kern, jeder einzeln abschaltbar:
  `WAMO_STOPP_GRENZE` (Stopp an der ersten noch erreichbaren hörbaren Wortgrenze, Satz- oder Kommaende bis 0,3 s später
  geht vor, der Verlauf wird auf das Gehörte gekürzt), `WAMO_DUCKEN` (Stimme sofort leiser, sobald die Person spricht,
  Nachricht `stimme_leiser`; bei Rückmeldelaut oder Geräusch nach 0,3 s wieder normal und 8 s keine neue Dämpfung, bei
  Echo 30 s), `WAMO_RUECKMELDUNG` (erweiterte Erkennung von „mhm“, „mm-hmm“, „alles klar“, Wortanfänge wie „Gen“ vor
  „Genau“ zählen im Zwischenstand nicht), `WAMO_ECHO_MODUS` (hört WAMO sich erkennbar selbst, mindestens zwei
  Echo-Urteile in 20 s, unterbricht nur noch ein Stoppwort wie „Stopp“, „Moment“, „Was“, „Nein“; wirkt nur mit
  `WAMO_ECHO_FILTER`), `WAMO_WARTESATZ` und `WAMO_WARTESATZ_SECS` (kurzer Satz bei einem Werkzeugaufruf, der länger als
  0,9 s dauert und bei dem WAMO sonst schweigt, `voice-core/wartesatz.py`). Standard: alle an, nur `WAMO_WARTESATZ` aus
  (in der Messung sagte das Modell den Füllsatz selbst, der Wächter löste nie aus). Abschalten mit `aus`, `0`, `nein`;
  ein unbekannter Wert gilt als an und wird einmal gewarnt (`voice-core/env_schalter.py`). Auf Sokrates-2 stehen alle
  Namen in der Liste erlaubter Werte (`infra/sokrates2/wamo_core_env.py`).
- **Sprachpakete A und B** (31.10.2026; Standardwerte nach der Messung vom 31.10.2026, siehe Tabelle „Schalter und
  Standardwerte“ im Abschnitt „Stimme messen“: an sind `WAMO_PAUSEN_PROMPT`, `WAMO_RUECKMELDUNG_WERKZEUG` und das Lernen,
  aus bleiben `WAMO_TTS_SATZLEERZEICHEN`, `WAMO_PERSONA_RUECKFRAGEN`, `WAMO_PERSONA_PC_ORDNER` und `WAMO_JA_WAECHTER`,
  weil ihre Messung unvollständig ist). `WAMO_TTS_SATZLEERZEICHEN`
  (Leerzeichen am Ende jedes Textstücks für Soniox, damit das Satzzeichen nicht als „Punkt“ vorgelesen wird,
  `voice-core/soniox_tts.py`), `SONIOX_SPEED` (0,7 bis 1,3) und `SONIOX_REDUCE_SILENCE` (an/aus) für Tempo und Pausen
  (leer = Standard von Soniox; die Stimme selbst bleibt `SONIOX_VOICE`, die Wahl trifft die Hörprobe),
  `WAMO_PERSONA_RUECKFRAGEN` (höchstens eine Rückfrage je Auftrag, Aufträge am Stück, keine Anschlussfrage, Freigaben
  unberührt), `WAMO_PERSONA_PC_ORDNER` (Ordner des PCs nie raten, mit `system_info` erfragen), `WAMO_PAUSEN_PROMPT` (Texte
  nach einer Pause ohne „Ich bin noch da“; nach der kurzen Pause erledigen oder still warten, nach der langen genau eine
  Frage), `WAMO_LERNEN_OHNE_MOMENT` (Standard an: Lern-Prompt ohne Momentaufnahmen und Aufgabenstände),
  `WAMO_RUECKMELDUNG_WERKZEUG` (ein „mhm“ bei laufendem Werkzeug startet keinen Zug und bricht es nicht ab, Nachlauf 4 s;
  Stoppwörter und echte Einwürfe wirken weiter), `WAMO_JA_WAECHTER` (`voice-core/ja_waechter.py`: ein kurzes Ja auf eine
  echte Rückfrage führt den Auftrag aus; greift nie bei offener Freigabe-Karte, Freigabe-Meldung im Verlauf oder
  Rückfragen zu Freigabe und Sicherheit). Messszenario `werkzeug_mhm` (zwei Rückmeldelaute während der Websuche).
- **Fehlerbehebungen aus der Sitzung vom 31.10.2026** (Kern-Journal 12:43 bis 12:47, 15 Züge; alle Schalter Standard an, sie
  ändern nur Fehlerfälle). `WAMO_ABBRUCH_HINWEIS` (`voice-core/werkzeug_wache.py`): Bricht eine Äußerung ein laufendes Werkzeug
  ab, stand im Verlauf nur „CANCELLED“; das Modell hielt die Aktion für gescheitert oder vergaß den Auftrag (Bildschirm sehen
  12:45:14, Scrollen 12:45:41). Jetzt steht dort „ohne Ergebnis, Auftrag offen“ (Lesewerkzeuge erneut aufrufen, Aktionen erst
  prüfen). `WAMO_WERKZEUG_DOPPELT`: Dieselbe Öffnen-Aktion im selben Zug läuft nicht noch einmal (der Host fängt nur 6 s ab), eine
  weitere Seite derselben Webseite bekommt einen Hinweis im Ergebnis, ab der vierten wird nicht mehr geöffnet; ein neuer
  Beitrag der Person setzt zurück, gescheiterte Aufrufe zählen nicht. Die zwei Amazon-Suchen von 12:44:35 gehören zu zwei
  Antworten des Modells hintereinander, nicht zu zwei gleichzeitigen Aufrufen; ob die Person beides wollte, zeigt das Log ohne
  Inhalte nicht, deshalb wird nichts verboten. `WAMO_MARKER_FALLBACK` (`voice-core/marker_wache.py`): Fehlt der Satzende-Marker ●
  (12:44:36 nach der Werkzeugrunde), gab Pipecat den Text erst am Ende der Antwort frei; jetzt sofort, ohne Textverlust.
  `WAMO_LERNEN_HAUS_SECS` (Standard 15): Für Profile mit Claude-Zugang darf das Hausmodell für „lernen“ nur so lange brauchen
  (gemma4-31b lief zweimal in die 45 s), danach 300 s Pause und gleich Claude. Verzögerungen im Protokoll: Zug 9 und 11 (+3,1 und
  +3,7 s) sind ein ◐-Urteil nach dem Einwurf (erste Antwort 8 Token, 1,5 s Wartezeit, zweite Anfrage), Zug 14 (+2 s STT) und
  Zug 8, 10 sind zu früh beendete Redebeiträge („weitergesprochen“), Zug 13 (+1,1 s) ist die Smart-Turn-Wartezeit. Wirken
  erst mit den Messschaltern: `WAMO_RUECKMELDUNG_WERKZEUG` (Zug 10, Einwurf bei laufendem Werkzeug) und `WAMO_PAUSEN_PROMPT`
  (Zug 9, 11). Harmlos: „No screen video transceiver“ (die Oberfläche sendet keinen Bildschirm) und „Media stream error“
  beim Auflegen.
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

### Start beim Tippen (stiller Start, nur Oberfläche)

Der Schalter „Still starten beim Tippen“ (Einstellungen, „Sprache und Ton“; je Gerät und je Person, **Standard an seit 12.11.2026**, wer ihn
ausschaltet, behält „aus“; kein Server-Zustand, `localStorage`-Schlüssel `wamo.stillStart.<user_id>`, Wert `an` oder `aus`) sorgt dafür,
dass man „Gespräch starten“ nicht mehr klicken muss: Der erste
Tastendruck im Eingabefeld startet eine Sitzung ohne Mikrofon (der Medienmanager `ui/src/lib/stillMedien.ts` öffnet es erst bei „Sprechen“,
kein `getUserMedia`, keine Audiospur am Sender), die Wiedergabe bleibt stumm, Antworten kommen als Text (`audio_response: false`). Die erste
Zeile wird gehalten und genau einmal gesendet, sobald die Sitzung steht. Der Mikrofonknopf und „Gespräch starten“ machen aus der stillen
Sitzung in derselben Sitzung ein Gespräch mit Stimme. Die Logik steht in `ui/src/lib/stillStart.ts`.

- **Nie auf Laborplätzen:** Konten `labor-NN@wamo.test` und `labor-schau@wamo.test` starten nie von selbst (die Oberfläche kennt das
  Laborprofil vor dem Verbinden sonst nicht). Die Ablehnung im Kern selbst gibt es noch nicht, sie kommt mit KP-1C.
- **Leerlauf:** Eine per Tippen gestartete Sitzung endet nach 20 Minuten ohne Eingabe (Konstante `LEERLAUF_MS`), aber nie, solange die
  PC-Steuerung an ist, eine Karte offen ist oder ein Entwicklungsauftrag, ein Vorgang, das Live-Bild oder eine Antwort läuft. Eine mit
  „Gespräch starten“ begonnene Sitzung bleibt unverändert.
- **Verdrängung (Grenze):** Der Kern führt je Profil eine Sitzung, eine neue löst die laufende ab (`WAMO_SITZUNG_EINZELN`). Die
  Oberfläche erfährt vor dem Start nichts von einer Sitzung auf einem anderen Gerät, das Tippen kann sie deshalb dort verdrängen. Der
  Schalter gehört nur auf ein Gerät je Person (Hinweis in der Einstellung). Erkennbar ist nur der umgekehrte Fall: Hat eine neuere Sitzung
  die hier abgelöst, startet das Tippen nicht von selbst neu, sondern zeigt „W.A.I.S. läuft in einem anderen Fenster“ mit „Hier übernehmen“.
  Eine Abfrage im Kern, die das vorher zeigt, gehört in KP-1C.
- **Hinweis für Betreiber:** Eine still gestartete Sitzung ist für den Server eine ganz normale Sitzung. Bis zu ihrem Ende (spätestens 20
  Minuten nach der letzten Eingabe, bei PC-Steuerung oder offener Karte länger) zählt sie für die Ruheprüfung und für `ausleiten` in
  `infra/sokrates2/wamo-core-slots.sh` als laufende Sitzung. `umstellen` und `ausleiten` warten oder scheitern an der Ruhe, solange
  jemand getippt hat. Das gilt schon ab diesem Paket.

### Stimme messen (Szenariotest)

Der Test spielt über ein virtuelles Mikrofon Sprechproben und Geräusche gegen einen lokalen Kern (Unterwegs) und nimmt
WAMOs Ausgabestrom, das Mikrofon und die Lautstärke des Wiedergabeelements mit einer gemeinsamen Uhr auf. Die Person
spricht immer dann, wenn WAMO in einem bestimmten Zustand ist („seit 5 s am Sprechen“). Szenarien: `fakten`, `kurz`,
`lang`, `einwurf`, `ein_wort`, `backchannel`, `geraeusch`, `echo`, `schnell`, `pause`, `werkzeug`, `werkzeug_mhm`. Voraussetzung: ein temporäres
Testkonto (`scripts/wamo_nutzer.py anlegen`, danach `sperren`), `konto.json` im Messordner (nicht im Repo), Oberfläche aus
einer Kopie gebaut (`NEXT_PUBLIC_VOICE_CORE_URL=http://127.0.0.1:7861`).

```powershell
python scripts\wamo_stimme.py clips D:\tmp\wamo-stimme2
$env:WAMO_MESS_DIR = "D:\tmp\wamo-stimme2"; $env:WAMO_UI_URL = "http://127.0.0.1:3101"
$env:KERN_ENV = "WAMO_STOPP_GRENZE=aus;WAMO_DUCKEN=aus"        # Schalter zum Vergleichen
node scripts\wamo-stimme-messen.cjs vorher "einwurf:8,backchannel:6,echo:4"
python scripts\wamo_stimme.py auswerten D:\tmp\wamo-stimme2 vorher
python scripts\wamo_stimme.py hoerprobe D:\tmp\wamo-stimme2 vorher D:\tmp\stimme-hoerprobe vorher
```

Kennzahlen: Zeit von der Frage bis zum ersten Ton, größte Stille innerhalb einer Antwort, Anteil falscher Unterbrechungen
(Rückmeldelaute, Geräusche, Echo), verpasste echte Einwürfe, Zeit bis zum Verstummen, hartes Abschneiden, erste Reaktion
der Oberfläche (Dämpfen).

Ergebnis (Unterwegs, Lauf `vorher2` mit allen Schaltern aus gegen `nachher2` mit den Standardwerten; Zahlen sind Median
und 95. Perzentil bei 5 bis 8 Wiederholungen, die Stichproben sind klein):

| Kennzahl | vorher | nachher |
| --- | --- | --- |
| Zeit bis zum Verstummen nach echtem Einwurf | 2285 / 2800 ms | 1245 / 2040 ms |
| Erste hörbare Reaktion auf den Einwurf | 2241 / 2750 ms | 757 / 780 ms (Dämpfen) |
| Verpasste Einwürfe | 0 von 8 | 0 von 8 |
| Hart abgeschnitten (Auslauf unter 35 ms) | 1 von 8 | 0 von 8 |
| Falsche Unterbrechung durch „mhm“, „ja genau“, „aha“ (Log) | 3 von 12 | 0 von 16 |
| Falsche Unterbrechung durch Husten, Klopfen | 0 von 8 | 0 von 10 |
| Unterbrechungen durch Echo (4 Läufe, Echopegel 0,5) | 11 | 3 |
| Größte Stille innerhalb einer Antwort | 1410 ms (p95), einmalige Stockung | 660 ms (p95) |
| Erster Ton nach Faktenfrage | 2101 / 2314 ms | 2058 / 2924 ms (innerhalb der Streuung) |

#### Sprachpakete A und B: Messung je Schalter (31.10.2026)

Aufbau: Unterwegs, Claude Haiku, Oberfläche aus einer Kopie. Lauf `basis3` mit allen sechs Schaltern aus, danach je ein Lauf
mit nur einem Schalter an, alle anderen aus. Zahlen sind Median / 95. Perzentil (bei 3 bis 8 Wiederholungen ist das 95.
Perzentil praktisch der größte Wert). Ein Strich heißt: nicht gemessen. Die Läufe der anderen Schalter dienen zugleich als
Streuung der Basis, weil in ihnen der jeweils geprüfte Schalter aus ist. Eine Verschlechterung gilt nur, wenn der Wert
deutlich über dieser Streuung liegt.

| Kennzahl | Basis | Satzleerzeichen | Rückfragen | PC-Ordner | Pausen-Prompt | Rückmeldung Werkzeug |
| --- | --- | --- | --- | --- | --- | --- |
| Verstummen nach Einwurf, ms | 1340 / 1910 (8) | 1400 / 1780 (7) | - | 1290 / 1960 (5) | 1315 / 2260 (6) | 1345 / 2480 (8) |
| Erste hörbare Reaktion (Dämpfen), ms | 761 / 770 | 771 / 781 | - | 769 / 799 | 769 / 790 | 766 / 921 |
| Verpasste Einwürfe | 0 von 8 | 0 von 7 | - | 0 von 5 | 0 von 6 | 0 von 8 |
| Falsche Unterbrechungen (Rückmeldelaute, Log) | 0 von 15 | 0 von 16 | - | - | - | 0 von 15 |
| Größte Stille in langen Antworten, p95 ms | 660 | 750 | 680 | 750 | 780 | 680 |
| Erster Ton nach Faktenfrage, ms | 2316 / 3242 | 2416 / 3154 | 2840 / 3740 | - (Hausmodell) | 2336 / 2574 | 2617 / 2814 |
| Erster Ton bei Websuche, ms | 2296 / 2791 | 2546 / 2682 | 2478 / 2621 | 2777 / 5852 | 2390 / 2481 | 2406 / 2441 |
| Unterbrechungen durch Echo (4 Läufe) | 7 | - (Lauf abgebrochen) | - | - | - | 5 |
| Pause: WAMO redet dazwischen (6 Pausen) | 0 | - | - | 0 | 0 | 1 |
| Werkzeug+mhm: Zug gestartet (10 Laute) | 5 | 7 | 3 von 6 | 3 von 6 | 3 von 6 | 0 |
| Werkzeug+mhm: als Rückmeldung erkannt | 0 | 0 | 0 | 0 | 0 | 8 |
| Werkzeug+mhm: Ergebnis kam | 5 von 5 | 4 von 5 | 2 von 3 | 3 von 3 | 3 von 3 | 5 von 5 |

Streuung der Läufe untereinander (Schalter jeweils irrelevant für die Kennzahl): Verstummen Median 1290 bis 1400 ms, p95 1780
bis 2260 ms (mit dem Lauf zu `WAMO_RUECKMELDUNG_WERKZEUG` 2480 ms); größte Stille p95 660 bis 780 ms; erster Ton nach Faktenfrage Median 2316 bis 2617 ms (früher 2058 bis 2101 ms);
Unterbrechungen durch Echo 3 bis 11 je 4 Läufe (frühere Läufe `vorher2` 11, `nachher2` 3, `basis3` 7); Zug bei Rückmeldelaut
ohne den Schalter 21 von 38 Lauten.

Schalter und Standardwerte (Stand 31.10.2026, Quelle `voice-core/env_schalter.py`, `STANDARDWERTE`; jeder Schalter
abschaltbar mit `aus`, `0`, `nein`, einschaltbar mit `an`, `1`, `ja`):

| Schalter | Standard | Begründung |
| --- | --- | --- |
| `WAMO_PAUSEN_PROMPT` | an | Lauf vollständig, alle Kennzahlen in der Streuung (Verstummen 1315 / 2260 ms, verpasst 0 von 6, erster Ton Fakten 2336 / 2574 ms, WAMO redet in den Pausen nie dazwischen). Der Nutzen (keine Floskel „Ich bin noch da“) zeigt sich in den Kennzahlen nicht, nur in der Hörprobe |
| `WAMO_RUECKMELDUNG_WERKZEUG` | an | Zug bei Rückmeldelaut 0 von 10 statt 5 von 10 (und 21 von 38 in den Läufen ohne Schalter), Ergebnis kam 5 von 5, Median des Verstummens gleich (1345 gegen 1340 ms), keine verpassten Einwürfe, keine falschen Unterbrechungen. Das 95. Perzentil des Verstummens (2480 ms) ist der größte von 8 Werten und liegt knapp über den anderen Läufen (bis 2260 ms), ebenso die erste Reaktion p95 921 ms (sonst 770 bis 799 ms, ein Ausreißer). Im Szenario Pause redete WAMO einmal dazwischen (1 von 6, sonst 0): der Schalter wirkt nur bei laufendem Werkzeug und im Szenario Pause läuft keines, vermutlich Zufall bei kleiner Stichprobe, ausgeschlossen ist es nicht Freigaben entscheidet nie die Sprache, Sätze mit Inhalt („Ja, bitte freigeben“) starten weiter einen Zug (`test_werkzeug_rueckmeldung.py`) |
| `WAMO_TTS_SATZLEERZEICHEN` | aus | Lauf brach beim Echo-Szenario ab (Browser geschlossen), Echo und Pause nicht gemessen; die übrigen Kennzahlen in der Streuung. Nutzen (Satzzeichen nicht vorlesen) nur in der Hörprobe prüfbar |
| `WAMO_PERSONA_RUECKFRAGEN` | an seit 04.11.2026 (Entscheidung Niko, zu viele Rückfragen im Test; die frühere Messung war unvollständig) | Lauf unvollständig (Datenbankausfall, Einwurf, Backchannel, Echo, Pause fehlen); erster Ton nach Faktenfrage 2840 ms gegen 2316 bis 2617 ms in den anderen Läufen, bei 6 Fragen nicht entscheidbar |
| `WAMO_PERSONA_PC_ORDNER` | aus | Sitzung mit den Faktenfragen fiel auf ein Hausmodell (Datenbank nicht erreichbar), nicht gemessen; erster Ton bei Websuche p95 5852 ms gegen 2441 bis 2791 ms (ein Ausreißer bei 4 Antworten) |
| `WAMO_JA_WAECHTER` | an | Seit 14.11.2026 an: Ein kurzes Ja auf eine echte Rückfrage führt den Auftrag aus, statt erneut zu fragen (`voice-core/ja_waechter.py`). Freigaben bleiben unberührt. Messung steht aus |

Nicht gemessen und offen: `WAMO_JA_WAECHTER` (kein Lauf), Echo und Pause für `WAMO_TTS_SATZLEERZEICHEN`, Einwurf,
Backchannel, Echo, Pause für `WAMO_PERSONA_RUECKFRAGEN`, Faktenfragen für `WAMO_PERSONA_PC_ORDNER`, die Kombination aller
einzeln unauffälligen Schalter, eine zweite Basis für die Streuung. Ausfälle: In den Läufen fiel zweimal der Zugriff auf die
Datenbank von Sokrates-2 aus (Zugang dann „nur Hausmodelle“, Auswertung warnt jetzt mit „ACHTUNG … Hausmodell“), und der
Arbeitsspeicher des Messrechners war knapp (Oberfläche und Browser stürzten einmal ab). Die Messung zeigt nur, dass sich
die Kennzahlen nicht verschlechtern; ob die Stimme besser klingt, entscheiden die Hörproben in
`D:\tmp\stimme-hoerprobe2\` (`vorher-*` gegen `<schalter>-an-*`; jede Datei mischt Mikrofon und WAMO wie im Raum).
Worauf man hört: `satzleerzeichen-an-antwort` (wird am Satzende „Punkt“ oder „Fragezeichen“ mitgesprochen, klingt das
Satzende rund?), `pausenprompt-an-pause` (sagt WAMO nach „ähm,“ nichts oder genau einen kurzen Satz, nie „Ich bin noch da“),
`werkzeugmhm-an-werkzeug_mhm` gegen `vorher-werkzeug_mhm` (nach „mhm“ und „ja genau“ während der Websuche läuft die Suche
weiter und WAMO antwortet danach; vorher bricht sie oft ab und fängt neu an), `rueckfragen-an-antwort` und
`pcordner-an-werkzeug` (Antwortlänge, Rückfragen), dazu `*-unterbrechung` und `*-backchannel` als Gegenprobe, dass
Einwurf und „mhm“ unverändert klingen. Die Dateien wurden nur ausgewertet, nicht angehört.

Nicht messbar verbessert: die Stille innerhalb von Antworten (Satzpausen 0,5 bis 0,65 s sind die Stimme selbst, die
Stockung von 1,4 s im Lauf `vorher2` trat im Endstand nicht mehr auf, Ursache offen) und die Wartezeit bei Werkzeugen
(Websuche 6 bis 10 s, das Modell sagt vorher selbst „Moment“). Befunde, die den Entwurf bestimmt haben:

- Soniox-TTS hält das Ende des Textes zurück, bis weiterer Text oder `text_end` kommt (Messung: der Rest eines Satzes
  erklingt erst, wenn der nächste eintrifft). Lücken in laufenden Antworten entstehen deshalb nur, wenn das Modell langsamer
  liefert als WAMO spricht, vor allem bei Werkzeugaufrufen: dort setzt der Wartesatz ein.
- Die Stille von etwa 2,4 s bis zum Verstummen kam fast ganz aus dem Kern: Spracherkennung (0,9 s bis zum ersten Wort),
  dann bis zu drei weitere Wörter oder die Zeitgrenze von 1,5 s, dann 0,5 s Vorsprung der Zeitstempel.
- Vorbilder: Pipecat (Verlauf enthält nur das Gespielte, `MinWords` gegen Rückmeldelaute), LiveKit (Rückmeldelaute und
  Wiederaufnahme nach falscher Unterbrechung, 0,5 s Mindestdauer, 2 s `false_interruption_timeout`), OpenAI Realtime (Kürzen
  des Verlaufs auf die gehörte Dauer), Soniox und Full-Duplex-Bench-v3 (Wartesatz nur einmal, kurz, abbrechbar).
- REA an der öffentlichen Demo unmute.sh (Quelle des Clients offen, Auswertung der WebSocket-Ereignisse mit REA): der Client
  puffert nur 80 ms und leert beim Unterbrechen, der Server unterbricht auf die Sprachaktivität vor dem Transkript
  (`unmute.interrupted_by_vad`), filtert aber „Mm hmm“ nicht und antwortet dann darauf. Übernommen wurde die frühe
  Reaktion (Dämpfen), nicht das Fehlen des Filters.

#### Gesprächsoptimierung (31.10.2026)

Recherche, Vergleich mit dem Code und Maßnahmenliste: `docs/gespraech-optimierung.md`. Umgesetzt sind zwei Schalter (beide
Standard aus, bis gemessen) und eine Kennzahl:

| Schalter | Standard | Wirkung |
| --- | --- | --- |
| `WAMO_PERSONA_GESPRAECH` | aus | Prompt-Block "Gesprächsführung": bei Selbstkorrektur gilt nur die letzte Fassung, Rückbezug statt Neufragen, nichts wiederholen, nur eine Frage, knappe Bestätigung. Eine Freigabe ersetzt das nie |
| `WAMO_WARTESATZ_MODELL` | aus | Liefert das Modell nach einer Anfrage `WAMO_WARTESATZ_MODELL_SECS` (Standard 2,5) lang weder Zeichen noch Marker noch Werkzeugaufruf, sagt WAMO einmal "Einen Moment." (nicht im Verlauf). Ein unvollständiger Satz der Person (Marker ◐ und ○) löst nichts aus |
| `WAMO_PERSONA_NATUERLICH` | an | Natürliche Sprechweise: Die meisten Antworten beginnen mit einer kurzen Reaktion als eigenem Satz ("Klar.", "Okay.", "Hm, gute Frage."), die sofort gesprochen wird, danach zusammenhängende Sätze, ab und zu ein "ähm", Abrunden mit einer eigenen Einschätzung, keine Anschlussfrage nach erledigten Aufträgen (`voice-core/persona.py`, gilt auch für Laborprofile). Mit `aus` gilt die frühere ruhige Sprechweise mit kurzen Sätzen |
| `WAMO_FUELLWOERTER` | an | Startet das Modell ein Werkzeug, ohne vorher etwas zu sagen, und ist nach `WAMO_FUELLWORT_SECS` (Standard 0,4) noch kein Ton da, spielt WAMO ein vorab aufgenommenes "Moment." (`voice-core/fuellwoerter.py`). Läuft das Werkzeug weitere 6 Sekunden still, einmal "Dauert noch einen Moment.". Die Aufnahmen entstehen einmal je Stimme und Einstellung beim ersten Gesprächsstart und liegen danach im Cache-Ordner des Dienstes. Nur mit der Soniox-Stimme, Messung steht aus |
| `WAMO_MHM` | aus | Hält das Modell den Satz der Person für unfertig, sagt WAMO selten ein kurzes "Mhm" (Ruhezeit 20 Sekunden, nie wenn die Person weiterspricht) |
| `WAMO_INNEHALTEN` | an | Ein einzelnes echtes Wort der Person ("Danke", "Du"), während WAMO spricht, lässt WAMO an der nächsten Wortgrenze innehalten (der Tonausgang pausiert, `voice-core/wiedergabe.py`); kommt nichts nach, spricht WAMO dort weiter. Zwei Wörter oder ein Stoppwort brechen ab wie bisher, Rückmeldelaute nie. Mit `aus` bewirkt ein einzelnes Wort nichts, ein Einwurf stoppt wie vorher mit Ausblenden in der Oberfläche. Nicht bei der Sprachausgabe im Haus (ganze Sätze) |
| `WAMO_INNEHALTEN_WEITER_SECS` | 0,7 | So viel Stille nach dem letzten Wort der Person, dann spricht WAMO an derselben Stelle weiter (0,2 bis 3) |
| `WAMO_INNEHALTEN_MAX_SECS` | 2,5 | So lange hält WAMO höchstens inne, auch wenn die Person weiterspricht (0,5 bis 3,5; Pipecat verträgt keine Pause über 10 s, der Halt gibt spätestens nach 4 s frei) |

Neue Kennzahl `folge_ton_ms` (Szenario `einwurf`): Zeit vom Ende des Einwurfs bis zum ersten Ton der Folgeantwort. Zu messen
(Testprofil gesperrt, Messplan im Dokument, Abschnitt 5): Basis mit beiden Schaltern aus, je ein Lauf mit nur einem an, dazu
ein Lauf mit `SONIOX_TTS_URL=wss://tts-rt.eu.soniox.com/tts-websocket` gegen den globalen Endpunkt.

### Lange Werkzeugausgaben kürzen

Lange Ausgaben von Werkzeugen (PowerShell-Listen, Testausgaben, Git-Ausgaben, JSON von Geräten, Webseitentext) gingen bisher
ungekürzt in den Kontext des Sprachmodells. Das kostet bei jeder weiteren Anfrage des Zugs Tokens und verlängert die Zeit bis zur
Antwort. Der Kern kürzt sie jetzt selbst, verlustarm und ohne Fremdsoftware (`voice-core/ausgaben_kuerzen.py`).

**Wo.** Jedes Werkzeugergebnis erreicht die Pipeline genau einmal als `FunctionCallResultFrame`, egal ob es aus `tools.py`, dem
Boss, dem Gerätedienst oder über den MCP- und Tunnel-Client vom PC kommt. Der Frame-Prozessor `AusgabenKuerzer` sitzt in
`bot.py` direkt hinter der Aktivitätsanzeige (Zeitleiste, Freigabekarten und PC-Sperre sehen das Original) und vor dem
Kontext-Aggregator (er schreibt das Ergebnis in den Verlauf). Neue Werkzeuge sind ohne Zutun dabei.

**Wirkung.** Ab der Schwelle (`WAMO_AUSGABE_SCHWELLE`, Standard 3000 Zeichen, 0 schaltet das Kürzen und das Werkzeug ab) gilt:

- Text: gleiche Zeilen in Folge werden mit Zähler zusammengefasst (`[400 mal]`), Leerzeilen gebündelt, Kopf und Schwanz bleiben,
  die Mitte wird durch `[... n Zeilen ausgelassen, Nachladen: ausgabe_nachladen("ref", ab_zeile=12)]` ersetzt. Zeilen mit
  error, fehler, exception, warn, traceback, fail, denied, abgelehnt, fatal, blocked, locked, gesperrt, verweigert, forbidden,
  approval, critical, freigabe oder rejected bleiben bevorzugt stehen, zusammen aber höchstens die Hälfte des Zielumfangs (Kopf und
  Schwanz bekommen den Rest). So kann Fremdtext das Budget nicht mit Zeilen füllen, die ein Stichwort enthalten.
- JSON (auch als Text vom PC-Host): leere Werte und null fallen weg, lange Listen behalten die ersten Einträge plus Einträge mit
  Problem und einen Zähler (`weitere 37 Einträge`), lange Texte darin werden zeilenweise wie oben gekürzt. Zahlen, Wahrheitswerte,
  `status` und die Felder für Fehler, Hinweis und Vertrauen bleiben unverändert; Listen aus Zahlen werden nie gekürzt.
- Das Ergebnis trägt `gekuerzt: true` und `ref`, damit das Modell nicht glaubt, es habe alles gesehen. Reicht der Gewinn nicht
  (weniger als 15 Prozent), bleibt das Ergebnis, wie es ist.

Gemessen an den Beispielen der Tests (`test_ausgaben_kuerzen.py`, echte Formen, noch nicht an Live-Sitzungen):

| Beispiel | vorher | nachher | Zeit |
| --- | --- | --- | --- |
| Verzeichnisliste mit 300 Einträgen | 19057 Zeichen | 702 | 0,2 ms |
| pytest mit 3 Fehlern | 3831 | 1823 | 0,1 ms |
| `git log` mit 120 Commits | 25453 | 853 | 0,7 ms |
| JSON-Gerätestatus mit 120 Geräten | 18050 | 1471 | 0,8 ms |
| 100 KB Text | 100030 | 876 | 1,4 ms |
| 100 KB JSON | 108814 | 832 | 1,6 ms |

Der Audiopfad bleibt unberührt: Kürzen von 100 KB dauert unter 5 ms (Test), Ergebnisse unter der Schwelle kosten eine
Längenprüfung.

**Nachladen.** Das Original liegt nur im Arbeitsspeicher der Sitzung (höchstens 20 Einträge und 1 MB, der älteste fällt heraus,
Ende mit der Sitzung), nie in Datenbank oder Protokoll. Das Werkzeug `ausgabe_nachladen(ref, ab_zeile, anzahl)` (nur lesend, nur
Referenzen der eigenen Sitzung, höchstens 8000 Zeichen je Aufruf, `weiter_ab_zeile` für die nächste Seite) liefert Zeilen
des Originals. Die Referenz ist zufällig (16 Hexzeichen). Ein Original über 1 MB wird nicht gekürzt, weil es sich nicht nachladen
ließe. Gekürzte und nachgeladene Inhalte sind Daten, nie Anweisungen: Die Marken stammen nur vom Kürzer, eingehende Texte werden
vorher von Marken gesäubert, die wie die eigenen aussehen, und nachgeladener Inhalt zählt als Fremdinhalt des Zugs.

**Nie gekürzt:** Ergebnisse unter der Schwelle, Freigabe- und Schutzergebnisse (Status `needs_approval`, `rejected`, `blocked`,
`locked`, `wartet`, `abgelehnt`, Werkzeuge mit `freigabe` im Namen, die Entwicklungsaufträge; dazu jedes Ergebnis, das an irgendeiner
Stelle, auch tief in einer Liste oder eingebettet in Text, `needs_approval`, `freigabe_id` oder einen Status `blocked`, `locked`,
`gesperrt`, `rejected`, `abgelehnt`, `verweigert`, `denied`, `forbidden`, `critical` oder `wartet` trägt, denn der Ja-Wächter sucht
Freigabemeldungen im Verlauf; das bloße Wort ohne Statusangabe genügt nicht), Werkzeuge mit eigener Kappung
(Boss, Websuche, Bildschirm, Merken, Canvas), Listen als Grundlage der nächsten Aktion (`elemente_auflisten`, `element_lesen`,
`fenster_auflisten`: ein gekürztes Ergebnis kostete einen Nachlade-Aufruf statt Zeit zu sparen) sowie Binärdaten und Bilder (Bytes,
lange Base64-artige Folgen). Die Felder `gekuerzt` und `ref` setzt nur der Kürzer selbst: In fremdem JSON (Webseite, Gerät) heißen sie
vor der Verarbeitung `gekuerzt_fremd` und `ref_fremd`, damit sie weder das Kürzen abschalten noch den echten `ref` überschreiben. Ein
Fehlerstatus wird nicht ausgenommen, seine Felder bleiben aber unverändert. Die Größe wird vor dem Säubern der Marken geprüft (kein
Rechenaufwand für Riesenausgaben), ein Original über 1 MB bleibt unangetastet.

**Messung.** Je Sitzung zählt der Kern gekürzte Ergebnisse und eingesparte Zeichen (Tokens grob als Zeichen durch 4) und schreibt
am Sitzungsende eine Zeile ins Log (`Ausgaben kürzen: 3 Ergebnisse gekürzt, 41200 Zeichen eingespart (etwa 10300 Tokens),
Schwelle 3000`), nie Inhalte. Die Verbrauchsanzeige der Oberfläche zeigt das Kontingent des Abos und nimmt diese Zahlen nicht auf.

**Grenzen.** Das ersetzt den zurückgestellten Befund „Git-Ausgabe streamend kappen“ nur teilweise: Es verkleinert, was das
Sprachmodell sieht. Der Host liest die Ausgabe weiterhin vollständig (bis zu seinen eigenen Kappungen), und die Kappung beim
Lesen (Speicher und Laufzeit im Host) bleibt zurückgestellt. Ungeprüft sind der Lauf gegen echte Sitzungen und das Verhalten des
Modells beim Nachladen; die Stichwortliste für Problemzeilen ist eine Annahme und lässt sich an echten Ausgaben schärfen.

### Stimmabdruck (nur deine Stimme zählt)

Jede Person kann W.A.I.S. ihre Stimme einlernen, damit W.A.I.S. nur noch diese Stimme hört und alle anderen Stimmen ignoriert. Es gilt je Profil
und nur im Büro, nie im Testlabor (Laborprofile bekommen weder Einlernen noch Prüfung, die Nachricht `stimmabdruck` wird für sie von der
Laborsperre abgewiesen). Code: `voice-core/stimme_*.py`, Tests: `voice-core/test_stimme_*.py`.

**Bedienung.** Der Kern schickt beim Verbinden den Text der Einwilligung (Version `stimme-1`). In den Einstellungen der Oberfläche liest die
Person ihn, bestätigt ihn und startet das Einlernen (Klick, nie per Sprache). W.A.I.S. sagt kurz, worum es geht, dann liest die Person den Text
vor oder erzählt frei, bis der Balken voll ist (20 Sekunden reine Sprache). Währenddessen hört die Spracherkennung nur Stille, W.A.I.S. antwortet
also nicht auf den Vorlesetext. Danach steht der Abdruck im Profil. **Löschen** in den Einstellungen entfernt den Abdruck
vollständig (DSGVO Art. 17, zugleich Widerruf der Einwilligung). Wer nicht einlernt, hat keinen Nachteil: Ohne Abdruck prüft W.A.I.S. nichts und
sperrt nichts. Einen vorhandenen Abdruck zu **ersetzen oder zu löschen** (und danach in derselben Sitzung neu einzulernen) geht in jedem Modus
außer `aus` nur mit Nachweis: Die Person wurde gerade sicher an der Stimme erkannt, oder die Anmeldung mit zweitem Faktor ist jünger als
10 Minuten (Zeitpunkt aus dem Token, `amr`). Sonst meldet die Oberfläche einen Fehler und bittet, sich neu anzumelden. Im Modus `aus` geht Löschen weiter immer.

**Modi** (`WAMO_STIMME_PRUEFUNG`, wirkt nur für Profile mit Abdruck):

| Modus | Wirkung |
| --- | --- |
| `streng` (Standard) | Eine **sicher fremde** Äußerung erreicht das Sprachmodell nicht: kein Zug, kein Eintrag im Verlauf und im Gedächtnis, eine Meldung an die Oberfläche (`ignoriert`). Unklare oder kurze Äußerungen gehen durch, aber alle Werkzeuge außer Uhrzeit, Websuche und Canvas sind gesperrt, solange die Stimme nicht sicher die eigene war (oder die Person nicht getippt hat). |
| `beobachten` | Misst, zeigt das Ergebnis in der Oberfläche und schreibt es ins Log, sperrt und verwirft nichts. Geeignet zum Einstellen der Schwellen. |
| `aus` | Keine Prüfung, kein Einlernen. Löschen eines vorhandenen Abdrucks geht trotzdem. Ohne Datenbank ist die Prüfung ebenfalls aus. |

**Schwellen** (alle in `voice-core/.env.example`, am Server über `wamo-core-env`; leer heißt Standard, Unbrauchbares auch):

| Schalter | Standard | Bedeutung |
| --- | --- | --- |
| `WAMO_STIMME_EIGEN_MIN` | 0,5 | Ab diesem Wert (Kosinus der Abdrücke, 0,35 bis 0,95) gilt eine Äußerung als die eigene Stimme |
| `WAMO_STIMME_FREMD_MAX` | 0,3 | Bis zu diesem Wert (0 bis 0,8) gilt sie als sicher fremd; muss unter `EIGEN_MIN` liegen, sonst gelten beide Standardwerte |
| `WAMO_STIMME_MIN_SECS` | 1 | So viel reine Sprache braucht eine Äußerung für ein Urteil (0,3 bis 10); kürzere ("Ja", "mach") erben das Vertrauen nur, wenn sie nach der eigenen Stimme klingen (Wert ab `EIGEN_MIN`), sonst gilt die Stimme als nicht sicher erkannt |
| `WAMO_STIMME_KURZ_VERTRAUEN_SECS` | 15 | So lange läuft das Vertrauen höchstens weiter, wenn seit der letzten sicheren Erkennung nur kurze Äußerungen kamen (1 bis 120); eine sichere Erkennung oder getippter Text beginnt neu |
| `WAMO_STIMME_VERTRAUEN_SECS` | 120 | So lange gilt "eigen" für kurze Folgesätze und Werkzeuge (10 bis 3600) |
| `WAMO_STIMME_MODELL` | Cache-Ordner | Pfad der Modelldatei, Standard `XDG_CACHE_HOME/wamo/sprecher/nemo_en_titanet_large.onnx`, sonst `voice-core/.cache/sprecher/` |

**Wie geprüft wird.** Das Modell (NeMo TitaNet-Large als ONNX über `sherpa-onnx`, 192 Werte je Stimme) läuft im Kern auf der CPU, einmal je
Prozess für alle Sitzungen. Abdruck und Prüfzustand gehören je Sitzung zum Profil, einen Abdruck-Zwischenspeicher über Profile hinweg gibt
es nicht. Der Ton der letzten 10 Sekunden liegt nur im Arbeitsspeicher; beim Sprechende (VAD) wird die Äußerung (höchstens die letzten 8
Sekunden) berechnet und mit dem Abdruck verglichen. Das Ergebnis steht im Log (`Stimme: 0.78 bei 2.3 s -> eigen`, nie Inhalte). Zum Einlernen
teilt der Kern die Sprache in Stücke von 3 Sekunden, nimmt den Mittelwert und verwirft die Aufnahme, wenn die Stücke zu unterschiedlich
klingen (zu unruhig oder mehrere Stimmen). Gespeichert werden nur 192 Zahlen, nie eine Aufnahme.

**Antwortzeit.** Die Prüfung beginnt beim Sprechende und läuft parallel zur Spracherkennung. Der Wächter hält nur das **endgültige** Transkript
einer Äußerung an, bis das Urteil da ist, höchstens 1,5 Sekunden; die Zwischenstände laufen ungebremst durch, Unterbrechen bleibt so schnell
wie vorher. Die Antwort für die eigene Stimme wird also höchstens um den Teil der Prüfzeit später, der über die Wartezeit der Spracherkennung
hinausgeht (rund 250 ms für 8 Sekunden Sprache auf einem PC, am Server nicht gemessen). Läuft die Frist ab, gilt die Äußerung als unklar und
geht durch. Der Wächter ersetzt das Transkript einer fremden Stimme durch ein Leerzeichen, der Zug endet dann leer (`on_user_turn_stopped`
in `bot.py` kehrt zurück).

**Meldungen an die Oberfläche** (Servernachrichten, Details in der Kopfzeile von `voice-core/bot.py`):

- `{"type": "stimme", "status": "aus"|"fehlt"|"laedt"|"kaputt"|"bereit", "modus": "..."}` Stand beim Verbinden und nach jeder Änderung. `fehlt`: nichts eingelernt (nichts wird geprüft oder gesperrt), `kaputt`: Modell oder Datenbank nicht verfügbar (streng sperrt dann Werkzeuge, Tippen geht; war die Datenbank nicht lesbar, liest der Kern den Stand nach 30 s, 1, 2 und dann alle 5 Minuten neu, bis er bekannt ist).
- `{"type": "stimme", "ergebnis": "eigen"|"fremd"|"unklar", "wert": 0.78, "modus": "..."}` je Äußerung; zusätzlich `"ignoriert": true`, wenn eine sicher fremde Äußerung nicht ans Modell ging.
- `{"type": "stimmabdruck", "status": "start"|"aufnahme"|"rechnet"|"fertig"|"fehler"|"abgebrochen"|"geloescht", "sekunden": 8.4, "ziel": 20, "text": "..."}` beim Einlernen und Löschen.
- `{"type": "stimmabdruck_einwilligung", "version": "stimme-1", "text": "..."}` beim Verbinden (nicht im Modus aus, nie im Labor; im Labor kommt nur `{"type": "stimme", "status": "aus", "modus": "aus"}`).
- Von der Oberfläche: Client-Nachricht `stimmabdruck` mit `{"aktion": "start", "einwilligung": "stimme-1"}`, `{"aktion": "abbrechen"}` oder `{"aktion": "loeschen"}`. Der Start gilt nur mit genau der aktuellen Version der Einwilligung.

**Datenschutz.** Gespeichert werden nur der Abdruck (192 Zahlen) sowie Zeitpunkt und Version der Einwilligung in `speakers` (Spalte
`voiceprint`, Migration `0001`, Rolle `wamo_core`, für die Oberfläche nicht lesbar), nie eine Aufnahme. Berechnung und Vergleich laufen im
Kern auf dem eigenen Server, nichts davon geht an Soniox oder Anthropic. Zweck, Rechtsgrundlage (ausdrückliche Einwilligung, Art. 9 Abs. 2
lit. a DSGVO), Löschung und den Wortlaut der Einwilligung nennt der Abschnitt 4 in `docs/labor-datenschutz-entwurf.md` (Entwurf, eine
verantwortliche Person prüft ihn). Offen vor dem Echtbetrieb: die Datenschutz-Folgenabschätzung nach Art. 35 DSGVO und die Aufbewahrung der
Sicherungen.

**Modelldatei.** Die Datei (`nemo_en_titanet_large.onnx`, rund 100 MB, SHA-256 `d51abcf31717ef28162f26acb9d44dd4127c3d44c9b8624f699f3425daca8e77`)
lädt der Kern beim ersten Bedarf von GitHub (Release `speaker-recongition-models` von sherpa-onnx) nach, prüft die Summe und prüft sie bei jedem
Prozessstart erneut; eine falsche Datei im Cache-Ordner wird gelöscht (ein Pfad aus `WAMO_STIMME_MODELL` nie), nach einem Fehlschlag wartet der Kern 60 Sekunden, 5 Minuten, danach 30 Minuten bis zum nächsten Versuch. Der Download ist auf 150 MB und 10 Minuten begrenzt. Ohne Datei (und ohne Netz) gibt es kein Einlernen und
keine Prüfung, das Gespräch läuft weiter. Am Server ist das ausgehende Netz begrenzt, deshalb vorab ablegen (Pfade nach der Unit
`wamo-core@.service`, `CacheDirectory=wamo-core`, `XDG_CACHE_HOME=/var/cache/wamo-core`, am Server nicht ausgeführt):

```bash
# auf einem Rechner mit Netz
curl -L -o nemo_en_titanet_large.onnx https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_large.onnx
sha256sum nemo_en_titanet_large.onnx        # muss die oben genannte Summe zeigen
scp nemo_en_titanet_large.onnx wamo-sokrates2:/tmp/
# auf Sokrates-2
sudo install -d -o wamo-core -g wamo-core -m 0700 /var/cache/wamo-core/wamo /var/cache/wamo-core/wamo/sprecher
sudo install -o wamo-core -g wamo-core -m 0600 /tmp/nemo_en_titanet_large.onnx /var/cache/wamo-core/wamo/sprecher/
rm /tmp/nemo_en_titanet_large.onnx
sudo -u wamo-core sha256sum /var/cache/wamo-core/wamo/sprecher/nemo_en_titanet_large.onnx
```

Liegt die Datei woanders, nennt `WAMO_STIMME_MODELL` (über `wamo-core-env`) den Pfad; kein `WAMO_STIMME_*` steht in der Liste der Schalter, die
`scripts/wamo_kern_sokrates2.py` aus der `.env` des PCs übernimmt: Modus und Schwellen sind Sicherheitseinstellungen des Servers (nur über
`wamo-core-env`), und der Pfad gilt je Rechner. Die Bibliothek `sherpa-onnx` 2.0.1 steht in
`voice-core/pyproject.toml` und `uv.lock`; für Linux aarch64 und Python 3.12 gibt es beide Wheels (`sherpa-onnx` und `sherpa-onnx-core`,
laut PyPI am 14.11.2026). Ein echter Lauf auf dem Server steht aus.

**Grenzen.** Die Stimmprüfung ersetzt keine Freigabe: Aktionen mit Risiko brauchen weiter die Freigabe per Klick. Sie schützt nicht gegen
Aufnahmen oder geklonte Stimmen. Sie kann sich irren (Erkältung, Lärm, Mikrofon): Eine eigene Äußerung, die als sicher fremd gilt, wird im Modus
streng ignoriert, getippter Text geht immer; bei häufigen Fehlurteilen `WAMO_STIMME_FREMD_MAX` senken, `beobachten` einschalten oder
neu einlernen. Spricht jemand Fremdes, während WAMO redet, ist das Urteil unklar (Echo): der Satz geht durch, nur die Werkzeuge bleiben
gesperrt. Kurze Äußerungen (unter einer Sekunde Sprache, etwa "Ja") tragen das Vertrauen nur weiter, wenn sie nach der eigenen Stimme klingen, und höchstens
15 Sekunden nach der letzten sicheren Erkennung; klingen sie anders, ist die Stimme nicht sicher erkannt (Werkzeuge gesperrt, die Äußerung geht ans Modell).
Ein neues Stück Sprache nach einem fertigen Satz (Satzzeichen im Transkript), nach Ende des Zuges oder nach Rede von WAMO wird für sich geprüft; mitten in
einem unfertigen Satz bleibt es beim Zug, deshalb kann ein Einwurf einer fremden Stimme innerhalb eines Satzes der Person noch am Urteil des Satzes hängen.
Eine Äußerung, die nicht sicher die Stimme der Person war (Profil mit Abdruck, Modus `streng`), färbt den Zug wie Fremdinhalte:
WAMO lernt nichts daraus, der Ja-Wächter hängt keinen Hinweis an, und im Gesprächsprotokoll (`turns.speaker_label`) steht `stimme-unsicher`.
Ohne Abdruck oder im Modus `beobachten` ändert sich nichts.
Die Untertitel der Oberfläche können einen ignorierten Satz noch zeigen. Die Schwellen 0,5 und 0,3 sind Richtwerte aus Beispielaufnahmen
des Modells, mit den Mikrofonen im Büro noch nicht gemessen: vor dem Einsatz einige Gespräche im Modus `beobachten` laufen lassen, die Werte im Log
ansehen, die Schwellen bei Bedarf anpassen und danach wieder `streng` (Standard) einstellen.

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
- **Vorschläge in der Oberfläche prüfen (seit 12.11.2026, Migration `0020_gedaechtnis_vorschlaege.sql`):** Admins finden unter
  Einstellungen, „Gedächtnis“ alle offenen Vorschläge mit vollem Text, Art, Autor und Zeit und entscheiden mit „Für alle freigeben“
  oder „Ablehnen“ (gleich gewichtet). Ein Admin sieht beim Entstehen eines Vorschlags im Hinweis „Gemerkt“ den Link „Jetzt prüfen“.
  Kern: `voice-core/gedaechtnis_api.py` (`GET /wamo/api/gedaechtnis/vorschlaege`, `POST /wamo/api/gedaechtnis/entscheiden`, nur Admin,
  30 Aufrufe je Minute). Der Kern hat weiter kein `UPDATE` auf `memories.status`: Er ruft die Datenbankfunktion
  `gedaechtnis_vorschlag_entscheiden` (SECURITY DEFINER, `EXECUTE` nur für `wamo_core`) mit der aus dem Token geprüften Admin-Kennung
  auf; sie prüft den Admin in der Datenbank noch einmal und ändert genau eine offene Zeile (`status`, `reviewed_by`, `reviewed_at`).
  Ein freigegebener Eintrag gilt in allen Sitzungen, die danach beginnen. Reihenfolge beim Einspielen: Migration, dann Kern, dann
  Oberfläche. Der Weg über `wamo_admin.py` bleibt. Tests: `voice-core/test_gedaechtnis_api.py`, `test_migration_0020.py`,
  `supabase/tests/0020_gedaechtnis_vorschlaege_test.sql`, `ui/src/lib/gedaechtnis.test.ts`, `Gedaechtnis.comp.test.tsx`.
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

### Abnahme: prüfbares Ziel mit Runden

Ein Entwicklungsauftrag kann ein prüfbares Ziel haben. Er läuft dann in Runden: arbeiten, prüfen, bei Nichterfüllung die nächste
Runde mit genau den offenen Punkten, bis alles erfüllt ist oder Runden oder Zeit erschöpft sind. Das Ziel muss messbar sein
("die Tests laufen grün", "die README erklärt die Installation"), nicht "mach es gut". Die Schleife ist eigener Code des Kerns
(`voice-core/abnahme.py`, Schleife in `dev_agent.py`): WAMO braucht je Kriterium ein Ergebnis, begrenzt Runden und Zeit selbst und
bleibt unabhängig von der Version von Claude Code. Ohne `abnahme` bleibt alles wie bisher: eine Runde, kein Prüfen.

Das Sprachmodell setzt die Argumente von `entwicklung_vorbereiten`; nennt die Person kein Ziel, leitet es selbst ein bis drei
Kriterien ab und nennt sie in einem Satz, ohne zusätzliche Rückfrage; den Projektnamen leitet es aus dem Auftrag ab und fragt nicht danach
(Persona-Schalter `WAMO_PERSONA_ABNAHME`, Standard an; `aus` bringt den früheren Prompt zurück).

| Argument | Bedeutung |
| --- | --- |
| `abnahme` | Liste von höchstens 5 Texten, je höchstens 300 Zeichen (Steuerzeichen entfernt, Doppelte fallen weg) |
| `max_runden` | Standard 3, hart höchstens 5 |
| `zeitlimit_min` | Standard 30, hart höchstens 60. Gezählt wird die Arbeitszeit der Läufe und Prüfungen; das Warten auf einen Klick bei einer Freigabe zählt nicht |

Zwei Arten von Kriterien:

- **Aussage** (jeder normale Text): Ein kleines Modell (der günstigste Weg des Profils, im Kern der schlanke Aufruf `llm_anbieter.anfrage`,
  bei nur Hausmodellen das Hausmodell) beurteilt sie anhand des Abschlussberichts und der Liste der geänderten Dateien (nur was mit
  Write und Edit geändert wurde). Es antwortet mit strengem JSON `{"erfuellt": true|false, "hinweis": "..."}`. Alles andere
  (kein JSON, andere Felder, falsche Typen, Fehler, Zeitüberschreitung nach 60 s) zählt als nicht erfüllt. Bericht, Dateinamen und
  Kriterium stehen als Daten in Blöcken mit einem zufälligen Code, nie als Anweisung. Das ist ein weiches Urteil, kein Beweis.
- **Befehl** (Eintrag beginnt mit `befehl:`, zum Beispiel `befehl: pytest -q`): Exit 0 heißt erfüllt. **Standard aus:** Befehlskriterien
  sind eine bewusste Zuschaltung des Betreibers (`WAMO_ABNAHME_BEFEHLE=ja`, nur dieser Wert, dazu die Betriebsart `lokal` oder auf
  Sokrates-2 der Weg über den Dispatcher, siehe unten). Ohne den Schalter lehnt `entwicklung_vorbereiten` ein `befehl:` schon beim Vorbereiten mit einem Satz ab (die Werkzeugbeschreibung nennt es dem
  Modell gar nicht erst), es bleiben Aussagekriterien. Grund: Projektcode (conftest, Makefile, Tests) läuft durch einen Befehl
  automatisch mit den Rechten des Kerns, ohne Hook und ohne eigene Freigabe. Das ist neu gegenüber dem Ablauf vor Paket 4 und soll eine
  Entscheidung sein, kein Nebeneffekt. Der Text stammt vom Modell und ist nicht vertrauenswürdig, deshalb gilt:
  - ein einzelner Befehl mit Argumenten, aufgerufen ohne Shell; keine Zeichen für Verkettung, Umleitung oder Ersetzung (Semikolon, &amp;,
    senkrechter Strich, Backtick, Dollarzeichen, spitze Klammern, Backslash), keine Zeilenumbrüche und Steuerzeichen,
    Anführungszeichen nur paarweise;
  - eine echte Allowlist je Programm: erlaubt ist nur, was ausdrücklich aufgeführt ist, jede andere Option und jedes andere Argument wird
    abgewiesen (das Kriterium gilt dann als nicht erfüllt, der Befehl läuft nie). Erlaubt sind: `pytest` (`-q`, `-x`, `-v`, `--tb=short|line|no`,
    `-k` und `-m` mit einem einfachen Ausdruck aus Namen, `and`, `or`, `not` und Klammern, Testpfade relativ im Projekt, auch mit `::`),
    `python -m pytest|unittest|mypy|compileall` mit denselben Regeln (`unittest`: `-v -q -f -b`, `discover`, `-s`, `-p`, Testnamen, die mit `tests` oder `test_` beginnen;
    `compileall`: `-q`), `npm|pnpm|yarn test` und `run test|build|lint|typecheck|check|verify` (danach höchstens `--` mit `--run`, `--ci`,
    `--watchAll=false`, `--passWithNoTests`), `node --test` mit relativen Testpfaden, `go test` (`-run`, `-count`, `-v`, `-short`,
    `-failfast`, Paketmuster `./...` oder relative Pfade), `go vet` und `go build` (nur Paketmuster, bei `build` zusätzlich `-v`),
    `cargo test|check|build|clippy` (`--workspace`, `--all-targets`, `--locked`, `--offline`, `--release`, `--lib`, `-q`, bei `test` ein
    Testname), `dotnet test|build` (`--no-restore`, `--nologo`, `-c Debug|Release`, ein Projekt oder eine Lösung), `make test|check|lint|build`,
    `ruff check` (`--no-cache`, `-q`, Pfade), `mypy` (`--strict`, `--ignore-missing-imports`, Pfade), `tsc` nur mit `--noEmit` (`-p` mit
    relativer `.json`), `eslint` (`--quiet`, `--max-warnings N`, Pfade), `grep` (Kurzschalter `q c r n i E F w l s v`, Suchmuster, relative
    Dateien). Abgewiesen werden zum Beispiel `pytest -p`, `-c`, `--rootdir`, `-o`, `--junitxml`, `go test -exec` und `-toolexec`,
    `go vet -vettool`, `node --import` und `--require`, `dotnet -p:...`, `cargo --config`, `ruff check --fix` und `--add-noqa`, `eslint
    --fix`, `grep -f` und `-R`, jede Option mit `=`-Zuweisung an eine unbekannte Option sowie `-X` und `-D`;
  - höchstens 12 Argumente, jedes höchstens 200 Zeichen; keine `@datei`-Argumente; keine Netzwerkpfade (`//`, `\\`), keine Laufwerksangaben
    (`C:`) und keine Adressen (`://`): Sie werden schon beim Parsen abgewiesen und nie auf Vorhandensein geprüft, denn unter Windows löst
    schon `Path.exists()` auf `//host/share` eine SMB-Verbindung aus (NTLM-Hash an einen Fremdhost);
  - kein Programm mit Pfad, kein `.cmd` und `.bat`, nichts aus dem Projektordner selbst;
  - nur Pfade im Projektordner (kein `..`, kein `~`, kein vorhandener Pfad außerhalb, auch nicht über einen Link im Projekt). Die Prüfung der
    Pfade läuft in einem Arbeitsthread mit Frist von 2 Sekunden, nie in der Ereignisschleife des Sprachpfads;
  - jeder Befehl geht vorher durch den Wächter (`dev_guard.decide`, wie ein Bash-Aufruf des Auftrags): Sperre oder Freigabepflicht heißt
    "nicht erfüllt" mit Hinweis und der Befehl läuft nie. Eine Freigabekarte umgeht die Abnahme nie, und die Abnahme umgeht keine;
  - Zeitlimit 120 s, Ausgabe auf 2 KB gekürzt (das Ende bleibt, dort steht das Ergebnis), minimale Umgebung ohne Schlüssel und
    Verbindungsdaten, kein stdin, eigene Prozessgruppe, die bei Zeitlimit und Abbruch ganz beendet wird.

Die Startkarte des Auftrags in der Oberfläche zeigt die Abnahmekriterien als kurze Liste "Abnahme" (höchstens 5, je höchstens 300
Zeichen, bereinigt, Befehle in Festbreite, alles nur als Text, nie als Markup). Wer auf "Starten" klickt, bestätigt damit auch die Befehle,
die WAMO danach ausführt, also muss die Person sie vorher sehen.

Ablauf und Meldung: Nach Runde 1 steht der Vorgang auf "wird geprüft", alle Kriterien werden ausgewertet (jedes Mal alle, damit ein Rückschritt
auffällt) und in den Vorgang eingetragen (Hinweis je Kriterium, höchstens 200 Zeichen, Schlüssel geschwärzt, dazu "Ziel erreicht" und
"Runde n von m"). Sind alle erfüllt, ist der Auftrag fertig. Sonst, solange Runden und mindestens eine Minute Zeit übrig sind, setzt ein
neuer Lauf die Sitzung fort; sein Prompt nennt nur die offenen Kriterien samt Hinweisen. Sonst endet der Vorgang als Fehler mit
"Ziel nach n Runden nicht erreicht" (bei Zeitmangel mit "(Zeitlimit)") und den offenen Kriterien auf dem Canvas; WAMO sagt das ehrlich und
nie "fertig". Dem Sprachmodell gehen nur feste Zahlen ("0 von 1 Kriterien erfüllt, Runde 2 von 2"), nie die Hinweise. Ein Lauf, der selbst
scheitert (Zeitlimit, Verbindung, Zugang), wird weder geprüft noch wiederholt. Eine offene Freigabe hält die Schleife an (Status
"wartet auf Freigabe"); nach dem Klick läuft derselbe Auftrag weiter und wird danach geprüft. Lehnt die Person ab, endet der Auftrag. Das
Auflegen beendet die Schleife sofort samt laufendem Prüfbefehl. Jede Runde ist ein eigener Lauf und zählt wie bisher in den Verbrauch
(Kosten im Canvas); eine Kostengrenze gibt es noch nicht, nur Runden und Zeit.

**Befehlskriterien auf Sokrates-2 (vorbereitet am 10.11.2026, standardmäßig aus, nicht ausgerollt):** Der Auftrag läuft dort in der
Sandbox, die der Kern nicht betreten darf, und der Kern darf Befehle eines Modells nicht mit seinen eigenen Rechten ausführen. Ein
`befehl:` läuft deshalb über den Dispatcher-Befehl `pruefen` (`infra/sokrates2/wamo_job_dispatch.py` ab Version 6, Abschnitt 17 in
`infra/sokrates2/README.md`) in einer engeren Sandbox als der Auftragslauf: kein Netz, kein Token, kein Claude, 120 Sekunden. Die
Kern-Seite ist `voice-core/abnahme_remote.py`:

- **Voraussetzungen:** Der Dispatcher ist ab Version 6 eingerichtet (`abnahme_befehl.py` und `wamo_job_pruefen.py` liegen in
  `/opt/wamo/waechter`, der Rauchtest ist gelaufen) und hat `WAMO_PRUEFEN=an` in `/etc/wamo/geheim.env`. Der Kern hat
  `WAMO_ABNAHME_BEFEHLE=ja` und den vorhandenen SSH-Schlüssel der Aufträge; eine neue Umgebungsvariable gibt es nicht.
- **Reihenfolge beim Ausrollen:** erst der Dispatcher (Schalter aus), dann der Rauchtest, dann `WAMO_PRUEFEN=an`, dann der Kern, zuletzt
  `WAMO_ABNAHME_BEFEHLE=ja`. Zurück in umgekehrter Reihenfolge. Ein alter Kern gegen den neuen Dispatcher bleibt unverändert. Ein neuer
  Kern gegen den alten lehnt `befehl:` ab; wird der Dispatcher mitten in der Sitzung zurückgebaut, endet `pruefen` mit Exit 2 und
  das Kriterium gilt als nicht erfüllt.
- **Der ping entscheidet vorab:** `entwicklung_vorbereiten` fragt den Dispatcher per `ping`, aber nur, wenn ein `befehl:` im Ziel steht,
  und merkt sich die Antwort 5 Minuten (höchstens 5 Sekunden je Aufruf, ein Fehler gilt nur 30 Sekunden und heißt "nicht möglich", nie
  eine Ausnahme; der Aufruf läuft im Werkzeugaufruf mit Frist, nie im Audiopfad). `befehl:` geht nur, wenn `pruefen` gemeldet wird und die
  `abnahme_befehl_sha256` des Dispatchers der Prüfsumme der lokalen Datei `abnahme_befehl.py` gleicht (gleiche Allowlist). Sonst nennt die
  Ablehnung den Grund in einem Satz: "Schalter aus", "Dispatcher ohne pruefen", "Prüfsumme der Allowlist weicht ab" oder "Dispatcher
  nicht erreichbar". Das ist Komfort; maßgeblich ist der Dispatcher, der jeden Aufruf neu prüft. Die Beschreibung des Werkzeugs entsteht
  beim Anlegen des Gesprächs und nennt `befehl:` auch mit einem abgelaufenen Cache, wenn der letzte eindeutige Stand "möglich" war.
  Der Ping wird beim `attach` im Hintergrund aufgewärmt; im ersten Gespräch nach dem Start des Kerns kennt die Beschreibung ihn noch nicht
  und nennt `befehl:` nicht, erst ab dem nächsten Gespräch. Beim Vorbereiten selbst gilt immer der frische Stand.
- **Aufruf:** `ssh ... pruefen <job>` mit dem Zwangsbefehl-Schlüssel, **eine** stdin-Zeile `{"argv": [...]}` (höchstens 8 KiB), stdin bleibt
  als Lebenslinie offen, eine JSON-Zeile als Antwort (höchstens 64 KiB, 240 Sekunden). Die Allowlist prüft der Kern weiter vorab (schnelles
  Feedback, dazu Wächter und Netzwerkpfade), aber keinen lokalen Pfad; der Wächter bekommt den Projektpfad auf dem Server als Basis (mit
  `None` würde ein relativer Pfad wie `tests/x.py` gegen das Arbeitsverzeichnis des Kerns aufgelöst und als WAMO-Repo gesperrt).
- **Streng ausgewertet:** erfüllt nur bei Prozess-Exit 0 und `ok` true und einem echten `exit` 0 (kein bool, kein float) und `abgebrochen`
  false und `v` gleich 1 und genau den acht Feldern. Alles andere (kein oder ungültiges JSON, fehlende oder zusätzliche Felder, falsche
  Typen, eine zweite Zeile, Prozess-Exit 2, 3, 4, 6, 124, 130 oder 255, SSH-Fehler, Zeitüberschreitung) ist nicht erfüllt, mit einem Grund aus
  einer festen Liste (`abnahme_remote.GRUENDE`), nie mit Text aus der Antwort.
- **Ausgabe:** Ein Befehl, der lief und scheiterte, liefert Exit und Ausgabe als Datenfeld (höchstens 2 KiB, ohne Steuerzeichen, geschwärzt) in
  den Hinweis des Kriteriums, wie lokal. Sie geht nie an das Sprachmodell; dem gehen nur feste Zahlen.
- **Auflegen:** Der Kern beendet ssh (damit endet die Lebenslinie und der Dispatcher die Sandbox) und ruft zusätzlich `cancel <job>`. Ein
  einzelnes Zeitlimit ruft es nicht: `cancel` legt eine Abbruchmarke ab, die jede weitere Prüfung desselben Auftrags abweist.
- **Messbefund (10.11.2026, Sandbox des Benutzers `wamo` auf Sokrates-2):** Es gibt `python3` 3.12 (ohne pytest und ohne ruff), `node` 22,
  `npm`, `make`, `grep`, `eslint` und `git`. Es fehlen `pytest`, `ruff`, `mypy`, `tsc`, `pnpm`, `yarn`, `go`, `cargo` und `dotnet`; ein Befehl
  damit scheitert mit "nicht gefunden" und das Kriterium gilt als nicht erfüllt (kein Fehler des Dispatchers). Python-Projekte gehen als
  `uv run --no-sync --offline pytest ...`, nur wenn im Projekt eine `.venv` liegt (uv installiert und lädt dort nichts), sonst kommt
  "nicht möglich" mit Hinweis. Die Beschreibung des Werkzeugs sagt dem Modell das, damit es keine Kriterien ableitet, die nie erfüllbar sind.

**Grenzen:**

- **Betriebsart Sokrates-2 (der Standard):** Ohne `WAMO_ABNAHME_BEFEHLE=ja` im Kern, `WAMO_PRUEFEN=an` im Dispatcher oder einen passenden ping
  weist `entwicklung_vorbereiten` ein `befehl:` mit einem Satz ab, es bleiben Aussagekriterien. Der Weg ist **nicht an einem echten
  Server geprüft** (die Tests nutzen Attrappen für ssh); offen sind der Rauchtest des Dispatchers, die Prüfung der Sicherheit und die
  Messung, ob Selbsttest und Befehl zusammen in die 240 Sekunden des Kerns passen. Belegt der Auftrag die Sandbox noch (Exit 4), gilt das
  Kriterium als nicht erfüllt und kostet eine Runde.
- **Betriebsart `lokal`:** Der Code der Projekte (Tests, conftest, Makefile, Skripte) läuft ohne Sandbox mit den Rechten des Kerns, genau
  wie der Auftrag selbst. Allowlist und Wächter sind eine Bremse, keine Sandbox. Build-Werkzeuge können Abhängigkeiten aus dem Netz laden,
  das verhindert die Allowlist nicht. Deshalb ist die Zuschaltung (`WAMO_ABNAHME_BEFEHLE=ja`) eine bewusste Entscheidung des Betreibers.
- **Prozessbaum:** Zeitlimit und Abbruch beenden den ganzen Baum nur, solange der Hauptprozess des Befehls läuft. Ist er schon beendet,
  überleben Enkelprozesse, die er gestartet hat: Es gibt danach kein `killpg` auf die Gruppe, und unter Windows kein Job Object. Das ist
  bekannt und nicht umgebaut.
- **Nicht an einem echten Lauf geprüft:** Die Tests nutzen Attrappen für den Claude-Prozess und das beurteilende Modell; echte Programme
  wurden nicht gegen echte Projekte ausgeführt.

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

## Fernzugriff (Remote Desktop der Sparks)

Der Admin kann von unterwegs den Bildschirm von Sokrates-1, Sokrates-2 und Caesar öffnen (Konzept `docs/remote-desktop.md`,
Betrieb `infra/sokrates2/README.md` Abschnitt 16). Das Handy kommt über WireGuard ins Büronetz, der Zugang läuft über WAMO:

- **Einstieg:** Einstellungen, Abschnitt Fernzugriff (nur Admin). Karte mit den Zielen und ihrem Zustand (frei, belegt, nicht
  erreichbar). „Verbinden“ startet ohne Code, der Code der Anmeldung genügt (Entscheidung vom 11.11.2026), und öffnet die Sitzung
  in einem eigenen Tab. Nur wenn die Anmeldung zu alt ist (Stufenschalter `WAMO_RD_ANMELDUNG_MAX_MIN`), verlangt der Kern einen
  frischen Code und der Dialog zeigt das Codefeld. Die Karte zeigt die Restzeit und trennt auf Knopfdruck.
- **Grenzen:** 60 Minuten Höchstdauer, 10 Minuten Leerlauf, ein Zuschauer je Ziel, höchstens 3 Sitzungen gleichzeitig, 6 Starts
  je Stunde und Profil. Keine Zwischenablage, kein Laufwerk, kein Drucker, kein Ton, keine Aufzeichnung. Protokolliert werden nur
  wer, welches Ziel, Start, Ende und Grund (Tabelle `rd_sitzungen`, Migration 0015).
- **Ziele:** Die Zielliste liegt als root-Datei auf Sokrates-2 (`/etc/wamo-rd/ziele.toml`), aus der Anfrage kommt nur der Name.
  Angemeldet wird am Spark als Konto `wamo-rd` ohne sudo, docker und SSH. Auf Sokrates-2 darf dieses Konto per Ausgangsregel
  (`infra/sparks/wamo-rd-ausgang-einrichten.sh`) nur das Internet erreichen, nicht Kern, Datenbank, LAN oder andere Sparks.
- **Beim Einrichten eines Ziels gefunden (Ende-zu-Ende-Probe 1.11.):** Zwei Dinge verhinderten den Desktop, `wamo-rd-ziel-einrichten.sh`
  behebt beide. (1) xrdp kann mit `security_layer=tls` seinen Schlüssel nicht lesen, solange der Benutzer `xrdp` nicht in der
  Gruppe `ssl-cert` ist (im Gateway "Security negotiation failed", Fehler 769). (2) `Xsession.d/90xbrlapi` wartet auf einem
  Server ohne Braillezeile für immer, die Sitzung bleibt schwarz; das Konto startet deshalb mit eigener `~/startwm.sh` direkt
  `gnome-session --session=ubuntu`. Probe ohne Anmeldung am Kern: Anweisung als Dienstbenutzer des Kerns bauen, beim Zugangsdienst
  einreichen, Websocket-Tunnel öffnen, alle 4 Sekunden `nop` senden (sonst "User is not responding", Fehler 776) und die Prozesse
  von `wamo-rd` ansehen (`gnome-shell` muss laufen, es müssen hunderte `blob`-Anweisungen eintreffen).
- **Weitere Ziele (Sokrates-1, Caesar):** Dasselbe Konto und xrdp wie auf Sokrates-2, dazu Port 3389 nur für die LAN-Adresse von
  Sokrates-2 (`192.168.178.136/32`) und die Ausgangsregel mit `--gateway 192.168.178.136/32`. Das Passwort entsteht am Ziel und
  geht ohne Bildschirm und ohne Argument per Pipe an Sokrates-2 (`wamo-rd-konto-einrichten.sh --fern`, dort `--uebernehmen --ziel NAME`,
  Aufruf im Kopf des Skripts). Danach das Ziel in `/etc/wamo-rd/ziele.toml` eintragen, `sudo systemctl reload wamo-firewall` und
  `docker restart wamo-rd-web wamo-rd-zugang`. Braucht das Dienstkonto am Ziel ein sudo-Passwort (Caesar), richtet der Admin einmal
  `infra/sparks/wamo-sudo-einrichten.sh` ein: sudo ohne Passwort nur für feste Befehlszeilen von vier root-eigenen Skripten unter
  `/usr/local/lib/wamo-rd` (Rückweg `--zurueck`).
- **Einschalten:** `WAMO_RD_AKTIV=ja` im Kern, erst nach dem Einrichten des Gateways (`wamo-rd-einrichten.sh`) und mit den Proben
  aus `wamo_rd_rauchtest.py`. Ohne den Schalter melden die Routen, dass der Fernzugriff nicht eingerichtet ist.
- **Der Code beim Anmelden genügt (KP-2, Migration 0019):** Der Kern verlangt aal2 mit einem totp-Eintrag im Token, aber keinen frischen
  Code je Start mehr, und die Datenbank kennt weder die Frist von 120 Sekunden noch „ein Code, ein Start“. `WAMO_RD_ANMELDUNG_MAX_MIN`
  (Minuten, 1 bis 43200) begrenzt das Alter des Codes der Anmeldung nur für den Fernzugriff; leer oder 0 heißt aus (Standard), jeder
  ungültige Wert gilt als strengste Stufe (frischer Code wie früher) und schreibt eine Fehlerzeile. Zwei gleichzeitige Starts eines Profils
  laufen nacheinander, und nach dem Anmelden am Gateway prüft der Kern die Datenbankzeile noch einmal: Es bleibt höchstens ein Zuschauer je
  Ziel. **Restrisiko:** Ein gestohlenes Token startet jetzt allein Sitzungen, bis die Anmeldung abläuft (Kern 168 Stunden, Browser 24
  Stunden, App 7 Tage). Dagegen meldet jeder Start sich als Hinweis bei allen Admins (Titel „Fernzugriff gestartet: Ziel“); das ist eine
  Erkennung, kein Schutz (`docs/remote-desktop.md`, Abschnitt 3.6). Ein zweiter Tunnel mit demselben Sitzungstoken (zweiter Tab oder
  zweites Gerät) schließt die Sitzung nach 15 Sekunden Schonfrist.
- **Neustart immer gemeinsam:** `docker restart wamo-rd-web wamo-rd-zugang`. Der Zugangsdienst hält seinen Zustand nur im
  Speicher, Tokens und offene Tunnel liegen im Gateway. Nur den Zugangsdienst neu zu starten ließe Tunnel ohne Zeitlimit weiterlaufen.
- **Offen (bekannt, bewusst nicht behoben):** Die Zertifikatsprüfung gegen xrdp ist noch nicht angeheftet (`ignore-cert`, im LAN
  zwischen Gateway und Ziel könnte jemand mitlesen). Das Sitzungstoken steht in der Adresse und damit im Browserverlauf, bis die
  Sitzung endet. Der Kern prüft den Zugangsdienst am Socket nicht per Benutzerkennung (der Ordner gehört `wamo-rd-gateway:wamo-core`
  mit Modus 2770). Beim Ablösen einer eigenen Sitzung kann der alte Tunnel kurz weiterlaufen, wenn das Gateway nicht antwortet. Das
  Gateway ist nur im Büronetz und über WireGuard erreichbar.

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
- Stimmabdruck (nur Büro-Profile, nur mit ausdrücklicher Einwilligung, Abschnitt "Stimmabdruck"): Im Modus streng erreicht eine sicher fremde
  Stimme das Sprachmodell nicht, und Werkzeuge außer Uhrzeit, Websuche und Canvas verlangen die eingelernte Stimme oder getippten Text. Das ist keine
  Freigabe und kein Schutz gegen Aufnahmen oder geklonte Stimmen. Der Abdruck (192 Zahlen) liegt nur in `speakers`, nie eine Aufnahme.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
