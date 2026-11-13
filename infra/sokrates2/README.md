# WAMO auf Sokrates-2: Zugänge für GitHub, Vercel, Supabase und Claude

Ziel: WAMO arbeitet für Entwicklungsaufträge direkt auf Sokrates-2 (192.168.178.136) und greift von dort auf
GitHub, Vercel, Supabase und Claude zu, ohne den Umweg über einen Arbeitsplatz-PC. Bei jedem Auftrag stehen die
ECC-Skills, Agenten und Hooks sowie der REA-MCP-Server zur Verfügung.

Stand 19.10.2026: Eingerichtet und geprüft. Benutzer `wamo` (Home `/srv/wamo`) mit Claude Code 2.1.303, Node 22.23.3
(eigenes fnm, System-Node 18 unverändert), Vercel-CLI 64.5.0, gh, Supabase-CLI, uv; ECC-Plugin `ecc@ecc` installiert,
REA-MCP-Server verbunden. Geheimnisse in `/etc/wamo/geheim.env` eingetragen (Abschnitt 2), Konnektoren für GitHub,
Supabase und Jira eingerichtet und in der Auftrags-Sandbox geprüft (Abschnitt 3).
Seit 19.10.2026 zusätzlich: HTTPS und Datenbank-TLS mit eigener CA (Abschnitt 5) und Firewall für die WAMO-Ports
(Abschnitt 6). CA auf dem PC WMC-H-04-NS importiert, Stufe 2 seit 19.10.2026 aktiv (Abschnitt 7). Weitere PCs
brauchen nur den Import (Abschnitt 5.2). Seit 19.10.2026 abends läuft der Voice-Core zusätzlich als Dienst `wamo-core`
auf Sokrates-2, die PC-Werkzeuge kommen über einen SSH-Rücktunnel (Phase P3, Abschnitt 10). Seit 21.10.2026 läuft auch die
Oberfläche als Container `wamo-ui` auf Sokrates-2 (HTTPS 55445), damit WAMO im Browser jedes Büro-Geräts nutzbar ist
(Abschnitt 12).

## 0. Eigener Supabase-Stack (eingerichtet am 19.10.2026)

WAMO hat einen eigenen Self-Hosting-Stack, getrennt von allen anderen Diensten auf Sokrates-2:

| Punkt | Wert |
| --- | --- |
| Verzeichnis | `/home/wamocon/supabase-wamo` (Compose-Projekt `supabase-wamo`, Container `wamo-supabase-*`) |
| API und Auth | `https://192.168.178.136:55443` (ES256, JWKS unter `/auth/v1/.well-known/jwks.json`), intern weiter `http://…:55321` (Abschnitt 5) |
| Postgres | Supavisor `:55322` (Session) und `:56543` (Transaktion) mit TLS, Benutzer `wamo_core.wamo`, `sslmode=verify-full` |
| Docker-Netz | `172.23.0.0/16` (die Vorlage setzt fest `172.21.0.0/16`, das ist auf Sokrates-2 bereits belegt) |
| Registrierung | geschlossen; Konten nur über `scripts/wamo_nutzer.py` |
| Schemas | `wamo_dev`, `wamo_test` (Migration 0001) |

Neu aufsetzen oder prüfen (wiederholbar, erzeugt Geheimnisse nur beim ersten Lauf; `SRC` ist Pflicht und zeigt auf den
Ordner eines offiziellen Supabase-Self-Hosting-Stacks als Vorlage):

```bash
ssh sokrates2 "SRC=<Ordner der Vorlage> bash ~/wamo-infra/supabase-wamo-einrichten.sh"
```

Danach `wamo-tls-einrichten.sh` und `wamo-firewall-einrichten.sh` (Abschnitte 5 und 6), zuletzt
`python scripts/wamo_db_rolle.py --neu` auf dem PC.

### Ablage der Skripte auf Sokrates-2

Skripte aus diesem Ordner liegen auf Sokrates-2 immer in `~/wamo-infra` im Home von `wamocon`, nie in `/tmp`. In
`/tmp` könnte ein anderer Benutzer (etwa der Agent-Benutzer `wamo`) den Ordner vorab anlegen oder Dateien austauschen,
die dann als root oder mit den Rechten der docker-Gruppe laufen. Die Skripte prüfen beim Start, dass ihr Ordner und
alles darin dem Aufrufer oder root gehört und weder für die Gruppe noch für alle beschreibbar ist.

```bash
ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
```

## 1. Einrichten (einmalig, als `wamocon`)

```bash
# Ordner wie oben unter "Ablage der Skripte" nach ~/wamo-infra kopieren, dann:
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-einrichten.sh"
```

Das Skript legt den Systembenutzer `wamo` an (Home `/srv/wamo`, ohne sudo, ohne docker-Gruppe), installiert
gh und die Supabase-CLI als Systempakete sowie uv, Node 22, Vercel-CLI und Claude Code im Home von `wamo`.
Danach registriert es für Claude Code das ECC-Plugin (`ecc@ecc`) und den REA-MCP-Server (`rea-agents@6.5.0`).

## 2. Geheimnisse eintragen

Datei `/etc/wamo/geheim.env` (Rechte 640, root:wamo). Nie ins Repository.

| Variable | Wofür | Empfehlung für minimale Rechte |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` oder `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code ohne Bildschirm | Firmen-API-Schlüssel mit Ausgabenlimit, oder Abo-Token aus `claude setup-token` |
| `WAMO_SERVERZUGANG` | `aus` sperrt den Rückfall auf den Claude-Zugang dieser Datei für Läufe ohne persönlichen Zugang (Standard `an`); Abschnitt 9, Persönlicher Claude-Zugang | `aus`, sobald jedes Profil seinen eigenen Zugang hinterlegt hat |
| `ANTHROPIC_WORKSPACE_ID` | Pflicht bei einem Organisationsschlüssel (`wrkspc_…`), wird als Header `anthropic-workspace-id` mitgeschickt | derselbe Wert wie in der `.env` des PCs |
| `WAMO_GH_APP_ID` | App-ID der GitHub-App (nur Ziffern); schaltet zusammen mit den beiden folgenden den App-Modus ein | Anleitung unten |
| `WAMO_GH_APP_INSTALLATION_ID` | Installation-ID der App in der Organisation (nur Ziffern) | Anleitung unten |
| `WAMO_GH_APP_SCHLUESSEL` | Pfad zum privaten Schlüssel der App, direkt in `/etc/wamo` (z. B. `/etc/wamo/gh-app.pem`) | Datei `root:wamo 0640`, RSA ab 2048 Bit, ohne Passwort; liegt außerhalb der Sandbox und wird dort nie eingehängt |
| `GH_TOKEN` | gh, git push, Pull Requests (nur ohne App-Modus) | Rückfall, solange die GitHub-App nicht eingerichtet ist; danach entfernen und in GitHub widerrufen (Anleitung unten, Schritt 7) |
| `GH_TOKEN_LESEN` | GitHub nur lesend für die Arten planung, review, analyse, sicherheit, reverse_engineering und dokumentation (kommt in der Sandbox als `GH_TOKEN` an; nur ohne App-Modus) | wie `GH_TOKEN`: Rückfall bis zur Umstellung. Fehlt er, bekommen diese Arten ohne App-Modus gar kein GitHub-Token |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID` | Vercel-CLI (Preview-Deployments, Logs) | Team-Token mit Ablaufdatum; Produktion nur nach Freigabe (dev_guard) |
| `SUPABASE_ACCESS_TOKEN` | Supabase-CLI und MCP für Cloud-Projekte | Persönlicher Token eines Service-Kontos; MCP standardmäßig `--read-only` und mit `--project-ref` |
| `JIRA_API_KEY`, `JIRA_EMAIL` | Jira-MCP (`wamocon.atlassian.net`), nur lesend | API-Token eines technischen Benutzers mit „Browse Projects“ auf die nötigen Projekte (Anleitung unten); `JIRA_EMAIL` ist dessen Anmeldeadresse |
| `SONIOX_API_KEY`, `LITELLM_API_KEY` | Sprachkern auf Sokrates-2 (Phase P3) | eigener LiteLLM-Schlüssel nur für WAMO; gelangen nie in die Auftrags-Sandbox |

App-Modus: Sind `WAMO_GH_APP_ID`, `WAMO_GH_APP_INSTALLATION_ID` und `WAMO_GH_APP_SCHLUESSEL` gesetzt und gültig, bekommt
jeder Lauf ein eigenes, kurzlebiges Token der App (Ablauf in Abschnitt 9); `GH_TOKEN` und `GH_TOKEN_LESEN` werden dann
nie mehr in eine Sandbox gegeben. Fehlen alle drei, gilt der Rückfall auf `GH_TOKEN` bzw. `GH_TOKEN_LESEN`. Ist nur ein
Teil gesetzt oder ein Wert ungültig (Datei fehlt, falsche Rechte, kein RSA-Schlüssel), startet kein Auftrag mit GitHub:
fail-closed, ohne Rückfall auf den persönlichen Token. Dasselbe gilt für einen Namen mit Präfix `WAMO_GH_`, der keiner
der drei Namen ist (Tippfehler): so schaltet ein Schreibfehler den App-Modus nicht still aus.

### GitHub-App einrichten: kurzlebige Token je Auftrag

Einmalig, etwa 15 Minuten. Der Name der App spielt keine Rolle und steht nirgends in WAMO. Eingerichtet sind Rechte,
Repo-Auswahl und Schlüsseldatei bereits (Schritte 1 bis 3, bei einer neuen App oder einer Neuerzeugung des Schlüssels
sind sie so zu wiederholen). Offen sind die Einträge in `geheim.env`, die Prüfung und das Abschaffen der persönlichen
Token (Schritte 5 bis 7).

1. **Rechte der App.** In GitHub die Einstellungen der App öffnen (Organisation, Settings, Developer settings, GitHub
   Apps, bei der App „Edit“) und unter „Permissions & events“ bei „Repository permissions“ nur setzen:
   „Contents: Read and write“, „Pull requests: Read and write“, „Metadata: Read-only“. Alle anderen Repository
   permissions sowie alle „Organization permissions“ und „Account permissions“ auf „No access“. Speichern. Fordert
   GitHub danach in der Installation eine Bestätigung der geänderten Rechte an, dort annehmen. Braucht die App für
   andere Zwecke mehr Rechte, für WAMO eine eigene App mit genau diesen Rechten anlegen.
2. **Nur gewünschte Repos.** Bei der Installation der App in der Organisation (Settings, GitHub Apps, „Configure“) unter
   „Repository access“ „Only select repositories“ wählen, nur die Repos auswählen, an denen Aufträge arbeiten dürfen,
   und speichern. Das WAMO-Repo selbst gehört nicht dazu (es ist für Aufträge gesperrt und soll auch per Token nicht
   erreichbar sein); der Dispatcher lehnt ein Token ohne Ziel-Repo ab, wenn die Installation „All repositories“ ist
   oder das WAMO-Repo enthält. Neue Repos legt ein Auftrag im App-Modus nicht selbst an (dafür fehlt bewusst das Recht
   „Administration“): Repo von Hand anlegen und hier hinzufügen.
3. **Privater Schlüssel (bei einer neuen App oder einer Neuerzeugung).** In den Einstellungen der App unter „General“,
   Abschnitt „Private keys“, „Generate a private key“ klicken; der Browser lädt eine `.pem`-Datei herunter. Vom PC
   (Git Bash) nach Sokrates-2 kopieren und dort ablegen (`<heruntergeladene Datei>` durch deren Pfad ersetzen):

   ```bash
   scp "<heruntergeladene Datei>" sokrates2:gh-app.pem
   ssh sokrates2 "chmod 600 ~/gh-app.pem && sudo mv ~/gh-app.pem /etc/wamo/gh-app.pem && sudo chown root:wamo /etc/wamo/gh-app.pem && sudo chmod 640 /etc/wamo/gh-app.pem && sudo ls -l /etc/wamo/gh-app.pem"
   ```

   Die Ausgabe muss `-rw-r----- 1 root wamo` zeigen. Danach die heruntergeladene Datei auf dem PC löschen und den
   Papierkorb leeren. Schlüssel der App, die nirgends mehr gebraucht werden, in GitHub unter „Private keys“ löschen.
4. **IDs ablesen.** Die App-ID steht in den Einstellungen der App unter „General“, Abschnitt „About“, als „App ID“
   (eine Zahl, etwa `5137887`). Die Installation-ID steht in der Adresse der Installation: Organisation, Settings,
   GitHub Apps, bei der App „Configure“; die Adresse endet auf `/settings/installations/<Zahl>`, diese Zahl ist die
   Installation-ID (etwa `166575545`).
5. **Eintragen.** `ssh -t sokrates2 "sudoedit /etc/wamo/geheim.env"` öffnen und drei Zeilen ergänzen (Datei bleibt
   `root:wamo 0640`, kein Neustart nötig, jeder Auftrag liest sie beim Start). So sehen die Werte aus:

   ```bash
   WAMO_GH_APP_ID=5137887
   WAMO_GH_APP_INSTALLATION_ID=166575545
   WAMO_GH_APP_SCHLUESSEL=/etc/wamo/gh-app.pem
   ```

6. **Prüfen.** `ssh sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-dispatch ping"` muss `"github": "app"` melden;
   steht dort `"token"`, hat ein Tippfehler im Namen aller drei Variablen den App-Modus nicht eingeschaltet (stiller
   Rückfall auf den persönlichen Token), steht dort `"fehler: …"`, nennt der Text den Grund (etwa falsche Rechte der
   Schlüsseldatei). Danach der Rauchtest: `ssh sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp
   --attrappe --app-echt"`. `--app-echt` arbeitet nur lesend und zeigt: Rechte der Installation (nur Contents und Pull
   requests schreibend, Metadata lesend), nur ausgewählte Repos, ein Lesetoken für die Installation listet die Repos
   (das WAMO-Repo ist nicht dabei),
   ein Lesetoken nur für ein Repo sieht genau dieses, liest es und seine Pull Requests, ein anderes privates Repo
   bleibt unsichtbar, beide Token sind nach dem Widerruf ungültig. Alle Zeilen müssen `[OK]` zeigen.
7. **Persönliche Token abschaffen.** Mit `ssh -t sokrates2 "sudoedit /etc/wamo/geheim.env"` die Zeilen `GH_TOKEN=` und
   `GH_TOKEN_LESEN=` löschen, den Rauchtest aus Schritt 6 wiederholen (wieder alles `[OK]`). Dann in GitHub den
   persönlichen Token widerrufen: Profilbild, Settings, Developer settings, Personal access tokens (Fine-grained
   tokens bzw. Tokens (classic)), den Token öffnen und „Delete“ bzw. „Revoke“. Steht derselbe Token noch an anderer
   Stelle (etwa in der `.env` des PCs für die Betriebsart lokal), dort ebenfalls entfernen.

Hinweise zum Betrieb:

- Ein Token lebt höchstens eine Stunde. Aufträge, die länger als 60 Minuten laufen (`ZEITLIMIT_S` in `wamo-job.conf` ist
  Standard 1800, Obergrenze 7200), verlieren danach den GitHub-Zugang.
- Fehlt beim Auftrag das Ziel-Repo, gilt das Token für alle Repos der Installation (mit den Rechten der Art). Das
  Sprachmodell setzt `repo`, wenn der Auftrag ein Repo nennt; nennt der Auftragstext mehrere Repos, fragt WAMO nach.
- `contents: write` erlaubt Push in die Zielrepos, `pull_requests: write` auch das Mergen eines Pull Requests über die
  API. Der stärkste Schutz ist ein Ruleset für den Standardbranch jedes Zielrepos: kein direkter Push, Pull Request
  Pflicht, mindestens eine Genehmigung durch einen Menschen, die App nicht als „Bypass actor“ eintragen. Ohne diese Regel
  kann ein Auftrag seinen eigenen Pull Request mergen. Der Wächter verlangt für `git push` ohnehin eine Freigabe, das
  Token selbst bleibt aber in der Sandbox nutzbar (der Ausgangsfilter lässt Port 443 offen).
  **Voraussetzung:** Rulesets und Branch-Schutz gibt es für private Repos erst ab dem Tarif GitHub Team (Stand
  22.10.2026: die Organisation Wamocon nutzt den kostenlosen Tarif, GitHub antwortet mit „Upgrade to GitHub Pro“).
  Ohne Upgrade bleibt das Restrisiko: Ein gekaperter Auftrag könnte in den Zielrepos direkt auf den Standardbranch
  pushen. Abmildern: die App nur auf die Repos installieren, die WAMO braucht, schreibende Auftragsarten nur nach
  Freigabe starten, Pushes und Merges in den Zielrepos regelmäßig prüfen (GitHub, Aktivität) und bei Auffälligkeiten
  die Installation der App sofort entfernen.
- Der Schlüssel ist das eigentliche Geheimnis (er stellt Token für alle Rechte der App aus): nur in `/etc/wamo`, nie in
  die Sandbox, nie in ein Repository, bei Verdacht in GitHub löschen und neu erzeugen.
- Der GitHub-MCP-Server in der Sandbox verbindet sich mit dem App-Token. Meldet `--mcp` github als nicht verbunden,
  nimmt der MCP-Server dieses Token nicht an; `gh` und `git` im Auftrag sind davon nicht betroffen.

### Jira: technischer Benutzer statt persönlichem Schlüssel

Der Jira-Konnektor läuft im Nur-Lese-Modus (`READ_ONLY_MODE=true`) und braucht deshalb nur Leserechte. Umstellung:

1. In der Atlassian-Verwaltung einen eigenen Benutzer für WAMO anlegen (eigene Postfachadresse, nicht die eines
   Mitarbeiters) und ihm Zugriff auf das Produkt Jira geben, keine Administratorrolle.
2. In Jira je Projekt, das Aufträge lesen sollen, dem Benutzer (oder einer Gruppe, in der nur er steht) die Berechtigung
   „Browse Projects“ geben, über das Berechtigungsschema oder eine Projektrolle. Mehr ist nicht nötig: kein Anlegen,
   Bearbeiten, Kommentieren oder Löschen. Projekte ohne diese Berechtigung bleiben für Aufträge unsichtbar.
3. Als dieser Benutzer unter id.atlassian.com, Sicherheit, ein API-Token mit Ablaufdatum erzeugen.
4. Auf Sokrates-2 in `/etc/wamo/geheim.env` die beiden Werte ersetzen (Datei bleibt root:wamo 0640):
   `JIRA_EMAIL=<Adresse des technischen Benutzers>` und `JIRA_API_KEY=<neues API-Token>`. Ein Neustart ist nicht
   nötig, jeder Auftrag liest `geheim.env` beim Start.
5. Prüfen: `ssh sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp"` meldet jira als verbunden (Art
   umsetzung). Danach das alte persönliche API-Token unter id.atlassian.com widerrufen.

## 3. Konnektoren für Claude Code (als `wamo`)

Eingetragen am 19.10.2026. In `/srv/wamo/.claude.json` stehen nur Platzhalter (`${NAME:-}`), nie Werte. Die Werte kommen
erst zur Laufzeit aus der Umgebung des Auftrags; welche Token eine Auftragsart bekommt, legt `ZUGANG_VARIABLEN` in
`voice-core/werkzeugkarte.py` fest. Fehlt ein Token, startet der Konnektor in diesem Auftrag ohne Zugang. Versionen sind
fest gepinnt und liegen schreibgeschützt in der Sandbox (kein Download je Auftrag).

```bash
sudo -iu wamo
npm install -g @supabase/mcp-server-supabase@0.13.0
uv tool install mcp-atlassian==0.23.1
claude mcp add-json --scope user github '{"type":"http","url":"https://api.githubcopilot.com/mcp/","headers":{"Authorization":"Bearer ${GH_TOKEN:-}"}}'
claude mcp add-json --scope user supabase '{"type":"stdio","command":"npx","args":["-y","@supabase/mcp-server-supabase@0.13.0","--read-only"]}'
claude mcp add-json --scope user jira '{"type":"stdio","command":"/srv/wamo/.local/bin/mcp-atlassian","args":[],"env":{"JIRA_URL":"https://wamocon.atlassian.net","JIRA_USERNAME":"${JIRA_EMAIL:-}","JIRA_API_TOKEN":"${JIRA_API_KEY:-}","READ_ONLY_MODE":"true"}}'
```

Geprüft in der Sandbox mit der Umgebung echter Auftragsarten (`claude mcp list`): GitHub verbunden; Supabase verbunden
bei `datenbank`, ohne Zugang bei `umsetzung` (so gewollt); Jira startet, die Anmeldung braucht zusätzlich `JIRA_EMAIL`.

- **GitHub:** gh-CLI plus GitHub-MCP. Push, Merge und Löschen bleiben freigabepflichtig (dev_guard).
- **Jira:** mcp-atlassian im Nur-Lese-Modus (`READ_ONLY_MODE=true`). Der Wächter erlaubt lesende Werkzeuge
  (`jira_get_*`, `jira_search`, `confluence_get_*`), schreibende brauchen eine Freigabe, Löschen ist gesperrt.
- **Vercel:** Die CLI mit `VERCEL_TOKEN` genügt. Der Vercel-MCP-Server meldet sich per OAuth im Browser an und eignet
  sich deshalb nicht für den Betrieb ohne Bildschirm.
- **Supabase Cloud:** CLI und Supabase-MCP mit `SUPABASE_ACCESS_TOKEN`, schreibend nur nach Freigabe.
- **Supabase auf Sokrates-2:** direkt per `psql` bzw. eigener Rolle je Projekt (wie `wamo_core`), nie mit `postgres`.
- **ECC und REA** sind nach Schritt 1 in jedem Claude-Code-Lauf von `wamo` aktiv. WAMO wählt sie über die
  Werkzeugkarte (`voice-core/werkzeugkarte.py`) passend zur Auftragsart.

## 4. Sicherheitsregeln

- Entwicklungsaufträge laufen als `wamo` in `/srv/wamo/projekte/<projekt>` in einer bubblewrap-Sandbox ohne Zugriff
  auf `/etc/wamo` (eingerichtet mit `wamo-job-einrichten.sh`, Start nur über den Dispatcher per SSH-Schlüssel
  `wamo-jobs`, Einzelheiten in Abschnitt 9).
- Freigabepflichtig bleiben: `git push`, Merge, Produktions-Deployments, Schreibzugriffe auf Produktionsdatenbanken,
  verändernde REA-Werkzeuge (Frida, ADB-Installationen, Debugger).
- Jeder Auftrag wird mit Auftragsart, Werkzeugen und Freigaben in `actions` protokolliert.

## 5. TLS mit eigener CA (eingerichtet am 19.10.2026)

| Punkt | Wert |
| --- | --- |
| API und Anmeldung | `https://192.168.178.136:55443`, ebenso `https://sokrates2:55443` und `https://spark-651f.local:55443` |
| HTTPS-Vorbau | Container `wamo-caddy` (offizielles Image `caddy:2.11.2-alpine`, per Digest festgehalten, arm64), Compose-Projekt `wamo-caddy` in `/home/wamocon/wamo-tls/caddy`, Netz `172.24.0.0/24`, leitet an Envoy `:55321` weiter (WebSockets für Realtime, große Header, Streaming) |
| Interne CA | `WAMO interne CA` (ECDSA P-256, Seriennummer `3B46F704DE363947B4CBDDA4283539B9B0A80514`, gültig bis 16.10.2036). Root mit Schlüssel nur in `/home/wamocon/wamo-tls/ca` auf dem Host (Rechte 700/600), nie im Container |
| Zwischenzertifikat | `WAMO interne CA Caddy` (pathlen 0, dieselben Beschränkungen, gültig bis 18.10.2028), mit Schlüssel in `/home/wamocon/wamo-tls/caddy/ca`, im Container unter `/ca` lesbar. Daraus stellt Caddy je Adresse ein kurzlebiges Serverzertifikat aus und erneuert es selbst |
| Öffentliches Root-Zertifikat | `infra/sokrates2/wamo-ca.crt` (nie ein Schlüssel im Repo) |
| Fingerabdruck | SHA-1 `9F:5D:E3:7F:85:5F:D0:83:14:2E:9E:14:8A:09:54:EA:B2:81:72:00`, SHA-256 `A9:7E:9E:DA:3D:9E:C6:74:32:B0:59:7E:2F:16:AC:FB:45:5F:80:7F:3F:C5:88:21:55:6F:E3:5E:E6:9C:E0:23` |
| Begrenzung der CA | Name Constraints (kritisch) nur für IP 192.168.178.136 und 127.0.0.1 sowie die Namen `sokrates2`, `spark-651f.local`, `localhost`, `supavisor`, `wamo-supabase-pooler`; Verwendungszweck nur Serverauthentifizierung. Ein PC, der der CA vertraut, akzeptiert von ihr also nur Serverzertifikate für genau diese Adressen; Zertifikate dieser CA für andere Adressen oder Webseiten lehnt er ab. Das gilt für Clients, die Name Constraints auswerten (Windows, Chrome, Edge, OpenSSL und damit Python). |
| Token-Aussteller | GoTrue `API_EXTERNAL_URL` und `SUPABASE_PUBLIC_URL` zeigen auf die HTTPS-Adresse, Tokens tragen `iss = https://192.168.178.136:55443/auth/v1`. Der Voice-Core prüft gegen `SUPABASE_URL/auth/v1` (Ausnahme nur über `WAMO_AUTH_ISSUER`). |
| Datenbank | Supavisor nimmt auf 55322 und 56543 TLS an (Zertifikat der WAMO-CA, RSA 2048, SAN nur `192.168.178.136`, `supavisor`, `wamo-supabase-pooler`, gültig bis 20.11.2027, Ergänzung `docker-compose.wamo-tls.yml` über `COMPOSE_FILE`) |
| IPv6 | Dieselbe Ergänzung veröffentlicht Envoy (55321) und Supavisor (55322, 56543) nur auf `0.0.0.0`; auch `wamo-caddy` (55443) lauscht nur auf IPv4. ufw sperrt IPv6 eingehend zusätzlich (`IPV6=yes`). |
| HTTP 55321 | bleibt in Stufe 1 für das Büro-LAN offen (Übergang bis zum Import auf allen PCs), Stufe 2 sperrt es (Abschnitt 7) |

