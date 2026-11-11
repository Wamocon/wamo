# Folgepaket 1: Veröffentlichung durch den Dispatcher, Freigabe-Beleg, Auftragsdienst, Aufräumen und Ablauf in sshd

Planpapier, Stand 11.11.2026, Basis `origin/main` 013a357 (Zweig `docs/folgepaket-1-plan`). Dieses Papier ändert weder Code noch Server. Es setzt Nikos Entscheidungen vom
11.11.2026 um (Abschnitt 1) und baut auf `docs/entwicklung-im-betrieb.md` (Abschnitt 4 Sicherheitsmodell, Abschnitt 9 Folgestufen) und
`docs/entwicklung-im-betrieb-arbeitsliste.md` (Schnittstellen I1 bis I10, Abschnitte 5 bis 8) auf. Kennzeichnung: **B** belegt (Datei:Zeile im Arbeitsbaum der Basis),
**A** abgeleitet, **Annahme** nicht belegt, mit Prüfstelle in 2.3. Pfade gelten relativ zur Repo-Wurzel, Bezeichner stehen in ASCII. Schritte, die nur Niko ausführen kann, sind mit
**Niko, manuell** gekennzeichnet. Die Pakete heißen FP1-0 bis FP1-16 (Abschnitt 4).

**Kurzfassung.** Die Sandbox eines Kern-Auftrags bekommt nie mehr ein Schreib-Token. Nach dem Lauf baut der Dispatcher außerhalb der Sandbox aus dem Dateibaum des Projektordners,
nicht aus dessen `.git`, einen eigenen Commit in einem eigenen Git-Verzeichnis und meldet eine Änderungsübersicht mit Commit-ID und Tree-ID. Niko liest die Karte, gibt einen frischen
Code des zweiten Faktors ein und holt beim Freigabe-Dienst einen Beleg (Ed25519, gebunden an Auftrag, Commit-ID, Tree-ID und Person, 120 Sekunden gültig, einmal verwendbar). Erst mit
diesem Beleg erzeugt der Dispatcher ein Schreib-Token für genau ein Repo, pusht genau diesen Commit nach `wamo/<job>` und öffnet den Pull Request als Entwurf. Gemergt wird nie durch WAMO.
Ein eigener Auftragsdienst mit Unix-Socket und `SO_PEERCRED` ersetzt danach den ssh-Weg des Kerns: Aufträge überleben das Auflegen, der SSH-Schlüssel des Kerns verschwindet von der Platte.
Dazu kommen das Aufräumen der Kern-Ordner mit Plattenwache (F5) und `expiry-time` als zweite Schicht, die nur für den Übergang taugt (F7, Abschnitt 3.5). Der Aufwand liegt bei etwa 150 Stunden.

**IQ:** Die Grenze sitzt dort, wo ein gekaperter Kern nichts ändern kann: Der Dispatcher liest nie ein `.git` aus der Sandbox, das Schreib-Token entsteht erst nach einem Beleg, der nur der
Freigabe-Dienst ausstellen kann, und alles ist fail-closed. **EQ:** Niko sieht vor dem Klick genau, was veröffentlicht wird, hört nach jedem Klick eine Quittung, und ein Auftrag geht beim
Auflegen nicht mehr verloren. **VQ:** Ein Ablaufbild, Tabellen mit gleichen Spalten, je Paket Dateien, Abnahme und Stunden. **Synthese:** Zuerst die riskanteste Grenze bauen und prüfen
(Snapshot der unvertrauten Sandbox-Dateien, FP1-4), dann Beleg und Einreichen, danach den Transportwechsel, der unabhängig davon ist.

## 1. Ziel, Entscheidungen, Abgrenzung

**Entscheidungen von Niko vom 11.11.2026 (verbindlich, Arbeitsliste Abschnitt 7)**

| Nr. | Entscheidung | Folge in diesem Entwurf |
| --- | --- | --- |
| F1 | bauen: Der Dispatcher veröffentlicht selbst. Lauf der Sandbox nur mit Lese-Token, nach dem Freigabe-Klick pusht er nach `wamo/<job>` und öffnet den Pull Request. Branch-Schutz bei GitHub ist kein Ersatz | 3.1 |
| F2 | zusammen mit F1: Der Freigabe-Dienst stellt einen an das aal2-Token gebundenen Beleg aus, den der Dispatcher im Kern-Kanal verlangt, bevor er pusht | 3.2 |
| F3, F4 | jetzt einplanen: eigener Auftragsdienst mit Unix-Socket und `SO_PEERCRED` statt ssh vom Kern, Aufträge überleben das Auflegen und sind später abrufbar, kein SSH-Schlüssel des Kerns mehr auf der Platte | 3.3 |
| F5 | im selben Entwurf: Aufräumen der Kern-Job- und Projektordner (Namensraum `kern-`) und Plattenquote | 3.4 |
| F6 | entfällt, keine frische aal2-Prüfung je Auftragsstart | gilt für den Start. Für das Einreichen stellt sich die Frage neu (Frage 1) |
| F7 | mit den Folgepaketen: `expiry-time` als zweite Schicht neben `ABLAUF`, mit einem Probeschlüssel belegen | 3.5, mit der Antwort zur Lebensdauer nach F3 und F4 |
| F8 | gehört nicht dazu (nach Stufe 4) | nicht geplant |

**Ziel.** (1) In keinem Kern-Auftrag steht ein Schreib-Token in der Sandbox. (2) Nichts erreicht GitHub ohne Beleg des Freigabe-Dienstes, und ein gekaperter Kern kann keinen Beleg
ausstellen. (3) Ein Auftrag überlebt das Auflegen der Sitzung, sein Ergebnis und seine Änderung sind in der nächsten Sitzung abrufbar, und auf der Platte des Kerns liegt kein SSH-Schlüssel mehr.
(4) Die Ordner des Namensraums `kern-` wachsen nicht unbegrenzt, und ein Lauf kann die Platte nicht füllen. (5) Der PC-Kanal verhält sich unverändert.

**Nicht-Ziele.** Merge durch WAMO (nie). Eine Commit-Folge des Agenten (es gibt einen Commit je Auftrag, Entscheidung im Entwurf, 3.1.3). Submodule, LFS, Änderungen an `.github/`. Einreichen
im PC-Kanal (Frage 5). Aufträge, die ohne Sitzung weiterrunden (Abnahme-Runden bleiben Sache des Kerns, Frage 6). Änderungen an `wamo-core-slots.sh`, `wamo-core-vorpruefung.sh` und
`wamo-core@.service` (Vertrag in CLAUDE.md, das Drop-in `wamo-entwicklung-buero.conf` darf wachsen). `SELF_REPO = "wamo"` bleibt (infra/sokrates2/wamo_job_dispatch.py:132,
infra/sokrates2/wamo_job_kernkanal.py:68). Eine eigene Benutzertrennung für den GitHub-App-Schlüssel (Risiko 8 in Abschnitt 7).

## 2. Ist-Stand mit Belegen

### 2.1 Worauf der Entwurf aufbaut

| Nr. | Aussage | Beleg | Folge für den Entwurf |
| --- | --- | --- | --- |
| 1 | Dispatcher Version 7, einmaliger Aufruf je ssh-Verbindung, Kanal `lokal`, `pc`, `kern` aus dem benutzten Schlüssel | infra/sokrates2/wamo_job_dispatch.py:118,140,2444-2458,3171-3204 | Neue Befehle und eine neue Kanalquelle (Socket) hängen dort ein, Version wird 8 |
| 2 | Befehlsnamen müssen `[a-z]{2,10}` sein, die Befehlsliste des Kern-Kanals ist fest | wamo_job_dispatch.py:157,320-329, wamo_job_kernkanal.py:63-65 | Die Namen `aenderung`, `einreichen`, `verwerfen`, `liste`, `ereignisse` passen. `veroeffentlichen` hätte 16 Buchstaben und würde abgewiesen |
| 3 | Ein Schreib-Token liegt heute als `GH_TOKEN` in der Umgebung der Sandbox, Rechte je Art aus der Werkzeugkarte | wamo_job_dispatch.py:568-608 (596-597), voice-core/werkzeugkarte.py:69-91,318-330 | Im Kern-Kanal wird für schreibende Arten die Stufe `lesen` genommen, das Schreib-Token entsteht nur noch im Dispatcher |
| 4 | Token je Lauf: erzeugen für ein Repo, Antwort prüfen, am Ende widerrufen, Signale aufgeschoben | wamo_job_dispatch.py:823-844,865-904,910-924,965-1001 | Dieselben Bausteine für das Einreichen, mit eigenem Rechtesatz |
| 5 | Fork-Prüfung liegt vor und läuft beim ersten Token je Lauf | wamo_job_dispatch.py:2547-2555, wamo_job_kernkanal.py:670 ff. | Wird beim Einreichen wiederholt |
| 6 | Heute pusht der Agent: Der Wächter meldet `git push` und `gh pr create` als Freigabe, `approve` trägt nur den Fingerabdruck ein, danach läuft der Agent weiter | voice-core/dev_guard.py:107,167-176,213,358-359, wamo_job_dispatch.py:2028-2050, voice-core/dev_agent.py:904-953 (940-946) | Im Kern-Kanal sperrt der Wächter diese Wege ganz, das Einreichen ist kein Werkzeugaufruf des Agenten mehr |
| 7 | Entschärfung des Projektordners läuft vor jedem Lauf und arbeitet mit Verzeichnis-Deskriptoren, filtert aber nur: Das `.git` aus der Sandbox bleibt unvertraut | wamo_job_dispatch.py:1304-1327,1415-1443,1629-1651 | Der Dispatcher führt nie Git gegen den Projektordner aus (3.1.3) |
| 8 | Die vorhandene Funktion `projekt_leeren_sicher` ist definiert, wird aber nirgends aufgerufen | wamo_job_dispatch.py:1740-1761 | Grundlage der frischen Arbeitskopie je Auftrag (3.1.1) |
| 9 | Die Sandbox sieht nur den Projektordner beschreibbar, der Auftragsordner ist unsichtbar, Zeitlimit 1800 s | infra/sokrates2/wamo-job.sh:83,313-319 | Snapshot, Zustand und Journal gehören in den Auftragsordner |
| 10 | Der Benutzer `wamo` erreicht das Internet nur auf TCP 80 und 443, Dispatcher und Sandbox teilen diese Regel | infra/sokrates2/wamo-egress.nft (Kette `wamo`) | Push und Pull Request gehen vom Dispatcher aus, ohne neue Firewallregel |
| 11 | Stdin-Ende der ssh-Verbindung beendet den Auftrag (Lebenslinie), der Kern hält stdin offen und bricht beim Auflegen ab | wamo_job_dispatch.py:2758-2766, voice-core/dev_agent.py:1277,1292-1299 | Der Grund für F3: Ohne Dauerprozess kann kein Auftrag das Auflegen überleben |
| 12 | Der Kern ruft den Dispatcher mit `ssh -F none` und eigenem Schlüssel, Standardpfad `~/.ssh/id_ed25519_wamo_jobs` (`/var/lib/wamo-core/.ssh`) | voice-core/dev_agent_weg.py:57-123 (61-63), infra/sokrates2/wamo-job-kern-einrichten.sh:75,469,494 | `SshWeg` bleibt als Rückweg, `SocketWeg` kommt dazu (3.3) |
| 13 | Der Kern prüft das Sitzungstoken der Oberfläche selbst, er sieht es also im Klartext | voice-core/auth_gate.py:5,263,289 | Ein Beleg ohne frischen Code wäre gegen einen gekaperten Kern wertlos (3.2) |
| 14 | Freigabe-Dienst: eigener Benutzer `wamo-anker`, Socket `/run/wamo-freigabe/freigabe.sock`, `SO_PEERCRED` nur für root und `wamocon` (Caddy), Token über JWKS geprüft, aal2 mit jüngstem Code höchstens 60 Sekunden alt, 8 Versuche je 10 Minuten | infra/sokrates2/anker/freigabe_dienst.py:64-66,264-271, anker/freigabe.py:34,102-147,150-172, anker/wamo-freigabe.service:30,41,52 | Der Beleg wird eine zweite Route desselben Dienstes mit denselben Prüfungen |
| 15 | Die Oberfläche holt die Release-Freigabe über Caddy auf Port 55444 direkt beim Freigabe-Dienst, am Kern vorbei, mit Code-Frische-Prüfung (45 s) und Fehlertabelle | infra/sokrates2/wamo-caddy/kern/wamo-core.caddy:28-45, ui/src/lib/aenderungenApi.ts:39-44, ui/src/lib/aenderungenFreigabe.ts:12 | Dieselbe Bauart für den Beleg, die Logik wird wiederverwendet |
| 16 | Der Dispatcher läuft mit dem Systempaket `cryptography` (RSA-Signatur für die App), sonst nur mit der Standardbibliothek | wamo_job_dispatch.py:739-755,762-774 | Ed25519-Prüfung ohne neue Abhängigkeit (Annahme 2) |
| 17 | Die Slot-Vorlage erlaubt `AF_UNIX`, schreibt nur nach `/run/wamo-core`, `ProtectSystem=strict` | infra/sokrates2/wamo-core@.service:50,53,64,75 | Der Kern erreicht einen Socket unter `/run/wamo-auftrag`, ohne Änderung der Vorlage |
| 18 | Das Drop-in `wamo-entwicklung-buero.conf` schreibt nur `wamo-job-kern-einrichten.sh schalter`, `ALLE_VARIABLEN` führt die Kern-Variablen | wamo-job-kern-einrichten.sh:620-628, voice-core/entwicklung_betrieb.py:41, infra/sokrates2/wamo_core_env.py:21 | Neue Kern-Variablen (Weg, Socket) kommen nur ins Drop-in und nie in `core.env` |
| 19 | Audit je Auftrag in `actions`, `tool` ist freier Text, der Status folgt einem Automaten, `wamo_core` darf Status und Ergebnis nachtragen | supabase/migrations/0001_wamo_init.sql:192-207,285-300, voice-core/memory.py:132 | Eine zweite Zeile `auftrag_einreichen` braucht keine Migration |
| 20 | Aufräumen gibt es nur für Caches (7 und 30 Tage), Kern-Ordner zählt nur der Befehl `status`, Platz prüft nur `MIN_PLATZ_MB` beim Start | wamo_job_dispatch.py:2229-2253, wamo-job-kern-einrichten.sh:748, wamo_job_kernkanal.py:600-631 | F5 ergänzt Fristen, Quote und eine Wache während des Laufs |
| 21 | Die Schlüsselzeile des Kerns hat bewusst kein `expiry-time`, Rauchtest und Test prüfen das, der Ablauf steht als `ABLAUF` in `kernkanal.conf` | wamo-job-kern-einrichten.sh:494, infra/sokrates2/wamo_job_kern_rauchtest.py:385-386, voice-core/test_job_kern_skripte.py:882, infra/sokrates2/README.md:1348,1378 | F7 kehrt diese Prüfungen um (3.5) |
| 22 | Der sshd-Block für `wamo` hat `ForceCommand`, `MaxSessions 2`, `ExposeAuthInfo`, zwei Schlüsseldateien. Lingering für `wamo` ist gesetzt, die Sandbox startet über `systemd-run --user --scope` | infra/sokrates2/wamo-job-einrichten.sh:161-162,335-352, wamo-job.sh:365 | Ein Dauerdienst als `wamo` kann Sandboxen weiter starten (Annahme 5) |
| 23 | Der Runner entfernt jede Umgebungsvariable, die nicht auf seiner Allowlist steht | infra/sokrates2/wamo-job.sh:206-214 | `WAMO_EINREICHEN` muss dort aufgenommen werden, sonst erreicht sie den Wächter nie. Das ändert ein Skript, das beide Kanäle benutzen (FP1-2, Rauchtest mit PC-Auftrag) |

