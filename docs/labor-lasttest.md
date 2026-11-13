# Lasttest für das Testlabor (Stand 13.11.2026, nicht am Server gelaufen)

Das Werkzeug `scripts/wamo_labor_last.py` misst, wie sich WAMO mit 1, 2, 4 und 8 gleichzeitigen Sitzungen verhält. Es setzt den Absatz "Last" in `docs/testlabor-wamo.md`, Abschnitt 3.7, um. Dieses Dokument beschreibt Ablauf, Voraussetzungen, Kosten, Abbruch, Auswertung und die Grenzen der Aussage. **Das Werkzeug wurde nur gegen Attrappen getestet.** Alles, was nur ein echter Lauf zeigt, steht im letzten Abschnitt.

## 1. Was der Lauf tut

1. **Vorprüfung** (nur lesend, vor jeder Änderung): feste Adresse, Sprechproben, Seeds in der Umschlag-Datei (nur ob da), Laborprofile im Kern (alle gesperrt, alle vorhanden), Plätze 1 bis N im Dienst (leer), Kern `wamo-core@b` (aktiv, Speicher unter 3 GiB, Journal, Speicher und CPU-Zeit lesbar). Scheitert eine Prüfung, wurde nichts verändert.
2. **Vorbereitung** (einmal für die größte Stufe): Profile freischalten (`wamo_labor.py aktivieren`), je Platz einen Durchlauf öffnen (Betreuer-Schnittstelle, Einwilligung Version 1), je Sitzung eine eigene Chrome-Instanz mit virtuellem Mikrofon starten und mit E-Mail, Passwort und Code aus der Umschlag-Datei anmelden.
3. **Stufen** (Standard 1, 2, 4, 8 Sitzungen, je 15 Minuten, auf Wunsch weitere Stufen mit 8 Sitzungen und Murmeln): je Sitzung die Kette A1, J1, G3 im Wechsel, nach jedem Schritt eine zufällige Denkpause von 8 bis 20 Sekunden. Die Folge ist mit festem Seed wiederholbar, die Sitzungen beginnen versetzt (A1, J1, G3). Am Kern wird alle 15 Sekunden gelesen.
4. **Aufräumen** (immer, auch bei Fehlern, Grenzen und Strg+C): Gespräche beenden, Browser schließen, Profile sperren, Durchläufe beenden, Plätze leeren, Gesprächstexte der Profile im Kern ab Beginn des Laufs löschen, Nachweis (Profile gesperrt, Plätze leer), Temp-Dateien löschen. Jeder Schritt läuft für sich, ein gescheiterter hält die anderen nicht auf und steht im Bericht.
5. **Auswertung**: eine JSON-Datei und ein Bericht (Markdown) mit Bestehen, Durchfallen oder "offen" je Kriterium.

## 2. Voraussetzungen auf dem Mess-PC

