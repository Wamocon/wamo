# Testlabor 28.11.2026: Checkliste für Freitag 27.11. und Samstag 28.11.

**Stand:** 13.11.2026, Entwurf. Gehört zum Konzept `docs/testlabor-wamo.md` (3.9, 5.3) und zum Runbook `docs/labor-runbook.md`. Die Checkliste ist zum Ausdrucken und Abhaken gedacht. In jeder Zeile steht, was zu tun ist und was dabei herauskommen muss ("Erwartet"). Weicht ein Ergebnis ab, wird nicht abgehakt, sondern in die Tabelle "Abweichungen" am Ende eingetragen. Alles, was in den Unterlagen noch fehlt, steht als `[offen: ...]` im Text.

**Wer.** Server und Skripte prüft T (Technik-Bereitschaft WAMO). Die Konsole des Sprachmodells prüft Niko. Plätze prüfen B, N und T gemeinsam. Rollen: siehe Runbook, Abschnitt 1.1.

**Befehle.** Alle Befehle laufen im Repo-Hauptordner auf dem PC der Technik. Ein Server-Befehl `ssh wamo-sokrates2 "..."` braucht den ssh-Alias und am Server `sudo` ohne Passwort. Die Skripte `wamo_labor.py` und `wamo_labor_faktoren.py` brauchen `WAMO_ADMIN_DATABASE_URL` in der Umgebung, die Faktoren zusätzlich die Umschlag-Datei unter `%USERPROFILE%\.wamo-labor\umschlag-labor.txt`.

## Teil A: Freitag 27.11.2026 (Daten-Freeze, Rückwegübung, Einweisung)

**Daten-Freeze ist um 12:00.** Ab dann ändert niemand mehr Daten oder Einstellungen. Einzige Ausnahme ist ein Sicherheitsfehler (Konzept 5.3). Die Rückwegübung am Nachmittag braucht Ruhe: Es läuft keine Sitzung.

### A1 Freeze und Stand (T, bis 12:00)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| A1.1 | [ ] | Liste der Datenänderungen seit dem 20.11. (Datum und Prüfsumme je Änderung) durchgehen | Die Liste ist vollständig. Zu jeder Änderung gab es 3 Läufe der betroffenen Karten |
| A1.2 | [ ] | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-daten-einrichten.sh status"` | Alle acht Dateien entsprechen der Lieferung oder der Änderungsliste. Der Lader nimmt sie an. Die gewählte Fassung der Regeln ist die entschiedene (`standard` oder `streng`). `WAMO_LABOR_DATEN` ist beim Kern gesetzt |
| A1.3 | [ ] | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh status"` | Der aktive Slot läuft mit dem Release des Freeze-Etiketts `labor-freeze`. Stand 13.11.2026 am Server: Slot b `v0.1.0-c751d04` aktiv, Slot a inaktiv (kommt ein späterer Release, den Namen hier nachtragen). Kein weiterer Release ist unterwegs |
| A1.4 | [ ] | Befunde der Generalprobe vom 26.11. durchgehen | Jeder Befund ist behoben oder bewusst als Abweichung eingetragen |
| A1.5 | [ ] | `python scripts/wamo_labor.py status` | Neun Laborprofile (`labor-01` bis `labor-09`), alle "gesperrt". Kein Profil ist über Nacht freigeschaltet |
| A1.6 | [ ] | Daten der Generalprobe löschen. `python scripts/wamo_labor.py status` zeigt je Profil die Zeilen. Stehen dort noch welche: `python scripts/wamo_labor_nachlauf.py nachlauf --ausfuehren --vor-dem-labortag` (sperrt alle Profile, leert die 16 Plätze im Dienst und den Gesprächstext im Kern, mit Zählung davor und danach) | `status` zeigt für jedes Profil "gesperrt" und "0 Zeilen". Der Nachlauf meldet "Nachweis erbracht" |

