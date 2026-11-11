# Entwicklungsaufträge im Betrieb auf Sokrates-2

Planpapier, Stand 10.11.2026, Revision 2 nach der Gegenprüfung durch drei Prüfer (Sicherheit, Umsetzbarkeit, Betrieb), Basis `origin/main` 2a90999.
Grundlage ist Nikos Freigabe vom 10.11.2026 („ausarbeiten und umsetzen“, „aufräumen“). Sokrates-2 war nicht erreichbar, was nur der Server zeigt,
steht in Abschnitt 8. Kennzeichnung: **B** belegt (Datei:Zeile im Arbeitsbaum), **A** abgeleitet, **V** vermutet. Pfade gelten relativ zur
Repo-Wurzel, Bezeichner stehen in ASCII. Die Arbeitspakete stehen in `docs/entwicklung-im-betrieb-arbeitsliste.md`. Schritte, die nur Niko ausführen
kann, sind mit **Niko, manuell** gekennzeichnet.

**Kurzfassung.** Der Kern im Betrieb ruft den Dispatcher weiter über `ssh` auf die eigene LAN-Adresse auf, mit einem eigenen Schlüssel in einer
eigenen Datei. Neu ist die Zuordnung des Kanals: Der Dispatcher erkennt den Kern-Kanal am benutzten Schlüssel (Fingerabdruck aus `SSH_USER_AUTH`),
nicht an der Quelladresse. Sobald ein Kern-Schlüssel existiert, gilt der strikte Modus: Jede Verbindung muss eindeutig einem Kanal zugeordnet
werden, sonst Exit 2. Im Kern-Kanal sind Art, Repo, Dienste, Befehle, Projektnamen, Zugang (nur `zugang=folgt`), Läufe je Zeit und die Kosten je
Lauf (`--max-turns`, `--max-budget-usd`) hart begrenzt, aus der root-eigenen Datei `/etc/wamo/kernkanal.conf`. Ein Notaus per Sperrdatei wirkt ohne
Neustart des Kerns. Der Schalter im Kern heißt `WAMO_ENTWICKLUNG_BUERO`, steht in einem eigenen Drop-in der Slot-Vorlage und wird nur von Releases
gelesen, die die Büro-Schranken kennen. Die Vorprüfung des Kerns bleibt unverändert, deshalb kann kein Entwicklungsschalter den Start des Sprachkerns
verhindern. Acht Stufen (0, 1, 2a, 2b, 3a, 3b, 4, 5) führen von „ohne Wirkung“ bis zu echten Repos, die geschlossenen Stufen 0 bis 2a laufen ohne Niko.

**IQ:** Die harte Grenze sitzt dort, wo ein gekaperter Kern nichts ändern kann (Dispatcher, root-eigene Dateien), und sie gilt fail-closed.
**EQ:** Niko hört nach jedem Klick, ob und warum ein Auftrag startet, ein Einschalten trennt niemanden, der Notaus wirkt in Sekunden.
**VQ:** Ein Ablaufbild, Tabellen mit immer gleichen Spalten, eine Stufenfolge zum Abhaken. **Synthese:** Zuerst Schranken und Rückwege ohne
Wirkung, dann ein Durchstich bei geschlossenem Kanal, dann Recherche, lesend, schreibend im Testrepo, zuletzt echte Repos.

**Was sich gegenüber Revision 1 ändert**

1. Der Kanal hängt am Schlüssel, nicht an der Quelladresse, und der strikte Modus ist fail-closed (E1, 4.1).
2. Der Kern-Kanal verlangt `zugang=folgt` mit Profil und zweiter Zeile, ein fehlendes Feld ist gesperrt (E4).
3. Der Schalter heißt `WAMO_ENTWICKLUNG_BUERO`, `wamo-core-vorpruefung.sh` bleibt unverändert, ältere Releases und Rückwege starten mit Entwicklung aus (E2).
4. Kern-Aufträge arbeiten im Namensraum `kern-`, der PC-Kanal darf ihn nicht öffnen (E4).
5. Der Kern-Kanal hat eine Befehlsliste statt einer Sperrliste, `pruefen` ist gesperrt (E4).
6. Kosten- und Wendungsgrenzen sitzen im Dispatcher, der Kern zählt die Gesamtkosten über alle Runden (E6).
7. Der Notaus ist eine Sperrdatei mit festgelegter Reihenfolge und zweiter Suche (E7).
8. Der Ablauf der Schlüsselzeile steht in `kernkanal.conf`, nicht in `expiry-time` (E8).
9. Neue Stufen 3a (Recherche ohne GitHub) und 3b (lesend), Token-Löschen erst vor 3b, Stufe 5 nur mit Branch-Schutz oder Veröffentlichung durch den Dispatcher (5).
10. Der Server-Ist-Stand des Dispatchers ist unbekannt (Version 6 gilt laut README als nicht ausgerollt), Rückwege, Dateilisten und Aufwand sind angepasst.

## 1. Ziel und Nicht-Ziele

**Ziel.** Niko (Admin, zweiter Faktor aal2) startet im Büro-Betrieb Entwicklungsaufträge per Sprache und Karte. Sie laufen in der vorhandenen
Sandbox auf Sokrates-2, mit harten Grenzen, hörbarer Rückmeldung zu jedem Klick und einem Notaus. Das Einschalten darf weder den Sprachkern noch
den PC-Weg stören und braucht keine Downtime.

**Erfolgsmaße.** (1) Ein Auftrag aus einem anderen als dem erlaubten Kanal, Profil, Repo oder Zugang startet nicht und erzeugt kein Token. (2) Jeder
Klick bekommt eine Quittung. (3) Der Notaus beendet laufende Kern-Aufträge und schließt den Kanal in unter einer Minute, ohne Neustart. (4) Ein
Fehler am Schalter oder an den Dateien schaltet nur die Entwicklung aus, nie den Kern.

**Nicht-Ziele**

- Selbständerung: `SELF_REPO = "wamo"` bleibt (voice-core/dev_guard.py:66, infra/sokrates2/wamo_job_dispatch.py:123).
- Aufträge über das Ende der Sitzung hinaus (eigener Auftragsdienst, Folgestufe F3).
- Mitglieder: nur Admins, im Büro nur das Profil aus `WAMO_ENTWICKLUNG_PROFILE`.
- Befehlskriterien der Abnahme (`pruefen`) im Betrieb: Der Kern-Kanal sperrt den Befehl, bis eigens entschieden wird.
- Supabase-Zugang in der Sandbox, damit keine Arten `datenbank` und `deployment`. Keine neuen Repos anlegen.
- Änderungen an der Unit-Härtung, an der Firewall, am globalen sshd und an `wamo-core-slots.sh` (der Anker prüft dessen Prüfsumme,
  infra/sokrates2/anker/wamo_anker.py:1015) sowie an `wamo-core-vorpruefung.sh`.
- Änderungen am Verhalten des PC-Kanals, mit genau drei Ausnahmen: `ExposeAuthInfo yes` im Match-Block, im strikten Modus eine eindeutige
  Zuordnung des Schlüssels, und der Namensraum `kern-` ist dem PC-Kanal verschlossen.
- Unix-Socket zum Dispatcher (Abschnitt 3, Folgestufe F4) und Veröffentlichung durch den Dispatcher (Folgestufe F1).

## 2. Entscheidungen

**E1 Weg: `ssh` bleibt, der Kanal hängt am Schlüssel.** Der Kern ruft den Dispatcher wie der PC-Kern per `ssh -F none` auf `wamo@192.168.178.136` auf
(voice-core/dev_agent.py:327-351), mit eigenem Schlüssel in eigener Datei `/etc/ssh/authorized_keys.d/wamo-kern`. Der Dispatcher ordnet den Kanal nach
dem benutzten Schlüssel zu (Regeln in 4.1). Ein Unix-Socket mit Peer-Prüfung (`SO_PEERCRED`) ist nach Bewertung (Tabelle in Abschnitt 3) die
sauberere Endlösung, wird aber in diesem Durchgang nicht gebaut: Er verlegt den Dispatcher in einen neuen Ausführungskontext (Dienst statt
sshd-Sitzung) und braucht einen neuen Client im Kern, neue Units und eine neue Lebenslinie. Das ist ein Umbau mit Ausfallrisiko für den PC-Weg
und für den Sandbox-Start, der kleinste sichere Schritt ist der vorhandene Weg mit einer harten Kanalbindung (Folgestufe F4).

**E2 Schalter `WAMO_ENTWICKLUNG_BUERO` als eigenes Drop-in der Slot-Vorlage.** Die Datei
`/etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf` (root:root 0644, ohne Spiegel-Marke) setzt `WAMO_ENTWICKLUNG_BUERO=an`,
`WAMO_ENTWICKLUNG_ORT=sokrates2`, `WAMO_ENTWICKLUNG_PROFILE=<user_id>`, `WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD=3` und
`WAMO_ENTWICKLUNG_KOSTEN_TAG_USD=10`. Die Units behalten `WAMO_ENTWICKLUNG=aus` (infra/sokrates2/wamo-core@.service:30).

- **Neuer Name, Vorprüfung unverändert (schließt Einwand c).** Nur Releases mit den Büro-Schranken lesen `WAMO_ENTWICKLUNG_BUERO`. Ältere
  Releases, die alte Unit, ein Anker-Rückweg (`rueckweg-zu` startet das Release der letzten guten Fassung, infra/sokrates2/wamo-core-slots.sh:339-360)
  und jede alte Vorprüfung sehen weiter `WAMO_ENTWICKLUNG=aus` und starten ohne Entwicklung. `wamo-core-vorpruefung.sh` (Zeile 25 verlangt `aus`)
  bleibt byte-gleich, deshalb kann kein Entwicklungsschalter, keine Datei und kein Neustart des Servers den Start des Sprachkerns verhindern
  (Restart=on-failure und Reboot nutzen dieselbe Vorprüfung, infra/sokrates2/wamo-core@.service:40,43).