| Voraussetzung | Prüfung |
| --- | --- |
| Büronetz, Uhr per NTP | `python scripts/wamo_labor_platz.py pruefen` meldet die Abweichung zum Server (höchstens 5 Minuten, für die Codes besser unter 2 Sekunden) |
| Chrome (installiert) und Node mit Playwright | `PLAYWRIGHT_MODUL` zeigt auf den Ordner von `playwright-core` (wie beim Messskript `scripts/wamo-stimme-messen.cjs`) |
| WAMO-CA im Benutzerspeicher | Chrome prüft das Zertifikat, es gibt kein `ignoreHTTPSErrors` (Anleitung `docs/labor-platz-einrichten.md`) |
| ssh-Alias `wamo-sokrates2`, Schlüssel ohne Passwortfrage | `ssh wamo-sokrates2 true` |
| Am Server `sudo` ohne Passwort für `python3` und `journalctl` | gleiche Voraussetzung wie `scripts/wamo_labor_nachlauf.py`, dazu muss `journalctl -u wamo-core@b` lesbar sein (sonst bricht die Vorprüfung ab) |
| `WAMO_ADMIN_DATABASE_URL` gesetzt | für `wamo_labor.py aktivieren`, `sperren` und `loeschen` (Rolle `wamo_admin`) |
| Laborprofile `labor-01` bis `labor-08` angelegt, **zweiter Faktor eingerichtet** | `python scripts/wamo_labor_faktoren.py pruefen` (Seed je Profil ja oder nein) |
| Umschlag-Datei mit den Zugängen und den TOTP-Seeds | `%USERPROFILE%\.wamo-labor\umschlag-labor.txt`, nie im Repo, nur Niko darf sie lesen (Konzept 3.7: nie im Chat). Das Werkzeug öffnet sie nur in der Vorprüfung und im Trockenlauf (dort nur "Seed vorhanden") |
| Sprechproben | `python scripts/wamo_labor_last.py clips` erzeugt sie mit der Windows-Stimme Hedda in `%USERPROFILE%\.wamo-labor\last\clips` (A1, J1, Einwurf, acht Faktenfragen für G3) |
| Labor-Dienst läuft, Plätze 1 bis 8 leer (kein offener Durchlauf, keine Tickets) | Die Vorprüfung meldet den ersten belegten Platz und startet nicht, damit nie Daten eines Besuchers gelöscht werden |
| Alle Laborprofile gesperrt, kein anderer Betrieb auf den Plätzen | Die Vorprüfung meldet ein freigeschaltetes Profil und startet nicht |

Die Seeds sind nach dem Labortag zu erneuern: Am Mi 25.11. werden die Faktoren der Betreuer-Geräte neu eingerichtet, damit die Seeds des Mess-PCs ungültig sind (Konzept 3.9).

## 3. Aufruf

Alle Befehle im Repo-Hauptordner mit der Python-Umgebung aus `voice-core/.venv`.

```
python scripts/wamo_labor_last.py clips
python scripts/wamo_labor_last.py lauf                                   # Trockenlauf, Standard
python scripts/wamo_labor_last.py lauf --ausfuehren --bestaetigung LASTTEST
```

Der **Trockenlauf** ist der Standard. Er zeigt Plan, Zahl der Profile und Plätze, Stufen, geschätzte Dauer, geschätzte Tokens und Anfragen, Kosten (nur mit Preisen), Abbruchgrenzen und die lokalen Befunde (Sprechproben da, Seeds da, Node und ssh gefunden). Er ruft weder den Server noch den Browser auf. Der **Echtlauf** startet nur mit `--ausfuehren` **und** `--bestaetigung LASTTEST`, alles andere endet mit Ausgang 2, bevor etwas angefasst wird. Abgekürzte Schalter gelten nie.

| Option | Bedeutung |
| --- | --- |
| `--sitzungen 1,2,4,8` | Stufen mit dieser Sitzungszahl (nur 1, 2, 4, 8) |
| `--minuten 15` | Dauer je Stufe, höchstens 20 |
| `--murmeln 20,10,5` | zusätzliche Stufen mit 8 Sitzungen und Murmeln bei diesem Signal-Rausch-Abstand in dB |
| `--murmeln-datei WAV` | eigene Aufnahme (16 Bit, mono) statt des erzeugten Murmelns |
| `--seed 1` | Seed der Denkpausen |
| `--freigabe ablehnen\|freigeben\|keine` | was mit der Freigabekarte bei J1 geschieht (Standard ablehnen, schreibt nichts in den Dienst) |
| `--ratenlimit-anfragen N`, `--ratenlimit-tokens N` | Limit des Schlüssels aus der Konsole, sonst bleiben diese Kriterien offen |
| `--preis-eingabe USD`, `--preis-ausgabe USD` | Dollar je Million Tokens, nur damit gibt es eine Schätzung in Dollar |
| `--kosten-obergrenze USD` | lehnt einen teureren Plan schon im Trockenlauf ab (braucht beide Preise) |
| `--kerndaten-behalten` | die Gesprächstexte im Kern am Ende nicht löschen |
| `--schema`, `--host`, `--slot` | Standard `wamo_dev`, `wamo-sokrates2`, `b` |
| `--ausgabe-ordner`, `--clips-ordner`, `--umschlag-datei` | Standard unter `%USERPROFILE%\.wamo-labor`, nie im Repo (das Werkzeug lehnt Orte im Repo ab) |

