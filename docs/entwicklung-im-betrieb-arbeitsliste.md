# Arbeitsliste: Entwicklungsaufträge im Betrieb

Begleitpapier zu `docs/entwicklung-im-betrieb.md` (Revision 2, Basis `origin/main` 2a90999, Zweig `feat/entwicklung-im-betrieb`). Die Pakete sind durchnummeriert. **R** heißt Repo-Arbeit,
**S** Server-Rollout (nur mit Verbindung, nur bei Ruhe, nie `systemctl restart wamo-core*`), **N** nur Niko, manuell. Jedes Paket nennt Dateien, gewünschtes Verhalten, Tests, die zuerst rot sein
müssen (TDD), Abnahme, Abhängigkeiten und Aufwand in Stunden (Schätzung, ein Umsetzer).

**Aufteilung für zwei Umsetzer.** Gruppe 1 arbeitet in `voice-core`, `ui` und `scripts`, Gruppe 2 in `infra/sokrates2` einschließlich Dispatcher und Skripten. Jede Gruppe ändert nur Dateien ihrer
eigenen Liste, auch die Testdateien (Zuordnung steht je Paket). Berührungspunkte sind allein die Schnittstellen in Abschnitt 1. Wer Kern und Dispatcher gegeneinander prüfen will, importiert nur lesend
(zum Beispiel `dev_agent.ssh_argv` im Rauchtest-Test der Gruppe 2). Dateien über 800 Zeilen (`dev_agent.py` 1622, `wamo_job_dispatch.py` 2962, `bot.py` 1263, `useWamo.ts` 1319) bekommen nur
dünne Einhängepunkte, neue Logik steht in neuen Modulen.

**Arbeitsweise.** Zuerst die roten Tests schreiben und rot sehen, dann umsetzen. Voice-core-Tests gezielt je Datei aus PowerShell, `uv sync` im voice-core nie, der volle Lauf (etwa 16 Minuten) nur
einmal am Ende und nicht parallel zu Builds. Vor jedem Commit: der Fremdverweis-Grep aus der Projektregel (die Wortliste steht außerhalb des Repos und nicht in diesem Papier) auf Repo und Commit-Text, Secret-Scan, keine Attributionszeilen. Deutsche Texte mit
echten Umlauten und ohne Gedankenstriche, Bezeichner, Pfade und Env-Namen in ASCII. Keine Migration, kein SQL.

## 1. Schnittstellen zwischen den Gruppen

Diese Festlegungen gelten für beide Gruppen und werden nicht einseitig geändert. Eine Abweichung wird im Vertragstest (Paket R13) sichtbar.

**I1 Dateien und Pfade**

| Name | Wert | Schreibt | Liest |
| --- | --- | --- | --- |
| Schlüsselzeile Kern | `/etc/ssh/authorized_keys.d/wamo-kern`, genau eine Zeile `restrict,from="192.168.178.136",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 <Schlüssel> wamo-kern-jobs`, root:root 0644, ohne `expiry-time` | R10 | R7 |
| PC-Datei (unverändert) | `/etc/ssh/authorized_keys.d/wamo` | `wamo-job-einrichten.sh` | R7 |
| sshd-Block | in `/etc/ssh/sshd_config.d/wamo.conf` unter `Match User wamo` die Zeilen `AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u /etc/ssh/authorized_keys.d/%u-kern` und `ExposeAuthInfo yes` | R9 | sshd |
| Regeldatei | `/etc/wamo/kernkanal.conf`, root:wamo 0640, Zeilen `SCHLUESSEL=wert`, Kommentarzeilen mit `#` | R10 | R7 |
| Sperrdatei Notaus | `/etc/wamo/kernkanal.notaus`, root:wamo 0640, Existenz zählt, Inhalt ist freier Text ohne Wert | R10 | R7 |
| Drop-in | `/etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf`, root:root 0644, ohne Spiegel-Marke | R10 | systemd, R1 (über die Umgebung) |
| Dispatcher-Modul | `infra/sokrates2/wamo_job_kernkanal.py`, installiert nach `/opt/wamo/waechter/wamo_job_kernkanal.py` (root:wamo 0640) | R7, R9 (Installation) | R8 |
| Zähler | `/srv/wamo/logs/kernkanal.jsonl`, je Zeile `{"t": <epoch>, "job": "<id>", "ereignis": "start" oder "ende", "dauer_s": <int, nur bei ende>}` | R8 | R7 |
| Sicherungen, Bedienspur | `/var/backups/wamo-job-kern/<JJJJMMTT-HHMMSS>/`, `/var/log/wamo-job-kern/aktionen.jsonl` (`{"t":..., "aktion":"schluessel\|kanal\|schalter\|notaus", "ergebnis":"ok\|fehler"}`) | R10 | R10 |
| Rauchtest | `infra/sokrates2/wamo_job_kern_rauchtest.py`, installiert nach `/opt/wamo-core/bin/wamo-job-kern-rauchtest` (root:root 0755) | R10 | R10 |
| Skripte | `infra/sokrates2/wamo-job-kern-einrichten.sh` mit `schluessel [--trocken] [--ablauf JJJJMMTT]`, `kanal --arten A,B --repos O/N[,O/N] [--dienste D] [--max-turns N] [--max-budget-usd X]`, `kanal zu`, `schalter an\|aus <release> [--profil <user_id>] [--kosten-auftrag X] [--kosten-tag X]`, `notaus-aufheben`, `status`; `infra/sokrates2/wamo-job-kern-rueckweg.sh` mit `notaus`, `--entfernen`, `--trocken` | R10 | `scripts/wamo_kern_sokrates2.py verteilen` (R6) |

Zusätzlich liefert `verteilen` (R6) alle Dateien, die `wamo-job-einrichten.sh` verlangt: `wamo_job_kernkanal.py`, `kontingent.py`, `abnahme_befehl.py`, die drei neuen Kern-Weg-Dateien und `wamo_job_pruefen.py`.

**I2 `kernkanal.conf`: Schlüssel, Standard und Obergrenzen** (der Dispatcher lehnt jeden Wert außerhalb des Bereichs und jeden unbekannten oder doppelten Schlüssel mit `konfig_ungueltig` ab)

| Schlüssel | Standard beim Anlegen | Bereich |
| --- | --- | --- |
| `KANAL` | `zu` | `zu` oder `offen` |
| `ABLAUF` | heute plus 30 Tage (`JJJJMMTT`) | Pflicht, höchstens 400 Tage voraus |
| `ARTEN`, `REPOS`, `DIENSTE` | leer | Komma-Liste, `REPOS` als `owner/name` (A-Z, a-z, 0-9, `.`, `_`, `-`), höchstens 10, kein Name `wamo`, `DIENSTE` nie `supabase` |
| `MAX_TURNS` | 40 | 1 bis 200 |
| `MAX_BUDGET_USD` | 2.00 | 0.01 bis 20 (Punkt als Dezimaltrenner, höchstens zwei Stellen) |
| `MAX_START_STUNDE`, `MAX_START_TAG` | 4, 8 | 1 bis 60, 1 bis 200 |
| `MAX_LAUFZEIT_TAG_S`, `MAX_FOLGEN` | 14400, 5 | 60 bis 86400, 0 bis 20 |
| `MIN_ABSTAND_S`, `MIN_PLATZ_MB` | 60, 2048 | 0 bis 3600, 100 bis 1000000 |

**I3 Kern liest (Drop-in schreibt)**: `WAMO_ENTWICKLUNG_BUERO` (`an` oder `aus`, Standard `aus`), `WAMO_ENTWICKLUNG_ORT` (`sokrates2`), `WAMO_ENTWICKLUNG_PROFILE` (eine oder mehrere `user_id`
als UUID, durch Komma getrennt, im Büro Pflicht), `WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD` und `WAMO_ENTWICKLUNG_KOSTEN_TAG_USD` (positive Dezimalzahl), `WAMO_ENTWICKLUNG_BEFEHLE_BUERO` (`an` oder `aus`,
Standard `aus`). `WAMO_ENTWICKLUNG` bleibt `aus` in den Units und wird im Büro ignoriert. Release-Nachweis: `voice-core/entwicklung_betrieb.py` enthält genau eine Zeile `SCHRANKEN_VERSION = 1`, `schalter` prüft sie
mit `grep -Eq '^SCHRANKEN_VERSION = [1-9][0-9]*$'` im Zielrelease (`/opt/wamo-core/releases/<release>/voice-core/entwicklung_betrieb.py`).