Die erste CA vom 19.10.2026 (Seriennummer `7316231A89474689E011E0B5F30B2C2F636B1B92`) war zu weit gefasst
(ganzes Büro-LAN, `*.local`, `*.fritz.box`, ohne Verwendungszweck) und ist ersetzt; ihr Schlüssel ist gelöscht.

### 5.1 Einrichten und prüfen (wiederholbar)

```bash
# Ordner nach ~/wamo-infra kopieren (Abschnitt 0, "Ablage der Skripte"), dann:
ssh sokrates2 "bash ~/wamo-infra/wamo-tls-einrichten.sh"
scp sokrates2:/home/wamocon/wamo-tls/ca/wamo-ca.crt infra/sokrates2/wamo-ca.crt   # nur nach einer neuen CA
python scripts/wamo_db_rolle.py   # auf jedem PC: SUPABASE_URL, WAMO_CA_FILE, sslmode=verify-full, ui/.env.local
```

Das Skript lässt eine passende CA unverändert. Entspricht sie nicht mehr den Vorgaben, bricht es ab; eine neue CA gibt
es nur ausdrücklich mit `--neue-ca` (danach Import auf allen PCs). Zwischenzertifikat und Pooler-Zertifikat stellt es
neu aus, wenn sie fehlen, nicht zur CA passen oder in weniger als 60 Tagen ablaufen. Neu erstellt werden nur
WAMO-Container, deren Konfiguration sich geändert hat. Am Ende prüft es Health, JWKS und Aussteller über HTTPS, die
Kette für alle drei Adressen, TLS an beiden Pooler-Ports und dass kein WAMO-Port über IPv6 lauscht. Die kurzzeitige
Sicherung `/home/wamocon/supabase-wamo/.env.vor-tls` (enthält Geheimnisse) löscht es nach erfolgreicher Prüfung;
bricht ein Lauf ab, bleibt sie liegen und ist zu löschen, sobald der Stack wieder läuft.

**Erinnerung:** Das Pooler-Zertifikat läuft am 20.11.2027 ab, das Zwischenzertifikat am 18.10.2028. Ein erneuter Lauf
des Skripts in den letzten 60 Tagen davor (also ab Ende September 2027) stellt beide neu aus, ohne Import auf den PCs.

Der Voice-Core braucht keinen Import in Windows: `WAMO_CA_FILE` (gesetzt von `scripts/wamo_db_rolle.py`) gilt für
JWKS, GoTrue und Postgres (`voice-core/wamo_tls.py`). Für alle anderen Verbindungen bleibt alles beim Alten.

### 5.2 Import auf einem Windows-PC (macht Niko selbst)

Browser und Desktop-App vertrauen der Anmeldung über HTTPS erst nach dem Import. Bis dahin meldet die Oberfläche
einen Zertifikatsfehler bei der Anmeldung. In einer normalen Eingabeaufforderung (keine Administratorrechte nötig):

```bat
rem Falls schon die erste, ersetzte CA importiert wurde: zuerst entfernen (Meldung "nicht gefunden" ist dann unkritisch)
certutil -user -delstore Root 7316231a89474689e011e0b5f30b2c2f636b1b92
certutil -user -addstore Root D:\IDEA\Projekt\wamo\infra\sokrates2\wamo-ca.crt
```

Windows fragt mit einer Sicherheitswarnung nach. Vor dem Bestätigen den angezeigten Fingerabdruck (sha1) mit
`9F5DE37F 855FD083 142E9E14 8A0954EA B2817200` vergleichen. Danach Browser und WAMO-Desktop neu starten und
`https://192.168.178.136:55443/auth/v1/health` öffnen: Es erscheint eine kurze JSON-Antwort ohne Warnung.

- Chrome, Edge und die Desktop-App (Electron) nutzen den Windows-Speicher. Firefox nur mit
  `security.enterprise_roots.enabled = true`.
- Kontrolle: `certutil -user -store Root 3b46f704de363947b4cbdda4283539b9b0a80514`
- Entfernen: `certutil -user -delstore Root 3b46f704de363947b4cbdda4283539b9b0a80514`
- `curl` unter Windows (Schannel) bricht ohne Sperrliste ab; dort `--ssl-revoke-best-effort` ergänzen.
- Bis zum Import lässt sich die Oberfläche übergangsweise mit `NEXT_PUBLIC_SUPABASE_URL=http://192.168.178.136:55321`
  in `ui/.env.local` betreiben (nur in Stufe 1). Der Voice-Core bleibt dabei auf HTTPS, er vertraut der CA über
  `WAMO_CA_FILE`; die Tokens tragen ohnehin den HTTPS-Aussteller.

### 5.3 Entscheidung Datenbank-TLS

Supavisor 2.9.12 unterstützt TLS für eingehende Verbindungen sauber: `GLOBAL_DOWNSTREAM_CERT_PATH` und
`GLOBAL_DOWNSTREAM_KEY_PATH` (gelesen in `releases/2.9.12/runtime.exs`, verwendet im ClientHandler beim SSLRequest).
Der Aufwand war gering (eine Compose-Ergänzung, ein Zertifikat der WAMO-CA), deshalb ist TLS eingerichtet:

- Clients mit `sslmode=prefer` (Standard von psql und asyncpg) verbinden sich weiter, jetzt verschlüsselt.
- WAMO verlangt `sslmode=verify-full`: asyncpg prüft das Zertifikat gegen `WAMO_CA_FILE` und die IP-Adresse.
- Geprüft mit `openssl s_client -starttls postgres` (TLS 1.3) und den Integrationstests
  `voice-core/test_integration_db.py::PoolerTlsTests` (ohne WAMO-CA wird das Zertifikat abgelehnt).
- Die Firewall begrenzt 55322 und 56543 zusätzlich auf das Büro-LAN. Ab Phase P3 läuft der Voice-Core auf
  Sokrates-2 und braucht die Pooler-Ports im LAN nicht mehr.

## 6. Firewall für die WAMO-Ports (eingerichtet am 19.10.2026)

Docker veröffentlicht Ports per DNAT an ufw vorbei. Wirksam ist deshalb nur die Kette `DOCKER-USER`. Die Regeln
vergleichen den ursprünglichen Zielport über conntrack, weil nach dem DNAT der Containerport im Paket steht.
Andere Ports und Dienste auf Sokrates-2 bleiben unberührt.

| Port | Dienst | Erlaubt (Stufe 1) | Stufe 2 |
| --- | --- | --- | --- |
| 55321 | API und Auth über HTTP (Envoy) | 127.0.0.0/8, 172.24.0.0/24 (Caddy, nur über eine Docker-Bridge), 192.168.178.0/24 | ohne Büro-LAN |
| 55443 | API und Auth über HTTPS (Caddy) | 127.0.0.0/8, 192.168.178.0/24 | unverändert |
| 55444 | WAMO-Kern über HTTPS (Caddy vor dem Unix-Socket, Abschnitt 10) | 127.0.0.0/8, 192.168.178.0/24 | unverändert |
| 55445 | WAMO-Oberfläche über HTTPS (Caddy vor dem Container `wamo-ui`, Abschnitt 12) | 127.0.0.0/8, 192.168.178.0/24 | unverändert |
| 55322 | Postgres Session (Supavisor, TLS) | 127.0.0.0/8, 192.168.178.0/24 | unverändert |
| 56543 | Postgres Transaktion (Supavisor, TLS) | 127.0.0.0/8, 192.168.178.0/24 | unverändert |

Alles andere wird verworfen und (begrenzt auf 6 je Minute) mit `WAMO-FW verworfen:` im Kernel-Log vermerkt.
Andere Docker-Netze erreichen die WAMO-Ports nicht (die Pooler-Ports und 55443 bekommen Docker-Netze erst mit
Phase P3). Ein Absender aus 172.16.0.0/12, der nicht über eine Docker-Bridge (`br-*`) kommt, gilt als gefälscht und
wird vor allen Erlaubnissen verworfen. Docker selbst verwirft in der raw-Tabelle (PREROUTING) Pakete an Container-IPs
aus 172.23.0.0/16 und 172.24.0.0/24, die nicht über die jeweilige Bridge kommen (am 19.10.2026 lesend geprüft).
192.168.178.0/24 umfasst die WireGuard-Clients der Fritzbox. Das ConnectX-Netz der Sparks (10.100.104.0/24,
10.100.105.0/24) erreicht die WAMO-Ports bewusst nicht. Über IPv6 sind die WAMO-Ports gar nicht veröffentlicht
(Abschnitt 5), ufw sperrt IPv6 eingehend zusätzlich.

```bash
# Ordner nach ~/wamo-infra kopieren (Abschnitt 0, "Ablage der Skripte"), dann:
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-firewall-einrichten.sh"   # Prüfung, Sicherung, Skript, Unit, reload
ssh sokrates2 "sudo systemctl reload wamo-firewall"                    # Regeln neu setzen, atomar, ohne Lücke
ssh sokrates2 "sudo wamo-firewall status"                              # Regeln mit Zählern
ssh sokrates2 "sudo journalctl -k | grep 'WAMO-FW verworfen'"          # verworfene Zugriffe
```

| Datei auf Sokrates-2 | Zweck |
| --- | --- |
| `/usr/local/sbin/wamo-firewall` | Regeln setzen, entfernen, anzeigen (`start`, `stop`, `status`, `regeln` als Probelauf). `start` legt `DOCKER-USER` bei Bedarf selbst an und hält den Sprung nach `WAMO-FW` genau einmal an Position 1 |
| `/etc/systemd/system/wamo-firewall.service` | `Type=oneshot`, `Before=docker.service`, aktiviert für `multi-user.target` und `docker.service`: Die Regeln stehen beim Systemstart und bei jedem Start von Docker, bevor Docker Ports veröffentlicht. Ohne `Requires`/`PartOf`: Stoppt oder startet Docker neu, bleiben die Regeln stehen. Nur ein ausdrückliches `systemctl stop wamo-firewall` entfernt sie |
| `/etc/wamo/firewall.conf` | `WAMO_FW_HTTP_NUR_INTERN=nein` (Stufe 1) oder `ja` (Stufe 2); andere Werte lehnt das Skript ab und ändert dann nichts |
| `/var/backups/wamo-firewall/` | Sicherungen `iptables-save` und `ip6tables-save` mit Zeitstempel; die erste (`*-20261019-134351.rules`) ist der Stand vor der WAMO-Firewall |

Änderungen immer mit `systemctl reload wamo-firewall` übernehmen, nicht mit `restart`: `restart` entfernt die Regeln
kurz, `reload` ersetzt sie in einem Schritt.

Hinweis für Phase P3: Ein Container im Netz `supabase-wamo_default` erreicht die eigenen veröffentlichten Ports über
die Host-Adresse nicht (das blockiert ufw, unabhängig von dieser Firewall). Der Voice-Core nutzt dort die
Dienstnamen im Docker-Netz oder läuft in einem eigenen Netz und spricht `https://192.168.178.136:55443` an.

## 7. Stufe 2: HTTP nur noch intern (aktiv seit 19.10.2026)

Sobald auf allen PCs `wamo-ca.crt` importiert ist und `SUPABASE_URL` sowie `NEXT_PUBLIC_SUPABASE_URL` auf
`https://192.168.178.136:55443` zeigen, lässt sich Port 55321 im LAN schließen. Danach bleibt er nur für Loopback
und das Caddy-Netz offen (Caddy; `scripts/wamo_nutzer.py` spricht auf dem Server über Loopback und läuft nicht durch
`DOCKER-USER`); Supabase Studio dann über HTTPS aufrufen. Das Skript wechselt die Regeln per `systemctl reload`.

```bash
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-stufe2-http-intern.sh --ja"        # einschalten
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-stufe2-http-intern.sh --zurueck"   # zurück zu Stufe 1
```

Prüfen vom PC: `curl -s -m 5 http://192.168.178.136:55321/auth/v1/health` läuft in eine Zeitüberschreitung,
`curl --ssl-revoke-best-effort https://192.168.178.136:55443/auth/v1/health` antwortet.

## 8. Rückweg

| Was | Befehl |
| --- | --- |
| Firewall abschalten | `ssh sokrates2 "sudo bash ~/wamo-infra/wamo-firewall-rueckweg.sh"` (mit `--entfernen` auch Skript, Unit und Einstellung löschen) |
| TLS zurücknehmen | `ssh sokrates2 "bash ~/wamo-infra/wamo-tls-rueckweg.sh"`: GoTrue wieder auf HTTP, Pooler ohne TLS, Ports wieder auch auf `[::]` (ufw sperrt IPv6), `wamo-caddy` entfernt; CA und Zertifikate bleiben liegen |
| Clients nach TLS-Rückweg | in `.env` `SUPABASE_URL=http://192.168.178.136:55321`, in beiden DSNs `?sslmode=verify-full` entfernen und `WAMO_CA_FILE` leeren, in `ui/.env.local` `NEXT_PUBLIC_SUPABASE_URL` entsprechend |
| Sicherung der `.env` | `/home/wamocon/supabase-wamo/.env.vor-tls` gibt es nur während eines Laufs von `wamo-tls-einrichten.sh`; nach erfolgreicher Prüfung löscht das Skript sie. Bleibt sie nach einem Abbruch liegen, nach der Reparatur löschen |
| Ausgangsfilter der Aufträge abschalten | `ssh sokrates2 "sudo bash ~/wamo-infra/wamo-egress-rueckweg.sh"` (mit `--entfernen` auch Unit und Regeldatei); danach startet kein Auftrag mehr (Selbsttest) |
| sshd-Drop-in für wamo zurücknehmen | `ssh sokrates2 "sudo rm /etc/ssh/sshd_config.d/wamo.conf && sudo sshd -t && sudo systemctl reload ssh"`; ohne Drop-in sucht sshd den Schlüssel wieder unter `/srv/wamo/.ssh`, der Auftragsschlüssel geht dann nicht mehr |
| Kern auf Sokrates-2 abschalten | `ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-rueckweg.sh"` (Dienst aus, Rolle `wamo_core_s2` nologin); mit `--entfernen` auch Code, venv, `core.env`, Benutzer, Rolle |
| HTTPS 55444 zurücknehmen | `ssh sokrates2 "bash ~/wamo-infra/wamo-core-caddy.sh --zurueck"` (Site-Datei weg, `caddy reload`; Port und Mount bleiben im Compose, ohne Site lauscht Caddy dort nicht) |
| Oberfläche abschalten | `ssh sokrates2 "bash ~/wamo-infra/wamo-ui-rueckweg.sh"` (Container, Caddy-Site und Drop-in weg; mit `--neustart` startet wamo-core ohne Sitzung neu, mit `--entfernen` auch Images, Einstellungen, Quellen). Port 55445 bleibt im Compose von `wamo-caddy` und in der Firewall (ohne Site lauscht Caddy dort nicht) |
| Tunnel abschalten | `ssh sokrates2 "sudo bash ~/wamo-infra/wamo-tunnel-rueckweg.sh"` (Schlüssel und Drop-in weg, `sshd -t`, Sitzungen beendet); mit `--entfernen` auch Benutzer `wamo-tunnel`. Auf dem PC: `scripts\wamo-tunnel-entfernen.ps1 [-Alles]` |
| Rücktunnel je Gerät abschalten | `ssh sokrates2 "sudo bash ~/wamo-infra/wamo-pc-tunnel-rueckweg.sh"` (Zeitgeber aus, alle Schlüssel gesperrt, Sitzungen beendet, Drop-in weg, `sshd -t`); mit `--entfernen` auch Programme, Units und Konfiguration. Die Benutzer `wamo-t-NN` bleiben gesperrt bestehen (keine uid-Wiederverwendung). Der alte Tunnel `wamo-tunnel` bleibt unberührt |

Die Sicherungen in `/var/backups/wamo-firewall` dienen zum Nachsehen. Nicht blind mit `iptables-restore` einspielen,
weil Docker seine eigenen Regeln bei jedem Containerstart ändert.

## 9. Entwicklungsaufträge: Sandbox, Ausgangsfilter und SSH (eingerichtet am 19.10.2026)

Eingerichtet mit `wamo-job-einrichten.sh` (wiederholbar, Ablage über `~/wamo-infra` wie oben, Kopfkommentar des Skripts).
Rauchtest danach immer: `ssh sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp --attrappe"`.

| Baustein | Ort | Zweck |
| --- | --- | --- |
| Dispatcher | `/opt/wamo/bin/wamo-job-dispatch` (root:wamo) | Forced Command des Schlüssels `wamo-jobs`: nur `ping`, `start`, `status`, `pending`, `approve`, `cancel`; Selbsttest mit Kanarienvögeln je Lauf; vergibt Freigaben atomar über einen Unix-Socket; räumt alte Caches als `wamo` auf |
| Läufer | `/opt/wamo/bin/wamo-job.sh` | bubblewrap-Sandbox, Landlock (connect und bind), seccomp, systemd-Grenzen, Zeitlimit |
| Wächter | `/opt/wamo/waechter`, in der Sandbox unter demselben Pfad schreibgeschützt | Freigaben (per Socket reserviert und bestätigt), Sperrliste, seccomp-Filter |
| Managed-Settings | `/opt/wamo/claude-code/managed-settings.json`, in der Sandbox `/etc/claude-code` | nur verwaltete Hooks (allein der Wächter), keine Projekt-MCP-Server; `strictPluginOnlyCustomization` für `agents` und `mcp`; Marktplatz `ecc` ist die lokale Kopie ohne Hooks |
| ECC ohne Hooks | `/opt/wamo/claude-plugins` (root:wamo), in der Sandbox `/srv/wamo/.claude/plugins` | Kopie des ECC-Plugins von wamo ohne `hooks/`, `scripts/hooks/` und `.mcp.json`, als lokaler Marktplatz `ecc`; Skills, Agenten und Befehle heißen gleich (`ecc:verification-loop`). Erzeugt von `wamo-job-ecc.py`, nach jedem ECC-Update `wamo-job-einrichten.sh` erneut ausführen |
| Managed-MCP | `/opt/wamo/claude-code/managed-mcp.json` | die MCP-Server (rea, github, supabase, jira) kommen allein von hier (ersetzt Benutzer-Einträge); Token über `${…:-}` aus der Umgebung |
| Ausgangsfilter | `/etc/wamo/egress.nft`, `wamo-egress.service` | nft-Tabelle `inet wamo_egress`, nur für uid `wamo` |
| SSH | `/etc/ssh/authorized_keys.d/wamo`, `/etc/ssh/sshd_config.d/wamo.conf` | Schlüssel mit `restrict,from="192.168.178.0/24",command=...`; `Match User wamo` mit `ForceCommand`, nur Public Key, keine Weiterleitungen, kein TTY, `PermitUserRC no`, `MaxSessions 2`. Ab Dispatcher 7 zusätzlich `ExposeAuthInfo yes` und eine zweite `AuthorizedKeysFile` (`authorized_keys.d/wamo-kern`, der Schlüssel des Kerns, nur mit Abschnitt 18); die vorige `wamo.conf` liegt dauerhaft unter `/var/backups/wamo-job-kern/<Zeit>/`, die Einrichtung verlangt Ruhe (kein Auftrag, kein offener Totmannschalter) |
| Version | `CLAUDE_VERSION` in `/opt/wamo/wamo-job.conf` | nach einem Update von Claude Code startet nichts, bis das Einrichtungsskript und der Rauchtest liefen |
| Modell | `modell` je Karte in `voice-core/werkzeugkarte.py`, Wahl nach Zugangsart in `modell_waehlen` (Dispatcher) | Mit API-Schlüssel (Guthaben) Standard für alle Arten `claude-haiku-5-5` (Sonnet kostet etwa das Dreifache), mit Abo-Zugang (Max-Token) `claude-sonnet-5-5`; `claude-opus-5-5` oder ein anderes Modell nur über `WAMO_ENTWICKLUNG_MODELL` in der `.env` des PCs (nur Namen aus `MODELLE_ERLAUBT`, über die Kopfzeile an den Dispatcher) |
| Claude-Zugang | zweite stdin-Zeile von `start`, `WAMO_SERVERZUGANG` in `geheim.env` | persönlicher Zugang des Profils für genau einen Lauf, Rückfall auf `geheim.env` nur wenn erlaubt (unten) |
| GitHub-Zugang | `GITHUB_ZUGRIFF` und `GITHUB_APP_RECHTE` in `voice-core/werkzeugkarte.py`, Schlüssel `/etc/wamo/gh-app.pem` | im App-Modus je Lauf ein kurzlebiges Installations-Token mit den Rechten der Art, auf Wunsch nur für ein Repo, am Laufende widerrufen (unten) |

GitHub-CLI im Auftrag: ohne Karte nur lesende Befehle (`gh repo view/list/clone`, `gh pr view/list/diff/checks/status/
checkout`, `gh issue view/list/status`, `gh run`, `gh workflow`, `gh release` lesend, `gh label list`, `gh gist view/list`,
`gh search`, `gh status`, `gh api` ohne schreibende Optionen). Jeder andere gh-Befehl (etwa `gh repo create`, `gh repo
edit --visibility public`, `gh issue create`, `gh pr comment`, `gh workflow run`) braucht eine Freigabe; `gh alias`,
`gh extension`, `gh auth`, `gh secret`, `gh variable`, `gh codespace` sowie Löschen und Archivieren sind gesperrt.

Vor jedem Lauf entschärft der Dispatcher den Projektordner (außerhalb der Sandbox, ohne Links zu folgen): `.mcp.json`,
das ganze projekteigene `.claude/`, Git-Hooks und `config.worktree` in jedem `.git` im Baum (auch eingebettet oder als
`gitdir:`-Zeiger), und setzt jede `.git/config` auf eine Allowlist zurück. Der Auftrags-Konfigordner `jobs/<job>/claude`
wird je Lauf bis auf `projects/` (Sitzungen für `--resume`) geleert. Bekäme ein Lauf mehr Token als frühere Läufe im
selben Projekt, zeigt die Freigabekarte die seit dem letzten Lauf geänderten Autostart-Flächen (package.json-Skripte,
`.npmrc`, `.envrc`, `node_modules/.bin`, `CLAUDE.md`) und verlangt eine Freigabe.

GitHub im App-Modus (Variablen und Einrichtung in Abschnitt 2). Ablauf je Lauf, alles im Dispatcher außerhalb der
Sandbox:

1. Beim Start prüft der Dispatcher die drei Variablen `WAMO_GH_APP_*`. Fehlen alle, gilt der Rückfall auf `GH_TOKEN`
   bzw. `GH_TOKEN_LESEN`. Ist die Konfiguration unvollständig oder ungültig, endet der Start mit dem Ereignis
   `start_abgelehnt` und Code 8, ohne Rückfall auf den persönlichen Token.
2. Erst nach Selbsttest und Buchführung je Projekt liest er den privaten Schlüssel: Ordner und Datei ohne Links
   (`O_NOFOLLOW`), Datei relativ zum geöffneten Ordner, Besitzer root, nicht gruppen- oder weltschreibbar, nicht
   weltlesbar, höchstens 16 KiB, RSA ab 2048 Bit. Der Schlüssel steht nie im Log, in argv oder in der Sandbox.
3. Er signiert ein JWT (RS256, `iat` eine Minute zurück, `exp` 9 Minuten voraus, `iss` = App-ID) mit dem Systempaket
   `python3-cryptography` und ruft `POST https://api.github.com/app/installations/<id>/access_tokens` auf (TLS mit
   Zertifikats- und Hostnamenprüfung, Zeitlimit 15 s, kein Proxy, keine Weiterleitung). Rechte je Art aus
   `GITHUB_ZUGRIFF`: lesende Arten `contents`, `metadata` und `pull_requests` lesend, schreibende Arten `contents` und
   `pull_requests` schreibend, `metadata` lesend; recherche bekommt kein Token. Nie `administration`, `workflows`,
   `secrets`, `actions` oder `members`. Nennt die Kopfzeile ein Repo (`repo`, Form `owner/name`, vom Voice-Core gesetzt,
   wenn der Auftrag ein Repo nennt), gilt das Token nur dafür, sonst für die Installation mit den Rechten der Art.
4. Die Antwort wird geprüft: nie mehr Rechte als angefragt, mit Repo genau dieses Repo samt Owner, ohne Repo nur bei
   einer Installation mit ausgewählten Repos, zu denen das WAMO-Repo nicht gehört (dafür liest der Dispatcher mit dem
   neuen Token `GET /installation/repositories`, höchstens 500 Repos). Passt etwas nicht, widerruft der
   Dispatcher das Token sofort und lehnt den Start ab (Code 8). Nennt ein Auftragstext das WAMO-Repo, ohne dass das
   Feld `repo` gesetzt ist, lehnt schon der Voice-Core den Auftrag ab und bittet um das Ziel-Repo.
5. Das Token geht als `GH_TOKEN` in die Sandbox (gh und git nutzen es wie bisher), der Systemtext bekommt einen kurzen
   Hinweis auf die Grenzen. Während der Erzeugung sind SIGTERM und SIGHUP aufgeschoben, danach führen sie zum Stopp der
   Sandbox.