- **Wo die Sicherheitsprüfung stattdessen sitzt.** (1) `schalter an` prüft als root vorab streng: Schlüssel, known_hosts, Rauchtest, Release-Nachweis
  (`SCHRANKEN_VERSION` in `voice-core/entwicklung_betrieb.py` des Zielrelease), kein `WAMO_ENTWICKLUNG*` in core.env (über `wamo-core-env liste`, nur
  Namen), keine gleichnamige Datei in `wamo-core.service.d`. (2) Der Kern prüft beim Aufbau jeder Sitzung weich: Ein Fehler schaltet nur die
  Entwicklung aus und schreibt eine Logzeile mit Grund (voice-core/bot.py:540-542 ist dieser Weg schon). (3) Jeder Auftragsstart prüft Lesbarkeit
  von Schlüssel und known_hosts erneut und antwortet mit Quittung. (4) Der Dispatcher prüft bei jedem Start den Kanal. Nicht-blockierender
  Warnweg: `status` meldet jede Abweichung, auch die der laufenden Prozesse (`/proc/<MainPID>/environ`, nur Namen und Schalterwert).
- **Nur `wamo-job-kern-einrichten.sh schalter an|aus <release>` schreibt das Drop-in**, danach `daemon-reload` und `wamo-core-slots.sh umschalten-kern <release>`.
  Scheitert das vor dem Umschalten, stellt das Skript das vorige Drop-in wieder her. Wurde schon umgeschaltet (Abfrage über `aktiver_slot`), bleibt das
  Drop-in und das Skript meldet „teilweise“. `systemctl restart` kommt nicht vor. Ein zweiter Aufruf mit gleichem Stand ist ein No-op.
- **wamo_core_env.py:** `ERLAUBT` bleibt ohne `WAMO_ENTWICKLUNG*` (sonst hätte `EnvironmentFile` Vorrang vor dem Drop-in, und der Schalter wäre vom PC aus
  setzbar). Nur Docstring (16-17) und Kommentar (59-61) werden korrigiert.
- **Befehlskriterien:** `WAMO_ABNAHME_BEFEHLE` bleibt in core.env stehen und wird im Büro nur wirksam, wenn zusätzlich `WAMO_ENTWICKLUNG_BEFEHLE_BUERO=an`
  gesetzt ist (Standard aus). Damit überlebt der Zustand jeden `geheimnisse`-Lauf (scripts/wamo_kern_sokrates2.py:74,184-198), und der Dispatcher sperrt `pruefen` im Kern-Kanal ohnehin.

**E3 Dateien und Rechte**

| Pfad | Besitzer, Modus | Inhalt |
| --- | --- | --- |
| `/var/lib/wamo-core/.ssh` | wamo-core 0700 | im StateDirectory, beide Slots teilen ihn |
| `.../id_ed25519_wamo_jobs` | wamo-core 0600 | per `runuser -u wamo-core -- ssh-keygen -t ed25519 -N '' -C wamo-kern-jobs`, verlässt den Server nie |
| `.../known_hosts_wamo_jobs` | wamo-core 0644 | eine Zeile aus `/etc/ssh/ssh_host_ed25519_key.pub`, Fingerabdruck gegen scripts/wamo-tunnel-einrichten.ps1:44 geprüft |
| `/etc/ssh/authorized_keys.d/wamo-kern` | root:root 0644 | `restrict,from="192.168.178.136",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 <öffentlich> wamo-kern-jobs`, ohne `expiry-time` |
| `/etc/ssh/sshd_config.d/wamo.conf` | root:root 0644 | im Block `Match User wamo`: zweite `AuthorizedKeysFile` (`.../%u-kern`) und `ExposeAuthInfo yes` |
| `/etc/wamo/kernkanal.conf` | root:wamo 0640 | Regeln des Kern-Kanals (4.1), Schreibweise `SCHLUESSEL=wert` |
| `/etc/wamo/kernkanal.notaus` | root:wamo 0640 | Sperrdatei, Existenz heißt Notaus (4.4) |
| `.../wamo-core@.service.d/wamo-entwicklung-buero.conf` | root:root 0644 | Schalter (E2) |
| `/opt/wamo/waechter/wamo_job_kernkanal.py` | root:wamo 0640 | Kern-Kanal-Modul des Dispatchers, installiert durch `wamo-job-einrichten.sh` |
| `/opt/wamo-core/bin/wamo-job-kern-rauchtest` | root:root 0755 | Rauchtest, installiert durch `schluessel` aus `~/wamo-infra` nach `pruefe_herkunft` |
| `/var/backups/wamo-job-kern/<Zeit>/` | root:root 0700 | Sicherungen, auch das vorige `wamo.conf` für den Totmannschalter |
| `/var/log/wamo-job-kern/aktionen.jsonl` | root:root 0640 | jede Bedienhandlung als Zeile ohne Werte, dazu `logger -t wamo-job-kern` |

Der öffentliche Kern-Schlüssel liegt nie in `~/wamo-infra`: `wamo-job-einrichten.sh` greift jede Datei `*wamo_jobs*.pub` auf und trüge sie mit
`from=` auf das ganze /24 in die PC-Datei ein (infra/sokrates2/wamo-job-einrichten.sh:57-69,265-282). `wamo-core` kommt nie in die Gruppe `wamo`.

**E4 Kern-Kanal im Dispatcher (Version 7).** Die Regeln stehen in 4.1 und 4.2. **E5 Zugänge** in 4.2. **E6 Schranken und Grenzen** in 4.3.
**E7 Notaus** in 4.4. **E8 Ablauf** in 4.5.

**E9 Audit über `actions`, ohne Migration.** Je Auftrag eine Zeile `entwicklungsauftrag` nach dem Automaten (supabase/migrations/0001_wamo_init.sql:285-300):
`pending` und `approved` beim Klick, `running` bei Übergabe an ssh, `done` oder `failed` mit `result` (Job, Art, Projekt, Repo, Status, Dauer, Kosten,
Runden, nie ein Zugangswert). Ein abgewiesener Start geht über `running` nach `failed`, weil `approved` nach `failed` nicht erlaubt ist. `wamo_core` darf
`status`, `result`, `finished_at` ändern (:343-346), eine Migration entfällt. Zeilen, die nach einem Kern-Absturz auf `running` stehen, schließt der
nächste Start desselben Profils nach 2 Stunden als `failed` („abgelaufen“), und die Tageskosten zählen jede solche Zeile mit der Auftragsgrenze.
Abgrenzung: `vorgaenge` (Migration 0017) ist die Sicht für Niko im Gespräch, `actions` ist Audit und Kostenquelle. Bedienhandlungen am Server
(`schluessel`, `kanal`, `schalter`, `notaus`) stehen in `aktionen.jsonl` und im Journal.

**E10 Schreibende Aufträge und Branch-Schutz.** Ein Lauf ohne Schreib-Token mit anschließendem Push und Pull Request durch den Dispatcher ginge
nur mit großem Umbau: neuer Dispatcher-Befehl, Wächter und Systemvorgabe müssen `git push` und `gh pr create` im Kern-Kanal verbieten, eine neue
Karte mit Änderungsübersicht, ein Push aus einem Ordner, den der Agent beschreibt (die vorhandene Entschärfung `git_config_filtern`,
infra/sokrates2/wamo_job_dispatch.py:1361-1383, wäre Grundlage), neue Tests und ein Sicherheitsreview. Das ist Folgestufe F1. Bis dahin gilt: Stufen
3a und 3b haben nur ein Lese-Token oder keines, Stufe 4 läuft nur im leeren Testrepo mit dem vorhandenen Ablauf (Schreib-Token je Lauf für genau ein
Repo, `git push` nur nach Freigabe-Klick, infra/sokrates2/wamo_job_dispatch.py:772-853, voice-core/dev_guard.py:167-176), Stufe 5 nur nach F1 oder
Branch-Schutz.

**E12 Claude-Code-Version in der Sandbox.** Getestet ist 2.1.303 (infra/sokrates2/README.md:7). Die veröffentlichten Änderungshinweise nennen nach
2.1.303 sicherheitsrelevante Korrekturen: 2.1.309 (Shell-Startdateien außerhalb der Sandbox, Änderung der MCP-Konfigurationsdatei, Prüfung zusammengesetzter
Bash-Befehle) und 2.1.319 („Bash permission bypass“ über ein präpariertes Umgebungspräfix, „sandbox escape when creating a worktree“). Beide Flags,
`--max-turns` und `--max-budget-usd`, sind dokumentiert, ihre Verfügbarkeit in 2.1.303 prüft S0 mit `claude --help`. Die Sandbox schützt sich selbst
(bwrap, Wächter, Ausgangsfilter), deshalb ist ein Update keine Voraussetzung für Stufe 0 bis 2a. Vor Stufe 3a wird die Version angehoben (Paket S6),
nach dem Ablauf der README (Update, danach `wamo-job-einrichten.sh` und Rauchtest, denn `wamo-job.sh` startet bei abweichender Version nichts,
infra/sokrates2/wamo-job.sh:204-205). Die Anhebung wirkt auch auf den PC-Kanal und braucht Ruhe.

**E11 Frühere Fragen an Niko, jetzt mit der Empfehlung entschieden** (Niko hat alle Freigaben erteilt, Änderungen bleiben jederzeit möglich)

| Frage | Entscheidung | Wer |
| --- | --- | --- |
| Deckt das Ja vom 10.11. das Einschalten? | Repo-Arbeit, Aufräumen und die Stufen 0 bis 2a laufen ohne weiteres Ja, weil dort kein Auftrag laufen kann. Die Stufen 3a und 3b setzen Schritte von Niko voraus. Vor Stufe 4 und Stufe 5 gibt es je ein ausdrückliches Ja. | Niko, manuell (Ja) |
| Tokens enger fassen | Vor Stufe 3b: App-Auswahl prüfen, `GH_TOKEN` und `GH_TOKEN_LESEN` aus `geheim.env` löschen (Server, nur Namen prüfen) und bei GitHub widerrufen. Folge für den PC-Kanal: PC-Aufträge laufen danach nur für Repos der App-Installation. | Niko, manuell (GitHub), Server-Schritt S8 |
| Testrepo | Neues, leeres, privates Repo mit neutralem Namen, ohne Verbindung zu echten Repos, nur in der App-Auswahl und in `REPOS`. | Niko, manuell |
| Claude-Zugang | Nikos eigener Zugang in der Karte Modellzugang, bevorzugt ein API-Schlüssel mit Ausgabenlimit im Workspace, sonst ein Claude-Code-Token mit Rotation nach den Stufen 4 und 5. Im Büro zählt nur Quelle `token` oder `api`, nie `uebergang`. Widerrufsstelle im Konto vor Stufe 3a prüfen. Der Serverzugang bleibt gesperrt. | Niko, manuell |
| Kostengrenzen | Je Lauf 2 USD und 40 Wendungen (Dispatcher), je Auftrag 3 USD und je Tag und Profil 10 USD (Kern, alle Läufe gezählt), im Dispatcher höchstens 8 Starts je Tag. Vor Stufe 5 neu festlegen. | Umsetzung, Niko prüft |
| Wer darf | Nur Niko über `WAMO_ENTWICKLUNG_PROFILE`, weitere Admins einzeln. | Umsetzung |
| Befehlskriterien | Im Büro aus (E2), `pruefen` im Kern-Kanal gesperrt. | Umsetzung |
| Branch-Schutz | Testrepo ohne. Vor Stufe 5 Branch-Schutz (Tarif mit Rulesets für private Repos) oder Veröffentlichung durch den Dispatcher (F1). | Niko, manuell |
| Ablauf der Schlüsselzeile | `ABLAUF` 30 Tage bis Stufe 4, danach 180 Tage mit bewusster Verlängerung. | Umsetzung |
| Sitzungsbindung | Ein Auftrag endet mit der Sitzung, die Oberfläche warnt vor dem Neuladen. Eigener Auftragsdienst später (F3). | Umsetzung |