**I4 Aufruf Kern zum Dispatcher** (unverändert gegenüber heute, voice-core/dev_agent.py:1193-1220): `start <job> <projekt> <art> [<session>]` über `ssh`, erste stdin-Zeile als Kopf
`{"auftrag", "system", "modell"?, "repo"?, "zugang", "profil"}`, bei `zugang` gleich `folgt` eine zweite Zeile mit dem Zugang. Im Kern-Kanal sind `zugang=folgt` und `profil` Pflicht, bei GitHub-Arten auch `repo`. Weitere
Kopf-Felder gibt es nicht (der Dispatcher kennt nur `auftrag`, `system`, `modell`, `repo`, `zugang`, `profil`).

**I5 Projektname** (Kern leitet ab, Dispatcher erzwingt, beide mit derselben Regel): `kern-` plus Name. Name aus dem Repo (Teil nach dem `/`) oder, bei Arten ohne Repo, aus dem Projektwort des Modells: klein schreiben,
`.` und `_` durch `-` ersetzen, jede andere Zeichenfolge außerhalb `a-z0-9-` verwerfen, mehrere `-` zu einem, `-` am Rand streichen. Gesamtlänge 3 bis 50 Zeichen. Mit gesetztem Repo muss das Projekt genau `kern-<abgeleiteter Name>`
sein. Der PC-Kanal darf Namen mit `kern-` nicht öffnen. Wertetabelle (beide Seiten testen dieselben Zeilen):

| Eingabe | Ergebnis |
| --- | --- |
| Repo `Acme/Test-Repo` | `kern-test-repo` |
| Repo `acme/my_repo.js` | `kern-my-repo-js` |
| Repo `acme/a__b` | `kern-a-b` |
| Repo `acme/-x-` | `kern-x` |
| ohne Repo, Wort `Marktstudie` | `kern-marktstudie` |
| Repo `acme/wamo` oder `acme/WAMO` | abgelehnt (`repo`) |
| Repo mit Name von 46 Zeichen oder mehr | abgelehnt (`projekt`, länger als 50 mit Präfix) |
| Projekt `test-repo` aus dem Kern-Kanal | abgelehnt (`projekt`, Präfix fehlt) |
| Projekt `kern-test-repo` aus dem PC-Kanal | abgelehnt |

**I6 Ereignisse Dispatcher zum Kern.** Eine Zeile `{"type": "wamo", "ereignis": "kanal_gesperrt", "grund": "<Code>"}` (Format wie `wamo_event`, infra/sokrates2/wamo_job_dispatch.py:1776-1777), danach Exit 2
(`EXIT_ABGEWIESEN`). Feste Liste der Grund-Codes: `notaus`, `konfig_ungueltig`, `kanal_zu`, `abgelaufen`, `befehl`, `fremd`, `art`, `repo`, `repo_fehlt`, `dienst`, `projekt`, `zugang`, `fork`, `herkunft_unklar`,
`grenze_start`, `grenze_laufzeit`, `grenze_folgen`, `abstand`, `platz`. Der Dispatcher hält sie als Tupel `KANAL_GRUENDE` in `wamo_job_kernkanal.py`, der Kern als Schlüssel von `GRUND_SAETZE` in `entwicklung_betrieb.py`
(je ein kurzer, sprechbarer deutscher Satz ohne Pfade und Werte). Der Kern bildet `kanal_gesperrt` in `REMOTE_EREIGNIS_STATUS` auf „nicht gestartet“ mit dem Satz ab.

**I7 `ping`.** Neue Felder `kanal` (`lokal`, `pc` oder `kern`) und `kernkanal` als Objekt `{"strikt": bool, "offen": bool, "grund": <Code oder null>, "arten": [..], "repos": [..], "ablauf": "JJJJMMTT" oder null,
"tage_rest": <int oder null>}`. `version` ist `"7"`. Nie ein Wert aus `geheim.env`.

**I8 `auftrag.json` und Log.** Neues Feld `kanal` (`kern` oder `pc`) beim Anlegen. Logzeilen tragen `kanal=<lokal|pc|kern>`. Fehlt das Feld, gilt der Auftrag als nie vom Kern.

**I9 Exit-Codes der Skripte.** `wamo-job-kern-einrichten.sh` und `wamo-job-kern-rueckweg.sh`: 0 ok, 1 Fehler, 2 falsche Aufrufform, 3 Vorbedingung nicht erfüllt (OpenSSH, `fail2ban`, Release-Nachweis, Rauchtest, Ruhe), 4 teilweise
umgesetzt (`schalter` nach schon erfolgtem Umschalten). `wamo_job_kern_rauchtest.py`: 0 alle Proben bestanden, 1 eine Probe fehlgeschlagen, 2 Umgebung ungeeignet. Die Ausgabe nennt nie Werte aus Geheimnisdateien.

**I10 Notaus-Reihenfolge** (R10 setzt sie um, R7 reagiert darauf): Sperrdatei anlegen, Schlüsselzeile sichern und löschen, Aufträge mit `kanal=kern` per lokalem `cancel` beenden, erneut suchen, protokollieren.

## 2. Gruppe 1: voice-core, ui und scripts

Reihenfolge in der Gruppe: R1, R2, R3, dann R4 und R5 nebeneinander, R6 jederzeit. Typ R für alle.

**R1 Büro-Schalter und Schranken im Kern** (6 h, abhängig von nichts)

- Dateien: `voice-core/entwicklung_betrieb.py` (neu), `voice-core/dev_agent.py`, `voice-core/bot.py`, `voice-core/conftest.py`, `voice-core/test_entwicklung_betrieb.py` (neu), `voice-core/test_smoke_bot.py`, `voice-core/test_boss_bot.py`.
- Verhalten:
  - `entwicklung_betrieb.py` enthält `SCHRANKEN_VERSION = 1`, `buero_problem()` (Grund als Text oder `None`) und die Klasse `BueroZulassung` (unveränderlich, `user_id`, Kurzkennung aus 8 Hexzeichen). `entwicklung_enabled()` in `dev_agent.py` liest im Büro (`pc_host.buero()`) nur `WAMO_ENTWICKLUNG_BUERO` und ignoriert `WAMO_ENTWICKLUNG`, außerhalb des Büros bleibt alles wie bisher.
  - `buero_problem()` meldet: `ORT=lokal`, fehlende oder ungültige `WAMO_ENTWICKLUNG_PROFILE`, ungültige Kostengrenzen (I3), unlesbarer Schlüssel oder unlesbare known_hosts (erweitertes `ssh_problem()`, ohne Pfade im Text). Jeder Fall schaltet nur die Entwicklung aus, nie wirft er beim Start des Kerns.
  - `serverzugang_erlaubt()` liefert im Büro immer `False`. `_remote_befehle` nutzt `WAMO_ABNAHME_BEFEHLE` im Büro nur bei `WAMO_ENTWICKLUNG_BEFEHLE_BUERO=an`.
  - `bot.build_session`: Profilbindung (Admin außerhalb von `WAMO_ENTWICKLUNG_PROFILE` bekommt keine Entwicklung), Quelle `uebergang` zählt im Büro wie `keine`, eine Logzeile `Entwicklungsaufträge aus: <Grund>` je Sitzung im Büro, auch bei Schalter aus. Die Statusnachricht bekommt für Admins das optionale Feld `entwicklung_grund` (Text, höchstens 120 Zeichen, nie für Mitglieder).
  - `conftest.py` neutralisiert alle neuen Variablen.
- Rote Tests zuerst: `lokal` im Büro ergibt Grund. Ungültige Werte jeder neuen Variable schalten ab. Admin außerhalb des Profils bekommt keinen DevAgent, ein Mitglied nie. `uebergang` ergibt aus. Im Büro kein Rückfall auf `WAMO_CLAUDE_SCHLUESSEL_PROFIL`. Unlesbarer Schlüssel ergibt ein Problem. `WAMO_ENTWICKLUNG=an` allein wirkt im Büro nicht. Außerhalb des Büros bleiben alle bisherigen Tests unverändert grün.
- Abnahme: Tests grün, Betriebsart Unterwegs unverändert, `python-reviewer` ohne CRITICAL oder HIGH.