### 2.2 Was daraus folgt

- **Das `.git` im Projektordner ist feindlich.** Es kann Hooks, `include`, `insteadOf`, `pushurl`, Alternates und manipulierte Objekte enthalten. Die Entschärfung vor dem Lauf (Nr. 7) ist eine Bereinigung, kein Vertrauen. Deshalb baut der Dispatcher den Commit aus Dateien, nicht aus dessen Git.
- **Der Kern sieht das Sitzungstoken (Nr. 13).** Ein Beleg, der nur aal2 verlangt, kann ein gekaperter Kern selbst abholen. Erst der frische Code (jüngster `totp`-Eintrag höchstens 60 Sekunden alt) macht den Beleg zu etwas, das der Kern nicht allein erzeugen kann. Das widerspricht F6 nicht, weil F6 den Auftragsstart betrifft (Frage 1).
- **Der Dispatcher ist kein Dauerprozess (Nr. 1, 11).** Alles, was einen Auftrag über das Auflegen hinaus am Leben hält oder später abrufbar macht, braucht einen Dienst. Die Befehle von F1 sollen deshalb transportneutral sein, damit F3 und F4 sie ohne Änderung weitertragen.
- **Der PC-Kanal teilt die Datei des Dispatchers (Nr. 1).** Jede Änderung gilt für beide Kanäle, solange sie nicht an `KANAL == "kern"` hängt. Jede Neuerung bekommt einen Test, dass der PC-Kanal unverändert bleibt.
- **Die Befehlsnamen sind begrenzt (Nr. 2).** Eine Änderung der Regel `COMMAND_RE` ist eine Änderung am Eingang für beide Kanäle und unterbleibt.

### 2.3 Annahmen, die nicht belegt sind

| Nr. | Annahme | Prüfstelle |
| --- | --- | --- |
| 1 | `git` ist auf Sokrates-2 in Version 2.34 oder jünger vorhanden (Ubuntu 24.04 liefert 2.43), und GitHub liefert beim `fetch` eine einzelne Commit-ID aus | FP1-0 (`git --version`, Probe im Testrepo) |
| 2 | Das Systempaket `cryptography` enthält Ed25519 (sicher, wenn es neu genug für die RSA-Signatur in Nr. 16 ist) | FP1-0 (`python3 -c`) |
| 3 | GitHub weist einen Push mit Änderungen unter `.github/workflows` ab, wenn die App das Recht `workflows` nicht hat (Verhalten laut GitHub-Dokumentation, hier nicht geprüft) | FP1-9, Rauchprobe im Testrepo. Der Dispatcher sperrt `.github/` ohnehin selbst |
| 4 | Entwurfs-Pull-Requests gibt es im Tarif der Organisation auch für private Repos. Sonst antwortet GitHub mit 422, und der Code fällt auf einen normalen Pull Request zurück | FP1-9 |
| 5 | Ein Dienst als Benutzer `wamo` darf mit `systemd-run --user --scope` eine bwrap-Sandbox starten, auch mit der Härtung einer Systemunit. `RestrictNamespaces` und Teile des Syscall-Filters müssen dabei wahrscheinlich entfallen | FP1-10, Spike mit stufenweiser Härtung |
| 6 | Das Dateisystem von `/srv/wamo` kennt keine Projektquoten, deshalb eine Wache im Dispatcher statt einer Dateisystemquote | FP1-0 (`findmnt`, `quotaon -p`) |
| 7 | `expiry-time` verhält sich wie die Handbuchseite von OpenSSH 9.6p1 sagt: Zeit in der Systemzeitzone, ein reines Datum gilt ab 00:00 dieses Tages, laufende Sitzungen bleiben bestehen, die Ablehnung steht im Journal | FP1-1, Probeschlüssel an einer zweiten sshd-Instanz |
| 8 | `SupplementaryGroups=` in einem Drop-in der Slot-Vorlage reicht, damit der Kern die Gruppe `wamo-auftrag` für den Socket bekommt, ohne `/etc/group` für `wamo-core` zu ändern | FP1-11 |
| 9 | Ein Entwurf mit etwa 300 geänderten Dateien und 20 MB deckt die üblichen Aufträge ab, sonst werden die Standardwerte in `kernkanal.conf` angehoben | Stufe 4, Beobachtung |

## 3. Entwurf je Paket

### 3.1 F1: Veröffentlichung durch den Dispatcher

**3.1.1 Verhalten.** Es gilt für Arten mit GitHub-Stufe `schreiben` und gesetztem Repo im Kern-Kanal. Alle anderen Arten behalten ihren Ablauf und veröffentlichen nie.

```text
1 start         Regeln 1 bis 9 wie heute. Neu: Auch für schreibende Arten gilt im Kern-Kanal die Stufe lesen (Token nur lesend).
2 Arbeitskopie  Nur beim ersten Lauf eines Auftrags: Der Dispatcher legt ein eigenes Git-Verzeichnis ausgang.git im Auftragsordner an (flacher Klon, Tiefe 50, Lese-Token),
                merkt basis (Commit-ID der Spitze des Standardbranches), leert den Projektordner (projekt_leeren_sicher) und zieht dort eine Arbeitskopie hinein
                (lokaler Klon ohne Hardlinks, Remote auf die GitHub-Adresse). Fortsetzungen desselben Auftrags behalten den Ordner.
3 Lauf          Sandbox wie heute. Der Wächter sperrt im Kern-Kanal git push, gh pr create, gh pr merge und schreibende gh api, das Token kann ohnehin nur lesen.
4 Snapshot      Token widerrufen, Einheiten gestoppt und geprüft, dann liest der Dispatcher die Dateien des Projektordners mit Deskriptoren (3.1.3), baut Blobs, Tree und einen
                Commit in ausgang.git und schreibt aenderung.json. Ereignis aenderung an den Kern: Übersicht, commit_id, tree_id, status.
5 Karte         Der Kern zeigt die Karte Änderung. Niko liest sie, gibt einen frischen Code ein, die Oberfläche holt beim Freigabe-Dienst den Beleg (3.2).
6 einreichen    Der Kern ruft einreichen <job> mit dem Beleg auf. Der Dispatcher prüft Kanal, Zustand, Beleg und Grenzen, erzeugt das Schreib-Token (ein Repo), pusht commit_id
                nach refs/heads/wamo/<job>, öffnet den Pull Request als Entwurf und widerruft das Token. Ereignis eingereicht mit Zweig, Nummer und Adresse.
```

Zustand in `aenderung.json`, Feld `status`: `keine` (nicht schreibende Art, keine Änderung, Lauf ungewöhnlich beendet), `gesperrt` (mit Gründen), `bereit`, `einreichen_laeuft`,
`branch_gepusht`, `eingereicht`, `verworfen`, `abgelaufen`, `fehler`. Übergänge laufen nur unter der Sperre `.einreichen` im Auftragsordner und werden atomar geschrieben
(`write_json_atomic`, wamo_job_dispatch.py:1090). Ein neuer Lauf desselben Auftrags ersetzt `bereit` durch einen neuen Snapshot, ein früherer Beleg passt dann nicht mehr (andere `commit_id`).
Nach `eingereicht` gilt ein Auftrag als abgeschlossen: Nacharbeit ist ein neuer Auftrag.

**3.1.2 Schnittstellen**

| Art | Name | Inhalt und Regel |
| --- | --- | --- |
| Befehl | `aenderung <job>` | liest `aenderung.json` eines eigenen Auftrags und gibt das Ereignis `aenderung` aus. Rechnet nie neu. Erlaubt auch bei geschlossenem Kanal, nicht bei Notaus |
| Befehl | `einreichen <job>` | erste stdin-Zeile ist der Beleg (höchstens 2048 Byte). Nur bei offenem Kanal und gesetztem `EINREICHEN=an` |
| Befehl | `verwerfen <job>` | `bereit` nach `verworfen`, löscht `ausgang.git`. Erlaubt auch bei geschlossenem Kanal |
| Ereignis | `aenderung` | `job`, `status`, `commit_id`, `tree_id`, `basis`, `dateien`, `plus`, `minus`, `liste` (höchstens 200 Einträge: `pfad`, `art` mit `neu`, `geaendert` oder `geloescht`, `plus`, `minus`, `klasse` mit `normal` oder `sensibel`), `uebersprungen` (Zahl), `gruende` (bei `gesperrt`), `titel` |
| Ereignisse | `eingereicht`, `einreichen_fehler` | `eingereicht`: `job`, `zweig`, `pr` (Nummer), `adresse`. `einreichen_fehler`: `job`, `grund` aus `EINREICHEN_GRUENDE` |
| Gründe | `AENDERUNG_GRUENDE` | `pfad_gesperrt`, `geheimnis`, `zu_gross`, `symlink`, `sonderdatei`, `anomalie`, `schon_eingereicht`, `basis_fehlt` |
| Gründe | `EINREICHEN_GRUENDE` | `einreichen_aus`, `zustand`, `beleg_fehlt`, `beleg_ungueltig`, `beleg_abgelaufen`, `beleg_falsch`, `beleg_benutzt`, `grenze_einreichen`, `zweig_belegt`, `github_fehler`, dazu der vorhandene `fork` und die Kanalgründe |
| Feld | `auftrag.json` | neu `profil` (8 Hexzeichen aus der Kopfzeile, im Kern-Kanal Pflicht), `titel` (höchstens 120 Zeichen aus dem Auftragstext) und `basis`. Der Dispatcher legt sie in `vorbereiten` an (wamo_job_dispatch.py:1276-1301). Ein Folgelauf mit anderem Profil wird wie bei Projekt, Art und Repo abgewiesen (:1285-1288) |
| Dateien | im Auftragsordner | `aenderung.json`, `ausgang.git`, `einreichen.json` (Zustand, verbrauchte `jti`), `.einreichen` (Sperre). Der Auftragsordner ist für die Sandbox unsichtbar (wamo-job.sh:313-319) |
| Konfiguration | `kernkanal.conf` | `EINREICHEN` (an oder aus, Standard aus), `MAX_EINREICHEN_TAG` (1 bis 50, Standard 5), `MAX_AENDERUNG_DATEIEN` (1 bis 2000, Standard 300), `MAX_AENDERUNG_MB` (1 bis 200, Standard 20). Gesetzt über die neue Option `kanal --einreichen an` oder `aus` des Skripts. Eintrag in `BEKANNTE_SCHLUESSEL` (wamo_job_kernkanal.py:100) und in `KONFIG_SCHLUESSEL` des Skripts (wamo-job-kern-einrichten.sh:87) |
| Umgebung Sandbox | `WAMO_EINREICHEN=dispatcher` | nur im Kern-Kanal gesetzt, der Wächter liest sie (3.1.5). Die Allowlist `ERLAUBT_RE` in `wamo-job.sh` (Zeile 209) bekommt den Namen, sonst entfernt der Runner die Variable |
| Module | `wamo_job_aenderung.py`, `wamo_job_einreichen.py` | neue Module nach dem Muster von `wamo_job_kernkanal.py`, installiert nach `/opt/wamo/waechter` durch `wamo-job-einrichten.sh` (dessen Dateiliste und Rückweg wachsen mit) |
| Nachrichten Kern und Oberfläche | `aenderung` (zur Oberfläche), `einreichen` mit `job` und `beleg` und `verwerfen` mit `job` (von der Oberfläche), `einreichen_quittung` mit `job`, `ok`, `grund` | Quittung je Klick wie `auftrag_quittung` (dev_agent.py:832) |
| Audit | `actions`, `tool = auftrag_einreichen` | Risiko `change`, `params` mit Auftrag, Repo, `commit_id`, Kürzel der `jti`, nie ein Token. Keine Migration (Nr. 19) |

**3.1.3 Snapshot und Commit-Bau (die riskanteste Grenze des Pakets)**

