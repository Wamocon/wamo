# Platz-Skript: Laptops des Testlabors einrichten

**Stand:** 13.11.2026. Gehört zum Paket P8 (`docs/testlabor-wamo.md`, Abschnitt 3.9). Das Skript richtet einen Windows-Laptop als Platz des WAMO-Testlabors ein, startet die beiden Fenster (WAMO links, Board rechts), prüft den Platz und nimmt alles wieder zurück. Es läuft im Kiosk-Konto **ohne Administratorrechte**, braucht nur Python (Standardbibliothek) und nutzt weder PowerShell noch Zusatzpakete.

**Wichtig:** Die Chrome-Richtlinien stehen im Benutzerzweig (HKCU). Das ist Zusatzschutz und nicht die Grenze, denn ein Benutzer kann seinen eigenen Zweig ändern. Die Grenzen sind der Kern (Anmeldung, zweiter Faktor, Rechte) und das Netz (Segmentierung durch die Kollegen).

## 1. Was das Skript ändert

| Was | Wo | Rückweg |
| --- | --- | --- |
| Zertifikat der WAMO-CA | Benutzerspeicher Root (`certutil -user`), nie der Speicher des Rechners | wird nur entfernt, wenn das Skript es importiert hat |
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

Dazu `infra\sokrates2\wamo-ca.crt` (nur das öffentliche Zertifikat, nie ein Schlüssel) **neben die Skripte** legen und in `wamo-ca.crt` benennen (so heißt sie schon). Beispiel für den Ordner `C:\WAMO\scripts`:

```bat
robocopy scripts C:\WAMO\scripts wamo_labor_platz.py wamo_labor_platz_adressen.py wamo_labor_platz_betrieb.py wamo_labor_platz_pruefen.py wamo_labor_platz_registrierung.py wamo_labor_platz_start.py wamo_labor_platz_umgebung.py wamo_labor_platz_verknuepfung.py wamo_labor_platz_zertifikat.py wamo_labor_platz_zustand.py
copy infra\sokrates2\wamo-ca.crt C:\WAMO\scripts\wamo-ca.crt
```

Der Ordner sollte dem Kiosk-Konto nur Lesezugriff geben, damit das Skript nicht verändert wird.

## 4. Einrichten, prüfen, starten

Alle Befehle im Kiosk-Konto (`N` ist die Platznummer 1 bis 16, Plätze 15 und 16 sind für die Abnahme des Dienstes reserviert):

1. **Plan ansehen, ändert nichts:** `python C:\WAMO\scripts\wamo_labor_platz.py einrichten --platz N --trocken`
2. **Einrichten:** `python C:\WAMO\scripts\wamo_labor_platz.py einrichten --platz N`. Windows zeigt eine **Sicherheitswarnung** zum Zertifikat. Den angezeigten Fingerabdruck (SHA-1) mit der Zeile des Skripts und mit `infra/sokrates2/README.md`, Abschnitt 5.2, vergleichen und nur bei Übereinstimmung bestätigen. Das Skript prüft nach dem Import nach genau diesem Hash. Eine Wiederholung ist ungefährlich: Was schon stimmt, wird nicht angefasst ("nichts zu ändern").
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

Der Weg des Tokens ist mit Absicht ungewöhnlich, denn er darf nirgends liegen bleiben:

- **Nicht in der Befehlszeile** (Prozessliste, Task-Manager, `chrome://version`, Protokolle der Prozessüberwachung).
- **Nicht in einer Datei** (Platte, Wiederherstellung, Virenscanner) und **nicht in der Zwischenablage** (Verlauf mit Win+V, Abgleich mit dem Konto).
- Stattdessen öffnet das Skript für höchstens 10 Sekunden einen Anschluss auf `127.0.0.1:55489`. Das Board-Fenster bekommt nur die Adresse `http://127.0.0.1:55489/weiter/<Zufallspfad>` und wird vom Skript einmalig auf `/board#t=<Token>` umgeleitet. Danach ist der Anschluss zu. Der Port wird ausschließlich gebunden, der Host-Kopf geprüft, der Pfad gilt genau einmal.
- Die Adresse des Boards kommt aus der Platzdatei und wird **vor der Eingabe** gegen die festen Adressen des Labors geprüft. Eine veränderte Platzdatei kann das Token nie an einen fremden Rechner schicken.