## 3. Architektur

```text
Admin (aal2, Profil aus WAMO_ENTWICKLUNG_PROFILE) klickt die Karte (Repo, Art, Text sichtbar)
Kern wamo-core@a|b (Büro-Schranken: Admin, Profil, Zugang, Kosten, Quittung, Audit in actions)
  ssh -F none, /var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs, Ziel wamo@192.168.178.136:22
sshd, Match User wamo (ExposeAuthInfo yes), AuthorizedKeysFile: .../wamo (PC) und .../wamo-kern (Kern)
  restrict,from="192.168.178.136",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 ... wamo-kern-jobs
Dispatcher 7: Kanal aus Schlüssel (SSH_USER_AUTH), strikter Modus, Regeln aus /etc/wamo/kernkanal.conf
  Notaus-Datei, Befehlsliste, Namensraum kern-, nur zugang=folgt, nur GitHub-App, Grenzen je Lauf und Tag
Sandbox (bwrap), Wächter, Ausgangsfilter, Freigaben per Klick: unverändert
```

**Komponenten.** Kern: `voice-core/dev_agent.py` (Auftragslogik, 1622 Zeilen, wird nur dünn angefasst), neu `voice-core/entwicklung_betrieb.py` (Büro-Schranken,
Zulassung, Kosten, Audit-Hilfen, `SCHRANKEN_VERSION`), `voice-core/bot.py` (Verdrahtung). Dispatcher: `infra/sokrates2/wamo_job_dispatch.py` (Version 7, 2962
Zeilen, nur Einhängepunkte), neu `infra/sokrates2/wamo_job_kernkanal.py` (alle Kern-Kanal-Regeln, nach dem Muster von `wamo_job_pruefen.py` in
`/opt/wamo/waechter/`). Skripte: `wamo-job-kern-einrichten.sh`, `wamo-job-kern-rueckweg.sh`, `wamo_job_kern_rauchtest.py`. Neue Logik liegt in neuen
Modulen, damit die Dateien über der Projektgrenze von 800 Zeilen nicht weiter wachsen (code-review.md, MEDIUM ohne Begründung).

**Weg zum Dispatcher: Bewertung der Alternativen**

| Weg | Kanalbindung | Aufwand und Risiko | Ergebnis |
| --- | --- | --- | --- |
| `ssh` mit Quelladresse (Revision 1) | nur `from=` und erstes Feld von `SSH_CONNECTION`, unbekannt heißt PC mit vollen Rechten | klein | verworfen, fail-open (Einwand S1) |
| `ssh` mit Schlüsselidentität (gewählt) | Fingerabdruck aus `SSH_USER_AUTH`, strikter Modus, unbekannt heißt Exit 2 | klein, gleicher Ausführungskontext wie der PC-Weg, vorhandener Client, vorhandene Tests | wird gebaut |
| Unix-Socket, Peer-Prüfung (`SO_PEERCRED`) | uid des Gegenübers, kein Schlüssel auf der Platte, kein fail2ban, keine OpenSSH-Abhängigkeit | neuer Client im Kern, Socket-Unit mit `User=wamo`, Sandbox-Start in neuem Kontext (kein sshd-Login, andere Härtung), Lebenslinie bei Verbindungsende neu zu bauen (infra/sokrates2/wamo_job_dispatch.py:2938-2944 hängt am Signal von sshd) | Folgestufe F4, sinnvoll zusammen mit einem Auftragsdienst (F3) |
| Loopback `127.0.0.1` | wie Adresse | zweite known_hosts-Zeile, Lauschadresse unbelegt | verworfen, dient als Nein-Probe |

Die Schlüsselidentität bringt den Kern-Kanal auf das Niveau der Peer-Prüfung, was die Bindung angeht: Die Zuordnung hängt an einem Berechtigungsnachweis,
den nur `wamo-core` besitzt, nicht an einer Adresse. Offen bleiben die Nachteile des Schlüssels auf der Platte (0600, `wamo-core`, wer `wamo-core` übernimmt,
nutzt ihn, der Kanal ist dafür eng) und die Abhängigkeit von `fail2ban` und OpenSSH (Vorbedingungen in Abschnitt 8).

**Sandbox, Wächter, Selbstschutz.** Unverändert. `SELF_REPO = "wamo"` bleibt im Kern (voice-core/dev_guard.py:66, voice-core/dev_agent.py:396,411,452) und im
Dispatcher (infra/sokrates2/wamo_job_dispatch.py:281,340,791,842), dazu im Kern-Kanal die Fork-Prüfung (4.1). `PARALLEL_MAX = 1` gilt für beide Kanäle
(:123), ein belegter Platz wird für Kern-Aufträge hörbar gemeldet. Die Sandbox sieht nur den Projektordner `/srv/wamo/projekte/<projekt>` (infra/sokrates2/wamo-job.sh:313-318),
im Kern-Kanal ist das immer ein Ordner im Namensraum `kern-`.

## 4. Sicherheitsmodell

### 4.1 Kanäle, fail-closed (schließt Einwand a)

Der Dispatcher bestimmt den Kanal einer Verbindung ausschließlich aus Dingen, die ein Angreifer im Kern nicht setzen kann.

| Fall | Erkennung | Folge |
| --- | --- | --- |
| lokal | weder `SSH_CONNECTION` noch `SSH_ORIGINAL_COMMAND` (Betreiber, Rauchtests, Notaus), wie bisher im Code (infra/sokrates2/wamo_job_dispatch.py:2923-2926) | Kanal `lokal`, Verhalten wie Version 6 |
| kein Kern-Schlüssel vorhanden | weder `/etc/ssh/authorized_keys.d/wamo-kern` noch `/etc/wamo/kernkanal.conf` existiert (Stufe 0) | nicht strikt: Kanal `pc`, Verhalten wie Version 6. Es kann niemand als Kern ankommen, weil kein Kern-Schlüssel existiert |
| strikter Modus | eine der beiden Dateien existiert (auch leer, unlesbar oder fehlerhaft) | jede ssh-Verbindung wird eindeutig zugeordnet, sonst Exit 2 |
| Kern | der Schlüssel aus `SSH_USER_AUTH` steht in `wamo-kern` | Kanal `kern`, Regeln aus `kernkanal.conf`, gewinnt vor `pc` |
| PC | der Schlüssel steht in `wamo` und nicht in `wamo-kern` | Kanal `pc`, Verhalten wie Version 6 |
| unklar | `SSH_USER_AUTH` fehlt, ist unlesbar, fremd besessen oder der Schlüssel steht in keiner der Dateien | Ereignis `kanal_gesperrt` mit Grund `herkunft_unklar`, Exit 2, vor jedem Lesen von `geheim.env` und jedem Token |

`SSH_USER_AUTH` setzt sshd selbst (`ExposeAuthInfo yes` im Block `Match User wamo`), das Muster steht im Repo (infra/sokrates2/wamo_pc_token.py:118-135,
infra/sokrates2/wamo-pc-tunnel-einrichten.sh:165). Der Dispatcher prüft zusätzlich, dass die Datei dem eigenen Benutzer gehört und nicht für andere
schreibbar ist. Das Lesen der Schlüsseldateien ist tolerant (Optionen und Kommentar werden übersprungen, verglichen wird der Schlüsselteil), ein Lesefehler im
strikten Modus ist `herkunft_unklar`. Die Quelladresse zählt nicht mehr für die Entscheidung, `from=` und `restrict` in der Zeile bleiben als zweite Schicht.
Fehlt in Stufe 1 die zweite `AuthorizedKeysFile` im sshd, lehnt sshd den Schlüssel ab, auch das ist geschlossen.

**Regeln des Kern-Kanals** (in dieser Reihenfolge, jeder Verstoß ist `kanal_gesperrt` mit Exit 2 und einem Grund-Code, ohne Token). Prüfstelle: unmittelbar
nach `parse_command` und `parse_header`, vor `geheim.env`, vor Sperren und Selbsttest, und ein zweites Mal unmittelbar vor der Token-Erzeugung
(Einwand S11, infra/sokrates2/wamo_job_dispatch.py:914-934,2393-2418).

1. Notaus-Datei vorhanden: `notaus`. Nur `ping` und `status` gehen noch.
2. `kernkanal.conf` fehlt, ist nicht root-eigen, für andere schreibbar, hat unbekannte oder doppelte Schlüssel oder ungültige Werte: `konfig_ungueltig`.
   `KANAL` ist `zu` oder fehlt: `kanal_zu`. `ABLAUF` fehlt, ist ungültig oder liegt vor heute: `abgelaufen`.
3. Befehlsliste: `ping`, `start`, `status`, `pending`, `approve`, `cancel`. Alles andere, auch `pruefen` bei `WAMO_PRUEFEN=an`: `befehl`.
   Bei geschlossenem oder fehlerhaftem Kanal gehen nur `ping`, `status`, `pending`, `cancel`, und zwar für eigene Aufträge.
4. Eigene Aufträge: `auftrag.json` trägt `kanal` (neu, `kern` oder `pc`). Ein Auftrag ohne das Feld gehört nie dem Kern-Kanal. `status`, `pending`,
   `approve` und `cancel` im Kern-Kanal wirken nur auf Aufträge mit `kanal=kern`: `fremd`.
