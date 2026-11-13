# Platz-Skript: Laptops des Testlabors einrichten

**Stand:** 13.11.2026. Gehört zum Paket P8 (`docs/testlabor-wamo.md`, Abschnitt 3.9). Das Skript richtet einen Windows-Laptop als Platz des WAMO-Testlabors ein, startet die beiden Fenster (WAMO links, Board rechts), prüft den Platz und nimmt alles wieder zurück. Es läuft im Kiosk-Konto **ohne Administratorrechte**, braucht nur Python (Standardbibliothek) und nutzt weder PowerShell noch Zusatzpakete.

**Wichtig:** Die Chrome-Richtlinien stehen im Benutzerzweig (HKCU). Das ist Zusatzschutz und nicht die Grenze, denn ein Benutzer kann seinen eigenen Zweig ändern. Die Grenzen sind der Kern (Anmeldung, zweiter Faktor, Rechte) und das Netz (Segmentierung durch die Kollegen).

**Nur im Kiosk-Konto einrichten:** Die Richtlinien gelten für **alle** Chrome-Profile dieses Windows-Benutzers und sperren jede Adresse außer den Labor-Adressen. Wer `einrichten` im eigenen Konto ausführt, sperrt sich den normalen Browser, bis `rueckweg` gelaufen ist. Deshalb verlangt `einrichten` den Schalter `--kiosk-konto` als Bestätigung (nur `--trocken` geht ohne ihn). Hat das Konto Administratorrechte, warnt das Skript deutlich, denn ein Kiosk-Konto ist ein Standardbenutzer. Enthält der Richtlinienschlüssel schon fremde Werte, nennt das Skript sie. Sie bleiben unberührt.

**Das Zertifikat gilt für alle Programme dieses Windows-Benutzers**, nicht nur für Chrome: Auch Edge und Electron-Anwendungen vertrauen der WAMO-CA, sobald sie im Benutzerspeicher steht.

## 1. Was das Skript ändert

| Was | Wo | Rückweg |
| --- | --- | --- |
| Zertifikat der WAMO-CA | Benutzerspeicher Root (`certutil -user`, mit vollem Pfad aus `System32`), nie der Speicher des Rechners. Gilt für alle Programme dieses Benutzers (Chrome, Edge, Electron) | wird nur entfernt, wenn das Skript es importiert hat, und nur mit dem festgelegten Fingerabdruck |
| Chrome-Richtlinien (34 Werte) | `HKCU\Software\Policies\Google\Chrome` | vorige Werte aus der Sicherung |
| Chrome-Profil | `%LOCALAPPDATA%\wamo-chrome-labor` (eigenes Profil, getrennt von `wamo-chrome`) | bleibt, löschen mit `--profil-loeschen` |
| Platzdatei | `%LOCALAPPDATA%\WAMO\platz.json`: Platznummer und Adressen, **nie ein Token** | wird entfernt |
| Sicherung | `%LOCALAPPDATA%\WAMO\platz-sicherung.json`: Zustand vor der ersten Änderung | wird nach dem Rückweg entfernt |
| Verknüpfungen auf dem Desktop | `WAMO Labor starten.lnk` und `WAMO Labor pruefen.lnk` | werden entfernt |

Die Richtlinien: Übersetzung, Passwortmanager, Adress- und Zahlungsdaten, Anmeldung und Sync aus, kein Verlauf, Startseite und Startadresse sind die WAMO-Oberfläche, Mikrofon nur für `https://192.168.178.136:55445` ohne Rückfrage erlaubt, Kamera und Bildschirmaufnahme aus, Popups aus, Entwicklerwerkzeuge aus, Downloads aus, Inkognito und Gastmodus aus, Erweiterungen gesperrt, und nur diese Adressen dürfen geöffnet werden: `https://192.168.178.136` auf den Ports 55443, 55444, 55445 und 55447 sowie die lokale Weiterleitung `http://127.0.0.1:55489/weiter/` (siehe Abschnitt 5).

## 2. Voraussetzungen (Kollegen)

- Windows 10 oder 11, **Kiosk-Konto als Standardbenutzer**, ohne Administratorrechte. Einrichten, Prüfen und Starten laufen in diesem Konto.
- **Python 3.10 oder neuer** für alle Benutzer installiert (`python.exe`, nicht nur `pythonw.exe`). Die Verknüpfungen zeigen auf die Python-Datei, mit der eingerichtet wurde.
- **Google Chrome** als Systeminstallation oder im Benutzerprofil (Edge wird nicht unterstützt).
- Netz: Der Laptop erreicht `192.168.178.136` auf TCP 55443 bis 55445 und 55447. Die Netzsegmentierung ist Sache der Kollegen (Abschnitt 3.9 des Konzepts).
- Uhr per NTP abgleichen (mehr als 5 Minuten Abweichung bricht die Anmeldung mit Code und die TLS-Prüfung).
- **Headset mit USB oder Klinke.** Bluetooth-Kopfhörer liefern im Freisprechprofil unter Windows oft nur Stille, `pruefen` warnt davor.