6. Am Laufende wird das Token widerrufen (`DELETE https://api.github.com/installation/token` mit dem Token selbst,
   bis zu drei Versuche mit 1 s und 3 s Pause, auch bei Ratenbegrenzung), auch bei `cancel`, abgerissener Lebenslinie,
   Fehler im Dispatcher oder SIGTERM, selbst wenn das Signal genau beim Start der Sandbox eintrifft. Nur bei SIGKILL oder Stromausfall
   bleibt es bis zu seinem Ablauf gültig, höchstens eine Stunde.
7. Audit (`/srv/wamo/logs/auftraege.log`): `gh_app_token <job> repo=<owner/name oder *> stufe=<lesen|schreiben>
   ablauf=<Zeit>`, `start ... github=app repo=...` und `gh_app_widerruf <job> ok`, nie das Token. Scheitert der Widerruf,
   steht `FEHLGESCHLAGEN` im Audit und der Voice-Core zeigt es auf dem Canvas. In der Buchführung je Projekt heißen die
   Zugänge `GH_APP_LESEN@<owner/name oder *>` und `GH_APP_SCHREIBEN@<owner/name oder *>`: der Umfang des Tokens gehört
   dazu, ein anderes oder breiteres Repo als in früheren Läufen verlangt eine Freigabe. Nach der Umstellung braucht
   deshalb jedes Projekt mit Inhalt einmal eine Freigabe (Karte „mehr Zugänge“). Das Ziel-Repo ist an den Auftrag
   gebunden (`auftrag.json`): eine Fortsetzung mit einem anderen Repo wird abgewiesen. Gibt der Agent das Token in
   seiner Ausgabe aus, schwärzt es der Dispatcher vor dem Weiterreichen zu `[GH_TOKEN]`.

`wamo-job-dispatch ping` meldet `"github": "app"`, `"token"` (Rückfall) oder `"fehler: <Grund>"`, ohne Netz. Der Rauchtest
prüft den App-Modus mit `--attrappe` ohne echte GitHub-Aufrufe und mit `--app-echt` gegen die eingerichtete App
(nur lesend).

### Persönlicher Claude-Zugang je Profil und Kontingent des Max-Abos (eingerichtet am 22.10.2026)

Jeder Lauf nutzt den Claude-Zugang des Profils, das den Auftrag stellt: den Max-Token aus `claude setup-token` (Kontingent
des Abos) oder einen Anthropic-API-Schlüssel (monatliches Guthaben). Beide hinterlegt der Kollege selbst, verschlüsselt, in der
Karte Modellzugang. Der Zugang in `geheim.env` (Nikos Schlüssel) bleibt nur als Rückfall für genau ein Profil.

Ablauf je Lauf (Kern in `voice-core/dev_agent.py`, Dispatcher in `wamo_job_dispatch.py`):

1. Der Kern holt den Zugang frisch (`modellzugang.claude_code_zugang_fuer`) und sendet ihn nach der Kopfzeile als zweite
   stdin-Zeile `{"typ": "oauth" | "api", "wert": "…", "workspace": "wrkspc_…" (nur api, optional)}` per SSH. Die Kopfzeile
   trägt nur `"zugang": "folgt"` und `"profil"` (die ersten 8 Zeichen der Profil-UUID, nur zur Zuordnung), nie den Wert. Der
   Wert steht nie in argv, in der Umgebung des ssh-Prozesses, im Log, im Audit, in Ereignissen oder in Fehlertexten. Nach
   `approve` und beim Fortsetzen sendet der Kern ihn erneut, der Dispatcher speichert nichts.
2. Der Dispatcher liest die Zeile vor Sperren und Selbsttest (höchstens 2 KiB, 30 Sekunden) und prüft streng: nur die
   Felder `typ`, `wert`, `workspace`; `oauth` verlangt `sk-ant-oat…`, `api` verlangt `sk-ant-api…` (Admin- und Sitzungsschlüssel
   passen auf keins von beiden); `folgt` ohne `profil` wird abgewiesen. Bei einem Fehler endet der Start mit Code 2 und dem
   Ereignis `zugang_ungueltig`, der Text nennt nie etwas aus der Zeile.
3. In der Sandbox steht genau eine Claude-Variable: `CLAUDE_CODE_OAUTH_TOKEN` (oauth) oder `ANTHROPIC_API_KEY` (api). Aus
   `geheim.env` kommt dann nichts dazu, auch nicht der Workspace-Header des Organisationsschlüssels (kein Mischen mit einem
   fremden Schlüssel). Die Dienst-Token der Auftragsart (GitHub, Vercel …) bleiben wie bisher.
4. Rückfall auf `geheim.env`: nur mit `"zugang": "server"` oder ohne das Feld (ältere Kerne). Der Kern erlaubt das nur dem
   Profil in `WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL` (leer: `WAMO_CLAUDE_SCHLUESSEL_PROFIL`). Alle anderen bekommen ohne eigenen
   Zugang die Meldung „kein Claude-Zugang“ und es startet nichts. `WAMO_SERVERZUGANG=aus` in `geheim.env` sperrt den Rückfall im
   Dispatcher ganz (Code 5, Ereignis `keine_zugangsdaten`); jeder andere Wert als `an`, `ja`, `1`, `true`, `on` und jeder
   Tippfehler im Namen (`WAMO_SERVER…`) sperrt ebenfalls. `ping` meldet `"serverzugang": true|false`.
5. Buchführung je Projekt: ein persönlicher Zugang heißt `CLAUDE_CODE_OAUTH_TOKEN@<profil>` bzw. `ANTHROPIC_API_KEY@<profil>`
   (nie der Wert). Kommt in einem Projekt mit Inhalt der Zugang einer anderen Person (oder der Serverzugang) dazu, verlangt der
   Dispatcher wie bei jedem neuen Token eine Freigabe (Karte „mehr Zugänge“).
6. Modell nach Zugangsart (`modell_waehlen`): Abo-Zugang (oauth) `claude-sonnet-5-5`, API-Schlüssel das Modell der Art (derzeit
   `claude-haiku-5-5`). Übersteuerung nur über `WAMO_ENTWICKLUNG_MODELL` bzw. das Kopfzeilenfeld `modell`, nur mit Namen aus
   `MODELLE_ERLAUBT`.
7. Schwärzung: Der Wert und jeder `sk-ant-…`-Schlüssel wird in der Ausgabe zum PC, im Protokoll `lauf.log` und nach dem Lauf in
   allen Dateien unter `claude/` (Sitzungen für `--resume`) durch `[CLAUDE_ZUGANG]` ersetzt. Bleibt etwas offen (nicht lesbar,
   Grenzen überschritten), löscht der Dispatcher `claude/projects` und schreibt `schwaerzen_unvollstaendig` ins Audit. Kodierte
   Formen (etwa Base64) fängt das nicht. Das Audit nennt nur `claude=persoenlich-oauth|persoenlich-api|server` und die Kennung
   `…@<profil>`.

Risiko, bewusst akzeptiert: Der Token liegt für die Dauer des Laufs in der Umgebung der Sandbox, genau wie `GH_TOKEN`. Ein
gekaperter Auftrag (Prompt-Injection über Webseiten oder Dateien) könnte ihn lesen und über Port 443 abfließen lassen.
Gegenmittel: (a) Tokens aus `claude setup-token` gelten nur für Claude Code (reine Inferenz, Umfang `user:inference`; Claude Code
2.1.303 beschreibt sie selbst als „limited to inference-only“), nicht für Konto, Verwaltung oder Chats; (b) der Kollege kann den
Token jederzeit in seinem Claude-Konto widerrufen und in der Karte Modellzugang löschen (die genaue Stelle im Claude-Konto beim
ersten echten Test prüfen und hier eintragen); (c) der Wächter `dev_guard.py` sperrt Befehle, die die Variablen nennen, sowie
`env`, `printenv` und `environ`; (d) Schwärzung wie oben; (e) höchstens ein Auftrag gleichzeitig, Zeitlimit je Lauf. Nicht
einsetzbar: `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` würde Anthropic-Zugänge aus Unterprozessen entfernen, erzwingt in Claude Code
2.1.303 aber den Berechtigungsmodus `default` und passt damit nicht zu `bypassPermissions` (im Versuch mit der Attrappe
bestätigt). Empfehlung: den Token nur für WAMO erzeugen, nicht für andere Zwecke mitnutzen.

Kontingent des Max-Abos (Sitzungslimit, Wochenlimit, Modell-Wochenlimit):

- Quelle ist das Ereignis `rate_limit_event` im stream-json von Claude Code (geprüft gegen Version 2.1.303 mit der API-Attrappe
  des Rauchtests, die die Kopfzeilen `anthropic-ratelimit-unified-*` schickt). Es erscheint nur bei einem Abo-Zugang: API-Schlüssel
  tragen diese Kopfzeilen nicht. Felder in `rate_limit_info`: `status` (`allowed`, `allowed_warning`, `rejected`), `rateLimitType`,
  `resetsAt` (Unix-Sekunden), `utilization` (Bruchteil) und `unifiedWindows` mit `five_hour` (Sitzungslimit, 5 Stunden), `seven_day`
  (Wochenlimit, alle Modelle) und `seven_day_overage_included` (modellbezogenes Wochenlimit, in Claude Code „Fable limit“), je mit
  `utilization` und `resetsAt`. Ältere Namen: `seven_day_opus`, `seven_day_sonnet`.
- Der Dispatcher gibt daraus (nur Felder einer festen Allowlist, Wertebereich geprüft, Zeilen aus der Sandbox sind nicht
  vertrauenswürdig, `voice-core/kontingent.py`) das Ereignis `{"type": "wamo", "ereignis": "kontingent", "quelle":
  "rate_limit_event", "limits": [{"art": "sitzung" | "woche" | "opus" | "sonnet" | "fable", "prozent": 42.0, "endet":
  "2026-10-22T15:00:00Z", "status": "ok" | "warnung" | "erreicht"}]}` weiter, höchstens alle 2 Sekunden und nur bei geänderten Werten.
- Der Kern prüft es erneut, legt es über den bekannten Stand (abgelaufene Fenster fallen weg), speichert es je Profil
  (`model_access.kontingent`, `kontingent_stand`, Migration 0012, nur der Kern hat Rechte darauf) und schickt die Servernachricht
  `{"type": "kontingent", "zustand": "bekannt" | "unbekannt", "stand": "…", "limits": [...]}` an die Oberfläche (`ui/src/lib/
  nachrichten.ts`, Zustand `kontingent`). Die Client-Nachricht `{"type": "kontingent"}` fragt den gespeicherten Stand ab. Ohne
  Daten ist der Zustand „unbekannt“. Das Kontingent des Serverzugangs wird nicht gespeichert (es gehört nicht dem Profil).
- Ohne laufenden Auftrag lässt sich das Kontingent mit diesem Token nicht abfragen: `/usage` und `GET /api/oauth/usage` brauchen
  den Umfang `user:profile`, den Tokens aus `claude setup-token` nicht haben, und der Token darf nur von Claude Code verwendet
  werden. Die Anzeige zeigt deshalb den Stand des letzten Laufs mit Zeitangabe. Möglich wäre ein Mini-Lauf (eine Antwort mit
  Haiku, wenige Token): er kostet etwas Kontingent und kann ein neues 5-Stunden-Fenster beginnen, deshalb ist er nicht gebaut.
- Der Name des modellbezogenen Limits („Fable“) kommt in Claude Code nur aus `/usage`; aus dem Ereignis ist nur die Art
  `fable` (`seven_day_overage_included`) bekannt. Beim ersten echten Test prüfen, ob die Prozentwerte zur Anzeige in Claude Code
  passen.
- Beim API-Schlüssel gibt es kein Kontingent; die Kosten je Auftrag stehen auf dem Canvas (`total_cost_usd`).

Schritt für Schritt, was ein Kollege tut:

1. Auf dem eigenen Rechner, angemeldet mit dem eigenen Max-Abo, `claude setup-token` ausführen und den Token (`sk-ant-oat01-…`,
   wird nur einmal angezeigt) kopieren. Alternativ im Anthropic-Konto einen API-Schlüssel (`sk-ant-api03-…`) für das monatliche
   Guthaben erzeugen.
2. In WAMO die Karte Modellzugang öffnen, den Token bzw. Schlüssel einfügen und speichern (er wird verschlüsselt abgelegt und
   nie wieder angezeigt, nur die letzten vier Zeichen).
3. Einen Entwicklungsauftrag stellen und starten. Mit dem Token läuft er mit Sonnet auf dem Kontingent des Abos, mit dem
   API-Schlüssel mit Haiku auf dem Guthaben. Nach dem ersten Lauf erscheint das Kontingent.
4. Zum Wechseln oder bei Verdacht: Token im Claude-Konto widerrufen, in der Karte Modellzugang löschen, neu erzeugen.

Betrieb (Niko): Zuerst den Dispatcher ausrollen (`ping` meldet `"version": "5"`), erst danach den Kern neu starten, sonst
weist ein alter Dispatcher die neuen Kopfzeilenfelder ab. Migration 0012 auf `wamo_dev` und `wamo_test` ist angewendet, für
`wamo_prod` nach 0010 und 0011 anwenden (Reihenfolge einhalten, nach jedem erneuten Lauf von 0010 oder 0011 auch 0012 erneut).
`WAMO_ENTWICKLUNG_SERVERZUGANG_PROFIL` in der `.env` des Kerns setzen (oder `WAMO_CLAUDE_SCHLUESSEL_PROFIL` beibehalten).
Haben alle Kollegen ihren eigenen Zugang, in `geheim.env` `WAMO_SERVERZUGANG=aus` setzen. Rauchtest mit den Attrappen:
`ssh sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp --attrappe --token-attrappe"`.

Ausgangsfilter: Für Sockets des Benutzers `wamo` gilt nur Internet auf TCP 80 und 443, DNS an `127.0.0.53` und auf
Loopback `127.0.0.1:20000-20999` für eigene Entwicklungsserver eines Auftrags. IPv6, LAN, Docker-Netze, private
Netze und eigene Adressen des Servers werden abgewiesen (Log mit Ratenbegrenzung, Fehler `EHOSTUNREACH` bzw. bei IPv6
`EACCES`). Andere Benutzer und Dienste laufen nicht durch die Kette (`meta skuid`); ufw, iptables-restore und Docker
fassen die eigene Tabelle nicht an. Zähler: `sudo nft list table inet wamo_egress`. Landlock sperrt zusätzlich das
Binden von TCP-Ports außer 20000-20999 (und Port 0), seccomp sperrt MPTCP und SCTP (die Landlock sonst umgingen).
Der Dispatcher prüft alle 15 s, ob der Filter noch steht, und beendet den Lauf sofort, wenn nicht.

Hinweise:

- Die Login-Shell von `wamo` ist `/bin/sh`, damit Forced Commands keine `.bashrc` laden. Für Arbeiten von Hand als wamo
  `sudo -u wamo -H bash -l` verwenden.
- Alte Auftrags-Caches räumt der Dispatcher als `wamo` auf (npm, cache, plugin-daten nach 7 Tagen, `claude/` nach 30 Tagen);
  es gibt keine root-eigene tmpfiles-Regel über die von der Sandbox beschreibbaren Ordner.
- Auch außerhalb der Sandbox gilt für `wamo` der Ausgangsfilter (etwa bei `npm install -g` von Hand: Internet über 443 geht).
- Hostweite Schalter wie `net.mptcp.enabled=0` oder `kernel.io_uring_disabled` sind bewusst nicht gesetzt (betreffen andere
  Dienste); die Sandbox sperrt beides selbst per seccomp.

## 10. Kern auf Sokrates-2 und SSH-Rücktunnel zum PC (Phase P3, eingerichtet am 19.10.2026)

Betriebsart **Büro**: Die Oberfläche auf dem PC spricht den Voice-Core auf Sokrates-2 an, die PC-Werkzeuge laufen über
einen SSH-Rücktunnel auf dem PC. Betriebsart **Unterwegs** (Standard, bis Büro abgenommen ist): Kern auf dem PC wie bisher.

```text
Browser/Desktop-App (PC) ──HTTPS 55444──> wamo-caddy ──Unix-Socket /run/wamo-core/core.sock──> wamo-core.service
          └──────────── WebRTC-Medien (UDP, direkt, nur Büro-LAN laut ufw) ────────────────────────┘
wamo-core ──Unix-Socket /run/wamo-tunnel/pc.sock──> sshd (wamo-tunnel) ══SSH══> ssh.exe (PC) ──> 127.0.0.1:8765 wamo_host.py --http
```

| Baustein | Ort | Zweck |
| --- | --- | --- |
| Dienst | `/etc/systemd/system/wamo-core.service` | Benutzer `wamo-core` (System, nologin, keine weiteren Gruppen), `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, leeres `CapabilityBoundingSet`, `SystemCallFilter=@system-service ~@privileged`, `MemoryMax=4G`, `Restart=on-failure`; lauscht nur auf dem Unix-Socket (`WAMO_CORE_UDS`), kein TCP-Port |
| Code und venv | `/opt/wamo-core` (root:wamo-core 0750) | `app/voice-core` (nur Laufzeitdateien), `venv` aus `uv.lock` (uv 0.13.1, Prüfsumme fest, System-Python 3.12), `wamo-ca.crt`, `bin/wamo-core-vorpruefung` |
| Vorprüfung | `ExecStartPre` | keine `.env` im Code-Baum (bot.py lädt sie mit Vorrang), Pflichtwerte gesetzt, DSN mit `verify-full`, `WAMO_ENTWICKLUNG` muss `aus` sein (die Vorprüfung kennt keinen anderen Entwicklungsschalter und bleibt unverändert, damit kein Schalter den Start des Kerns verhindern kann), Socket-Verzeichnis 0750 wamo-core:wamocon |
| Geheimnisse | `/etc/wamo/core.env` (root:wamo-core 0640) | nur über `sudo /usr/local/sbin/wamo-core-env setzen` (stdin, Schlüssel-Allowlist, atomar); `liste` zeigt nur Namen. Was die Unit festlegt (Betriebsart, Socket-Pfade, `SUPABASE_URL`, CA, erlaubte Herkunft, `WAMO_ENTWICKLUNG=aus`) lässt sich dort nicht überschreiben. Auch der Büro-Schalter der Entwicklungsaufträge (`WAMO_ENTWICKLUNG_BUERO` und seine Begleiter) steht nie in `core.env`, sondern im Drop-in `wamo-core@.service.d/wamo-entwicklung-buero.conf` (Abschnitt 18) |
| Datenbank | Rolle `wamo_core_s2` | LOGIN, Mitglied von `wamo_core` (`WITH INHERIT TRUE, SET FALSE`), dieselben Rollen-Einstellungen; RLS-Policies `to wamo_core` greifen für Mitglieder. Verbindung über Supavisor `192.168.178.136:55322`, `sslmode=verify-full` |
| HTTPS | `wamo-caddy`, Site `etc/kern/wamo-core.caddy` | `https://192.168.178.136:55444` (Zertifikat der WAMO-CA), `reverse_proxy unix//run/wamo-core/core.sock`, Body höchstens 512 KB; Socket-Verzeichnis per Bind-Mount nur lesend |
| Socket-Verzeichnisse | `/etc/tmpfiles.d/wamo-core.conf`, `wamo-tunnel.conf` | `/run/wamo-core` 0750 wamo-core:wamocon (Caddy läuft als 1000:1000); `/run/wamo-tunnel` 2750 wamo-tunnel:wamo-core (setgid: der Socket gehört der Gruppe wamo-core) |
| Tunnel | Benutzer `wamo-tunnel`, `/etc/ssh/authorized_keys.d/wamo-tunnel`, `/etc/ssh/sshd_config.d/wamo-tunnel.conf` | ohne Shell und Home; Schlüssel `restrict,port-forwarding,from="192.168.178.0/24",command="/bin/false"`; `AllowTcpForwarding remote`, `PermitListen none`, `PermitOpen none`, `AllowStreamLocalForwarding remote`, `StreamLocalBindMask 0117`, `StreamLocalBindUnlink yes`, `MaxSessions 0`, `PermitTTY no`, `ClientAlive 15 s x 2` |
| PC | `scripts\wamo-tunnel.ps1`, geplante Aufgabe „WAMO Tunnel“ | Host (`--http`, nur 127.0.0.1:8765, Bearer-Token aus `%LOCALAPPDATA%\WAMO\host-token`) und ssh in eigenen Job-Objekten, Wiederverbindung 2 s bis 60 s, Tunnel nur, solange der eigene Host den Port hält |

**Warum Unix-Sockets statt Ports:** Caddy im Bridge-Netz erreicht Loopback des Hosts nicht, und ein Port auf dem
Bridge-Gateway bräuchte eine ufw-Regel. Beim Tunnel könnte einen TCP-Port auf Loopback bei einem Ausfall jeder lokale
Benutzer belegen und so Anfragen und Token des Kerns abgreifen; den Socket legt nur `wamo-tunnel` an, verbinden kann nur
`wamo-core`. **Warum `AllowTcpForwarding remote`:** OpenSSH 9.6 prüft Socket-Rückweiterleitungen über dieselbe Rechteliste
wie TCP-Rückweiterleitungen, mit `no` wird auch der Socket abgelehnt. TCP-Listen verhindert `PermitListen none`, `-L`
verhindert `PermitOpen none` (geprüft: Sitzung, `-R 127.0.0.1:…` und `-W` werden abgelehnt). **Grenze:** OpenSSH prüft
Socket-Pfade nicht; `wamo-tunnel` kann Sockets überall anlegen, wo es schreiben darf (`/tmp`, `/dev/shm`), dort verbindet
sich niemand. Deshalb genau ein Schlüssel und ein PC je Benutzer; für weitere PCs je PC ein eigener Benutzer und Socket.

**WebRTC-Medien:** aioice bindet ephemere UDP-Ports (`local_addr=(addr, 0)`), ein fester Bereich geht ohne Patch nicht
und hostweite sysctls sind tabu. ufw verwirft eingehend alles außer Büro-LAN, Port 22 und den ConnectX-Netzen der Sparks.

**Profilbindung:** Den PC steuert nur das Profil aus `WAMO_PC_HOST_PROFILE` (genau eine user_id; Listen oder ungültige
Werte heißen niemand) und immer nur eine Sitzung zugleich. Die Oberfläche zeigt den Zielrechner; der Autostart der
PC-Steuerung greift im Büro nur, wenn die Desktop-App denselben Rechnernamen meldet.

```bash
# Vom PC (Repo-Wurzel). Einmal auf dem PC: scripts\wamo-tunnel-einrichten.ps1 (Token, Schlüssel, known_hosts, Aufgabe)
python scripts/wamo_kern_sokrates2.py verteilen                 # ~/wamo-infra und ~/wamo-infra/kern
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-einrichten.sh"  # Benutzer, /opt/wamo-core, Unit, Firewall 55444
ssh sokrates2 "bash ~/wamo-infra/wamo-core-caddy.sh"            # HTTPS 55444 (als wamocon)
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-tunnel-einrichten.sh" # danach binnen 5 Minuten über eine NEUE Verbindung:
ssh sokrates2 "sudo touch /run/wamo-tunnel-sshd-bestaetigt"
python scripts/wamo_kern_sokrates2.py alles --email <admin>     # DB-Rolle, Geheimnisse, Token, PC-Bindung, Neustart
powershell -File scripts\wamo-tunnel-einrichten.ps1 -Starten     # Tunnel jetzt starten (sonst ab der nächsten Anmeldung)
# Prüfen
python scripts/wamo_kern_sokrates2.py status
ssh sokrates2 "sudo journalctl -u wamo-core -n 50"               # INFO, ohne Gesprächsinhalte
```

Ein Update des Kerns ist `verteilen` und erneut `wamo-core-einrichten.sh` (ersetzt Code und venv, startet neu).

## 11. Freigaben für PC-Aktionen und Sitzungsgrenzen (vorbereitet am 20.10.2026, noch nicht ausgerollt)

| Baustein | Ort | Zweck |
| --- | --- | --- |
| Freigabe-Schlüssel | PC `%LOCALAPPDATA%\WAMO\host-freigabe`, Kern `WAMO_PC_HOST_FREIGABE` in `/etc/wamo/core.env` | zweites Geheimnis neben dem Host-Token; nur damit führt der Host eine freigegebene kritische Aktion aus (`freigabe_entscheiden`, nie für das Sprachmodell). Fehlt er, startet der Host trotzdem, gibt aber nichts frei |
| Kern-Einstellungen | `/etc/wamo/core.env` | `WAMO_SITZUNG_EINZELN` (eine Sitzung je Profil, Standard an), `WAMO_ANMELDUNG_MAX_STUNDEN` (Standard 168); `wamo-core-env` kennt die neuen Namen erst nach `wamo-core-einrichten.sh` |
| GoTrue | `/home/wamocon/supabase-wamo/.env` und `docker-compose.wamo-sitzungen.yml` | `GOTRUE_SESSIONS_TIMEBOX` (168h), `GOTRUE_SESSIONS_INACTIVITY_TIMEOUT` (72h) und `GOTRUE_SECURITY_UPDATE_PASSWORD_REQUIRE_REAUTHENTICATION` (true, Passwortwechsel nur mit erneuter Anmeldung, Befund des Token-Tests vom 12.11.2026), geprüft gegen gotrue v2.196.0; erstellt nur den Auth-Container neu |