- **Wann.** Am Ende jedes Laufs mit Ergebnis `fertig` oder `zeitlimit`, nachdem das Token widerrufen ist und `stop_units` gelaufen ist, solange die Sperren des Auftrags gehalten werden (wamo_job_dispatch.py:2678-2693). Nach `abgebrochen`, `egress_offen`, `freigabe_mehrfach` oder `verlassen` ist der Status `keine` mit Grund `anomalie`.
- **Lesen ohne Git.** Der Dispatcher geht den Projektordner mit `O_NOFOLLOW`, `O_DIRECTORY` und `dir_fd` durch (Muster `_walk_entschaerfen`, wamo_job_dispatch.py:1594-1626), öffnet jede Datei mit `O_NONBLOCK`, prüft `fstat` (nur reguläre Dateien) und liest höchstens 5 MB je Datei. Einträge namens `.git` (jede Tiefe, jede Schreibweise) werden nie gelesen.
- **Ausschluss.** Feste Liste (`node_modules`, `.venv`, `venv`, `__pycache__`, `.next`, `.turbo`, `.cache`, `.gradle`, `target`, `coverage`) und `.gitignore` über `git check-ignore --no-index --stdin -z` mit `--git-dir` auf `ausgang.git` und `--work-tree` auf den Ordner (liest nur Muster). Was im Basis-Tree schon steht, zählt immer. Das Übersprungene wird als Zahl gemeldet.
- **Commit-Bau mit Plumbing in `ausgang.git`** (bare, ohne Arbeitsbaum, also ohne Filter und Attribute; Umgebung `GIT_ENV` wie wamo_job_dispatch.py:1415 mit `GIT_CONFIG_GLOBAL=/dev/null`, dazu `-c core.hooksPath=/dev/null -c core.fsmonitor=false -c protocol.file.allow=never`, eigener Indexpfad): `read-tree basis`, je geänderte Datei ein Blob mit `hash-object -w --no-filters --stdin` (ob eine Datei abweicht, entscheidet ein Vergleich der in Python berechneten Blob-ID mit dem Basis-Tree), `update-index --index-info` nur mit den Modi 100644, 100755 und für erlaubte Links 120000, Löschungen mit `--force-remove`, `write-tree`, `commit-tree -p basis`. Autor und Committer sind fest `WAMO` mit einer Adresse unter `.invalid`, Zeitstempel einmal beim Snapshot (die Commit-ID ist dadurch beständig). Nachricht: Titel aus dem Auftragstext (erste Zeile, bereinigt, höchstens 72 Zeichen), `Auftrag: <job>`, `Beauftragt von Profil <8 Hexzeichen>`. Übersicht: `git diff-tree -r --numstat --no-renames --no-ext-diff --no-textconv basis commit`.
- **Ein Commit je Auftrag (Entscheidung im Entwurf).** Die Commits des Agenten zu übernehmen hieße, Objekte aus einem unvertrauten Git zu lesen. Der Preis: Der Pull Request hat einen Commit, die Verlaufsnachrichten des Agenten fehlen. Eine Änderung dieser Entscheidung braucht das Sicherheitsreview.
- **Geheimnisse.** Exakte Werte des Laufs (Claude-Zugang und Lese-Token aus `schwaerzungen(env)`, wamo_job_dispatch.py:2293) und Muster (`SK_ANT_RE`, wamo_job_dispatch.py:184, dazu `ghs_`, `ghp_`, `gho_`, `github_pat_`, PEM-Kopf, `AKIA`) in jeder neuen oder geänderten Textdatei. Ein Treffer macht den Status `gesperrt` mit Grund `geheimnis`, genannt werden nur Pfad und Regel, nie der Wert.
- **Grenzen.** `MAX_AENDERUNG_DATEIEN`, `MAX_AENDERUNG_MB`, höchstens 50 000 gelesene Einträge, Tiefe 20 und 60 Sekunden Zeitbudget. Überschreitung: `gesperrt` mit `zu_gross`.

| Klasse | Regel | Wirkung |
| --- | --- | --- |
| gesperrt | Namensteil `.git` in jeder Schreibweise (Groß und Klein, NFKC, angehängte Punkte und Leerzeichen, `git~1`, `::$`), `.gitmodules`, alles unter `.github/`. Pfad mit Steuerzeichen, Backslash, Doppelpunkt, `..`, führendem `/`, ungültigem UTF-8 oder mehr als 240 Zeichen. Windows-Gerätenamen (`con`, `prn`, `aux`, `nul`, `com1` bis `com9`, `lpt1` bis `lpt9`, auch mit Endung). Link mit absolutem oder aus dem Repo führendem Ziel, Sonderdateien, Gitlinks. Treffer der Geheimnissuche, Überschreiten der Grenzen | Status `gesperrt`, kein Einreichen, die Karte nennt Pfad und Grund |
| sensibel | `package.json`, Lockfiles, `.npmrc`, `.yarnrc*`, `Dockerfile*`, `docker-compose*`, `Makefile`, `*.sh`, `*.ps1`, `*.bat`, `*.cmd`, `.husky/`, `.pre-commit-config.yaml`, `pyproject.toml`, `setup.py`, `requirements*.txt`, `vercel.json`, `netlify.toml`, `.gitlab-ci.yml`, `.gitattributes`, `.env*`, `*.pem`, `*.key`. Neue oder geänderte Links, ausführbare Dateien und Binärdateien | Markierung auf der Karte, der Klick verlangt zusätzlich die Bestätigung der markierten Dateien (Frage 2) |

**3.1.4 Einreichen.** Reihenfolge in `einreichen <job>`, jeder Schritt bricht fail-closed ab:

1. Regeln 1 bis 4 des Kern-Kanals (Notaus, Konfiguration, Befehlsliste, eigener Auftrag), `EINREICHEN=an`, Repo aus `auftrag.json` steht in `REPOS`.
2. Sperre `.einreichen`, Zustand `bereit` und nicht älter als `AENDERUNG_TAGE` (3.4).
3. Beleg prüfen (3.2: Signatur, `job`, `commit`, `tree`, Person, Ablauf) und verbrauchen: Die `jti` wird in `einreichen.json` eingetragen, der Zustand wird `einreichen_laeuft`.
4. Objektprüfung: `commit_id` und `tree_id` stimmen mit `aenderung.json` und mit `git rev-parse` in `ausgang.git` überein.
5. Zähler `MAX_EINREICHEN_TAG` (Ereignis `einreichen` in `kernkanal.jsonl`, Muster `zaehler_schreiben`, wamo_job_kernkanal.py:582).
6. Zweite Kanalprüfung unmittelbar vor dem Token (Notaus, Konfiguration), wie beim Start (wamo_job_dispatch.py:2668-2672).
7. Schreib-Token für genau ein Repo mit den festen Rechten `contents: write`, `pull_requests: write`, `metadata: read` (eine Konstante im Modul, `_antwort_pruefen` und `GITHUB_APP_RECHTE_VERBOTEN` wirken wie heute, wamo_job_dispatch.py:823-844).
8. Repo-Metadaten mit diesem Token: Fork-Prüfung (`kern_fork_grund`) und Standardbranch. Beides kommt von GitHub, nie aus der Sandbox.
9. `git ls-remote` auf `refs/heads/wamo/<job>`: fehlt der Zweig, wird gepusht. Zeigt er auf `commit_id`, entfällt der Push. Zeigt er woanders hin, endet es mit `zweig_belegt`. Überschrieben wird nie.
10. Push aus `ausgang.git` nach `https://github.com/<repo>.git` (Repo geprüft mit `REPO_RE` und `REPOS`). Refspec genau `<commit_id>:refs/heads/wamo/<job>`, kein `+`, kein `--force`, `--no-verify`, `-c protocol.allow=never -c protocol.https.allow=always -c http.followRedirects=false -c credential.helper=`. Die Anmeldung steht nur in der Umgebung des Push-Prozesses (`GIT_CONFIG_COUNT` mit `http.https://github.com/.extraHeader`), nie in argv, URL oder Datei. `GIT_TERMINAL_PROMPT=0`, Zeitlimit 120 Sekunden, stderr geschwärzt.
11. Pull Request über die REST-Schnittstelle: `head` `wamo/<job>`, `base` der Standardbranch aus Schritt 8, `draft` an (Annahme 4), `maintainer_can_modify` aus. Titel `WAMO: <Titel>` (höchstens 80 Zeichen), Text aus einer Vorlage mit Auftragskürzel, Art, der vom Dispatcher berechneten Übersicht und dem Satz „Nicht gemergt, bitte prüfen“. Text des Agenten kommt nie hinein, `@` wird entwertet, Links aus dem Auftragstext fallen weg.
12. Token widerrufen (wamo_job_dispatch.py:910-924), Zustand `eingereicht`, Ereignis `eingereicht`, Zähler.

Das Schreib-Token entsteht erst in Schritt 7, nachdem der Beleg verbraucht und die Kanalprüfung bestanden ist. Es lebt nur im Speicher des Dispatchers und in der Umgebung des Push-Prozesses. Beim Einreichen läuft keine Sandbox.

**3.1.5 Wächter, Kern-Kanal, Systemtext**

- `dev_guard.py` (voice-core/dev_guard.py:167-176,213,358-359) sperrt bei gesetztem `WAMO_EINREICHEN=dispatcher` `git push`, `git send-pack`, `gh pr create`, `gh pr merge` und schreibende `gh api` mit `block` und dem Grund „Das Einreichen übernimmt WAMO nach deiner Freigabe“. Ohne die Variable bleibt alles wie bisher (PC-Kanal, lokale Läufe).
- Die Befehlsliste des Kern-Kanals (wamo_job_kernkanal.py:63-65) bekommt `aenderung`, `einreichen`, `verwerfen`, die geschlossene Liste `aenderung` und `verwerfen`. `kern_befehl_pruefen` prüft sie als eigene Aufträge (wamo_job_dispatch.py:2479-2488).
- `REMOTE_RULES` (voice-core/dev_agent_regeln.py:39-60) bekommt für das Büro einen eigenen Satz: Der Agent committet nicht für die Veröffentlichung und pusht nicht, WAMO legt nach Freigabe einen Pull Request an, der Ordner ist eine frische Arbeitskopie.
- `zugangs_quellen` führt im Kern-Kanal für schreibende Arten `GH_APP_LESEN@<repo>` (wamo_job_dispatch.py:546-565). Das Einreichen bucht `GH_APP_EINREICHEN@<repo>` nur im Protokoll, nicht in der Buchführung der Zugänge.

**3.1.6 Bedrohungsmodell**

| Bedrohung | Gegenmaßnahme | Restrisiko |
| --- | --- | --- |
| Feindlicher Auftrag legt Hooks, `include`, Alternates oder falsche Objekte in `.git` | Der Dispatcher führt nie Git gegen den Projektordner aus und liest dessen `.git` nicht. Der Commit entsteht nur in `ausgang.git` ohne Arbeitsbaum mit `GIT_ENV`, `core.hooksPath=/dev/null`, `core.fsmonitor=false` und `hash-object --no-filters` | `.gitignore` liest `check-ignore` (nur Muster), Tests mit Link, Übergröße und Negationen |
| Gitconfig-Trick (`url.insteadOf`, `pushurl`, `credential.helper`, `core.sshCommand`, `include`) | Es gibt keine Konfiguration aus dem Projekt. `ausgang.git` trägt nur, was der Dispatcher schreibt. Das Push-Ziel steht als URL im Aufruf, wird gegen `REPOS` und `REPO_RE` geprüft, andere Protokolle und Weiterleitungen sind aus | keins erkennbar |
| Manipuliertes Repo (präparierte `.gitattributes`, `.gitmodules`, Objekte) | `ausgang.git` ist bare: keine Filter, kein Smudge, keine Hooks. `fetch.fsckObjects` an. Änderungen an `.gitmodules` sind gesperrt, an `.gitattributes` sensibel | Code des Repos läuft weiter in der Sandbox, wie heute |
| Fork oder Kopie des WAMO-Repos als Ziel | `REPOS` ohne `wamo`, Fork-Prüfung beim Start und beim Einreichen, `head` ist immer ein Zweig im selben Repo | Eine Kopie unter fremdem Namen verhindert nur die Handarbeit bei der App-Auswahl (Hauptpapier 4.6) |
| Symlink, Hardlink, Sonderdatei | Lesen nur mit `O_NOFOLLOW` und `fstat`, Links werden nie verfolgt, also gelangt keine Serverdatei in den Commit. Neue Links sind sensibel, absolute Ziele gesperrt | keins erkennbar |
| Pfadtricks (`.GIT`, `git~1`, Gerätenamen, `:`) | gesperrt, damit ein Checkout unter Windows (Nikos Arbeitsplatz) nicht getroffen wird | Unbekannte Tricks: Im Zweifel gesperrt, dafür sind Fehlalarme möglich |
| Geheimnis im Inhalt (der Agent schreibt seinen Claude-Zugang in eine Datei) | exakte Werte und Muster, `gesperrt` | kodierte oder zerlegte Werte erkennt der Scan nicht |
| Gekaperter Kern | Kann keinen Beleg ausstellen (Schlüssel nur im Freigabe-Dienst), nicht auf `main` pushen (Zweigname fest, kein `+`), nicht in andere Repos (`REPOS`, Token für ein Repo) | Er kann Aufträge starten und die Karte fälschen. Das Tor ist der Merge durch einen Menschen |
| Gestohlenes Sitzungstoken | Der Beleg verlangt einen frischen Code (60 Sekunden), ein Code ergibt einen Beleg, der Beleg ist an Auftrag, Commit, Tree und Person gebunden, gilt 120 Sekunden und einmal | Das Fenster von 60 Sekunden nach Nikos echtem Code (Frage 1) |
| Replay, Doppelklick, Parallelaufruf | `jti` unter Sperre verbraucht, Zustandsautomat, Idempotenz des Pushs (Schritt 9) | keins erkennbar |
| Sandbox schreibt noch während des Snapshots | Einheiten gestoppt und geprüft, Sperren gehalten, `commit_id` unveränderlich gespeichert, gepusht wird nur dieses Objekt und nie der Ordner | keins erkennbar |
| Abfluss des Schreib-Tokens | nur im Speicher des Dispatchers und in der Umgebung des Push-Prozesses, ein Repo, Widerruf am Ende, Schwärzung in Ausgabe und Protokoll, keine Sandbox beim Einreichen | Port 443 bleibt offen |
| Pull-Request-Text als Einfallstor | Vorlage, Text des Agenten nie im Körper, `@` entwertet | Der Titel stammt aus Nikos Auftragstext |
| CI läuft auf dem Zweig mit Repo-Secrets | `.github/` gesperrt, sensible Dateien markiert, Pull Request als Entwurf | Code in Tests und Build-Skripten läuft in der CI (Risiko 2) |
| Dispatcher oder Benutzer `wamo` übernommen | nicht Gegenstand von F1 | hebt F1 und F2 auf (Risiko 8) |