**R2 Zulassung im DevAgent** (3 h, abhängig von R1)

- Dateien: `voice-core/dev_agent.py`, `voice-core/bot.py`, `voice-core/test_entwicklung_betrieb.py`, `voice-core/test_dev_agent_sokrates2.py`.
- Verhalten: Der Konstruktor bekommt das Schlüsselwort `zulassung: BueroZulassung | None = None`. Im Büro (`pc_host.buero()`) wirft er ohne Zulassung, außerhalb bleibt die Signatur wirksam ohne den Wert. `bot.py:613` übergibt die Zulassung aus der geprüften Identität (Admin, Profil). `_vorbereiten`, `_auftrag_start` und `_freigabe` prüfen im Büro erneut und protokollieren Versuche ohne Zulassung (Profilkürzel, kein Wert).
- Rote Tests zuerst: Konstruktor im Büro ohne Zulassung wirft. Eine Client-Nachricht `auftrag_start` ohne Zulassung startet keinen ssh-Prozess. Eine Freigabe ohne Zulassung ruft nie `approve`. Alle 19 bestehenden Aufrufe (`test_dev_agent_sokrates2.py`, `test_dev_abnahme.py`, `test_teilen.py`, `test_vorgaenge_dev.py`, `test_werkzeugkarte.py`, `eval/abnahme_persona.py`) bleiben unverändert grün.
- Abnahme: dieselben Tests grün, `security-reviewer` ohne CRITICAL oder HIGH.

**R3 Repo-Pflicht, Projektname, Quittung, Grund-Codes** (6 h, abhängig von R1)

- Dateien: `voice-core/dev_agent.py`, `voice-core/entwicklung_betrieb.py`, `voice-core/persona.py`, `voice-core/test_dev_agent_sokrates2.py`, `voice-core/test_persona.py`, `voice-core/test_entwicklung_betrieb.py`.
- Verhalten:
  - Im Büro verlangt `_vorbereiten` für Arten mit GitHub-Stufe ein Repo und lehnt sonst mit einem Satz ab. Das Projekt wird nach I5 aus dem Repo abgeleitet (Funktion `kern_projektname(repo, wort)` in `entwicklung_betrieb.py`), die Werkzeugbeschreibung in `persona.py` nennt die Repo-Pflicht.
  - `_auftrag_start` antwortet immer mit `{"type": "auftrag_quittung", "job": "<id>", "ok": true}` oder `"ok": false` mit `"grund": "<Satz>"` (Problem, belegt, `kanal_gesperrt`, ssh-Fehler). Die Karte bleibt bei einem Grund bestehen.
  - `GRUND_SAETZE` (I6) für jeden Code, `kanal_gesperrt` in `REMOTE_EREIGNIS_STATUS`. Exit 255 von ssh wird aus stderr klassifiziert (Hostschlüssel, Schlüssel abgelehnt, nicht erreichbar) ohne Pfade und Werte.
  - `update_action` auf `approved` erst nach Code 0 von `approve`. Scheitert `approve`, bleibt die Freigabe `pending`, und der zweite Klick wird verbucht.
- Rote Tests zuerst: `analyse` ohne Repo im Büro abgelehnt, `recherche` ohne Repo erlaubt. Die Wertetabelle aus I5 (alle Zeilen). Quittung bei Problem, belegt, `kanal_gesperrt` und Erfolg. Jeder Code aus I6 hat einen Satz. Scheitert `approve`, bleibt die Freigabe offen. Repo `wamo` bleibt gesperrt (`SELF_REPO` unverändert).
- Abnahme: Tests grün, `persona.py` nennt die Repo-Pflicht ohne Markdown und kurz.

**R4 Audit und Kostenbremse über `actions`** (6 h, abhängig von R3)

- Dateien: `voice-core/memory.py`, `voice-core/dev_agent.py`, `voice-core/entwicklung_betrieb.py`, `voice-core/test_memory.py`, `voice-core/test_integration_db.py`, `voice-core/test_entwicklung_betrieb.py`.
- Verhalten:
  - `memory.py` bekommt `aktion_abschliessen(action_id, status, result)` (setzt Status, `result`, `finished_at` in einem Schritt nach dem Automaten), `tageskosten(user_id)` (Summe `kosten_usd` der Zeilen `entwicklungsauftrag` des Tages in Europe/Berlin) und `veraltete_schliessen(user_id, nach_stunden=2)` (setzt alte `running`-Zeilen auf `failed` mit Grund „abgelaufen“). Kein neues Grant, keine Migration (supabase/migrations/0001_wamo_init.sql:343-346).
  - Je Auftrag eine Zeile `entwicklungsauftrag`: `pending` und `approved` beim Klick, `running` bei Übergabe an ssh, `done` oder `failed` mit `result` (Job, Art, Projekt, Repo, Status, Dauer, Kosten, Runden). Ein abgewiesener Start geht über `running` nach `failed`. Risiko `read` oder `change` nach GitHub-Stufe. `params` und `result` enthalten nie einen Zugangswert.
  - Auftragsgrenze (`WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD`) vor jedem weiteren Lauf, Tagesgrenze (`..._KOSTEN_TAG_USD`) vor dem Start, veraltete `running`-Zeilen zählen mit der Auftragsgrenze. Ist die Datenbank nicht erreichbar, startet im Büro nichts, mit Quittung. Logzeilen für Start und Ende mit Profilkürzel.
- Rote Tests zuerst: Integrationstest der Folge `pending`, `approved`, `running`, `done` mit `result`. Abgewiesener Start endet als `failed` mit Grund. Tagesgrenze verhindert den Start mit Satz, Auftragsgrenze den nächsten Lauf. Eine veraltete `running`-Zeile wird geschlossen und gezählt. Datenbank nicht erreichbar heißt kein Start. Ein Dummy-Zugangswert kommt in keiner Zeile, keinem `result` und keinem Log vor.
- Abnahme: Tests grün, `database-reviewer` nicht nötig (keine Migration), `python-reviewer` ohne CRITICAL oder HIGH.

**R5 Oberfläche: Repo, Quittung, Grund, Warnung** (6 h, abhängig von R1 und R3)

- Dateien: `ui/src/lib/nachrichten.ts`, `ui/src/lib/useWamo.ts`, `ui/src/lib/auftragStart.ts` (neu), `ui/src/lib/auftragStart.test.ts` (neu), `ui/src/lib/auftragNachricht.test.ts`, `ui/src/components/Freigaben.tsx`, `ui/src/components/Statusleiste.tsx`.
- Verhalten: Die Entscheidungslogik (Quittung, Frist von 15 Sekunden, Kartenzustand) steht als reine Funktion in `auftragStart.ts`. `nachrichten.ts` übernimmt `repo` der Startkarte, `auftrag_quittung` und `entwicklung_grund`. `useWamo.ts` meldet „gestartet“ erst nach `ok: true`, behält die Karte bei `ok: false` mit dem Grund und zeigt nach 15 Sekunden ohne Quittung einen neutralen Hinweis (verträgt einen Kern ohne Quittung). Die Statusleiste zeigt den Grund nur Admins. Eine Warnung vor dem Neuladen gilt, solange ein Auftrag läuft. WMC-Design (Rot `#F40E0E`, Grautöne, kein Blau).
- Rote Tests zuerst (`node --test`): Zustand nach Quittung ok, nach Quittung mit Grund, ohne Quittung nach 14 und 16 Sekunden. Fehlendes `repo` bricht die Anzeige nicht. Die Karte bleibt bei Ablehnung stehen.
- Abnahme: Tests und Typprüfung grün, `react-reviewer` ohne CRITICAL oder HIGH.

**R6 Aufräumen in `scripts/` und Prüfung der Umgebung** (3 h, abhängig von nichts)