## 3. Dateien auf den Laptop kopieren

Aus dem Repo (Ordner `scripts`) werden diese zehn Dateien gebraucht, die Tests und Attrappen (`test_*`, `*_attrappe.py`, `*_testwelt.py`) nicht:

`wamo_labor_platz.py`, `wamo_labor_platz_adressen.py`, `wamo_labor_platz_betrieb.py`, `wamo_labor_platz_pruefen.py`, `wamo_labor_platz_registrierung.py`, `wamo_labor_platz_start.py`, `wamo_labor_platz_umgebung.py`, `wamo_labor_platz_verknuepfung.py`, `wamo_labor_platz_zertifikat.py`, `wamo_labor_platz_zustand.py`

Dazu `infra\sokrates2\wamo-ca.crt` (nur das öffentliche Zertifikat, nie ein Schlüssel) **neben die Skripte** legen und in `wamo-ca.crt` benennen (so heißt sie schon). **Das Skript lässt nur genau diese CA zu:** Der SHA-256 der Datei steht fest im Skript (`CA_SHA256`, aus der Datei berechnet). `--ca-datei`, `WAMO_CA_FILE` und die Datei neben dem Skript werden gegen ihn geprüft, eine andere Datei bricht den Lauf ab. Die Datei wird einmal gelesen und geprüft, importiert wird aus einer geprüften Kopie im eigenen Ordner (`%LOCALAPPDATA%\WAMO`, nach dem Import gelöscht). Bei einer neuen CA im Repo schlägt ein Test fehl, dann beide Konstanten neu berechnen. Beispiel für den Ordner `C:\WAMO\scripts`:

```bat
robocopy scripts C:\WAMO\scripts wamo_labor_platz.py wamo_labor_platz_adressen.py wamo_labor_platz_betrieb.py wamo_labor_platz_pruefen.py wamo_labor_platz_registrierung.py wamo_labor_platz_start.py wamo_labor_platz_umgebung.py wamo_labor_platz_verknuepfung.py wamo_labor_platz_zertifikat.py wamo_labor_platz_zustand.py
copy infra\sokrates2\wamo-ca.crt C:\WAMO\scripts\wamo-ca.crt
```

Der Ordner sollte dem Kiosk-Konto nur Lesezugriff geben, damit das Skript nicht verändert wird.

## 4. Einrichten, prüfen, starten

Alle Befehle im Kiosk-Konto (`N` ist die Platznummer 1 bis 16, Plätze 15 und 16 sind für die Abnahme des Dienstes reserviert):

1. **Plan ansehen, ändert nichts:** `python C:\WAMO\scripts\wamo_labor_platz.py einrichten --platz N --trocken`
2. **Einrichten:** `python C:\WAMO\scripts\wamo_labor_platz.py einrichten --platz N --kiosk-konto`. Windows zeigt eine **Sicherheitswarnung** zum Zertifikat. Den angezeigten Fingerabdruck (SHA-1) mit der Zeile des Skripts und mit `infra/sokrates2/README.md`, Abschnitt 5.2, vergleichen und nur bei Übereinstimmung bestätigen. Das Skript prüft nach dem Import nach genau diesem Hash. Eine Wiederholung ist ungefährlich: Was schon stimmt, wird nicht angefasst ("nichts zu ändern").
3. **Prüfen:** `python C:\WAMO\scripts\wamo_labor_platz.py pruefen`. Jede Zeile beginnt mit `[ok]`, `[WARNUNG]` oder `[FEHLER]`, Ausgang 1 gibt es nur bei einem Fehler.
4. **Starten:** über die Verknüpfung **WAMO Labor starten** oder `python C:\WAMO\scripts\wamo_labor_platz.py starten`.

Was `pruefen` meldet:

| Zeile | Bedeutung und Abhilfe |
| --- | --- |
| Platz | Platzdatei vorhanden und gültig. Sonst `einrichten --platz N` |
| Zertifikat | WAMO-CA im Benutzerspeicher. Sonst `einrichten` |
| Richtlinien | alle Werte gesetzt (nur die Registrierung, nicht in Chrome gegengeprüft) |
| Chrome | Pfad von `chrome.exe`. Sonst Chrome installieren |
| Adresse (vier Zeilen) | Verbindung und **TLS-Prüfung gegen die WAMO-CA-Datei**. Jede HTTP-Antwort zählt als erreichbar, nur Verbindungs- und Zertifikatsfehler sind Fehler |
| Uhr | Abweichung zum Kopf `Date` des Servers, höchstens 5 Minuten. Sonst Zeit abgleichen |
| Mikrofon | aktive Eingabegeräte aus der Registrierung (nur lesend). Warnung bei einem aktiven Bluetooth-Freisprechgerät, dann Headset mit USB oder Klinke nehmen. Das ist eine Heuristik: Welches Gerät Chrome benutzt, lässt sich von hier aus nicht sicher feststellen |