### A2 Konsole des Sprachmodells (Niko, Handarbeit)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| A2.1 | [ ] | Ausgabenlimit der Konsole für den Schlüssel `ANTHROPIC_API_KEY` | Das Limit ist gesetzt: `[offen: Betrag]` |
| A2.2 | [ ] | Guthaben und Monatsgrenze | Sie decken die geschätzte Nutzung des Labortags, Schätzung aus dem Lasttest `[offen: erwartete Ausgaben]` |
| A2.3 | [ ] | Tarif und Ratenlimit des Schlüssels notieren | Die Werte stehen hier: `[offen: Tarif, Anfragen und Tokens je Minute]` |
| A2.4 | [ ] | Ersatzschlüssel für den Schlüsseltausch | Ein Schlüssel eines Kollegen liegt bereit, falls das Limit fällt `[offen: ob gewünscht]` |
| A2.5 | [ ] | Soniox: Konto aktiv, Parallelität mindestens 9 Sitzungen | Bestätigt `[offen: Wert aus der Messrunde, Konzept 2.3 Punkt 4]` |

### A3 Rückwegübung (T, anderthalb Stunden, nur mit Ruhe am Server)

Ziel: Die Zeit bis zum Rückweg messen und die Befehle festhalten, die im Runbook noch als `[offen: ...]` stehen. Die Zeiten werden in die Spalte "Gemessen" eingetragen und danach ins Runbook (Abschnitt 5.3 und 11) übernommen.

| Nr | Erledigt | Übung | Erwartet | Gemessen |
| --- | --- | --- | --- | --- |
| A3.1 | [ ] | Kern zurück mit laufendem vorigen Slot: `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh rueckweg-kern"`, danach wieder auf den Lab-Release umschalten | Der Rückweg dauert unter 5 Minuten. Danach zeigt `status` den richtigen aktiven Slot | `___ min` |
| A3.2 | [ ] | Rückweg ohne laufenden vorigen Slot (nach `ausleiten`): das vorige Release erneut bereitstellen (`release` und `umschalten-kern`) | Die Dauer ist gemessen und im Runbook eingetragen | `___ min` |
| A3.3 | [ ] | Schlüsseltausch: `ANTHROPIC_API_KEY` in `/etc/wamo/core.env` mit `wamo-core-env` ändern, `umschalten-kern <release>`, danach eine Probe-Sitzung | Eine neue Sitzung läuft mit dem neuen Schlüssel. Die genaue Befehlszeile ist festgehalten | `___ min` |
| A3.4 | [ ] | Dienst abschalten: `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-rueckweg.sh"` (Stufe 3 des Notaus) | Das Skript meldet "Fertig" und nennt die Ausfalldauer. Die Ports 55443 bis 55446 antworten | `___ s` |
| A3.5 | [ ] | Dienst wieder herstellen: `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh einrichten"`, danach `pruefen` | Der Dienst läuft, der Rauchtest ist grün. Das Verhalten ohne Besucher ist beschrieben `[offen: ob einrichten ohne Neuanlegen von Caddy läuft]` | `___ min` |
| A3.6 | [ ] | Neustart des Containers `wamo-labor-dienst` (Befehl festlegen und notieren) | Der Befehl ist im Runbook, Abschnitt 6.2, eingetragen | `___ s` |
| A3.7 | [ ] | Profile sperren und Wirkung: ein Probegespräch an Platz 1 laufen lassen, dann `python scripts/wamo_labor.py sperren --alle --ausfuehren` | Das Gespräch endet binnen etwa 2 Minuten, ein neues startet nicht | `___ min` |
| A3.8 | [ ] | Notaus Stufe 1 durch einen Betreuer: bei allen offenen Plätzen **Durchlauf beenden** | Alle Karten zeigen "Durchlauf beendet", alle Gespräche enden binnen 5 Sekunden | `___ s` |
| A3.9 | [ ] | Alles wieder in den Ausgangszustand: alle Profile gesperrt, Plätze und Kern leer (zum Beispiel mit `python scripts/wamo_labor_nachlauf.py nachlauf --ausfuehren --vor-dem-labortag`) | `python scripts/wamo_labor.py status` zeigt alle gesperrt und 0 Zeilen | |

### A4 Einweisung der Betreuer (30 Minuten)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| A4.1 | [ ] | Jeder Betreuer hat das Runbook (gedruckt) | Mindestens zwei Betreuer sind eingewiesen. Namen: `[offen]` |
| A4.2 | [ ] | Jeder Betreuer übt an einem Übungsplatz: Neuer Durchlauf mit Haken, Zurücksetzen, Durchlauf beenden, Daten löschen | Jeder schafft den Ablauf ohne Hilfe. Übungsplatz: `[offen: Platz, der am Samstag unbenutzt bleibt]` |
| A4.3 | [ ] | Jeder Betreuer löst die Stufe 1 des Notaus aus (Runbook 5.2) | Er findet die Knöpfe und kennt die Reihenfolge |
| A4.4 | [ ] | Die Liste "Wer ruft wen" ist ausgefüllt und für jeden Betreuer gedruckt | Alle Namen und Telefonnummern stehen drin (Runbook, Abschnitt 8) |
| A4.5 | [ ] | Der Weg zum Betreuer-Token steht fest und jeder Betreuer kann sich anmelden | Anmeldung an `https://192.168.178.136:55447/betreuer` klappt. Der Token steht nirgends gedruckt `[offen: Weg der Übergabe]` |