- Dateien: `scripts/wamo_kern_sokrates2.py`, `scripts/wamo_ui_sokrates2.py`, `scripts/test_wamo_ui_sokrates2.py`, `voice-core/test_env_schalter.py`.
- Verhalten: `verteilen` liefert zusätzlich die Dateien aus I1 (Zusatzliste). `neustart` und `alles` verweigern mit Hinweis auf `umschalten-kern`, sobald ein Slot läuft (Abfrage über ssh, nur lesend). `status` zeigt Slots und Schalter (nur Namen). Der Kommentar in Zeile 72-73 (Befehlskriterien „ohne Wirkung auf Sokrates-2“) wird korrigiert. `scripts/wamo_ui_sokrates2.py` passt den Hilfetext zu `--neustart` an. Das Skript `wamo-ui-einrichten.sh --neustart` selbst ändert R11 (Schnittstelle I11: im Slot-Betrieb Hinweis und Exit 0).
- Rote Tests zuerst: `AUS_ENV` und `ERLAUBT` enthalten kein `WAMO_ENTWICKLUNG*`. `verteilen` liefert alle Dateien, die `wamo-job-einrichten.sh` verlangt. `neustart` und `alles` verweigern bei laufendem Slot (ssh als Attrappe). Der Befehl in `scripts/test_wamo_ui_sokrates2.py:400` bleibt gültig.
- Abnahme: Tests grün, Grep nach `systemctl (restart|start) wamo-core` in `scripts` liefert nur bewusste Treffer.

**I11 (Ergänzung zu den Schnittstellen)**: `wamo-ui-einrichten.sh alles --neustart` und `wamo-rd-einrichten.sh kern` starten im Slot-Betrieb nichts neu, sie melden den Hinweis „`umschalten-kern` nutzen“ und enden mit Exit 0.

## 3. Gruppe 2: infra/sokrates2 mit Dispatcher und Skripten

Reihenfolge in der Gruppe: R7 zuerst, dann R8 und R9 nebeneinander, R10 nach R7 bis R9, R11 und R12 jederzeit. Typ R für alle.

**R7 Kern-Kanal-Modul des Dispatchers** (8 h, abhängig von nichts, I1 bis I8)

- Dateien: `infra/sokrates2/wamo_job_kernkanal.py` (neu), `voice-core/test_kernkanal.py` (neu).
- Verhalten: Reine Funktionen ohne Seiteneffekt beim Import, alle Pfade und die Uhr einspeisbar. `kanal_bestimmen(umgebung, pfade)` setzt die Falltabelle aus 4.1 um (lokal, nicht strikt, strikt, Kern, PC, unklar). Es liest `SSH_USER_AUTH` (Eigentümer, nicht für andere schreibbar, Schlüsselteil der Zeile `publickey ssh-ed25519 ...`) und vergleicht den Schlüsselteil mit `wamo-kern` und `wamo`, tolerant gegenüber Optionen und Kommentar. `konfig_lesen(pfad)` prüft Eigentümer (root), Rechte, unbekannte und doppelte Schlüssel und Werte nach I2. `pruefe_befehl` und `pruefe_start` liefern `None` oder einen Code aus `KANAL_GRUENDE` in der Reihenfolge der Regeln 1 bis 9. Dazu `kern_projektname` (I5), `fork_pruefen(antwort)` (Regel 8) und die Zähler für `MAX_START_STUNDE`, `MAX_START_TAG`, `MAX_LAUFZEIT_TAG_S`, `MAX_FOLGEN`, `MIN_ABSTAND_S`, `MIN_PLATZ_MB` über `kernkanal.jsonl`.
- Rote Tests zuerst: Kern-Schlüssel mit und ohne `from=` ergibt `kern`. PC-Schlüssel ergibt `pc`. Strikter Modus mit fremdem Schlüssel, fehlendem `SSH_USER_AUTH`, fremdem Eigentümer oder Lesefehler ergibt `herkunft_unklar`. Ohne Kern-Schlüssel und ohne Konfiguration ergibt jeder Aufruf `pc`. Ohne SSH-Variablen ergibt `lokal`. Eine Datei in beiden Listen ergibt `kern`. Jeder Konfigurationsfehler (fremder Eigentümer, schreibbar, unbekannter oder doppelter Schlüssel, Wert außerhalb des Bereichs, `wamo` in `REPOS`, `supabase` in `DIENSTE`) ergibt `konfig_ungueltig`. Jeder Code von 1 bis 9 mit eigener Eingabe, in der Reihenfolge. `zugang` fehlt oder `server` ergibt `zugang`. Wertetabelle I5. Fork, Vorlage und Ursprung `wamo` und eine unklare Antwort ergeben `fork`. Grenzen greifen am Rand (Start Nummer `MAX` und `MAX + 1`).
- Abnahme: alle Tests grün, das Modul hat keinen Zugriff auf Geheimnisse, `security-reviewer` und `python-reviewer` ohne CRITICAL oder HIGH.

**R8 Dispatcher Version 7: Einhängepunkte** (6 h, abhängig von R7)

- Dateien: `infra/sokrates2/wamo_job_dispatch.py`, `voice-core/test_sokrates2_dispatch.py`, `voice-core/test_sokrates2_pruefen.py` (nur, wo `ping` oder die Version vorkommt).
- Verhalten: `VERSION = "7"`. `main()` bestimmt den Kanal vor dem Handler, bei `kern` gilt die Befehlsliste. `cmd_start` ruft nach `parse_header` und vor `lese_geheimnisse()` die Regeln aus R7, bei einem Verstoß `wamo_event("kanal_gesperrt", grund=...)` und Exit 2 (I6). Eine zweite Prüfung steht unmittelbar vor der Token-Erzeugung (nach dem Selbsttest). `vorbereiten` schreibt `kanal` in `auftrag.json`, die Logzeilen tragen `kanal=`, `ping` meldet `kanal` und `kernkanal` (I7). Im Kern-Kanal gehen `--max-turns` und `--max-budget-usd` in den Claude-Aufruf (Zeile 2460-2463), die Werte kommen nur aus `kernkanal.conf`. Start und Ende schreiben Zeilen in `kernkanal.jsonl`. Der PC-Kanal lehnt Projekte mit Präfix `kern-` ab. `SELF_REPO` und `PARALLEL_MAX` bleiben. Bei der ersten Token-Erzeugung je Lauf im Kern-Kanal fragt der Dispatcher die Repo-Metadaten ab und ruft `fork_pruefen` auf.
- Rote Tests zuerst: Der Test auf `version == "6"` (Zeile 2076) wird auf `"7"` angehoben, `ping` trägt die neuen Felder. Start aus dem Kern-Kanal ohne Feld `zugang` endet mit Exit 2, und ein Zähler im Test zeigt, dass `lese_geheimnisse()` nicht aufgerufen wurde. `pruefen` aus dem Kern-Kanal wird abgewiesen, auch mit `WAMO_PRUEFEN=an`. Fremde Auftrags-ID und Auftrag ohne `kanal` werden bei `status`, `pending`, `approve` und `cancel` abgewiesen. Notaus-Datei lässt nur `ping` und `status` zu. Ein Start, der die erste Prüfung besteht, scheitert an der zweiten, wenn die Notaus-Datei dazwischen entsteht. Der Aufruf von Claude enthält die zwei Flags nur im Kern-Kanal. PC-Kanal: alle bisherigen Tests bleiben unverändert grün (außer der Versionszeile).
- Abnahme: `wamo_job_rauchtest.py` bleibt grün (Attrappe), Abweisung erfolgt, bevor ein GitHub-Token existiert, `security-reviewer` ohne CRITICAL oder HIGH.

**R9 sshd-Block, Totmannschalter, Rückweg-Sperre** (4 h, abhängig von R7 für den Namen des Moduls)

