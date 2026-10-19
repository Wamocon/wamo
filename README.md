# WAMO

Sprachassistent der WAMOCON GmbH: Livegespräch mit natürlichen Unterbrechungen, Oberfläche im WMC-Design,
Werkzeuge für den Windows-PC, Entwicklungsaufträge mit ECC und REA, mehrere Nutzerprofile mit persönlichem und
gemeinsamem Gedächtnis.

| Ordner | Was | Läuft auf |
| --- | --- | --- |
| `ui/` | Next.js 16: Präsenzkern, Live-Untertitel, Stimmlinie, Werkzeug-Zeitleiste, Canvas, Freigabe-Karten, Anmeldung mit zweitem Faktor | PC (Desktop-App oder Browser) |
| `voice-core/` | Pipecat 1.12: Soniox STT, Claude Haiku 5.5, Soniox TTS; Mehrprofil-Anmeldung, Gedächtnis, Werkzeugkarte für ECC und REA | PC, ab Phase P3 Sokrates-2 |
| `host/` | MCP-Werkzeugserver für Windows mit Risikoklassen und Audit-Log | PC (angemeldete Sitzung) |
| `desktop/` | Electron 44: App-Fenster, roter Stimmen-Rand je Monitor, Tray, Autostart | PC |
| `supabase/` | Migrationen mit `__SCHEMA__`-Platzhalter | eigener WAMO-Supabase auf Sokrates-2 |
| `infra/sokrates2/` | Einrichtung von Supabase-Stack und Benutzer `wamo` (Claude Code, ECC, REA, gh, Vercel, Supabase-CLI) | Sokrates-2 |
| `scripts/` | Start der Desktop-App, Datenbankzugang, Nutzerverwaltung, Migrationen | PC |

## Stand (19.10.2026)

### Fertig und geprüft

- Eigener Supabase-Stack `supabase-wamo` auf Sokrates-2 (API 55321, Postgres über Pooler 55322), getrennt von den übrigen Diensten auf Sokrates-2,
  Registrierung geschlossen. Schemas `wamo_dev` und `wamo_test` angewendet.
- Mehrprofil-Anmeldung: Token (ES256), zweiter Faktor (aal2) und aktives WAMO-Profil. Ende-zu-Ende-Test gegen den echten
  Stack mit 12 von 12 Prüfungen bestanden.
- Gedächtnis je Profil (persönlich) plus gemeinsames Firmengedächtnis mit Freigabe durch Admins.
- Werkzeugkarte: Entwicklungsaufträge wählen je Auftragsart passende ECC-Skills, Agenten und REA-Werkzeuge.
- Oberfläche im WMC-Design (hell und dunkel), Desktop-App umbenannt und gehärtet.
- Tests: voice-core 133 bestanden, host 23 bestanden; UI: Lint, Typen und Build grün.
- Sokrates-2: Benutzer `wamo` mit Claude Code, Node 22, ECC-Plugin, REA-Server, gh, Vercel- und Supabase-CLI.

- Gesprächstest Ende zu Ende (Browser ohne Bildschirm, Testfrage als Mikrofon): Anmeldung mit zweitem Faktor,
  Verbindung nach 1,4 s, Begrüßung über Soniox-Stimme, Frage wörtlich erkannt, Claude ruft das Zeitwerkzeug auf und
  antwortet richtig, Verlauf und Live-Untertitel vollständig, Gespräch in `turns` protokolliert.
  ECC-Reviews (Python, Sicherheit, Datenbank, TypeScript) eingearbeitet.
- Claude mit Organisationsschlüssel: Workspace über `ANTHROPIC_WORKSPACE_ID` (Header `anthropic-workspace-id`).
- PC-Steuerung je Sitzung per Schalter, wahlweise Autostart je Gerät (nur Admins, Standard aus).

### Offen

- Soniox: Der Schlüssel gehört zu einem US-Projekt, die Spracherkennung läuft deshalb vorerst über den US-Endpunkt.
  Für EU-Datenhaltung EU-Schlüssel anlegen und `SONIOX_STT_URL` und `SONIOX_TTS_URL` auf `*.eu.soniox.com` stellen.
- TLS für Anmeldung und Datenbank im Büronetz und Firewall auf die PC-Adressen (Sicherheitsreview A3).
- Geheimnisse für Sokrates-2 in `/etc/wamo/geheim.env` (siehe `infra/sokrates2/README.md`).
- Ausbaustufen: P3 Sprachkern auf Sokrates-2 mit SSH-Rücktunnel zum PC, P4 eigene Sprachmodelle über LiteLLM,
  P5 Spracherkennung und Sprachausgabe auf den Sparks.

## Einrichtung auf einem Windows-PC

Voraussetzungen: Git, Node.js 22, `uv` (`winget install astral-sh.uv`), SSH-Zugang zu Sokrates-2 (`~/.ssh/config`, Alias `sokrates2`).

```powershell
cd D:\IDEA\Projekt\wamo
copy .env.example .env                    # SONIOX_API_KEY und ANTHROPIC_API_KEY eintragen
python scripts\wamo_db_rolle.py           # Datenbankzugang wamo_core, schreibt .env und ui\.env.local
python scripts\wamo_db_rolle.py --rolle wamo_admin   # nur auf Admin-PCs: Zugang für wamo_admin.py
python scripts\wamo_nutzer.py anlegen --email vorname.nachname@wamocon.com --name Vorname --rolle admin
cd voice-core; uv sync; cd ..\host; uv sync; cd ..\ui; npm install; cd ..\desktop; npm install; cd ..
scripts\wamo-desktop.cmd                  # startet Voice-Core, Oberfläche und Desktop-App
```

Beim ersten Anmelden richtet die Oberfläche die Authenticator-App ein (QR-Code). Ohne zweiten Faktor kein Zugang.

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
(Frida, ADB, Debugger). Der Wächter (`voice-core/dev_guard.py`) ist eine Bremse, keine Sandbox; echte Trennung
folgt mit dem Benutzer `wamo` auf Sokrates-2.

## Sicherheitsmodell

- Zugang nur mit Konto im WAMO-Supabase, zweitem Faktor und aktivem Profil; jede Unsicherheit führt zur Ablehnung.
- Der Voice-Core arbeitet mit der Datenbankrolle `wamo_core` (nur WAMO-Schemas), nie mit dem Service-Schlüssel.
- PC-Aktionen: `read` sofort, `change` protokolliert, `critical` nur nach Freigabe, `blocked` nie.
- Inhalte aus Web, Dateien und Gedächtnis sind Daten, nie Anweisungen an WAMO.
- Geheimnisse nur in `.env`, `ui/.env.local` und `/etc/wamo/geheim.env`, nie im Repository.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