```bash
# PC: Freigabe-Schlüssel anlegen (Token bleibt), Überwacher dabei neu starten
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wamo-tunnel-einrichten.ps1 -FreigabeNeu
# Kern: Code und Helfer aktualisieren (startet wamo-core neu), dann Token und Freigabe-Schlüssel übertragen
python scripts/wamo_kern_sokrates2.py verteilen
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-einrichten.sh"
python scripts/wamo_kern_sokrates2.py token --neustart
# GoTrue: Sitzungsgrenzen (Werte anpassbar mit SITZUNG_ZEITBOX=168h SITZUNG_INAKTIV=72h)
ssh sokrates2 "NUR_SITZUNGSGRENZEN=1 bash ~/wamo-infra/supabase-wamo-einrichten.sh"
python scripts/wamo_kern_sokrates2.py status                     # WAMO_PC_HOST_FREIGABE: gesetzt
```

## 12. Oberfläche im Browser auf Sokrates-2 (Variante A, eingerichtet am 21.10.2026)

WAMO läuft damit im Browser jedes Geräts im Büronetz (oder über WireGuard), nicht nur in der Desktop-App auf Nikos PC.
Der Browser dort kennt nur die Betriebsart Büro (Kern auf Sokrates-2); PC-Werkzeuge gehören zur Desktop-App, die
Oberfläche zeigt „nur in der Desktop-App verfügbar“.

```text
Browser ──HTTPS 55445──> wamo-caddy ──HTTP 3000, Netz wamo-ui-netz (internal)──> wamo-ui-blau  oder  wamo-ui-gruen (Next.js, Standalone)
   ├──HTTPS 55443──> wamo-caddy ──> Envoy (Anmeldung, Supabase)
   └──HTTPS 55444──> wamo-caddy ──> wamo-core (Kern, Unix-Socket); Medien als WebRTC direkt (UDP)
```

Seit Schritt 1.1 der Selbstüberarbeitung (`docs/selbstueberarbeitung.md`, Abschnitt B) läuft die Oberfläche als zwei Container,
von denen genau einer live ist; ein Release schaltet ohne Lücke um, der Rückweg dauert eine Sekunde (Abschnitt 12.6).

### 12.1 Bausteine

| Baustein | Ort | Zweck |
| --- | --- | --- |
| Image und Container | `infra/sokrates2/wamo-ui/Dockerfile`, `docker-compose.yml`; auf dem Server `/home/wamocon/wamo-ui` | `npm ci`, `next build` (Standalone), Laufzeit `node:22-bookworm-slim` über Digest, arm64. Zwei Container `wamo-ui-blau` und `wamo-ui-gruen` (je Farbe ein Image-Tag `wamo-ui:blau` und `wamo-ui:gruen`, dazu ein Archiv-Tag `wamo-ui:vX.Y.Z-<Commit, 12 Zeichen>`, die drei neuesten bleiben). Beide: Benutzer `node`, `read_only`, `cap_drop: ALL`, `no-new-privileges`, 512 MB, 1 CPU, 128 Prozesse, `restart: unless-stopped`, Healthcheck auf `/version.json`, kein veröffentlichter Port |
| Gesundheitsprobe | `ui/src/app/version.json/route.ts`, `ui/src/lib/buildInfo.ts` | `GET /version.json` liefert `{"name","version","commit","gebaut","sauber"}`: Version aus `ui/package.json`, Commit-ID, Bauzeit und der Vermerk „unverändert aus Git“ aus den Build-Argumenten (`WAMO_COMMIT`, `WAMO_BAUZEIT`, `WAMO_SAUBER`). Jeder Wert wird gegen ein enges Muster geprüft, nie ein Geheimnis. Antwort mit `Cache-Control: no-store`, bei jeder Anfrage frisch berechnet |
| Netz | `wamo-ui-netz` (172.25.0.0/24), angelegt vom Compose von `wamo-caddy` | `internal` und ohne Adresse auf der Bridge (`inhibit_ipv4`): Der Container erreicht weder Internet noch Büro-LAN noch Dienste des Hosts (in `pruefen` geprüft: Gateway, Router, Internet, Host). Nur `wamo-caddy` hängt zusätzlich darin |
| Caddy-Site | `wamo-ui/wamo-ui.caddy.vorlage`, installiert als `/home/wamocon/wamo-tls/caddy/etc/kern/wamo-ui.caddy` | `https://192.168.178.136:55445` mit Zertifikat der WAMO-CA (`tls internal`), nur GET und HEAD, Body höchstens 1 KB, `encode`, ohne `Server` und `X-Powered-By`, COOP und CORP `same-origin`. Die übrigen Header (CSP, Referrer-Policy, nosniff, X-Frame-Options, Permissions-Policy) setzt Next.js. Kein HSTS: Browser ignorieren ihn bei einer IP-Adresse. Den Upstream bindet die Site über `import /etc/caddy/kern/wamo-ui-aktiv.inc` ein |
| Include-Datei | `/home/wamocon/wamo-tls/caddy/etc/kern/wamo-ui-aktiv.inc`, erzeugt von `wamo-ui-umschalten.sh` | legt fest, welche Farbe live ist (Kopfzeilen `# live=… vorige=…` und `# seit=…`), die Gesundheitsprobe und die Rückfallebene für statische Dateien. Endung `.inc`, damit der Import `kern/*.caddy` sie nicht als Site lädt. Nie von Hand ändern |
| Einstellungen | `/home/wamocon/wamo-ui/wamo-ui.conf` (Vorlage `wamo-ui/wamo-ui.conf.beispiel`) | `WAMO_UI_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_WAMO_KERN_BUERO`, `WAMO_UI_TLS`, `WAMO_UI_HSTS`, Anmeldedauer im Browser. Hier und nur hier stehen Adresse und Zertifikatsart; im Code der Oberfläche steht keine feste IP. Werte sind öffentlich (stehen im JavaScript), keine Geheimnisse |
| Publishable Key | liest das Skript auf dem Server aus `/home/wamocon/supabase-wamo/.env` | als Build-Argument ohne Wert in der Befehlszeile; abgelehnt wird alles außer `sb_publishable_…` (oder ein JWT mit Rolle `anon`), nie `service_role` |
| Firewall | Port 55445 in `WAMO-FW-LAN` (`wamo-firewall.sh`) | Loopback und Büro-LAN, übernommen per `systemctl reload wamo-firewall` ohne Lücke |
| Erlaubte Herkunft | Drop-in `/etc/systemd/system/wamo-core.service.d/wamo-ui.conf` | ergänzt `PIPECAT_ALLOWED_ORIGINS` um `WAMO_UI_URL` (Basis ist die Liste aus `wamo-core.service`); `wamo-core.service` selbst bleibt unberührt. Wirksam nach einem Neustart von wamo-core |
| Skripte | `wamo-ui-umschalten.sh` (Bauen, Umschalten, Rückweg, Schnittstelle zum Notanker), `wamo-ui-einrichten.sh`, `wamo-ui-rueckweg.sh`, vom PC `scripts/wamo_ui_sokrates2.py` | alle wiederholbar; prüfen den Ablageort in `~/wamo-infra` wie die übrigen Skripte |

Supabase braucht nichts Neues: Der Envoy des Stacks antwortet auf Preflight-Anfragen mit der Herkunft der Anfrage
(geprüft für `https://192.168.178.136:55445`). Anmeldung mit Passwort und TOTP kennt keine Weiterleitung;
`SITE_URL` und `ADDITIONAL_REDIRECT_URLS` in GoTrue (`http://localhost:3000`) werden nur für E-Mail-Links gebraucht
(Passwort vergessen, Magic Link), die WAMO nicht nutzt. Käme das einmal dazu, gehört die neue Adresse in
`ADDITIONAL_REDIRECT_URLS` (erstellt nur den Auth-Container neu).

### 12.2 Einrichten und aktualisieren

```bash
# vom PC, Repo-Wurzel
python scripts/wamo_ui_sokrates2.py verteilen --mit-firewall-und-caddy   # Quellen von ui/ (ohne node_modules, .next, .env*) und Skripte
python scripts/wamo_ui_sokrates2.py einrichten                            # Firewall, Caddy, Umschalten, Herkunft, Prüfung
python scripts/wamo_ui_sokrates2.py einrichten --neustart                 # zusätzlich wamo-core neu starten, aber nur ohne Sitzung
python scripts/wamo_ui_sokrates2.py verteilen && python scripts/wamo_ui_sokrates2.py umschalten   # nach einer Änderung an ui/: Release ohne Lücke
python scripts/wamo_ui_sokrates2.py zurueck                               # Rückweg auf die vorige Farbe (eine Sekunde)
python scripts/wamo_ui_sokrates2.py status                                # lesend; pruefen = Header, TLS, Härtung, Isolation, Ladezeit
```

- `verteilen` ersetzt nur `~/wamo-infra/ui` und `~/wamo-infra/wamo-ui` (und mit dem Schalter die drei gemeinsamen Dateien
  `wamo-firewall.sh`, `wamo-firewall.service`, `wamo-caddy/docker-compose.yml`). `wamo_kern_sokrates2.py verteilen` löscht
  dagegen ganz `~/wamo-infra`: danach `wamo_ui_sokrates2.py verteilen` erneut ausführen.
- Das erste `einrichten` legt `wamo-caddy` neu an (Port 55445 und das Netz `wamo-ui-netz` kommen hinzu): 55443 und 55444
  sind dann wenige Sekunden weg. Das Skript tut das nur, wenn keine Sitzung läuft (keine UDP-Sockets des Kerns, keine
  Journalzeile außer abgewiesenen Anfragen in den letzten 5 Minuten), sonst bricht es mit einem Hinweis ab.
- Den Neustart von wamo-core macht nur `herkunft --neustart` und auch nur ohne Sitzung (gleiche Prüfung); sonst bleibt er ein
  offener Schritt: `ssh sokrates2 "bash ~/wamo-infra/wamo-ui-einrichten.sh herkunft --neustart"`.
- Nach einem Update von `ui/` genügt `verteilen` und `umschalten` (Abschnitt 12.6). Die alte Farbe läuft weiter, zurück geht es
  mit `python scripts/wamo_ui_sokrates2.py zurueck`. `verteilen` schreibt dazu `wamo-ui/bau.info` (Commit-ID von HEAD und den
  Vermerk, ob `ui/` und `infra/sokrates2` unverändert sind); die Oberfläche meldet beides in `/version.json`.
- Rückweg der ganzen Oberfläche (abschalten): Abschnitt 8.

### 12.3 Ein weiterer Windows-PC (Browser, Schritt für Schritt)

Voraussetzung: Der PC ist im Büronetz oder über WireGuard verbunden und die Person hat ein WAMO-Konto (`scripts/wamo_nutzer.py`).

1. **WAMO-CA importieren** (einmal je PC, ohne Administratorrechte). Die Datei `wamo-ca.crt` aus `infra/sokrates2/` auf den PC
   legen, dann in einer Eingabeaufforderung im Ordner der Datei:

   ```bat
   certutil -user -addstore Root wamo-ca.crt
   ```

   Die Sicherheitswarnung von Windows bestätigen, aber vorher den angezeigten Fingerabdruck (sha1) mit
   `9F5DE37F 855FD083 142E9E14 8A0954EA B2817200` vergleichen. Die CA gilt nur für die Adressen von Sokrates-2 (Abschnitt 5).
   Chrome und Edge nutzen den Windows-Speicher; Firefox nur mit `security.enterprise_roots.enabled = true`.
   Danach den Browser komplett schließen und neu öffnen.
2. **Adresse öffnen:** `https://192.168.178.136:55445`. Ohne Warnseite erscheint die Anmeldemaske; die Warnung „Verbindung nicht
   privat“ heißt, dass der Import fehlt oder der Browser nicht neu gestartet wurde.
3. **Anmelden** mit E-Mail und Passwort und dem Code der Authenticator-App (beim ersten Mal richtet die Oberfläche die App ein,
   QR-Code scannen). Im Browser gilt die Anmeldung 24 Stunden; „Angemeldet bleiben“ erhält sie bis dahin über das Schließen hinaus.
4. **Mikrofon erlauben:** Beim ersten „Gespräch starten“ fragt der Browser, dann „Zulassen“. Dauerhaft: Schloss links in der
   Adresszeile, Website-Einstellungen, Mikrofon auf „Zulassen“. Bleibt es stumm, in den Windows-Einstellungen unter
   Datenschutz und Sicherheit, Mikrofon, den Zugriff für Desktop-Apps und den Browser zulassen.
5. **Lesezeichen oder App-Fenster:** Strg+D legt ein Lesezeichen an. Als eigenes Fenster ohne Drosselung im Hintergrund:
   `scripts\wamo-app.cmd https://192.168.178.136:55445` (eigenes Chrome-Profil `%LOCALAPPDATA%\wamo-chrome`, das Mikrofon
   wird dort einmal erlaubt und gemerkt).

Grenzen: Im Browser gibt es nur die Betriebsart Büro und keine PC-Steuerung (dafür die Desktop-App auf dem jeweiligen PC).
Außerhalb des Büronetzes zuerst WireGuard verbinden.

### 12.4 Später: Variante B (eigene Domain, öffentlich vertrautes Zertifikat)

Ziel: Geräte brauchen keinen CA-Import mehr. Der Name (z. B. `wamo.firma.de`) wird nur im Büronetz aufgelöst (Fritzbox oder
DNS-Server des Büros, Ziel 192.168.178.136); das Zertifikat stammt von einer öffentlich vertrauten Stelle, die ohne erreichbaren Server prüft
(DNS-Challenge). Es ändern sich genau diese Stellen:

| Stelle | Änderung |
| --- | --- |
| `/home/wamocon/wamo-ui/wamo-ui.conf` | `WAMO_UI_URL=https://wamo.firma.de:55445`; `NEXT_PUBLIC_SUPABASE_URL` und `NEXT_PUBLIC_WAMO_KERN_BUERO` auf Adressen derselben Domain (`:55443`, `:55444`); `WAMO_UI_TLS=automatisch` (Caddy besorgt das Zertifikat; im Büronetz geht das nur mit DNS-Challenge, dafür braucht der Caddy ein Image mit dem DNS-Plugin des Anbieters, oder ein extern erneuertes Zertifikat, das als Datei eingehängt wird: eine Volume-Zeile im Compose von `wamo-caddy` und eine `tls`-Zeile in `wamo-ui.caddy.vorlage`); `WAMO_UI_HSTS=ja` |
| Caddy | die Sites für 55443 (`wamo-caddy/Caddyfile`) und 55444 (`wamo-caddy/kern/wamo-core.caddy`) bekommen den Domainnamen und dasselbe Zertifikat statt `tls internal`; der Wert `default_sni` im Caddyfile entfällt oder zeigt auf den Domainnamen |
| Build-Argumente | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_WAMO_KERN_BUERO` kommen aus `wamo-ui.conf`; neu bauen mit `python scripts/wamo_ui_sokrates2.py bauen` |
| CSP | nichts von Hand: `ui/next.config.ts` liest dieselben Adressen beim Build und trägt sie in `connect-src` ein. Der Test `pruefen` prüft, dass beide Adressen in der CSP stehen |
| `PIPECAT_ALLOWED_ORIGINS` | das Skript schreibt das Drop-in aus `WAMO_UI_URL` neu: `python scripts/wamo_ui_sokrates2.py einrichten --neustart` (alte Herkunft bleibt nicht stehen, weil das Drop-in komplett ersetzt wird) |
| Token-Aussteller | GoTrue `API_EXTERNAL_URL` und `SUPABASE_PUBLIC_URL` (Abschnitt 5) und im Kern `SUPABASE_URL` (Unit) auf die Domain, sonst stimmt `iss` der Tokens nicht; danach melden sich alle Personen neu an |
| Geräte | der CA-Import entfällt; die Desktop-App und der Voice-Core auf dem PC brauchen `WAMO_CA_FILE` nur noch für die IP-Adressen, die sie noch benutzen |
| Firewall | unverändert (Ports bleiben); Namensauflösung im Büro ist die einzige neue Voraussetzung |

Die WAMO-CA kann bleiben (Desktop-App im Notfall über die IP), sollte aber nicht mehr auf neuen Geräten importiert werden.

### 12.5 Bekannte Grenzen

- **Container und Caddy im selben Netz:** `wamo-ui` erreicht Internet, Büro-LAN und Host nicht, aber `wamo-caddy` und damit
  dessen Ports 55443 und 55444 (wie ein Büro-Gerät ohne Anmeldung; Docker filtert Verkehr innerhalb einer Bridge nicht).
  Der Container hält keine Geheimnisse, Tokens liegen nur in den Browsern. Wer das schließen will, setzt in der Site 55443
  (`wamo-caddy/Caddyfile`) und in `wamo-caddy/kern/wamo-core.caddy` als erste Anweisung `@ui_netz remote_ip 172.25.0.0/24`
  und `abort @ui_netz`. Das ändert zwei gemeinsam genutzte Dateien und gehört in die Übernahme der Geräte-Änderungen.
- **PC-Steuerung „nur in der Desktop-App“** ist eine Entscheidung der Oberfläche, kein Schutz: Der Kern nimmt den Wunsch von
  jeder Admin-Sitzung an. Maßgeblich bleiben die Bindung des PCs an ein Profil (`WAMO_PC_HOST_PROFILE`), die PC-Sperre je
  Sitzung und die Freigabe-Karten. Eine Erzwingung im Kern (Herkunft der Oberfläche gleich PC-Steuerung aus) ist möglich und offen.
- **CSP mit `'unsafe-inline'`** für Skripte und Stile, weil Next.js Inline-Skripte zur Hydration setzt; eine Nonce-CSP wäre
  der nächste Schritt. Caddy führt kein Zugriffslog für die Oberfläche und setzt keine Timeouts für langsame Clients
  (globaler Block `timeouts` im Caddyfile, gemeinsam genutzt).
- **HSTS** gilt bei Variante B für die ganze Domain und ein Jahr; vorher klären, ob andere Dienste unter derselben Domain
  ohne HTTPS laufen.

### 12.6 Release ohne Lücke: blau/grün, Umschalten, Rückweg (Schritt 1.1, vorbereitet am 22.10.2026, noch nicht ausgerollt)

**Prinzip.** Zwei Container, einer ist live. Ein Release baut die Fassung in die andere Farbe, prüft sie dort, setzt die
Include-Datei um und lädt Caddy neu (`caddy reload`, kein Neustart, kein neues Anlegen von `wamo-caddy`). Die alte Farbe läuft
weiter und wird erst beim nächsten Release ersetzt. Der Kern (WebRTC, Medien), die Anmeldung und die Sitzungen bleiben unberührt:
Die Oberfläche hält keinen Serverzustand, Browser sprechen Kern und Anmeldung selbst an.

```bash
python scripts/wamo_ui_sokrates2.py verteilen                      # Quellen und Skripte, dazu wamo-ui/bau.info (Commit von HEAD)
python scripts/wamo_ui_sokrates2.py umschalten --trocken           # zeigt den Plan, ändert nichts
python scripts/wamo_ui_sokrates2.py umschalten                     # bauen, prüfen, umschalten, bewähren (30 Sekunden), sonst zurück
python scripts/wamo_ui_sokrates2.py umschalten --bewaehrung 120 --fehlerquote 5     # strenger und länger
python scripts/wamo_ui_sokrates2.py bauen && python scripts/wamo_ui_sokrates2.py umschalten --ohne-bau   # in zwei Schritten
python scripts/wamo_ui_sokrates2.py zurueck                        # Rückweg auf die vorige Farbe, ebenso wieder vorwärts
python scripts/wamo_ui_sokrates2.py eintrag                        # Eintrag "letzte gute Version" für den Notanker (JSON)
ssh sokrates2 "bash ~/wamo-infra/wamo-ui-umschalten.sh status"     # Farbe, Fassungen, öffentliche Antwort
```

**Ablauf von `umschalten`.**

| Schritt | Was geschieht | Bei einem Fehler |
| --- | --- | --- |
| 1 Bauen | Image `wamo-ui:vX.Y.Z-<Commit>` und `wamo-ui:<Farbe>`, `docker compose up -d --no-deps --force-recreate <Farbe>`; die live gesetzte Farbe wird nie angefasst. Nur ein sauberer Stand (aus unverändertem Git) bekommt den Archiv-Tag, alles andere `…-lokal-<Zeit>` | Rückgabewert 1, nichts geändert |
| 2 Prüfen | am neuen Container aus dem Netz von Caddy: `/version.json` mit der erwarteten Version und dem erwarteten Commit, Startseite 200, `nosniff`, die CSP nennt Anmeldung und Büro-Kern (nur dann zeigt der Build auf die richtigen Adressen); der Kern antwortet ohne Anmeldung auf `POST /start` mit 401 (nicht erreichbar: Hinweis, kein Abbruch; jeder andere Status: Abbruch) | Rückgabewert 3, der neue Container wird angehalten, die alte Farbe bleibt live |
| 3 Schalten | neue Include-Datei aus der Vorlage im Skript, Prüfung der ganzen Konfiguration in einem Wegwerf-Container ohne Netz (`caddy adapt`), atomarer Tausch (`mv`), `caddy reload` | alte Datei kommt zurück, Rückgabewert 3 |
| 4 Wirkung | wartet (höchstens 20 s), bis `GET /version.json` über die öffentliche Adresse die neue Fassung liefert | automatischer Rückweg, Rückgabewert 4 |
| 5 Bewährung | zwei Proben je Sekunde über die öffentliche Adresse, Standard 30 Sekunden (`--bewaehrung 0` bis 1800) | drei Fehler in Folge oder mehr als `--fehlerquote` Prozent (Standard 10) lösen den automatischen Rückweg aus, Rückgabewert 4 |
| 6 Aufräumen | der Container `wamo-ui` aus der Zeit vor blau/grün wird abgeräumt, sobald er nicht mehr die vorige Farbe ist; die drei neuesten Archiv-Tags bleiben | |

Der automatische Rückweg setzt die Include-Datei auf die alte Farbe zurück und hält die fehlerhafte Farbe an (Protokoll mit
`docker logs wamo-ui-<farbe>`). Rückgabewert 5 heißt, dass auch der Rückweg nicht gelang: Dann zuerst `docker exec wamo-caddy caddy
reload --config /etc/caddy/Caddyfile --adapter caddyfile` und `status`. Ein zweiter Lauf zugleich wird mit einer Sperre
(`flock` auf `~/wamo-ui/.umschalten.lock`) abgewiesen.

**Die Include-Datei** `etc/kern/wamo-ui-aktiv.inc` ist der einzige Zustand: Kopfzeilen `# live=… vorige=…` und `# seit=…`, darunter
zwei `handle`-Blöcke. Erster Upstream ist die live gesetzte Farbe, zweiter die vorige. Fällt der erste aus (Verbindungsfehler oder
die Gesundheitsprobe `/version.json` alle 2 Sekunden), nimmt Caddy ohne Eingriff den zweiten; bei einer Wahl, die nicht antwortet,
versucht es bis zu 5 Sekunden weiter (`lb_try_duration`), Wählen dauert höchstens 1 Sekunde. Für `/_next/static/*` fragt Caddy
die live gesetzte Farbe und gibt bei 404 die Antwort der vorigen weiter (`handle_response`): Jeder Build hat eigene Dateinamen,
und eine offene Seite der alten Fassung lädt ihre Chunks weiter. Dasselbe gilt nach dem Rückweg in die andere Richtung. Die Site
weist alles außer GET und HEAD mit 405 ab, bevor ein Upstream etwas sieht (Block `route`, die Reihenfolge steht dort fest).
Von Hand ändern gilt nicht: Das nächste Schalten erzeugt die Datei neu.

**Erstumstellung (einmalig).** Heute läuft der einzelne Container `wamo-ui`. Reihenfolge ohne Lücke:

1. `verteilen --mit-firewall-und-caddy` aus einem sauberen Stand (`git status` ohne Änderungen in `ui/` und `infra/sokrates2`),
   sonst bekommt die Fassung `sauber: false` und der Notanker nimmt sie nicht als Ziel an.
2. `python scripts/wamo_ui_sokrates2.py einrichten` legt nur die Include-Datei (live ist zunächst der alte Container `wamo-ui`) und
   die neue Site an und lädt Caddy neu; `wamo-caddy` selbst wird nicht neu angelegt, weil sich sein Compose nicht ändert. Die Site
   wirkt wie vorher, nur über die Include-Datei.
3. `python scripts/wamo_ui_sokrates2.py umschalten` baut `wamo-ui-blau`, prüft es neben dem alten Container, schaltet um und
   bewährt es. Der alte Container bleibt als vorige Farbe (`alt`) laufen: Der Rückweg geht auch jetzt in einer Sekunde, und
   die Chunks offener Seiten bleiben erreichbar. Er wird beim nächsten Release abgeräumt, die alten Images `wamo-ui:aktuell`,
   `:vorher` und die Zeitstempel-Tags bleiben liegen (von Hand mit `docker image rm`, wenn sie nicht mehr gebraucht werden).
4. Danach `python scripts/wamo_ui_sokrates2.py eintrag` ausgeben und als `komponenten.ui.aktiv` und `letzte_gute` in die
   `stand.json` des Ankers eintragen (root, die Datei prüft der Anker streng; der Anker schreibt sie später selbst).

**Notanker.** Der Anker ruft als root ein festes Schritt-Skript `schritte/rueckweg-ui <Version> <Commit> <Ziel>` aus seiner
Zustandsdatei auf (Abschnitt 14). `wamo-ui-umschalten.sh` ist genau dafür gebaut: Als `rueckweg-ui` kopiert (Name entscheidet),
bedient es den Befehl `anker-rueckweg`. Es ist idempotent (schon aktiv: Exit 0, nach einem Abbruch zwischen Tausch und Reload lädt
es Caddy neu), prüft jeden Parameter streng (nur `vX.Y.Z`, 40 Hexzeichen, `blau` oder `gruen`), startet die Zielfarbe bei Bedarf aus
dem Archiv-Image und geht nur auf saubere Stände. Das Einbinden (Datei nach `anker/schritte/` kopieren, Prüfsumme im Manifest) ist ein
Handgriff in der Anker-Einrichtung und gehört zu Schritt 1.6; dieser Schritt ändert den Anker nicht. Läuft das Skript als root, legt es
Dateien im Home von `wamocon` mit dem Besitzer des Ordners an.