**3.1.7 Fehlerfälle und Fail-closed**

| Fall | Verhalten |
| --- | --- |
| Klon oder Arbeitskopie scheitert (GitHub nicht erreichbar, Token abgelehnt) | `start_abgelehnt` (Exit 8), kein Lauf, kein Token bleibt liegen |
| Snapshot scheitert (Lesefehler, Grenzen, Zeitbudget) | Status `gesperrt` oder `fehler` mit Grund im Ereignis `aenderung`, nie ein halber Commit: Die `commit_id` wird erst nach `commit-tree` und dem Ref `refs/wamo/snapshot` in `ausgang.git` gemeldet |
| Notaus oder `KANAL=zu` während `bereit` | `einreichen` wird abgewiesen, der Snapshot bleibt, nach `notaus-aufheben` und `kanal` geht es weiter. `cancel` ändert den Zustand nicht |
| Beleg fehlt, falsch, abgelaufen, benutzt | `einreichen_fehler`, Zustand `bereit`. Ab Schritt 3 ist der Beleg verbraucht, ein neuer Code nötig |
| GitHub-Fehler beim Token, bei der Fork-Prüfung oder beim Push | Zustand `bereit`, Beleg verbraucht, Meldung `github_fehler` oder `fork` |
| Pull Request scheitert nach erfolgreichem Push | Zustand `branch_gepusht`. Ein zweiter Aufruf ohne neuen Beleg öffnet nur den Pull Request (gleiche `commit_id`, es wird nichts Neues veröffentlicht) |
| Dispatcher stirbt mitten im Einreichen | Zustand `einreichen_laeuft` ohne Sperre. Der nächste Aufruf fragt `ls-remote`: Zweig mit `commit_id` vorhanden heißt `branch_gepusht`, sonst `bereit`. Das Token läuft spätestens nach einer Stunde ab (wamo_job_dispatch.py:965-1001) |
| Zweig `wamo/<job>` mit anderer ID | `zweig_belegt`, nie überschreiben |
| Weiterer Lauf nach `eingereicht` | Status `gesperrt`, Grund `schon_eingereicht` |
| Änderung wird nicht eingereicht | nach `AENDERUNG_TAGE` Status `abgelaufen`, nie automatisch veröffentlicht |

**3.1.8 Tests (rot zuerst).** Neue Dateien `voice-core/test_job_aenderung.py` und `voice-core/test_job_einreichen.py`, erweitert werden `voice-core/test_kernkanal.py`, `test_sokrates2_dispatch.py`, `test_dev_guard.py`, `test_dev_agent_sokrates2.py`, `test_entwicklung_betrieb.py`. Funktionen mit Deskriptoren laufen nur unter Linux und werden auf der Entwicklungsmaschine wie bisher übersprungen (Arbeitsliste Abschnitt 7).

- Snapshot: Symlink auf `/etc/passwd` kommt nie als Inhalt in den Commit. `.git` in jeder Schreibweise, `.gitmodules`, `.github/x.yml`, Gerätename, Pfad mit Steuerzeichen und ungültigem UTF-8 ergeben `gesperrt`. Ein präpariertes `.git` mit Hook, `include`, Alternates und `pushurl` hat keine Wirkung (Zähler: kein Git-Aufruf gegen den Ordner). Geheimnis im Inhalt (exakter Wert, Muster, in einer Datei und in einem Link-Ziel). Grenzen am Rand (Datei Nummer `MAX` und `MAX + 1`). Gleicher Dateibaum ergibt gleiche `commit_id`. Löschung, Umbenennung (als Löschung plus Neu), Moduswechsel.
- Einreichen: jeder Schritt aus 3.1.4 mit eigener Eingabe in der Reihenfolge. Kein Token vor Schritt 7 (Zähler im Test). Push-Argumente genau wie festgelegt (kein `+`, kein `--force`, Ziel nur `wamo/<job>`, Token nie in argv oder URL). Idempotenz (Zweig vorhanden mit gleicher und mit anderer ID), Absturz nach dem Push, Doppelaufruf parallel.
- Kern-Kanal: Lese-Token auch für `test` und `refactoring`. Wächter sperrt `git push` und `gh pr create` nur mit `WAMO_EINREICHEN=dispatcher`, sonst unverändert. PC-Kanal: alle bisherigen Tests grün, `ping` meldet Version 8.
- Kern und Oberfläche: Quittung je Klick, `approved` und `auftrag_einreichen` nur nach Erfolg, jeder Grund aus beiden Listen hat einen Satz (Vertragstest, Arbeitsliste I6), kein Zugangswert in Zeile, Ergebnis oder Log.

### 3.2 F2: Freigabe-Beleg außerhalb des Kerns

**Verhalten.** Nach dem Klick auf „Einreichen“ verlangt die Oberfläche einen frischen Code (vorhandene Logik, ui/src/lib/aenderungenFreigabe.ts:12) und sendet `POST /wamo/auftrag/<job>/beleg` mit
`{"commit_id", "tree_id"}` und `Authorization: Bearer` an Caddy (Port 55444), der die Anfrage am Kern vorbei an den Freigabe-Dienst reicht, genau wie bei der Release-Freigabe
(infra/sokrates2/wamo-caddy/kern/wamo-core.caddy:28-45). Der Dienst prüft mit den vorhandenen Funktionen Token (`token_pruefen`), aal2 mit jüngstem Code höchstens 60 Sekunden (`aal2_zeit`), Drosselung (8 je 10 Minuten) und aktiven Admin
(`ist_admin`), dazu die Form (Auftrags-ID 16 bis 32 Hexzeichen, Commit-ID und Tree-ID 40 oder 64) und die Regel „ein Code, ein Beleg“. Er antwortet mit `{"ok": true, "beleg": "<Text>", "gilt_s": 120}`.
Die Oberfläche gibt den Beleg an den Kern, der ihn als erste stdin-Zeile an `einreichen <job>` weiterreicht. Der Dispatcher prüft den Beleg selbst, dem Kern wird nichts geglaubt.

**Beleg.** Nutzlast als JSON mit sortierten Schlüsseln: `v` (1), `zweck` (`einreichen`), `job`, `commit`, `tree`, `sub` (Person als UUID aus dem Token), `aal2` (Zeit des Codes), `iat`, `exp` (`iat` plus 120 Sekunden),
`jti` (16 zufällige Hexzeichen). Text: `base64url(Nutzlast)`, Punkt, `base64url(Signatur)`. Signatur: Ed25519 über die Bytes `wamo-beleg-v1:` plus Nutzlast (feste Zweckbindung, kein Austausch mit anderen Signaturen).
Der Dispatcher prüft: Zeichensatz und Länge, Signatur gegen jeden Schlüssel aus `beleg.pub`, dann `v`, `zweck`, `job`, `commit`, `tree` gegen `aenderung.json`, die ersten 8 Hexzeichen von `sub` gegen das `profil` des Auftrags,
`iat` nicht in der Zukunft (5 Sekunden Spiel), `exp` nicht vorbei, `exp` höchstens 120 Sekunden nach `iat`, `iat` höchstens 60 Sekunden nach `aal2`, `jti` unverbraucht. Dass der Code frisch war, hat der Dienst geprüft, `aal2` steht zur Nachvollziehbarkeit im Beleg.

**Ein Code, ein Beleg.** Der Dienst merkt sich (`sub`, `aal2`) mit Auftrag, Commit, Tree und Beleg in `/run/wamo-freigabe/beleg-codes.json` (0600, schreibbar durch `ReadWritePaths`, wamo-freigabe.service:41). Dieselbe Anfrage mit demselben Code liefert
denselben Beleg (verlorene Antwort), jede andere mit demselben Code endet mit dem neuen Fehler `code_benutzt`. Das verhindert, dass ein Kern mit dem Token aus Nikos Sitzung im Fenster von 60 Sekunden einen zweiten Beleg für einen anderen
Auftrag zieht. Er könnte den Beleg für Nikos Auftrag zuerst abholen, dann sieht Niko den Fehler und tippt einen neuen Code. Das fällt auf, es bleibt nicht still.

| Art | Name | Inhalt und Regel |
| --- | --- | --- |
| Route | `POST /wamo/auftrag/<job>/beleg` | Caddy-Matcher `^/wamo/auftrag/[0-9a-f]{16,32}/beleg$` (POST und OPTIONS, Ursprünge der Oberfläche wie bei `@freigeben`), im Dienst `PFAD_RE` ergänzt, alles andere bleibt 404 |
| Modul | `infra/sokrates2/anker/beleg.py` | Regeln ohne Netz und ohne Datei nach dem Muster von `anker/freigabe.py`, der Prozess `freigabe_dienst.py` bekommt nur die Route und die Verdrahtung |
| Schlüssel privat | `/etc/wamo-freigabe/beleg.key` | root:wamo-anker 0640, erzeugt von `freigabe-einrichten.sh beleg` mit `openssl genpkey -algorithm ed25519`, nie ausgegeben, nie überschrieben |
| Schlüssel öffentlich | `/etc/wamo/beleg.pub` | root:wamo 0640, höchstens zwei Zeilen `ed25519 <base64>` (aktueller und voriger Schlüssel, damit eine Erneuerung ohne Lücke geht) |
| Fehlercodes | `code_benutzt` neu | Die übrigen Codes kommen aus `FEHLER` (infra/sokrates2/anker/freigabe.py:53-69), jeder bekommt in der Oberfläche einen Satz |
| Prüfung im Dispatcher | `beleg_pruefen` in `wamo_job_einreichen.py` | nur Standardbibliothek und das Systempaket `cryptography` (Annahme 2), liest `beleg.pub` mit Eigentümer- und Rechteprüfung |
| Oberfläche | `ui/src/lib/auftragEinreichen.ts`, `auftragEinreichenApi.ts`, Karte | reine Logik mit `node --test`, Wiederverwendung von `codeFrisch` und dem Muster der Fehlertabelle |

**Bedrohungen.** Der Kern holt selbst einen Beleg: braucht einen frischen Code, den er nicht kennt, und nur ein Beleg je Code. Der Kern fälscht einen Beleg: Der Schlüssel liegt nur im Freigabe-Dienst, `beleg.key` ist für `wamo-core` nicht lesbar (0640 root:wamo-anker), und `/etc/wamo` liest `wamo-core` nicht
(Hauptpapier, Abschnitt 7). Beleg für anderen Auftrag, Commit oder Tree: Bindung. Replay: `jti` verbraucht. Fremder Admin: Die ersten 8 Hexzeichen von `sub` müssen zum Auftrag passen. Schlüsselerneuerung: zwei Schlüssel in `beleg.pub`.
Freigabe-Dienst oder Caddy aus: Es wird nicht eingereicht (fail-closed), Release-Freigaben sind davon nicht betroffen. Uhrabweichung: Dienst und Dispatcher laufen auf demselben Server.

**Grenze von F2, ehrlich.** F2 verhindert das Einreichen ohne Klick und ohne Beleg. F2 verhindert nicht, dass ein gekaperter Kern Niko eine gefälschte Änderungsübersicht zeigt, denn die Übersicht kommt über den Kern. Der echte Unterschied steht im Pull Request,
und den liest Niko vor dem Merge. Eine spätere Ausbaustufe könnte die Übersicht über den Freigabe-Dienst und den Auftragsdienst beziehen (ab F3 möglich), sie ist hier nicht geplant.

**Tests (rot zuerst).** `voice-core/test_freigabe.py` und `test_freigabe_dienst.py`: Token ungültig, aal2 fehlt oder alt, kein Admin, Drosselung, Form, „ein Code, ein Beleg“ (gleiche Anfrage gleicher Beleg, andere `code_benutzt`), Signatur gegen den öffentlichen Schlüssel, Nutzlast exakt, Route und 404 für alles andere. `test_freigabe_skripte.py`: Probelauf von `beleg` (Rechte, kein Schlüssel in der Ausgabe, zweiter Lauf ändert nichts, ein vorhandener Schlüssel wird nie überschrieben). `test_job_einreichen.py`: jede Prüfung des Dispatchers, dazu ein verändertes Byte, ein fremder Schlüssel, abgelaufen, benutzt, ein Schlüssel von zweien. Erweiterung von `infra/sokrates2/freigabe_tokentest.py`: echtes Token (als `wamocon`) ergibt einen Beleg, den das Modul des Dispatchers akzeptiert. Oberfläche: Code-Frische, jeder Fehlercode hat einen Satz.

### 3.3 F3 und F4: Auftragsdienst mit Unix-Socket, Aufträge überleben das Auflegen

F3 (Dienst) und F4 (Socket mit `SO_PEERCRED`, Kern-Client, Wegfall des Schlüssels) sind ein Entwurf, weil der Dienst ohne den Socket keinen Zugang und der Socket ohne den Dienst keinen Zweck hat.