## 5. Am Labortag: Platz-Token und Besucherwechsel

Der Betreuer holt das **Platz-Token** auf der Betreuer-Seite (nach `neuer-durchlauf` oder mit `platz-token`) und startet den Platz. Das Skript fragt das Token **ohne Anzeige** ab (32 Hexzeichen). Das Token wird nie gespeichert und nie ausgegeben.

Der Weg des Tokens ist mit Absicht ungewöhnlich, denn das Skript will es nirgends ablegen. Die Aussagen gelten für das Skript, nicht für alles, was Chrome oder die Betreuer-Seite damit tun (siehe Restrisiko):

- **Nicht in der Befehlszeile** (Prozessliste, Task-Manager, `chrome://version`, Protokolle der Prozessüberwachung).
- **Das Skript schreibt es nie in eine Datei** (Platte, Wiederherstellung, Virenscanner) **und nie in die Zwischenablage** (Verlauf mit Win+V, Abgleich mit dem Konto).
- Stattdessen öffnet das Skript für höchstens 10 Sekunden einen Anschluss auf `127.0.0.1:55489`. Das Board-Fenster bekommt nur die Adresse `http://127.0.0.1:55489/weiter/<Zufallspfad>` und wird vom Skript einmalig auf `/board#t=<Token>` umgeleitet. Danach ist der Anschluss zu. Der Port wird ausschließlich gebunden, der Host-Kopf und der Pfad werden zeitkonstant verglichen. Der Pfad gilt für genau eine **erfolgreiche** Auslieferung: Misslingt das Senden (Verbindung weg), bleibt der Einmalwert gültig, bis es gelingt oder die Frist von 10 Sekunden endet.
- Die Adresse des Boards kommt aus der Platzdatei und wird **vor der Eingabe** gegen die festen Adressen des Labors geprüft. Eine veränderte Platzdatei kann das Token nie an einen fremden Rechner schicken.

Restrisiko, ehrlich begrenzt:

- Ein anderes Programm desselben Benutzers kann den Zufallspfad aus der Befehlszeile von Chrome lesen und die Umleitung vor Chrome abholen.
- **Loopback gilt rechnerweit:** Auch andere gleichzeitig angemeldete Windows-Konten dieses Rechners erreichen den Anschluss. Sie brauchen den Zufallspfad, und der Anschluss ist höchstens 10 Sekunden offen.
- **Was Chrome mit der Adresse tut, hat das Skript nicht in der Hand.** Ob `/board#t=...` auf der Platte landet, hängt an der Wirkung der Richtlinie im Benutzerzweig (`SavingBrowserHistoryDisabled`) und an den Sitzungsdateien des Profils: Sie können die Adresse mit `#t=` enthalten, solange die Seite sie nicht aus der Adresszeile entfernt hat. Deshalb gehört das Profil nach dem Labortag gelöscht (Abschnitt 6), und die Generalprobe prüft es (Abschnitt 8).
- **Die Betreuer-Seite kopiert:** Die Knöpfe "Token kopieren" und "Link kopieren" legen das Token in die Zwischenablage und in den Verlauf (Win+V) des Betreuer-Geräts. "Nie in der Zwischenablage" gilt nur für den Weg des Skripts. Am Betreuer-Gerät den Verlauf der Zwischenablage ausschalten oder nach dem Kopieren leeren (Win+V, Alle löschen).
- Das Token gilt nur für das Board dieses einen Platzes (nur lesend) und wird mit `neuer-durchlauf` ungültig.

**Besucherwechsel:** beide Chrome-Fenster schließen, auf der Betreuer-Seite `neuer-durchlauf` (neues Token), dann den Platz neu starten und das neue Token eingeben. Ohne Token nur die Oberfläche: `starten --ohne-board`. Belegt ein anderes Programm den Port 55489, meldet das Skript es und startet nichts.

## 6. Rückweg

1. `python C:\WAMO\scripts\wamo_labor_platz.py rueckweg --trocken` zeigt den Plan.
2. `python C:\WAMO\scripts\wamo_labor_platz.py rueckweg` stellt die vorigen Richtlinienwerte her, entfernt die Verknüpfungen und die Platzdatei und entfernt das Zertifikat, **wenn das Skript es importiert hat** (war es schon vorher da, bleibt es). Ohne Sicherung gibt es nichts zurückzunehmen.
3. **Nach dem Labortag zusätzlich** `rueckweg --profil-loeschen`: Das Chrome-Profil enthält die angemeldete Sitzung des Platzes und gehört gelöscht (vorher Chrome schließen).