Ausgänge: 0 bestanden (oder Trockenlauf), 1 durchgefallen, vorzeitig beendet oder Aufräumen gescheitert, 2 Eingabe falsch (nichts angefasst), 3 vollständig gelaufen, Urteil offen, 130 Strg+C.

## 4. Empfohlene Reihenfolge am Di 24.11.

1. Vorbereitung am Vortag: Sprechproben erzeugen, Trockenlauf, Ratenlimit und Preise aus der Konsole in den Aufruf eintragen.
2. Kurzer Probelauf: `--sitzungen 1 --minuten 3` (Urteil bleibt "offen", der Umfang fehlt). Er prüft Anmeldung, Browser, Messung und Aufräumen, bevor die Stunden beginnen.
3. Voller Lauf ohne Murmeln (Stufen 1, 2, 4, 8, etwa 1 Stunde 20 Minuten mit Vorbereitung und Aufräumen).
4. Murmeln: entweder gleich im vollen Aufruf (`--murmeln 20,10,5`, etwa 2 Stunden 10 Minuten) oder danach allein (`--sitzungen 8 --murmeln 20,10,5`). Ein Lauf nur mit Murmelstufen bleibt "offen", weil ihm der Umfang fehlt, das ist gewollt: Die Murmelstufen ergänzen den vollen Lauf.
5. Nikos Büro-Betrieb als parallele Last: Das Werkzeug erzeugt sie nicht. Nur wenn Niko während des Laufs arbeitet, sagt die Spitze der Anfragen je Minute etwas über das gemeinsame Ratenlimit (Konzept 3.7, Entscheidung Q4).

## 5. Kosten

Vorab geschätzt aus Tokens je Zug. Die Annahmen stehen im Trockenlauf und im Bericht und sind **nicht gemessen**:

| Annahme | Wert | Herkunft |
| --- | --- | --- |
| Eingabe-Tokens je Zug | 17000 | Büro-Prompt laut `docs/gespraech-optimierung.md`, der Labor-Prompt ist kürzer (Obergrenze) |
| Ausgabe-Tokens je Zug | 300 | Annahme, in den Unterlagen nicht belegt |
| Anfragen je Zug | 2,5 | 2 bis 3 laut Konzept 3.7 |
| Zeit je Zug | Sprechprobe 5 s, Antwort 20 s, Denkpause im Mittel 14 s | Annahme |

Daraus folgen etwa 23 Züge je Sitzung und Stufe von 15 Minuten, bei den vier Standardstufen 345 Züge, mit drei Murmelstufen 897 Züge (rund 15 Millionen Eingabe-Tokens). **Preise trägt das Werkzeug nicht ein**: Ohne `--preis-eingabe` und `--preis-ausgabe` zeigt es keine Dollar. Der Cache verbilligt gelesene Eingabe-Tokens, das schätzt das Werkzeug nicht, die Schätzung ist deshalb eine Obergrenze ohne Cache. Das Ausgabenlimit in der Konsole bleibt der eigentliche Schutz (Handgriff Niko, Konzept 3.9).

## 6. Abbruch

Der Lauf endet sofort und räumt auf bei:

| Ereignis | Grenze |
| --- | --- |
| 429 | schon das erste, im Journal des Kerns oder in einer Antwort im Browser |
| Abbrüche von Sitzungen | mehr als 2 (Gespräch endet unerwartet, Gespräch startet nicht, Browser antwortet nicht) |
| Speicher des Kerns | über 3 GiB |
| Messung am Server | 3 Fehler oder unvollständige Messungen in Folge (ohne Journal, Speicher oder CPU-Zeit wirken die Grenzen nicht) |
| Kern nicht mehr aktiv | sofort |
| Strg+C oder Signal | sofort, das Aufräumen zählt kein weiteres Signal |