### A5 Aushang und Material (B, N)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| A5.1 | [ ] | Datenschutzhinweis Version 1 von `https://192.168.178.136:55447/datenschutz` ausdrucken (Knopf Drucken), ein Aushang je Platz und einer am Betreuer-Platz | Der Ausdruck trägt "Version 1" und hat denselben Text wie die Seite |
| A5.2 | [ ] | Die Aufzeichnung (Rückfall) läuft am Fernseher | Bild und Ton gehen, die Länge ist bekannt `[offen: Dateiname, Ort, Gerät]` |
| A5.3 | [ ] | Kartensatz der 17 Karten ausgedruckt (`docs/labor-kartensatz.html`, im Browser drucken, A4, ohne Kopf- und Fußzeile des Browsers) | Je Platz ein Exemplar, Erwartungswerte zu W1 und W2 vom Trainer eingetragen `[offen: Ablageort am Platz]` |
| A5.4 | [ ] | Reserve-Laptop eingerichtet und geprüft | Das Platz-Skript `pruefen` zeigt keine Zeile mit `[FEHLER]` `[offen: Reserve-Laptop gehört zu Platz 9]` |
| A5.5 | [ ] | Zettel "Defekt" und "Notaus" liegen bereit | Je Betreuer-Platz ein Zettel mit der Stufe 1 des Notaus |

### A6 Faktoren (T)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| A6.1 | [ ] | `python scripts/wamo_labor_faktoren.py pruefen` | Jedes der neun Profile meldet "ja" (Seed in der Datei, kein Netz) |
| A6.2 | [ ] | Der Faktor der Betreuer-Geräte wurde am 25.11. neu eingerichtet (die Seeds des Mess-PCs sind ungültig) | Die Authenticator-Einträge der Betreuer-Geräte sind die neuen |

## Teil B: Samstag 28.11.2026, Morgen (vor der Öffnung)

Beginn: 90 Minuten vor der Öffnung `[offen: Uhrzeit der Öffnung]`. Am Labortag laufen keine anderen Sitzungen und keine Wartung auf demselben Kern.

### B1 Server (T)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| B1.1 | [ ] | Uhr: `ssh wamo-sokrates2 "timedatectl"` und die Uhr des PCs der Technik vergleichen | Die Uhr des Servers ist synchronisiert (NTP) und weicht höchstens 1 Minute ab |
| B1.2 | [ ] | Kern-Slots: `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh status"` | Der aktive Slot läuft ("active") mit dem Lab-Release, derselbe wie am Freitag (A1.3). Hinweis: Der vorige Slot läuft nur, wenn er nicht ausgeleitet wurde |
| B1.3 | [ ] | Kern-Kanal bleibt zu: `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh status"` | "Kanal: zu", "Schalter (Drop-in: aus)", keine Meldung über einen offenen Kanal |
| B1.4 | [ ] | Labor-Dienst, Zustände: `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh status"` | Der Container ist gesund. Die Ketten der Firewall und die Site von Caddy stehen |
| B1.5 | [ ] | Labor-Dienst, Rauchtest: `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh pruefen"` | Alle 20 Proben grün, Ausgang 0. Eine übersprungene Probe wird genannt und ist nur mit Grund in Ordnung |
| B1.6 | [ ] | Ports von Caddy: `for p in 55443 55444 55445 55446 55447; do echo $p $(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 https://192.168.178.136:$p/); done` | Jeder Port nennt einen HTTP-Status. Keiner nennt 000 |
| B1.7 | [ ] | Firewall: `ssh wamo-sokrates2 "sudo systemctl is-active wamo-firewall"` | `active`. Die Ketten für 55447 und gegen den Ausgang des Dienstes prüft der Rauchtest (B1.5) |
| B1.8 | [ ] | Dienst von einer fremden Adresse nicht erreichbar | Vom Rauchtest geprüft. Von einem Gäste-Laptop aus `/gesundheit` und `/api/kern/` öffnen: beide ohne Inhalt (403 oder 404) |
| B1.9 | [ ] | Kein anderer Betrieb auf dem Kern | Niemand sonst nutzt WAMO. Keine geplante Wartung am Server |