**Messung (Rauchtest `wamo_ui_rauchtest.py`, Attrappen und echtes Caddy 2.11.2, 20 Anfragen je Sekunde, Stand 22.10.2026).** Null
Fehler bei Erstumstellung, Release, Rückweg, Weg vorwärts, Rückweg des Ankers, Ausfall der live Farbe (`docker kill`) und Prüfungen,
die vor dem Umschalten scheitern (1212 Anfragen, 0 Fehler). Das Prüfen der Konfiguration, der Tausch der Datei und das Neuladen von Caddy
dauern zusammen 0,3 bis 0,4 Sekunden; die neue Fassung antwortet 0,3 bis 0,6 Sekunden nach Beginn des Schaltens über Caddy. `zurueck`
ist nach 0,5 Sekunden fertig, vom Aufruf bis zur ersten Antwort der alten Fassung vergehen 0,6 Sekunden (Ziel: unter 30 Sekunden).
Ein Release dauert mit Attrappe und 6 Sekunden Bewährung etwa 8 Sekunden. Je Umschalten bricht eine Ruhe-Verbindung ab, weil Caddy sie
beim Neuladen schließt; ein Browser wiederholt die GET-Anfrage unbemerkt, der Rauchtest tut es ebenso und zählt es getrennt.
Eine Bewährung, die durchfällt (Absturz der neuen Fassung nach dem Umschalten), schaltet automatisch zurück (Rückgabewert 4): bei einem
Absturz 0 Fehler, bei Fehlerantworten 500 der neuen Fassung 9 Fehler in 17 Sekunden, bis die Gesundheitsprobe sie aus dem Pool nimmt.

**Grenzen.**

- Offene Seiten, die zwei Releases zurückliegen, verlieren nachgeladene Chunks (die Farbe, die das nächste Release ersetzt, trug
  die älteste Fassung) und brauchen ein Neuladen. Der Hinweis „Neue Version, bitte neu laden“ in der Oberfläche folgt mit Schritt 1.5.
- Antwortet die neue Fassung nach dem Umschalten mit Fehlerstatus 500 statt mit Verbindungsfehlern, erreichen diese Antworten
  Nutzer, bis die Gesundheitsprobe sie aus dem Pool nimmt (zwei Sekunden) und die Bewährung zurückschaltet. Verbindungsfehler
  und Abstürze fängt Caddy ab.
- Fällt die live gesetzte Farbe aus, geht der Verkehr zehn Sekunden lang (`fail_duration`) an die vorige, auch wenn sie älter ist.
- Das Skript folgt als root keinen Links (Sperrdatei, Include-Datei und Compose-Datei werden abgelehnt, wenn sie welche sind),
  verlässt sich aber auf die Dateien im Home von `wamocon`; wer dort schreiben darf, hat ohnehin `sudo` und `docker`
  (Grenze des Ankers, Abschnitt 14).
- Ein Rückweg über das Archiv startet die Zielfarbe neu. Hat Caddy sie zuvor als ausgefallen vermerkt, verwendet es sie erst nach
  `fail_duration` (10 Sekunden) wieder; das Skript wartet darauf und meldet erst dann Erfolg (gemessen 13 Sekunden insgesamt, ohne
  Fehler, die vorige Fassung bedient solange weiter). Das Zeitlimit des Ankers beträgt 120 Sekunden für die Oberfläche.

**Tests.** `uv run python -m pytest ../scripts/test_wamo_ui_sokrates2.py` im Ordner `voice-core` (Syntax, feste Regeln, rund 90
Verbotsproben der Parameter ohne Aufruf von docker, Verteil-Werkzeug); `npm test` in `ui/` (Route und Vertrag mit dem Muster des
Skripts); `python infra/sokrates2/wamo_ui_rauchtest.py` (braucht nur Docker; legt ausschließlich Container, Netze, Volumes und Images
mit dem Namen `wamo-test-rauch-…` an und räumt sie wieder ab; `--nur kurz` lässt Absturz und Ausfall weg).

## 13. Rücktunnel je Gerät (Laptops, vorbereitet am 21.10.2026)

Jeder Laptop meldet sich als eigener Unix-Benutzer `wamo-t-NN` (NN = `tunnel_slot` 01 bis 64 aus dem Geräteregister) und darf
nur einen Socket in seinem Ordner `/run/wamo-pc/tNN` (2750, Besitzer `wamo-t-NN`, Gruppe `wamo-core`) anlegen. Grund:
OpenSSH 9.6 prüft Socket-Pfade bei `-R` weder mit `PermitListen` noch mit `permitlisten`; getrennt wird deshalb über das
Dateisystem. Der Kern (Gruppe `wamo-core`) öffnet nur den Ordner, den das Register für das Profil nennt, und prüft nach
dem Verbinden per `SO_PEERCRED`, wer lauscht. Den alten Tunnel (Abschnitt 10, `wamo-tunnel`) lässt das alles unberührt.

| Baustein | Ort auf Sokrates-2 | Zweck |
| --- | --- | --- |
| sshd-Drop-in | `/etc/ssh/sshd_config.d/wamo-pc.conf` | `Match User wamo-t-*`: nur Public Key, `AllowUsers wamo-t-*` (ergänzt ein globales `AllowUsers` aus anderen Drop-ins, ohne es zu ändern), nur Rückweiterleitung auf Unix-Sockets, kein TTY, `MaxSessions 1`, `ExposeAuthInfo`, `ForceCommand` |
| Zwangsbefehl | `/opt/wamo-pc/bin/wamo-pc-token` | Login-Shell und `ForceCommand`: liest genau zwei Zeilen (Host-Token, Freigabe-Schlüssel), schreibt sie samt Fingerabdruck des benutzten Schlüssels nur in den eigenen Ordner (0640), löscht beim Trennen nur die eigenen Dateien. Neueste Verbindung gewinnt (Exit 75, der Laptop verbindet sofort neu) |
| Abgleich | `wamo-pc-abgleich.timer` (alle 10 s), `/opt/wamo-pc/bin/wamo-pc-abgleich` | macht aus `<schema>.pc_abgleich_liste()` (nur aktive Geräte aktiver Profile) Benutzer, `/etc/ssh/authorized_keys.d/wamo-t-NN` und tmpfiles-Zeilen. Widerruf kommt zuerst und hängt an nichts anderem. Fällt die Datenbank aus, bleibt der Zustand (fail static); nach 5 Minuten ohne gültige Liste folgt `WAMO-GERAET ALARM` und Exit 1 |
| Streuner | `wamo-pc-streuner.timer` (alle 5 Minuten) | meldet lauschende Sockets in `sshd: wamo-t-NN`-Prozessen außerhalb des eigenen Ordners (aus `/proc/net/unix`, Ort egal) und beendet die Sitzungen des Benutzers |
| Konfiguration | `/etc/wamo/pc-abgleich.conf` | Schema (`WAMO_DB_SCHEMA`), Container, `from=`-Netze; Umstellen: `sudo sed -i 's/^WAMO_DB_SCHEMA=.*/WAMO_DB_SCHEMA=wamo_dev/' /etc/wamo/pc-abgleich.conf` |

```bash
scp infra/sokrates2/{wamo-pc-*,wamo_pc_*}* sokrates2:wamo-infra/           # oder Ablage wie in Abschnitt 0
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-pc-tunnel-einrichten.sh"        # prüft sshd -t, vergleicht sshd -T für wamocon und wamo-tunnel
ssh sokrates2 "sudo touch /run/wamo-pc-sshd-bestaetigt"                    # binnen 5 Minuten über eine NEUE Verbindung (Totmannschalter)
ssh sokrates2 "sudo python3 -I /opt/wamo-pc/bin/wamo-pc-rauchtest"         # Probe-Geräte pa und pb, alle Fälle grün
ssh sokrates2 "journalctl -u wamo-pc-abgleich -u wamo-pc-streuner --since -1h | grep WAMO-GERAET"
# Tests der Skripte selbst (als root, Datei test_pc_server.py aus voice-core/ danebenlegen):
ssh sokrates2 "cd ~/wamo-infra && sudo PYTHONDONTWRITEBYTECODE=1 WAMO_INFRA=\$HOME/wamo-infra python3 -m unittest test_pc_server"
```

### 13.1 Einrichtung am Laptop (ohne Handarbeit) und Zuordnung im Kern

1. Anmeldung in der Desktop-App (Betriebsart Büro, zweiter Faktor). Die App erzeugt einmalig `%USERPROFILE%\.ssh\id_ed25519_wamo_pc`,
   holt vom Büro-Kern eine Einmal-Herausforderung (`GET /wamo/api/geraet/herausforderung`), signiert sie samt `user_id` und
   Rechnername und bindet das Gerät (`POST /wamo/api/geraet/binden`, voice-core/geraet_api.py). Erstbindung ist
   automatisch aktiv nur für ein Mitglied ohne früheres Gerät; Admins, Gerätewechsel und doppelte Rechnernamen warten auf
   einen Admin, der einen Hinweis bekommt. Tunnelziel und Fingerabdruck des SSH-Hostschlüssels sind in der App festgehalten
   (`SHA256:FI+Dtt+vY0zJgLOwu7IajO50ywDaPwznZ9UgwJLN4lQ`, geprüft am 21.10.2026), IP und Rechnername sind nur Anzeige.
2. Die App schreibt `%LOCALAPPDATA%\WAMO\geraet.json` und ruft `scripts\wamo-tunnel-einrichten.ps1 -Geraet -Starten`
   (Rechte von `scripts` und `host`, geplante Aufgabe "WAMO Tunnel" mit zwei Auslösern: Anmeldung und alle 5 Minuten).
   Der Überwacher `scripts\wamo-tunnel.ps1` erzeugt bei jedem Host-Start neues Token und neuen Freigabe-Schlüssel, startet
   den Host angehalten im Job-Objekt und schickt beide über stdin an den Zwangsbefehl. Herzschlag: `tunnel.status`.
3. Der Abgleich auf Sokrates-2 richtet binnen 10 s Benutzer, Schlüssel und Ordner ein (aus `<schema>.pc_abgleich_liste()`).
   Test der Laptop-Seite ohne Server: `scripts\test-tunnel-geraet.ps1` und `scripts\test-tunnel-alt.ps1`.
4. Zuordnung im Kern (`WAMO_PC_REGISTER=an`, fest in `wamo-core.service`, Standard `aus`): `voice-core/pc_geraet.py`. Das
   Gerät ergibt sich nur aus dem Profil der Sitzung (aktives persönliches Gerät). Der Kern liest Token, Freigabe-Schlüssel
   und Fingerabdruck wettlauffrei aus `/run/wamo-pc/tNN/` (O_PATH und O_NOFOLLOW, Besitzer `wamo-t-NN`, Rechte 2750 und
   0640), verbindet nur mit `/run/wamo-pc/tNN/pc.sock` und prüft nach dem Verbinden per `SO_PEERCRED`, dass `wamo-t-NN`
   lauscht, bevor ein Byte gesendet wird. Jede Anfrage trägt `X-WAMO-Geraet` und `X-WAMO-Profil` (der Host prüft sie gegen
   `geraet.json`). Der Rechnername des Hosts muss zum Register passen, sonst keine Werkzeuge. Alle 60 s (erste Prüfung nach
   15 s) prüft eine Wache, ob das Gerät noch aktiv ist; gesperrt, widerrufen oder drei Ausfälle des Registers beenden die
   PC-Steuerung der Sitzung. Mitglieder bekommen Werkzeuge nur mit `WAMO_PC_MITGLIEDER=an` (Standard aus; vor der
   Einwilligung für Bildschirmfotos und den Rechten auf dem eigenen Laptop nicht setzen). Ablehnungen stehen mit Präfix
   `WAMO-GERAET` im Journal. Umstellen: erst Rauchtest, dann in der Unit `WAMO_PC_REGISTER=an`, `daemon-reload`, Neustart
   des Kerns (nur wenn laut Journal keine Sitzung läuft); zurück mit `aus`.

Der Rauchtest läuft nach jedem Update von `openssh-server` erneut: Die Pfadfreiheit von OpenSSH bei Rückweiterleitungen
beruht auf einem offenen Punkt im Quelltext (`channels.c`: „XXX add ACLs for streamlocal“). Er prüft neben dem
Positivfall (eigener Socket, Dateirechte, Fingerabdruck) auch die Neinfälle samt Ablehnungsgrund im sshd-Journal
(fremder Ordner, Ersetzen fremder Sockets, `-L` auf den Kern-Socket, TCP-Port, Befehl, `-W`, sftp, TTY), das Sperren
laufender Verbindungen und den Streuner. Der Abgleich läuft dort unter denselben Sandkasten-Einstellungen wie die Unit.
Das Journal liest der Test nach dem Programmnamen (`journalctl -t sshd`), nicht nach der Einheit: Der Verbindungsprozess
eines angemeldeten Tunnelbenutzers liegt dank `pam_systemd` in `session-NNN.scope`, seine Ablehnungsgründe stehen deshalb
nicht unter `ssh.service`. Die Gründe stehen bereits bei `LogLevel INFO` im Journal, `VERBOSE` ist nicht nötig.

Restrisiken: Wer auf Sokrates-2 `sudo` oder die docker-Gruppe hat, ist root und kommt an alle Tokens. Sockets
außerhalb von `/run/wamo-pc` sind möglich (der Streuner meldet sie binnen 5 Minuten); gefährlich wären sie nur, wenn ein
Dienst einen vorhersagbaren Pfad dort öffnet, deshalb prüft der Kern vor dem ersten Byte per `SO_PEERCRED`. Eine
Obergrenze für Weiterleitungen je Gerät gibt es nicht (`MaxSessions` zählt nur Sitzungen).

## 14. Notanker der Selbstüberarbeitung (Schritt 0.1, vorbereitet am 22.10.2026, noch nicht ausgerollt)

**Zweck.** Der Notanker ist der kleine Teil der geplanten Selbstüberarbeitung (`docs/selbstueberarbeitung.md`, Abschnitt G),
der Root-Rechte braucht. Er kann nur zurückführen, sperren und prüfen. Er liegt in `/usr/local/lib/wamo-anker`, außerhalb von
`/opt/wamo*` und außerhalb des Repo-Deploys, gehört root, ist für WAMO und alle Dienstbenutzer nie änderbar und wird nur von
Hand installiert. Quelltext: `infra/sokrates2/anker/`. Bis die Stufen 1 und 2 stehen, bleibt `SELF_REPO = "wamo"` unverändert.

| Befehl | Wirkung |
| --- | --- |
| `wamo-anker status` | aktive und letzte gute Version je Komponente (`ui`, `kern`), Sperre, Zustandsdatei, Hashkette des Audit-Protokolls, Prüfsumme der Installation. Offene Anfragen kommen mit Schritt 0.2 (Datenbank) |
| `wamo-anker rueckweg ui`, `kern`, `alles` | zurück auf den letzten guten Stand aus `stand.json`. Der Anker ruft dazu das feste Skript `/usr/local/lib/wamo-anker/schritte/rueckweg-ui` oder `rueckweg-kern` mit Version, Commit und Ziel (alles aus der Zustandsdatei, nie aus der Anfrage). Die Skripte liefern die Stufen 1.1 und 2.4; bis dahin antwortet `rueckweg` ehrlich mit "Schritt fehlt". Das Skript läuft auch dann, wenn `stand.json` schon den letzten guten Stand zeigt (die Buchführung könnte nach einem Absturz hinter dem Schalter zurückliegen; die Skripte müssen idempotent sein). Ein zweiter Aufruf geht nie weiter zurück als bis zum letzten guten Stand. Die Ausgabe des Skripts sehen nur `root` und `wamocon` |
| `wamo-anker sperre an`, `aus` | Abschalter für jede Selbständerung: Datei `/etc/wamo-anker/selbstaenderung.aus`. Steht sie (oder ist sie nicht eindeutig lesbar), lehnt der Anker alle Vorwärtsbefehle ab (`bereitstellen`, `migrieren`, `umschalten`, in Stufe 1 und 2 gefüllt; jetzt nur reserviert). `rueckweg` und `status` laufen immer, der Rückweg ist der Notausgang |
| `wamo-anker pruefe-sonder <id>` | Schnittstelle der Sonderfreigabe für die Schutzschicht. Bis Stufe 4 antwortet sie für jede Kennung `NEIN` (Exit 1). Änderungen an der Schutzschicht gibt künftig nur ein anderer Admin frei als der Beauftragte, und nur mit mindestens zwei aktiven Admins; heute gibt es einen, die Schutzschicht bleibt also gesperrt |
| `--trocken` | bei `rueckweg` und `sperre`: zeigt den Plan, ändert weder Zustand noch Sperre noch Systeme (nur das Audit-Protokoll bekommt die Zeile) |
| `wamo-anker lokal <befehl>` | Notfallweg nur für root, wenn der Dienst steht: dieselbe Prüfung und dasselbe Protokoll ohne Socket |

**Wer darf.** Der Dienst `wamo-anker` (root, ohne Capabilities) lauscht auf `/run/wamo-anker/anker.sock` (Ordner 2750
`root:wamocon`, Socket 0660). Zusätzlich prüft er jede Verbindung per `SO_PEERCRED`, bevor er ein Byte liest. Erlaubt sind
`root` und `wamocon`; `wamo-release` ist für Schritt 0.3 vorgesehen (status, rueckweg, sperre an, pruefe-sonder, nie
`sperre aus`; ein Zugriff dafür wäre eine ACL auf den Socket). `wamo`, `wamo-core`, `wamo-geraete`, die Tunnelbenutzer und alle
anderen werden abgewiesen.

**Was er nie tut.** Keine Shell und kein Pfad aus einer Anfrage (`subprocess` nur mit Liste, feste Pfade, leere Umgebung,
Zeitlimit, begrenzte Ausgabe); keine Geheimnisse lesen oder protokollieren; kein Netz (`RestrictAddressFamilies=AF_UNIX`);
nichts am Repo; keine Datenbank (kommt in 0.2 und 2.5); Schritt-Skripte nur, wenn sie root gehören, nicht beschreibbar sind und
ihre Prüfsumme im Manifest stimmt. Eingaben: genau eine ASCII-Zeile bis 160 Bytes, Allowlist-Regex je Feld, jede Anfrage
mit eigener Kennung (dieselbe Kennung wird nie zweimal bedient). Bei einem Fehler gilt immer die sichere Seite: unklare
Sperre heißt gesperrt, kaputte Zustandsdatei heißt kein Rückweg und keine Überschreibung, nicht schreibbares Audit-Protokoll
heißt keine ändernde Aktion. Damit niemand (auch kein erlaubter Benutzer) das Protokoll füllt und so den Rückweg aussperrt,
gelten Klassen: Lesen, Abweisungen und Anfragen ohne Wirkung werden nur bis 75 Prozent Füllgrad protokolliert (je uid und
Grund höchstens 20 Zeilen pro Minute, der Rest wird gezählt und in der nächsten Zeile genannt), eine ändernde Sperre bis 90
Prozent, ein Rückweg mit Wirkung bis 100 Prozent. Ändernde Anfragen sind je uid auf 30 pro Minute gedrosselt. Es wird nur
angehängt, wenn mindestens 1 MiB Platz frei ist (keine halben Zeilen bei voller Platte).

| Ort | Inhalt |
| --- | --- |
| `/usr/local/lib/wamo-anker` (root, 0755) | `wamo_anker.py`, `wamo-anker.sh`, `anker_rauchtest.py`, `schritte/`, `SHA256SUMS` (Prüfsumme aller Dateien; `ExecStartPre` prüft sie vor jedem Start, `status` zeigt Abweichungen) |
| `/etc/wamo-anker` (root, 0755, ohne Geheimnisse) | `selbstaenderung.aus` (die Sperre) |
| `/var/lib/wamo-anker` (root, 0700) | `stand.json` (Tabelle der letzten guten Stände je Komponente, die Vorgängerin als `.vorher`), Schreibsperre |
| `/var/log/wamo-anker` (root, 0750) | `audit.jsonl`: eine JSON-Zeile je Ereignis mit Hashkette (`n`, `vorher`, `hash`), `chattr +a`, Zeile höchstens 1024 Bytes, bei 32 MiB ist es voll |
| `/run/wamo-anker` | `anker.sock` |

Format von `stand.json` (bis die Stufen 1 und 2 es füllen, steht überall `null`; Handarbeit von root ist möglich, die Datei
wird beim Laden streng geprüft): je Komponente `aktiv` und `letzte_gute` mit `version` (`vX.Y.Z`), `commit` (40 Hexzeichen),
`ziel` (`blau` oder `gruen` für die Oberfläche, `a` oder `b` für den Kern) und `seit` (UTC, ISO 8601).

**Einrichten (von Hand, wiederholbar).** Erstinstallation legt `stand.json` leer an und **setzt die Sperre** (Selbständerung
aus); ein weiterer Lauf ändert weder Zustand noch Sperre noch Protokoll.

```bash
ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
ssh sokrates2 "sudo bash ~/wamo-infra/anker-einrichten.sh --trocken"                       # zeigt nur, was geschähe
ssh sokrates2 "sudo bash ~/wamo-infra/anker-einrichten.sh"
ssh sokrates2 "wamo-anker status"
ssh sokrates2 "sudo python3 -I /usr/local/lib/wamo-anker/anker_rauchtest.py --produktiv"   # Wegwerf-Instanz plus echte Installation
```

Der Rauchtest prüft in einer Wegwerf-Instanz Befehle, Verbotsproben (auch als `wamo`, `wamo-core`, `nobody` über `runuser`),
Sperre, `--trocken`, Hashkette und Zeit des Rückwegs (UI unter 30 Sekunden, Kern unter 60 Sekunden, hier mit Attrappen der
Schritt-Skripte). Mit `--produktiv` prüft er zusätzlich Rechte, Prüfsumme, `chattr +a` und den Socket der echten Installation
(es entstehen Zeilen im Audit-Protokoll). Probelauf der Skripte ohne root: `WAMO_ANKER_WURZEL=/tmp/anker-probe bash
anker-einrichten.sh` (läuft nie als root, die Wurzel darf nur aus A-Z, a-z, 0-9, `.`, `_`, `-` und `/` bestehen, ohne `//` und
ohne `.` oder `..`). Tests ohne Root: `uv run python -m pytest -q test_anker.py test_anker_skripte.py` im Ordner `voice-core`.
Das Einrichtungsskript lässt Systemordner wie `/usr/local/lib` unverändert und hält den bisherigen Baum als
`/usr/local/lib/wamo-anker.vorher`, bis `wamo-anker status` nach dem Neustart bestanden hat (sonst steht der Rückfallbefehl im
Fehlertext). `status` meldet außerdem Elternordner von `lib`, die für alle oder für eine Gruppe mit Mitgliedern beschreibbar
sind (wer dort schreiben darf, könnte `lib` samt Prüfsumme austauschen; auf Ubuntu ist `/usr/local/lib` root:staff 2775, die
Gruppe `staff` ist normalerweise leer und löst keine Meldung aus).

**Rückweg.** `sudo bash ~/wamo-infra/anker-rueckweg.sh` stoppt den Dienst, alles bleibt liegen. Mit `--entfernen` verschwinden
Programme, Unit, Verknüpfung, tmpfiles-Datei und `stand.json` (beide Dateien zuvor nach `/var/backups/wamo-anker` gesichert,
das Audit-Protokoll wird dorthin verschoben, nie gelöscht). Die Sperre bleibt bewusst bestehen; sie fällt nur mit
`sudo rm /etc/wamo-anker/selbstaenderung.aus`. Beide Skripte kennen `--trocken`.

**Wenn das Audit-Protokoll voll oder beschädigt ist.** `status` zeigt `Kette defekt` oder den Füllgrad. Ab 75 Prozent
protokollieren nur noch schreibende Befehle (der Rückweg bleibt möglich), bei 100 Prozent oder defekter Kette lehnen alle
schreibenden Befehle ab. Handarbeit: `sudo chattr -a /var/log/wamo-anker/audit.jsonl`, Datei nach `/var/backups/wamo-anker`
verschieben, `sudo systemctl restart wamo-anker` (neue Kette), `sudo chattr +a` auf die neue Datei.

**Grenzen.** Der Anker schützt gegen WAMO und die Dienstbenutzer, nicht gegen Menschen mit root: `wamocon` hat sudo und die
Docker-Gruppe und kann ihn ändern, ebenso das Audit-Protokoll samt Kette (die Hashkette ist nicht geschlüsselt; sie erkennt
Fehler und Teilmanipulation, ein Fälscher mit root rechnet sie neu; den letzten Hash zeigt `status`, ab Schritt 0.2 gehört er
zusätzlich in die Datenbank). Das Gedächtnis gegen doppelte Anfragen liegt im Speicher, nach einem Neustart des Dienstes ist
es leer. Ein Schritt-Skript, das einen Hintergrundprozess mit offener Ausgabe zurücklässt, läuft bis zum Zeitlimit; danach
beendet der Anker die ganze Prozessgruppe. Der Dienst bedient eine Verbindung nach der anderen: Ein laufender Rückweg
(`alles`: bis 300 Sekunden) hält `status` und `sperre` so lange auf; SIGTERM wartet ihn ab (`TimeoutStopSec=330`). Ein
erlaubter Benutzer kann den Dienst jeweils bis zu 5 Sekunden mit einer stillen Verbindung binden und das Protokoll innerhalb der
genannten Klassen und Drosseln beschweren; wer das Protokoll trotzdem füllt, braucht Handarbeit von root (oben), und root kann
den Rückweg im Notfall direkt am Skript unter `/usr/local/lib/wamo-anker/schritte` ausführen. Die Dienst-Unit wurde ohne
systemd-Test auf Sokrates-2 geschrieben und ist durch `systemd-analyze verify` im Einrichtungsskript, `systemd-analyze security
wamo-anker` und den Rauchtest zu bestätigen; startet sie nicht, sind `MemoryDenyWriteExecute` und `SystemCallFilter` die ersten
Verdächtigen.