Höchstgrenzen im Code (`scripts/wamo_labor_last_plan.py`): höchstens 8 Sitzungen, höchstens 20 Minuten je Stufe, höchstens 10 Stufen. Der Browser darf nur die feste Adresse der Oberfläche auf Sokrates-2 öffnen (`https://192.168.178.136:55445`, Prüfung wie in `scripts/wamo_labor_platz_adressen.py`), Anfragen der Seite gehen nur an die vier Herkünfte 55443, 55444, 55445 und 55447.

Am Server schreibt das Werkzeug nur über vorhandene Wege: `wamo_labor.py` (aktivieren, sperren, loeschen) und die Betreuer-Schnittstelle des Dienstes (neuer Durchlauf, Durchlauf beenden, Daten löschen, nur Plätze 1 bis 8, wie `scripts/wamo_labor_nachlauf.py`). Am Kern wird ausschließlich gelesen.

## 7. Auswertung und Bestehen

Ausgabe unter `%USERPROFILE%\.wamo-labor\last\<lauf>.json` und `<lauf>.md`. Die JSON-Datei enthält je Stufe die Kennzahlen, alle Schritte (Karte, erster Ton, Verstummen, Abbruch) und die Messungen am Kern. Sie enthält keine Passwörter, Seeds, Codes, Gesprächstexte oder Journalzeilen.

| Kriterium (Konzept 3.7) | Grenze | Bemerkung |
| --- | --- | --- |
| p95 des ersten Tons bei 8 Sitzungen | höchstens 3500 ms | Stufe mit 8 Sitzungen ohne Murmeln. Ein Schritt ohne Antwort zählt mit der Wartefrist von 45 s, stumme Sitzungen schönen das p95 nie |
| p95 bei 8 Sitzungen gegen 1 Sitzung | höchstens das 1,5-fache | braucht beide Stufen |
| Abbrüche | 0 | alle Stufen |
| CPU des Kerns | unter 70 Prozent | **Annahme:** Prozent eines Kerns (100 ist ein Kern), p95 der 15-Sekunden-Stichproben je Stufe. Der Bezug steht nicht in den Unterlagen |
| Speicher des Kerns | unter 2,8 GiB | größter Stichprobenwert, dazu die Spitze des Speichers (`memory.peak`), wenn der Lauf sie erhöht hat |
| 429 | kein einziges | Journal und Browser. Ein unlesbares oder abgeschnittenes Journal gilt nie als "kein 429", sondern als offen |
| Spitze der Anfragen und Tokens je Minute | unter 70 Prozent des Ratenlimits | gleitende 60 Sekunden aus Eimern von 10 Sekunden. Ohne Angabe des Limits offen. Tokens nur, wenn der Kern Latenzzeilen (`LATENZ_TOKEN`, Schalter `WAMO_LATENZ_LOG`) schreibt |
| Umfang des Laufs | Stufen 1, 2, 4 und 8, je mindestens 10 Minuten | Ein Teillauf liefert Hinweise, belegt aber nie das Bestehen |

**Urteile:** Eine fehlende Messung ist nie ein Bestehen, sie heißt "offen". Durchgefallen ist der Lauf, sobald ein Kriterium durchfällt. Bestanden ist er nur, wenn alle Kriterien bestehen und der Lauf bis zum Ende kam. Zeigt das Journal in einer Stufe keine einzige Anfrage an das Modell, bleiben CPU, Speicher und 429 offen, weil dann nicht belegt ist, dass die Last den gemessenen Kern erreichte (falscher Slot oder falsches Schema).

Weitere Zahlen im Bericht ohne Grenze im Konzept: Verstummen (Einwurf bis Ende der Wiedergabe, p50 und p95), falsche Unterbrechungen, Soniox-Fehler, Unterbrechungen im Journal gegen die Einwürfe der Probe, Aufgaben des Kerns, Zeitüberschreitungen, Konsolenfehler im Browser.

## 8. Aufräumen von Hand

Scheitert ein Aufräumschritt (der Bericht nennt ihn mit `FEHLGESCHLAGEN`), von Hand nacharbeiten und prüfen:

```
python scripts/wamo_labor.py sperren --kennung labor-01 labor-02 labor-03 labor-04 labor-05 labor-06 labor-07 labor-08 --ausfuehren
python scripts/wamo_labor_nachlauf.py nachlauf            # Trockenlauf mit Zählung davor, nur lesen
python scripts/wamo_labor_nachlauf.py nachlauf --ausfuehren --vor-dem-labortag
```

