# Selbstüberarbeitung von WAMO ohne Downtime

Konzept, Stand 22.10.2026, Basis Commit 332a37f. Nichts davon ist gebaut: keine Dienste, keine Migrationen, kein Code.
Ziel: WAMO arbeitet über Sprache und Oberfläche am eigenen Code, ein Admin gibt das Ergebnis frei, ein Release-Dienst auf
Sokrates-2 prüft, versioniert und setzt es ohne Unterbrechung live. Bis dieser Weg steht, bleibt `SELF_REPO = "wamo"`
unverändert gesperrt. Gegliedert nach den Kernfragen A bis I; Namen, Pfade und Bezeichner stehen in ASCII.

## 0. Kurzfassung

| Frage | Antwort |
| --- | --- |
| A Kern | Zwei Kern-Instanzen laufen nebeneinander (Unit-Vorlage `wamo-core@.service`, je Instanz eigener Socket und eigener Releaseordner). Caddy leitet nach Pfad `/i/<slot>/` und hält jede Sitzung auf ihrer Instanz; die alte Instanz läuft aus, bis ihre letzte Sitzung endet (höchstens `WAMO_MAX_SITZUNG_MIN`, Standard 120 Minuten). Kleinster Code: eine Sitzungsliste im Dateisystem, damit "eine Sitzung je Profil" und die PC-Sperre über beide Instanzen gelten. |
| B Oberfläche | Zwei Container `wamo-ui-blau` und `wamo-ui-gruen`, Caddy zeigt über eine Include-Datei auf den aktiven; Gesundheitsprüfung vor dem Umschalten, `caddy reload` schaltet ohne Lücke, Rückweg ist dieselbe Handlung rückwärts. |
| C Datenbank | Nur erweiternde Migrationen im Release, Aufräumen frühestens zwei Releases später; Prüfsumme je angewendeter Migration, ein Lint und der Agent `database-reviewer` vor jedem Apply. Angewendet wird vom Anker als `postgres` per `docker exec`, weil `wamo_admin` nur Spaltenrechte hat. |
| D Release-Dienst | `wamo-release` (unprivilegiert, Unix-Socket, Befehle anfordern, zurueck, status; `freigeben` nimmt der Anker entgegen) orchestriert; die Prüfstrecke läuft in einer geheimnislosen Sandbox mit Domain-Allowlist; Review und Refactor mit dem Claude-Zugang des beauftragenden Admins; alles Root-Nötige macht der Anker. |
| E Freigaben | Tabellen `release_anfragen`, `release_freigaben`, `release_ereignisse`; Freigabe am Kern vorbei direkt beim Freigabe-Dienst des Ankers, mit frischem Token (aal2, zweiter Faktor höchstens 60 Sekunden alt) und gebunden an Commit-ID und Tree-ID; "Schutzschicht berührt" erkennt der Anker per Pfadliste am Diff, nie der Agent. |
| F Version | Ein kleines Python-Skript berechnet vX.Y.Z aus Conventional Commits, der Admin darf erhöhen, nie senken; Release-Commit mit Versionsdateien und `CHANGELOG.md`, annotierter Tag, GitHub Release. Erstes Tag v0.1.0. |
| G Notanker | Root-eigener Teil außerhalb von `/opt/wamo*` und außerhalb des Repo-Deploys: Umschalten, Rückweg, Klassifizierung, Prüfung der Sonderfreigabe, Abschalter. WAMO kann ihn nicht ändern. |
| H Sicherheit | Größtes Restrisiko ist die Freigabe selbst (Diff nicht gelesen, Täuschung, gekaperter Kern). Empfehlung: die Schutzschicht erst in Stufe 4 öffnen, nach bewährten Stufen 0 bis 3 und mit mindestens zwei Admins. |

**IQ:** Sicherheit entsteht aus getrennten Rechten. Schreiben (Agent), Prüfen (Prüfstrecke), Freigeben (Mensch) und Schalten
(Anker) liegen bei vier verschiedenen Akteuren, und jede Freigabe hängt an Commit-ID und Tree-ID. **EQ:** Eine Freigabe muss in
unter zwei Minuten verständlich sein (Klartext-Diff, Prüfstand, Rückweg mit einem Klick), und niemand im Büro darf mitten im
Gespräch getrennt werden. **VQ:** Ein Ablaufbild, eine Stufentabelle, Freigabekarten mit immer gleichen Zonen.
**Synthese:** Erst bauen, was nur umschaltet und zurückführt (Stufe 0 und 1), dann den Kern (2), dann WAMO als Autor (3),
zuletzt die Schutzschicht (4).

## 1. Ausgangslage (mit Belegen)