## 15. Release-Dienst der Selbstüberarbeitung (Schritt 0.3, Gerüst, vorbereitet am 22.10.2026, noch nicht ausgerollt)

**Zweck.** `wamo-release` (Konzept `docs/selbstueberarbeitung.md`, Abschnitt D) führt eine Release-Anfrage durch einen
Zustandsautomaten. Er läuft unprivilegiert (Benutzer `wamo-release`, ohne Capabilities, nur Unix-Sockets) und schaltet nie
selbst: Alles Schaltende liegt beim Notanker, den er nur über dessen Socket mit `status`, `rueckweg` und `sperre an` erreicht
(nie `sperre aus`). In diesem Schritt sind die Stufen (Übernehmen, Prüfstrecke, Review, Refactor, Veröffentlichen,
Bereitstellen) Schnittstellen ohne Inhalt (`release/release_stufen.py`, Stufe 1 bis 3 füllen sie), es gibt keine
Datenbankverbindung (`release/release_db.py`, Stufe 1), keinen Aufruf eines Modells, kein Netz und kein GitHub. Der Dienst
im Betrieb lehnt `anfordern` deshalb ehrlich ab (`datenbank_fehlt`, solange die Sperre des Ankers steht `gesperrt`). Voll
getestet sind Socket, Audit-Kette, Zustandsautomat, Wiederaufnahme, Zeitgrenzen, Sperre und Rückweg. Quelltext:
`infra/sokrates2/release/`.

| Befehl (`sudo wamo-release ...`) | Wirkung |
| --- | --- |
| `status` | offene Anfrage, letzte Anfragen, Sperre des Ankers, Datenbank, Audit-Kette, Spiegel, Prüfsumme der Installation |
| `anfordern main <profil>` | legt eine Anfrage an: nur für einen aktiven Admin laut Datenbank, nur wenn der Anker erreichbar und nicht gesperrt ist, höchstens eine offene, höchstens drei je Admin und Tag. Die Quelle `selbst` ist erst mit Stufe 3 freigeschaltet |
| `abbrechen <anfrage>` | beendet die offene Anfrage, solange nichts umgeschaltet ist; ab `veroeffentlicht` (der Anker kann schon begonnen haben) erst nach einem gelungenen Rückweg, ab `bereitgestellt` nur über `zurueck` |
| `zurueck ui`, `kern`, `alles` [`--trocken`] | Rückweg über den Anker; beendet auch die offene Anfrage (vor `veroeffentlicht` und in `veroeffentlicht` mit `abgebrochen`, ab `bereitgestellt` mit `zurueckgenommen`, hier nur bei `alles`: ein Teilrückweg lässt sie offen). Geht aus jedem Zustand, auch bei defekter Zustandsdatei, nicht beschreibbarem Protokoll und hängendem Schloss |
| `freigeben`, `diff` | gibt es hier nie: Freigaben schreibt nur der Freigabe-Dienst des Ankers (Stufe 1.4), die Diff-Ansicht kommt mit Stufe 1.5 |

**Wer darf.** Socket `/run/wamo-release/ctl.sock` (Ordner 2750 `wamo-release:wamo-core`, Socket 0660) und `SO_PEERCRED`
vor dem ersten gelesenen Byte: `root` darf alle vier Befehle, `wamo-core` (der Kern) nur `status` und `anfordern`, nie
Freigabe, Rückweg oder Abbruch. Menschen kommen über `sudo`. Der Socket für Personen mit Token (`api.sock`, Prüfung von Token,
aal2 und Rolle) kommt mit Stufe 1.4; bis dahin kann der Kern eine Anfrage im Namen eines beliebigen Admin-Profils stellen (die
Datenbank prüft, dass es ein aktiver Admin ist; die Person, die den Kern bedient, prüft der Kern). Eingaben: eine ASCII-Zeile bis
160 Bytes, Regex je Feld, jede Kennung nur einmal. Grenzen gegen Dauerfeuer: der Kern darf 10 ändernde Anfragen je Minute und
protokolliert nur bis 75 Prozent Füllgrad, root 30 und bis 90 Prozent, der Rückweg bis zum Ende; `status` höchstens 120 je Minute
und Benutzer; getrennte Verbindungstöpfe (8 für root, 4 für den Kern), damit der Kern den Rückweg nicht aushungern kann.

**Zustände.** Es sind die der Datenbank (Migration 0013), 17 Zustände und 30 erlaubte Übergänge; die Quelle `main` überspringt
`arbeit`, `review`, `refactor` und `pruefung2`. Fehler, Zeitüberschreitung und Abbruch führen nach `abgebrochen` (mit Grund);
ab `veroeffentlicht` (dort ruft der Dienst den Anker) geschieht das erst nach einem gelungenen Rückweg, ab `bereitgestellt`
heißt das Ende `zurueckgenommen`. Gelingt der Rückweg nicht, bleibt die Anfrage offen, der Anker bekommt `sperre an`, und die
Wache versucht es alle 60 Sekunden bis zu fünfmal (`status` zeigt `rueckweg_offen`, danach Handarbeit mit `wamo-anker rueckweg
alles`). Eine Stufe, die auf einen Menschen wartet (zum Beispiel die Wahl der Version), verbraucht keine Versuche. Die Zustände ab `freigegeben` außer `veroeffentlicht`
setzt nur der Anker oder der Freigabe-Dienst in der Datenbank; der Dienst beobachtet sie nur und prüft bei der Freigabe
Commit-ID und Tree-ID gegen seine eigenen Werte (Abweichung: Abbruch und `sperre an`).

| Zustand | Arbeit | Zeitgrenze |
| --- | --- | --- |
| `angefragt` | Anfrage steht | 15 Min |
| `arbeit` (selbst) | Agent in der Arbeitskopie | 60 Min |
| `uebernommen` | Quelle als Commit C1 festhalten | 10 Min |
| `pruefung1` | bauen, testen, scannen | 45 Min |
| `review`, `refactor` (selbst) | lesende Agenten, Refactor als Commit R | je 30 Min |
| `pruefung2` (selbst) | Prüfstrecke auf R | 45 Min |
| `versionswahl` | Vorschlag, Wahl des Admins, Release-Commit V | 24 Std |
| `pruefung3` | schnelle Prüfung auf V, Klassifizierung durch den Anker | 15 Min |
| `wartet_freigabe` | wartet auf die Freigabe (Datenbank) | 24 Std |
| `freigegeben` | veröffentlichen | 15 Min |
| `veroeffentlicht`, `bereitgestellt` | der Anker stellt bereit und schaltet um | 30 und 15 Min, danach Rückweg |
| `umgeschaltet` | Bewährung (30 Minuten, Anker) | 60 Min, danach Rückweg |

Zum Wortlaut der Konzeptskizze: angefordert heißt `angefragt`, Quelle geholt `uebernommen`, gebaut und getestet `pruefung1`,
geprüft `review`, refaktoriert `refactor`, nachgetestet `pruefung2`, Version vorgeschlagen `versionswahl`. Insgesamt darf eine
Anfrage drei Tage offen sein. Jede Stufe prüft ihr Ergebnis an einem Tor (Pflichtzeilen des Prüfstands, Commit-IDs,
Version nie unter dem Vorschlag, Klasse der Schutzschicht): `gesperrt` beendet die Anfrage immer, `sonder` verlangt mindestens zwei
aktive Admins (sonst endet sie mit `zweiter_admin_noetig`; heute gibt es einen, die Schutzschicht bleibt also gesperrt) und ist
für die Quelle `selbst` bis Stufe 4 ganz gesperrt. Ein anderer Admin als der Beauftragte gibt `sonder` frei, das setzt die
Datenbank durch (Trigger der Migration 0013).

**Audit und Wiederaufnahme.** `audit.jsonl` mit Hashkette (wie beim Anker, `chattr +a`) ist zugleich das Write-Ahead-Log:
Eine Zustandsänderung gilt erst, wenn ihre Zeile steht; `zustand.json` ist ein Zwischenspeicher davon. Nach einem Absturz
zieht der Dienst die Zeilen hinter dem Stand der Datei nach (auch ohne die Datei). Die Datei ist an die Kette gebunden (Hash der
Zeile `audit_n`, Position des Spiegelstands): Ein zurückgespielter Stand, der über das Ende des Protokolls hinausreicht, oder ein
rotiertes Protokoll macht den Dienst defekt (er beginnt nichts Neues; `zurueck` geht weiter). Eine Stufe, die beim Absturz lief,
läuft höchstens dreimal. Die Zeilen werden in `release_ereignisse` gespiegelt (in Reihenfolge, nach einem Ausfall der Datenbank
nachgeholt, jede Zeile einzeln festgehalten; doppelt höchstens bei einem Absturz zwischen Datenbank und Merken, erkennbar an
`quelle_n`); wird der Zustand ohne Datei aus dem Protokoll neu aufgebaut, gilt der Spiegel als erledigt. `status` zeigt den
Rückstand. Hält der Anker die Sperre oder ist er nicht erreichbar, beginnt keine neue Anfrage und keine Stufe läuft weiter.
**Protokoll voll (32 MiB)** oder bewusst rotieren, nur ohne offene Anfrage: `sudo systemctl stop wamo-release`, `sudo chattr -a
/var/log/wamo-release/audit.jsonl`, Protokoll und `zustand.json` nach `/var/backups/wamo-release` verschieben, `sudo systemctl
start wamo-release`, danach `sudo chattr +a /var/log/wamo-release/audit.jsonl` (der Dienst legt die neue Datei an; die Zähler
der Anfragen des Tages beginnen dabei von vorn).

| Ort | Inhalt |
| --- | --- |
| `/opt/wamo-release/app` (root, 0755) | Module, `wamo-release.sh`, `release_rauchtest.py`, `SHA256SUMS` (`ExecStartPre` prüft sie) |
| `/var/lib/wamo-release` (wamo-release, 0700) | `zustand.json` (atomar, die Vorgängerin als `.vorher`) |
| `/var/log/wamo-release` (wamo-release, 0750) | `audit.jsonl` (Zeile höchstens 2 KiB, bei 32 MiB voll) |
| `/run/wamo-release` | `ctl.sock` |

**Einrichten (von Hand, wiederholbar, Voraussetzung: der Anker ist eingerichtet).**

```bash
ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
ssh sokrates2 "sudo bash ~/wamo-infra/release-einrichten.sh --trocken"                  # zeigt nur, was geschähe
ssh sokrates2 "sudo bash ~/wamo-infra/release-einrichten.sh"
ssh sokrates2 "sudo wamo-release status"
ssh sokrates2 "sudo python3 -E -s /opt/wamo-release/app/release_rauchtest.py --produktiv"
```

Probelauf ohne root: `WAMO_RELEASE_WURZEL=/tmp/release-probe bash release-einrichten.sh`. Tests im Ordner `voice-core`:
`uv run python -m pytest -q test_release.py test_release_socket.py test_release_skripte.py` (die Socket-Tests laufen nur unter
Linux). **Rückweg:** `sudo bash ~/wamo-infra/release-rueckweg.sh` stoppt den Dienst (der Anker und die Produktion bleiben
unberührt), mit `--entfernen` verschwindet er, Audit-Protokoll und Zustand werden nach `/var/backups/wamo-release`
gesichert; beide Skripte kennen `--trocken`.

**Grenzen und Prüfpunkte.** (a) Zum Anker kommt der Dienst über die Zusatzgruppe `wamocon` (dessen Ordner ist 2750
`root:wamocon`). Das gibt ihm die Gruppenrechte an Dateien außerhalb von `/home` (die Unit verbirgt das Home und die Geheimnisordner
anderer Dienste); besser wäre eine eigene Gruppe oder eine ACL nur für den Socket, eine Änderung am Anker. Außerdem erlaubt der
Anker dem Benutzer `wamo-release` heute schon alle Vorwärtsbefehle (nur die Sperre bremst sie, und ihre Schritt-Skripte fehlen
noch): Der Riegel gegen `sperre aus` und die Vorwärtsbefehle ist in diesem Schritt allein der Client (`release_anker.py`); für
Schritt 0.5 gehört `ERLAUBT["wamo-release"]` im Anker bis Stufe 1 auf `status`, `rueckweg` und `sperre:an`. (b) Die Unit ist auf Sokrates-2 nicht erprobt: `systemd-analyze verify` läuft im
Einrichtungsskript, `systemd-analyze security wamo-release` und der Rauchtest sind zu prüfen; `MemoryDenyWriteExecute` wurde mit einem
nachgebauten Seccomp-Filter gegen Tests und Rauchtest (Teil A) geprüft. (c) Ein Stufenaufruf, der nicht zurückkehrt, wird nach
etwa zwei Stunden erkannt: Der Dienst beendet sich mit Fehler, systemd startet ihn neu, die Wiederaufnahme übernimmt. (d) Wer root
hat, kann das Protokoll samt Kette neu rechnen (wie beim Anker). (e) Offen für 0.5: Anker-Befehle `klassifiziere`, `bereitstellen`,
`migrieren`, `umschalten` (die Liste in `release_anker.py` muss dann bewusst erweitert werden), echte Datenbank mit
`wamo_release`, Socket für Personen mit Token.

## 16. Remote Desktop (Gateway), Stufen 0 bis 2 (vorbereitet am 31.10.2026, noch nicht ausgerollt)

Konzept und Begründungen: `docs/remote-desktop.md`. Ziel: Ein Admin bedient unterwegs (WireGuard der Fritzbox, WAMO-App) den Desktop
der Sparks über ein Gateway auf Sokrates-2 (Apache Guacamole, Weg A). Gebaut sind nur die Stufen 0 bis 2 im Repo; die Anmeldung über
WAMO (`rd_api.py`, Zugangsdienst), die Oberfläche und die weiteren Ziele folgen in den Stufen 3 bis 7. Ohne Zugangsdienst kann sich
niemand am Gateway anmelden.

**Grenzen (Entscheidung vom 31.10.2026).** Höchstens 60 Minuten je Sitzung, 10 Minuten Leerlauf (am Ziel durch `IdleTimeLimit`),
ein Zuschauer je Ziel, bei jedem Start der Code der Anmeldung (seit 11.11.2026 kein frischer Code mehr, Stufenschalter `WAMO_RD_ANMELDUNG_MAX_MIN`, `docs/remote-desktop.md` Abschnitt 3.6), nur Rolle `admin`; keine Zwischenablage,
keine Laufwerke, kein Ton, keine Aufzeichnung. Das Konto `wamo-rd` hat kein sudo, keine Gruppe `docker`, kein SSH; sein Ausgang soll
nur ins Internet gehen, nicht ins LAN (Stufe 8 des Konzepts, nft-Regel `meta skuid wamo-rd` am Ziel; Empfehlung des Konzepts übernommen).

**Bausteine.**

| Baustein | Datei | Stufe |
| --- | --- | --- |
| xrdp auf Caesar einhegen (Root-Anmeldung aus, Port 3389 nur vom Gateway) | `infra/sparks/xrdp-einhegen.sh` | 0 |
| Gateway: Container, Netze, Caddy-Site 55446, Firewall | `infra/sokrates2/wamo-rd/`, `wamo-rd-einrichten.sh`, `wamo-rd-rueckweg.sh`, Ketten `WAMO-FW-RD` und `WAMO-FW-RDIN` in `wamo-firewall.sh`, Port und Netz in `wamo-caddy/docker-compose.yml` | 1 |
| Rauchtest (Testmodus ohne Server, Betrieb mit `--betrieb`) | `wamo_rd_rauchtest.py` | 1 |
| Konto `wamo-rd` mit Passwort nur in `/etc/wamo-rd/ziele.env` | `infra/sparks/wamo-rd-konto-einrichten.sh` | 2 |
| xrdp als Ziel (Sokrates-2 zuerst) | `infra/sparks/wamo-rd-ziel-einrichten.sh` | 2 |

**Stufe 0: xrdp auf Caesar einhegen (macht der Nutzer selbst, `sudo` verlangt dort ein Passwort).** Zuerst ansehen, dann ausführen:

```bash
scp infra/sparks/xrdp-einhegen.sh caesar:                 # in das eigene Home, nie nach /tmp
ssh -t caesar "sudo bash ~/xrdp-einhegen.sh --trocken"    # zeigt Version, Patchstand, geplante Regeln; ändert nichts
ssh -t caesar "sudo bash ~/xrdp-einhegen.sh --neustart"   # Root-Anmeldung aus, Port 3389 nur von Sokrates-2 und lokal
ssh -t caesar "sudo bash ~/xrdp-einhegen.sh --zurueck"    # Rückweg
```

Das Skript aktualisiert nichts. Steht ein Update an (CVE-2025-68670), sagt es das und nennt den Befehl (`apt changelog xrdp`,
dann bewusst `sudo apt-get install --only-upgrade xrdp`). Sicherungen: `/etc/xrdp/sesman.ini.vor-wamo-ZEITSTEMPEL`, `/var/backups/wamo-xrdp`.
Läuft auf Caesar kein ufw, entsteht eine eigene nftables-Tabelle `inet wamo_xrdp` (fremde Regeln bleiben unberührt), die Unit
`wamo-xrdp-sperre.service` und ein Drop-in `xrdp.service.d/wamo-sperre.conf`, das xrdp nur mit geladener Sperre starten lässt (fail-closed).

**Stufe 1: Gateway ohne Ziel einrichten** (als `wamocon` auf Sokrates-2, Ablage wie in Abschnitt 12):

```bash
ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
ssh sokrates2 "bash ~/wamo-infra/wamo-rd-einrichten.sh --trocken"     # nur anzeigen
ssh sokrates2 "bash ~/wamo-infra/wamo-rd-einrichten.sh"               # konto, bilder, firewall, caddy, start, pruefen
ssh sokrates2 "bash ~/wamo-infra/wamo-rd-rueckweg.sh"                 # Rückweg (Container, Netze, Site); mit --entfernen auch Images, Schlüssel, Benutzer
```

Der Lauf legt `wamo-caddy` einmal neu an (Port 55446, Netz `wamo-rd-netz`): 55443 bis 55445 sind wenige Sekunden weg, deshalb nur ohne
laufende Sitzung des Kerns (das Skript prüft es). Reihenfolge, wenn man einzeln vorgeht: `konto`, `bilder`, `firewall`, `caddy`, `start`, `pruefen`.
Das Netz `wamo-rd-netz` gehört zu `wamo-caddy`; die Netze `wamo-rd-guacd-netz` und `wamo-rd-ziel` legt das Compose-Projekt `wamo-rd` an.
Die Zielliste `/etc/wamo-rd/ziele.toml` ist zu Beginn leer: Die Firewall lässt vom Gateway dann nichts hinaus. Ein Ziel erst mit
Stufe 2 eintragen, danach `sudo systemctl reload wamo-firewall`.

Härtung der Container wie bei `wamo-ui` (read-only, `cap_drop: ALL`, `no-new-privileges`, Benutzer `wamo-rd-gateway`, Speicher-, CPU- und
Prozessgrenzen, kein veröffentlichter Port). Images: die offiziellen Fassungen 1.6.0 von `guacamole/guacamole` und `guacamole/guacd`,
am 31.10.2026 mit `docker buildx imagetools inspect` als arm64 belegt und über den Digest des Multi-Arch-Index festgehalten; ein
eigener Bau ist nicht nötig. Anmeldung nur mit signiertem JSON aus der festen Adresse des Zugangsdienstes (`JSON_TRUSTED_NETWORKS`,
Schlüssel aus `/etc/wamo-rd/json.key`, 0640 `root:wamo-rd-gateway`, ohne Zeilenumbruch); Fehlversuche sperren die Absenderadresse
(Erweiterung `ban`). Die echte Absenderadresse hinter Caddy liefert der RemoteIpValve, vertraut wird allein Caddy (feste Adresse 172.26.0.2).

*Aktualisierungsplan.* Neuen Digest holen (`docker buildx imagetools inspect guacamole/guacamole:<Version>`), in
`wamo-rd/docker-compose.yml` eintragen, `wamo-rd-einrichten.sh bilder start`, `python3 wamo_rd_rauchtest.py --betrieb --ca ...`;
Rückweg ist der alte Digest (`git revert`). Vor jeder Aktualisierung die Sicherheitsseite des Projekts auf neue CVEs prüfen.

*Rauchtest.* `python infra/sokrates2/wamo_rd_rauchtest.py` baut den Stack aus der echten Compose-Datei und der echten Caddy-Vorlage mit dem
Namensraum `wamo-test-rd-t1-` (eigene Netze, Caddy mit HTTP), prüft Gesundheit, Härtung, Kopfzeilen, Isolation und alle Verbotsproben
(JSON von fremder Adresse, falsche Signatur, abgelaufen, gefälschtes `X-Forwarded-For`) sowie den positiven Weg (Zugangsdienst-Attrappe),
und räumt ab; `--behalten` lässt ihn für eine Browserprobe stehen. `--betrieb --ca wamo-ca.crt` prüft die laufenden Container auf
Sokrates-2 (Isolation aus den Containern, Zielliste gegen die Kette `WAMO-FW-RD`).

*Bekannte Grenzen.* (a) Die Oberfläche von Guacamole (AngularJS) braucht `'unsafe-eval'` in `script-src`; sonst bleibt die Seite leer
(im Browser belegt). Die Herkunft 55446 hält kein WAMO-Token, kein `'unsafe-inline'` für Skripte, keine fremde Quelle. (b) `POST /api/tokens`
bleibt offen, weil die Oberfläche damit ihr Sitzungstoken erneuert; abgesichert durch `JSON_TRUSTED_NETWORKS` und die Sperre nach
3 Fehlversuchen (15 Minuten). (c) `wamo-caddy` hängt im Netz `wamo-rd-netz` wie bei `wamo-ui` und erreicht die Webanwendung; `guacd`
(unauthentisiert, kein TLS) liegt in einem eigenen internen Netz, das nur die Webanwendung teilt, und lauscht nur auf seiner festen
Adresse 172.26.1.3 dort, nie im Netz `wamo-rd-ziel` (sonst erreichte ihn jeder Prozess auf dem Host über 172.27.0.1; der Rauchtest prüft es). (d) Pakete an den Host aus dem Netz
`wamo-rd-ziel` gehen durch `WAMO-FW-RDIN` (nur TCP 3389, dann entscheidet ufw); sie ist die einzige Ergänzung in `INPUT` (ein Sprung an
Position 1). (e) Die Laufzeit der Sitzung (60 Minuten, Verlängerung) setzt der Zugangsdienst in Stufe 3 durch, nicht dieses Gateway.
(f) Der Sprung nach `WAMO-FW-RDIN` steht nach einem Neustart oder `ufw reload` womöglich nicht mehr an erster Stelle von `INPUT`; der Rauchtest
(`--betrieb`) prüft die Position, `sudo systemctl reload wamo-firewall` stellt sie wieder her. (g) Die Sperre von Guacamole zählt Fehlversuche je Absenderadresse
(3, dann 15 Minuten): Der Zugangsdienst (Stufe 3) darf ein abgelehntes JSON nicht wiederholen und muss Schlüssel und Uhrzeit vor dem Senden prüfen, sonst sperrt er sich selbst;
sein Schlüssel gehört nie als Argument eines Programms (kein `openssl enc -K` im echten Dienst). (h) `ignore-cert` am Ziel bleibt bis zum Pinning des Zertifikats
(`cert-fingerprints` in Guacamole 1.6) ein Rest; solange nur das lokale Ziel Sokrates-2 läuft, bleibt der Weg auf dem Host.

**Stufe 2: Konto und Ziel Sokrates-2** (am Ziel, mit `sudo`; Passwort tippt der Nutzer selbst). Entscheidung: xrdp. Begründung: Das Konzept
(Abschnitt 2.1) nennt xrdp als Zielzustand, weil nur er auf dieser Hardware (NVIDIA GB10, Xorg ohne Monitor) nachweislich läuft; GRD im
Systemmodus ist dort ungeprüft und braucht zwei Anmeldungen. Gebaut ist deshalb nur xrdp.

```bash
scp infra/sparks/* sokrates2:wamo-infra-sparks/            # vorher: ssh sokrates2 "mkdir -p ~/wamo-infra-sparks"
ssh -t sokrates2 "sudo bash ~/wamo-infra-sparks/wamo-rd-konto-einrichten.sh --trocken"   # dann ohne --trocken
ssh -t sokrates2 "sudo env WAMO_XRDP_QUELLE=172.27.0.0/24 bash ~/wamo-infra-sparks/xrdp-einhegen.sh --nur-sperre"
ssh -t sokrates2 "sudo apt-get install xrdp xorgxrdp"      # bewusst von Hand; vorher apt changelog xrdp, CVE-2025-68670
ssh -t sokrates2 "sudo bash ~/wamo-infra-sparks/wamo-rd-ziel-einrichten.sh --trocken"
ssh -t sokrates2 "sudo bash ~/wamo-infra-sparks/wamo-rd-ziel-einrichten.sh --neustart"
```