```text
Kern wamo-core@a|b (User wamo-core, Gruppe wamo-auftrag per Drop-in)
  Unix-Socket /run/wamo-auftrag/auftrag.sock, SO_PEERCRED: uid von wamo-core gibt Kanal kern, uid 0 gibt lokal, sonst Abbruch ohne Antwort
wamo-auftrag.service (User wamo): je Befehl ein Kindprozess /opt/wamo/bin/wamo-job-dispatch mit WAMO_AUFTRAG_KANAL, Regeln 1 bis 9 unverändert im Kindprozess
  start: Pipes für stdin und stdout, die Lebenslinie hält der Dienst offen, jede stdout-Zeile geht erst nach /srv/wamo/jobs/<job>/ereignisse.jsonl, dann an die Verbundenen
```

**Verhalten**

- **Protokoll.** Zeilen JSON in UTF-8. Erste Zeile des Kerns: `{"v": 1, "befehl": "start", "args": ["<job>", "<projekt>", "<art>"]}` (Befehl und Argumente nach `parse_command`, wamo_job_dispatch.py:332-345), danach dieselben stdin-Zeilen wie heute (Kopf, Zugang). Antwort: dieselben Zeilen wie heute auf stdout (stream-json und `wamo`-Ereignisse), zuletzt `{"type": "wamo", "ereignis": "exit", "code": N}`. `status`, `pending`, `approve`, `cancel`, `aenderung`, `einreichen`, `verwerfen` antworten ebenso. Neu, mit Aufnahme in die Befehlsliste des Kern-Kanals: `liste <profil>` (eigene Aufträge mit `job`, `projekt`, `art`, `repo`, `status`, `start`, `ende`, `kosten_usd`, Status der Änderung) und `ereignisse <job> <ab>` (Wiedergabe ab einer laufenden Nummer und, solange der Auftrag läuft, weiter bis zum Ende oder bis die Verbindung endet).
- **Überleben.** Trennt der Kern, löst sich nur der Zuhörer. Der Auftrag läuft bis zum Ende, zum Zeitlimit, zur Kostengrenze, zum Notaus oder zu `cancel`. Die Lebenslinie (stdin des Dispatchers, wamo_job_dispatch.py:2758-2766) hält der Dienst offen. Was nicht überlebt: ein Neustart des Dienstes (`KillMode=control-group`, Rollout nur bei Ruhe) und die Abnahme-Runden des Kerns (Frage 6).
- **Journal.** `ereignisse.jsonl` im Auftragsordner, je Zeile `{"n": <fortlaufend>, "z": <Zeile>}`, höchstens 32 MiB je Auftrag. Bei Überschreitung fallen nur Zeilen vom Typ `assistant` und `user` weg (Ereignis `abgeschnitten`), `wamo`-Ereignisse bleiben. Die Zeilen sind schon geschwärzt (wamo_job_dispatch.py:2831). Kopf- und Zugangszeile des Starts speichert und protokolliert der Dienst nie.
- **Kanal.** Der Dienst setzt im Kindprozess `WAMO_AUFTRAG_KANAL=kern` oder `lokal`. Der Dispatcher kennt die Variable als zusätzliche Quelle in `kanal_bestimmen`. Sie gilt nur, wenn weder `SSH_CONNECTION` noch `SSH_ORIGINAL_COMMAND` gesetzt sind, und sie kann einen Kanal nur herabstufen: Ein Prozess, der sie selbst setzt, bekommt höchstens die Rechte von `kern`, nie die von `pc`.
- **Zweite Schicht am Socket.** Der Dienst prüft vor dem Kindprozess Notaus-Datei, `KANAL` und `ABLAUF` aus `kernkanal.conf` (dasselbe Modul `wamo_job_kernkanal`). Der Kindprozess prüft sie ohnehin.
- **Kern-Client (F4).** `voice-core/auftragsweg.py` mit `SshWeg` (der heutige Code aus `dev_agent_weg.py:57-123`, unverändert) und `SocketWeg`. Auswahl über `WAMO_ENTWICKLUNG_WEG` (`ssh`, Standard, oder `socket`) und `WAMO_ENTWICKLUNG_SOCKET`, beide nur im Drop-in. `dev_agent.py` ruft `self._weg.start(...)` und `self._weg.kurz(...)` statt `ssh_argv` (dev_agent.py:1202-1219,1259-1263). Das Start-Objekt hat `stdin`, `stdout`, `returncode`, `wait()` und `trennen()` wie ein Prozess. `trennen()` beendet den Auftrag nie, nur `cancel` tut das. Dadurch entfällt im Socket-Weg die Absicherung `cancel` bei nicht sauberem Ende (dev_agent.py:1298-1299) und das Abbrechen beim Schließen der Sitzung.
- **Wiederaufnahme.** Beim Sitzungsstart ruft der Kern `liste <profil>`, baut für laufende Aufträge `Job`-Objekte neu, verbindet sich mit `ereignisse <job> <ab>` und sendet `auftrag_liste` an die Oberfläche. Für beendete Aufträge zeigt eine Karte Ergebnis und Änderung. Die Audit-Zeile in `actions` wird nachgebucht (`aktion_abschliessen` mit Status, Dauer und Kosten aus `ergebnis.json`). Offene Zeilen zählen wie bisher mit der Auftragsgrenze, `veraltete_schliessen` greift nach 2 Stunden (Hauptpapier E9).
- **`ergebnis.json`** im Auftragsordner, vom Dienst beim Ende geschrieben: Status, Ende, Kosten (`total_cost_usd` aus der `result`-Zeile), Wendungen, letzter Text des Agenten (geschwärzt, höchstens 4 KB), Status der Änderung.
- **Kein Schlüssel mehr.** Mit `WEG=socket` verlangt `ssh_problem()` weder Schlüssel noch known_hosts. Nach der Bewährung entfernt `wamo-job-kern-rueckweg.sh --ssh-entfernen` Zeile, Schlüssel und known_hosts des Kerns (Rollout I in Abschnitt 5).
- **Unit.** `wamo-auftrag.socket` (`SocketMode=0660`, `SocketUser=wamo`, `SocketGroup=wamo-auftrag`) und `wamo-auftrag.service` als Benutzer `wamo`. Der Ordner `/run/wamo-auftrag` (2750, `wamo:wamo-auftrag`, per `tmpfiles.d`) trägt den Socket. Der Kern bekommt die Gruppe über `SupplementaryGroups=wamo-auftrag` im Drop-in `wamo-entwicklung-buero.conf` (Annahme 8), `/etc/group` für `wamo-core` bleibt unberührt. Härtung: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict` mit `ReadWritePaths=/srv/wamo /run/wamo-auftrag`, `LimitCORE=0`, `MemoryMax`, `TasksMax`, `PR_SET_DUMPABLE` wie `speicher_begrenzen` (wamo_job_dispatch.py:3149-3168). Nicht gesetzt wird `RestrictNamespaces` (bwrap braucht Namensräume), der Syscall-Filter wird stufenweise erprobt (Annahme 5). `XDG_RUNTIME_DIR` zeigt ausdrücklich auf `/run/user/<uid>` (Lingering, wamo-job-einrichten.sh:161-162). Code root-eigen unter `/opt/wamo-auftrag/app` mit `SHA256SUMS` und Selbstprüfung in `ExecStartPre` wie beim Freigabe-Dienst. Beim Start stoppt der Dienst verwaiste Einheiten `wamo-job-*` von Kern-Aufträgen.

**Bedrohungen**

| Bedrohung | Gegenmaßnahme | Restrisiko |
| --- | --- | --- |
| Gekaperter Kern | Dieselben Regeln, derselbe Beleg, derselbe Kanal `kern` wie über ssh. Der Schlüssel auf der Platte entfällt | Wer `wamo-core` übernimmt, nutzt den Socket wie bisher den Schlüssel |
| Anderer lokaler Benutzer am Socket | Ordner 2750 mit Gruppe `wamo-auftrag`, zusätzlich `SO_PEERCRED` mit fester uid-Liste | keins erkennbar |
| Socket-Flut, lange Zeilen | `PARALLEL_MAX` Verbindungen (acht kurze, ein `start`), Zeilengrenzen wie beim Dispatcher, Lesezeit 30 Sekunden je Zeile (`_zeile_lesen`, wamo_job_dispatch.py:2256), `TasksMax` | Ein Dauerstart des Kerns bleibt durch Regel 9 begrenzt |
| Fehler im Dienst | läuft als `wamo` ohne Capabilities, kennt nur den durchgereichten Zugang, wertet nichts selbst aus (Regeln im Kindprozess) | Ein Fehler im Dienst trifft alle Kern-Aufträge, nicht den PC-Weg |
| Journal enthält Gesprächsinhalt | Dateien 0600 `wamo`, Frist 14 Tage (F5), schon geschwärzt | Inhalte von Aufträgen liegen bis dahin auf der Platte |
| Verwaiste Sandboxen nach Absturz | Zeitlimit 1800 s der Sandbox, Aufräumen beim Start des Dienstes | bis zu 30 Minuten |
| Link oder fremder Name im Journalpfad | `job` nur nach `JOB_RE`, Öffnen mit `O_NOFOLLOW` im Auftragsordner | keins erkennbar |

**Fehlerfälle.** Dienst nicht erreichbar: Quittung „Der Auftragsdienst ist nicht erreichbar“, solange der ssh-Schlüssel noch besteht kann der Betreiber `schalter weg ssh` setzen. Verbindung bricht vor der Kopfzeile: Es startet nichts. Fremde Auftrags-ID: `fremd`. Journal voll: siehe oben. Platte voll: Start abgewiesen (`platz`, `quote`). Dienst-Neustart mit laufendem Auftrag: wird verhindert durch die Ruhe-Regel, sonst endet der Auftrag mit `verlassen`.

**Tests (rot zuerst).** Dienst (`voice-core/test_auftragsdienst.py`, neu): fremde uid am Socket, Zeilen- und Größengrenzen, Pipes mit Attrappe des Dispatchers, Überleben (Client trennt, der Auftrag läuft weiter), nummeriertes Journal und Wiedergabe ab Nummer, Notaus am Socket, kein Zugangswert in Journal, Log und `ergebnis.json` (Dummy-Wert), Start räumt verwaiste Einheiten. Kern: `SocketWeg` gegen einen Attrappe-Server (Protokoll, `exit`-Ereignis, `trennen()` ruft kein `cancel`), `SshWeg` und alle bisherigen Tests unverändert grün, Wiederaufnahme (`liste`, `Job`, Nachbuchung, offene Zeile zählt), `ssh_problem` je nach Weg. Dispatcher: `WAMO_AUFTRAG_KANAL` stuft nur herab und gilt nie neben `SSH_CONNECTION`. Vertragstest: Befehlsliste von Dienst, Dispatcher und Kern gleich.

### 3.4 F5: Aufräumen der Kern-Ordner und Plattenquote

**Verhalten.** Ein täglicher Lauf (`wamo-auftrag-aufraeumen.timer`, Dienst `Type=oneshot` als `wamo`, Modul `wamo_job_aufraeumen.py`) wendet diese Fristen an. Die Werte stehen in `kernkanal.conf`.

| Gegenstand | Bedingung | Aktion |
| --- | --- | --- |
| Auftragsordner mit `kanal=kern` im Status `eingereicht`, `verworfen`, `fehler`, `keine` oder beendet | älter als `AUFBEWAHREN_TAGE` (Standard 14) seit dem Ende | schwere Teile löschen (`claude/`, `npm/`, `cache/`, `plugin-daten/`, `lauf.log*`, `ereignisse.jsonl`, `ausgang.git`), Marker bleiben (`auftrag.json`, `status.json`, `ergebnis.json`, `aenderung.json`) |
| Marker | älter als 90 Tage (fest) | ganz löschen |
| Änderung im Status `bereit` | älter als `AENDERUNG_TAGE` (Standard 7) | Status `abgelaufen`, danach wie oben. Nie automatisch veröffentlichen |
| Auftrag laufend, `einreichen_laeuft`, `branch_gepusht` | immer | nie anfassen |
| Projektordner `kern-*` | kein laufender oder `bereiter` Auftrag verweist darauf, letzte Änderung älter als `PROJEKT_TAGE` (Standard 7) | Inhalt und Ordner löschen |

- **Sicherheit des Löschens.** Nur Namensraum `kern-` und Aufträge mit `kanal=kern` (ein Auftrag ohne das Feld gehört nie dem Kern, Arbeitsliste I8), PC-Aufträge und PC-Projekte bleiben unberührt. Gelöscht wird mit Deskriptoren ohne Links zu verfolgen und mit `fchmod` auf Verzeichnisse (die Sandbox kann Rechte entziehen, Muster `projekt_entschaerfen`, wamo_job_dispatch.py:1629-1651). Nie ein Ordner, dessen Sperre `.lauf` gehalten wird oder dessen Einheit aktiv ist. `AUFRAEUMEN` in `kernkanal.conf` steht auf `trocken` (Standard, protokolliert nur) oder `an`.
- **Quote.** `MAX_KERN_MB` (Standard 10240, 100 bis 1 000 000) ist die Summe aus Projektordnern `kern-*` und Kern-Auftragsordnern. Der Wert steht in `/srv/wamo/logs/kern-platz.json` (vom Aufräumlauf und am Ende jedes Laufs für den eigenen Ordner aktualisiert). Beim Start gilt: ist er größer als die Quote oder älter als 2 Tage, wird mit dem neuen Grund `quote` abgewiesen. `MIN_PLATZ_MB` bleibt.
- **Wache während des Laufs.** Ein Thread `platz_wache` neben `egress_wache` (wamo_job_dispatch.py:2768-2776) misst alle 20 Sekunden den Projektordner mit `du -sx --apparent-size` (Zeitlimit 10 Sekunden) und stoppt den Lauf bei mehr als `MAX_PROJEKT_MB` (Standard 3072) mit Ergebnis `platz`. Eine Dateisystemquote ist nicht geplant (Annahme 6).
- **Sichtbarkeit.** `wamo-job-kern-einrichten.sh status` zeigt Zahl, Größe und ältesten Auftrag der Kern-Ordner und die Quote.

**Tests (rot zuerst).** Fristen am Rand, Marker, Namensraum (ein PC-Auftrag mit gleichem Projektnamen bleibt), Link und nicht lesbares Verzeichnis im Projektordner, laufender Auftrag, `bereit` unter und über der Frist, `trocken` ändert nichts. Wache mit Attrappe von `du`: Lauf wird gestoppt, Ergebnis `platz`, Wert nicht lesbar heißt kein Stopp mit Logzeile. Start mit zu alter oder zu großer Quote ergibt `quote`.

### 3.5 F7: `expiry-time` in der Schlüsselzeile des Kerns

**Verhalten.** Die Zeile wird `restrict,from="192.168.178.136",expiry-time="<JJJJMMTT>",command="/opt/wamo/bin/wamo-job-dispatch" ssh-ed25519 <Schlüssel> wamo-kern-jobs`. Der Wert ist der Tag nach `ABLAUF`, weil ein
reines Datum ab 00:00 Systemzeit gilt (Annahme 7), `ABLAUF` aber „bis einschließlich“ gilt (Hauptpapier 4.5). Der Dispatcher bleibt die erste Schicht (hörbarer Grund `abgelaufen`), sshd die zweite (nur „Schlüssel abgelehnt“). `schluessel --ablauf`
schreibt bei einer Verlängerung zuerst die Zeile und dann `kernkanal.conf`, bei einer Verkürzung zuerst `kernkanal.conf`, damit sshd nie früher abläuft als der Dispatcher. `status` meldet jede Abweichung zwischen beiden Werten.
Die Ja-Probe `ping` und die Rücknahme der Zeile bei einem Fehler (wamo-job-kern-einrichten.sh:493-494 ff.) bleiben: Eine ungültige Option macht die Zeile ungültig, die Probe fängt das.

**Probeschlüssel (Beleg, dass sshd den Ablauf durchsetzt).** Das Skript `wamo-job-kern-ablaufprobe.sh` (root, schreibt nichts in die Produktion) startet eine zweite sshd-Instanz auf `127.0.0.1:22222` mit eigener Konfiguration, eigenem Hostschlüssel, eigener `authorized_keys`,
`ForceCommand` auf einen festen Echo-Aufruf und einem Wegwerf-Schlüssel, der danach gelöscht wird. Es belegt in dieser Reihenfolge und hält das Ergebnis als Tabelle fest: (A) `expiry-time` drei Minuten voraus (Form `JJJJMMTTHHMM`): Anmeldung gelingt. (B) Nach Ablauf: Anmeldung scheitert, die Journalzeile wird
wörtlich festgehalten. (C) Eine vor dem Ablauf geöffnete Sitzung bleibt bestehen. (D) Reines Datum von gestern wird abgelehnt, von morgen angenommen, vom heutigen Tag nach der beobachteten Grenze notiert. (E) Ein Tippfehler im Optionsnamen macht die Zeile ungültig. (F) Zeitzone der Grenze laut `timedatectl`. Die Produktion wird damit nie berührt, die Zeile der Produktion prüft nur der Ja-Test von `schluessel`.

**Lebensdauer, offen gesagt.** F7 schützt nur den ssh-Schlüssel des Kerns und endet mit seinem Abbau in Rollout I (3.3). Danach gibt es keine Zeile mehr. Für den PC-Weg gilt F7 nicht: Der Schlüssel in `authorized_keys.d/wamo` gehört dem Laptop, ein Ablauf würde den PC aussperren, und
ein Ablauf dort wäre eine eigene Entscheidung. Für den Socket-Weg gibt es ebenfalls zwei Schichten, nämlich `ABLAUF` am Socket im Auftragsdienst und im Dispatcher (3.3). Folgt der Abbau des Schlüssels innerhalb weniger Wochen, schützt F7 nur das Fenster der Stufen 3a bis 4, in dem der Schlüssel
mit Aufträgen lebt. Empfehlung: trotzdem bauen, es sind 4 Stunden und das Fenster ist das riskanteste.

**Tests (rot zuerst).** `voice-core/test_job_kern_skripte.py:882` kehrt sich um (die Zeile enthält `expiry-time` mit dem Folgetag von `ABLAUF`). Neu: Verlängerung und Verkürzung in der richtigen Schreibreihenfolge, Abweichung in `status`, Rücknahme bei ungültiger Zeile, `wamo_job_kern_rauchtest.py:385-386` prüft `expiry-time` passend zu `ABLAUF`. Die Probe selbst ist nur am Server prüfbar und als solche gekennzeichnet.

### 3.6 Vorgeschlagene neue Namen

Alle Namen folgen dem Namensvertrag in CLAUDE.md (Präfix `wamo`, Env `WAMO_`, Bezeichner in ASCII). FP1-16 trägt sie dort ein.

| Bereich | Name | Anmerkung |
| --- | --- | --- |
| Dispatcher | Version `8`, Befehle `aenderung`, `einreichen`, `verwerfen`, `liste`, `ereignisse` | höchstens 10 Buchstaben (`COMMAND_RE`) |
| Dispatcher-Module | `infra/sokrates2/wamo_job_aenderung.py`, `wamo_job_einreichen.py` | installiert nach `/opt/wamo/waechter` |
| Zweig | `wamo/<job>` | feste Form, `<job>` nach `JOB_RE` |
| Dateien im Auftragsordner | `aenderung.json`, `einreichen.json`, `ausgang.git`, `.einreichen`, `ereignisse.jsonl`, `ergebnis.json` | für die Sandbox unsichtbar |
| Zähler und Platzdatei | Ereignis `einreichen` in `/srv/wamo/logs/kernkanal.jsonl`, `/srv/wamo/logs/kern-platz.json` | |
| Schlüssel in `kernkanal.conf` | `EINREICHEN`, `MAX_EINREICHEN_TAG`, `MAX_AENDERUNG_DATEIEN`, `MAX_AENDERUNG_MB`, `AUFRAEUMEN`, `AUFBEWAHREN_TAGE`, `AENDERUNG_TAGE`, `PROJEKT_TAGE`, `MAX_KERN_MB`, `MAX_PROJEKT_MB` | Bereiche und Standardwerte in 3.1.2 und 3.4 |
| Grund-Codes | `pfad_gesperrt`, `geheimnis`, `zu_gross`, `symlink`, `sonderdatei`, `anomalie`, `schon_eingereicht`, `basis_fehlt`, `einreichen_aus`, `zustand`, `beleg_fehlt`, `beleg_ungueltig`, `beleg_abgelaufen`, `beleg_falsch`, `beleg_benutzt`, `grenze_einreichen`, `zweig_belegt`, `github_fehler`, `quote` | Sätze im Kern als Schlüssel von `GRUND_SAETZE` |
| Umgebung | `WAMO_EINREICHEN` (Sandbox, Wert `dispatcher`), `WAMO_AUFTRAG_KANAL` (Kindprozess des Dienstes), `WAMO_ENTWICKLUNG_WEG`, `WAMO_ENTWICKLUNG_SOCKET` (Kern, nur im Drop-in) | nie in `core.env` und nie in `ERLAUBT` |
| Beleg | Route `/wamo/auftrag/<job>/beleg`, `infra/sokrates2/anker/beleg.py`, `/etc/wamo-freigabe/beleg.key`, `/etc/wamo/beleg.pub`, `/run/wamo-freigabe/beleg-codes.json`, Aufruf `freigabe-einrichten.sh beleg`, Zweck `einreichen`, Präfix `wamo-beleg-v1:` | |
| Auftragsdienst | `wamo-auftrag.service`, `wamo-auftrag.socket`, Gruppe `wamo-auftrag`, Socket `/run/wamo-auftrag/auftrag.sock`, Code `/opt/wamo-auftrag/app` (`wamo_auftragsdienst.py`), `wamo-auftrag-einrichten.sh`, `wamo-auftrag-rueckweg.sh`, `wamo_auftrag_rauchtest.py` | läuft als vorhandener Benutzer `wamo`, kein neuer Benutzer |
| Aufräumen | `wamo_job_aufraeumen.py`, `wamo-auftrag-aufraeumen.service`, `wamo-auftrag-aufraeumen.timer` | |
| Ablaufprobe | `infra/sokrates2/wamo-job-kern-ablaufprobe.sh` | nur Probe, nie produktiv |
| Skriptformen | `wamo-job-kern-einrichten.sh schalter weg ssh\|socket <release>`, `wamo-job-kern-einrichten.sh kanal --einreichen an\|aus`, `wamo-job-kern-rueckweg.sh --ssh-entfernen` | Aktionen `weg` und `einreichen` in `/var/log/wamo-job-kern/aktionen.jsonl` |
| Kern und Oberfläche | `voice-core/auftragsweg.py`, `voice-core/entwicklung_einreichen.py`, `ui/src/lib/auftragEinreichen.ts`, `auftragEinreichenApi.ts`, `ui/src/components/AenderungKarte.tsx`, Nachrichten `aenderung`, `einreichen`, `verwerfen`, `einreichen_quittung`, `auftrag_liste` | |
| Audit | `actions.tool = auftrag_einreichen` | ohne Migration |

## 4. Reihenfolge, Abhängigkeiten und Pakete

```text
FP1-0 Review und Bestandsaufnahme  ->  alle anderen (Annahmen 1, 2, 5, 6 müssen vorher geklärt sein)
FP1-1 F7 (unabhängig, sofort)
Gruppe 1: FP1-2 -> FP1-3 -> FP1-4 -> FP1-6 -> FP1-9 (Prüfstrecke und Rollout F1, F2) -> FP1-10 -> FP1-14
Gruppe 2: FP1-5 (Beleg, parallel zu FP1-2 bis FP1-4), FP1-7 und FP1-8 (ab Vertrag 3.1.2, parallel), FP1-11 -> FP1-12 -> FP1-13, FP1-15 (parallel zu FP1-10), FP1-16 am Ende
```

Typ: **R** Repo, **S** Server (nur mit Verbindung, nur bei Ruhe, Alias `wamo-sokrates2`, aus Geheimnisdateien nur Namen), **O** Oberfläche. Aufwand in Stunden, Schätzung für einen Umsetzer. Jedes Paket beginnt mit den roten Tests (Abschnitt 3) und endet
mit den genannten Prüfern. Die Regeln der Arbeitsliste gelten weiter: gezielte Testläufe aus PowerShell, `uv sync` nie, der volle Lauf einmal am Ende, vor jedem Commit der Fremdverweis-Grep und der Secret-Scan.

| Nr. | Paket (Typ) | Dateien | Verhalten und Abnahme | Std. | Abhängig von, parallel |
| --- | --- | --- | --- | --- | --- |
| FP1-0 | Review und Bestandsaufnahme (S, lesend) | keine, Notiz mit den Ergebnissen im PR | `security-reviewer` liest dieses Papier (ein Durchgang, Befunde kommen in Abschnitt 7). Am Server nur lesen: Annahmen 1, 2, 5 (Vorprobe), 6, dazu Versionen von `git`, `cryptography`, `systemd`, Lingering und Zeitzone. Abnahme: jede Annahme hat ein Ergebnis | 2,5 | zuerst, parallel zu FP1-1 |
| FP1-1 | F7 `expiry-time` (R und S) | `wamo-job-kern-einrichten.sh`, `wamo_job_kern_rauchtest.py`, `wamo-job-kern-ablaufprobe.sh` (neu), `voice-core/test_job_kern_skripte.py`, `infra/sokrates2/README.md` | 3.5. Abnahme: Probeschlüssel belegt Durchsetzung (Proben A bis F), Zeile mit `expiry-time`, Ja-Probe `ping`, Rauchtest `--produktiv` grün | 4 | FP1-0 für Annahme 7, sonst frei |
| FP1-2 | Grundlage in Kern-Kanal und Wächter (R) | `wamo_job_kernkanal.py`, `wamo_job_dispatch.py` (Version 8, Lese-Token im Kern-Kanal, neue Befehle zunächst abweisend, `WAMO_EINREICHEN`), `voice-core/dev_guard.py`, `dev_agent_regeln.py`, `infra/sokrates2/wamo-job.sh` (Allowlist der Umgebung), `wamo-job-kern-einrichten.sh` (Konfigurationsschlüssel), `test_kernkanal.py`, `test_sokrates2_dispatch.py`, `test_dev_guard.py` | 3.1.5. Abnahme: alle bisherigen Tests grün (außer der Versionszeile), PC-Kanal unverändert, `security-reviewer` und `python-reviewer` ohne CRITICAL und HIGH | 6 | FP1-0 |
| FP1-3 | Arbeitskopie und Basis (R) | `wamo_job_aenderung.py` (neu, Teil 1), `wamo_job_dispatch.py` (Einhängepunkt), `wamo-job-einrichten.sh` und `wamo-job-rueckweg.sh` (Modulliste an den sechs Stellen), `test_job_aenderung.py` (neu) | 3.1.1 Schritt 2: `ausgang.git`, Klon, Basis, Leeren des Projektordners nur beim ersten Lauf, jeder Fehler bricht den Start (Exit 8). Abnahme: Tests grün, Probelauf im Testrepo ohne Lauf der Sandbox | 6 | FP1-2 |
| FP1-4 | Snapshot und Änderungsübersicht (R) | `wamo_job_aenderung.py` (Teil 2), `wamo_job_dispatch.py` (Aufruf nach dem Lauf, Befehle `aenderung` und `verwerfen`), `test_job_aenderung.py` | 3.1.3 vollständig mit Ereignis und `aenderung.json`. Abnahme: alle Snapshot-Tests aus 3.1.8, `security-reviewer` ohne CRITICAL und HIGH (Pflicht, Schwerpunkt Deskriptoren, Pfade, Geheimnisse), ein Lauf am Server gegen das Testrepo ohne Einreichen | 14 | FP1-3 |
| FP1-5 | Beleg im Freigabe-Dienst (R und S) | `anker/beleg.py` (neu), `anker/freigabe_dienst.py`, `freigabe-einrichten.sh`, `freigabe-rueckweg.sh`, `wamo-caddy/kern/wamo-core.caddy`, `wamo-core-caddy.sh`, `freigabe_rauchtest.py`, `freigabe_tokentest.py`, `test_freigabe.py`, `test_freigabe_dienst.py`, `test_freigabe_skripte.py` | 3.2, Dienstseite. Abnahme: Tests grün, Probebeleg mit echtem Token (als `wamocon`), `security-reviewer` ohne CRITICAL und HIGH | 9 | FP1-0, parallel zu FP1-2 bis FP1-4 |
| FP1-6 | Einreichen im Dispatcher (R) | `wamo_job_einreichen.py` (neu), `wamo_job_dispatch.py`, `wamo_job_kernkanal.py`, `wamo-job-einrichten.sh`, `wamo-job-rueckweg.sh`, `wamo_job_rauchtest.py` (Attrappe), `test_job_einreichen.py` (neu) | 3.1.4 und Beleg-Prüfung nach 3.2. Abnahme: alle Tests aus 3.1.8 zum Einreichen, Attrappe-Rauchtest grün, `security-reviewer` und `python-reviewer` ohne CRITICAL und HIGH | 14 | FP1-4 und das Belegformat aus 3.2 (beginnt vor FP1-5, wenn das Format festliegt) |
| FP1-7 | Kern: Verarbeitung, Quittung, Audit (R) | `voice-core/entwicklung_einreichen.py` (neu), `entwicklung_betrieb.py` (Sätze, `GRUND_SAETZE`), `dev_agent.py` (dünne Einhängepunkte), `test_entwicklung_betrieb.py`, `test_dev_agent_sokrates2.py`, `test_kernkanal_vertrag.py` | 3.1.2, Kernseite: Ereignis `aenderung`, Nachrichten, `einreichen_quittung`, Zeile `auftrag_einreichen`. Abnahme: Tests grün, jeder Grund hat einen Satz, kein Zugangswert in Log und Zeile | 8 | Vertrag aus 3.1.2, parallel zu FP1-4 |
| FP1-8 | Oberfläche: Karte Änderung und Dialog (O) | `ui/src/components/AenderungKarte.tsx` (neu), `Freigaben.tsx` (Einhängung), `ui/src/lib/auftragEinreichen.ts`, `auftragEinreichenApi.ts`, `nachrichten.ts`, `useWamo.ts`, zugehörige Tests | Karte mit Übersicht und Markierungen, Dialog mit frischem Code, Beleg holen, Quittung, WMC-Design (Rot, Grau, kein Blau). Abnahme: `node --test` und Typprüfung grün, `react-reviewer` ohne CRITICAL und HIGH | 12 | Vertrag aus 3.1.2 und 3.2, parallel zu FP1-4 bis FP1-6 |
| FP1-9 | Prüfstrecke und Rollout F1, F2 (R und S) | Pull Request, `verification-loop`, die drei Reviewer, Rollout A bis E (Abschnitt 5), Proben zu den Annahmen 3 und 4 | Abschnitt 5, Stufen A bis E. Abnahme: alle Stufen bestanden, Negativproben ohne Ja | 10 | FP1-1 bis FP1-8, Testrepo und App-Auswahl von Niko (N1, N2) |
| FP1-10 | Auftragsdienst (R) | `infra/sokrates2/wamo_auftragsdienst.py` (neu), `wamo_job_dispatch.py` (`WAMO_AUFTRAG_KANAL`, `liste`, `ereignisse`, `ergebnis.json`), `wamo_job_kernkanal.py`, `voice-core/test_auftragsdienst.py` (neu) | 3.3, Dienst. Beginnt mit einem Spike (Sandbox aus einer gehärteten Systemunit, Annahme 5, etwa 3 Stunden), dann der Dienst. Abnahme: Tests grün, Spike-Ergebnis festgehalten, `security-reviewer` ohne CRITICAL und HIGH | 18 | technisch frei, empfohlen nach FP1-9 |
| FP1-11 | Auftragsdienst: Einrichtung und Rauchtest (R und S) | `wamo-auftrag.service`, `wamo-auftrag.socket`, `wamo-auftrag-einrichten.sh`, `wamo-auftrag-rueckweg.sh`, `wamo_auftrag_rauchtest.py`, `tmpfiles.d`, `voice-core/test_auftrag_skripte.py` (neu) | Einrichten ohne Wirkung (der Kern bleibt auf ssh). Ja- und Nein-Proben als `wamo-core` mit der Gruppe (Annahme 8). Abnahme: Probelauf-Tests grün, Server-Proben als „nur am Server prüfbar“ gekennzeichnet | 8 | FP1-10 |
| FP1-12 | Kern-Client und Wiederaufnahme (R) | `voice-core/auftragsweg.py` (neu), `dev_agent.py`, `dev_agent_weg.py`, `entwicklung_betrieb.py` (`weg_problem`, `SCHRANKEN_VERSION = 2`), `wamo-job-kern-einrichten.sh` (`schalter weg`), zugehörige Tests | 3.3, Kernseite. Abnahme: `SshWeg` und alle bisherigen Tests unverändert grün, `SocketWeg` gegen Attrappe, Wiederaufnahme, Nachbuchung | 14 | FP1-10, FP1-7 |
| FP1-13 | Oberfläche: Auftragsliste und Wiederaufnahme (O) | `ui/src/lib/nachrichten.ts`, `useWamo.ts`, Karte für beendete Aufträge, Tests | `auftrag_liste`, Hinweis „läuft weiter“ beim Neuladen statt der Warnung. Abnahme: `node --test` und Typprüfung grün, `react-reviewer` | 6 | Vertrag aus FP1-12 |
| FP1-14 | Umschalten und Abbau des ssh-Schlüssels (S) | `wamo-job-kern-rueckweg.sh` (`--ssh-entfernen`), `wamo-job-kern-einrichten.sh`, `wamo_job_kern_rauchtest.py`, `infra/sokrates2/README.md` | Rollout H und I (Abschnitt 5) | 5 | FP1-11, FP1-12, FP1-13 |
| FP1-15 | F5 Aufräumen und Quote (R und S) | `wamo_job_aufraeumen.py` (neu), `wamo-auftrag-aufraeumen.service` und `.timer`, `wamo_job_dispatch.py` (`platz_wache`, Grund `quote`), `wamo_job_kernkanal.py`, `wamo-job-kern-einrichten.sh` (`status`, Schlüssel), `voice-core/test_job_aufraeumen.py` (neu) | 3.4, Rollout F. Abnahme: Tests aus 3.4, Trockenlauf eine Woche ohne Fund außerhalb von `kern-` | 10 | FP1-4 (Ordnerstruktur), parallel zu FP1-10 |
| FP1-16 | Doku, Namensvertrag, Vertragstest (R) | `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `infra/sokrates2/README.md`, `voice-core/test_kernkanal_vertrag.py`, beide `.env.example` | Namen aus 3.6 eintragen, Vertragstest erweitern (Gründe, Befehlslisten, Belegformat, Standardwerte), echte Umlaute, keine Gedankenstriche, Fremdverweis-Grep leer, keine Geheimnisse und keine `user_id` | 5 | alle |