| Baustein | Ist | Folge heute |
| --- | --- | --- |
| Kern-Update | `infra/sokrates2/wamo-core-einrichten.sh` ersetzt `/opt/wamo-core/app` (Schritt 4) und ruft `systemctl restart wamo-core` (Schritt 8); `wamo-core-vorpruefung.sh` verlangt `WAMO_CORE_UDS=/run/wamo-core/core.sock` und `WAMO_ENTWICKLUNG=aus` | jede Aktualisierung beendet alle Sitzungen; `kern_ruhig` in `wamo-ui-einrichten.sh` bricht deshalb ab, sobald eine läuft |
| UI-Update | `wamo-ui-einrichten.sh bauen` und `zurueck` rufen `docker compose up -d --force-recreate` | Oberfläche einige Sekunden weg, der Rückweg hat dieselbe Lücke |
| Caddy | `wamo-caddy/Caddyfile` hat `stream_close_delay 5m`; die Sites für 55444 und 55445 kommen aus `etc/kern/*.caddy`; neue Ports oder Netze im Compose legen den Container neu an | `caddy reload` taugt zum Umschalten, ein Recreate nicht |
| Selbstschutz | `SELF_REPO = "wamo"` in `dev_guard.py:66`, `dev_agent.py` (`project_slug`, `repo_pruefen`, `repo_aus_auftrag`), `wamo_job_dispatch.py` (`check_project`, `check_repo`, Prüfung der Installation auf das WAMO-Repo) | Aufträge sehen das Repo nie; die vorhandene GitHub-App darf es nicht enthalten |
| Migrationen | von Hand (`render-migration.sh` in `docker exec -i wamo-supabase-db psql -U postgres`, Kopf von `0012_kontingent.sql`); `wamo_admin` hat nur Spaltenrechte (0001, Zeilen 351 bis 355) | kein automatischer Weg, keine Prüfsumme |
| Versionen, CI | alle fünf Pakete auf 0.1.0, kein Tag, kein `.github`, Commit-Titel nicht durchgängig konventionell (PR #5 heißt "Mikrofon, Modellzugang, ...") | Versionsableitung braucht eine Regel für Nicht-Konventionelles |
| Server (nur gelesen) | 20 Kerne, 121 GB RAM, 3,1 TB frei, systemd 255, bubblewrap 0.9.0, Python 3.12.3; `wamo-core` läuft seit 08:51 ohne Neustart; `wamo-ui` und `wamo-caddy` gesund; Bilder `wamo-ui:aktuell`, `wamo-ui:vorher` und Zeitstempel-Tags vorhanden; Benutzer `wamocon` hat sudo und docker | root-Macht liegt bei Menschen; der Schutz richtet sich gegen WAMO und die Dienstbenutzer |
| Kern im Büro | `WAMO_ENTWICKLUNG=aus` fest in der Unit; Entwicklungsaufträge starten heute vom PC-Kern | Selbständerung braucht in Stufe 3 einen schmalen eigenen Weg, nicht den allgemeinen Schalter |

## 2. Zielbild

```text
Admin ──Sprache/Oberfläche──> Kern ──ctl.sock──> RELEASE-DIENST (wamo-release, unprivilegiert)
                                                   │ 1 Anfrage (admin, aal2)
        Auftrags-Sandbox <──Art selbstaenderung────┘ 2 Arbeitskopie wamo-selbst, KEIN GitHub-Token, kein Push
        (schreibt nur Dateien)  ──Patch──────────> 3 Übernahme: Basis fixieren, Patch in eigenen Spiegel,
                                                     Pfadliste (ANKER klassifiziert): normal | sonder | gesperrt
 ┌─────── Prüfer-Sandboxen ohne Geheimnisse, Egress nur Allowlist (Code-Stufen ohne Token) ───────────────┐
 │ 4 Prüfstrecke 1: Tests, Typen, Lint, Secret-Scan, Fremdverweis-Grep, Migrations-Lint                   │
 │ 5 Review: code-, security-, python-, typescript-, react-, database-reviewer (nur lesen)                │
 │ 6 Refactor als eigener Commit R: wmc-vibecode-cleanup, code-simplifier, refactor-cleaner               │
 │ 7 Prüfstrecke 2: alles erneut auf R; Schutzschicht-Diff C1 zu R muss leer sein (außer Sonderfreigabe)  │
 └────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 8 Versionsvorschlag ──Admin bestätigt oder erhöht──> 9 Release-Commit V (Version, CHANGELOG)
 10 Prüfstrecke 3 (schnell, auf V) ──> 11 FREIGABE: Admin, aal2 frisch, gebunden an commit_id + tree_id
                                         (sonder: weiterer Admin, Wartezeit, Prüfung durch den Anker)
 12 Veröffentlichen: Branch, annotierter Tag vX.Y.Z, GitHub Release (App-Token des Release-Dienstes)
 ═══════════ ab hier handelt nur der ANKER (root), er prüft die Freigabe erneut aus der Datenbank ═══════════
 13 Bereitstellen: UI Image bauen, Kern Slot starten, Migration (erweiternd) zuerst
 14 Gesundheit: /version.json (UI), /start ohne Token = 401 (Kern)  ──nicht ok──> Abbruch, nichts umgeschaltet
 15 Umschalten: Include-Datei tauschen, caddy reload; alte Instanz läuft aus
 16 Bewährung 30 Minuten (Journal, Neustarts) ──Fehler──> 17 Rückweg automatisch zur letzten guten Version
```

| Komponente | Benutzer | Ort | Darf | Darf nicht |
| --- | --- | --- | --- | --- |
| Auftrags-Sandbox | `wamo` | `/srv/wamo/projekte/wamo-selbst` | Dateien in der Arbeitskopie schreiben | GitHub-Token halten, pushen, `/opt`, `/etc/wamo` sehen |
| Release-Dienst | `wamo-release` (neu) | `/opt/wamo-release/app`, Daten `/var/lib/wamo-release`, Sockets `/run/wamo-release` | Spiegel lesen, Patch anwenden, Prüfstrecke starten, Datenbank (eigene Rolle `wamo_release`), GitHub (zweite App) | Dienste umschalten, root werden, Anker ändern |
| Prüfer-Sandbox | `wamo-pruefer` (neu) | `/srv/wamo-pruefer`, eigene nft-Tabelle | Code bauen und testen | Geheimnisse, Zugang außer Allowlist |
| Anker | root | `/usr/local/lib/wamo-anker`, Konfiguration `/etc/wamo-anker`, Zustand `/var/lib/wamo-anker` | Container und Units schalten, Caddy-Dateien tauschen, Migrationen anwenden, Rückweg; Freigaben entgegennehmen und schreiben (eigener Prozess ohne root) | von WAMO oder dem Release-Dienst verändert werden |

Die Rechte sind getrennt: **Schreiben** (Agent), **Prüfen** (Prüfer-Sandbox), **Freigeben** (Mensch), **Schalten** (Anker).
Der Anker liegt bewusst außerhalb von `/opt/wamo*` und wird nie durch den Release-Weg installiert, sondern nur von Hand mit
`sudo bash anker-einrichten.sh`. Neue Namen ergänzen den Namensvertrag in `CLAUDE.md` (Präfix `wamo`, Env `WAMO_RELEASE_*`,
DB-Rollen `wamo_release` und `wamo_anker`, Schemas wie bisher).

## A. Kern ohne Downtime

**Antwort:** Ja, zwei Instanzen können nebeneinander laufen. Es scheitert heute nur an festen Namen (Socket, Code- und
venv-Ordner, Vorprüfung) und an vier Registern im Speicher des Prozesses. Pipecat muss nicht umgebaut werden.

### A.1 Zustand am Prozess und was bei zwei Instanzen bricht

| Zustand | Beleg | Bei zwei Instanzen | Maßnahme |
| --- | --- | --- | --- |
| `active_sessions` nach `POST /start`, `_pcs_map` je `pc_id` | pipecat 1.12, `runner/run.py` (`proxy_request`: unbekannte Sitzung gibt 404), `smallwebrtc/request_handler.py` (`handle_patch_request`) | `/sessions/<id>/...`, Offer, ICE-PATCH und Neuverhandlung müssen die Instanz treffen, die `/start` beantwortet hat | Routing nach Pfad (A.2) |
| Eine Sitzung je Profil | `sitzungen.py` (`Sitzungen._aktiv`), `bot.py:215` | Instanz b kennt die Sitzung von a nicht: zwei Sitzungen desselben Profils, die alte wird nicht abgelöst | Sitzungsliste und Ablöse-Brücke (A.3) |
| PC-Sperre | `pc_host.py` (`_lease`, `_lease_je_geraet`) | beide Instanzen könnten denselben PC steuern | die Brücke gibt die Sperre der alten Sitzung frei, bevor die neue sie nimmt |
| Tunnel-Sockets `/run/wamo-pc/tNN`, `/run/wamo-tunnel/pc.sock` | `pc_tunnel.py` (`GegenstellenPruefung`, `SO_PEERCRED`), `pc_geraet.py` | unkritisch: der Kern ist nur Client, beide Instanzen laufen als `wamo-core` in der Gruppe `wamo-core` und prüfen den Benutzer `wamo-t-NN` selbst; der Host am Laptop ist zustandslos (MCP über HTTP, JSON) | nichts |
| Geräte-Register, Wache | `geraete_register.py`, `pc_geraet.geraet_wache` je Sitzung | Entzug trifft beide Instanzen, weil beide das Register lesen | nichts |
| Drosselung, Nonces | `geraet_api._benutzt` und `_versuche`, `modellzugang._versuche` | Zähler je Prozess: im Übergang doppelte Grenze, Einmaligkeit einer Herausforderung gilt je Instanz | akzeptiert (siehe H) |
| TTL-Caches | `auth_gate` (Token und Profil 60 s), `boss._probe_cache`, `llm_anbieter._pruef_cache`, `geraete_werkzeuge._liste` | harmlos | nichts |
| Datenbank | `memory.py` Pool 1 bis 5; Rolle `wamo_core_s2` mit Limit 20 (`wamo_kern_sokrates2.py`, `VERBINDUNGEN`) | höchstens 2 mal 5 | reicht |
| Medien | aioice bindet ephemere UDP-Ports, direkt zwischen Browser und Instanz | kein Konflikt, Medien laufen nicht über Caddy | nichts |
| Feste Namen | Vorprüfung (`WAMO_CORE_UDS`), `/opt/wamo-core/app`, `venv`, `StateDirectory` | verhindern die zweite Instanz | Vorlage `wamo-core@.service`, Slot-Ordner `/opt/wamo-core/slots/a` und `b` als Verweise auf `releases/vX.Y.Z` |

### A.2 Routing der neuen Sitzungen

| Option | Urteil |
| --- | --- |
| systemd-Socket-Aktivierung | Nein. Sie hält einen Listen-Socket für einen Dienst, kennt keine Sitzungen, und zwei Prozesse am selben Socket nehmen zufällig an. Ein Neustart des Dienstes trennt weiterhin alle Verbindungen. |
| Caddy mit Wiederholung bei 404 | Nicht belegt: ob Caddy den Anfragekörper des Offers (POST) beim zweiten Versuch erneut sendet, ist ungeprüft. |
| Präfix in der Sitzungskennung (Middleware schreibt die Antwort von `/start` um) | Deterministisch, greift aber in `auth_gate.py` (Schutzschicht) und in den Vertrag mit Pipecat ein. |
| Eigener Router-Prozess | Geht, ist aber ein weiterer Dienst mit Tabelle im Speicher. |
| **Slot im Pfad (Empfehlung)** | Caddy routet `/i/a/*` und `/i/b/*` je auf einen Socket (`uri strip_prefix`), `/wamo/slot` antwortet mit dem aktiven Slot. Die Oberfläche fragt den Slot und startet mit `${kern}/i/<slot>/start`. Belegt: `@pipecat-ai/small-webrtc-transport` bildet die Offer-Adresse mit `startEndpoint.replace("/start", "/sessions/<id>/api/offer")`, und ICE-PATCH wie Neuverhandlung gehen an dieselbe Adresse (`dist/index.module.js`, um Zeile 2697 und 2959). Jede Sitzung bleibt damit auf ihrer Instanz, ohne Änderung am Kern. |

Skizze der Include-Datei, die der Anker erzeugt (`etc/kern/slot.inc`, von `wamo-core.caddy` eingebunden; im echten Caddyfile steht
jede Direktive in einer eigenen Zeile):

```caddy
handle /wamo/slot            { header Cache-Control no-store ; respond "b" 200 }
handle /i/a/start            { respond `{"slot":"b"}` 409 }        # erst nach dem Nachlauf von 60 s
handle /i/a/*                { uri strip_prefix /i/a ; reverse_proxy unix//run/wamo-core/core-a.sock { flush_interval -1 } }
handle /i/b/*                { uri strip_prefix /i/b ; reverse_proxy unix//run/wamo-core/core-b.sock { flush_interval -1 } }
handle                       { reverse_proxy unix//run/wamo-core/core-b.sock { flush_interval -1 } }   # aktiver Slot
```

### A.3 Sitzungsliste und Ablöse-Brücke (der einzige neue Python-Code im Kern)

Jede Instanz legt für ihre Sitzungen Dateien `/run/wamo-core/sitzungen/<user_id>.<slot>` an (Verzeichnis 0700 `wamo-core`, nur
die Kennung, kein Inhalt). `Sitzungen.uebernehmen` (heute `sitzungen.py`) prüft zusätzlich die Datei der anderen Instanz,
legt eine Marke `<user_id>.abloesen` an und wartet höchstens 5 Sekunden, bis die andere Instanz die Sitzung abgelöst
(`_abloesen`: PC-Sperre frei, Meldung, Trennen) und ihre Datei gelöscht hat. Die andere Instanz fragt die Marken jede Sekunde
ab. Dieselbe Liste ersetzt die UDP-Heuristik von `kern_ruhig` durch eine echte Zählung ("Instanz a hat 0 Sitzungen").

### A.4 Ablauf, Dauer und Grenzen

1. Anker startet den freien Slot (b) mit dem neuen Release, `ExecStartPre` prüft wie bisher, dann Gesundheit (A.5).
2. Include-Datei wechselt auf b, `caddy reload`. Neue Sitzungen gehen auf b, laufende bleiben auf a (Pfad `/i/a/`).
3. a läuft aus: kein neuer `/start` nach 60 Sekunden Nachlauf (409 mit dem aktiven Slot, die Oberfläche wiederholt einmal
   mit frischem Slot), Beendigung erst bei 0 Sitzungen und frühestens nach der Bewährung (30 Minuten), damit der Rückweg
   sofort möglich bleibt.
4. **Dauer:** höchstens `WAMO_MAX_SITZUNG_MIN` (`bot.py:202`, Standard 120) plus Nachlauf, denn `SessionGuard` beendet jede
   Sitzung nach dieser Zeit. Verkürzen: Servernachricht `update_bereit` zeigt einen ruhigen Hinweis; optional ein Schalter
   "bei Gesprächspause automatisch erneuern" (kurze Wiederverbindung, kein Datenverlust, Standard aus).

**Ehrliche Grenzen:** (a) Seiten aus der Zeit vor Stufe 2 kennen keinen Slot und nutzen die Standardroute, also den aktiven
Slot; das trägt bis zum nächsten Umschalten, die Oberfläche zeigt über `/version.json` den Hinweis zum Neuladen. Neuere
Seiten fragen den Slot bei jedem Verbindungsaufbau. (b) Stürzt eine Instanz ab, enden ihre Sitzungen wie heute. (c) Die Pfadlogik hängt am Verhalten von
`small-webrtc-transport`; ein Update des Pakets braucht den Rauchtest "zwei Instanzen" (Stufe 2). (d) Migrationen müssen
rückwärtskompatibel sein, weil beide Versionen kurz denselben Datenbestand sehen (C). (e) Zwei Instanzen belegen 2 mal
`MemoryMax=4G`, auf dem Server unkritisch.

### A.5 Gesundheitsprüfung des Kerns vor dem Umschalten

`systemctl is-active`, kein Neustartzähler, Socket vorhanden, Importprobe der Vorprüfung; `POST /i/b/start` und
`GET /i/b/status` ohne Token müssen 401 liefern (der Kern lebt, die Zugangskontrolle ist geladen; sie liegt vor jeder Route,
ein 200 ohne Token wäre also ein Fehler); `GET /wamo/api/...` ohne Token ebenfalls 401. Einen Rauchtest mit echter
WebRTC-Sitzung gibt es nur in der Stufenübung, nicht in jedem Release, weil er ein Testprofil mit zweitem Faktor bräuchte.

## B. Oberfläche ohne Downtime (blau und grün)

| Datei | Änderung |
| --- | --- |
| `infra/sokrates2/wamo-ui/docker-compose.yml` | statt `ui` zwei Dienste `blau` und `gruen` (Container `wamo-ui-blau`, `wamo-ui-gruen`), Image `wamo-ui:${BLAU_TAG}` und `wamo-ui:${GRUEN_TAG}` (Versionstags wie `wamo-ui:v0.1.1`, `pull_policy: never`), gleiche Härtung und Netz `wamo-ui-netz` wie heute. Gestartet wird nur der inaktive Dienst (`docker compose up -d gruen`), nie `--force-recreate` des aktiven. |
| `infra/sokrates2/wamo-ui/wamo-ui.caddy.vorlage` | `reverse_proxy wamo-ui:3000` wird `import /etc/caddy/kern/wamo-ui-aktiv.inc`. Die Include-Datei erzeugt der Anker (Skizze, im echten Caddyfile eine Direktive je Zeile): `reverse_proxy wamo-ui-gruen:3000 wamo-ui-blau:3000 { lb_policy first ; health_uri /version.json ; health_interval 3s ; fail_duration 10s }`. Der erste Upstream ist der neue; fällt er aus, übernimmt der alte ohne Eingriff. |
| Statische Dateien | `handle /_next/static/*` fragt zuerst den neuen, bei 404 den vorigen Container (GET ohne Körper, daher unkritisch). Sonst fehlen einer offenen Seite beim Umschalten ihre Chunks. Die genaue Caddy-Syntax (`handle_response`) ist in Stufe 1 mit einem Test zu belegen; Rückfall: vorigen Container 30 Minuten laufen lassen. |
| `ui/public/version.json`, `infra/sokrates2/wamo-ui/Dockerfile` | der Build schreibt `{"version","commit"}` (Build-Argumente `WAMO_VERSION`, `WAMO_COMMIT`); die Probe prüft damit, dass wirklich der freigegebene Stand läuft. Die Oberfläche fragt die Datei alle 5 Minuten und zeigt bei Abweichung "Neue Version, bitte neu laden". |
| `wamo-ui-einrichten.sh`, `scripts/wamo_ui_sokrates2.py` | `bauen` baut `wamo-ui:<version>` und startet den inaktiven Dienst; neue Befehle `umschalten` und `zurueck` tauschen nur die Include-Datei (`caddy validate`, atomar `mv`, `caddy reload`). `wamo-caddy` wird nie neu angelegt. Im Release-Weg ruft der Anker dieselben Schritte; die Skripte bleiben die Rückfallebene von Hand. |

**Ablauf:** (1) inaktive Farbe mit dem neuen Image starten; (2) warten bis `healthy` (`start_period` 15 s, für den
Umschaltmoment eigene Probe alle 3 s); (3) von außen prüfen: `docker exec wamo-caddy wget -qO- http://wamo-ui-<farbe>:3000/version.json`
liefert die erwartete Version und den erwarteten Commit; (4) Include-Datei tauschen; (5) `caddy reload` (laufende Anfragen
enden normal, neue gehen an die neue Farbe, keine Lücke); (6) Bewährung 30 Minuten (Healthcheck, Probe alle 5 s, Zeilen
`dial tcp` im Caddy-Log, denn Caddy führt für 55445 sonst kein Zugriffslog); (7) alte Farbe stoppen, Image bleibt (die
letzten drei Tags). **Rückweg:** Include-Datei zurück und Reload, etwa 1 Sekunde, solange die alte Farbe läuft; danach
Start aus dem vorhandenen Image in etwa 10 Sekunden. **Grenze:** Die Oberfläche kennt keine Serverzustände (nur GET und
HEAD, Anmeldung und Kern spricht der Browser selbst an), deshalb genügt dieser einfache Weg.

## C. Datenbank

| Regel | Inhalt |
| --- | --- |
| Erweitern, dann aufräumen | Eine Migration im Release darf nur hinzufügen: Tabellen, Spalten (null erlaubt oder mit Vorgabe), Indizes, Funktionen, Policies im Muster `drop ... if exists`, dann `create`. Verboten im selben Release: `drop table`, `drop column`, `rename`, Typänderung, `set not null` ohne Vorgabe und Nachfüllung, `truncate`, `delete` ohne `where`, `revoke` auf bestehende Rechte. Aufräumen (Contract) frühestens zwei Releases später und nur, wenn die Rückweg-Version den alten Teil nicht mehr braucht. |
| Rückweg bleibt möglich | Beide Kern-Versionen sehen kurz denselben Bestand (A). Darum gilt: Release N läuft mit dem Schema von N minus 1 und von N plus 1. Ein Lint in der Prüfstrecke erkennt die verbotenen Anweisungen; ein Treffer macht die Migration zu "Contract" und sperrt sie in den Stufen 1 bis 3 (nur von Hand durch Niko). |
| Unveränderlich nach PROD | Neue Spalte `sha256` in `<schema>.schema_migrations` (selbst eine erweiternde Migration, 0013). Der Anker trägt beim Anwenden die Prüfsumme ein; die Prüfstrecke vergleicht jede bereits angewendete Datei mit ihr, ein Unterschied bricht ab. Änderungen sind nur als neue Migration mit `.down.sql` möglich (Regel aus `CLAUDE.md`). Alte Migrationen werden nie erneut ausgeführt (die Falle um 0010 bis 0012 im Kopf von `0012_kontingent.sql` entfällt). |
| Reihenfolge | Prüfstrecke: Wegwerf-Postgres (Binaries aus dem Paket, nur in der Sandbox) wendet alle Migrationen frisch an, prüft `.down.sql` und `supabase/tests/*.sql`. Bereitstellung: `wamo_test` zuerst, dann `wamo_prod`, immer `psql -v ON_ERROR_STOP=1 --single-transaction` mit `set local lock_timeout`, erst danach der Code. |
| Wer wendet an | Der Anker, nicht der Release-Dienst, als `postgres` über `docker exec -i wamo-supabase-db psql` (der heutige Weg), nur für Dateien, die zur freigegebenen Commit-ID gehören. `wamo_admin` reicht nicht: Er hat nur Spaltenrechte (0001), die Objekte gehören `postgres`. Bis Schritt 2.5 bleibt das Anwenden Handarbeit von Niko (die Karte zeigt "Migration offen", der Release wartet). Besser, aber später (Entscheidung offen): Rolle `wamo_migrator` mit Eigentum an den WAMO-Objekten, damit der Superuser nicht mehr nötig ist. |
| Prüfung | Agent `database-reviewer` auf jede Migration (Stufe Review), dazu der mechanische Lint und der Pflichtteil aus `CLAUDE.md`: RLS, Policy `core_all`, nur nötige Grants, FK-Indizes, keine Rechte für `anon`, `authenticated`, `service_role`. Jede Migration steht in den "Wirkungsflächen" der Freigabekarte und gilt in Stufe 4 als Schutzschicht (Sonderfreigabe). |

## D. Release-Dienst

### D.1 Dienst, Sockets, Befehle, Audit

`wamo-release.service`, Benutzer `wamo-release` (System, nologin), gleiche Härtung wie `wamo-core.service` (`ProtectSystem=strict`,
`NoNewPrivileges`, leeres `CapabilityBoundingSet`, `SystemCallFilter`, `ReadWritePaths` nur `/var/lib/wamo-release`,
`/run/wamo-release`, `/var/log/wamo-release`). Code root-eigen unter `/opt/wamo-release/app`.

| Socket | Wer darf | Zweck |
| --- | --- | --- |
| `/run/wamo-release/api.sock` | Caddy (Benutzer `wamocon`, uid 1000) und root, geprüft per `SO_PEERCRED`; die Person weist sich mit ihrem Token aus, das der Dienst selbst gegen JWKS prüft (aal2, Profil aktiv, Rolle admin) | `anfordern`, `status`, `diff`, `zurueck`; Caddy reicht `/wamo/release/*` auf 55444 hierher, am Kern vorbei (Ausnahme: `.../freigeben`, siehe unten) |
| `/run/wamo-anker/freigabe.sock` | Caddy und root (`SO_PEERCRED`); der Prozess gehört dem Anker (Benutzer `wamo-anker`, ohne root) | nur `POST /wamo/release/<id>/freigeben`: prüft das Token und schreibt die Freigabe (E); weder Kern noch Release-Dienst können Freigaben schreiben |
| `/run/wamo-release/ctl.sock` | nur `wamo-core` (`SO_PEERCRED`) | der Kern stellt im Namen eines Admins eine Anfrage (nur Vorschlag, nie Freigabe), übergibt den Claude-Zugang für einen Lauf, liest den Stand für die Servernachrichten |
| `/run/wamo-anker/anker.sock` | nur `wamo-release` (`SO_PEERCRED`) | `klassifiziere`, `bereitstellen`, `migrieren`, `umschalten`, `rueckweg`, `status`; der Anker prüft jede Freigabe erneut aus der Datenbank |

Befehle der Personen sind genau vier: `anfordern`, `freigeben` (nur beim Freigabe-Dienst des Ankers), `zurueck`, `status`; alles andere lehnt der Dienst ab.
Zustände: angefragt, arbeit, uebernommen, pruefung1, review, refactor, pruefung2, versionswahl, pruefung3, wartet_freigabe,
freigegeben, veroeffentlicht, bereitgestellt, umgeschaltet, bewaehrt, zurueckgenommen, abgebrochen. Es läuft höchstens ein
Release zugleich. Audit: `/var/log/wamo-release/audit.jsonl` (nur anhängen, `chattr +a` durch den Anker, jede Zeile mit der
Prüfsumme der vorigen) und `release_ereignisse`; nie Schlüssel, Tokens oder Gesprächsinhalte.

### D.2 Quelle des Codes und Übergabe

* **Spiegel:** `git clone --mirror` des Repos unter `/var/lib/wamo-release/spiegel.git`. Eine **zweite GitHub-App** (nur Repo
  `wamo`, Contents und Pull requests lesen und schreiben, Metadata lesen) mit eigenem Schlüssel `/etc/wamo-release/app.pem`
  (root:wamo-release 0640). Die bestehende App und ihr Schlüssel `/etc/wamo/gh-app.pem` (für `wamo` lesbar) dürfen das
  WAMO-Repo nie enthalten, der Dispatcher verweigert sonst den Start; sie bleibt unberührt.
* **Zwei Tokens:** lesend für das Holen der Basis, schreibend erst im Schritt Veröffentlichen (Branch `release/vX.Y.Z`, Tag,
  Release) und danach widerrufen, wie der Dispatcher es für seine Token tut.
* **Kein Push durch den Agenten:** Die Auftrags-Sandbox bekommt kein GitHub-Token. Der Release-Dienst liest nur die Dateien
  der Arbeitskopie (reguläre Dateien, keine Links, Größen- und Pfadgrenzen; `.git` wird nie geöffnet, weil Hooks und
  Konfiguration dort feindlich sein können), baut daraus über `git hash-object` und einen frischen Index den Commit C1 auf der
  festgehaltenen Basis und verwirft alles andere.
* **GitHub Free:** Es gibt keinen Branch-Schutz. Ersatz ist die Bindung der Freigabe an Commit-ID und Tree-ID und die Regel,
  dass der Release-Dienst `main` nur per Fast-Forward fortschreibt, wenn `main` noch die geprüfte Basis ist; sonst bricht er ab
  ("Basis hat sich geändert") und beginnt neu.
* **Menschliche Änderungen:** Die Quelle `main` (von Hand gemergte Pull Requests) durchläuft dieselbe Strecke, ohne Agent und
  ohne Refactor.

### D.3 Prüfstrecke (Prüfer-Sandbox, ohne Geheimnisse)

Wiederverwendet werden `wamo-job.sh` (bubblewrap, systemd-Grenzen), `abschottung.py` (Landlock und seccomp) und das Muster
von `wamo-egress.nft`, aber mit eigenem Benutzer `wamo-pruefer`, eigenem Ordner und ohne `/etc/wamo`. Der Egress ist enger als
beim Auftrags-Benutzer (dort alles auf 443): nur Loopback zu zwei Proxys des Release-Dienstes (CONNECT mit Domain-Allowlist,
DNS macht der Proxy). Proxy "Pakete": `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`, `github.com`,
`api.github.com`. Proxy "Claude": nur `api.anthropic.com`. **Code ausführen und Token halten geschieht nie im selben
Prozess.** Umsetzung (eigenes Netz der Sandbox statt Loopback-Egress, zwei zusätzliche Schrift-Hosts, Windows-Pakete nur gebaut) und Abnahme stehen im Änderungsplan unter 1.2c.

| Stufe | Werkzeuge | Tor |
| --- | --- | --- |
| Bauen | `uv sync --frozen` je Paket, `npm ci --ignore-scripts`, `next build` mit Platzhalterwerten wie im Dockerfile | Fehler bricht ab |
| Tests | `pytest` in `voice-core`, `host`, `geraete` (ohne `test_integration_*`), `npm test`, `supabase/tests` auf Wegwerf-Postgres | alles grün |
| Typen und Lint | `npx tsc --noEmit`, `npm run lint`, `ruff check` (festes Werkzeug außerhalb des Projekts, Version gepinnt, Regeln E, F, B, S; das Repo hat noch keine Ruff-Konfiguration) | null Fehler |
| Secret-Scan | eigene Muster (`sk-ant-`, JWT, PEM, `.env`, Schlüsselnamen aus `dev_guard.SECRET_PATH_RE`) über Diff und Arbeitsbaum | null Treffer |
| Fremdverweis-Grep | Mustervorlage liegt im Anker (`/etc/wamo-anker/fremdverweise.txt`), nicht im Repo | null Treffer |
| Abhängigkeiten | Lockfile-Diff (neue Pakete, Registry-Adressen nur npmjs und PyPI), `npm audit --omit=dev`, `pip-audit` | neue Pakete sichtbar, keine bekannte Lücke hoch oder kritisch |
| Migrations-Lint | Abschnitt C | Contract gesperrt |
| Wirkungsflächen | deterministischer Scan der hinzugefügten Zeilen: neue Netzwerkziele, `subprocess`, `eval`, `exec`, `pickle`, Zugriffe auf Geheimnisnamen (`WAMO_SCHLUESSEL_KEK`, `core.env`, `geheim.env`, `sudo`) | Anzeige in der Karte; Treffer auf Geheimnisnamen außerhalb ihres Moduls machen die Änderung zur Sonderfreigabe |
| Schutzschicht | `anker klassifiziere` am Diff (Abschnitt E) | normal, sonder oder gesperrt |

### D.4 Review und Refactor

* **Review** (nur lesen: `Read`, `Grep`, `Glob`, Wächterprofil `pruefung`): ECC-Agenten `code-reviewer`, `security-reviewer`,
  `python-reviewer`, `typescript-reviewer`, `react-reviewer` (nur bei `.tsx`), `database-reviewer` (nur bei Migrationen). Ein
  Befund der Schwere hoch oder kritisch hält die Strecke an, bis er behoben oder vom Admin mit Begründung quittiert ist.
* **Refactor** als eigener Commit R, nur für geänderte Dateien: `code-simplifier`, `refactor-cleaner` und der Standard
  `/wmc-vibecode-cleanup` (Verdikt BEHALTEN, ÜBERARBEITEN, NEU BAUEN; der Agent führt nur mechanische Schritte aus, "NEU BAUEN"
  kommt als Befund in die Karte, weil Architektur Menschensache bleibt; Pflicht-Sicherheitscheck, offene Befunde null).
  Werkzeuge: `Read`, `Grep`, `Glob`, `Edit`, `Write`, `Bash` nur lesend (Wächterprofil `pruefung`); Tests, Format und Lint
  laufen danach in der Prüfstufe ohne Token. Der Skill liegt als root-eigene Kopie in der Release-Sandbox (heute existiert er
  nur im Benutzerprofil des PCs).
* **Danach:** Prüfstrecke 2 wiederholt alles auf R. R darf Schutzschicht-Dateien nicht berühren, außer die Sonderfreigabe
  deckt sie ab; der Anker vergleicht C1 und R pfadweise. Verändert R das Verhalten (Testergebnis anders als vor R), fällt die
  Strecke auf C1 zurück und meldet "Refactor verworfen". C1 und R erscheinen in der Karte als getrennte Diffs.
* **ECC-Hooks:** Der Release-Lauf bekommt eine eigene Kopie des Plugins (Verfahren wie `wamo-job-ecc.py`) mit gefilterter
  `hooks.json`. Allowlist (Qualitäts-Hooks, die kein Programm aus dem Projekt starten): `pre:config-protection` (verhindert das
  Aufweichen von Lint- und Test-Konfiguration), `stop:check-console-log`, `pre:write:doc-file-warning`. Ausgeschlossen:
  `stop:format-typecheck` (ruft Formatierer und `tsc` aus dem Projektordner per `spawnSync`, siehe
  `scripts/hooks/stop-format-typecheck.js` im Plugin), alle Dispatcher, `observe`, `hookify-runtime`, Sitzungs- und
  Benachrichtigungs-Hooks. Format und Typprüfung übernehmen die Prüfstufen mit festen Werkzeugen. Die Skripte stehen mit
  SHA-256 im Manifest des Ankers; ein ECC-Update ist Handarbeit mit neuem Manifest.
* **Claude-Zugang (Entscheidung 6):** der des beauftragenden Admins, nie ein geteilter. Der Kern holt ihn frisch
  (`modellzugang.claude_code_zugang_fuer`) und übergibt ihn über `ctl.sock` für genau einen Lauf; der Dienst hält ihn nur im
  Speicher, setzt ihn nur in der Claude-Stufe (`CLAUDE_CODE_OAUTH_TOKEN` oder `ANTHROPIC_API_KEY`) und schwärzt ihn wie der
  Dispatcher. Kein Rückfall auf `geheim.env`, auch nicht für Niko (`WAMO_SERVERZUGANG` gilt hier nicht). Hat der Admin keinen
  eigenen Zugang, endet die Strecke vor dem Review mit einem klaren Satz. Modell wie `modell_waehlen`: Abo-Token Sonnet,
  API-Schlüssel Haiku. Die Karte nennt vorab die Prüftiefe ("schnell": code-reviewer, ein Sprach-Reviewer, security-reviewer;
  "voll": alle) und danach den verbrauchten Anteil des Kontingents (`kontingent.py`). Freigeben kann auch ein Admin ohne Zugang.

### D.5 Schnittstelle zum Kern und zur Oberfläche

| Seite | Inhalt |
| --- | --- |
| Kern-Werkzeuge (nur Rolle admin, `tools.py`) | `release_anfordern(titel, quelle)` und `release_status()`. Es gibt bewusst kein Werkzeug zum Freigeben: Freigaben gelten nie per Stimme (wie bei PC-Aktionen, `Freigaben.tsx`). |
| Servernachrichten (`ui/src/lib/nachrichten.ts`) | `{"type":"release","id","stand","schritt","schritte":[{"name","status"}],"version","commit","art"}` und `{"type":"update_bereit","version"}`; wie alle Nachrichten streng geprüft, Unbekanntes wird ignoriert |
| Client-Nachricht | `{"type":"release_status"}` fragt den Stand ab, mehr nicht |
| HTTP am Release-Dienst | `GET /wamo/release/status`, `GET /wamo/release/<id>/diff?teil=c1` oder `teil=r`, `POST /wamo/release/anfordern`, `POST /wamo/release/<id>/freigeben` (Body: `commit_id`, `tree_id`, gewählte Version), `POST /wamo/release/<id>/zurueck` |
| Karte "Änderungen" (`ui/src/components/kommando/`, neue Dateien `Release*.tsx`) | Zonen von oben nach unten: Kopf (Version, Art normal oder sonder, Quelle), Prüfstand (Ampel je Stufe), Diff (C1 und Refactor R getrennt umschaltbar), Wirkungsflächen und Migrationen, Versionsvorschlag (Auswahl nur gleich oder höher), Schaltfläche Freigeben. Ein Klick öffnet die Abfrage des zweiten Faktors (`mfa.challenge` und `verify`), erst dann geht die Freigabe ab. |

## E. Freigaben und Rollen

### E.1 Datenmodell (Migration 0013, erweiternd, mit `.down.sql`)

| Tabelle | Spalten (Auszug) | Schreibt | Liest |
| --- | --- | --- | --- |
| `release_anfragen` | `id`, `angefordert_von` (FK `profiles`), `art` (`normal`, `sonder`), `quelle` (`selbst`, `main`), `status`, `basis_commit`, `c1_commit`, `r_commit`, `v_commit`, `tree_id`, `bump`, `version_vorschlag`, `version_final`, `schutz_treffer` (jsonb, Pfade), `pruefstand` (jsonb, Ergebnis je Stufe), `wartet_bis`, `erstellt_at`, `aktualisiert_at` | `wamo_release` (Stand und Prüfstand), `wamo_anker` (Status ab `freigegeben`) | `wamo_core` nur lesend |
| `release_freigaben` | `id`, `anfrage_id` (FK), `freigeber` (FK `profiles`), `commit_id`, `tree_id`, `aal2_zeit`, `quell_ip`, `erstellt_at`, `widerrufen_at`; eindeutig je (`anfrage_id`, `freigeber`) | **nur** der Freigabe-Dienst des Ankers (`wamo_anker`) | `wamo_release`, `wamo_core` lesend |
| `release_ereignisse` | `id`, `anfrage_id`, `zeit`, `akteur` (null = System), `ereignis`, `daten` (jsonb, höchstens 4 KB), `vorher_hash`, `hash` | `wamo_release`, `wamo_anker`, nur einfügen | alle drei |
| `release_stand` | `komponente` (`ui`, `kern`), `aktive_version`, `aktiver_commit`, `farbe_oder_slot`, `seit`, `letzte_gute_version` | `wamo_anker` | `wamo_core`, `wamo_release` |

Rechte nach `CLAUDE.md`: RLS auf jeder Tabelle, Policy `core_all` für `wamo_core` (Grant nur `select`), je eine Policy für
die beiden neuen Rollen `wamo_release` und `wamo_anker` (beide LOGIN, DSN in root-eigenen Dateien), keine Rechte für `anon`,
`authenticated`, `service_role`, kein `delete`, FK-Indizes (partiell auf offene Status), Selbsttest wie in `0012`. Ein Trigger
auf `release_freigaben` prüft als zweite Linie, was der Dienst schon geprüft hat: Freigeber ist aktiver Admin, `commit_id` und
`tree_id` gleich denen der Anfrage, `aal2_zeit` höchstens 60 Sekunden alt, Status `wartet_freigabe`, bei `sonder` Freigeber
ungleich Beauftragter und `wartet_bis` erreicht.

### E.2 Regeln

| | normal | sonder (Schutzschicht berührt) |
| --- | --- | --- |
| Beauftragen | Admin | nur Admin; der Kern darf im Namen eines Admins anfragen, der Release-Dienst prüft die Rolle in der Datenbank, der Anker vor dem Bereitstellen erneut |
| Freigeben | ein Admin, auch der Beauftragende; Token mit zweitem Faktor höchstens 60 Sekunden alt | ein **anderer** Admin als der Beauftragte; der Beauftragte bestätigt vorher nur die Versionswahl |
| Wartezeit | keine | 15 Minuten zwischen "Karte bereit" und der frühesten Freigabe (Zeit zum Lesen) |
| Verfall | Freigabe gilt 60 Minuten | 24 Stunden; danach neu |
| Nur ein Admin | unverändert möglich | **gesperrt, fail closed**: der Anker zählt aktive Admins in der Datenbank, unter zwei lehnt `pruefe-sonder` ab, die Anfrage endet mit dem Satz "Braucht einen zweiten Admin". Es bleibt der heutige Weg von Hand (Pull Request, Skripte). |

**Frisches aal2:** Die Oberfläche löst `mfa.challenge` und `mfa.verify` unmittelbar vor dem Klick aus. Der Freigabe-Dienst
liest im Token den jüngsten `totp`-Eintrag in `amr` (Gegenstück zu `auth_gate.anmeldezeit`, das den frühesten nimmt), prüft
Signatur gegen JWKS, `iss`, `aud`, `aal2` und lehnt über 60 Sekunden ab. Er ist ein eigener kleiner Prozess ohne root
(Benutzer `wamo-anker`), damit weder Kern noch Release-Dienst Freigaben schreiben können.

**Erkennung "berührt die Schutzschicht":** `anker klassifiziere` im Anker, nicht der Agent, nicht ein Prompt. Es bildet
`git diff --name-status --no-renames -z` zwischen Basis und Kopf (alter und neuer Pfad zählen, auch Löschen, Moduswechsel,
Verweise, Untermodule), normalisiert die Pfade (Unicode NFC, Groß- und Kleinschreibung, kein `..`) und vergleicht mit der
Liste. Maßgeblich ist der Boden `/etc/wamo-anker/schutzpfade.txt` (root); das Repo darf die Liste in
`infra/sokrates2/anker/schutzpfade.txt` nur erweitern, nie kürzen. Zusätzlich machen die Funde der Wirkungsflächen-Stufe
(Geheimnisnamen) eine Änderung zu `sonder`.

| Klasse | Pfade (Boden der Liste) |
| --- | --- |
| gesperrt, nie über den Release-Weg | `infra/sokrates2/anker/**`, `/etc/wamo-anker/**` (liegt ohnehin außerhalb), die Liste selbst |
| sonder | Wächter und Aufträge: `voice-core/dev_guard.py`, `dev_agent.py`, `werkzeugkarte.py`, `infra/sokrates2/wamo_job_dispatch.py`, `wamo-job*`, `abschottung.py`, `claude-code/**`. Anmeldung: `voice-core/auth_gate.py`, `kern_dienst.py`, `wamo_api.py`, `wamo_tls.py`, `memory.py`, `sitzungen.py`. Schlüssel: `schluessel*.py`, `modellzugang*.py`, `ui/src/components/ModellZugang*`, `ui/src/lib/modellzugang*`. Geräte und PC: `pc_*.py`, `geraet*_api.py`, `geraete_*.py`, `host/**`, `geraete/**`, `desktop/**`. Release: `infra/sokrates2/release/**`, `ui/src/components/kommando/Release*`, `ui/src/components/Freigaben.tsx`, `LoginGate.tsx`, `ui/src/lib/supabase.ts`, `anmeldung.ts`, `ui/next.config.ts`. Infrastruktur: `infra/sokrates2/**`, `.github/**`, `scripts/**`, `Dockerfile`s. Daten: `supabase/migrations/**`, `supabase/tests/**`. Anweisungen an Agenten: `CLAUDE.md`, `**/AGENTS.md`, `.claude/**`, `.mcp.json`, `package.json`-Skripte |
| normal | alles andere; neue Abhängigkeiten in Lockdateien werden in der Karte gelb markiert und brauchen eine Bestätigung |

Bis Stufe 4 gilt: Änderungen der Quelle `selbst` mit Treffer sind **gesperrt**. Für Änderungen der Quelle `main` (Mensch, per
Pull Request geprüft) mit Treffer genügt ein Admin mit der zusätzlichen Bestätigung "Schutzschicht berührt". Ab Stufe 4 gilt
für beide Quellen die Sonderfreigabe.

## F. Versionierung

Ein Skript `scripts/wamo_version.py` (nur Standardbibliothek: `subprocess` für git, `re`, `tomllib`, `json`) mit den
Unterbefehlen `vorschlag`, `anwenden <version>` und `pruefen`. Es gibt eine Version für das ganze Monorepo.

1. **Basis:** höchster Tag `vX.Y.Z` (Muster `^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`, keine Vorabversionen). Gibt es keinen,
   gilt der Start unten.
2. **Commits** zwischen Basis und Kopf ohne Merge-Commits; bei Squash zählt der Titel. Lesart nach Conventional Commits:
   `Typ(Bereich)!: Text` und Fußzeile `BREAKING CHANGE:`.
3. **Stufe:** `fix` und `perf` ergeben Patch, `feat` Minor, `!` oder `BREAKING CHANGE` Major. `docs`, `chore`, `ci`, `test`,
   `style`, `refactor`, `build` heben nicht an; enthält das Release Änderungen außerhalb von `docs/`, gilt mindestens Patch.
   Nicht konventionelle Titel (heute etwa der von PR #5) zählen als Patch und erscheinen als Warnung mit dem Hinweis, den
   Titel konventionell zu schreiben. Der Major-Sprung von 0.y.z auf 1.0.0 braucht eine ausdrückliche Bestätigung des Admins.
4. **Vorschlag und Wahl:** Der Vorschlag steht in der Karte. Der Admin darf Patch auf Minor oder Major erhöhen, nie unter den
   Vorschlag senken und nie unter das letzte Tag; `anwenden` und der Dienst lehnen es ab.
5. **Versionsdateien** (müssen übereinstimmen, `pruefen` meldet jeden Unterschied): `ui/package.json` und `package-lock.json`
   (Wurzel und `packages[""]`), `desktop/package.json` mit Lock, `voice-core/pyproject.toml`, `host/pyproject.toml`,
   `geraete/pyproject.toml` und die Einträge der Pakete in den `uv.lock` (sonst scheitert `uv sync --locked`).
   `next.config.ts` liest die Version aus `ui/package.json` für die Anmeldeseite; `version.json` kommt aus demselben Wert.
6. **`CHANGELOG.md`** nach Keep a Changelog, deutsch: `## [Unveröffentlicht]`, dann ein Abschnitt je Version nach dem Muster `## [0.1.1] - 2026-10-22` mit den Gruppen
   Hinzugefügt (`feat`), Geändert (`refactor`, `perf`), Behoben (`fix`), Entfernt, Sicherheit; je Commit eine Zeile mit PR-Nummer,
   Brüche vorangestellt, Vergleichslinks am Ende.
7. **Release-Commit V:** `chore(release): v0.1.1` enthält nur Versionsdateien und `CHANGELOG.md`, erzeugt vom Skript. Die
   Stufe Prüfstrecke 3 führt das Skript erneut aus und vergleicht Byte für Byte; ein Unterschied bricht ab.
8. **Veröffentlichen** (nach der Freigabe): annotierter Tag `vX.Y.Z` auf V (Text = Changelog-Abschnitt; auf Wunsch mit
   SSH-Signatur, `gpg.format=ssh`), GitHub Release mit demselben Text, `main` per Fast-Forward.
9. **Start:** Heute stehen alle Pakete auf 0.1.0 und es gibt kein Tag. Vorschlag: erstes Tag `v0.1.0` von Hand auf den Merge
   von PR #7, ohne Version-Bump-Commit. Der erste automatische Release ergibt dann v0.1.1 oder v0.2.0.

## G. Notanker

Der Anker ist alles, was Root-Rechte oder die Echtheit der Freigabe braucht, und sonst nichts. Er ist klein (Python,
Standardbibliothek, unter 1000 Zeilen plus Tests; der Freigabe-Dienst kommt als zweiter kleiner Prozess dazu), hat Quelltext im Repo (`infra/sokrates2/anker/`, damit er prüfbar ist), wird aber
**nie** durch den Release-Weg installiert.

| Befehl | Wirkung |
| --- | --- |
| `status` | aktive und letzte gute Version je Komponente, Sperre, offene Anfragen |
| `rueckweg ui`, `kern`, `alles` | zurück auf `letzte_gute` aus `/var/lib/wamo-anker/stand.json` (root 0600, geschrieben erst nach bestandener Bewährung): UI Include-Datei und Reload, Kern Slot der letzten guten Version starten und Include tauschen. Die Datenbank geht nicht zurück (erweiternd, C) |
| `klassifiziere` | Pfadliste und Funde, Ergebnis normal, sonder oder gesperrt (E) |
| `pruefe-sonder <id>` | liest nur (Rolle `wamo_anker`, in diesem Befehl ohne Schreiben): ein Freigeber ungleich Beauftragtem, beide aktive Admins, mindestens zwei aktive Admins, Commit und Tree gleich, Wartezeit vorbei, nicht verfallen, Basis noch gültig. Ausgabe `OK` oder `NEIN <Grund>` |
| `bereitstellen`, `migrieren`, `umschalten` | die Root-Schritte aus A bis C; prüfen vorher jede Freigabe erneut aus der Datenbank und bauen aus `git archive <commit_id>` eines frisch geholten, mit `fsck` geprüften Stands |
| `sperre an` oder `aus` | Abschalter: Datei `/etc/wamo-anker/selbstaenderung.aus`. Steht sie, nimmt der Dispatcher keine Erlaubnis für `selbstaenderung` mehr an, der Release-Dienst lehnt neue Anfragen ab, der Anker führt nur noch `rueckweg` und `status` aus |
| `bewaehrung` | läuft als eigene transiente Unit (`systemd-run`), 30 Minuten, löst bei Fehlern den Rückweg aus, auch wenn der Release-Dienst tot ist |

**Unveränderlich:** Programme in `/usr/local/lib/wamo-anker` (root:root 0755), Konfiguration `/etc/wamo-anker` (root, 0644, ohne
Geheimnisse; dort auch `fremdverweise.txt` und `schutzpfade.txt`), Zustand `/var/lib/wamo-anker` (root 0700), Datenbankzugang
in root-eigenen Dateien, Units in `/etc/systemd/system`. Weder `wamo`, `wamo-core` noch `wamo-release` haben dort
Schreibrechte oder eine sudo-Regel; der Release-Dienst erreicht den Anker nur über `anker.sock` (`SO_PEERCRED`, feste
Befehle). Installiert und aktualisiert wird er nur von Hand (`sudo bash anker-einrichten.sh` aus `~/wamo-infra`, nach den
Regeln aus Abschnitt 0 der Infra-README). Grenze: Wer auf Sokrates-2 `sudo` oder die docker-Gruppe hat, kann ihn ändern. Er
schützt gegen WAMO und die Dienstbenutzer, nicht gegen Menschen mit root.

**Test:** (1) Einheitentests mit Attrappen für `docker`, `systemctl` und `caddy` über den PATH; (2) `--trocken` zeigt jeden
Befehl, ohne ihn auszuführen; (3) Rückwegübung mit einer absichtlich ungesunden Version, Zeit gemessen, Ziel unter 30 Sekunden
für die Oberfläche und unter 60 Sekunden für den Kern; (4) Verbotsprobe als `wamo`, `wamo-release` und `wamo-core`: Schreiben in
die Ankerordner und Aufrufe von `anker.sock` mit falscher Kennung müssen scheitern; (5) Fixtures für `pruefe-sonder`: eine
Person doppelt, Beauftragter als Freigeber, nur ein Admin, abgelaufen, anderer Tree, je `NEIN`; (6) Übung nach jeder
Änderung am Anker und vierteljährlich; `anker status` läuft nachts per Timer und meldet, wenn `main` vom letzten Tag abweicht.

## H. Sicherheitsbewertung

| Bedrohung | Gegenmaßnahme | Restrisiko |
| --- | --- | --- |
| Prompt Injection im Repo (Kommentare, Tests, `CLAUDE.md`) | Auftrags-Sandbox ohne GitHub-Token und mit Allowlist-Netz; Ergebnis sind nur Dateien; Anweisungsdateien sind Schutzschicht; Review-Agenten haben nur Lesewerkzeuge und behandeln Repo-Inhalt als Daten | Reviewer-Agenten lassen sich beeinflussen; darum zusätzlich deterministische Scans und der Mensch |
| Manipulierte Abhängigkeiten | `--ignore-scripts`, nur npmjs und PyPI, Lockfile-Diff mit Bestätigung, `npm audit`, `pip-audit`, Prüfstufe ohne Token | bösartiges Paket ohne Install-Skript wirkt erst zur Laufzeit; ein Mensch bestätigt jedes neue Paket |
| Angriff auf den Release-Dienst | unprivilegiert, hart eingeschränkt, kann weder schalten noch Freigaben schreiben; Anker prüft alles erneut | er kann die Karte verfälschen; bis Stufe 4 ist der Diff mit `git diff` unabhängig nachprüfbar, ab Stufe 4 liefert die unabhängige Freigabeseite den Diff selbst |
| Gekaperter Kern (sieht alle Tokens) | Kern darf nur anfragen, Freigabe läuft am Kern vorbei, Token höchstens 60 Sekunden alt, Anfrage- und Commit-ID im Body, jede Freigabe meldet sich allen Admins | ein mitgelesenes frisches Token ließe sich kurz wiederverwenden; Gegenmittel in Stufe 4: Freigabeseite mit eigenem Ursprung, damit das Token nie durch den Kern läuft |
| Freigabe-Umgehung durch Zweitkonto | Konten legt nur ein Admin an (`scripts/wamo_nutzer.py`), Sonderfreigabe verlangt zwei verschiedene Profile, Hinweis an alle Admins, Quelladresse wird vermerkt | eine Person mit zwei Admin-Konten bleibt organisatorisch zu verhindern |
| Race zwischen Freigabe und Bereitstellung | Freigabe an Commit-ID und Tree-ID gebunden; Anker prüft erneut, holt den Stand frisch und baut nur aus `git archive <commit_id>`; die Zeile wird gesperrt; Freigabe verfällt | gering |
| Schädlicher Refactor nach der Freigabe | gibt es nicht: der Refactor R liegt vor der Freigabe und ist Teil von V; ein weiterer Agentenlauf ist eine neue Anfrage; C1 und R werden getrennt gezeigt | Refactor kann Verhalten ändern, das kein Test abdeckt; Prüfstrecke 2 vergleicht die Ergebnisse |
| Freigeber getäuscht (großer oder versteckter Diff) | Grenze 800 Zeilen oder 25 Dateien je Release (sonst Aufteilen), Liste der Wirkungsflächen, Markierung von Bidi- und Steuerzeichen und sehr langen Zeilen | Mensch bleibt Engpass |
| WAMO ändert die Freigabekarte | `Release*.tsx`, `Freigaben.tsx`, Anmeldung gehören zur Schutzschicht | bis Stufe 4 nur von Hand |
| Kontingent und Kosten | Vorabhinweis zur Prüftiefe, Anzeige des Verbrauchs, ein Release gleichzeitig, höchstens drei Anfragen je Admin und Tag | Max-Kontingent des Admins wird verbraucht |
| GitHub Free ohne Schutzregeln | zweite App mit Schlüssel nur für `wamo-release`, Schreibtoken nur im Schritt Veröffentlichen, nächtlicher Vergleich `main` gegen Tag | gestohlener App-Schlüssel erlaubt Pushes bis zum Entdecken |

**Sollte "Schutzschicht änderbar" schon in Stufe 1 zugelassen werden?** Nein. Gründe: Die Schutzschicht trägt alle anderen
Maßnahmen (Wächter, Anmeldung, Freigabe, Release-Dienst); ein Fehler dort ist mit demselben Weg nicht mehr heilbar, nur noch
mit dem Anker. Der Release-Weg ist beim ersten Einsatz unerprobt. Die Vier-Augen-Regel braucht mindestens zwei aktive Admins. Der
Nutzen ist klein, weil sich die Schutzschicht selten ändert und von Hand per Pull Request änderbar bleibt. **Sichere Reihenfolge:**
Stufen 0 bis 3, dann Stufe 4, sobald zehn Releases ohne Zwischenfall liefen, mindestens zwei echte Rückwegübungen bestanden
sind, zwei aktive Admins existieren, der Red-Team-Test (3.5) bestanden ist und die unabhängige Freigabeseite steht. Die
Entscheidung trifft der Nutzer.

**Lockerung von `SELF_REPO` in Stufen:** Bis Schritt 3.1 bleibt die Sperre in `dev_guard.py:66`, `dev_agent.py` und
`wamo_job_dispatch.py` unverändert. Ab 3.1 gilt sie weiter für das Repo `wamo`, den Namen `wamo` und die GitHub-App-Installation
(die zweite App liegt in einer getrennten Installation). Neu ist nur eine Arbeitskopie `wamo-selbst` für die Art
`selbstaenderung`, und nur mit einer Erlaubnisdatei, die der Release-Dienst über den Anker root-eigen anlegt (Besitzer root,
einmalig, befristet, an Auftrags-ID und Admin gebunden). Der Dispatcher prüft sie; ohne sie bleibt jede Anfrage so gesperrt wie
heute. Die Arbeitskopie hat kein Token und keinen Weg ins Repo außer dem Patch, den der Release-Dienst liest. Die erste Stufe der
Lockerung ist lesend (Planung, Review, Analyse der eigenen Quellen), erst danach schreibend, erst Stufe 4 mit Schutzschicht.

## I. Stufenplan (Blueprint)

| Stufe | Inhalt | Selbstschutz | Aufwand | Abnahme |
| --- | --- | --- | --- | --- |
| 0 | Anker und Gerüst des Release-Dienstes, nur Umschalten und Rückweg, kein Agent | unverändert | 50 h | Rückwegübung, Verbotsproben |
| 1 | Oberfläche blau und grün, Prüfstrecke, Freigabe, Versionierung, menschliche Änderungen | unverändert | 84 h | Release der Oberfläche ohne Lücke, Rückweg unter 30 Sekunden |
| 2 | Kern mit zwei Instanzen und Ausleiten, Migrationen automatisch | unverändert | 78 h | Kern-Release mit drei laufenden Sitzungen, keine getrennt |
| 3 | WAMO als Autor (nicht Schutzschicht), Review und Refactor, Sprachweg | gelockert nur für `wamo-selbst`, Schutzschicht gesperrt | 68 h | Red-Team bestanden |
| 4 | Sonderfreigabe für die Schutzschicht | wie 3, plus Sonder | 44 h | Sonder-Dummy-Release, Zwei-Admin-Test |

Gesamt etwa **324 Stunden**, reihenfolgebedingt etwa 8 bis 10 Wochen bei einer Person. Jeder Schritt ist ein Pull Request mit
`verification-loop`, `security-reviewer` und `python-reviewer` (UI: `react-reviewer`, SQL: `database-reviewer` vor jedem
PROD-Apply). Stärkstes Modell für 0.1, 0.3, 1.4, 2.3, 2.4, 3.1, 3.3, 3.5 und alle Schritte der Stufe 4, sonst Standard.

| Nr | Ziel | Dateien (neu oder geändert) | Tests | Rückweg | h | nach |
| --- | --- | --- | --- | --- | --- | --- |
| 0.1 | Anker-Kern: `status`, `rueckweg`, `sperre`, `--trocken`, Socket mit `SO_PEERCRED` | `infra/sokrates2/anker/**`, `anker-einrichten.sh` | Attrappen-Tests, Verbotsprobe | `anker-rueckweg.sh` entfernt alles | 14 | |
| 0.2 | Migration 0013: Tabellen, Rollen, Trigger, `sha256` in `schema_migrations` | `supabase/migrations/0013_*.sql` mit `.down.sql`, `supabase/tests/0013_*` | SQL-Tests, Rechtedrift-Selbsttest | `.down.sql` | 8 | |
| 0.3 | Release-Dienst-Gerüst: Unit, Sockets, `status`, `zurueck`, Audit-Kette, Zustandsautomat | `infra/sokrates2/release/**`, `wamo-release.service` | Einheitentests, Kettenprüfung | Dienst stoppen | 14 | 0.1 |
| 0.4 | Versionsskript und `CHANGELOG.md` | `scripts/wamo_version.py`, Tests, `CHANGELOG.md` | Tabelle von Fällen, Dateikonsistenz | Datei löschen | 8 | |
| 0.5 | Einrichtung, Rückweg, Rauchtest, Doku, Ergänzung des Namensvertrags | `infra/sokrates2/*.sh`, `README.md`, `CLAUDE.md` | Rauchtest | Rückwegskripte | 6 | 0.1 bis 0.3 |
| 1.1 | UI blau und grün, `version.json`, Include, `umschalten ui` | `wamo-ui/**`, `wamo-ui-einrichten.sh`, `scripts/wamo_ui_sokrates2.py` | Umschalten mit Dauermessung (Anfragen im Takt), Rückweg | `anker rueckweg ui` | 14 | 0.1 |
| 1.2 | Prüfer-Sandbox, Proxys, Stufen Bauen bis Wirkungsflächen | `infra/sokrates2/release/pruefer/**`, nft-Datei | Proben mit Absicht kaputtem Code, gesperrte Ziele | Dienst aus | 24 | 0.3 |
| 1.3 | Quelle: zweite GitHub-App, Spiegel, Commit, Tag, Release, Fast-Forward | `release/quelle.py` | Attrappe der GitHub-API, Rauchtest `--app-echt` lesend | App-Installation entfernen | 10 | 0.3, 0.4 |
| 1.4 | Freigabe-Dienst: Token, frisches aal2, Tabelle, Caddy-Route | `anker/freigabe.py`, `wamo-caddy/kern/*.caddy` | Fixtures (alt, falsches aal2, falscher Tree) | Route entfernen | 12 | 0.2 |
| 1.5 | Karte "Änderungen", Servernachrichten, Diff-Ansicht | `ui/src/components/kommando/Release*.tsx`, `ui/src/lib/nachrichten.ts` | `node --test`, Handprobe | vorige Farbe | 16 | 1.4 |
| 1.6 | Ende zu Ende: menschlicher Pull Request bis Umschalten, Rückweg | keine neuen | Übung, Zeitmessung | Anker | 8 | 1.1 bis 1.5 |
| 2.1 | Unit-Vorlage `wamo-core@.service`, Slots, Vorprüfung, Skripte | `wamo-core*.sh`, `scripts/wamo_kern_sokrates2.py` | Vorprüfung mit zwei Slots | alte Unit | 12 | 1.6 |
| 2.2 | Caddy `/i/<slot>/`, `/wamo/slot`, Oberfläche `startMitSlot`, 409-Wiederholung | `wamo-core.caddy`, `ui/src/lib/useWamo.ts` | Test: ICE-PATCH und Neuverhandlung bleiben auf dem Slot | Datei zurück | 12 | 2.1 |
| 2.3 | Sitzungsliste und Ablöse-Brücke, PC-Sperre | `voice-core/sitzungen.py`, `pc_host.py` | zwei Prozesse, Ablösen in beide Richtungen, Absturz | Schalter aus | 14 | 2.1 |
| 2.4 | `umschalten kern`, Ausleiten, Bewährung, Auto-Rückweg | `anker/**` | Übung mit ungesundem Slot | `anker rueckweg kern` | 14 | 2.2, 2.3 |
| 2.5 | Migrationen: Lint, Wegwerf-Postgres, `migrieren`, `database-reviewer`-Stufe | `release/migration_lint.py`, `anker/**` | Fälle erlaubt und verboten, frische Anwendung 0001 bis N | `.down.sql` | 18 | 1.2 |
| 2.6 | Übung: drei Browser, ein Laptop-Tunnel, Kern-Release, Rückweg | keine neuen | Protokoll der Übung | Anker | 8 | 2.4, 2.5 |
| 3.1 | Art `selbstaenderung`, Arbeitskopie, Erlaubnisdatei | `dev_agent.py`, `werkzeugkarte.py`, `wamo_job_dispatch.py`, `dev_guard.py` | `test_dev_guard.py`, `test_dev_agent_sokrates2.py`, `test_sokrates2_dispatch.py`: alte Sperren bleiben, neuer Pfad nur mit Erlaubnis | Erlaubnis nicht ausstellen, `sperre an` | 16 | 1.6 |
| 3.2 | Übernahme als Commit C1, Klassifizierung, sonder und gesperrt abgelehnt | `release/uebernahme.py`, `anker/**` | Pfadtricks | wie 3.1 | 10 | 3.1 |
| 3.3 | Review und Refactor: Wächterprofil, Hook-Allowlist, Skill-Kopie, Zugang des Admins | `release/agenten.py`, `wamo-job-ecc.py`-Variante | Allowlist-Test, Schwärzung, kein Rückfall auf `geheim.env` | Stufe überspringen nicht möglich, `sperre an` | 20 | 3.2 |
| 3.4 | Sprachweg, Werkzeuge, Karte um Review und Refactor, Prüftiefe, Kontingent | `tools.py`, `bot.py`, `ui/**` | Kern-Tests, `node --test` | Werkzeuge aus | 12 | 3.3 |
| 3.5 | Red-Team: Prompt Injection, Pfadtricks, Race, Bidi-Zeichen | Testdateien | Protokoll, alle Funde behoben | `sperre an` | 10 | 3.4 |
| 4.1 | Sonderfreigabe: Trigger, Wartezeit, `pruefe-sonder`, Verhalten bei einem Admin | `anker/**`, Migration 0014 | Fixtures aus G | `sperre an` | 10 | 3.5 |
| 4.2 | Unabhängige Freigabeseite mit eigener Anmeldung und eigenem Ursprung | `anker/freigabeseite/**`, Caddy | Handprobe, Token-Weg ohne Kern | Route entfernen | 12 | 4.1 |
| 4.3 | Boden der Schutzliste, Inhaltsfunde, Öffnung der Schutzschicht außer Anker | `anker/**`, `release/**` | Pfadtabelle | `sperre an` | 8 | 4.1 |
| 4.4 | Lockerung im Dispatcher für Art sonder | `wamo_job_dispatch.py`, `dev_guard.py` | Dispatcher-Tests | Erlaubnis nicht ausstellen | 6 | 4.2, 4.3 |
| 4.5 | Übung: Sonder-Dummy-Release, Zwei-Admin-Test, Rückweg | keine neuen | Protokoll | Anker | 8 | 4.4 |

**Abhängigkeiten und Parallelität:** 0.1, 0.2 und 0.4 laufen parallel; 0.3 folgt 0.1; 1.1, 1.2, 1.3 und 1.4 sind nach Stufe 0
parallel möglich, 1.5 wartet auf 1.4; 2.2 und 2.3 laufen nach 2.1 parallel; 2.5 ist von 2.1 bis 2.4 unabhängig und kann
danebenlaufen; 3.1 darf schon neben Stufe 2 beginnen und ab 1.6 für Pfade unter `ui/` eingesetzt werden; Änderungen am Kern (`voice-core/`) erst nach 2.6, weil sie einen Kern-Release ohne Trennung brauchen. **Invarianten nach jedem Schritt:**
alle bisherigen Tests grün, `SELF_REPO` in den drei Dateien unverändert (bis 3.1), Fremdverweis-Grep leer, kein Geheimnis im
Diff, nichts schaltet ohne geprüften Rückweg. **Änderungen am Plan** (teilen, einfügen, überspringen, umordnen, aufgeben)
trägt der Bearbeiter am Dateiende mit Datum, Schritt und Grund ein.

**Die drei größten Risiken:** (1) Die Freigabe ist nur so gut wie das Lesen des Diffs, und der Kern sieht Tokens; Gegenmittel
sind kleine Diffs, Wirkungsflächen, die Freigabe am Kern vorbei und in Stufe 4 die unabhängige Freigabeseite. (2) Der Kern mit
zwei Instanzen hängt am Verhalten von Pipecat 1.12 und `small-webrtc-transport`; Sitzungsliste, PC-Sperre und Pfadrouting sind
neu und nur durch die Übung 2.6 belegt, ein Paketupdate kann sie brechen. (3) Der Anker hat Root-Macht und wendet Migrationen
als `postgres` an; ein Fehler dort ist schwer rückholbar, und verletzen Migrationen die Regel "erst erweitern", ist der
Rückweg des Kerns wertlos.

## Offene Entscheidungen für den Nutzer

1. Wie viele aktive Admins gibt es heute? Bei einem bleibt Stufe 4 gesperrt. Darf bei `normal` der Beauftragte seine eigene
   Änderung freigeben (so entworfen), oder soll auch dort ein zweiter Admin nötig sein?
2. Zweite GitHub-App für das Repo `wamo` (Contents und Pull requests schreibend) anlegen? Die bestehende darf es nie enthalten.
   **Entschieden am 02.11.2026 (Niko):** Später. Bis dahin nur lesend und Merge von Hand (siehe 5); die App legt Niko selbst an, bis dahin wird 1.3 gegen eine Attrappe der GitHub-API gebaut.
3. Migrationen vorerst durch den Anker als `postgres` (so entworfen) oder zuerst eine Rolle `wamo_migrator` mit Eigentum an
   den WAMO-Objekten (mehr Aufwand, kein Superuser im Ablauf)?
4. Ausleiten des alten Kerns bis zu 120 Minuten abwarten (so entworfen), oder zusätzlich das automatische Erneuern bei
   Gesprächspause anbieten (kurze Wiederverbindung)?
5. `main` fortschreiben durch den Release-Dienst per Fast-Forward (so entworfen) oder nur Branch und Tag, den Merge macht ein
   Mensch auf GitHub?
   **Entschieden am 02.11.2026 (Niko):** Vorerst "nur lesen, Merge von Hand": Der Release-Dienst schreibt nichts nach GitHub und nie auf `main`. Das Fortschreiben per Fast-Forward kommt mit der zweiten GitHub-App (Entscheidung 2) und wird dann gesondert freigegeben. (Zwischenstand, davor am selben Tag "Fast-Forward durch den Dienst" gewählt und mit der Zurückstellung der App überholt.)

## Änderungen am Plan

| Datum | Schritt | Änderung | Grund |
| --- | --- | --- | --- |
| 22.10.2026 | 1.1 | Die Gesundheitsprobe ist eine Route (`ui/src/app/version.json/route.ts`, dynamisch, Felder `name`, `version`, `commit`, `gebaut`, `sauber`), keine statische `ui/public/version.json`. Commit, Bauzeit und Sauberkeit kommen als Build-Argumente `WAMO_COMMIT`, `WAMO_BAUZEIT`, `WAMO_SAUBER` in die Umgebung des Laufzeit-Images; die Version aus `package.json`. Der Hinweis "Neue Version, bitte neu laden" (Abfrage alle 5 Minuten) bleibt offen und gehört zu Schritt 1.5 | Eine Datei in `public/` würde mit der Route kollidieren; die Route prüft jeden Wert gegen ein enges Muster und berechnet die Antwort bei jeder Anfrage |
| 22.10.2026 | 1.1 | Statische Dateien: Caddy fragt zuerst die live gesetzte Farbe und bei 404 die vorige (`handle_response` mit zweitem `reverse_proxy`), im Rauchtest mit echtem Caddy belegt. Der Zustand (live, vorige, seit) steht in den Kopfzeilen der Include-Datei `etc/kern/wamo-ui-aktiv.inc`, eine zweite Zustandsdatei gibt es nicht | Die Include-Datei ist die einzige Wahrheit, sie wird bei jedem Schalten aus der Vorlage im Skript neu erzeugt und vorher als ganze Konfiguration geprüft |
| 22.10.2026 | 1.1 | Die Site schützt mit einem `route`-Block (405 für alles außer GET und HEAD vor dem Include). Der Rückfall auf 404 gilt nur für zwei Fassungen: Seiten, die zwei Releases zurückliegen, verlieren nachgeladene Chunks | Caddy ordnet `handle` vor `respond` ein (Fund des Reviews, im Rauchtest belegt); die Farbe, die das nächste Release ersetzt, trägt die älteste Fassung |
| 22.10.2026 | 1.1 | `wamo-ui-umschalten.sh` ist zugleich das Schritt-Skript des Ankers: Als `schritte/rueckweg-ui` kopiert, bedient es `rueckweg-ui vX.Y.Z HEX40 Farbe` (blau oder gruen; idempotent, nur saubere Stände). Die Zeile `EINTRAG {...}` am Ende von `umschalten` ist der Eintrag "letzte gute Version" im Format von `stand.json`; geschrieben wird `stand.json` weiterhin nur vom Anker | Der Anker bleibt unverändert (Schritt 0.1); die Verdrahtung (Kopie ins Verzeichnis `anker/schritte`, Manifest) ist ein Handgriff in der Anker-Einrichtung |
| 01.11.2026 | 1.4 | Geteilt: 1.4a sind die Regeln des Freigabe-Dienstes (`infra/sokrates2/anker/freigabe.py`, Token und frisches aal2 über den JÜNGSTEN `totp`-Eintrag, Bindung an Commit-ID und Tree-ID, Drosselung, Abbildung der Trigger-Sätze auf feste Codes, Attrappe der Datenbank) und `voice-core/test_freigabe.py` (47 Tests, Sicherheitsprüfung ohne kritischen oder hohen Fund, Funde umgesetzt). 1.4b bleibt offen: Prozess (Unix-Socket, Benutzer `wamo-anker`, Schlüsselsatz von GoTrue), Caddy-Route, echte Datenbankanbindung als Rolle `wamo_anker`, Rauchtest mit echter GoTrue-Instanz (zu belegen: der `totp`-Zeitstempel in `amr` wird beim Token-Refresh nicht erneuert). | Die Regeln sind ohne Netz und Datenbank vollständig prüfbar, die Anbindung hängt an der noch fehlenden Datenbankschicht des ganzen Release-Wegs (Rollen, Zugangsdatei, Treiber), die auch Schritt 1.2 und 1.3 brauchen. Sie wird einmal für alle gebaut. |
| 01.11.2026 | 1.4b, 1.2, 1.3 | Die gemeinsame Datenbankschicht ist gebaut (`infra/sokrates2/pg/`: `wamo_pg.py` mit Zugangsdatei, TLS verify-full, Zeitgrenzen und Fehlerabbildung ohne Servertext, `release_pg.py` für den Release-Dienst, `freigabe_pg.py` für den Freigabe-Dienst) und gegen ein echtes Wegwerf-Postgres mit den echten Migrationen und Rollen geprüft (`voice-core/test_datenbankschicht.py`, 28 Tests, Teil 2 braucht Docker). Dabei fand das echte Postgres einen Fehler in Migration 0013: Der Trigger `release_anfragen_guard` liest `release_freigaben` mit `FOR SHARE`, das verlangt ein Update-Recht, das `wamo_release` bewusst nicht hat; der Release-Dienst hätte nie auf `veroeffentlicht` schalten können. Behoben in der neuen Migration `0016_release_freigaben_lesen` (zwei Funktionen mit festen Rechten, Trigger sonst unverändert, mit Rückweg). 0016 ist am 01.11.2026 auf `wamo_dev` und `wamo_test` angewendet (zweiter Lauf folgenlos, SQL-Test von 0013 weiter grün), auf `wamo_prod` nicht (braucht `database-reviewer`). Am selben Tag sind die Login-Rollen `wamo_release_s2` und `wamo_anker_s2` und die Zugangsdateien `/etc/wamo-release/db.zugang` (root:wamo-release 0640) und `/etc/wamo-anker/db.zugang` (root:root 0600) eingerichtet (`scripts/wamo_dienst_db.py`); gemessen über Supavisor mit TLS verify-full: Anmeldung, Rechte und Verbotsproben bestehen. Befund dabei: Supavisor ignoriert die Startoptionen der Verbindung (der `statement_timeout` blieb bei der Einstellung der Rolle), die Zeitgrenzen gelten deshalb als `SET LOCAL` in jeder Transaktion; und er lehnt die erste Anmeldung direkt nach einer Passwortrotation noch mit dem alten Geheimnis ab. Offen für den Betrieb: Treiber `psycopg[binary]` in einer Umgebung der Dienste, Netzfreigabe der Dienste nur zur Datenbank (heute `RestrictAddressFamilies=AF_UNIX`), Benutzer `wamo-anker` und Prozess des Freigabe-Dienstes, Installation der Dateien mit Prüfsummen. | Die Datenbank war für alle Schritte der Stufe 1 die gemeinsame Voraussetzung. Der Fehler in 0013 zeigt, warum die Prüfung gegen das echte Postgres nötig war: Weder die Attrappe noch die SQL-Tests mit dem Eigentümer hatten ihn gesehen. |
| 01.11.2026 | 1.4b, 1.2 | Der Release-Dienst bekommt die echte Datenbank: `wamo_release.py` lädt über `datenbank_aus_umgebung()` `ReleaseDatenbank` (Rolle `wamo_release_s2`), sobald `WAMO_RELEASE_SCHEMA` gesetzt und `/etc/wamo-release/db.zugang` gültig ist, sonst wie bisher `OhneDatenbank`. Treiber psycopg liegt hash-gesichert (`pg/requirements.lock`, `pip --require-hashes --no-deps`) root-eigen unter `/opt/wamo-release/pylib` und wird hinten an `sys.path` gehängt. Netz nur zur Datenbank: Unit mit `AF_UNIX AF_INET`, `IPAddressDeny=any` und `IPAddressAllow=192.168.178.136/32`, dazu die nft-Tabelle `wamo_release_ausgang` (nur TCP 55322 für den Benutzer `wamo-release`, sonst verworfen), die der Dienst per `Requires` verlangt. `release-einrichten.sh` und `release-rueckweg.sh` erweitert, Rauchtest um Netzproben als `wamo-release` ergänzt. | Der Dienst darf kein anderes Ziel erreichen als Supavisor, auch wenn der Code kompromittiert wäre; zwei unabhängige Schichten (systemd und nft). Ausgerollt am 02.11.2026 auf Sokrates-2: `wamo-release status` meldet die Datenbank als angeschlossen, der Rauchtest besteht mit 53 von 53 Proben (Netzproben: SSH, Router und Internet gesperrt, Supavisor erreichbar). Befund beim Rollout: Docker setzt für den veröffentlichten Port 55322 ein DNAT auf `172.23.0.5:5432`, bevor die Filterkette läuft; die nft-Regel prüft deshalb das ursprüngliche Ziel (`ct original`), und `IPAddressAllow` nimmt das Netz `supabase-wamo_default` (172.23.0.0/16) auf, weil der Filter von systemd erst die umgesetzte Adresse sieht. |
| 02.11.2026 | 1.4b | Der Prozess des Freigabe-Dienstes ist gebaut (`infra/sokrates2/anker/freigabe_dienst.py`): HTTP über Unix-Socket, genau `POST /wamo/release/<id>/freigeben`, Gegenstelle per `SO_PEERCRED` (anfangs nur root), Schlüsselsatz von GoTrue über HTTPS mit der eigenen CA (gepuffert, höchstens ein Abruf je 30 Sekunden bei unbekannter `kid`), Datenbank als `wamo_anker_s2`, Selbstprüfung der Prüfsummen vor jedem Start. Eigene Unit `wamo-freigabe.service` (Benutzer `wamo-anker`, Netz nur Supavisor 55322 und GoTrue 55443 über `IPAddressAllow` und die nft-Tabelle `wamo_freigabe_ausgang` mit `ct original`), Einrichtung `freigabe-einrichten.sh` mit hash-gesicherten Bibliotheken (PyJWT, cryptography, psycopg). Gegenüber dem Konzept liegt der Socket in `/run/wamo-freigabe/` (dem Benutzer gehörender Laufordner) statt in `/run/wamo-anker/`, und der Dienst bekommt eine Kopie der Zugangsdatei in `/etc/wamo-freigabe/`, damit am Ordner des Ankers nichts geändert wird. Offen: 1.4c, die Caddy-Route und der Test gegen ein echtes GoTrue-Token (zu belegen: `totp` in `amr` wird beim Token-Refresh nicht erneuert). | Der Prozess ist ohne Netz und Datenbank vollständig prüfbar (`test_freigabe_dienst.py`, echter Socket unter Linux); die Route zur Oberfläche ändert Caddy und gehört in einen eigenen, bewusst freigegebenen Schritt. |
| 02.11.2026 | 1.2a | Die deterministischen Prüfstufen sind gebaut (`infra/sokrates2/release/pruefer/pruefer_scans.py`, Standardbibliothek): Secret-Scan, Fremdverweis-Grep, Wirkungsflächen und Abhängigkeiten (Lockfile-Diff für `uv.lock` und `package-lock.json`). Sie prüfen nur hinzugefügte Zeilen und Dateinamen und nennen nie den gefundenen Text (Pfad, Zeile, Art). Sie laufen später im Prozess der Prüfer-Sandbox, nicht im Release-Dienst. Auslegungen: Ein Geheimnisname gilt als "im eigenen Modul", wenn die Datei ihn vor der Änderung schon enthielt, jeder neue Verweis in einer anderen Datei macht die Änderung zur Sonderfreigabe. Platzhalter trennt die Stufe nach Form, nicht nach einer Markierung im Code (die könnte der Agent selbst setzen): Anthropic-Schlüssel zählen erst ab 80 Zeichen (echte haben über 100, die Testwerte im Repo unter 60), ein privater Schlüssel nur mit zusammenhängendem Base64-Körper von mindestens 100 Zeichen, Passwort-Zuweisungen in Testdateien werden nur als Hinweis angezeigt. Probe über den ganzen Bestand (602 Dateien): Secret-Scan 0 Treffer, 10 Hinweise. Offen in 1.2: Sandbox-Läufer (bubblewrap, Benutzer `wamo-pruefer`, Proxys, nft), die Werkzeuge Bauen, Tests, Typen und Lint, `npm audit` und `pip-audit`. | Die Scanner sind reine Textfunktionen und ohne Server vollständig prüfbar (`test_pruefer_scans.py`, 39 Tests). Ein Fund beim Bestandslauf: kaputte Adressen wie `http://[::1` ließen `urlsplit` abstürzen und sind jetzt ein Treffer statt eines Absturzes. |
| 02.11.2026 | 1.2b | Proxy und Läufer der Prüfer-Sandbox sind gebaut (`infra/sokrates2/release/pruefer/pruefer_proxy.py` und `pruefer_lauf.py`, nur Standardbibliothek). Proxy: nur `CONNECT` auf Port 443, Name wörtlich in der Liste (keine IP-Adresse, kein Platzhalter), Auflösung im Proxy mit Prüfung jeder Adresse auf "öffentlich" (Schutz gegen DNS-Rebinding), Grenzen für Kopf, Leerlauf, Zeit, Menge und gleichzeitige Verbindungen, lauscht nur auf Loopback, Protokoll ohne frei gewählte Namen. Läufer: vergleicht Basis und neuen Stand (Prüfsumme auch für Binär- und Großdateien, Links werden gemeldet und sind beim Bauen ein Fehler), führt Bauen, Tests, Typen und Lint über einen austauschbaren Ausführer aus (feste Befehle ohne Shell, Umgebung nur mit Platzhaltern und dem Paket-Proxy), die Scanner aus 1.2a und den Audit bei Lockfile-Änderung, und schreibt den Prüfstand als JSON in der Form, die `release_automat` verlangt. Die Zeilen `schutzschicht` (Anker) und `version_bytegleich` (Git) liefert er nie, `migrations_lint` nur als Fehler, sobald eine Migration geändert wurde, bis es das Lint (2.5) gibt. Offen in 1.2: Einrichtung auf Sokrates-2 (Benutzer `wamo-pruefer`, Werkzeuge Node, uv und ruff mit festen Fassungen, bubblewrap-Aufruf, Units der Proxys, nft-Regel, Rauchtest mit Absicht kaputtem Code und gesperrten Zielen). | Beides ist ohne Server vollständig prüfbar (90 Tests, unter Linux mit echten Prozessen). Funde dabei: Ein frei gewählter Zielname hätte im Proxy-Protokoll gestanden, ein Link auf einen Ordner blieb unbemerkt, geänderte Binärdateien blieben unbemerkt. Alle behoben. |
| 02.11.2026 | 1.2c | Die Prüfer-Sandbox ist auf Sokrates-2 eingerichtet und abgenommen (`infra/sokrates2/pruefer-einrichten.sh`, `pruefer-rueckweg.sh`, `pruefer/*`, `pruefer_rauchtest.py`). Benutzer `wamo-pruefer` (fremder Code) und `wamo-pruefer-proxy` (Proxy), alles unter `/opt/wamo-pruefer` (Programme mit SHA256SUMS, Werkzeuge: Node 22.23.4 mit fester Prüfsumme, uv, ruff und pip-audit nur mit Prüfsummen aus `pruefer/requirements-werkzeuge.lock`), Arbeitsordner `/srv/wamo-pruefer/lauf` (je Lauf frisch, danach gelöscht). **Netz anders als in D.3 geplant:** Die Sandbox läuft in einem eigenen Netz (bwrap `--unshare-net`) und sieht nichts vom Netz des Servers, auch nicht dessen Loopback (Datenbank, SSH, Dienste). Ein Loopback-Port als Zugang zum Proxy hätte nicht gereicht, denn Turbopack verbindet sich beim Bauen mit einem eigenen Node-Prozess über 127.0.0.1. Zum Internet führt nur ein Unix-Socket zum Proxy `pakete`; die Brücke `pruefer_bruecke.py` bietet ihn in der Sandbox als 127.0.0.1:18080 an. Die nft-Tabelle `wamo_pruefer_ausgang` ist nur noch der Rückfall (Sandbox-Benutzer ohne Sandbox: alles abgewiesen; Proxy-Benutzer: keine privaten Netze). bubblewrap läuft über eine eigene Kopie mit AppArmor-Profil (das System-bwrap bleibt gesperrt), Grenzen über systemd-run (Speicher 24 GB, kein Swap, Aufgaben, CPU, Zeit). **Abweichungen von D.3, bewusst:** (1) Die Domainliste des Proxys enthält zusätzlich `fonts.googleapis.com` und `fonts.gstatic.com`, weil die Oberfläche Roboto Condensed beim Bauen über `next/font/google` holt (inzwischen zurückgenommen: siehe die Zeile "Schrift selbst ausgeliefert"). (2) `host` und `desktop` sind Windows-Anwendungen, ihre Tests laufen unter Linux nicht (cmd, C:-Pfade, Fenstersteuerung). Sie werden gebaut und geprüft (Audit), aber nicht getestet; ändert ein Stand etwas darin, setzt der Läufer `windows_pruefung_noetig` und die Freigabe darf das nicht übergehen. (3) `pip-audit` kennt keine Schweregrade: Jede bekannte Lücke macht die Stufe rot (strenger als "hoch oder kritisch"). (4) Der Arbeitsordner heißt in der Sandbox `wamo`, weil der Selbstschutz des Wächters das Repo am Namen erkennt. Abnahme: Rauchtest 86 von 86 (Installation, Proxy mit 16 Abweisungsfällen, Sandbox-Sonde mit Netz-, Datei- und Umgebungsproben, Zeitlimit ohne Reste, fünf Läufe mit einem kleinen Projekt: grün, kaputter Test, untergeschobener Schlüssel, Lockfile mit Audit samt bekannter Lücke, Link im Stand). Auf dem echten WAMO-Stand besteht der Läufer alle acht Stufen mit Exit 0 in 198 Sekunden (uv sync, npm ci, `next build`, 2751 Python-Tests, 283 UI-Tests, tsc, Lint). Die damals offenen Punkte (ruff-Vergleich, Aufruf, Mustervorlage, Claude-Proxy) stehen erledigt unter 1.2d. | Der Rauchtest hat vier Fehler im Entwurf aufgedeckt: `/etc` war in der Sandbox beschreibbar (jetzt `--remount-ro /`), `pip-audit` war als Projektabhängigkeit angesetzt statt als festes Werkzeug, `onnxruntime` brach ohne `/sys/devices/system/cpu` ab, und das Loopback-Konzept scheiterte an Turbopack. Der echte Lauf deckte sieben Linux-Fehler in Tests auf, die bisher nur unter Windows liefen (Altlasten, jetzt behoben: Parameter `db` im Test des Release-Sockets, überschriebene 0400-Datei, Verbindungsabbruch statt leerer Antwort, Windows-Pfade, Sticky-Bit mit Gruppe). Außerdem: `release-rueckweg.sh` hatte ein wörtliches Zeilenumbruch-Zeichen mitten im Befehl (Heredoc-Überbleibsel) und hätte die Ausgangsregel nicht abgeschaltet (behoben, per Test abgesichert). |
| 02.11.2026 | 1.2d | Schritt 1.2 ist abgeschlossen. Die vier offenen Punkte sind erledigt. (1) **ruff** gehört zur Stufe `typen_lint` (Regeln F, B, S, E7, E9, ohne Projektkonfiguration): Basis und neuer Stand werden verglichen, und der Stand fällt durch, sobald die Zahl der Funde je Datei und Regel steigt. S101 (assert) gilt nie als Fund, die übrigen S-Regeln nicht in Testdateien. Im Ergebnis stehen nur Datei, Regel und Menge, nie der Wortlaut. (2) Die **Mustervorlage** des Fremdverweis-Greps liegt root-eigen unter `/etc/wamo-anker/fremdverweise.txt` (nie im Repo); der Läufer nimmt sie ohne `--fremdverweise` als Standard und bricht ab, wenn sie fehlt oder nicht root gehört. (3) Der **Claude-Proxy** läuft als zweite Instanz der Proxy-Unit (`wamo-pruefer-proxy@claude`, 127.0.0.1:18081, nur `api.anthropic.com`) und wird von der Sandbox der Prüfstrecke nie eingebunden ("Code ausführen und Token halten nie im selben Prozess"). Die Claude-Stufe selbst (Review und Refactor) bleibt Schritt 3.3. (4) **Aufruf:** Entscheidung Niko 02.11.2026: Der Anker bleibt unverändert, der Release-Dienst erreicht die Sandbox über einen eigenen kleinen **Annahme-Dienst** (`wamo-pruefer-annahme.socket`, Socket 0600 nur für `wamo-release`, je Verbindung ein Root-Prozess `pruefer_annahme.py`). Root liest nie einen Ordner, den ein Dienst ohne Root ändern kann: Der Client `pruefer_client.py` schickt Basis und neuen Stand als Datenstrom (Format in `pruefer_archiv.py`: Pfade streng geprüft, nichts unter Datei oder Link, keine Doppelten, Grenzen für Größe und Zahl), und root schreibt ihn selbst in einen frischen Ordner (jede Komponente `O_NOFOLLOW` über Verzeichnis-Deskriptoren, jede Datei `O_EXCL`). Verknüpfungen kommen als ungültige Links an, damit der Läufer sie weiter meldet. Der Dienst startet nur den festen Läufer, der Client wählt allein die Runde. Antwort ist eine JSON-Zeile mit dem Ergebnis des Läufers oder einem kurzen Grund (benutzer, kopf, archiv_ungueltig, belegt, zeitlimit, lauf_ohne_ergebnis). Der Anschluss an die Stufen des Release-Dienstes (`pruefen` in `release_stufen.py`) folgt mit Schritt 1.3 und 3.2, wo die Stände entstehen. | Abnahme: Rauchtest 132 von 132 (Teil F: Socket nur für wamo-release, 13 Missbrauchsfälle im Archiv, drei volle Läufe als wamo-release über den Socket, zweite Anfrage während eines Laufs wird mit "belegt" abgewiesen, keine Reste). Auf dem echten WAMO-Stand besteht der Weg als wamo-release über den Annahme-Dienst alle acht Stufen (Exit 0, 197 s, mit der echten Mustervorlage und dem ruff-Vergleich). Funde dabei: `open()` auf eine Pipe im Stand blockierte den Client endlos (jetzt Prüfung vor dem Öffnen und `O_NONBLOCK`), ein abgewiesener Antrag ließ die Dienstinstanz als "failed" in systemd stehen (jetzt Exit 0 nach jeder Antwort und `CollectMode`), und ein Signaltest in `test_sokrates2_dispatch.py` war zeitabhängig (jetzt mit echter Wartezeit). |
| 02.11.2026 | 1.3 | Die Quelle des Release-Dienstes ist gebaut, **nur lesend** (Entscheidung 02.11.2026: kein Schreiben nach GitHub, nie auf main, Merge von Hand, bis Niko die zweite GitHub-App angelegt hat). `release/quelle.py`: (1) **Spiegel**, ein bare-Repository, das nur `refs/heads/main` holt, mit gehärteten Git-Aufrufen (keine Hooks, keine Benutzer- oder Systemkonfiguration, nur https, Prüfung aller Objekte, nichts aus der Umgebung des Dienstes, Fehlertexte von git werden nie weitergegeben). (2) **GitHubApp, nur lesend:** JWT (RS256, signiert mit dem festen Programm openssl), Installations-Token mit nur `contents:read` und `metadata:read` für ein Repository; die Antwort muss genau das bestätigen (mehr Rechte, ein anderes oder ein zweites Repository werden abgelehnt); das Token geht nur über die Umgebung an git (nie über Argumente) und wird nach dem Holen widerrufen, auch nach einem Fehler. (3) **Archiv aus dem Objektspeicher:** Ein Commit wird direkt aus `git ls-tree` und `git cat-file` im Archivformat des Annahme-Dienstes geschrieben, ohne Arbeitsordner und ohne Git-Attribute (`export-ignore` und `export-subst` würden den Inhalt verändern), Verknüpfungen kommen als Link, Submodule und unzulässige Pfade sind ein Fehler, Ordner wie `node_modules` fehlen wie im Läufer. `release/stufen_quelle.py`: **QuellenStufen.pruefen** ist die erste echte Stufe des Release-Dienstes. Runde 1 prüft `c1_commit`, Runde 2 prüft `r_commit` (oder `c1_commit`, wenn der Refactor verworfen wurde), jeweils gegen `basis_commit`; der Weg führt über `pruefer_client.pruefen_mit` und den Annahme-Dienst in die Sandbox. Meldet der Läufer `windows_pruefung_noetig`, kommt die sichtbare Zeile `windows_tests` mit `uebersprungen` dazu. Alle anderen Stufen bleiben `NichtVerfuegbar`; im Betrieb ist die neue Stufe noch nicht eingeschaltet. **Bewusst nicht gebaut** (kommt mit der zweiten App): Branch `release/vX.Y.Z`, Tag, Release und Fast-Forward von `main`, das Schreibtoken, der Rauchtest `--app-echt`; ein Test sichert ab, dass im Modul kein Schreibweg nach GitHub steht (kein PUT, PATCH, Push, Merge, Tag, Release). Runde 3 gibt es noch nicht, sie braucht die Klassifizierung des Ankers (klasse, treffer_anzahl). | Abnahme: über 40 Tests (echte lokale Git-Repositories und eine Attrappe der GitHub-API mit einem echten, im Test erzeugten RSA-Schlüssel, dessen Signatur mit openssl nachgeprüft wird). Rauchtest auf Sokrates-2 um Teil G erweitert (Spiegel aus einem lokalen Repository, Archiv aus dem Objektspeicher, die Stufe `pruefen` für eine gute und eine kaputte Änderung durch den echten Annahme-Dienst und die Sandbox): 140 von 140. Die Release-Installation wurde um `quelle.py`, `stufen_quelle.py`, `pruefer_archiv.py` und `pruefer_client.py` ergänzt (Rauchtest des Release-Dienstes weiter 53 von 53). Für den späteren echten Betrieb fehlt dem Release-Dienst noch der Netzweg zu GitHub (nft-Regel und `IPAddressAllow` für einen Proxy auf Loopback, Proxy-Instanz `github` mit `github.com` und `api.github.com`); das richtet sich ein, sobald die App da ist. |
| 02.11.2026 | 1.4c | Die **Caddy-Route** zum Freigabe-Dienst ist live, und der Dienst ist mit einem **echten GoTrue-Token** getestet (Freigabe Niko 02.11.2026). Route: `POST` und `OPTIONS` auf `/wamo/release/<hex>/freigeben` gehen auf 55444 am Kern vorbei an `/run/wamo-freigabe/freigabe.sock`; alle anderen Pfade und Methoden gehen wie bisher an den Kern (in `handle` gefasst). Caddy prüft nichts vom Token, das tut allein der Dienst. Die Antwort darf nur die Oberfläche lesen: CORS-Recht (`Access-Control-Allow-Origin` mit dem Ursprung, kein Stern, keine Cookies) für die drei festen Ursprünge auf 55445; Preflight beantwortet Caddy selbst (204); Körper höchstens 4 KB. **Zugang:** Der Container läuft als 1000:1000, also gehört `wamocon` (1000) zu den erlaubten Peers (`WAMO_FREIGABE_PEERS=0,1000`), und der Socketordner ist `2750 wamo-anker:wamocon` (Gruppe `wamocon`, Setgid vererbt sie an den Socket, Modus 0660). Der Ordner wird wie beim Kern von `tmpfiles.d` angelegt und nicht mehr per `RuntimeDirectory`, sonst bände Docker nach jedem Neustart des Dienstes einen alten Ordner ein; die Unit bekommt dafür `ReadWritePaths=/run/wamo-freigabe`. Der Dienst selbst läuft weiter ohne Zusatzgruppe und ohne Rechte. **Ausgerollt:** `freigabe-einrichten.sh`, dann `wamo-core-caddy.sh` (prüft die Konfiguration in einem Wegwerf-Container, legt `wamo-core.caddy.vorher` als Sicherung an, legt den Container neu an; 55443 bis 55446 waren wenige Sekunden unterbrochen, vorher geprüft: kein Sitzungsbetrieb, keine offenen Verbindungen). Rückweg nur für die Route: `wamo-core-caddy.sh --route-zurueck`. **Nebenbei:** Auf dem Server lag eine ältere Site als im Repo (ohne die 26-MiB-Grenze für geteilte Dateien); mit dieser Einrichtung gilt auch sie. | Test mit echtem Token (`freigabe_tokentest.py`, als `wamocon`, Wegwerf-Konto ohne WAMO-Profil, Konto danach gelöscht): 22 Proben am Socket und 30 mit der Route durch Caddy. Belegt: Ein echtes aal2-Token (ES256, Aussteller, Zielgruppe, Ablauf, frischer totp-Eintrag) wird akzeptiert (erst die Berechtigung lehnt ab, weil das Konto kein Profil hat), aal1 wird mit `aal2_fehlt` abgewiesen, ein verfälschtes Token mit `token_ungueltig`, nach über 60 Sekunden gibt es `aal2_alt`, und ein neuer Code macht das Token wieder frisch. **Die offene Frage aus 1.4b ist beantwortet:** Ein Token-Refresh erneuert den Zeitstempel des totp-Eintrags in `amr` nicht (`aal2` bleibt, der Zeitstempel bleibt), ein per Refresh erneuertes Token wird nach 60 Sekunden ebenfalls mit `aal2_alt` abgewiesen. Die Regel "jüngster totp-Eintrag höchstens 60 Sekunden alt" ist damit belegt und nicht aushebelbar. Rauchtest des Freigabe-Dienstes weiter grün, 36 Tests mehr für Unit, Caddy-Site, Compose und Skripte. Noch nicht gebaut: der Aufruf von der Oberfläche (Schritt 1.5, die Karte "Änderungen") und die Route für die übrigen Befehle des Release-Dienstes (`api.sock`, kommt mit 1.5). |
| 02.11.2026 | Schrift | **Schrift selbst ausgeliefert** (Auftrag Niko 02.11.2026). Die Oberfläche lädt Roboto Condensed nicht mehr über `next/font/google`, sondern über `next/font/local` aus dem Repo: `ui/src/app/fonts/` enthält die Schnitte 400 und 700 (Teilmenge latin, enthält ä, ö, ü, ß) als WOFF2, die Lizenz (SIL OFL 1.1) und `HERKUNFT.md` mit Quelle (`@fontsource/roboto-condensed` 5.3.0) und Prüfsummen. Die CSS-Variable `--font-condensed` und die Schriftreihenfolge (Arial Narrow, Liberation Sans Narrow, Roboto Condensed) bleiben. Damit holen Bau und Betrieb nichts mehr bei Google, und die zwei Hosts `fonts.googleapis.com` und `fonts.gstatic.com` sind wieder aus der Domainliste des Paketproxys der Prüfer-Sandbox gestrichen (zurück auf die fünf Namen des Konzepts). | Neuer Test `ui/src/lib/schrift.test.ts` (Prüfsummen, Lizenz, Herkunft, `layout.tsx` nutzt `next/font/local`, im ganzen Quelltext kein Verweis auf Google-Schriften); `next build` läuft lokal durch. |

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
