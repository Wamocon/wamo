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
  API. In jedem Zielrepo deshalb ein Ruleset für den Standardbranch einrichten und verbindlich so setzen: kein direkter
  Push, Pull Request Pflicht, mindestens eine Genehmigung durch einen Menschen, die App nicht als „Bypass actor“
  eintragen. Ohne diese Regel kann ein Auftrag seinen eigenen Pull Request mergen. Der Wächter verlangt für `git push`
  ohnehin eine Freigabe, das Token selbst bleibt aber in der Sandbox nutzbar (der Ausgangsfilter lässt Port 443 offen).
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
| SSH | `/etc/ssh/authorized_keys.d/wamo`, `/etc/ssh/sshd_config.d/wamo.conf` | Schlüssel mit `restrict,from="192.168.178.0/24",command=...`; `Match User wamo` mit `ForceCommand`, nur Public Key, keine Weiterleitungen, kein TTY, `PermitUserRC no`, `MaxSessions 2` |
| Version | `CLAUDE_VERSION` in `/opt/wamo/wamo-job.conf` | nach einem Update von Claude Code startet nichts, bis das Einrichtungsskript und der Rauchtest liefen |
| Modell | `modell` je Karte in `voice-core/werkzeugkarte.py` | Standard für alle Arten `claude-haiku-5-5` (Sonnet kostet etwa das Dreifache); `claude-sonnet-5-5` oder `claude-opus-5-5` nur über `WAMO_ENTWICKLUNG_MODELL` in der `.env` des PCs (nur Namen aus `MODELLE_ERLAUBT`, über die Kopfzeile an den Dispatcher) |
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
| Vorprüfung | `ExecStartPre` | keine `.env` im Code-Baum (bot.py lädt sie mit Vorrang), Pflichtwerte gesetzt, DSN mit `verify-full`, Entwicklung aus, Socket-Verzeichnis 0750 wamo-core:wamocon |
| Geheimnisse | `/etc/wamo/core.env` (root:wamo-core 0640) | nur über `sudo /usr/local/sbin/wamo-core-env setzen` (stdin, Schlüssel-Allowlist, atomar); `liste` zeigt nur Namen. Was die Unit festlegt (Betriebsart, Socket-Pfade, `SUPABASE_URL`, CA, erlaubte Herkunft, Entwicklung aus) lässt sich dort nicht überschreiben |
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
| GoTrue | `/home/wamocon/supabase-wamo/.env` und `docker-compose.wamo-sitzungen.yml` | `GOTRUE_SESSIONS_TIMEBOX` (168h) und `GOTRUE_SESSIONS_INACTIVITY_TIMEOUT` (72h), geprüft gegen gotrue v2.196.0; erstellt nur den Auth-Container neu |

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
Browser ──HTTPS 55445──> wamo-caddy ──HTTP 3000, Netz wamo-ui-netz (internal)──> wamo-ui (Next.js, Standalone)
   ├──HTTPS 55443──> wamo-caddy ──> Envoy (Anmeldung, Supabase)
   └──HTTPS 55444──> wamo-caddy ──> wamo-core (Kern, Unix-Socket); Medien als WebRTC direkt (UDP)