Restrisiko: Ein anderes Programm desselben Benutzers kann den Zufallspfad aus der Befehlszeile von Chrome lesen und die Umleitung vor Chrome abholen. Das Token gilt nur für das Board dieses einen Platzes (nur lesend) und wird mit `neuer-durchlauf` ungültig.

**Besucherwechsel:** beide Chrome-Fenster schließen, auf der Betreuer-Seite `neuer-durchlauf` (neues Token), dann den Platz neu starten und das neue Token eingeben. Ohne Token nur die Oberfläche: `starten --ohne-board`. Belegt ein anderes Programm den Port 55489, meldet das Skript es und startet nichts.

## 6. Rückweg

1. `python C:\WAMO\scripts\wamo_labor_platz.py rueckweg --trocken` zeigt den Plan.
2. `python C:\WAMO\scripts\wamo_labor_platz.py rueckweg` stellt die vorigen Richtlinienwerte her, entfernt die Verknüpfungen und die Platzdatei und entfernt das Zertifikat, **wenn das Skript es importiert hat** (war es schon vorher da, bleibt es). Ohne Sicherung gibt es nichts zurückzunehmen.
3. **Nach dem Labortag zusätzlich** `rueckweg --profil-loeschen`: Das Chrome-Profil enthält die angemeldete Sitzung des Platzes und gehört gelöscht (vorher Chrome schließen).

Die Sicherung wird vor der ersten Änderung geschrieben und danach nie überschrieben: Auch nach mehreren Läufen führt der Rückweg zum echten Ausgangszustand.

## 7. Fehlerbehebung

| Meldung | Ursache und Abhilfe |
| --- | --- |
| Platznummer fehlt oder ungültig | beim ersten Mal `--platz N` mit 1 bis 16 angeben |
| CA-Datei nicht gefunden | `wamo-ca.crt` neben die Skripte legen oder `--ca-datei PFAD` |
| Das Zertifikat wurde nicht importiert | der Dialog wurde abgelehnt: `einrichten` wiederholen und bestätigen |
| TLS-Prüfung gegen die WAMO-CA gescheitert | falsche oder fehlende CA-Datei, oder die Uhr geht falsch |
| Die Uhr geht ... vor oder hinter der Serveruhr | Zeit per NTP abgleichen |
| Weiterleitung nicht abgeholt | Chrome hat die Startadresse nicht aufgerufen (Richtlinie, Port, Chrome-Start). Noch einmal starten, sonst `pruefen` |
| Anschluss für die Weiterleitung ist belegt | anderes Programm auf Port 55489 beenden |
| platz.json gehört nicht zu den festen Adressen | Datei wurde verändert: `einrichten --platz N` stellt sie wieder her |

## 8. Nur auf einem echten Laptop prüfbar (Generalprobe)

Die Tests laufen mit Attrappen für Registrierung, Zertifikatsspeicher, Prozesse und Netz (`python -m pytest -q` im Ordner `scripts`). Nicht ohne echten Laptop belegt sind:

- der Zertifikatsdialog von Windows und das Verhalten von `certutil -user -addstore Root` im Kiosk-Konto,
- ob Chrome die Richtlinien aus dem **Benutzerzweig** übernimmt und ob `URLBlocklist` mit `*` die Seiten `chrome://...` und `about:blank` mitsperrt (Kontrolle: `chrome://policy` vor dem ersten Einrichten in einem Testkonto),
- ob die Filter der Adressliste mit IP-Adresse und Port wie vorgesehen greifen, insbesondere die Zeile ohne Port und mit Pfad für `127.0.0.1:55489/weiter/`,
- dass das Mikrofon für die WAMO-Adresse ohne Rückfrage freigegeben ist,
- die Fensterlage (zwei Hälften des Arbeitsbereichs, auch bei skalierter Anzeige) und dass das zweite Fenster im selben Chrome-Prozess mit eigener Größe öffnet,
- der Weg des Tokens: Umleitung, Entfernen aus der Adresszeile durch die Seite, die Kopfzeile des Fensters bei einer Umleitung in einem App-Fenster,
- die Mikrofon-Prüfung (Heuristik auf die Namen der Geräte in `MMDevices`) mit einem echten Bluetooth-Headset,
- die Verknüpfungen (Erstellung über die Shell wurde auf dem Entwicklungsrechner einmal in einem Testordner geprüft, nicht auf dem Kiosk-Konto).