- Dateien: `infra/sokrates2/wamo-job-einrichten.sh`, `infra/sokrates2/wamo-job-rueckweg.sh`, `voice-core/test_sokrates2_pruefen.py` (Rückweg-Teile), `voice-core/test_sokrates2_dispatch.py` (Zeile 1665-1666 liest das Skript), `voice-core/test_job_kern_skripte.py` (neu, gemeinsam mit R10, getrennte Testklassen).
- Verhalten: Der Match-Block bekommt `ExposeAuthInfo yes` und die zweite `AuthorizedKeysFile` (I1). Die vorige `wamo.conf` wird dauerhaft nach `/var/backups/wamo-job-kern/<Zeit>/wamo.conf.vorher` gesichert, der Totmannschalter stellt sie wieder her und lädt `ssh` neu (nur wenn es keine vorige Fassung gab, löscht er die Datei). `sshd -t` und die Globalprobe bleiben. Das Skript installiert `wamo_job_kernkanal.py` nach `/opt/wamo/waechter/` (Liste in Zeile 7-8, 70-75, 146, 184-188) und bricht ohne Ruhe (`ps -u wamo` zeigt Aufträge oder ein Totmannschalter ist offen) mit Exit 3 ab, im Probelauf mit Wurzel nicht. Die Hinweise nennen den Alias `wamo-sokrates2`. Das Skript schreibt nie in `authorized_keys.d/wamo-kern`. `wamo-job-rueckweg.sh` verweigert, solange `$WURZEL/etc/ssh/authorized_keys.d/wamo-kern` existiert.
- Rote Tests zuerst: Textprüfung (beide Pfade und `ExposeAuthInfo` im Match-Block, Globalprobe unverändert, kein Schreibzugriff auf `wamo-kern`, Totmann stellt die Sicherung wieder her, Ruhe-Prüfung vorhanden). Rückweg im Probelauf: mit Datei Abbruch mit Meldung, ohne Datei läuft er wie bisher.
- Abnahme: alle bestehenden Tests zu diesen Skripten grün.

**R10 Skripte für den Kern-Weg und Rauchtest** (10 h, abhängig von R7, R8, R9)

- Dateien: `infra/sokrates2/wamo-job-kern-einrichten.sh`, `infra/sokrates2/wamo-job-kern-rueckweg.sh`, `infra/sokrates2/wamo_job_kern_rauchtest.py` (alle neu), `voice-core/test_job_kern_skripte.py`.
- Verhalten (Aufrufformen und Exit-Codes nach I1 und I9):
  - `schluessel`: Vorbedingungen (OpenSSH-Version lesen, `/usr/bin/ssh`, `fail2ban` mit `ignoreip` oder `ignoreself` für 192.168.178.136 und 127.0.0.1 belegt oder nicht aktiv, Ruhe), Ordner 0700, `ssh-keygen` per `runuser` nur wenn der Schlüssel fehlt, known_hosts aus dem lokalen Hostschlüssel mit Abgleich gegen `--hostfingerabdruck` (Wert aus scripts/wamo-tunnel-einrichten.ps1:44), Datei `wamo-kern` (Zeilenform genau I1, den öffentlichen Schlüssel liest nur root aus `/var/lib/wamo-core/.ssh`), `kernkanal.conf` mit Standard und `KANAL=zu`, Sicherung, Installation des Rauchtests (nach `pruefe_herkunft`), Ja-Probe `ping`, bei Fehler Rücknahme der Zeile. `--trocken` prüft nur und schreibt nichts.
  - `kanal`: prüft Werte nach I2, verweigert die Öffnung für GitHub-Arten, solange `claude --help` (als `wamo`) eines der Flags `--max-turns` und `--max-budget-usd` nicht kennt, ändert Regeln nur ohne laufenden Kern-Auftrag (`kanal zu` immer), schreibt atomar mit Sicherung.
  - `schalter an|aus <release>`: Vorabprüfungen nach E2 (Schlüssel, known_hosts, Rauchtest, `SCHRANKEN_VERSION` im Zielrelease, kein `WAMO_ENTWICKLUNG*` in core.env über `wamo-core-env liste`, keine gleichnamige Datei in `wamo-core.service.d`, Ruhe), Drop-in schreiben, `daemon-reload`, `wamo-core-slots.sh umschalten-kern <release>`. Scheitert das, wird das vorige Drop-in wiederhergestellt, oder bei schon erfolgtem Umschalten (Abfrage über `wamo-core-slots.sh status`) bleibt es und das Skript endet mit Exit 4. Gleicher Stand ist ein No-op. `status`: Resttage, Kanalzustand, Schalter je Slot aus `/proc/<MainPID>/environ` (nur Name und Schalterwert), laufende Kern-Aufträge, Zahl der Kern-Ordner, jede Abweichung. `notaus-aufheben` nach 4.4.
  - `wamo-job-kern-rueckweg.sh`: `notaus` nach I10, `--entfernen` verweigert bei vorhandenem Drop-in und entfernt sonst Schlüssel, known_hosts, `kernkanal.conf` und Sperrdatei, `--trocken`.
  - Jede Bedienhandlung schreibt eine Zeile in `aktionen.jsonl` und per `logger -t wamo-job-kern`, nie Werte. Die Skripte enthalten kein `systemctl restart`, `start` oder `stop`. Probelauf mit `WAMO_JOB_KERN_WURZEL` ohne root.
  - Rauchtest: Eigenschaften der Unit aus `systemctl show wamo-core@<slot>` (Muster infra/sokrates2/wamo_pc_rauchtest.py:133-177), Aufruf als `wamo-core`, `--ping` (Ja-Probe) und `--produktiv` (alle Ja- und Nein-Proben, Übergangsproben und Dateirechte nach Abschnitt 7 des Planpapiers). Die ssh-Optionen sind die von `dev_agent.ssh_argv`.
- Rote Tests zuerst: Zeilenform genau, ohne `expiry-time`. Probelauf legt alle Dateien mit richtigen Modi an, ein zweiter Lauf ändert nichts. `authorized_keys.d/wamo` bleibt unverändert (Prüfsumme). Kein `.pub` entsteht unter `~/wamo-infra`. Kein `systemctl restart|start|stop` im Text. `schalter` nimmt das Drop-in bei gescheitertem Umschalten zurück, meldet „teilweise“ mit Exit 4, ist beim zweiten Aufruf ein No-op. `--entfernen` verweigert bei vorhandenem Drop-in. `notaus` hält die Reihenfolge ein (Sperrdatei zuerst, Probelauf mit Attrappen). Die ssh-Optionen des Rauchtests gleichen `dev_agent.ssh_argv` (lesender Import). Ohne `--hostfingerabdruck` bricht `schluessel` mit Exit 2 ab.
- Abnahme: Probelauf-Tests grün, Ja- und Nein-Proben sind als „nur am Server prüfbar“ gekennzeichnet (S3), `security-reviewer` ohne CRITICAL oder HIGH.

**R11 Aufräumen der Altpfade in `infra/sokrates2`, Vorprüfung unverändert** (5 h, abhängig von nichts, I11)

- Dateien: `infra/sokrates2/wamo-core-einrichten.sh` (Zeile 150), `wamo-rd-einrichten.sh` (416, 423), `wamo-ui-einrichten.sh` (21, 332), `wamo-ui-rueckweg.sh` (80-87), `wamo-rd-rueckweg.sh` (10, 75), `wamo-core-caddy.sh` (117), `wamo_core_env.py` (nur Docstring 16-17 und Kommentar 59-61), `voice-core/test_kern_slots.py`, `voice-core/test_rd_skripte.py`.
- Verhalten: Im Slot-Betrieb startet keines dieser Skripte die alte Unit neu. Sie melden den Hinweis auf `umschalten-kern` und enden mit Exit 0 (I11). `wamo-core-caddy.sh` nennt `umschalten-kern` statt `systemctl start wamo-core`. `wamo-core-slots.sh`, `wamo-core-vorpruefung.sh` und `wamo-core@.service` bleiben unverändert.
- Rote Tests zuerst: Ein Grep nach `systemctl (restart|start) wamo-core` in `infra` und `scripts` liefert nur bewusste Treffer (die Liste steht im Test). `wamo-core-vorpruefung.sh` enthält die Zeile `[ "${WAMO_ENTWICKLUNG:-aus}" = aus ]` und kein `WAMO_ENTWICKLUNG_BUERO`, die Unit enthält `WAMO_ENTWICKLUNG=aus` und kein `WAMO_ENTWICKLUNG_BUERO`. `ERLAUBT` enthält kein `WAMO_ENTWICKLUNG*`.
- Abnahme: `git diff origin/main -- infra/sokrates2/wamo-core-slots.sh infra/sokrates2/wamo-core-vorpruefung.sh infra/sokrates2/wamo-core@.service` ist leer (der Anker prüft die Prüfsumme des Slot-Skripts, infra/sokrates2/anker/wamo_anker.py:1015). `test_kern_slots.py` und `test_rd_skripte.py` grün.