```

### 12.1 Bausteine

| Baustein | Ort | Zweck |
| --- | --- | --- |
| Image und Container | `infra/sokrates2/wamo-ui/Dockerfile`, `docker-compose.yml`; auf dem Server `/home/wamocon/wamo-ui` | `npm ci`, `next build` (Standalone), Laufzeit `node:22-bookworm-slim` über Digest, arm64. Container: Benutzer `node`, `read_only`, `cap_drop: ALL`, `no-new-privileges`, 512 MB, 1 CPU, 128 Prozesse, `restart: unless-stopped`, Healthcheck, kein veröffentlichter Port |
| Netz | `wamo-ui-netz` (172.25.0.0/24), angelegt vom Compose von `wamo-caddy` | `internal` und ohne Adresse auf der Bridge (`inhibit_ipv4`): Der Container erreicht weder Internet noch Büro-LAN noch Dienste des Hosts (in `pruefen` geprüft: Gateway, Router, Internet, Host). Nur `wamo-caddy` hängt zusätzlich darin |
| Caddy-Site | `wamo-ui/wamo-ui.caddy.vorlage`, installiert als `/home/wamocon/wamo-tls/caddy/etc/kern/wamo-ui.caddy` | `https://192.168.178.136:55445` mit Zertifikat der WAMO-CA (`tls internal`), nur GET und HEAD, Body höchstens 1 KB, `encode`, ohne `Server` und `X-Powered-By`, COOP und CORP `same-origin`. Die übrigen Header (CSP, Referrer-Policy, nosniff, X-Frame-Options, Permissions-Policy) setzt Next.js. Kein HSTS: Browser ignorieren ihn bei einer IP-Adresse |
| Einstellungen | `/home/wamocon/wamo-ui/wamo-ui.conf` (Vorlage `wamo-ui/wamo-ui.conf.beispiel`) | `WAMO_UI_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_WAMO_KERN_BUERO`, `WAMO_UI_TLS`, `WAMO_UI_HSTS`, Anmeldedauer im Browser. Hier und nur hier stehen Adresse und Zertifikatsart; im Code der Oberfläche steht keine feste IP. Werte sind öffentlich (stehen im JavaScript), keine Geheimnisse |
| Publishable Key | liest das Skript auf dem Server aus `/home/wamocon/supabase-wamo/.env` | als Build-Argument ohne Wert in der Befehlszeile; abgelehnt wird alles außer `sb_publishable_…` (oder ein JWT mit Rolle `anon`), nie `service_role` |
| Firewall | Port 55445 in `WAMO-FW-LAN` (`wamo-firewall.sh`) | Loopback und Büro-LAN, übernommen per `systemctl reload wamo-firewall` ohne Lücke |
| Erlaubte Herkunft | Drop-in `/etc/systemd/system/wamo-core.service.d/wamo-ui.conf` | ergänzt `PIPECAT_ALLOWED_ORIGINS` um `WAMO_UI_URL` (Basis ist die Liste aus `wamo-core.service`); `wamo-core.service` selbst bleibt unberührt. Wirksam nach einem Neustart von wamo-core |
| Skripte | `wamo-ui-einrichten.sh`, `wamo-ui-rueckweg.sh`, vom PC `scripts/wamo_ui_sokrates2.py` | alle wiederholbar; prüfen den Ablageort in `~/wamo-infra` wie die übrigen Skripte |

Supabase braucht nichts Neues: Der Envoy des Stacks antwortet auf Preflight-Anfragen mit der Herkunft der Anfrage
(geprüft für `https://192.168.178.136:55445`). Anmeldung mit Passwort und TOTP kennt keine Weiterleitung;
`SITE_URL` und `ADDITIONAL_REDIRECT_URLS` in GoTrue (`http://localhost:3000`) werden nur für E-Mail-Links gebraucht
(Passwort vergessen, Magic Link), die WAMO nicht nutzt. Käme das einmal dazu, gehört die neue Adresse in
`ADDITIONAL_REDIRECT_URLS` (erstellt nur den Auth-Container neu).

### 12.2 Einrichten und aktualisieren

```bash
# vom PC, Repo-Wurzel
python scripts/wamo_ui_sokrates2.py verteilen --mit-firewall-und-caddy   # Quellen von ui/ (ohne node_modules, .next, .env*) und Skripte
python scripts/wamo_ui_sokrates2.py einrichten                            # Firewall, Caddy, Image, Container, Herkunft, Prüfung
python scripts/wamo_ui_sokrates2.py einrichten --neustart                 # zusätzlich wamo-core neu starten, aber nur ohne Sitzung
python scripts/wamo_ui_sokrates2.py bauen                                 # nach einer Änderung an ui/: nur Image und Container
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
- Nach einem Update von `ui/` genügt `bauen`. Das vorige Image bleibt als `wamo-ui:vorher`, zurück geht es mit
  `python scripts/wamo_ui_sokrates2.py zurueck`.
- Rückweg: Abschnitt 8.

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

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