Der letzte Befehl leert alle 16 Plätze und alle Gesprächstexte der Laborprofile, also auch Daten, die nicht aus dem Lasttest stammen. Vorher die Zählung ansehen. Hängt der Browser, beendet `taskkill /IM chrome.exe` nur auf dem Mess-PC die Fenster des Lasttests, wenn kein anderes Chrome läuft.

## 9. Was die Last nicht belegt

- **Echte Menschen:** Die Sprechproben sind eine Windows-Stimme, das Murmeln ist erzeugt (sechs überlagerte, in der Tonhöhe verschobene Sprechproben) oder eine Aufnahme. Das Murmeln ersetzt keine echte Mehrstimmenaufnahme. Gerät, WLAN (8 Geräte im Raum) und echte Mikrofone fehlen.
- **Eine Maschine:** Acht Chrome-Instanzen auf einem PC teilen sich Netz, CPU und Speicher. Ob der Mess-PC selbst zum Engpass wird, ist offen, die Zeiten des ersten Tons enthalten diesen Anteil.
- **Dienst der Kollegen:** Gemessen wird, was der Kern und der Browser sehen. Mit der Attrappe statt des Dienstes (Konzept 3.7) ist die Dienstlast nicht belegt. Bei `--freigabe ablehnen` entstehen keine Tickets.
- **Nicht gemessen:** Verzug der Ereignisschleife und Datenbankverbindungen des Kerns (kein lesender Weg, den das Werkzeug nutzt), Cache-Treffer und Websuchen, Tokens je Zug außerhalb der Latenzzeilen.
- **Kein Ratenlimit ohne Parallellast:** Das gemeinsame Limit des einen Schlüssels (Entscheidung Q4) zeigt sich erst, wenn Nikos Büro-Betrieb gleichzeitig läuft.
- **Eine Stichprobe:** Ein Lauf ist keine Statistik. Bei wenigen Schritten je Stufe ist das p95 fast der größte Wert.
- **Deutung von "Verstummen":** Das Konzept nennt Verstummen im Lastsatz ohne Definition. Hier ist es die Zeit vom Einwurf (Sprechprobe "Moment, stopp ...") bis zum Ende der Wiedergabe, gemessen in jeder dritten A1-Runde (Abweichung von der reinen Kette A1, J1, G3, abschaltbar nur durch Codeänderung `EINWURF_JEDE`). Spricht WAMO nach dem Einwurf länger als 4 Sekunden weiter, gilt das als verpasst.

## 10. Offene Angaben

| Angabe | Stand |
| --- | --- |
| Bezug der CPU-Grenze (ein Kern oder die ganze Maschine) | in den Unterlagen nicht genannt, hier ein Kern (der Kern ist ein Python-Prozess) |
| Ratenlimit des Schlüssels, Preise | Konsole, Handgriff Niko |
| Muster für 429 und Soniox-Fehler im Journal | Annahme (zum Beispiel `Error code: 429`, `rate_limit_error`, `Too Many Requests`, Zeilen mit `soniox` und einem Fehlerwort), am echten Journal bestätigen |
| Schema des Labortags | `wamo_dev` ist der Standard aller Laborskripte, `--schema` nennt ein anderes |
| Bezeichnungen der Oberfläche (Anmelden, Gespräch starten, Beenden, Freigeben) und das Attribut `data-cut` für abgeschnittene Antworten | aus dem Quelltext gelesen, nur am echten Lauf prüfbar |
| Verhalten von Chrome mit acht Instanzen auf dem Mess-PC | nicht erprobt (Speicherbedarf), `--kopflos` ist nicht erprobt |
| Lesbarkeit von `journalctl` und `memory.peak` für den ssh-Benutzer | die Vorprüfung meldet es |
| Das Löschen der Gesprächstexte im Kern wartet die Sperrfrist von 3 Minuten ab | verlängert das Aufräumen um etwa 4 Minuten |