**R12 `infra/sokrates2/README.md`** (2 h, abhängig von R7 bis R10)

- Dateien: `infra/sokrates2/README.md` (Zeilen um 396 und 581-582 aktualisieren, neuer Abschnitt „Kern-Kanal der Entwicklungsaufträge“ mit Dateien, Rückweg, Notaus, Ablauf, Risiko des Claude-Zugangs und Widerrufsstelle).
- Abnahme: echte Umlaute, keine Gedankenstriche im Fließtext, keine Geheimnisse, Fremdverweis-Grep leer.

## 4. Abschluss im Repo (nach beiden Gruppen)

Erst wenn beide Gruppen fertig sind, damit `CHANGELOG.md`, `README.md` und `CLAUDE.md` nur einmal angefasst werden. Ein Umsetzer, Typ R.

**R13 Vertragstest und Dokumentation** (5 h, abhängig von R1 bis R12)

- Dateien: `voice-core/test_kernkanal_vertrag.py` (neu), `README.md` (Stand, Auflage in Zeile 130-131), `CHANGELOG.md`, `docs/selbstueberarbeitung.md` (Zeile 32, 39), `.env.example`, `voice-core/.env.example`, `CLAUDE.md` (Namensvertrag).
- Verhalten: Der Vertragstest vergleicht beide Seiten: `KANAL_GRUENDE` gegen die Schlüssel von `GRUND_SAETZE`, `kern_projektname` des Kerns gegen die des Dispatchers mit der Wertetabelle I5, die Zeile `SCHRANKEN_VERSION` gegen das Muster von `schalter`, die Standardwerte des Skripts gegen I2, die ssh-Optionen des Rauchtests gegen `dev_agent.ssh_argv`. Der Namensvertrag in `CLAUDE.md` bekommt: `wamo-kern-jobs`, `authorized_keys.d/wamo-kern`, `kernkanal.conf`, `kernkanal.notaus`, `wamo-entwicklung-buero.conf`, `wamo-job-kern-einrichten.sh`, `wamo-job-kern-rueckweg.sh`, `wamo_job_kern_rauchtest.py`, `wamo_job_kernkanal.py`, `WAMO_ENTWICKLUNG_BUERO`, `WAMO_ENTWICKLUNG_PROFILE`, `WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD`, `WAMO_ENTWICKLUNG_KOSTEN_TAG_USD`, `WAMO_ENTWICKLUNG_BEFEHLE_BUERO`, Dispatcher 7, `/var/backups/wamo-job-kern`, `/var/log/wamo-job-kern`, `kernkanal.jsonl`. Beide `.env.example` führen die Büro-Variablen mit Platzhaltern.
- Rote Tests zuerst: der Vertragstest. Abnahme: echte Umlaute, keine Gedankenstriche im Fließtext, Fremdverweis-Grep leer, keine Geheimnisse und keine `user_id` im Repo, alle neuen Namen im Namensvertrag.

**R14 PR mit Prüfstrecke** (3 h plus Behebung, abhängig von R13)

- Ein Pull Request von `feat/entwicklung-im-betrieb` mit `verification-loop` (Build, Typen, Lint, Tests, Secret-Scan, Diff) und den Agenten `security-reviewer`, `python-reviewer` und `react-reviewer`. CRITICAL und HIGH werden behoben, MEDIUM nach Möglichkeit.
- Abnahme: alle Prüfungen grün, `SELF_REPO` unverändert (Test), keine Migration, `git diff origin/main` zeigt `wamo-core-slots.sh`, `wamo-core-vorpruefung.sh` und `wamo-core@.service` unverändert. Merge erst nach Nikos Ja (N6, laut Auftrag vom 10.11. erteilt, im Zweifel nachfragen).

## 5. Server-Rollout (S) in Reihenfolge

Alle Server-Pakete: nur mit Verbindung (Büronetz oder WireGuard), Alias `wamo-sokrates2`, nur bei Ruhe (`ps -u wamo` leer, kein offener Totmannschalter), nie `systemctl restart wamo-core*`, aus
Geheimnisdateien nur Namen zeigen. Die Stufen folgen aufeinander, jede erst nach bestandener Abnahme der vorigen. Pläne und Begründungen stehen in Abschnitt 5 des Planpapiers.

| Paket | Stufe | Voraussetzung | Aufwand |
| --- | --- | --- | --- |
| **S0** Bestandsaufnahme (nur lesen) | vor 0 | keine, kann schon während der Repo-Arbeit laufen | 1 h |
| **S1** Dispatcher 7 und sshd | 0a | R14 gemergt, S0 ohne offenen Befund, Ruhe | 3 h |
| **S2** Kern-Release mit Schalter aus, Oberfläche | 0b | S1 | 3 h |
| **S3** Schlüssel, Rauchtest, Notaus-Übung | 1 | S2, `fail2ban`-Vorbedingung | 2 h |
| **S4** Schalter an, Kanal zu | 2a | S3 | 2 h |
| **S5** Durchstich in Nikos Sitzung | 2b | S4, N4 | 1 h |
| **S6** Claude Code anheben | vor 3a | S5, Ruhe | 2 h |
| **S7** Recherche mit Grenzen | 3a | S6, N4 | 3 h |
| **S8** Tokens enger fassen (Serverteil) | vor 3b | S7, N1, N2 | 1 h |
| **S9** Lesend im Testrepo | 3b | S8, N3 | 3 h |
| **S10** Schreibend im Testrepo | 4 | S9, N6 (Ja) | 3 h |
| **S11** Echte Repos | 5 | S10, zwei Wochen Befund frei, N5, N6 (Ja) | 3 h je Repo |

**S0** Alle 15 Punkte aus Abschnitt 8 des Planpapiers abarbeiten. Abnahme: aktiver Slot und Release eindeutig oder Zweig `umstellen` gewählt, alte Unit inaktiv, Dispatcher-Version und `pruefen`-Stand bekannt, OpenSSH-Version und
`expiry-time`-Frage beantwortet, `fail2ban`-Lage geklärt, Flags von Claude Code bekannt, Abweichungen vom Repo als Befund notiert. Liefert die Frage, ob R-Pakete anzupassen sind (zum Beispiel bei einer Vorversion unter 6).

**S1** Dateien nach dem Kopfkommentar von `infra/sokrates2/wamo-job-einrichten.sh` bereitstellen (`verteilen`), dann:

```text
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-einrichten.sh"
ssh wamo-sokrates2 "sudo touch /run/wamo-sshd-bestaetigt"      # über eine NEUE Verbindung, binnen 5 Minuten
ssh wamo-sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-dispatch ping"
ssh wamo-sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp --attrappe"
```

Dazu bei Vorversion unter 6 `wamo-pruefen-rauchtest` mit `WAMO_PRUEFEN=aus`, und der PC-Kern wird per `git pull` aktualisiert. Abnahme: `ping` meldet Version 7, vom PC aus (Auftragsschlüssel) meldet `ping` `kanal=pc` und `kernkanal` nicht strikt,
Rauchtest grün, ein PC-Auftrag läuft wie bisher, Totmannschalter bestätigt. Rückweg: Totmannschalter, dann `wamo-job-rueckweg.sh` (Vorversion laut S0).

**S2** Falls S0 „noch nicht umgestellt“ ergab, zuerst `wamo-core-slots.sh umstellen <release>`. Dann:

```text
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh einrichten"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh release v0.1.0-<commit>"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh umschalten-kern v0.1.0-<commit>"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh bewaehren 30"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh ausleiten"          # erst nach Ruhe-Regel
ssh wamo-sokrates2 "bash ~/wamo-infra/wamo-ui-einrichten.sh umschalten"
```

Abnahme: Bewährung bestanden, Journal des neuen Slots zeigt „Entwicklungsaufträge aus“ mit Grund, Statusleiste zeigt den Grund nur Admins. Rückweg: `rueckweg-kern`, `wamo-ui-einrichten.sh zurueck`.