Die Sicherung wird vor der ersten Änderung geschrieben und danach nie überschrieben: Auch nach mehreren Läufen führt der Rückweg zum echten Ausgangszustand.

## 7. Fehlerbehebung

| Meldung | Ursache und Abhilfe |
| --- | --- |
| `einrichten` verändert dieses Windows-Konto ... mit --kiosk-konto bestätigen | Nur im Kiosk-Konto mit `--kiosk-konto` ausführen. Den Plan gibt es auch ohne den Schalter mit `--trocken` |
| Platznummer fehlt oder ungültig | beim ersten Mal `--platz N` mit 1 bis 16 angeben |
| CA-Datei nicht gefunden | `wamo-ca.crt` neben die Skripte legen oder `--ca-datei PFAD` |
| CA-Datei weicht von der mitgelieferten WAMO-CA ab | Es wird nur `infra/sokrates2/wamo-ca.crt` aus dem Repo zugelassen. Die richtige Datei kopieren (die erste vorhandene Kandidatendatei muss stimmen, auch `WAMO_CA_FILE`) |
| Die Sicherung ... ist beschädigt oder verändert | `platz-sicherung.json` wurde verändert oder ist unvollständig. Es wurde nichts geändert, die Datei bleibt liegen: von Hand prüfen, vorher den Zustand mit `pruefen` ansehen |
| ... lässt sich nicht verlustfrei sichern | Ein vorhandener Wert (binär, mehrzeilig, Liste mit Unterschlüssel) ginge beim Rückweg verloren. Wert sichern oder entfernen, dann noch einmal |
| Das Zertifikat wurde nicht importiert | der Dialog wurde abgelehnt: `einrichten` wiederholen und bestätigen |
| TLS-Prüfung gegen die WAMO-CA gescheitert | falsche oder fehlende CA-Datei, oder die Uhr geht falsch |
| Die Uhr geht ... vor oder hinter der Serveruhr | Zeit per NTP abgleichen |
| Weiterleitung nicht abgeholt | Chrome hat die Startadresse nicht aufgerufen (Richtlinie, Port, Chrome-Start). Noch einmal starten, sonst `pruefen` |
| Anschluss für die Weiterleitung ist belegt | anderes Programm auf Port 55489 beenden |
| platz.json gehört nicht zu den festen Adressen | Datei wurde verändert: `einrichten --platz N` stellt sie wieder her |

## 8. Nur auf einem echten Laptop prüfbar (Generalprobe)

Die Tests laufen mit Attrappen für Registrierung, Zertifikatsspeicher, Prozesse und Netz (`python -m pytest -q` im Ordner `scripts`). Nicht ohne echten Laptop belegt sind:

- der Zertifikatsdialog von Windows und das Verhalten von `certutil -user -addstore Root` im Kiosk-Konto,
- ob Chrome die Richtlinien aus dem **Benutzerzweig** übernimmt und ob `URLBlocklist` mit `*` die Seiten `chrome://...` und `about:blank` mitsperrt (Kontrolle: `chrome://policy` im Testkonto, bevor die Adressen gesperrt sind),
- **ob das Token auf der Platte landet:** Nach einem echten Start Chrome schließen und den Profilordner `%LOCALAPPDATA%\wamo-chrome-labor` mit einem Suchwerkzeug nach den 32 Zeichen des Tokens durchsuchen (auch als Teil von `#t=`), **ohne das Token oder einen Treffer auszugeben**: Das Werkzeug meldet nur Dateinamen und die Zahl der Treffer. Bei einem Treffer das Profil löschen und Niko melden (Wirkung von `SavingBrowserHistoryDisabled` und der Sitzungsdateien),
- ob die Filter der Adressliste mit IP-Adresse und Port wie vorgesehen greifen, insbesondere die Zeile ohne Port und mit Pfad für `127.0.0.1:55489/weiter/`,
- dass das Mikrofon für die WAMO-Adresse ohne Rückfrage freigegeben ist,
- die Fensterlage (zwei Hälften des Arbeitsbereichs, auch bei skalierter Anzeige) und dass das zweite Fenster im selben Chrome-Prozess mit eigener Größe öffnet,
- der Weg des Tokens: Umleitung, Entfernen aus der Adresszeile durch die Seite, die Kopfzeile des Fensters bei einer Umleitung in einem App-Fenster, und dass `chrome://version` und der Task-Manager nur die Weiterleitungsadresse mit dem Zufallspfad zeigen, nie das Token,
- die Mikrofon-Prüfung (Heuristik auf die Namen der Geräte in `MMDevices`) mit einem echten Bluetooth-Headset,
- die Verknüpfungen (Erstellung über die Shell wurde auf dem Entwicklungsrechner einmal in einem Testordner geprüft, nicht auf dem Kiosk-Konto).