**Aufwand.** Summe etwa 150 Stunden (151,5). Mit zwei Umsetzern (Gruppe 1: FP1-1, 2, 3, 4, 6, 9, 10, 14 mit 77 Stunden, Gruppe 2: FP1-5, 7, 8, 11, 12, 13, 15, 16 mit 72 Stunden, FP1-0 gemeinsam) sind das zehn bis zwölf Arbeitstage Kalenderzeit ohne die Bewährungs- und Wartezeiten des Rollouts. F1 und F2 allein (FP1-0 und FP1-2 bis FP1-9) sind etwa 82 Stunden,
etwa sechs Arbeitstage mit zwei Umsetzern. Die Schätzung hat etwa 30 Prozent Unsicherheit, die größten Faktoren sind FP1-4 (unvertraute Dateien sicher lesen), FP1-10 (Sandbox aus einer Systemunit) und die Proben gegen GitHub (Annahmen 3 und 4).

**Empfehlung für den Beginn.** FP1-0 und FP1-1 sofort, sie sind klein und unabhängig, und F7 schützt genau das Fenster, in dem der Kern-Schlüssel mit Aufträgen lebt. Danach das Rückgrat FP1-2, FP1-3, FP1-4 in einer Gruppe und FP1-5 parallel in der anderen. Der Snapshot (FP1-4) ist die riskanteste Grenze des Entwurfs
(unvertraute Dateien werden zu einem vertrauten Commit), er hängt weder an Beleg noch an GitHub-Rechten und lässt sich früh prüfen, und er liefert schon vorher einen Nutzen: die Änderungsübersicht für die Stufen 3b und 4. Der Transportwechsel (FP1-10 bis FP1-14) folgt danach, aus drei Gründen:
Vor Stufe 5 müssen F1 und F2 stehen (Arbeitsliste Abschnitt 7), F3 und F4 sind nicht Voraussetzung dafür. Der Wechsel setzt stabile Befehle voraus, und die Befehle von F1 sind transportneutral entworfen. Der Dienst ist das Teil mit der meisten Unsicherheit (Annahme 5), und ein Fehler dort sollte nicht F1 und F2 aufhalten. Hält Niko den Schlüssel auf der
Platte für das größere Risiko als das fehlende Einreichen, ließe sich FP1-10 vorziehen, ich empfehle es nicht, weil bis zur Stufe 5 ohnehin nur das Testrepo und Recherche laufen.