**S3**

```text
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh schluessel --trocken"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh schluessel --ablauf <JJJJMMTT> --hostfingerabdruck <SHA256:...>"
ssh wamo-sokrates2 "sudo /opt/wamo-core/bin/wamo-job-kern-rauchtest --produktiv"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-rueckweg.sh notaus"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh notaus-aufheben"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh schluessel --ablauf <JJJJMMTT> --hostfingerabdruck <SHA256:...>"
```

Abnahme: alle Ja- und Nein-Proben bestanden, PC-Datei unverändert (Prüfsumme), `ping` vom PC meldet weiter `kanal=pc`, nach `notaus` scheitert der Kern-`ping` mit `notaus`, nach der Wiedereinrichtung geht er wieder, Schlüssel 0600 `wamo-core`, `.ssh` 0700, `kernkanal.conf` steht auf `KANAL=zu`.

**S4** Vorher am freien Slot die Absturz-Neustart-Probe (`systemctl kill`, dann `warte_auf_slot`). Dann:

```text
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh schalter an v0.1.0-<commit> --profil <user_id> --kosten-auftrag 3 --kosten-tag 10"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh bewaehren 30"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh ausleiten"
ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh status"
```

Abnahme: Journal zeigt für Niko `entwicklung=an` oder „kein eigener Zugang“, ein Mitglied bleibt aus, `status` zeigt Schalter je Slot gleich dem Drop-in, ein Rückweg auf ein älteres Release ergibt Entwicklung aus.

**S5** Niko hat N4 erledigt, lädt neu und klickt eine Karte. Abnahme: hörbar „Kanal geschlossen“, `actions` zeigt `failed`, `/srv/wamo/logs/auftraege.log` zeigt `kanal=kern`, kein Token, kein Claude-Lauf, das sshd-Journal zeigt den Fingerabdruck des Kern-Schlüssels.

**S6** Version von Claude Code anheben nach README und Abschnitt E12 (Ruhe, danach `wamo-job-einrichten.sh` und Rauchtest). Abnahme: `CLAUDE_VERSION` neu, beide Flags in `claude --help`, Rauchtest grün, ein PC-Auftrag läuft.

**S7** `kanal --arten recherche` mit den Werten aus I2, dann drei Aufträge (einer durch Auflegen beendet), eine Rauchprobe mit `--max-budget-usd 0.05` und eine mit `--max-turns 2`, danach die Werte zurück. Abnahme: Grenzen greifen, Kosten und Dauer in `actions`, Auflegen räumt Sandbox ab, kein GitHub-Token entstanden, kein Zugangswert in Log oder `result`, ob `total_cost_usd` bei Abo-Token geliefert wird ist notiert.

**S8** Nach N1 und N2: `ping` meldet `github app`, Rauchtest `--app-echt` grün, das WAMO-Repo fehlt in der Installation. Dann `GH_TOKEN` und `GH_TOKEN_LESEN` aus `/etc/wamo/geheim.env` löschen (Namen prüfen, nie Werte zeigen) und einen PC-Auftrag im App-Modus als Probe laufen lassen. Danach N3. Hinweis an Niko: PC-Aufträge laufen ab jetzt nur noch für Repos der App-Installation.

**S9** `kanal --arten recherche,review,analyse,sicherheit --repos <owner>/<testrepo>`, drei Aufträge, `kanal zu` einmal üben und wieder öffnen. Abnahme: Token nur für das Testrepo und am Ende widerrufen, Fork-Prüfung wirkt, eine GitHub-Art ohne Repo wird abgelehnt, Kosten und Dauer in `actions`.

**S10** Nach Nikos Ja: `--arten recherche,review,analyse,sicherheit,test,refactoring` mit dem Testrepo, drei Aufträge mit Branch, Änderung und Pull Request. Abnahme: `git push` nur nach dem Freigabe-Klick, Pull Request vorhanden, Freigabe genau einmal als `approved` verbucht. „Kein Push auf den Standardbranch“ wird im Log beobachtet, nicht erzwungen.

**S11** Nach Nikos Ja und den Voraussetzungen aus Stufe 5: Repos in App-Auswahl (N1) und `REPOS`, Ablauf verlängern (180 Tage), `DIENSTE` erst nach engem Jira und Vercel. Abnahme: die ersten fünf Aufträge ohne Befund, wöchentlicher Abgleich Dispatcher-Log gegen `actions`, nur Repos aus `REPOS` erhalten ein Token.

**Stand des Rollouts (11.11.2026).** S1 bis S4 sind am Server ausgeführt. Der Kanal ist zu, es kann kein Auftrag starten, bis S7 ihn mit `kanal --arten ...` öffnet.

- **S1:** Dispatcher Version 7, sshd-Block mit `ExposeAuthInfo`, Totmannschalter bestätigt, Rauchtest 58 von 58, `ping` vom PC meldet `kanal=pc`. **Befund:** Die Ruhe-Prüfung von `wamo-job-einrichten.sh` zählt die Benutzerdienste `xdg-document-portal` und `xdg-permission-store` von `wamo` als Auftrag und endet mit Exit 3, ohne etwas zu ändern. Beide wurden vor dem Lauf mit `systemctl --user --machine=wamo@.host stop` gestoppt (sie starten bei Bedarf neu). Folgearbeit: beide Namen in die Ausnahmeliste von `ruhe_pruefen` aufnehmen.
- **S2:** Kern-Release `v0.1.0-ee5b595` (`main` nach PR 79 und 80) in Slot a, Bewährung 30 Minuten bestanden, Oberfläche blau live (Image `wamo-ui:v0.1.0-b6d9b1c463e1`, Bewährung 58 Proben ohne Fehler). Ob die Logzeile „Entwicklungsaufträge aus“ mit Grund erscheint, zeigt sich erst bei der nächsten Büro-Sitzung (die Funktionen `grund_vorab` und `anzeige_grund` liefern im neuen Release den Grund für Admins und nichts für Mitglieder).
- **S3:** Kern-Schlüssel mit Ablauf 20261211 (30 Tage), der Hostschlüssel wurde gegen den Pin des PCs geprüft. Rauchtest `--produktiv`: 28 von 30, die zwei Abweichungen waren falsche Erwartungen der Proben (der Dispatcher wies beide richtig ab, Audit-Log `ABGEWIESEN`), behoben mit PR 81. Notaus-Übung bestanden (Kern-`ping` scheitert, Sperrdatei und Schlüsselzeile weg, nach `notaus-aufheben` und neuem Schlüssel geht er wieder), PC-Datei unverändert (Prüfsumme gleich), `kernkanal.conf` steht auf `KANAL=zu`. Offen aus den Übergangsproben: Notaus mit laufendem Kern-Auftrag (erst mit S7 möglich).
- **S4:** Absturz-Neustart-Probe am freien Slot bestanden (Neustart nach `systemctl kill -s KILL`, antwortet wieder mit 401). `schalter an` für Nikos Profil mit Kosten 3 USD je Auftrag und 10 USD je Tag, Kanal zu.
- **Kleinigkeit für später:** `aktiver_slot()` im Rauchtest nimmt den ersten laufenden Slot, während der Auslaufzeit also den auslaufenden. Nach `ausleiten` stimmt es.

## 6. Niko, manuell (N)

Diese Schritte kann nur Niko ausführen (GitHub-Einstellungen, Konto, Entscheidungen). Alles andere im Papier läuft ohne ihn. Zu keinem Schritt gehört, einen Wert zu nennen: Niko hinterlegt Werte nur an den genannten
Stellen, der Server und das Repo sehen sie nie im Klartext.