### B2 Profile (T)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| B2.1 | [ ] | `python scripts/wamo_labor.py status` | Neun Profile, alle "gesperrt", jedes "0 Zeilen" (Daten der Probe sind gelöscht) |
| B2.2 | [ ] | Freischalten, wie geplant: `python scripts/wamo_labor.py aktivieren --kennung labor-01 labor-02 labor-03 labor-04 labor-05 labor-06 labor-07 labor-08 --ausfuehren` | Acht Meldungen "freigeschaltet (labor_aktiv = true)". Das Reserveprofil `labor-09` bleibt gesperrt `[offen: ob die Reserve schon am Morgen freigeschaltet wird]` |
| B2.3 | [ ] | `python scripts/wamo_labor.py status` | Genau die geplanten Profile sind "freigeschaltet" (acht Plätze), die Reserve und alle anderen "gesperrt" |
| B2.4 | [ ] | Faktor prüfen: `python scripts/wamo_labor_faktoren.py codes --profil labor-03` und den Code mit der Authenticator-App des Betreuer-Geräts vergleichen | Beide Codes sind gleich. Weichen sie ab, geht eine Uhr falsch (NTP) |
| B2.5 | [ ] | Anmeldung an jedem Platz-Laptop mit Passwort und Code ("Angemeldet bleiben") | Das WAMO-Fenster zeigt "Gespräch starten". Passwörter bleiben bei T und L |

### B3 Plätze (B, N, T je Laptop, Plätze 1 bis 8 und Reserve)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| B3.1 | [ ] | Laptop an Strom und Kabelnetz, Headset mit USB oder Klinke eingesteckt | Das Kabel liegt, das Headset ist erkannt (kein Bluetooth-Freisprechgerät) |
| B3.2 | [ ] | Laptop: Verknüpfung **WAMO Labor pruefen** | Jede Zeile beginnt mit `[ok]`. Keine Zeile mit `[FEHLER]`. Eine `[WARNUNG]` zum Bluetooth-Mikrofon: Headset wechseln |
| B3.3 | [ ] | Zertifikat: Zeile "Zertifikat" von `pruefen` und `https://192.168.178.136:55445` im Browser | `[ok]`, keine Zertifikatswarnung |
| B3.4 | [ ] | Uhr: Zeile "Uhr" von `pruefen` | Abweichung zum Server höchstens 5 Minuten, besser unter 1 Minute |
| B3.5 | [ ] | Mikrofon: Zeile "Mikrofon" von `pruefen`, dann Pegel in den Windows-Einstellungen ansehen und sprechen | Ein aktives Eingabegerät, der Pegel bewegt sich |
| B3.6 | [ ] | Netz: Von einem Platz-Laptop aus sind nur 55443 bis 55445 und 55447 des Servers erreichbar | Stichprobe der Netz-Prüfliste vom 25.11. `[N]` |
| B3.7 | [ ] | Kurzprobe je Platz (2 Minuten): Betreuer öffnet den Platz (Runbook 3.1, der Betreuer bestätigt für sich), das Board erscheint rechts, "Gespräch starten", einen Satz sprechen, Antwort hören | Links WAMO und rechts das Board mit dem Startstand (sechs Tickets). WAMO antwortet mit Stimme `[offen: ob die Kurzprobe gewünscht ist, ihr Text wird gespeichert und beim Nachlauf gelöscht]` |
| B3.8 | [ ] | Nach der Kurzprobe an jedem Platz **Durchlauf beenden** und **Daten löschen** | Jede Karte zeigt "Geschlossen, kein Durchlauf", 0 Tickets. Beide Chrome-Fenster sind zu |
| B3.9 | [ ] | Betreuer-Seite: Übersicht der 16 Plätze | Alle 16 Karten sind da. Platz 15 und 16 zeigen "Reserviert für die Abnahme, kein Besucher". Kein Platz zeigt "Durchlauf offen" |
| B3.10 | [ ] | Reserve-Laptop: Schritte B3.2 bis B3.5 | Wie bei den Plätzen. Er bleibt aus, bis er gebraucht wird |