5. Art in `ARTEN`: `art`. Arten mit GitHub-Stufe brauchen ein Repo aus `REPOS` (`repo`, `repo_fehlt`) und den App-Modus. Dienste außer GitHub nur aus
   `DIENSTE`, `supabase` nie: `dienst`.
6. Projektname: muss mit `kern-` beginnen, und bei gesetztem Repo genau `kern-<name>` sein (Name kleingeschrieben, `.` und `_` zu `-`): `projekt`. Der
   PC-Kanal darf Namen mit `kern-` nicht öffnen. Damit teilen sich beide Kanäle keinen Projektordner (Einwände S3 und U12).
7. Zugang: nur `zugang` gleich `folgt` mit `profil` und gültiger zweiter Zeile. Fehlt das Feld oder steht dort `server`: `zugang`. Diese Prüfung liegt vor
   `lese_geheimnisse()`, deshalb erreicht auch ein Kern ohne Feld nie den Serverzugang (Einwände S2, U1, B13; infra/sokrates2/wamo_job_dispatch.py:364-370,2406-2411).
8. Fork-Prüfung beim ersten Token je Lauf: GitHub meldet für das Repo `fork`, `parent`, `source` und `template_repository`. Ein Fork, ein Repo aus einer
   Vorlage oder ein Ursprung mit Namen `wamo` (klein verglichen) ist `fork`. Eine unklare Antwort ist ebenso gesperrt.
9. Grenzen (4.3): `grenze_start`, `grenze_laufzeit`, `grenze_folgen`, `abstand`, `platz`.

`kernkanal.conf` kennt genau diese Schlüssel, jeweils einmal: `KANAL`, `ABLAUF`, `ARTEN`, `REPOS` (Form `owner/name`, klein verglichen, höchstens 10, kein
`wamo`, keine Platzhalter), `DIENSTE`, `MAX_TURNS`, `MAX_BUDGET_USD`, `MAX_START_STUNDE`, `MAX_START_TAG`, `MAX_LAUFZEIT_TAG_S`, `MAX_FOLGEN`,
`MIN_ABSTAND_S`, `MIN_PLATZ_MB`. Standard und Obergrenzen legt das Skript fest, der Dispatcher lehnt Werte außerhalb ab. `ping` meldet
`kanal` (`lokal`, `pc` oder `kern`) und `kernkanal` (strikt, offen, Grund, Arten, Repos, Ablauf, Resttage), nie Werte aus `geheim.env`.

### 4.2 Zugänge (schließt Einwand b)

| Zugang | Heute | Im Kern-Kanal | Pflicht vor |
| --- | --- | --- | --- |
| Claude | eigener Zugang, sonst Serverzugang für das Profil der Kernzuordnung (voice-core/dev_agent.py:276-282) | nur der eigene Zugang des Profils je Lauf über die zweite Zeile (`zugang=folgt`), Quelle `token` oder `api`. Serverzugang: im Kern nie (`serverzugang_erlaubt()` ist im Büro immer `False`), im Dispatcher durch Regel 7 unabhängig vom Feld `zugang` | Stufe 3a |
| GitHub | App oder Rückfall `GH_TOKEN`, Repo optional (infra/sokrates2/wamo_job_dispatch.py:600-622,787-793) | nur App, Installation auf ausgewählte Repos ohne `wamo` und ohne Kopien, Token je Lauf für ein Repo aus `REPOS`, Rechte je Art, höchstens eine Stunde, am Ende widerrufen (:772-853). Danach `GH_TOKEN` und `GH_TOKEN_LESEN` löschen und bei GitHub widerrufen | Stufe 3b |
| Jira, Vercel | breite Schlüssel | gesperrt, bis ein Jira-Lesekonto und ein Vercel-Token nur für Preview in `DIENSTE` stehen | Stufe 5 |
| Supabase | Konto-Token ohne Projektgrenze | nie | entfällt |
| Kern | `WAMO_SCHLUESSEL_KEK` entschlüsselt alle Profilzugänge (voice-core/modellzugang.py:395-425) | kein neues Token, nur der SSH-Schlüssel. Ein übernommener Kern kennt alle Profil-Zugänge, das Risiko steht in 4.6 | entfällt |

### 4.3 Kosten- und Wendungsgrenzen, Schranken im Kern

Zwei Schichten mit klarer Rollenverteilung: Der **Dispatcher** begrenzt hart und gilt auch gegen einen übernommenen Kern. Der **Kern** begrenzt die
Gesamtkosten über alle Runden und schützt vor Fehlbedienung, nicht vor Übernahme (Einwand S5).

| Grenze | Wo | Umsetzung |
| --- | --- | --- |
| Wendungen je Lauf | Dispatcher | `--max-turns <MAX_TURNS>` im Claude-Aufruf (infra/sokrates2/wamo_job_dispatch.py:2460-2463), Wert nur aus `kernkanal.conf`, die Kopfzeile kann nichts steuern. Nur im Kern-Kanal, der PC-Kanal bleibt unverändert |
| Kosten je Lauf | Dispatcher | `--max-budget-usd <MAX_BUDGET_USD>` ebenso. Fehlt ein Flag in der installierten Version, startet der Lauf nicht (Claude Code meldet den Fehler), und das Skript `kanal` verweigert die Öffnung (S0 prüft `claude --help`, Rauchprobe in Stufe 3a mit 0.05 USD) |
| Starts, Laufzeit, Folgen | Dispatcher | `MAX_START_STUNDE`, `MAX_START_TAG`, `MAX_LAUFZEIT_TAG_S`, `MAX_FOLGEN` (Fortsetzungen je Auftrag), Zähler in `/srv/wamo/logs/kernkanal.jsonl` (je Start und Ende eine Zeile). Überschreitung lehnt den einzelnen Start ab (`grenze_*`), der Kanal bleibt offen, damit ein Angreifer ihn nicht mit Absicht dauerhaft schließen kann. Obergrenze der Kosten je Tag ist damit `MAX_START_TAG * MAX_BUDGET_USD` |
| Platz für den PC-Kanal | Dispatcher | `MIN_ABSTAND_S` zwischen zwei Kern-Läufen, `PARALLEL_MAX = 1` bleibt. Ein Dauerstart des Kerns kann den PC nicht dauerhaft verdrängen |
| Platte | Dispatcher | `MIN_PLATZ_MB` freier Platz vor jedem Kern-Start (`platz`). Job- und Projektordner des Kern-Kanals werden vom Skript `status` gezählt, ein Aufräumen ist Folgepaket |
| Zeit je Lauf | Wächter | 1800 s (infra/sokrates2/wamo-job.sh:83,185), bei Abnahme höchstens 5 Runden und 60 Minuten (voice-core/dev_agent.py:106-111) |
| Kosten je Auftrag und Tag | Kern | Je Auftrag vor jedem weiteren Lauf, je Tag und Profil (Europe/Berlin) vor dem Start, aus `actions` (E9). Alle Läufe zählen, auch mit Abo-Token (ob `total_cost_usd` dort geliefert wird, prüft die Rauchprobe 3a). Ist die Datenbank nicht erreichbar, startet im Büro nichts |

**Schranken im Kern** (Modul `voice-core/entwicklung_betrieb.py`, Einhängepunkte in `dev_agent.py` und `bot.py`):

- **Nur Admin, Profilbindung.** `bot.py:536` bleibt. Im Büro verlangt der `DevAgent`-Konstruktor eine `BueroZulassung`, die nur `bot.build_session` aus der
  geprüften Identität erzeugt (Admin, Profil in `WAMO_ENTWICKLUNG_PROFILE`, Muster `tunnel_profil()` in voice-core/pc_host.py:100-113). Ohne Zulassung
  wirft der Konstruktor im Büro. Außerhalb des Büros bleibt die Signatur gleich, die 19 bestehenden Aufrufe in 7 Dateien brechen nicht (Einwand U3).
  `_vorbereiten`, `_auftrag_start` und `_freigabe` prüfen die Zulassung erneut.
- **Büro-Sperre.** `WAMO_ENTWICKLUNG_ORT=lokal` ist bei `WAMO_BETRIEB=buero` abgelehnt (Vorbild voice-core/pc_host.py:117-119). Ungültige Variablen schalten die
  Entwicklung mit Grund aus. `entwicklung_enabled()` liest im Büro nur `WAMO_ENTWICKLUNG_BUERO`.
- **Zugang im Büro.** Quelle `uebergang` (voice-core/boss.py:201-220) zählt wie `keine`: Die Entwicklung bleibt für dieses Profil aus, mit Grund im Log und für Admins
  in der Statusleiste (voice-core/bot.py:571-577). Kein Rückfall auf die Kernzuordnung (Einwand U15).
- **Zielrepo.** Im Büro Pflicht für Arten mit GitHub-Stufe, der Admin hört dazu einen Satz. Das Repo steht auf der Karte. Das Projekt wird aus dem Repo abgeleitet.
- **Quittung.** Jeder Klick bekommt `auftrag_quittung` (ok oder Grund). `kanal_gesperrt` und ssh-Exit 255 erscheinen als lesbarer Grund (Hostschlüssel, Schlüssel
  abgelehnt, nicht erreichbar), ohne Pfade oder Werte. `approved` gilt erst nach Code 0 von `approve`.

### 4.4 Notaus ohne Neustart des Kerns

Zwei Stufen, beide ohne Eingriff in den Kern. **Kanal zu** (`wamo-job-kern-einrichten.sh kanal zu`) setzt `KANAL=zu`: neue Starts und Freigaben werden abgewiesen, laufende
Aufträge enden regulär. **Notaus** (`wamo-job-kern-rueckweg.sh notaus`) in fester Reihenfolge, damit kein Start durch die Lücke rutscht (Einwand S11):

1. Sperrdatei `/etc/wamo/kernkanal.notaus` anlegen. Ab jetzt weist jeder Kern-Befehl außer `ping` und `status` mit `notaus` ab, und die zweite Prüfung vor der Token-Erzeugung greift.
2. Zeile in `authorized_keys.d/wamo-kern` sichern und löschen (der Kern kommt nicht mehr durch sshd).
3. Alle Aufträge mit `kanal=kern` aus `/srv/wamo/jobs/*/auftrag.json` per lokalem `cancel` beenden (Sandbox gestoppt, Token widerrufen, wie bei Auflegen).
4. Erneut suchen, bis keine Sandbox-Einheit `wamo-job-*` eines Kern-Auftrags mehr läuft, und das Ergebnis in `aktionen.jsonl` und im Journal festhalten.