| Nr. | Was, wo | Wann | Nachweis |
| --- | --- | --- | --- |
| **N1** | GitHub-App prüfen: Zugriff „Only select repositories“, ohne das WAMO-Repo und ohne Kopien oder Forks davon, mit dem Testrepo | vor S8 | S8: `ping` meldet `github app`, Rauchtest `--app-echt` grün |
| **N2** | Testrepo anlegen: privat, leer, neutraler Name, kein Fork, keine Vorlage, keine Verbindung zu echten Repos. Der Name geht an die Umsetzung für `REPOS` | vor S8 | Name und Eigentümer in `REPOS` |
| **N3** | `GH_TOKEN` und `GH_TOKEN_LESEN` bei GitHub widerrufen (nach dem Löschen in S8). PC-Aufträge laufen danach nur für Repos der App-Installation | nach S8, vor S9 | Der PC-Auftrag im App-Modus aus S8 lief schon vorher durch |
| **N4** | Eigenen Claude-Zugang in der Karte Modellzugang hinterlegen, bevorzugt einen API-Schlüssel mit Ausgabenlimit im Workspace, sonst ein Claude-Code-Token. Die Stelle für den Widerruf im Konto notieren (Weg, nicht der Wert) | vor S5 | Quelle `token` oder `api` in der Sitzung, kein „kein eigener Zugang“ im Journal |
| **N5** | Vor Stufe 5: Entscheidung zum Branch-Schutz (Tarif mit Rulesets für private Repos) oder Auftrag für F1, Token rotieren, bei Bedarf Jira-Lesekonto und Vercel-Token nur für Preview anlegen | vor S11 | Entscheidung und Datum in der Doku |
| **N6** | Ausdrückliches Ja: Merge des Pull Requests (liegt laut Auftrag vor), Stufe 4, Stufe 5 | vor R14-Merge, S10, S11 | Ja in der Sitzung oder im PR |

## 7. Folgepakete (nicht in diesem Durchgang)

Mit Auslöser im Planpapier, Abschnitt 9. Jedes bekommt bei Bedarf ein eigenes Planpapier und eigene Pakete.

- **F1 Veröffentlichung durch den Dispatcher** (Typ R, Gruppe 2 plus Kern und Oberfläche, grob 5 bis 8 Arbeitstage): neuer Befehl, Karte mit Änderungsübersicht, entschärftes Git (Grundlage `git_config_filtern`, infra/sokrates2/wamo_job_dispatch.py:1361-1383), Wächter-Regeln, Tests, Sicherheitsreview.
- **F2 Freigabe-Beleg** über den vorhandenen Freigabe-Dienst für `approve` im Kern-Kanal.
- **F3 und F4 Auftragsdienst mit Unix-Socket** (`SO_PEERCRED`), Aufträge überleben die Sitzung.
- **F5 Aufräumen der Kern-Ordner**, Plattenquote. **F6 Frische aal2-Prüfung** je Auftragsstart. **F7 `expiry-time`** als zweite Schicht. **F8 Befehlskriterien im Betrieb** entscheiden.

**Stand der Folgearbeiten (11.11.2026).**

- **Auslagerung großer Dateien, `dev_agent.py`: erledigt** (Zweig `refactor/dev-agent-module`). 21 Definitionen sind unverändert nach `dev_agent_weg.py` (SSH-Weg, Statustexte) und `dev_agent_regeln.py` (Umgebungs-Allowlist, Sperrmuster, Regeltexte) gezogen, per AST-Vergleich belegt. Die Selbstschutz-Prüfungen (`project_slug`, `repo_pruefen`, `repo_aus_auftrag`, `SELF_REPO`) bleiben bewusst in `dev_agent.py`. Die Klasse `DevAgent` (etwa 1100 Zeilen, ein gemeinsamer Zustand) bleibt ungeteilt, ein Schnitt in Mischklassen brächte mehr Umwege als Lesbarkeit.
- **Auslagerung im Dispatcher: bewusst nicht jetzt.** Jedes neue Modul braucht Änderungen am Auslieferungsweg (Einrichtungsskript an sechs Stellen, Rückweg, Prüfsumme in `ping`), ein Fehler dort träfe auch den PC-Kanal. Die Funktionen des Blocks „Projekt entschärfen“ arbeiten mit Dateideskriptoren und laufen laut Kommentar nur auf dem Server, ihre Tests werden auf der Entwicklungsmaschine übersprungen. Der Block „GitHub-App“ hängt an Patchpunkten der Tests (`_https`, `SCHLUESSEL_ORDNER`, `HEIM`, `emit`, `log`). Der richtige Zeitpunkt ist F1, denn F1 baut genau auf dem Git-Block auf.
- **F1 bis F8: Entscheidungen von Niko (11.11.2026).** Die Auslöser aus dem Planpapier (Abschnitt 9) waren nicht erfüllt, Niko hat jede Folgestufe einzeln entschieden:
  - **F1 bauen.** Der Dispatcher veröffentlicht selbst (eigener Zweig `wamo/<job>`, Pull Request, der Auftrag hat nur ein Lese-Token). Branch-Schutz bei GitHub ersetzt F1 nicht. Damit ist N5 zur Hälfte beantwortet (Auftrag für F1); Token rotieren und Jira- und Vercel-Zugänge bleiben vor S11 offen.
  - **F2 zusammen mit F1.** Der Freigabe-Beleg des Freigabe-Dienstes (an das aal2-Token gebunden) wird im selben Paket gebaut, weil der Dispatcher mit F1 selbst pusht und den Klick belegt bekommen soll.
  - **F3 und F4 jetzt einplanen, F5 zusammen mit ihnen.** Eigener Auftragsdienst mit Unix-Socket und `SO_PEERCRED`, Aufträge überleben das Auflegen, kein Schlüssel mehr auf der Platte, Aufräumen der Kern-Ordner und Plattenquote im selben Entwurf.
  - **F6 entfällt.** Die Anmeldung der Sitzung mit zweitem Faktor genügt, es gibt keine frische aal2-Prüfung je Auftragsstart.
  - **F7 mit den Folgepaketen.** Der Auslöser ist erfüllt: die Handbuchseite des installierten sshd (OpenSSH 9.6p1) nennt `expiry-time`. Bei der Umsetzung ist mit einem Probeschlüssel zu belegen, dass sshd den Ablauf wirklich durchsetzt. Die frühere Annahme, F7 warte auf diese Antwort, ist überholt.
  - **F8 nach Stufe 4 entscheiden**, wie im Plan.

  Daraus entsteht ein **Folgepaket 1 (F1, F2, F3, F4, F5, F7)**. Es ist sicherheitskritisch und bekommt vor dem ersten Code ein eigenes Planpapier mit Sicherheitsreview. Es blockiert S5 bis S10 nicht: Der Kern-Kanal läuft in den Stufen bis 4 ohne diese Pakete. Vor S11 (echte Repos) müssen F1 und F2 stehen.

## 8. Reihenfolge und Aufwand

**Reihenfolge**

```text
S0 (sofort, nur lesen, parallel zur Repo-Arbeit)
Gruppe 1: R1, R2, R3, dann R4 und R5, R6          Gruppe 2: R7, dann R8 und R9, R10, R11, R12     (parallel)
R13 Vertragstest und Doku  ->  R14 PR und Prüfstrecke  ->  Merge (N6)
S1 -> S2 -> S3 -> S4                                  (ohne Niko, Kanal zu, kein Auftrag möglich)
N4 -> S5 -> S6 -> S7                                  (Niko hinterlegt den eigenen Zugang)
N1, N2 -> S8 -> N3 -> S9                              (Testrepo, Tokens enger)
N6 (Ja) -> S10 -> zwei Wochen ohne Befund -> N5, N6 (Ja) -> S11
```

**Aufwand.** Gruppe 1 etwa 30 Stunden (R1 6, R2 3, R3 6, R4 6, R5 6, R6 3), Gruppe 2 etwa 35 Stunden (R7 8, R8 6, R9 4, R10 10, R11 5, R12 2). Mit zwei Umsetzern parallel sind das fünf Arbeitstage,
dazu R13 und R14 mit einem Tag und der Behebung der Review-Befunde. Gesamt im Repo sechs bis sieben Arbeitstage Kalenderzeit statt der drei Tage der ersten Fassung. Der Server-Rollout bis Stufe 2a (S1 bis S4) braucht
etwa einen Arbeitstag mit den Bewährungszeiten, bis 3b weitere zwei bis drei Stunden je Schritt. Die Zeiten sind Schätzungen, der größte Unsicherheitsfaktor ist der Ist-Stand des Dispatchers (S0).