## 5. Rollout je Stufe mit Rückweg

Jede Stufe beginnt erst, wenn die Prüfung der vorigen bestanden ist. **Ruhe** heißt wie im Hauptpapier (Abschnitt 6): `ps -u wamo` leer, kein offener Totmannschalter, keine Entwicklungssitzung im Journal, dazu ab Stufe G: `liste` (lokal als `wamo`) zeigt keinen laufenden Kern-Auftrag.
Der Kanal bleibt zu (`KANAL=zu`), bis Stufe E (F1) und Stufe H (F3, F4) ihre Abnahme bestanden haben. Nach jedem Transportwechsel wird die Abnahme der zuletzt offenen Stufe wiederholt (je drei Aufträge, einer davon mit Auflegen), bevor der Kanal weiter geöffnet wird.

| Stufe | Schritte | Vorbedingung | Prüfung | Abbruch | Rückweg |
| --- | --- | --- | --- | --- | --- |
| A F7 | `schluessel --ablauf <JJJJMMTT>` schreibt die Zeile mit `expiry-time` neu | FP1-1 gemergt, Probe bestanden, Ruhe, Kanal zu | Ja-Probe `ping` als `wamo-core`, `status` ohne Abweichung, Rauchtest `--produktiv` grün | Probe rot, Zeile fehlerhaft | Rücknahme durch das Skript, sonst die Sicherung unter `/var/backups/wamo-job-kern/<Zeit>/` zurückkopieren |
| B Dispatcher 8 ohne Wirkung | `verteilen`, `wamo-job-einrichten.sh` (Ruhe: startet `wamo-egress` neu, installiert auch den geänderten Runner `wamo-job.sh`), Bestätigung über eine neue sshd-Verbindung binnen 5 Minuten. `EINREICHEN` fehlt, gilt als aus | FP1-2 bis FP1-4, FP1-6 gemergt, Ruhe | `ping` meldet 8, vom PC `kanal=pc`, Kern-`ping` `kanal=kern`. `wamo-job-rauchtest --mcp --attrappe` grün, ein PC-Auftrag läuft wie bisher | Rauchtest rot, PC gestört | Totmannschalter, `wamo-job-rueckweg.sh` (verweigert, solange `wamo-kern` besteht: erst `notaus`) |
| C Beleg im Freigabe-Dienst | `freigabe-einrichten.sh` (startet den Dienst neu, erzeugt `beleg.key`, legt `beleg.pub` ab), `wamo-core-caddy.sh` für die zweite Route (Caddy lädt neu) | FP1-5 gemergt, keine Release-Freigabe offen | `freigabe_rauchtest.py --produktiv`, Probebeleg mit echtem Token, das Modul des Dispatchers akzeptiert ihn offline, die Release-Route verhält sich unverändert | Rauchtest rot, Release-Route gestört | `freigabe-rueckweg.sh`, `wamo-core-caddy.sh --route-zurueck` |
| D Kern und Oberfläche | Kern-Release mit `wamo-core-slots.sh release`, `umschalten-kern`, `bewaehren 30`, `ausleiten`, danach `wamo-ui-einrichten.sh umschalten`. Das Drop-in bleibt, der Kanal bleibt zu | FP1-7, FP1-8 gemergt, Ruhe für `ausleiten` | Journal wie bisher (Entwicklung für Niko an), die Karte Änderung erscheint nur bei einem Ereignis `aenderung`, die Bewährung der Oberfläche ohne Fehler | Bewährung rot | `rueckweg-kern`, `wamo-ui-einrichten.sh zurueck` |
| E F1 abnehmen (Stufe 4 mit F1) | **Niko, manuell:** Ja, Testrepo (N2), App-Auswahl (N1). `kanal --arten test,refactoring --repos <owner>/<testrepo> --einreichen an` | A bis D bestanden, Stufe 3b bestanden (Lese-Token, Fork-Prüfung) | Drei Aufträge Änderung, Karte, Code, Beleg, Pull Request als Entwurf. Nein-Proben: kein Beleg, Beleg eines anderen Auftrags, abgelaufen, benutzt, Notaus bei `bereit`, Änderung unter `.github/`, Link auf eine Serverdatei, Geheimnis im Inhalt, Fork-Probe. Draft- und Workflow-Probe (Annahmen 3 und 4). Im Dispatcher-Log kein Push ohne Beleg, Token nur für das Testrepo | ein Nein wird Ja, Push ohne Beleg, Token für mehr als ein Repo | `kanal --einreichen aus`, Arten zurück, `kanal zu`, im Zweifel `notaus` |
| F F5 | Timer aktiv mit `AUFRAEUMEN=trocken`, nach einer Woche Protokoll prüfen, dann `an`. Quote und Wache ein | FP1-15 gemergt | Trockenlauf listet nur `kern-`-Dinge, erste echte Löschung mit Stichprobe, die Wache stoppt einen Probelauf mit Dummy-Füllung | Fund außerhalb von `kern-` | `AUFRAEUMEN=trocken`, Timer stoppen |
| G Auftragsdienst ohne Wirkung | `verteilen`, `wamo-auftrag-einrichten.sh` (Gruppe, Units, `tmpfiles.d`, Code mit Prüfsummen), Ruhe, weil das Skript die Module des Dispatchers mitführt | FP1-10, FP1-11 gemergt | Rauchtest als `wamo-core` mit der Gruppe: Ja für `ping` über den Socket. Nein für fremde uid, zu lange Zeile, `start` bei geschlossenem Kanal, `einreichen` ohne Beleg. PC-Weg und ssh-Weg des Kerns unverändert | Probe rot | `wamo-auftrag-rueckweg.sh` |
| H Kern auf den Socket | Kern-Release mit `auftragsweg.py` (Standard `ssh`), `umschalten-kern`, dann `schalter weg socket <release>` (Drop-in mit Weg, Socket und Gruppe), `bewaehren 30`, `ausleiten`. Der ssh-Schlüssel bleibt | G, FP1-12, FP1-13 gemergt, Ruhe, `liste` ohne laufenden Auftrag | Drei Aufträge über den Socket, einer mit Auflegen während des Laufs, danach neue Sitzung: Ergebnis, Änderung, Nachbuchung in `actions`. Notaus mit laufendem Auftrag. Absturz des Dienstes | Auftrag geht beim Auflegen verloren, Nachbuchung fehlt | `schalter weg ssh <release>` mit `umschalten-kern` (der Schlüssel besteht noch) |
| I Abbau | nach zwei Wochen Socket-Betrieb ohne Befund und Nikos Ja: `wamo-job-kern-rueckweg.sh --ssh-entfernen` | H bewährt | Auf der Platte von `wamo-core` liegt kein Schlüssel und keine known_hosts, `ssh_problem()` schweigt bei `WEG=socket`, Aufträge laufen, F7 und die Zeilenprüfung des Rauchtests sind entfallen | Auftrag scheitert | `schluessel` legt ein neues Paar an, `schalter weg ssh <release>` |