Der Kern bleibt dabei unberührt, er meldet „Notaus“ als Grund. Wieder öffnen: `notaus-aufheben` entfernt die Sperrdatei, `schluessel` setzt die Zeile neu, `kanal`
öffnet. Der lokale Aufruf (`sudo -u wamo`) bleibt im strikten Modus möglich, weil er ohne sshd-Variablen läuft (Fall „lokal“ in 4.1).

### 4.5 Ablauf der Schlüsselzeile

Der Ablauf steht als `ABLAUF=JJJJMMTT` in `kernkanal.conf` und wird vom Dispatcher erzwungen (Regel 2, `abgelaufen`, bis einschließlich des Tags). `expiry-time` steht nicht in der
Zeile: Auf Sokrates-2 läuft laut Repo-Doku OpenSSH 9.6 (infra/sokrates2/README.md:592,865), und eine unbekannte Option macht die ganze Zeile ungültig. Der Ablauf
wird damit hörbar (`kanal_gesperrt`, nicht „Schlüssel abgelehnt“) und ist von der OpenSSH-Version unabhängig. `status` zeigt die Resttage. Ist `expiry-time` am Server
bekannt (S0), kann es später als zweite Schicht dazukommen.

### 4.6 Bedrohungen

| Bedrohung | Gegenmaßnahme | Restrisiko |
| --- | --- | --- |
| Gekaperter Kern | Kern-Kanal mit Allowlist, Schlüsselbindung, nur App, kein Serverzugang, Namensraum, eigene Aufträge, harte Grenzen, Ablauf, Notaus | Innerhalb der Allowlist startet er ohne Klick und erteilt Freigaben in eigener Sache. Er kennt alle Profil-Zugänge über den Hauptschlüssel. Ab Stufe 4 gibt es keinen Beleg des Klicks außerhalb des Kerns (Einwand S7) |
| Kanal verwechselt | strikter Modus, unbekannt heißt Exit 2, Rauchtest mit einem Schlüssel ohne `from=` | keins erkennbar |
| Schlüssel kopiert | `restrict`, `from`, Zwangsbefehl, 0600 im StateDirectory | root und `wamo-core` können ihn nutzen |
| Falsches Gegenüber | known_hosts aus dem lokalen Hostschlüssel, `StrictHostKeyChecking=yes` | keins erkennbar |
| Fremdes Profil, Mitglied | `bot.py`, Zulassung im `DevAgent`, Profilbindung | gestohlene aal2-Sitzung von Niko |
| Prompt-Injection | Das Modell legt nur Karten an, Start und Freigabe per Klick, die Karte zeigt Repo, Art und Text | Klick ohne Lesen |
| Abfluss des Claude-Zugangs aus der Sandbox | Zugang je Lauf über stdin, Port 443 bewusst offen (infra/sokrates2/README.md:493-502) | Ein langlebiges Claude-Code-Token ist vom Dispatcher nicht widerrufbar. Deshalb Limit-Schlüssel bevorzugt, Rotation, Widerrufsstelle vor 3a geprüft |
| Token-Abfluss GitHub | ein Repo, höchstens eine Stunde, Widerruf am Ende | Port 443 offen |
| Selbstschutz umgangen | `REPOS`, App-Auswahl, `SELF_REPO`, Fork-Prüfung | Eine Kopie als neues Repo unter anderem Namen ist nur durch die Handarbeit bei der App-Auswahl zu verhindern |
| Kosten | Grenzen im Dispatcher (hart) und im Kern (Gesamtsumme) | Ein Lauf kann die Auftragsgrenze einmal überschreiten |
| Plattenfüller, Platzverdrängung | `MIN_PLATZ_MB`, `MIN_ABSTAND_S`, Starts je Zeit | Aufräumen der Kern-Ordner folgt später |
| Direkter Push | Wächter, Freigabe für `git push` | ohne Branch-Schutz technisch möglich, deshalb Stufe 5 erst nach F1 oder Branch-Schutz |
| Rückfall auf einen alten Dispatcher öffnet den Kanal | `wamo-job-rueckweg.sh` verweigert, solange `wamo-kern` existiert | keins erkennbar |
| Ausfall durch das Einschalten | Vorprüfung unverändert, weiche Prüfung im Kern, alte Releases ignorieren den Schalter | keins erkennbar |

## 5. Stufen

Reihenfolge: Repo-Arbeit und PR zuerst (Arbeitsliste R1 bis R12), dann Server-Rollout. Die Stufen 0 bis 2a brauchen Niko nicht, in ihnen kann kein Auftrag laufen
(Kanal geschlossen oder Kern-Schlüssel nicht vorhanden). Ab Stufe 2b brauchen sie Schritte von Niko, die als **Niko, manuell** stehen (Arbeitsliste N1 bis N6).
Jede Stufe beginnt erst, wenn die Prüfung der vorigen bestanden ist und kein Auftrag läuft (Ruhe-Regel, Abschnitt 6).

| Stufe | Ziel und Schritte | Prüfung (Rauchtest) | Abbruch | Rückweg |
| --- | --- | --- | --- | --- |
| 0 ohne Wirkung | Voraussetzung: S0 bestanden, Slot-Betrieb (sonst erst `umstellen`), `ps -u wamo` leer, kein Totmannschalter. PR mergen. Dispatcher 7 und sshd über `wamo-job-einrichten.sh` (`ExposeAuthInfo`, zweite `AuthorizedKeysFile`, vorige `wamo.conf` dauerhaft gesichert). Bestätigung über neue Verbindung binnen 5 Minuten. Kern-Release mit `release` und `umschalten-kern`, `bewaehren 30`, `ausleiten`, danach die Oberfläche | `ping` meldet Version 7, `kanal=pc`, `kernkanal` nicht strikt. Rauchtest grün (bei Vorversion unter 6 zusätzlich `wamo-pruefen-rauchtest` mit `WAMO_PRUEFEN=aus`). Ein PC-Auftrag läuft wie bisher. Journal des neuen Slots: „Entwicklungsaufträge aus“ mit Grund | sshd-Bestätigung scheitert, Rauchtest rot, Slot nicht gesund, PC-Weg gestört | Totmannschalter stellt die gesicherte `wamo.conf` wieder her, `wamo-job-rueckweg.sh` (geht auf die Sicherung vor der letzten Änderung), `rueckweg-kern`, `wamo-ui-einrichten.sh zurueck` |
| 1 Schlüssel, Kanal zu | `schluessel --trocken` (OpenSSH-Version, Zeilenform, `fail2ban` mit `ignoreip` oder `ignoreself` für 192.168.178.136 und 127.0.0.1 als harte Vorbedingung), dann `schluessel --ablauf <JJJJMMTT>` (30 Tage). `kernkanal.conf` mit `KANAL=zu`. Notaus üben, wieder einrichten | Rauchtest als `wamo-core` mit den Eigenschaften aus `systemctl show wamo-core@<slot>`: Ja für `ping` (`kanal=kern`, Kanal zu). Nein für alle Proben aus Abschnitt 7. PC-Weg: `ping` vom PC meldet `kanal=pc`, ein PC-Auftrag läuft. PC-Datei unverändert (Prüfsumme) | Ein erwartetes Nein wird Ja, PC-Weg gestört, `fail2ban` sperrt | `wamo-job-kern-rueckweg.sh notaus`, mit `--entfernen` alles. `wamo-job-rueckweg.sh` verweigert, solange `wamo-kern` existiert |
| 2a Schalter an, Kanal zu | `schalter an <release> --profil <user_id> --kosten-auftrag 3 --kosten-tag 10` (Vorabprüfungen als root, E2), `bewaehren 30`, `ausleiten`. Vorher am freien Slot die Absturz-Neustart-Probe (`systemctl kill`, dann `warte_auf_slot`) | Journal: für Niko `entwicklung=an`, oder ohne eigenen Zugang „Entwicklungsaufträge aus: kein eigener Zugang“. Mitglied aus. `status` zeigt Schalter je Slot gleich dem Drop-in. Rückweg auf ein älteres Release ergibt Entwicklung aus | Slot nicht gesund, Abweichung in `status` | `schalter aus`, dann `bewaehren 30` und `ausleiten` |
| 2b Durchstich, Kanal zu | **Niko, manuell:** eigenen Claude-Zugang hinterlegen (N4), Oberfläche neu laden, eine Karte klicken | Hörbar „Kanal geschlossen“. `actions` steht auf `failed`, das Dispatcher-Log zeigt `kanal=kern grund=kanal_zu`, kein Token, kein Claude-Lauf | Start trotz geschlossenem Kanal, „gestartet“ ohne Quittung | `schalter aus` |
| 3a Recherche | Version anheben (S6), `kanal --arten recherche` mit `MAX_*` nach 4.3. Drei Aufträge, einer durch Auflegen beendet. Rauchproben mit `MAX_BUDGET_USD=0.05` und `MAX_TURNS=2` | Grenzen greifen, Kosten und Dauer in `actions`, Auflegen räumt Sandbox ab, kein GitHub-Token entstanden, kein Zugangswert in Log oder `result` | Grenze greift nicht, Wert im Log, PC-Weg gestört | `kanal zu`, im Zweifel `notaus` |
| 3b lesend, Testrepo | **Niko, manuell:** Testrepo (N2), App-Auswahl (N1). Server: `GH_TOKEN` und `GH_TOKEN_LESEN` löschen (S8), Niko widerruft bei GitHub (N3), ein PC-Auftrag im App-Modus als Probe. `kanal --arten recherche,review,analyse,sicherheit --repos <owner>/<testrepo>`, drei Aufträge, `kanal zu` einmal üben | Token nur für das Testrepo und widerrufen, Fork-Prüfung wirkt, eine GitHub-Art ohne Repo wird abgelehnt, Kosten und Dauer in `actions` | Token für mehr als ein Repo, fehlender Widerruf, PC-Auftrag im App-Modus scheitert, Auftrag ohne Audit | `kanal` auf 3a, `kanal zu` |
| 4 schreibend, nur Testrepo | **Niko, manuell:** Ja. `test,refactoring` ergänzen. Drei Aufträge „Branch, Änderung, Pull Request“ (E10) | `git push` erst nach dem Freigabe-Klick, Pull Request vorhanden, die Freigabe genau einmal als `approved` verbucht. Kein Push auf den Standardbranch ist Beobachtung im Log, nicht erzwungen | Push ohne Freigabe, Pull Request in anderem Repo | `kanal` auf 3b |
| 5 echte Repos | **Niko, manuell:** Ja, Branch-Schutz (Tarif, Rulesets) oder F1 gebaut und erprobt, zwei Wochen Stufe 4 ohne Befund, Kostengrenzen neu, Tokens rotiert (N5). Repos in App-Auswahl und `REPOS`, Ablauf verlängern. `DIENSTE` erst nach engem Jira und Vercel | Die ersten fünf Aufträge mit Blick in Log und `actions`, danach wöchentlicher Abgleich des Dispatcher-Logs gegen `actions` | jeder Fund außerhalb der Allowlist | Repo aus `REPOS` streichen, `kanal zu`, `notaus` |