**Vorbedingung vor dem ersten Ziel Sokrates-2 (offene Entscheidung):** Auf Sokrates-2 ist die Desktopsitzung von `wamo-rd` ein Prozess auf demselben Host und
erreicht damit alle Dienste auf Loopback (die Firewall lässt Loopback überall durch). Die Ausgangsregel `meta skuid wamo-rd` (nur Internet, weder Loopback noch LAN,
Konzept Abschnitt 5.2) ist deshalb dort schon vor dem ersten Ziel nötig; sie ist nicht Teil von Stufe 2. Sokrates-1 und Caesar haben das Problem nicht.

Danach das Ziel in `/etc/wamo-rd/ziele.toml` eintragen (Vorlage `wamo-rd/ziele.toml.beispiel`) und `sudo systemctl reload wamo-firewall`.
Rückweg: `wamo-rd-ziel-einrichten.sh --zurueck` (Konfigurationen aus `.vor-wamo-rd-*`, Sperre weg, `.xsessionrc` weg) und
`wamo-rd-konto-einrichten.sh --entfernen` (oder `--sperren`). Die Passwortrotation ist `wamo-rd-konto-einrichten.sh --rotieren`
(alle 90 Tage und nach jedem Verdacht). Das Passwort steht nur in `/etc/wamo-rd/ziele.env`, nie auf dem Bildschirm, im Protokoll oder
in einem Argument. Die Sitzung entsteht nur für `wamo-rd` (eigene Xorg-Sitzung, Display ab `:10`); die Konsole von `wamocon` wird
nie berührt. Anmeldung nur für die Gruppe `wamo-rd-ts` (`AlwaysGroupCheck=true`), Ende bei Trennung nach 15 Minuten, Leerlauf nach 10.

*Prüfpunkte am Ziel (Stufe 2 des Konzepts).* (1) GRD als Alternative: `grdctl --system` im Systemmodus testen; läuft es ohne zweite Anmeldung
unter Xorg und NVIDIA 580, ist es wegen des unprivilegierten Dienstes vorzuziehen, sonst bleibt xrdp. (2) Schließt die Sitzung sofort,
fehlt meist `allowed_users=anybody` in `/etc/X11/Xwrapper.config`; ebenso prüfen, dass xorgxrdp keinen Platz an `seat0` belegt. (3) Zertifikat:
xrdp stellt ein selbstsigniertes aus; das Gateway nutzt vorerst `ignore-cert`, Pinning ist zu prüfen. (4) Ob `DELETE /api/session/tokens`
einen laufenden Tunnel sofort beendet (Stufe 3). (5) `wamo-rd` darf nur lesen, was für alle lesbar ist: Homes der anderen auf 0750 oder
enger prüfen (`ls -ld /home/*`).

## 17. Befehl `pruefen`: Abnahmebefehle in der Sandbox des Auftrags (Dispatcher Version 6, vorbereitet am 10.11.2026, AUS, nicht ausgerollt)

Der Dispatcher kennt den Befehl `pruefen <job>`. Er führt einen Befehl der Abnahme (`befehl:`-Kriterium, `voice-core/abnahme.py`) im
Projekt des Auftrags aus, in einer Sandbox, die **enger** ist als der Auftragslauf. Standardmäßig AUS: ohne `WAMO_PRUEFEN=an` in
`/etc/wamo/geheim.env` (jeder andere Wert und jeder Tippfehler im Namen sperrt) lehnt der Dispatcher jeden Aufruf ab, `ping` meldet
`"pruefen": false`. Die Einrichtung schaltet nichts ein.

| Bereich | Auftragslauf (`start`) | Befehl `pruefen` |
| --- | --- | --- |
| Netz | Ausgangsfilter (TCP 80 und 443, DNS) | keines: eigener Netz-Namensraum (`--unshare-net`), nur `lo` |
| Token, Claude-Zugang | die der Auftragsart | keine; Umgebung nur Basisumgebung plus Git-Hooks aus (`ERLAUBT_PRUEFEN_RE` im Läufer) |
| Claude, Plugins, Zustand des Auftrags | gemountet | nicht gemountet |
| Zeitlimit, Prozesse, Speicher | 30 min, 1024, 8 GB | 120 s fest, 256, 4 GB (`wamo-job.conf`: `PRUEFEN_*`); von jedem Wert aus `wamo-job.conf` und dem des Modus gilt der kleinere, der Modus ist nie weiter als der Auftragslauf |
| beschreibbar | Projekt, `/tmp`, `.npm`, `.cache` (je Auftrag auf der Platte, dauerhaft), Claude-Konfiguration | Projekt, `/tmp`, `.npm` und `.cache` als je Lauf eigenes, leeres tmpfs (256 MiB je Ordner, `PRUEFEN_CACHE_MB`), nie die dauerhaften Ordner der Auftragsläufe |

**Aufruf (nur der Kern, über den SSH-Schlüssel der Aufträge):** SSH-Befehl `pruefen <job>`, dazu **eine** stdin-Zeile
`{"argv": ["npm", "test"]}` (genau dieses Feld, höchstens 8 KiB). Danach bleibt stdin als Lebenslinie offen, EOF beendet den Lauf. Das
Projekt steht nie im Aufruf, es kommt aus `auftrag.json`. Antwort, eine JSON-Zeile auf stdout:
`{"v":1,"ok":bool,"exit":int|null,"abgebrochen":bool,"grund":str|null,"dauer_s":float,"ausgabe":str,"gekuerzt":bool}`. Die Ausgabe ist das
Ende (höchstens 2048 Byte), geschwärzt, ohne Steuerzeichen und nur ein JSON-String. **Erfüllt gilt nur bei Prozess-Exit 0, `ok` true und
einem echten `exit` 0.** Prozess-Exit: 0 der Befehl lief (auch wenn er scheiterte), 2 abgewiesen oder "nicht möglich", 3 Selbsttest
fehlgeschlagen, 4 belegt, 6 intern, 124 Zeitlimit, 130 abgebrochen; alles außer 0 heißt nicht erfüllt.

**Was der Dispatcher selbst erzwingt** (der Kern ist nicht vertrauenswürdig): die Allowlist aus `abnahme_befehl.py` erneut (eine Kopie
von `voice-core/abnahme_befehl.py` in `/opt/wamo/waechter`, `ping` meldet ihre `abnahme_befehl_sha256`), je Argument keine Steuerzeichen
und keine Shell-Metazeichen, den Wächter (`decide` wie ein Bash-Aufruf, nur `allow` läuft), die Sperren wie bei `start` (ein Lauf je
Auftrag, ein Auftrag je Projekt, `PARALLEL_MAX`), `pruefen` nur nach einem beendeten Lauf des Auftrags, höchstens 40 Aufrufe je
Auftrag, vorher `projekt_entschaerfen` und den Selbsttest im Modus `pruefen` (Netz zu, `/proc/net/dev` nur `lo`, Token weg, Wächter lehnt
den Sperrfall ab). Audit mit Pflichtzeile: Job, argv, Exit, Dauer, nie die Ausgabe. Die uv-Form `uv run --no-sync --offline pytest ...`
läuft nur, wenn im Projekt eine `.venv` liegt, sonst kommt "nicht möglich" mit Hinweis.

**Härtung nach der Sicherheitsprüfung (10.11.2026):**

- *Immer genau eine Zeile.* Ist der Schalter an, aber fehlt `wamo_job_pruefen.py` oder ist die Datei defekt, kommt trotzdem genau eine
  JSON-Zeile (`ok` false, fester `grund` "pruefen ist nicht verfügbar", sonst die Felder wie immer) und Prozess-Exit 6 (Fehler der
  Einrichtung, fail-closed). `cmd_pruefen` fragt `pruefen_aktiv` vor allem anderen; `ping` meldet dann `"pruefen": false`.
- *Platte.* Es gibt weiter **keine echte Quote** (`prlimit --fsize` begrenzt nur einzelne Dateien; das Projekt hat keine Quote). Als
  Eintrittsprüfung misst der Dispatcher vor jedem Aufruf: mindestens 2 GiB frei auf dem Dateisystem von `/srv/wamo`, das Projekt höchstens
  2 GiB (begrenzter Durchlauf ohne Links, Abbruch bei Überschreitung, Zeitbudget 5 s). Sonst kommt "nicht möglich" mit festem Grund
  (Prozess-Exit 2). Ein einzelner Lauf kann in seinen 120 Sekunden trotzdem weiter Platz füllen. `.npm` und `.cache` sind je Lauf ein
  tmpfs mit Größengrenze und belegen nie die Platte.
- *Pfade.* Jeder Pfad im Aufruf wird vor dem Start über Links aufgelöst (`realpath`) und muss im Projektordner bleiben, sonst wird abgewiesen
  (`grep -r` folgt einem Link auf der Kommandozeile). Ein Link im Projekt, der nach außen zeigt, macht den Aufruf also unmöglich.
- *Nach dem Lauf entschärft.* Der Dispatcher entschärft das Projekt (Hooks, `.git/config`, `.claude`, `.mcp.json`) vor und nach jedem Lauf; ein
  Fehler danach steht im Audit und verdeckt das Ergebnis nicht. `pruefen` führt **keine Buchführung der Zugänge** (`zugang_pruefen`): Der Lauf
  hat keine Zugänge, und ein Lauf ohne Zugänge schriebe die Schnittmenge auf leer und löste vor jedem späteren `start` eine Freigabekarte aus.
- *Audit.* Jede Zeile von `pruefen` im Audit (argv, Fehlertext des Selbsttests) geht durch Bereinigung (Steuer-, Format-, Richtungs-
  und Trennzeichen weg), Schwärzung und Kürzung auf 500 Zeichen; dieselbe Bereinigung trägt der Fehlertext an den Kern.
- *Sperre und cancel.* Die Sperre hängt am Prozess des Dispatchers; stirbt er (SIGKILL), läuft seine Sandbox bis `RuntimeMaxSec`. Wer die
  Sperre des Auftrags hält, beendet deshalb zuerst alle Reste der Einheiten des Auftrags. `cancel` wirkt **jobweit** (Datei `abbruch` und
  alle Einheiten des Auftrags): Der Kern ruft es nur, solange `pruefen` läuft, nie danach, denn die Datei `abbruch` sperrt jedes weitere
  `pruefen` dieses Auftrags (bis ein `start` sie löscht). Ein `cancel` zwischen der letzten Prüfung und dem Start der Einheit wird nach dem
  Lauf erkannt (Ergebnis nicht erfüllt); die letzte Prüfung steht unmittelbar vor dem Start.
- *Allowlist.* `python -m unittest` nimmt nach `tests` oder `test_...` nur noch Pakete und Module (klein geschrieben, mitten im Namen),
  Klassen (groß geschrieben) und Namen, die mit `test` beginnen; `test_x.sys.exit` lief gemessen mit Exit 0 ohne Test. Rest: Die Allowlist
  ist eine Vorauswahl, kein Schutz: Gepflanzter Projektcode läuft in jedem erlaubten Programm mit, und ein Test kann grün lügen; die Grenze ist
  die Sandbox.
- *Prüfsumme im `ping`.* `abnahme_befehl_sha256` ist nur ein **Versionsabgleich** (der Kern vergleicht damit seine Kopie). Maßgeblich ist die
  Kopie in `/opt/wamo/waechter`, die der Dispatcher selbst lädt und gegen die er jeden Aufruf erneut prüft.

**Messbefund auf Sokrates-2 (10.11.2026):** In der Sandbox gibt es python3 3.12 (ohne pytest und ruff), node 22, npm, make, grep, eslint, git
und uv; es fehlen pytest, ruff, mypy, tsc, pnpm, yarn, go, cargo und dotnet. Diese scheitern mit "nicht gefunden", das Kriterium gilt als
nicht erfüllt; das ist kein Fehler des Dispatchers.

**Ausrollen (nur auf ausdrückliches Ja, in dieser Reihenfolge):** (1) die Dateien ablegen und `wamo-job-einrichten.sh` ausführen
(sichert die ersetzten Dateien nach `/opt/wamo/vorher/<Zeitstempel>`, kopiert `abnahme_befehl.py` und `wamo_job_pruefen.py` nach
`/opt/wamo/waechter`; der Schalter bleibt aus), (2) `sudo -u wamo -H /opt/wamo/bin/wamo-pruefen-rauchtest` (der Test braucht den Schalter
nicht; offene Messpunkte: Dauer des Selbsttests, löst bwrap bloße Programmnamen über den gesetzten PATH auf, Verhalten der Fork-Bombe),
(3) `WAMO_PRUEFEN=an` in `geheim.env`, (4) erst danach der Kern (`WAMO_ABNAHME_BEFEHLE=ja`, die Anbindung ist noch zu bauen).
**Rückweg:** `sudo bash ~/wamo-infra/wamo-job-rueckweg.sh` stellt die Dateien aus der neuesten Sicherung wieder her, die noch nicht
zurückgespielt wurde, und entfernt die neuen (mit `--trocken` nur Anzeige). Die Sicherung bekommt danach die Markierung `ZURUECK.txt` und
bleibt liegen: Jeder weitere Aufruf geht eine Sicherung weiter zurück, bis alle zurückgespielt sind (dann ändert er nichts und meldet das mit
Exit 0). Eine neue Einrichtung legt eine neue, unmarkierte Sicherung an, die zuerst dran ist. Nach dem ersten Aufruf meldet `ping` wieder
Version 5. Der Rauchtest löscht sein Wegwerfprojekt nur, wenn eine Markerdatei dieses Tests darin liegt. Ein alter Kern gegen den neuen
Dispatcher bleibt unverändert; ein neuer Kern gegen den alten lehnt `befehl:` ab (`ping` ohne `pruefen`).

## 18. Kern-Kanal der Entwicklungsaufträge (Dispatcher Version 7, vorbereitet am 11.11.2026, Kanal ZU, nicht ausgerollt)

Plan und Begründungen: `docs/entwicklung-im-betrieb.md`, Pakete: `docs/entwicklung-im-betrieb-arbeitsliste.md`. Der Kern im Büro (`wamo-core@a` und `wamo-core@b`)
ruft den Dispatcher wie der PC über `ssh` auf die eigene LAN-Adresse auf, mit einem **eigenen Schlüssel in einer eigenen Datei**. Der Dispatcher erkennt den
Kanal am benutzten Schlüssel (`SSH_USER_AUTH`, `ExposeAuthInfo yes`), nie an der Quelladresse. Solange weder `authorized_keys.d/wamo-kern` noch
`/etc/wamo/kernkanal.conf` existieren (Stufe 0), ändert sich nichts: jeder `ssh`-Aufruf gilt als `pc`, der Dispatcher verhält sich wie Version 6.

| Kanal | Erkennung | Folge |
| --- | --- | --- |
| `lokal` | weder `SSH_CONNECTION` noch `SSH_ORIGINAL_COMMAND` (Betreiber, Rauchtests, Notaus) | wie Version 6, auch bei Notaus |
| `pc` | Schlüssel aus `authorized_keys.d/wamo` | wie Version 6, nur der Namensraum `kern-` ist ihm verschlossen |
| `kern` | Schlüssel aus `authorized_keys.d/wamo-kern` (gewinnt vor `pc`) | die Regeln unten |
| unklar | im strikten Modus (Kern-Schlüssel oder `kernkanal.conf` vorhanden) jede Verbindung, die sich nicht eindeutig zuordnen lässt | Ereignis `kanal_gesperrt` mit Grund `herkunft_unklar`, Exit 2, vor jedem Lesen von `geheim.env` |

**Regeln des Kern-Kanals** (Modul `wamo_job_kernkanal.py`, in dieser Reihenfolge; jeder Verstoß ist `{"type": "wamo", "ereignis": "kanal_gesperrt", "grund": <Code>}` und Exit 2,
ohne Token): (1) Notaus-Datei: nur `ping` und `status` gehen noch (`notaus`). (2) `kernkanal.conf` fehlerhaft, Kanal zu oder Ablauf überschritten
(`konfig_ungueltig`, `kanal_zu`, `abgelaufen`). (3) Befehlsliste `ping`, `start`, `status`, `pending`, `approve`, `cancel`, alles andere auch `pruefen` (`befehl`); bei geschlossenem Kanal nur
`ping`, `status`, `pending`, `cancel`. (4) nur eigene Aufträge: `auftrag.json` trägt `kanal` gleich `kern` (`fremd`). (5) Art, Repo und Dienste aus der Konfiguration, GitHub-Arten nur im
App-Modus und mit Repo (`art`, `repo`, `repo_fehlt`, `dienst`). (6) Projektname `kern-<Name>`, bei Repo genau daraus abgeleitet (`projekt`). (7) Zugang nur `folgt` mit Profil, vor
`lese_geheimnisse()` (`zugang`). (8) Fork-Prüfung beim ersten Token je Lauf (`fork`). (9) Grenzen (`grenze_start`, `grenze_laufzeit`, `grenze_folgen`, `abstand`, `platz`); jeder Start, der die erste Prüfung besteht, zählt als Versuch (höchstens 20 je Stunde, `MAX_VERSUCH_STUNDE` nur nach unten), der Abstand gilt nicht für Folgen desselben Auftrags, und ein Projektordner, den kein Kern-Auftrag angelegt hat (oder ein anderes Repo belegt hat), ist gesperrt (`projekt`). Der Dispatcher
prüft zweimal: vor den Geheimnissen und unmittelbar vor der Token-Erzeugung, damit eine Notaus-Datei dazwischen noch greift. `--max-turns` und `--max-budget-usd` gehen nur im Kern-Kanal
und nur aus `kernkanal.conf` in den Aufruf von Claude Code. `ping` meldet `kanal` und das Objekt `kernkanal` (`strikt`, `offen`, `grund`, `arten`, `repos`, `ablauf`, `tage_rest`), nie einen Wert
aus `geheim.env`.

| Datei | Besitzer, Modus | Inhalt |
| --- | --- | --- |
| `/var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs` | `wamo-core` 0600 | Schlüssel des Kerns, entsteht nur auf dem Server (`ssh-keygen` per `runuser`) und verlässt ihn nie. Der öffentliche Teil liegt nie in `~/wamo-infra` |
| `/var/lib/wamo-core/.ssh/known_hosts_wamo_jobs` | `wamo-core` 0644 | eine Zeile aus dem lokalen Hostschlüssel, abgeglichen gegen `--hostfingerabdruck` |
| `/etc/ssh/authorized_keys.d/wamo-kern` | root 0644 | genau eine Zeile `restrict,from="192.168.178.136",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 ... wamo-kern-jobs`, ohne `expiry-time` |
| `/etc/wamo/kernkanal.conf` | root:wamo 0640 | Regeln `SCHLUESSEL=wert`: `KANAL`, `ABLAUF`, `ARTEN`, `REPOS`, `DIENSTE`, `MAX_TURNS`, `MAX_BUDGET_USD`, `MAX_START_STUNDE`, `MAX_START_TAG`, `MAX_LAUFZEIT_TAG_S`, `MAX_FOLGEN`, `MIN_ABSTAND_S`, `MIN_PLATZ_MB`. Der Dispatcher lehnt unbekannte oder doppelte Schlüssel und Werte außerhalb der Bereiche ab |
| `/etc/wamo/kernkanal.notaus` | root:wamo 0640 | Sperrdatei, schon die Existenz schließt den Kanal |
| `/etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf` | root 0644, ohne Spiegel-Marke | der Schalter `WAMO_ENTWICKLUNG_BUERO=an` mit `WAMO_ENTWICKLUNG_ORT`, `WAMO_ENTWICKLUNG_PROFILE`, `WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD`, `WAMO_ENTWICKLUNG_KOSTEN_TAG_USD`; die Units behalten `WAMO_ENTWICKLUNG=aus`, die Vorprüfung bleibt unverändert |
| `/opt/wamo/waechter/wamo_job_kernkanal.py` | root:wamo 0640 | das Modul des Dispatchers (durch `wamo-job-einrichten.sh`) |
| `/opt/wamo-core/bin/wamo-job-kern-rauchtest` | root 0755 | Rauchtest (aus `wamo_job_kern_rauchtest.py`, installiert durch `schluessel`) |
| `/srv/wamo/logs/kernkanal.jsonl` | wamo | Zähler: je Start und Ende eine Zeile (`t`, `job`, `ereignis`, `dauer_s`) |
| `/var/backups/wamo-job-kern/<JJJJMMTT-HHMMSS>/`, `/var/log/wamo-job-kern/aktionen.jsonl` | root 0700, 0640 | Sicherungen (nie ein privater Schlüssel) und die Bedienspur: jede Handlung als Zeile ohne Werte, dazu `logger -t wamo-job-kern` |

**Einrichten und Bedienen** (alle als `sudo bash ~/wamo-infra/<Skript>` über `ssh wamo-sokrates2`, nur bei Ruhe, nie ein Neustart eines Dienstes; Exit-Codes: 0 ok, 1 Fehler, 2 falsche Aufrufform,
3 Vorbedingung nicht erfüllt, 4 teilweise umgesetzt):

1. `wamo-job-einrichten.sh` (Dispatcher 7, sshd-Block, Modul). Es bricht ohne Ruhe mit Exit 3 ab, sichert die vorige `wamo.conf` dauerhaft und der Totmannschalter stellt sie wieder her.
2. `wamo-job-kern-einrichten.sh schluessel --trocken`, dann `schluessel --ablauf <JJJJMMTT> --hostfingerabdruck <SHA256:...>`: Vorbedingungen (OpenSSH ab 7.6, `fail2ban` mit Ausnahme für
   192.168.178.136 und 127.0.0.1, Dispatcher 7, sshd-Block, Ruhe), Schlüssel, known_hosts, Zeile, `kernkanal.conf` mit `KANAL=zu`, Rauchtest, Ja-Probe `ping`; scheitert sie, wird die Zeile zurückgenommen.
3. `/opt/wamo-core/bin/wamo-job-kern-rauchtest --produktiv` (als root): alle Ja- und Nein-Proben, Isolation, Dateirechte, PC-Datei unverändert. **Alle Proben sind nur am Server prüfbar**; die Übergangsproben
   (Absturz-Neustart, Rückweg, PC und Kern gleichzeitig, WireGuard, Notaus mit laufendem Auftrag) sind Handarbeit.
4. `wamo-job-kern-einrichten.sh schalter an <release> --profil <user_id> --kosten-auftrag 3 --kosten-tag 10` (Vorabprüfungen: Schlüssel, known_hosts, Rauchtest, `SCHRANKEN_VERSION` im Zielrelease, kein
   `WAMO_ENTWICKLUNG*` in `core.env`, keine gleichnamige Datei in `wamo-core.service.d`, Ruhe), dann `wamo-core-slots.sh umschalten-kern`, `bewaehren 30`, `ausleiten`. Scheitert das vor dem Umschalten,
   kommt das vorige Drop-in zurück; nach dem Umschalten bleibt es und das Skript endet mit Exit 4. Gleicher Stand ist ein No-op.
5. `kanal --arten recherche` (später `--repos <owner>/<name>` und weitere Arten) öffnet, `kanal zu` schließt (geht immer). GitHub-Arten öffnen nur, wenn `claude --help` als `wamo` beide Flags kennt und die Repos
   (höchstens 10, nie das WAMO-Repo) benannt sind. `status` zeigt Resttage, Kanalzustand, den Schalter je Slot aus `/proc/<MainPID>/environ` (nur Namen und der Schalterwert, nie die Profil-Kennung),
   laufende Kern-Aufträge, die Zahl der Kern-Ordner und jede Abweichung.

**Notaus und Rückweg.** `wamo-job-kern-rueckweg.sh notaus` wirkt ohne Neustart des Kerns, in fester Reihenfolge: (1) Sperrdatei anlegen, (2) Schlüsselzeile sichern und löschen, `KANAL=zu` setzen und das Schlüsselpaar von wamo-core beiseitelegen (Endung `.notaus-<Zeit>`), (3) alle Aufträge mit `kanal=kern`
per lokalem `cancel` beenden (laufende zuerst, Sandbox gestoppt, Token widerrufen; ein fehlgeschlagenes `cancel` bricht nie vor Schritt 4 ab), (4) erneut suchen, bis keiner mehr läuft, und das Ergebnis protokollieren. Wieder öffnen: `notaus-aufheben` öffnet nichts, `schluessel` legt ein neues Paar an (nie den womöglich kompromittierten Schlüssel), erst `kanal` öffnet. Der Baum `/var/lib/wamo-core/.ssh` gehört dem Kern: beide Skripte arbeiten dort nur als wamo-core und verweigern bei einem Link.
`wamo-job-kern-rueckweg.sh --entfernen` verweigert, solange das Drop-in besteht (erst `schalter aus`) oder ein Kern-Auftrag läuft, und entfernt sonst Schlüsselzeile, Schlüsselpaar, known_hosts,
`kernkanal.conf` und Sperrdatei. `--trocken` zeigt nur an. **`wamo-job-rueckweg.sh` verweigert mit Exit 3, solange `authorized_keys.d/wamo-kern` besteht:** ein älterer Dispatcher kennt den Kern-Kanal nicht,
die Zeile des Kerns bekäme die Rechte des PC-Kanals. Der Kern selbst bleibt bei jedem Rückweg unberührt: ältere Releases lesen `WAMO_ENTWICKLUNG_BUERO` nicht und starten mit Entwicklung aus.

**Ablauf.** Die Schlüsselzeile läuft nicht über `expiry-time` (eine unbekannte Option macht die ganze Zeile ungültig), sondern über `ABLAUF=JJJJMMTT` in `kernkanal.conf`: bis einschließlich dieses Tages, höchstens 400 Tage
voraus, Standard 30 Tage. Der Dispatcher erzwingt ihn (`abgelaufen`, hörbar statt „Schlüssel abgelehnt“). Verlängern: `schluessel --ablauf <JJJJMMTT> --hostfingerabdruck <SHA256:...>` (sichert die alte Konfiguration).

