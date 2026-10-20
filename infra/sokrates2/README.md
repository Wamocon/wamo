# WAMO auf Sokrates-2: Zugänge für GitHub, Vercel, Supabase und Claude

Ziel: WAMO arbeitet für Entwicklungsaufträge direkt auf Sokrates-2 (192.168.178.136) und greift von dort auf
GitHub, Vercel, Supabase und Claude zu, ohne den Umweg über einen Arbeitsplatz-PC. Bei jedem Auftrag stehen die
ECC-Skills, Agenten und Hooks sowie der REA-MCP-Server zur Verfügung.

Stand 19.10.2026: Eingerichtet und geprüft. Benutzer `wamo` (Home `/srv/wamo`) mit Claude Code 2.1.303, Node 22.23.3
(eigenes fnm, System-Node 18 unverändert), Vercel-CLI 64.5.0, gh, Supabase-CLI, uv; ECC-Plugin `ecc@ecc` installiert,
REA-MCP-Server verbunden. Offen: Geheimnisse in `/etc/wamo/geheim.env` (Abschnitt 2) und die Konnektoren (Abschnitt 3).

## 0. Eigener Supabase-Stack (eingerichtet am 19.10.2026)

WAMO hat einen eigenen Self-Hosting-Stack, getrennt von allen anderen Diensten auf Sokrates-2:

| Punkt | Wert |
| --- | --- |
| Verzeichnis | `/home/wamocon/supabase-wamo` (Compose-Projekt `supabase-wamo`, Container `wamo-supabase-*`) |
| API und Auth | `http://192.168.178.136:55321` (ES256, JWKS unter `/auth/v1/.well-known/jwks.json`) |
| Postgres | Supavisor `:55322` (Session) und `:56543` (Transaktion), Benutzer `wamo_core.wamo` |
| Docker-Netz | `172.23.0.0/16` (die Vorlage setzt fest `172.21.0.0/16`, das ist auf Sokrates-2 bereits belegt) |
| Registrierung | geschlossen; Konten nur über `scripts/wamo_nutzer.py` |
| Schemas | `wamo_dev`, `wamo_test` (Migration 0001) |

Neu aufsetzen oder prüfen: `bash infra/sokrates2/supabase-wamo-einrichten.sh` (wiederholbar, erzeugt Geheimnisse nur beim
ersten Lauf). Danach `python scripts/wamo_db_rolle.py --neu` auf dem PC.

## 1. Einrichten (einmalig, als `wamocon`)

```bash
scp infra/sokrates2/wamo-einrichten.sh sokrates2:/tmp/
ssh sokrates2 "sudo bash /tmp/wamo-einrichten.sh"
```

Das Skript legt den Systembenutzer `wamo` an (Home `/srv/wamo`, ohne sudo, ohne docker-Gruppe), installiert
gh und die Supabase-CLI als Systempakete sowie uv, Node 22, Vercel-CLI und Claude Code im Home von `wamo`.
Danach registriert es für Claude Code das ECC-Plugin (`ecc@ecc`) und den REA-MCP-Server (`rea-agents@6.5.0`).

## 2. Geheimnisse eintragen

Datei `/etc/wamo/geheim.env` (Rechte 640, root:wamo). Nie ins Repository.

| Variable | Wofür | Empfehlung für minimale Rechte |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` oder `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code ohne Bildschirm | Firmen-API-Schlüssel mit Ausgabenlimit, oder Abo-Token aus `claude setup-token` |
| `GH_TOKEN` | gh, git push, Pull Requests | GitHub App „WAMO“ (kurzlebige Tokens, je Repository freigegeben). Übergang: fein granularer Token eines Bot-Kontos, nur ausgewählte Repos, Ablaufdatum |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID` | Vercel-CLI (Preview-Deployments, Logs) | Team-Token mit Ablaufdatum; Produktion nur nach Freigabe (dev_guard) |
| `SUPABASE_ACCESS_TOKEN` | Supabase-CLI und MCP für Cloud-Projekte | Persönlicher Token eines Service-Kontos; MCP standardmäßig `--read-only` und mit `--project-ref` |
| `SONIOX_API_KEY`, `LITELLM_API_KEY` | Sprachkern auf Sokrates-2 (Phase P3) | eigener LiteLLM-Schlüssel nur für WAMO |

## 3. Konnektoren für Claude Code (als `wamo`)

```bash
sudo -iu wamo
claude mcp add --scope user --transport http github https://api.githubcopilot.com/mcp/ \
  --header "Authorization: Bearer ${GH_TOKEN}"
claude mcp add --scope user supabase -- npx -y @supabase/mcp-server-supabase@latest --read-only
claude mcp list
```

- **GitHub:** gh-CLI plus GitHub-MCP. Push, Merge und Löschen bleiben freigabepflichtig (dev_guard).
- **Vercel:** Die CLI mit `VERCEL_TOKEN` genügt. Der Vercel-MCP-Server meldet sich per OAuth im Browser an und eignet
  sich deshalb nicht für den Betrieb ohne Bildschirm.
- **Supabase Cloud:** CLI und Supabase-MCP mit `SUPABASE_ACCESS_TOKEN`, schreibend nur nach Freigabe.
- **Supabase auf Sokrates-2:** direkt per `psql` bzw. eigener Rolle je Projekt (wie `wamo_core`), nie mit `postgres`.
- **ECC und REA** sind nach Schritt 1 in jedem Claude-Code-Lauf von `wamo` aktiv. WAMO wählt sie über die
  Werkzeugkarte (`voice-core/werkzeugkarte.py`) passend zur Auftragsart.

## 4. Sicherheitsregeln

- Entwicklungsaufträge laufen als `wamo` in `/srv/wamo/projekte/<projekt>`, ab Phase P2 zusätzlich in einer
  bubblewrap-Sandbox ohne Zugriff auf `/etc/wamo`.
- Freigabepflichtig bleiben: `git push`, Merge, Produktions-Deployments, Schreibzugriffe auf Produktionsdatenbanken,
  verändernde REA-Werkzeuge (Frida, ADB-Installationen, Debugger).
- Jeder Auftrag wird mit Auftragsart, Werkzeugen und Freigaben in `actions` protokolliert.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