**Slots und Auslieferung.** Der Kern wechselt ausschließlich über `wamo-core-slots.sh` (`release`, `umschalten-kern`, `bewaehren 30`, `ausleiten`, `rueckweg-kern`), nie über `systemctl restart wamo-core*`. `wamo-core-slots.sh`, `wamo-core-vorpruefung.sh` und `wamo-core@.service` bleiben byte-gleich (Diff-Test
in FP1-16 und im PR), neue Kern-Einstellungen stehen nur im Drop-in `wamo-entwicklung-buero.conf`, das `schalter` schreibt. Ältere Releases kennen die neuen Variablen nicht und starten mit dem alten Verhalten, ein Anker-Rückweg ist damit ohne Eingriff am Drop-in möglich. Auslieferung mit `scripts/wamo_kern_sokrates2.py verteilen`, dessen Zusatzliste mit jedem Paket wächst
(neue Module, Skripte, Units, `anker/beleg.py`). Ein Neustart von `wamo-auftrag.service` und jede Änderung an `wamo-job-einrichten.sh` brauchen Ruhe. `freigabe-einrichten.sh` braucht keine Auftragsruhe, aber keine offene Release-Freigabe.

**Notaus.** Die feste Reihenfolge aus Hauptpapier 4.4 gilt weiter: (1) Sperrdatei anlegen (Dispatcher und Auftragsdienst beachten sie, `einreichen` ist gesperrt), (2) solange der ssh-Schlüssel besteht die Zeile sichern und löschen, am Socket weist der Dienst alles außer `ping` und `status` ab,
(3) laufende Kern-Aufträge mit lokalem `cancel` beenden, die Kindprozesse des Dienstes enden mit, (4) erneut suchen und festhalten. Als letztes Mittel stoppt der Betreiber `wamo-auftrag.socket` und `wamo-auftrag.service`. Das ist ein Eingriff nur in den Auftragsdienst, nie in den Kern, und die einzige Ausnahme von der Regel „kein `systemctl stop` in den Kern-Skripten“, die der Test benennt.
Ein Notaus ändert den Zustand `bereit` nicht: Die Änderung bleibt für `verwerfen` und für ein Einreichen nach `notaus-aufheben` erhalten.

**Übergang vom ssh-Weg zum Socket-Weg ohne Lücke.** Beide Wege bestehen gleichzeitig, Dispatcher, Regeln und Beleg sind dieselben. Der Kern wählt den Weg je Slot über das Drop-in, alte Sitzungen bleiben auf ihrem Slot und damit auf ihrem Weg. Ein Auftrag wandert nie von einem Weg auf den anderen, darum schaltet `schalter weg` nur bei Ruhe.
`PARALLEL_MAX = 1` gilt über beide Wege, ein zweiter Start ist `belegt`. Der Rückweg ist bis Stufe I jederzeit möglich, danach über `schluessel`.

## 6. Offene Fragen an Niko

Nur Entscheidungen, die sich nicht aus Code und Vorgaben ableiten lassen. Jede hat eine Empfehlung.

1. **Frischer Code beim Einreichen?** F6 entfällt für den Auftragsstart. Der Beleg braucht aber etwas, das der Kern nicht allein erzeugen kann, und der Kern sieht das Sitzungstoken im Klartext (voice-core/auth_gate.py:5). Ohne frischen Code könnte ein gekaperter Kern den Beleg selbst abholen, F2 schützte dann nur vor Fehlbedienung. **Empfehlung:** Ja, ein frischer Code nur beim Klick auf „Einreichen“ (ein Code je veröffentlichtem Auftrag), nie beim Start.
2. **Pfadklassen.** Gesperrt: `.github/`, `.gitmodules`, `.git` in jeder Schreibweise, Windows-Gerätenamen, Links nach außen. Sensibel mit zusätzlicher Bestätigung: Paket- und Build-Dateien, Skripte, `.env*`, neue ausführbare Dateien und Binärdateien (Liste in 3.1.3). **Empfehlung:** wie im Entwurf. Änderungen an Workflows macht Niko von Hand.
3. **Aufbewahrung.** Aufträge 14 Tage, nicht eingereichte Änderungen 7 Tage, Projektordner 7 Tage, Marker 90 Tage. Das Löschen einer nicht eingereichten Änderung ist endgültig. **Empfehlung:** wie im Entwurf, die erste Woche im Trockenlauf. Soll länger aufbewahrt werden, wird nur die Quote angehoben.
4. **Stufe 4 erst mit F1?** Stufe 4 (S10) war für den alten Ablauf geplant (Schreib-Token in der Sandbox, `git push` nach dem Klick). Mit Folgepaket 1 ändert sich der Ablauf, die Abnahme müsste wiederholt werden. **Empfehlung:** S5 bis S9 jetzt (sie hängen nicht an Folgepaket 1), Stufe 4 erst als Stufe E dieses Papiers, damit der Kern-Kanal nie mit einem Schreib-Token in der Sandbox läuft. Schreibende Aufträge kommen dadurch etwa sechs Arbeitstage später.
5. **Einreichen auch im PC-Kanal?** Der PC-Kanal ist Nikos eigener Laptop mit eigenem Vertrauensmodell und schreibt heute über die Sandbox. **Empfehlung:** Nein, vorerst nicht. Eine Entscheidung nach Stufe 5, mit den Erfahrungen aus dem Kern-Kanal.
6. **Runden nach dem Auflegen.** Mit F3 überlebt der laufende Lauf, nicht die Abnahme-Schleife des Kerns (weitere Runden steuert die Sitzung). Alternative: die Abnahme in den Auftragsdienst verlegen, ein eigenes großes Paket, und `pruefen` ist im Kern-Kanal gesperrt (F8). **Empfehlung:** so lassen. Nach der Rückkehr zeigt die Karte das Ergebnis, und Niko startet bei Bedarf die nächste Runde.

## 7. Risiken und Restrisiken

| Nr. | Risiko | Gegenmaßnahme | Restrisiko |
| --- | --- | --- | --- |
| 1 | Ein gekaperter Kern zeigt eine gefälschte Änderungsübersicht, Niko gibt etwas anderes frei, als er sieht | Der Beleg bindet Commit und Tree, nicht die Anzeige. Pull Request als Entwurf, nie gemergt durch WAMO | Die wirkliche Prüfung ist Nikos Blick in den Pull Request. Liest er ihn nicht, ist das Tor offen |
| 2 | Die CI des Repos führt Code des Zweigs mit Repo-Secrets aus (Pull Requests im selben Repo) | `.github/` gesperrt, sensible Dateien markiert, Entwurf, Testrepo ohne Secrets in Stufe E | Code in Tests und Build-Skripten läuft in der CI. Solange Repos Secrets haben, die für Pull-Request-Läufe sichtbar sind, bleibt das offen. Branch-Schutz und Environments mit Freigabe sind Zusatz, nicht Ersatz (Niko, manuell) |
| 3 | Das Fenster von 60 Sekunden nach Nikos echtem Code | ein Code ein Beleg, Bindung an Auftrag und Tree, 120 Sekunden Lebensdauer | Ein Kern mit dem Token kann im Fenster Belege für andere Aufträge versuchen, die „ein Code“-Regel macht das auffällig |
| 4 | Der Auftragsdienst ist ein neuer Dauerprozess, der den Claude-Zugang durchreicht und Sandboxen startet | läuft als `wamo` ohne Capabilities, Regeln im Kindprozess, Härtung nach Spike, Peer-Prüfung, Selbstprüfung des Codes | Härtung kann schwächer ausfallen als beim Freigabe-Dienst (kein `RestrictNamespaces`). Ein Fehler trifft alle Kern-Aufträge, nicht den PC-Weg |
| 5 | Ein Neustart des Auftragsdienstes beendet laufende Aufträge | Ruhe-Regel, Dienst-Updates nur bei Ruhe | Ein Absturz mitten im Lauf beendet den Auftrag wie bisher das Auflegen |
| 6 | Der Geheimnis-Scan ist unvollständig, die Pfadregeln erzeugen Fehlalarme | exakte Werte und Muster, im Zweifel gesperrt | Kodierte oder zerlegte Werte fallen durch, Fehlalarme kosten Bedienkomfort |
| 7 | Ein Commit je Auftrag: der Verlauf des Agenten geht verloren | bewusste Entscheidung, weil Objekte aus unvertrautem Git nicht gelesen werden | Der Pull Request ist weniger fein als bei einer Commit-Folge |
| 8 | Dispatcher und Sandbox laufen als derselbe Benutzer `wamo`, der GitHub-App-Schlüssel liegt für den Dispatcher lesbar in `/etc/wamo` (wamo_job_dispatch.py:691-737) | bestehende Härtung der Sandbox (bwrap, Wächter, Ausgangsfilter) | Ein Ausbruch aus der Sandbox auf Benutzerebene hebt F1 und F2 auf. Eine Trennung (Token-Ausgabe in einen eigenen Dienst mit eigenem Benutzer, Muster Freigabe-Dienst) ist nicht Teil dieses Papiers und eine Empfehlung für später |
| 9 | Das Journal des Auftragsdienstes enthält Gesprächs- und Codeinhalte auf der Platte | 0600 `wamo`, schon geschwärzt, Frist 14 Tage, Größenlimit | Inhalte liegen bis zur Frist auf dem Server, lesbar für `wamo` und root |
| 10 | F7 hat eine kurze Lebensdauer (3.5) | bewusst klein gehalten (4 Stunden) | schützt nur bis zum Abbau des ssh-Schlüssels |
| 11 | Die Datei des Dispatchers hat über 3200 Zeilen und teilt sich zwei Kanäle | neue Logik in neuen Modulen, Einhängepunkte an `KANAL == "kern"`, Test je Einhängepunkt, dass der PC-Kanal unverändert bleibt | jedes neue Modul braucht Änderungen am Auslieferungsweg, ein Fehler dort träfe auch den PC-Weg (Bestätigung durch Rauchtest und Totmannschalter) |
| 12 | Annahmen zu GitHub (Workflows, Entwurf, Fetch einer Commit-ID) und zu `git` stimmen nicht | Prüfstellen FP1-0 und FP1-9, Rückfall auf normalen Pull Request im Code, notfalls der Weg über die REST-Schnittstelle statt `git push` (Blobs, Tree, Commit und Ref per API, ohne Git-Prozess) | Bei einem Wechsel auf die REST-Schnittstelle ändert sich 3.1.4 Schritt 10 und die Tests |
| 13 | Ein frischer Code je Einreichen kostet Bedienkomfort | nur beim Einreichen, nicht beim Start (Frage 1) | Bei vielen kleinen Aufträgen wird es lästig. Dann ist die Frage 1 neu zu stellen, nicht still zu lockern |
| 14 | Der Zeitplan hängt an Fremdteilen (Verhalten von GitHub, Härtung des Dienstes, Wartezeiten des Rollouts) | Spike in FP1-10, Proben in FP1-9, Puffer von 30 Prozent | Die Schätzung kann um mehr als ein Drittel abweichen |