## 6. Betrieb

**Regeln**

- Der PC ist im Büronetz oder per WireGuard verbunden. Aus `geheim.env` und `core.env` werden nur Namen gezeigt, nie Werte.
- Nie `systemctl restart wamo-core*`, nie `wamo-core-einrichten.sh`, nie `scripts/wamo_kern_sokrates2.py neustart` oder `alles` im Slot-Betrieb. Nach Arbeitspaket R10
  verweigern diese Wege, sobald ein Slot läuft, und verweisen auf `umschalten-kern`.
- Stufe 4 und Stufe 5 beginnen erst nach Nikos ausdrücklichem Ja.

**Ruhe-Regel.** Ruhe heißt: `ps -u wamo` leer, kein offener Totmannschalter (`systemctl list-timers --all`), keine Entwicklungssitzung im Journal des aktiven Slots.

| Schritt | Ruhe nötig? | Grund |
| --- | --- | --- |
| `wamo-job-einrichten.sh` (Stufe 0) | ja | Es startet `wamo-egress` neu, dessen `ExecStop` die nft-Tabelle löscht (infra/sokrates2/wamo-job-einrichten.sh:255, infra/sokrates2/wamo-egress.service:22). Ein laufender Auftrag hätte kurz keinen Ausgangsfilter |
| `schluessel`, `kanal`, Änderungen an `kernkanal.conf` | ja, außer `kanal zu` und Notaus | Ein laufender Kern-Auftrag soll seine Regeln nicht unter den Füßen verlieren |
| `umschalten-kern` | nein | Laufende Sitzungen bleiben auf ihrem Slot, der neue Slot wird erst nach bestandener Probe geschaltet (infra/sokrates2/wamo-core-slots.sh:274-294) |
| `ausleiten` | ja | frühestens nach 1800 s Bewährung, ohne Sitzung und nach 15 Minuten Ruhe im Journal des alten Slots (:204-209). Der nächste `schalter` folgt erst danach |
| Version von Claude Code anheben (S6) | ja | Es betrifft beide Kanäle, `wamo-job.sh` startet bei abweichender Version nichts |

**Slot-Mechanik.** Die Vorlage `wamo-core@.service` hat die Instanzen `a` und `b`, `wamo-core-slots.sh` schaltet sie (`einrichten`, `release`, `umschalten-kern`,
`bewaehren`, `ausleiten`, `rueckweg-kern`, `status`). Läuft der Server noch auf der alten Unit (`aktiver_slot` meldet „erst umstellen“, :257-262), gilt vor Stufe 0 der Zweig
`umstellen <release>` mit denselben Ruhebedingungen (:225-241). Das Schalter-Drop-in liegt nur in `wamo-core@.service.d` und hat einen Dateinamen, den die alte Unit
nie hat. Das Spiegeln kopiert jede Datei der alten Unit mit Marke über gleichnamige Dateien der Vorlage (:176-195), deshalb prüft `schalter an`, dass in `wamo-core.service.d`
keine gleichnamige Datei liegt (Einwand S13). Nach jedem Kern-Wechsel gilt `bewaehren 30` und eine Admin-Sitzung ohne „Entwicklungsaufträge aus“ im Journal, soweit der Schalter an ist. In 3a, 3b
und 4 braucht es je drei erfolgreiche Aufträge.

**Rückweg**

| Ebene | Befehl | Wirkung |
| --- | --- | --- |
| Kanal zu | `wamo-job-kern-einrichten.sh kanal zu` | Neue Kern-Starts und Freigaben werden abgewiesen, laufende enden regulär, der PC bleibt unberührt |
| Notaus | `wamo-job-kern-rueckweg.sh notaus` | Reihenfolge nach 4.4, Dauer wenige Sekunden, Kern und PC laufen weiter |
| Schalter aus | `schalter aus <release>`, dann `bewaehren 30` und `ausleiten` | Das Drop-in fällt ohne Lücke weg |
| Während der Bewährung | `wamo-core-slots.sh rueckweg-kern` | vorige Instanz. Danach `status` prüfen und das Drop-in passend setzen |
| Kern-Code | `umschalten-kern <voriges Release>` oder der Anker-Rückweg | Ältere Releases lesen `WAMO_ENTWICKLUNG_BUERO` nicht und starten mit Entwicklung aus, ein manueller Eingriff am Drop-in ist nicht nötig |
| sshd-Drop-in | Totmannschalter oder `/var/backups/wamo-job-kern/<Zeit>/wamo.conf.vorher` | Die gesicherte Fassung wird wiederhergestellt und `ssh` neu geladen (Einwand B6) |
| Dispatcher | erst `notaus`, dann `wamo-job-rueckweg.sh` | Das Skript geht eine Sicherung zurück (Vorversion laut S0, laut README möglicherweise älter als 6) und verweigert, solange `wamo-kern` existiert |
| Oberfläche | `wamo-ui-einrichten.sh zurueck` | vorige Farbe |
| Alles | nach `schalter aus`: `wamo-job-kern-rueckweg.sh --entfernen` | Entfernt Schlüssel, known_hosts, `kernkanal.conf` und die Sperrdatei. Verweigert, solange das Drop-in besteht |

Die zweite `AuthorizedKeysFile` darf bleiben, sshd überspringt eine fehlende Datei (A, am Server prüfen).

**Beobachtbarkeit.** Jede Bedienhandlung (`schluessel`, `kanal`, `schalter`, `notaus`) schreibt eine Zeile ohne Werte in `/var/log/wamo-job-kern/aktionen.jsonl` und per `logger -t wamo-job-kern`.
Der Dispatcher führt `kanal=` in `auftrag.json`, im Log `/srv/wamo/logs/auftraege.log` und in `kernkanal.jsonl`. `status` zeigt Resttage der Schlüsselzeile, Kanalzustand, Schalter je Slot, laufende Kern-Aufträge
und die Zahl der Kern-Ordner. Ein Abgleich des Dispatcher-Logs gegen `actions` ist wöchentlich Pflicht ab Stufe 3b.

## 7. Tests

Alle Pakete folgen TDD: erst die roten Tests (Arbeitsliste nennt sie je Paket), dann die Umsetzung. Voice-core-Tests gezielt je Datei, der volle Lauf dauert etwa 16 Minuten und
läuft nicht parallel zu Builds. Tests starten aus PowerShell, `uv sync` im voice-core nie.

- **Kern** (`voice-core/test_entwicklung_betrieb.py`, `test_dev_agent_sokrates2.py`, `test_memory.py`, `test_integration_db.py`):
  - Ohne `BueroZulassung` wirft der Konstruktor im Büro, außerhalb des Büros bleiben die 19 bestehenden Aufrufe grün.
  - Admin außerhalb von `WAMO_ENTWICKLUNG_PROFILE` und Mitglieder bekommen keine Aufträge. `lokal` im Büro, unlesbarer Schlüssel und ungültige Variablen schalten mit Grund ab.
  - `entwicklung_enabled()` liest im Büro nur `WAMO_ENTWICKLUNG_BUERO`. Quelle `uebergang` zählt wie `keine`. Im Büro gibt es keinen Rückfall auf den Serverzugang.
  - Eine GitHub-Art ohne Repo wird abgelehnt, `recherche` ohne Repo ist erlaubt. Das Projekt wird aus dem Repo abgeleitet, `wamo` bleibt gesperrt.
  - Quittung bei Problem, bei „belegt“, bei `kanal_gesperrt` (jeder Grund-Code) und bei Erfolg. `approved` erst nach Code 0 von `approve`, der zweite Klick wird verbucht.
  - Beide Kostengrenzen greifen, veraltete `running`-Zeilen zählen mit der Auftragsgrenze. `actions` läuft in der Folge `pending`, `approved`, `running`, `done` oder `failed`, ohne Geheimnis in `result`.
  - Befehlskriterien wirken im Büro nur mit `WAMO_ENTWICKLUNG_BEFEHLE_BUERO=an`.
- **Dispatcher** (`voice-core/test_sokrates2_dispatch.py`, `test_sokrates2_pruefen.py`, neu `test_kernkanal.py`):
  - Kanalzuordnung: Kern-Schlüssel mit und ohne `from=` ergibt `kern`, PC-Schlüssel `pc`, im strikten Modus fremder oder fehlender Schlüssel `herkunft_unklar`, ohne Kern-Schlüssel `pc`, lokal `lokal`.
  - Jeder Konfigurationsfehler schließt den Kanal. Jede Regel 1 bis 9 aus 4.1 hat einen Test, die Abweisung liegt vor dem GitHub-Token und vor `lese_geheimnisse()` (Zähler im Test).
  - Eine Kopfzeile ohne `zugang` und eine mit `zugang=server` enden mit Exit 2 aus dem Kern-Kanal, aus dem PC-Kanal laufen sie wie bisher.
  - `pruefen` aus dem Kern-Kanal wird abgewiesen, auch mit `WAMO_PRUEFEN=an`. Fremde Auftrags-IDs und Aufträge ohne `kanal` werden abgewiesen. PC-Start auf `kern-*` wird abgewiesen.
  - `--max-turns` und `--max-budget-usd` stehen nur im Aufruf des Kern-Kanals. Notaus-Datei, Grenzen, Fork-Prüfung (mit Attrappe der GitHub-Antwort), Wiederholung der Prüfung vor dem Token.
  - Der bestehende Test auf `version == "6"` (voice-core/test_sokrates2_dispatch.py:2076) wird auf `"7"` angehoben, `ping` trägt `kanal` und `kernkanal`. Alle übrigen bestehenden Tests bleiben unverändert grün.
