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
brauchen nur den Import (Abschnitt 5.2).

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
| `GH_TOKEN` | gh, git push, Pull Requests | GitHub App „WAMO“ (kurzlebige Tokens, je Repository freigegeben). Übergang: fein granularer Token eines Bot-Kontos, nur ausgewählte Repos, Ablaufdatum |
| `GH_TOKEN_LESEN` | GitHub nur lesend für die Arten planung, review, analyse, sicherheit, reverse_engineering und dokumentation (kommt in der Sandbox als `GH_TOKEN` an) | fein granularer Token, nur „Contents: Read“ und „Metadata: Read“ auf die nötigen Repos, Ablaufdatum. Fehlt er, bekommen diese Arten gar kein GitHub-Token |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID` | Vercel-CLI (Preview-Deployments, Logs) | Team-Token mit Ablaufdatum; Produktion nur nach Freigabe (dev_guard) |
| `SUPABASE_ACCESS_TOKEN` | Supabase-CLI und MCP für Cloud-Projekte | Persönlicher Token eines Service-Kontos; MCP standardmäßig `--read-only` und mit `--project-ref` |
| `JIRA_API_KEY`, `JIRA_EMAIL` | Jira-MCP (`wamocon.atlassian.net`), nur lesend | API-Token eines Kontos mit Leserechten auf die nötigen Projekte; `JIRA_EMAIL` ist die Anmeldeadresse dieses Kontos |
| `SONIOX_API_KEY`, `LITELLM_API_KEY` | Sprachkern auf Sokrates-2 (Phase P3) | eigener LiteLLM-Schlüssel nur für WAMO; gelangen nie in die Auftrags-Sandbox |

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

Die Sicherungen in `/var/backups/wamo-firewall` dienen zum Nachsehen. Nicht blind mit `iptables-restore` einspielen,
weil Docker seine eigenen Regeln bei jedem Containerstart ändert.

## 9. Entwicklungsaufträge: Sandbox, Ausgangsfilter und SSH (eingerichtet am 19.10.2026)

Eingerichtet mit `wamo-job-einrichten.sh` (wiederholbar, Ablage über `~/wamo-infra` wie oben, Kopfkommentar des Skripts).
Rauchtest danach immer: `ssh sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp --attrappe"`.

| Baustein | Ort | Zweck |
| --- | --- | --- |
| Dispatcher | `/opt/wamo/bin/wamo-job-dispatch` (root:wamo) | Forced Command des Schlüssels `wamo-jobs`: nur `ping`, `start`, `status`, `pending`, `approve`, `cancel` |
| Läufer | `/opt/wamo/bin/wamo-job.sh` | bubblewrap-Sandbox, Landlock, seccomp, systemd-Grenzen, Zeitlimit |
| Wächter | `/opt/wamo/waechter`, in der Sandbox unter demselben Pfad schreibgeschützt | Freigaben, Sperrliste, seccomp-Filter |
| Managed-Settings | `/opt/wamo/claude-code/managed-settings.json`, in der Sandbox `/etc/claude-code` | nur verwaltete Hooks (Wächter, ECC als verwaltetes Plugin), keine Projekt-MCP-Server |
| Ausgangsfilter | `/etc/wamo/egress.nft`, `wamo-egress.service` | nft-Tabelle `inet wamo_egress`, nur für uid `wamo` |
| SSH | `/etc/ssh/authorized_keys.d/wamo`, `/etc/ssh/sshd_config.d/wamo.conf` | Schlüssel mit `restrict,from="192.168.178.0/24",command=...`; nur Public Key, keine Weiterleitungen, kein TTY |
| Aufräumen | `/etc/tmpfiles.d/wamo-jobs.conf` | Caches der Aufträge nach 7 Tagen, Claude-Daten nach 30 Tagen |
| Version | `CLAUDE_VERSION` in `/opt/wamo/wamo-job.conf` | nach einem Update von Claude Code startet nichts, bis das Einrichtungsskript und der Rauchtest liefen |

Ausgangsfilter: Für Sockets des Benutzers `wamo` gilt nur Internet auf TCP 80 und 443, DNS an `127.0.0.53` und auf
Loopback `127.0.0.1:20000-20999` für eigene Entwicklungsserver eines Auftrags. IPv6, LAN, Docker-Netze, private
Netze und eigene Adressen des Servers werden abgewiesen (Log mit Ratenbegrenzung, Fehler `EHOSTUNREACH` bzw. bei IPv6
`EACCES`). Andere Benutzer und Dienste laufen nicht durch die Kette (`meta skuid`); ufw, iptables-restore und Docker
fassen die eigene Tabelle nicht an. Zähler: `sudo nft list table inet wamo_egress`.

Hinweise:

- Die Login-Shell von `wamo` ist `/bin/sh`, damit Forced Commands keine `.bashrc` laden. Für Arbeiten von Hand als wamo
  `sudo -u wamo -H bash -l` verwenden.
- Auch außerhalb der Sandbox gilt für `wamo` der Ausgangsfilter (etwa bei `npm install -g` von Hand: Internet über 443 geht).
- Hostweite Schalter wie `net.mptcp.enabled=0` oder `kernel.io_uring_disabled` sind bewusst nicht gesetzt (betreffen andere
  Dienste); die Sandbox sperrt beides selbst per seccomp.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
