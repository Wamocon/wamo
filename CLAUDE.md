# WAMO: Hinweise für Claude Code

WAMO ist der Sprachassistent der WAMOCON GmbH. Stand, offene Punkte und Ausbaustufen stehen in `README.md`.

Monorepo: `ui/` (Next.js 16), `voice-core/` (Python, Pipecat 1.x), `host/` (Python, MCP v1, Windows),
`desktop/` (Electron), `supabase/` (SQL-Migrationen mit `__SCHEMA__`-Platzhalter), `scripts/`.

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