### B4 Kosten und Konsole (Niko, Handarbeit)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| B4.1 | [ ] | Konsole des Sprachmodells: Ausgabenlimit | Das Limit steht wie am Freitag (A2.1) |
| B4.2 | [ ] | Konsole: Ausgaben und Guthaben notieren | Stand am Morgen: `___ USD` (für die Differenz am Abend) |
| B4.3 | [ ] | Konsole: keine Störungsmeldung | Der Dienst des Sprachmodells meldet keine Störung |
| B4.4 | [ ] | Soniox: Konto und Guthaben | Aktiv, Guthaben reicht `[offen: Wert]` |
| B4.5 | [ ] | Der Ablauf bei Limit oder Ausfall ist bekannt | T und Niko kennen Runbook 6.1 und den Schlüsseltausch (A3.3) |

### B5 Datenschutz (L, D)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| B5.1 | [ ] | Aushang hängt an jedem Platz und am Betreuer-Platz | Der Aushang trägt "Version 1" und ist gut lesbar |
| B5.2 | [ ] | Seite `https://192.168.178.136:55447/datenschutz` öffnen (Betreuer-PC und ein Platz-Laptop) | Die Seite zeigt "Version 1" und denselben Text wie der Aushang, ohne Anmeldung |
| B5.3 | [ ] | Betreuer-Seite, Dialog **Neuer Durchlauf …** öffnen und wieder abbrechen | Die Zeile "Version" zeigt 1. Die Version von Aushang, Seite und Dialog ist dieselbe |
| B5.4 | [ ] | Die verantwortliche Person hat den Hinweis geprüft | Prüfung und Altersregel stehen fest `[offen: Name und Datum der Prüfung]` |
| B5.5 | [ ] | Ansprechperson für Löschwünsche ist benannt und erreichbar | Name und Telefon stehen im Runbook, Abschnitt 8 |
| B5.6 | [ ] | Alle Betreuer kennen den Weg bei Löschwunsch | Runbook 4.3: Durchlauf beenden, Daten löschen, T rufen, D informieren |

### B6 Betreuer-Adressen und Zugänge (T, B)

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| B6.1 | [ ] | Jeder Betreuer-PC steht in `/etc/wamo-labor/betreuer-adressen` | Von jedem Betreuer-PC zeigt `https://192.168.178.136:55447/betreuer` die Anmeldung, nicht "403" |
| B6.2 | [ ] | Von einem Betreuer-PC `https://192.168.178.136:55447/gesundheit` öffnen | Antwort "403". Nur der Server selbst sieht sie |
| B6.3 | [ ] | Jeder Platz-Laptop steht in `/etc/wamo-labor/plaetze-adressen` oder im erlaubten Büronetz | Von jedem Platz-Laptop zeigt `https://192.168.178.136:55447/board` die Board-Seite. Bis die Liste vorliegt, gilt das Büro-LAN (Frage Q13) |
| B6.4 | [ ] | Betreuer-Token: Anmeldung an der Betreuer-Seite klappt | Die Übersicht zeigt 16 Plätze. Der Token liegt nur bei Betreuern und Leitung |
| B6.5 | [ ] | Der Verlauf der Zwischenablage der Betreuer-Geräte ist ausgeschaltet oder wird geleert | Win+V zeigt keine Token (die Knöpfe "Token kopieren" und "Link kopieren" legen ihn dort ab) |

### B7 Abschluss und Go

| Nr | Erledigt | Prüfung | Erwartet |
| --- | --- | --- | --- |
| B7.1 | [ ] | Die Zeiten der Rückwegübung vom 27.11. stehen im Runbook, Abschnitt 5.3 | Stufe 3 und 4 haben eine gemessene Zeit |
| B7.2 | [ ] | Die Aufzeichnung ist startbereit am Fernseher | Der Rückfall dauert unter 1 Minute bis zum Start |
| B7.3 | [ ] | Go oder No-Go durch L und T | Alle Zeilen abgehakt oder bewusst als Abweichung eingetragen. Bei einem No-Go entscheidet P, ob der Rückfall (Runbook, Abschnitt 7) gilt |

## Abweichungen

| Nr der Zeile | Was weicht ab | Entscheidung | Wer | Uhrzeit |
| --- | --- | --- | --- | --- |
| | | | | |
| | | | | |
| | | | | |

Geprüft von (Rolle): `[offen]` Datum und Uhrzeit: `[offen]`

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