- **Projektname** in Kern und Dispatcher: dieselbe Wertetabelle (Arbeitsliste, Abschnitt 1) wird auf beiden Seiten getestet.
- **Unverändert-Test:** `wamo-core-vorpruefung.sh` und `wamo-core@.service` enthalten weder `WAMO_ENTWICKLUNG_BUERO` noch eine geänderte Zeile 25. `ERLAUBT` und `AUS_ENV` enthalten kein `WAMO_ENTWICKLUNG*`.
- **Skripte im Probelauf** (`WAMO_JOB_KERN_WURZEL`, `WAMO_JOB_WURZEL`, kein root; `voice-core/test_job_kern_skripte.py`, `test_sokrates2_pruefen.py`, `test_kern_slots.py`, `test_rd_skripte.py`, `scripts/test_wamo_ui_sokrates2.py`):
  - Zeilenform der Schlüsselzeile genau, ohne `expiry-time`. PC-Datei unverändert (Prüfsumme), zweiter Lauf ändert nichts, kein `.pub` unter `~/wamo-infra`.
  - Kein `systemctl restart`, `start` oder `stop` in den neuen Skripten. `schalter` nimmt das Drop-in zurück oder meldet „teilweise“, ein zweiter Aufruf ist ein No-op, `--entfernen` verweigert bei vorhandenem Drop-in.
  - Die Rückweg-Sperre prüft relativ zur Wurzel, beide Fälle (Datei vorhanden, nicht vorhanden). Das gesicherte `wamo.conf` wird vom Totmannschalter wiederhergestellt (Textprüfung).
  - Die ssh-Optionen des Rauchtests gleichen `dev_agent.ssh_argv`. Ein Grep nach `systemctl (restart|start) wamo-core` in `infra` und `scripts` liefert nur bewusste Treffer.
- **Oberfläche** (`node --test`, Typprüfung): reine Funktion in `ui/src/lib/auftragStart.ts` für Quittung, Frist (15 s) und Kartenzustand, plus `auftragNachricht.test.ts`. Ein Kern ohne Quittung bricht nichts.
- **Rauchtest am Server (Stufe 1, Ja und Nein):**
  - Ja für `ping`. Nein für `id`, den leeren Befehl, `ping; id`, mehr als 300 Zeichen, `-tt`, `-L`, `-R`, `sftp`, `pruefen`, einen Start mit Projekt `wamo`, ohne Präfix `kern-`, ohne Feld `zugang`, mit `zugang=server`, mit fremder Auftrags-ID und bei geschlossenem Kanal.
  - Ein Schlüssel ohne `from=` wird als `kern` erkannt, eine Verbindung über `127.0.0.1` scheitert an `from`, eine falsche known_hosts ergibt Exit 255. `wamo-core` liest `/etc/wamo` nicht, `wamo` liest `/var/lib/wamo-core` nicht.
  - Übergänge: Absturz-Neustart am freien Slot, Rückweg auf ein älteres Release (Entwicklung aus), PC und Kern gleichzeitig (belegt in beide Richtungen), ein PC über WireGuard landet nie im Kern-Kanal, Notaus mit laufendem Kern-Auftrag.
  - Die Eigenschaften der Unit kommen aus `systemctl show wamo-core@<slot>` (geladene Konfiguration einschließlich gespiegelter Drop-ins, Einwand B14).
- **Vor dem PR:** `verification-loop` (Build, Typen, Lint, Tests, Secret-Scan, Diff), die Agenten `security-reviewer`, `python-reviewer` und `react-reviewer`, Fremdverweis-Grep. Es gibt keine Migration und kein SQL.

## 8. Am Server vor dem Rollout lesend zu prüfen

Alles nur lesen, ohne einen Wert aus `geheim.env` oder `core.env` zu zeigen (Arbeitspaket S0). Abweichungen vom Repo werden als Befund notiert, bevor Stufe 0 beginnt.

1. Slots: `wamo-core-slots.sh status`, `slot.inc`, `is-active` und `is-enabled` für `wamo-core`, `wamo-core@a`, `wamo-core@b`, laufendes Release. Ist der Server noch nicht umgestellt, gilt der Zweig `umstellen` (Abschnitt 6).
2. Installierte Vorlage, `/opt/wamo-core/bin/wamo-core-vorpruefung` und Drop-ins in `wamo-core.service.d` und `wamo-core@.service.d` gleich dem Repo (diff, Marke der Spiegelung).
3. Dispatcher: `sudo -u wamo -H /opt/wamo/bin/wamo-job-dispatch ping` (Version, `github`, `serverzugang`, `pruefen`). Laut README.md:36 und infra/sokrates2/README.md:1020-1026 ist Version 6 samt `pruefen` nicht ausgerollt, der Ist-Stand ist **V**. Danach richten sich Rückweg und Umfang von Stufe 0.
4. Nur Namen: `sudo /usr/local/sbin/wamo-core-env liste` (kein `WAMO_ENTWICKLUNG*`, Stand von `WAMO_ABNAHME_BEFEHLE`, `WAMO_SCHLUESSEL_KEK`) und die Namen in `/etc/wamo/geheim.env` (`WAMO_GH_APP_*`, `GH_TOKEN`, `GH_TOKEN_LESEN`, `VERCEL_*`, `JIRA_*`, `SUPABASE_ACCESS_TOKEN`, `WAMO_SERVERZUGANG`, `WAMO_PRUEFEN`).
5. Routing: `ip route get 192.168.178.136` zeigt `local`, `sysctl net.ipv4.conf.all.accept_local` ist 0. Nur noch Zusatzbelege, die Kanalbindung hängt nicht mehr daran.
6. sshd: `ss -ltn` auf Port 22, `sshd -T -C user=wamo,host=sokrates2,addr=192.168.178.136` und mit `addr=127.0.0.1`, gefiltert auf `authorizedkeysfile`, `forcecommand`, `exposeauthinfo`, `acceptenv`, `maxsessions`. Zeilenzahl und Optionen von `authorized_keys.d/wamo` (nie den Schlüssel). OpenSSH-Version: `restrict`, mehrere `AuthorizedKeysFile`, `ExposeAuthInfo` und die Frage, ob `expiry-time` bekannt ist (Repo-Doku: 9.6). `acceptenv` darf `SSH_USER_AUTH` nicht durchlassen.
7. `fail2ban` oder `sshguard` aktiv? `ignoreip` oder `ignoreself` für 192.168.178.136 und 127.0.0.1 ist harte Vorbedingung für Stufe 1: Ein Bann der eigenen Adresse träfe Supavisor (55322) und die API (55443) des Kerns.
8. Firewall: `ufw status verbose`, `iptables -S INPUT`, `nft list ruleset` gefiltert auf `dport 22`, `iifname lo` und `skuid`. Keine Regel für `wamo-core` erwartet.
9. `ssh` unter der Härtung der Unit: `systemd-run` mit den Eigenschaften aus `systemctl show wamo-core@<slot>`, als `wamo-core`, zuerst `/usr/bin/ssh -V`. Paket `openssh-client` vorhanden.
10. Rechte: `stat` auf `/var/lib/wamo-core` (wamo-core 0700, `.ssh` fehlt), `/etc/wamo` (root:wamo 0750), `/etc/ssh/authorized_keys.d`. `id wamo-core` nur eigene Gruppe.
11. Claude Code: `CLAUDE_VERSION` in `/opt/wamo/wamo-job.conf` gegen 2.1.303 (getestet) und 2.1.319 (Korrekturen, E12). Enthält `claude --help` als `wamo` die Flags `--max-turns` und `--max-budget-usd`? Ist die Antwort Nein, bleibt der Kern-Kanal für GitHub-Arten zu.
12. Anker: `sudo wamo-anker status`, die Kopie `kern-slots` stimmt mit `~/wamo-infra/wamo-core-slots.sh` überein.
13. Modellzugang von Nikos Profil: Quelle `token`, `api` oder `uebergang`, nur ja oder nein. GitHub-App: Installation auf „Only select repositories“ ohne `wamo` (Rauchtest `--app-echt`), Tarif für Rulesets privater Repos (Niko, manuell).
14. Ruhe und Platz: `journalctl` des aktiven Slots seit 15 Minuten, `ps -u wamo`, `ls -t /srv/wamo/jobs`, `systemctl list-timers --all`, `df` auf `/srv/wamo`, `ls /srv/wamo/projekte` (kein Ordner `kern-*` vorhanden).
15. Nach Stufe 2b: `/srv/wamo/logs/auftraege.log` zeigt für Kern-Starts `kanal=kern`, das sshd-Journal den Fingerabdruck des Kern-Schlüssels.

## 9. Folgestufen

Nicht in diesem Durchgang, aber geplant und mit Auslöser:

| Nr. | Folgestufe | Auslöser |
| --- | --- | --- |
| F1 | Veröffentlichung durch den Dispatcher: Lauf mit Lese-Token, nach dem Freigabe-Klick pusht der Dispatcher in einen eigenen Zweig (`wamo/<job>`) und öffnet den Pull Request. Umfang siehe E10 (Befehl, Karte mit Änderungsübersicht, entschärftes Git, Wächter-Regeln, Tests, Review) | Voraussetzung für Stufe 5, wenn kein Branch-Schutz verfügbar ist |
| F2 | Freigabe-Beleg außerhalb des Kerns: Der vorhandene Freigabe-Dienst stellt einen an das aal2-Token gebundenen Beleg aus, den der Dispatcher für `approve` im Kern-Kanal verlangt | vor Stufe 5, wenn Stufe 4 Hinweise auf Freigaben ohne Klick zeigt |
| F3, F4 | Eigener Auftragsdienst (Aufträge überleben die Sitzung) zusammen mit dem Unix-Socket und `SO_PEERCRED`, kein Schlüssel mehr auf der Platte | wenn Sitzungsbindung oder Schlüsselverwaltung im Betrieb stören |
| F5 | Aufräumen der Kern-Job- und Projektordner, Plattenquote | wenn `status` mehr als 20 Kern-Ordner zeigt |
| F6 | Frische aal2-Prüfung je Auftragsstart | Neubewertung vor Stufe 5 |
| F7 | `expiry-time` als zweite Schicht neben `ABLAUF` | wenn die OpenSSH-Version es kennt |
| F8 | Befehlskriterien (`pruefen`) im Betrieb, eigene Entscheidung | nach Stufe 4 |