**Risiko des Claude-Zugangs und Widerrufsstelle.** Der Kern kennt über `WAMO_SCHLUESSEL_KEK` die Zugänge aller Profile, ein übernommener Kern wäre ein Risiko für alle. Im Kern-Kanal gilt deshalb nur der eigene
Zugang des Profils je Lauf (zweite stdin-Zeile, Quelle `token` oder `api`), nie der Serverzugang. Der Zugang erreicht die Sandbox, und Port 443 ist dort bewusst offen: Ein langlebiges Claude-Code-Token kann der
Dispatcher **nicht widerrufen**. Darum ist ein API-Schlüssel mit Ausgabenlimit im Workspace die bevorzugte Wahl, ein Claude-Code-Token nur mit Rotation nach den Stufen 4 und 5. Die Widerrufsstelle liegt im Konto,
bei dem der Zugang erzeugt wurde (API-Schlüssel: Schlüsselverwaltung des Workspaces, Claude-Code-Token: Kontoeinstellungen). Niko notiert den Weg dorthin vor Stufe 3a (Arbeitsliste N4), nie einen Wert; im Repo
und auf dem Server steht kein Zugangswert im Klartext.

**Was bleibt gesperrt:** Selbständerung (`SELF_REPO = "wamo"` im Kern und im Dispatcher, dazu die Fork-Prüfung), Supabase und `deployment` in der Sandbox, der Befehl `pruefen` im Kern-Kanal (auch mit
`WAMO_PRUEFEN=an`), Jira und Vercel, bis ein enges Konto in `DIENSTE` steht. `PARALLEL_MAX = 1` gilt für beide Kanäle.

## 19. Labor-Dienst (Test-Jira des Testlabors, Betrieb, vorbereitet am 12.11.2026, noch nicht ausgerollt)

Der Dienst selbst liegt im Repo unter `labor-dienst/` (Vertrag `docs/labor-dienst-vertrag.md`, Umgebung `WAMO_LABOR_DIENST_*`, Weboberflächen `/board`, `/betreuer` und
`/datenschutz`, Schnittstellen `/api/kern/`, `/api/browser/`, `/api/betreuer/`, `/gesundheit`). Dieser Abschnitt beschreibt nur seinen Betrieb auf Sokrates-2
für das Testlabor am 28.11.2026. Alles läuft als root (sudo), nach dem Muster des Remote-Desktop-Gateways (Abschnitt 16): feste Namen, feste Adressen,
Probelauf mit `--trocken`, Sicherung vor jeder Änderung, Rückweg, Rauchtest.

```text
Besucher ──HTTPS 55447──> wamo-caddy ──HTTP 8080 (Netz wamo-labor-netz, intern)──> wamo-labor-dienst
Kern     ──HTTP 127.0.0.1:18765 (Loopback)────────────────────────────────────> wamo-labor-dienst   (Caddy reicht /api/kern/ nie weiter)
```

| Baustein | Datei |
| --- | --- |
| Compose des Dienstes (Container, Netze, Härtung, Bind-Mounts) | `wamo-labor/docker-compose.yml` |
| Caddy-Site auf 55447 (Vorlage mit Platzhalter für die Betreuer-Adressen) | `wamo-caddy/kern/wamo-labor.caddy` |
| Port 55447 und Netz `wamo-labor-netz` (172.28.0.0/24, Caddy 172.28.0.2) | `wamo-caddy/docker-compose.yml` |
| Kette `WAMO-FW-LABOR` (Quellen für 55447) und `WAMO-FW-LABAUS` (Ausgang des Dienstes) | `wamo-firewall.sh` |
| Einrichten (`einrichten`, `status`, `pruefen`, `--trocken`) | `wamo-labor-einrichten.sh` |
| Rückweg | `wamo-labor-rueckweg.sh` |
| Löschlauf nach der Aufbewahrung | `wamo-labor-loeschlauf.sh` |
| Labor-Daten des Kerns (Regeln, Kontakte, Vorlagen): Unterordner `/etc/wamo-labor/daten`, Abschnitt 20 | `wamo-labor-daten-einrichten.sh` (setzt Dateien und `WAMO_LABOR_DATEN`, fasst die Token und Adressdateien nicht an) |
| Rauchtest (20 Proben) und seine Attrappe für den Lauf ohne Server (`--attrappe`) | `wamo_labor_rauchtest.py`, `wamo_labor_rauchtest_attrappe.py` |
| Tests der Skripte, der Compose-Dateien, der Site und des Rauchtests (Attrappen) | `voice-core/test_labor_betrieb_skripte.py` |

**Einrichten (Handgriffe von Niko, nichts davon läuft ohne ihn).**

```bash
# 1. Digest des Basis-Images am Server holen (kein wanderndes Tag, Zeile "Digest: sha256:..." des Index)
ssh sokrates2 "docker buildx imagetools inspect python:3.11-slim"
# 2. Ablage: Infrastruktur und die Quellen des Dienstes ins Home von wamocon (nie nach /tmp)
ssh sokrates2 "rm -rf ~/wamo-infra ~/wamo-labor-quelle" && scp -r infra/sokrates2 sokrates2:wamo-infra && scp -r labor-dienst sokrates2:wamo-labor-quelle
ssh sokrates2 "chmod -R go-w ~/wamo-infra ~/wamo-labor-quelle"
# 3. nur anzeigen, was geschehen würde
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh einrichten --trocken --basis-image python:3.11-slim@sha256:<Digest>"
# 4. einrichten (Vorprüfung, Sicherung, Ordner und Token, Image, Firewall, Caddy, Start, Kern, Rauchtest)
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh einrichten --basis-image python:3.11-slim@sha256:<Digest>"
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh status"          # nur Namen und Zustände, nie Werte
```

Das Skript prüft vorab die Werkzeuge (`docker`, `curl`, `ss`, `journalctl`, sonst Abbruch mit klarer Meldung), `wamo-caddy`, die Kennung 10001 (darf auf dem Host keinem
anderen Konto gehören), die Quellen, freie Netzadressen (172.28.0.0/24 und 172.28.1.0/29), das Basis-Image, dass 55447 und 18765 frei sind (oder dem eigenen Stack gehören, wie beim
zweiten Lauf) und dass beide Compose-Dateien gültig sind (`docker compose config -q`). Es erzeugt beim ersten Lauf `kern-token` und `betreuer-token` (je 32 Zufallsbytes, URL-sicher,
Modus 0400, Besitzer 10001) und gibt sie nie aus. Ein späterer Lauf lässt sie unberührt. Der Bau geschieht auf dem Server (kein `--platform`); danach müssen Basis-Image und Image die
Architektur des Servers haben (`uname -m`, auf Sokrates-2 arm64), sonst bricht das Skript ab.

**Caddy in zwei Stufen.** Stufe 1 installiert die Site und lädt Caddy neu (`caddy reload`): Das prüft die Konfiguration atomar und ohne Ausfall. Weil 55447 im Container noch nicht
veröffentlicht ist, gilt die Site vorerst nur im Container (gewollt). Erst Stufe 2 ändert das Compose von `wamo-caddy` (Port 55447, Netz `wamo-labor-netz`) und legt den Container einmal
neu an. Das legt 55443 bis 55446 kurz lahm, deshalb verlangt das Skript vorher Ruhe: keine Sitzung im Kern und keine offene Verbindung an 55443 bis 55447 (gezählt mit `netstat` im
Container, sonst mit `ss` auf dem Host). Das meldet es früh in der Vorprüfung und zeigt es im Trockenlauf an, im Echtlauf bricht es ab. Nach der Änderung und nach jeder Rücknahme
müssen 55443 bis 55446 wieder antworten (Zeitgrenze, Meldung, Ausgang ungleich 0). Die Ausfalldauer (Zeit zwischen Stopp und erster Antwort) gibt das Skript nach dem Lauf aus;
**sie ist am Server zu messen**, vorab gibt es dafür keine Zusage. Bei einem Fehler nimmt das Skript die Änderung an Caddy und den Start zurück. `WAMO_LABOR_URL=http://127.0.0.1:18765`
und `WAMO_LABOR_TOKEN` setzt es über `wamo-core-env setzen` (nur über stdin). Der Kern liest sie erst mit dem nächsten Kern-Release (`wamo-core-slots.sh umschalten-kern`), `wamo-core`
wird nie neu gestartet. Alle drei Skripte prüfen wie `wamo-rd-einrichten.sh` die Herkunft ihres Ordners (Besitzer root oder der aufrufende Benutzer, nichts für Gruppe oder alle
beschreibbar, keine Links), auch der Unterbefehl `pruefen`.

**Adressen.** Der Server selbst (Loopback, das Gateway 172.24.0.1 von `wamo-caddy_default` unter dem Caddy Verbindungen des Hosts sieht, und 192.168.178.136)
erreicht immer alles. Dazu zwei Listen in `/etc/wamo-labor` (nach dem ersten Lauf als Vorlage mit Kommentaren, eine Adresse oder ein Netz je Zeile):
`betreuer-adressen` (wer `/betreuer` und `/api/betreuer/` erreicht, Netze nur von /24 bis /32, nach einer Änderung `einrichten` erneut ausführen, dann setzt das
Skript die Adressen in die Site ein) und `plaetze-adressen` (Quellen für 55447 in der Firewall, Frage Q13: bis die Liste da ist, gilt das Büro-LAN; nach einer
Änderung `sudo systemctl reload wamo-firewall`). `/api/kern/` erreicht von außen nie jemand, `/gesundheit` nur der Server selbst. Alles außerhalb der Pfadliste
(`/`, `/board`, `/datenschutz`, `/web/<Datei>`, `/api/browser/<Name>`, Betreuer-Pfade, `/gesundheit`) beantwortet Caddy mit 404.

**Ohne Ausgang (Vertrag D2) trotz veröffentlichtem Port, und was daran hängt.** Docker veröffentlicht keine Ports über ein internes Netz. Der Dienst hängt deshalb in zwei Netzen:
`wamo-labor-netz` (intern, nur mit Caddy) und `wamo-labor-lokal` (nicht intern, nur für `127.0.0.1:18765`, Maskierung aus). Aus dem zweiten Netz verwirft `WAMO-FW-LABAUS` alles, was
weitergeleitet wird (Internet, Büro-LAN, fremde Container), und `WAMO-FW-LABIN` lässt zum Host (INPUT, Bridge-Adresse 172.28.1.1) nur Antworten auf Verbindungen des Hosts durch.
**Der Ausgang hängt allein an `wamo-firewall`:** Mehr Schutz gibt es nicht. Nach `systemctl stop wamo-firewall` (oder `wamo-firewall stop`) sind alle Labor-Ketten weg, der Dienst hat dann
wieder einen Weg nach außen und zum Host. Deshalb vorher den Dienst stoppen (`wamo-labor-rueckweg.sh`). Der Rauchtest prüft es von innen (P14: Internet, Router, Namensauflösung; P19: aus
einem fremden Container ist der Dienst nicht erreichbar, A10; P20: aus dem lokalen Netz ist 172.28.1.1:22 zu), jeweils mit einer Kontrolle, damit eine Probe nie nur deshalb besteht, weil sie
selbst nicht funktioniert. Zusätzlich verwirft `WAMO-FW` Pakete mit dem ursprünglichen Ziel 18765, die nicht von Loopback kommen (falls `route_localnet` je auf 1 steht).

**Regeln für die Adressliste der Plätze.** `plaetze-adressen` nimmt nur private Bereiche nach RFC 1918 mit mindestens /16 (10.x und 192.168.x, einzelne Adressen als /32). 172.16.0.0/12 ist der
Docker-Bereich und nie erlaubt (damit auch kein Docker-Netz des Labors und keine Liste, die den ganzen Bereich überdeckt), alles Ungültige wird ausgelassen und gemeldet. Docker-Absender
gelten nur über eine Brücke als echt (Wache `! -i br-+`). **Auch die Betreuer-PCs müssen in dieser Liste stehen**, sonst erreichen sie die Seite nicht. Vor jedem `systemctl reload wamo-firewall`
`sudo wamo-firewall regeln` ansehen: Es zeigt, was `start` setzen würde.

**Prüfen.** `wamo-labor-einrichten.sh pruefen` (läuft am Ende von `einrichten` mit) startet den Rauchtest: Container gesund, Benutzer 10001, `read_only`,
`cap_drop ALL`, `no-new-privileges`, Grenzen, Netze und Adressen, Port 18765 nur auf Loopback, Dateirechte, TLS-Kette und Schutzköpfe auf 55447, `/api/kern/`
über Caddy nie erreichbar (sechs Schreibweisen), Betreuer-Pfade und `/gesundheit` von einer fremden Adresse nicht erreichbar (kurzlebiger Container auf dem
Netz von `wamo-caddy`) mit Gegenprobe, kein Weg nach außen, Firewall-Ketten, Roundtrip V1 des Kerns über Loopback, `core.env`, Aufbewahrung, A10 und die Bridge-Adresse (20 Proben). P01 ist der Beleg "Image gebaut und gestartet" (Architektur wie der Server). Jede Probe
nennt in der Ausgabe, was sie belegt und was nicht, und unter der Tabelle steht, was nur von Hand prüfbar ist (Caddy sieht echte LAN-Adressen nur von einem Büro-PC: dort `/betreuer` und `/gesundheit` öffnen, je 403). Ohne Server: `python wamo_labor_rauchtest.py --attrappe` (alles grün) und `--attrappe --fehler kern_offen`
(die Probe dafür wird rot). Danach kommt das Abnahmeskript (`scripts/wamo_labor_abnahme.py`, README von `labor-dienst/`) und von Hand A10 des Vertrags.

**Rückweg.** `sudo bash ~/wamo-infra/wamo-labor-rueckweg.sh [--trocken]` entfernt Container und das Netz `wamo-labor-lokal`, die Caddy-Site (mit Reload) und spielt
die älteste Sicherung der Firewall ohne Labor-Kette wieder ein (danach werden die Ketten `WAMO-FW-LABOR` und `WAMO-FW-LABAUS` entfernt). Daten und Token bleiben.
Port 55447 und das Netz im Compose von `wamo-caddy` bleiben (ohne Site lauscht Caddy dort nicht), ebenso `WAMO_LABOR_URL` und `WAMO_LABOR_TOKEN` in `core.env`
(`wamo-core-env loeschen WAMO_LABOR_URL WAMO_LABOR_TOKEN`, wirkt mit dem nächsten Kern-Release).

**Aufbewahrung und Löschlauf (Vertrag D10).** Der Dienst löscht seine Daten ab dem 11.12.2026 selbst, solange er läuft. Die Container-Protokolle sind auf 3 Dateien zu
10 MB begrenzt und verschwinden mit dem Container. Nach der Frist (frühestens am 12.12.2026) löscht
`sudo bash ~/wamo-infra/wamo-labor-loeschlauf.sh --bestaetigung LABOR-LOESCHEN` Container, Daten (nach Kontrolle der festen Pfade, kein symbolischer Link), Token, alle Tags
des Images und die Kern-Einstellungen und schreibt einen Nachweis nach `/var/log/wamo-labor/loeschnachweis-<Zeit>.txt`: Namen, Größen und Zählungen, nie Inhalte. Ohne das
Bestätigungswort zeigt das Skript nur den Plan (Ausgang 3). Dieselbe Löschung vor der Frist: `wamo-labor-rueckweg.sh --daten-loeschen --bestaetigung LABOR-LOESCHEN`. Die
Sicherungen des Einrichtens (`/var/backups/wamo-labor`) enthalten weder Token noch Daten. Nicht in der Hand des Skripts: Snapshots und Sicherungen des Servers über den
11.12.2026 hinaus (der Nachweis nennt es, der Betrieb prüft es von Hand).

**Offene Punkte (vor dem Labortag).**

1. **Q13, Adressen der Plätze.** Niko nennt die Büro-Adressen der Plätze und der Betreuer-PCs. Bis dahin gilt für 55447 das ganze Büro-/24. Eintragen in `/etc/wamo-labor/plaetze-adressen`
   (Regeln oben), `sudo wamo-firewall regeln` ansehen, dann `sudo systemctl reload wamo-firewall` (vorher die Verbindungsliste lesen, wie bei den anderen Ports).
2. **Image-Bau und Digest am Server.** Der Digest des Basis-Images muss am Server geholt werden (Schritt 1 oben). Der Bau dort ist ungetestet und geschieht bewusst auf der Architektur des
   Servers (arm64): Das Skript bricht ab, wenn Basis-Image oder Image eine andere Architektur haben. Ob die Hashes in `labor-dienst/requirements.txt` für aarch64 reichen, wird gesondert geprüft
   (`pydantic-core` trägt 137 Hashes). Scheitert `pip install --require-hashes` an einem fehlenden Hash, Pins und Hashes mit `pip-compile --generate-hashes` neu erzeugen (README von `labor-dienst/`).
3. **Start mit echten Secrets.** Der erste Start mit den echten Token ist der erste Lauf überhaupt gegen den Server. Sollte die Veröffentlichung auf Loopback über das zweite Netz wider
   Erwarten nicht greifen (Probe P08 oder P16 rot), ist das Netz `wamo-labor-lokal` der erste Verdächtige.
4. **Betreuer-Token holen.** Der Betreuer-Token geht später über ein PC-Skript per ssh in eine lokale Datei auf dem PC des Betreuers (Folgeschritt, das Einrichtungsskript hat dafür
   bewusst keinen Befehl und zeigt keinen Token an).
5. **Lesefrist der Kopfzeilen.** Caddy kennt `read_header` nur für den ganzen Server (Block `servers` im Caddyfile), nie je Site. Die Site setzt bewusst nur Fristen zum Dienst
   (`dial_timeout`, `response_header_timeout`, `read_timeout`), damit keine bestehende Site berührt wird. Der Dienst schließt unvollständige Kopfzeilen nach 10 Sekunden selbst.
   Ein `servers :55447 { timeouts { read_header 10s } }` im Caddyfile wäre ein eigener, kleiner Schritt (Reihenfolge der `servers`-Blöcke beachten).
6. **Ausfalldauer von 55443 bis 55446 ist am Server zu messen.** Stufe 2 legt `wamo-caddy` neu an. Das Skript verlangt Ruhe, misst die Zeit zwischen Stopp und erster Antwort und gibt sie aus;
   notiert man sie beim ersten Lauf, gibt es erstmals eine belastbare Zahl.
7. **Protokoll von `wamo-caddy` (Restrisiko).** `docker logs wamo-caddy` kann bei Fehlern Besucheradressen enthalten und ist an die Container-ID gebunden. Es verschwindet erst, wenn der Container
   neu angelegt wird. Der Löschlauf tut das bewusst nicht (es legte 55443 bis 55446 lahm) und nennt es im Nachweis. Vorschlag: das nächste ohnehin nötige Neuanlegen von `wamo-caddy` nach dem Labor
   (zum Beispiel mit dem nächsten Compose-Update, in einer ruhigen Minute, frühestens nach dem 11.12.2026) dafür nutzen.
8. **Der Ausgang hängt allein an `wamo-firewall`.** Es gibt keine zweite Schicht außer der abgeschalteten Maskierung. Bei `systemctl stop wamo-firewall` ist er offen (siehe oben): vorher den Dienst stoppen.

## 20. Labor-Daten des Testlabors (vorbereitet am 13.11.2026, nicht ausgerollt)

Regeln, Kontakte, Vorlagen, Fachwissen und Schlusstext des Testlabors vom 28.11.2026 liegen als Dateien in `infra/sokrates2/labor-daten/` (Zweck, Zuständigkeiten und Freigabeablauf: dortige `README.md`) und
auf dem Server unter `/etc/wamo-labor/daten/`. Der Kern liest sie bei jedem Sitzungsstart (`voice-core/labor_daten.py`), eine Änderung braucht kein Release und keinen Neustart.
Der Ort ist nicht `/etc/wamo`: Der Benutzer `wamo-core` kann `/etc/wamo` (root:wamo 0750, dort liegen die Geheimnisse) nicht durchqueren und könnte darunter nichts lesen (der Lader meldete bei allen
Dateien `nicht_lesbar`). Eine ACL oder Rechteänderung an `/etc/wamo` gibt es dafür nicht. `/etc/wamo-labor` legt `wamo-labor-einrichten.sh` (Abschnitt 19) als root:root 0755 an, der Kern kann ihn durchqueren.
Der Standardordner im Lader (`STANDARD_VERZEICHNIS` in `voice-core/labor_daten.py`) bleibt unverändert, der Kern bekommt den Ordner über die Einstellung `WAMO_LABOR_DATEN`.

| Pfad | Besitz, Modus | Inhalt |
| --- | --- | --- |
| `/etc/wamo-labor/` | root:root 0755 | gehört `wamo-labor-einrichten.sh` (Abschnitt 19), Voraussetzung dieses Skripts: echter Ordner, kein Link |
| `/etc/wamo-labor/daten/` | root:root 0755 | der Ort der Labor-Daten, keine Links, nie `/etc/wamo` |
| `/etc/wamo-labor/daten/persona-zusatz.md` | root:root 0644 | Regelteil, Standardfassung oder strenge Fassung (Wahl mit `--persona`) |
| `/etc/wamo-labor/daten/persona-zusatz.streng.md` | root:root 0644 | die vorbereitete strenge Fassung, vom Lader nie gelesen |
| `/etc/wamo-labor/daten/{kontakte.md,anforderung-vorlage.md,wissen-istqb.md,bericht-vorlage.md,ausgang.md,ctfl-zuordnung.json}` | root:root 0644 | Datenteil, zusammen mit dem Regelteil höchstens 6000 Zeichen |
| `/var/backups/wamo-labor-daten/<JJJJMMTT-HHMMSS>/` | root 0700 | Sicherung des vorigen Stands, die Datei `VORHER` nennt das Ziel und schließt die Sicherung ab (eine Sicherung des früheren Ortes zählt nie) |

**Einrichten und Bedienen** (nur als root, `sudo bash ~/wamo-infra/wamo-labor-daten-einrichten.sh <Unterbefehl>` über `ssh wamo-sokrates2`, Ablage wie in Abschnitt 0; Exit-Codes: 0 ok, 1 Fehler, 2 falsche Aufrufform,
3 Vorbedingung nicht erfüllt):

1. `einrichten --trocken`: prüft die Lieferung mit dem echten Lader in einem Wegwerf-Ordner und zeigt je Datei neu, geändert oder unverändert. Es ändert nichts.
2. `einrichten [--persona standard|streng] [--release NAME]`: sichert den vorigen Stand, installiert die acht Dateien, prüft sie am Ziel noch einmal mit dem Lader und nimmt bei jeder Ablehnung alles zurück
   (Exit 1). Ohne `--persona` bleibt die vorhandene Fassung der Regeln. Ein zweiter Lauf mit gleichem Inhalt ändert die Dateien nicht. Danach setzt das Skript für den Kern genau eine Einstellung,
   `WAMO_LABOR_DATEN=/etc/wamo-labor/daten`, über `/usr/local/sbin/wamo-core-env setzen` (stdin, auch bei unverändertem Stand, gleicher Wert). Sie gilt **erst mit dem nächsten Kern-Release**
   (`wamo-core-slots.sh umschalten-kern <release>`), der Kern wird nie neu gestartet. Bis dahin liest er den Standardort und läuft mit dem reinen Laborprompt.
3. `status`: Rechte, Prüfsummen (die ersten 16 Zeichen von SHA-256, wie in der Logzeile `Labor-Daten:` des Sitzungsstarts), Vergleich mit der Lieferung, gewählte Fassung, ob `WAMO_LABOR_DATEN` beim Kern gesetzt ist (nur gesetzt oder nicht), Urteil des Laders, Sicherungen. Nie Inhalte.
4. `rueckweg [--trocken]`: stellt die letzte vollständige Sicherung für diesen Ort wieder her, der Stand davor bleibt als `<Zeit>.vor-rueckweg` liegen. Die Einstellung des Kerns bleibt unverändert.

Voraussetzung ist `/etc/wamo-labor` als echter Ordner (sonst Exit 3 mit dem Hinweis, zuerst `wamo-labor-einrichten.sh` auszuführen) und `wamo-core-env`. Der Lader läuft als `wamo-core` (so wie der Kern liest) und bekommt den
Ordner wie der Kern über `WAMO_LABOR_DATEN`, aus dem jüngsten Release mit `voice-core/labor_daten.py` und dessen `venv`, mit `--release NAME` aus einem bestimmten. Fehlt das Paket `loguru` oder gibt es
kein solches Release (der Lab-Release des Kerns muss zuerst angelegt sein), bricht das Skript vor jeder Änderung mit Exit 3 ab. Es startet nichts neu und fasst `/etc/wamo` nie an.

**Mail-Domäne.** Die Kontakte nutzen `labor.test`, die Standard-Domäne des Mail-Filters von `mail_senden` (`DOMAIN_STANDARD` in `voice-core/labor_mail.py`). Der Kern bleibt unverändert, ein Test prüft die
Übereinstimmung. Das Skript liest `core.env` nicht, es setzt dort nur `WAMO_LABOR_DATEN` über `wamo-core-env`.

Probelauf ohne root in einem Wegwerf-Ordner: `WAMO_LABOR_DATEN_WURZEL=/tmp/labor-probe WAMO_LABOR_DATEN_LADER=<voice-core> WAMO_LABOR_DATEN_PYTHON=<python mit loguru> bash wamo-labor-daten-einrichten.sh einrichten`. Tests im Ordner
`voice-core`: `python -m pytest -q test_labor_daten_inhalt.py test_labor_daten_skript.py`. Was echte Besitzer und den Benutzer `wamo-core` braucht (Herkunftsschutz mit fremdem Besitzer, Lader als `wamo-core`), ist nur am Server prüfbar.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