## Anhang A: Einwand-Protokoll

Nummern: **S1 bis S13** (Prüfer Sicherheit), **U1 bis U17** (Umsetzbarkeit), **B1 bis B14** (Betrieb), je mit der Schwere der Prüfer. Alle 44 Einwände sind erfasst, mehrere sind
zusammengefasst, wo sie dieselbe Stelle betreffen. Belege wurden im Arbeitsbaum nachgelesen, bevor etwas zurückgewiesen wurde.

| Einwand | Kurztext | Entscheidung | Begründung |
| --- | --- | --- | --- |
| S1 (hoch) | Kanalzuordnung fail-open, nur Quelladresse | übernommen | Zuordnung über `SSH_USER_AUTH`, strikter Modus, unbekannt heißt Exit 2 (E1, 4.1). Zuvor galt jede andere Adresse als PC (infra/sokrates2/wamo_job_dispatch.py:1027,2923-2926). Der Vorschlag „Match-Block je Quelladresse“ ist zurückgewiesen, er wäre wieder adressbasiert |
| S2, U1 (hoch) | `zugang=server` gesperrt, aber Feld optional | übernommen | Regel 7: nur `zugang=folgt`, vor `lese_geheimnisse()` (wamo_job_dispatch.py:364-370,2362-2369,2406-2411,421-427). Test ohne Feld |
| S3, U12 | Projektordner nicht an Kanal oder Repo gebunden | übernommen | Regel 6, Namensraum `kern-` zustandslos über den Namen, weil ein Marker im Projektordner für die Sandbox beschreibbar wäre (infra/sokrates2/wamo-job.sh:313-318). `check_project` prüfte nur Form und `wamo` (wamo_job_dispatch.py:278-283) |
| S4, U5, B5 | `pruefen` im Kern-Kanal ungeregelt | übernommen | Befehlsliste statt Sperrliste (wamo_job_dispatch.py:304-313,2894-2895), Nein-Probe im Rauchtest und in den Tests |
| S5 | Mengen- und Kostenschranken nur im Kern | teilweise übernommen | Grenzen im Dispatcher (4.3). Zurückgewiesen: „Kanal bei Überschreitung selbst schließen“. Ein Angreifer könnte den Kanal so dauerhaft stilllegen, die Sicherheitswirkung ist mit Ablehnung je Start gleich |
| S6 | Abfluss des Claude-Zugangs, „Kern sieht nie mehr als nötig“ | übernommen | Satz gestrichen, 4.6 nennt das Risiko (voice-core/modellzugang.py:395-425, infra/sokrates2/wamo_core_env.py:40). E11: Limit-Schlüssel bevorzugt, Rotation, Widerrufsstelle vor 3a |
| S7 | Freigabe ohne Beleg außerhalb des Kerns | teilweise übernommen | Akzeptanz als Beobachtung formuliert, Stufe 4 nur im leeren Testrepo. Zurückgewiesen für jetzt: Beleg über den Freigabe-Dienst (F2), er braucht Änderungen an Dispatcher, Freigabe-Dienst und Oberfläche. Pflicht vor Stufe 5 |
| S8, U7, U8, B1, B2, B3 | Vorprüfung widersprüchlich, nicht prüfbar, nicht releasegebunden, Ausfallgefahr | übernommen, schärfer | `wamo-core-vorpruefung.sh` bleibt unverändert, neuer Schalter-Name (E2). Zeile 25 (wamo-core-vorpruefung.sh:25), Lauf als `wamo-core` (:2), `/etc/wamo` root:wamo 0750 (infra/sokrates2/wamo-einrichten.sh:29). Zurückgewiesen als gegenstandslos: harte Prüfung nur bei schwächenden Einstellungen (B1), Marke im Release für die Vorprüfung (U8), Prüfpfad-Parameter (U7), `kern.letzte_gute` setzen (B2, wird nur im Anker-Weg geschrieben, infra/sokrates2/anker/wamo_anker.py:1041, alte Releases ignorieren den Schalter) |
| S9 | Selbstschutz nur über den Namen | übernommen | Regel 8 (Fork-Prüfung), `REPOS` strikt. Eine Kopie als neues Repo bleibt Restrisiko (4.6) |
| S10 | Stufe 3 ohne Zwischenschritt, Token-Löschen zu früh | übernommen | Stufe 3a, Löschen erst vor 3b (S8), PC-Auftrag im App-Modus als Probe (wamo_job_dispatch.py:600-622) |
| S11 | Notaus und Kanal-zu wirken nicht auf bestandene Prüfungen | übernommen | Zweite Prüfung vor dem Token, feste Notaus-Reihenfolge mit zweiter Suche, Aufträge ohne `kanal` nie dem Kern zuordnen (4.1, 4.4) |
| S12, U6 | OpenSSH, `expiry-time`, mehrere Schlüsseldateien unbelegt | übernommen, anderer Ersatz | `ABLAUF` im Dispatcher (4.5) statt eines Timers, der die Datei löscht (zweiter Mechanismus, stiller Ablauf). OpenSSH 9.6 laut infra/sokrates2/README.md:592,865. Prüfung in Abschnitt 8, Nr. 6 |
| S13 | Drop-in beim Spiegeln überschreibbar | übernommen | infra/sokrates2/wamo-core-slots.sh:176-195 gelesen: gleichnamige Datei wird überschrieben. Eigener Name, `schalter an` prüft |
| U2 | Dispatcher-Ist-Stand unbekannt | übernommen | README.md:36, infra/sokrates2/README.md:1020-1026. Stufe 0, Rückweg und S0 angepasst |
| U3 | `DevAgent`-Konstruktor in 19 Aufrufen | übernommen, andere Signatur | `BueroZulassung` wird nur im Büro verlangt. Pflichtparameter hätte alle Aufrufe gebrochen (voice-core/bot.py:613, 5 Testdateien, eval/abnahme_persona.py:446), ein Standard `True` wäre wirkungslos |
| U4 | Test auf Version 6 | übernommen | voice-core/test_sokrates2_dispatch.py:2076 wird angehoben |
| U9 | Aufräumen unvollständig | übernommen | zusätzlich scripts/wamo_ui_sokrates2.py:26, infra/sokrates2/wamo-core-caddy.sh:117, wamo-rd-rueckweg.sh:10,75, wamo-ui-einrichten.sh:21,332 und deren Tests (Grep bestätigt) |
| U10 | Tests zu `wamo-job-einrichten.sh` und Rückweg | übernommen | Probelauf relativ zu `WAMO_JOB_WURZEL`, beide Fälle |
| U11 | Oberfläche nicht testbar | übernommen | reine Funktion `ui/src/lib/auftragStart.ts` mit `node --test` |
| U13, B13, B14 | Rauchtest ohne Installation, Rechte, Eigenschaften der Unit | übernommen | E3, `systemctl show`, kein `.pub` in `~/wamo-infra` (infra/sokrates2/wamo-job-einrichten.sh:57-69) |
| U14 | Aufwand und Dateigröße | übernommen | neue Module `entwicklung_betrieb.py` und `wamo_job_kernkanal.py`, Aufwand je Paket in der Arbeitsliste |
| U15 | PC-Kanal-Folge, `uebergang` | übernommen | E11 nennt die Folge, `uebergang` zählt im Büro wie `keine` ohne Änderung an `boss.py` (voice-core/boss.py:201-220) |
| U16 | Server nicht umgestellt | übernommen | Zweig `umstellen` (Abschnitt 6) |
| U17, B11 | Audit-Folge, verwaiste `running`-Zeilen, Bedienspur | übernommen | E9, `aktionen.jsonl` |
| B4 | Löschen von `WAMO_ABNAHME_BEFEHLE` hält nicht | Problem übernommen, Vorschlag teilweise zurückgewiesen | Schalter `WAMO_ENTWICKLUNG_BEFEHLE_BUERO` im Drop-in. Der Name aus `AUS_ENV` zu nehmen (scripts/wamo_kern_sokrates2.py:74-76,184-198) änderte die Übertragung für alle |
| B6 | Totmannschalter löscht `wamo.conf` | übernommen | infra/sokrates2/wamo-job-einrichten.sh:310-327 gelesen, `vorher` wird nach Erfolg verworfen. Dauerhafte Sicherung, Rückweg-Zeile |
| B7 | `schalter` bei halbem Umschalten | übernommen | E2 („teilweise“, `/proc`-Abgleich in `status`) |
| B8 | Ruhe-Regel, `wamo-egress`, geschlossener Kanal | übernommen | wamo-job-einrichten.sh:255, wamo-egress.service:22, Abschnitt 6, Regel 3 |
| B9, B10, B12 | fehlende Übergangstests, `fail2ban`, Journalzeile | übernommen | Abschnitt 7, Vorbedingung Stufe 1, Log auch bei Schalter aus |

**Geprüfte Behauptungen mit Ergebnis „stimmt nicht“** (Kürzel: Prüfer und Nummer): SB11 „Verwechslung nur in sichere Richtung“ und SB12 „ExposeAuthInfo ungeeignet“ (4.1, der Block gilt nur
für `wamo`, Muster in infra/sokrates2/wamo_pc_token.py:118-135). SB13 und UB11 „`zugang=server` gesperrt“ (Regel 7). SB14 „Kern sieht nie mehr als nötig“ (4.6). UB10 „Dispatcher ist Version 6“ (U2).
UB12 „Tests bleiben bei Version 7 grün“ (U4). UB13 „Konstruktor berührt nur drei Dateien“ (U3). UB21 „sh-Test der Vorprüfung ohne Server“ und BB6 „Vorprüfung kann `core.env` lesen“ (Vorprüfung unverändert).
BB7 „Vorprüfung ans Release gebunden“ (gemeinsame Datei, infra/sokrates2/wamo-core-slots.sh:60, wamo-core-einrichten.sh:109, daher Schalter-Name). BB14 „sechs Befehle“ (sieben, `pruefen`, Befehlsliste).
UB25 „Verstoß gegen Namensvertrag“ stimmt nicht, es bleibt bei der Prüfung vor dem PR (Fremdverweis-Grep, Umlaute, Gedankenstriche). Nicht prüfbar sind SB10, SB29, SB30, SB31, SB32, UB22, UB23, UB24,
BB26 und BB27: Sie stehen in Abschnitt 8 (Nr. 5, 6, 9, 11) beziehungsweise in der Rauchprobe 3a (Kosten bei Abo-Token).
