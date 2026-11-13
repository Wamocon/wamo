# Testlabor 28.11.2026: Runbook für die Betreuer

**Stand:** 13.11.2026, Entwurf für die Generalprobe am 26.11.2026. Gehört zum Konzept `docs/testlabor-wamo.md` (3.9, 3.10, 5.2) und zur Checkliste `docs/labor-morgencheckliste.md`. Dieses Runbook führt Betreuer ohne Vorwissen durch den Labortag: Plätze öffnen und schließen, Notaus, Störungen, Rückfall, wer wen anruft. Alles, was am Server geschieht, gehört der Technik-Bereitschaft und steht in den Abschnitten 5 und 9. Was in den Unterlagen noch fehlt (Namen, Telefonnummern, Raum, Uhrzeiten), steht im Text als `[offen: ...]` und gesammelt in Abschnitt 11.

**So liest du es.** Jeder Ablauf besteht aus kurzen nummerierten Schritten. Mach sie in dieser Reihenfolge. Steht bei einem Schritt "Erwartet", vergleiche, bevor du weitermachst. Passt es nicht, höre auf und gehe zu Abschnitt 6 (Störungen) oder rufe die Leitung (Abschnitt 8). Ein Abbruch ist nie ein Fehler.

## 1. Rollen und Regeln

### 1.1 Rollen

Namen und Telefonnummern trägt Niko vor dem 26.11. ein (Abschnitt 8). Hier stehen nur die Rollen.

| Kürzel | Rolle | Aufgabe am Labortag |
| --- | --- | --- |
| B | Betreuer am Platz | Besucher begrüßen, Hinweis zeigen, Haken setzen, Platz öffnen und schließen, bei Störungen die ersten Schritte |
| L | Leitung der Betreuer vor Ort | entscheidet, ob ein Platz zubleibt oder der Notaus ausgelöst wird, ruft Technik und Datenschutz |
| T | Technik-Bereitschaft WAMO | alles am Server und mit den Verwaltungsskripten (Abschnitte 5 und 9) |
| N | Netz und Geräte (Kollegen) | WLAN, Kabel, Laptops, Headsets |
| D | Datenschutz-Ansprechperson | Löschwünsche, Beschwerden, Zweifel zum Hinweis |
| F | Trainer | Fachfragen zu den Karten und zum Lernziel |
| P | Projektverantwortung WAMO | entscheidet nach einem Notaus über das Weitermachen, Konsole des Sprachmodells (Ausgabenlimit, Guthaben) |

### 1.2 Sieben Regeln

1. **Ohne Haken kein Durchlauf.** Der Haken heißt: Der Besucher hat den Datenschutzhinweis gelesen und bestätigt. Nie vorab setzen, nie für jemand anderen.
2. **Kein Token zeigen.** Platz-Token und Betreuer-Token nicht vorlesen, nicht fotografieren, nicht in Chat oder Mail schreiben. Sie wandern nur von der Betreuer-Seite in das Platz-Skript.
3. **Besucher klicken Freigaben selbst.** Eine Freigabekarte zeigt, was WAMO tun will. Nur der Besucher klickt "Freigeben" oder "Ablehnen". Betreuer klicken nie für ihn.
4. **Erfundene Namen.** Besucher nennen keine echten persönlichen Daten. Bitte freundlich um Fantasienamen.
5. **Im Zweifel beenden.** "Durchlauf beenden" und die Leitung rufen ist immer richtig.
6. **Server nur Technik.** Betreuer arbeiten an den Plätzen und auf der Betreuer-Seite, sonst nirgends.
7. **Niemand wird gedrängt.** Wer nicht zustimmen möchte, schaut am Betreuer-Platz zu (Abschnitt 4).

## 2. Der Aufbau in Kürze

Ein Platz ist ein Laptop mit zwei Fenstern. Links läuft WAMO (Gespräch mit Stimme), rechts das Board mit den Tickets dieses Platzes (nur lesend). Die Betreuer bedienen alles über die Betreuer-Seite auf ihrem eigenen Gerät.

| Plätze | Verwendung |
| --- | --- |
| 1 bis 8 | Besucher |
| 9 | Reserve (wird bei Bedarf freigeschaltet) |
| 10 bis 14 | nicht belegt |
| 15 und 16 | nur für die Abnahme des Dienstes. Die Betreuer-Seite zeigt "Reserviert für die Abnahme, kein Besucher". Nie öffnen |

Platz N gehört zum Profil `labor-NN` (Platz 3 zu `labor-03`). Das brauchst du nur am Telefon mit der Technik.

| Was | Adresse |
| --- | --- |
| Betreuer-Seite (nur von den Betreuer-PCs) | `https://192.168.178.136:55447/betreuer` |
| Datenschutzhinweis für Besucher (mit Knopf Drucken) | `https://192.168.178.136:55447/datenschutz` |
| WAMO-Oberfläche | öffnet das Platz-Skript selbst (`https://192.168.178.136:55445`) |

Auf dem Desktop jedes Platz-Laptops liegen zwei Verknüpfungen: **WAMO Labor starten** und **WAMO Labor pruefen**.

## 3. Ablauf je Besucher

Die Knöpfe heißen auf der Betreuer-Seite genau so: "Neuer Durchlauf …", "Durchlauf beenden", "Zurücksetzen …", "Daten löschen …", "Platz-Token erzeugen". Bei den Knöpfen mit Auslassungspunkten kommt ein Fenster, in dem du die Platznummer eintippst.

### 3.1 Platz öffnen (neuer Durchlauf)

Ein Platz ist frei, wenn seine Karte "Geschlossen, kein Durchlauf" oder "Durchlauf beendet" zeigt.

1. Besucher an den Platz bitten. Das Headset ist noch nicht auf.
2. Den Datenschutzhinweis zeigen: Aushang am Platz, oder auf der Betreuer-Seite den Link "Hinweis für den Besucher anzeigen" (öffnet einen neuen Tab). Besucher in Ruhe lesen lassen und Fragen beantworten. Die Altersangabe steht im Hinweis: mindestens 16 Jahre, wer jünger ist, nimmt mit Zustimmung einer Aufsichtsperson (zum Beispiel Eltern oder Lehrkraft) teil. Es ist ein Richtwert, keine harte Grenze (Entscheidung von Niko am 13.11.2026). Halte dich an den Text, der am Aushang hängt.
3. Fragen: "Hast du den Hinweis gelesen und bist du einverstanden?" (Der Hinweis duzt die Besucher.) Bei einem Nein gehe zu Abschnitt 4. Es wird dann nichts angelegt.
4. Auf der Betreuer-Seite die Karte "Platz N" suchen und den Knopf **Neuer Durchlauf …** klicken.
5. Im Fenster prüfen: Die Zeile "Version" zeigt die Version des Hinweises (jetzt 1). Steht dort ein Fehlerhinweis, dass der Dienst schon eine höhere Version kennt, die Seite mit F5 neu laden und bei Schritt 4 beginnen.
6. Den Haken setzen: "Der Besucher hat den Datenschutzhinweis (Version 1) gelesen und bestätigt." Nur nach einem Ja aus Schritt 3.
7. Zur Bestätigung die Platznummer eintippen (zum Beispiel 3). **Ausführen** wird erst frei, wenn Haken und Platznummer stimmen.
8. **Ausführen** klicken. Erwartet: die Meldung "Neuer Durchlauf für Platz N: ausgeführt." und ein Kasten "Neuer Platz-Token für Platz N".
9. Der Kasten zeigt den **Besucher-Link** (`…/board#t=…`) und den **Platz-Token** und blendet sich nach 60 Sekunden aus ("Wird in … Sekunden ausgeblendet"). Verpasst? Dann auf der Karte **Platz-Token erzeugen** klicken. Der alte Token ist damit ungültig.
10. Am Platz-Laptop beide Chrome-Fenster schließen, falls offen. Die Verknüpfung **WAMO Labor starten** doppelklicken.
11. Das Skript fragt den Platz-Token ab. Auf der Betreuer-Seite **Token kopieren** klicken und im Skriptfenster einfügen, dann Enter. Die Eingabe bleibt unsichtbar, das ist gewollt `[offen: Einfügen in die verdeckte Eingabe in der Generalprobe prüfen, sonst die 32 Zeichen abtippen]`.
12. Erwartet: links WAMO, rechts das Board mit "Platz N" und den drei Spalten "Zu erledigen", "In Arbeit" und "Erledigt" mit den Startkarten (sechs Tickets, darunter LAB-11, LAB-12 und LAB-20).
13. Headset aufsetzen lassen. Der Besucher tippt auf "Gespräch starten". Die erste Aufgabe kommt aus der Kartenliste `[offen: Kartenliste der 17 Karten, Ort und Ausdruck]`.
14. Kontrolle auf der Betreuer-Seite: Die Karte zeigt "Durchlauf offen" und eine Einwilligung "Version 1 vom …".

**Weg B, der Besucher-Link.** Der Link zeigt das Board dieses Platzes, ohne das Platz-Skript. Nutze ihn nur, wenn das Board-Fenster fehlt und das Skript nicht hilft. Den Link nie weitergeben `[offen: In der Generalprobe klären, wo der Link im Kiosk-Fenster eingegeben wird]`.

### 3.2 Während des Gesprächs

1. Zuhören, nicht eingreifen, solange es läuft. WAMO darf Fehler machen. Fehler zu finden und als Ticket anzulegen ist das Ziel der Veranstaltung.
2. Erscheint eine Freigabekarte, liest der Besucher sie und klickt selbst **Freigeben** oder **Ablehnen**. Nie für ihn klicken. Eine Karte gilt etwa zwei Minuten, danach fragt der Besucher WAMO neu.
3. Nennt der Besucher echte persönliche Daten, bitte um Fantasiewerte. Wurde schon etwas gesagt oder getippt, gehe zu Abschnitt 4.3.
4. Eine Sitzung endet technisch nach höchstens 120 Minuten. Die geplante Besuchsdauer legt die Leitung fest `[offen: Dauer eines Besuchs, Generalprobe nutzt 10 Minuten]`.

### 3.3 Zurücksetzen

Wann: Der Besucher möchte dieselbe Aufgabe noch einmal, oder das Board ist durcheinander. Der Besucher bleibt am Platz.

1. Auf der Betreuer-Seite bei Platz N **Zurücksetzen …** klicken.
2. Die Platznummer eintippen, **Ausführen** klicken.
3. Erwartet: "Zurücksetzen für Platz N: ausgeführt." Das Board zeigt nach wenigen Sekunden wieder den Startstand. Durchlauf, Einwilligung, Platz-Token und das Gespräch bleiben.
4. Alle Tickets und Kommentare dieses Durchlaufs sind weg. Sag das dem Besucher.

### 3.4 Durchlauf beenden

Wann: Der Besucher ist fertig oder geht.

1. Auf der Betreuer-Seite bei Platz N **Durchlauf beenden** klicken. Es kommt keine Rückfrage.
2. Erwartet: Die Karte zeigt "Durchlauf beendet". Innerhalb von 5 Sekunden endet das Gespräch, das WAMO-Fenster lädt sich neu und ist leer. Das Board bleibt sichtbar, der Platz-Token bleibt gültig.
3. Besucher verabschieden. Für den nächsten Besucher weiter mit 3.1, sonst mit 3.5.

### 3.5 Daten löschen

Wann: Der Besucher geht und kein Nächster folgt sofort, der Besucher wünscht die Löschung, oder der Tag endet.

1. Erst den Durchlauf beenden (3.4).
2. Auf der Betreuer-Seite bei Platz N **Daten löschen …** klicken, Platznummer eintippen, **Ausführen**.
3. Erwartet: "Daten löschen für Platz N: ausgeführt." Die Karte zeigt "Geschlossen, kein Durchlauf", Einwilligung "keine", Tickets 0. Die Seite zeigt außerdem einen neuen Platz-Token. Er wird nicht gebraucht, nicht weitergeben. Für den nächsten Besucher entsteht mit **Neuer Durchlauf …** ein frischer.
4. Meldet die Seite "Das Löschen ist noch nicht abgeschlossen, die Datei ist nicht bereinigt. Bitte wiederholen.", den Schritt 2 noch einmal ausführen. Nach drei Versuchen T rufen.
5. **Wichtig:** Die Betreuer-Seite löscht die Tickets, Kommentare und die Einwilligung dieses Platzes. Der Gesprächstext liegt zusätzlich im Kern. Er wird am Ende des Tages durch den Nachlauf gelöscht (Abschnitt 10), auf Wunsch eines Besuchers sofort durch T (4.3).

### 3.6 Platz-Token ersetzen

Wann: Der Token fehlt, ging verloren, oder das Board zeigt "Dieser Platz-Link gilt nicht mehr, bitte beim Betreuer melden".

1. Auf der Betreuer-Seite bei Platz N **Platz-Token erzeugen** klicken. Es kommt keine Rückfrage.
2. Den Kasten innerhalb von 60 Sekunden nutzen. Der alte Token gilt ab jetzt nicht mehr.
3. Am Laptop beide Chrome-Fenster schließen, **WAMO Labor starten**, den neuen Token einfügen.

## 4. Besucher möchte nicht, oder anders

### 4.1 Besucher möchte nicht zustimmen

1. Kein Haken, kein neuer Durchlauf, kein Platz-Token. Für diese Person wird nichts gespeichert.
2. Sag: "Kein Problem. Die Teilnahme ist freiwillig. Du kannst dir WAMO gern am Betreuer-Platz ansehen." (So steht es im Hinweis.)
3. Führe die Person zum Betreuer-Platz `[offen: Standort und Gerät des Betreuer-Platzes]`. Dort führt ein Betreuer vor (7.3) oder zeigt die Aufzeichnung (7.2).
4. Frage nicht nach Gründen und notiere keinen Namen.

### 4.2 Besucher bricht ab

1. **Durchlauf beenden** (3.4).
2. Möchte die Person, dass nichts bleibt: **Daten löschen** (3.5) und weiter mit 4.3.

### 4.3 Besucher verlangt die Löschung

Der Hinweis verspricht: Auf Wunsch löschen wir deine Daten sofort.

1. Bleib ruhig und bestätige: "Wir löschen deine Daten."
2. **Durchlauf beenden** und **Daten löschen** (3.4, 3.5).
3. Notiere nur Platz und Uhrzeit von bis, ohne Namen.
4. Rufe T und nenne Platz und Zeitfenster. T sperrt dafür das Profil des Platzes, löscht den Gesprächstext dieses Zeitfensters im Kern und schaltet das Profil wieder frei (9.4). Der Platz ist dafür mindestens 3 Minuten nicht nutzbar (Sperrfrist), danach läuft das Löschen.
5. Informiere D (Abschnitt 8).
6. Sag der Person: "Die Löschung ist beauftragt. Spätestens am 11.12.2026 ist alles gelöscht."

### 4.4 Weitere Fälle

| Fall | Was du tust |
| --- | --- |
| Besucher nennt echte persönliche Daten | Um Fantasiewerte bitten. Wurde es schon gesagt oder getippt: 4.3 |
| Besucher ist jünger als 16 und keine Aufsichtsperson hat zugestimmt | Keine Einwilligung am Platz. Aufsichtsperson dazuholen (dann ist die Teilnahme möglich) oder am Betreuer-Platz zusehen lassen (4.1) |
| Besucher ist unsicher, ob er mitmachen will | Zuerst zusehen lassen (4.1). Der Haken kommt erst, wenn die Person Ja sagt |
| Besucher fotografiert oder filmt den Bildschirm | Freundlich bitten, kein Token und keine Daten anderer Besucher zu fotografieren. Bei Zweifel L rufen |

## 5. Notaus

### 5.1 Wann

Nur bei einem Sicherheitsproblem oder auf Anweisung der Leitung. Zum Beispiel: Ein Besucher sieht oder erreicht etwas Internes (Systemnamen, Pfade, fremde Daten, Fehlerseiten mit Technik). Eine Aktion geschieht ohne Klick des Besuchers. Es besteht der Verdacht auf Datenabfluss. Mehrere Plätze zeigen Unzulässiges. Eine normale Störung ist kein Notaus (Abschnitt 6).

### 5.2 Stufe 1, Betreuer, sofort (unter einer Minute)

1. Laut sagen: "Notaus." Die Leitung rufen.
2. Besucher bitten, nichts mehr zu bedienen und das Headset abzusetzen. Es wird nichts gelöscht und nichts zurückgesetzt, denn die Daten belegen, was geschehen ist.
3. Auf der Betreuer-Seite bei jeder Karte mit "Durchlauf offen" **Durchlauf beenden** klicken. Erwartet: Alle Karten zeigen "Durchlauf beendet", jedes Gespräch endet binnen 5 Sekunden.
4. Fenster am Platz offen lassen. Platz, Uhrzeit und das Gesehene aufschreiben.
5. T anrufen: "Notaus, Platz, Uhrzeit, was gesehen."

### 5.3 Stufen 2 bis 4, Technik, in dieser Reihenfolge

Die Befehle laufen im Repo-Hauptordner auf dem PC der Technik (Voraussetzungen in Abschnitt 9).

| Stufe | Wer | Was | Befehl | Wirkung | Zeit bis zur Wirkung |
| --- | --- | --- | --- | --- | --- |
| 1 | B | alle Durchläufe beenden | Betreuer-Seite, **Durchlauf beenden** je Platz | Gespräche enden, Schreiben im Dienst wird abgewiesen | unter 5 Sekunden, Klicken etwa 1 Minute |
| 2 | T | alle Laborprofile sperren | `python scripts/wamo_labor.py sperren --alle --ausfuehren` | laufende Gespräche enden, kein neues startet, Daten bleiben | rund 2 Minuten (Zwischenspeicher bis 60 s, Prüfung laufender Sitzungen jede Minute) |
| 3 | T | Dienst abschalten (Board, Tickets, Betreuer-Seite) | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-rueckweg.sh"` | Container, Site und Firewall-Kette weg, Daten und Token bleiben, der Kern nimmt keine Sitzung mehr an | `[offen: Dauer messen in der Rückwegübung am 27.11.]`, das Skript nennt die Ausfalldauer |
| 4 | T | Kern zurück auf die vorige Instanz | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh rueckweg-kern"` | neue Sitzungen gehen auf den vorigen Kern, der Laborprofile mit 403 abweist | unter 5 Minuten, nur solange der vorige Slot noch läuft (`status` zeigt es) |

Läuft der vorige Slot nicht mehr (nach `ausleiten`), gibt es keinen schnellen Rückweg. Dann stellt T das vorige Release erneut bereit (`wamo-core-slots.sh release` und `umschalten-kern`) `[offen: Dauer ist nicht gemessen, die Rückwegübung am 27.11. misst sie]`.

### 5.4 Welche Stufe

1. Stufe 1 und 2 immer. Sie stoppen die Besucher und lassen alle Daten stehen.
2. Der Dienst fällt auf (Board zeigt fremde Inhalte oder Tickets anderer Plätze): zusätzlich Stufe 3.
3. Der Kern fällt auf (Gespräch zeigt Internes, Aktion ohne Klick): zusätzlich Stufe 4.
4. Unklar: Stufe 3 und 4 nur auf Anweisung von P.

### 5.5 Danach

1. T meldet P. P und L entscheiden, ob der Labortag endet. Bei Verdacht auf Datenabfluss informiert L die Datenschutz-Ansprechperson D.
2. Kein Platz öffnet wieder ohne Freigabe von P und T. Wiederaufnahme: T schaltet die Profile mit `wamo_labor.py aktivieren --kennung labor-01 ... --ausfuehren` frei. Den Dienst nach Stufe 3 stellt T mit `wamo-labor-einrichten.sh einrichten` wieder her `[offen: Verhalten im laufenden Betrieb in der Rückwegübung prüfen]`.
3. Ein Sicherheitsfehler hebt den Freeze auf (Konzept 5.3). Eine Korrektur am Kern braucht einen neuen Release-Abend und ist am Labortag nicht vorgesehen. Dann gilt der Rückfall (Abschnitt 7).

## 6. Störungen und Sofortmaßnahmen

Merke dir zuerst: Ein Platz betroffen heißt meist Laptop, Netz am Platz oder Token. Alle Plätze betroffen heißt Dienst, Kern, Claude oder Spracherkennung.

### 6.1 Claude fällt aus

Du merkst es: WAMO antwortet nicht mehr, sehr langsam, oder meldet, dass ein anderes Modell antwortet. Bei leerem Guthaben oder erreichtem Limit sieht es genauso aus.

Sofort (B):
1. Zum Besucher: "Einen Moment, WAMO hat gerade eine Störung." Nichts neu starten.
2. Keine neuen Besucher öffnen.
3. L rufen, L ruft T.

Danach (T, rund 5 Minuten):
1. P prüft die Konsole des Sprachmodells: Ausgabenlimit, Guthaben, Störungsmeldung.
2. Hausmodelle einschalten, je Laborprofil: `uv run python wamo_admin.py modellzugang-setzen --user-id <uuid> --nur-haus an` im Ordner `voice-core` (die UUIDs zeigt `uv run python wamo_admin.py profile`). Das gilt ab der nächsten Sitzung. Die Qualität mit Hausmodellen ist nicht gemessen `[offen: 3 Läufe aus der Messrunde]`.
3. Oder Schlüsseltausch (9.6).
4. Nach dem Umschalten beendet der Besucher das Gespräch (Knopf "Beenden") und tippt neu auf "Gespräch starten". Der Durchlauf auf der Betreuer-Seite bleibt offen, das Board bleibt.
5. Geht beides nicht: Rückfall Aufzeichnung (Abschnitt 7).

### 6.2 Dienst ist aus

Du merkst es: Betreuer-Seite meldet "Keine Verbindung zum Dienst.", das Board lädt nicht, ein neuer Durchlauf geht nicht, Ticket-Aktionen scheitern mit einem festen Satz von WAMO.

Wirkung: Ohne Dienst nimmt der Kern keine neue Sitzung an (Absicht). Laufende Gespräche laufen weiter, schreibende Ticket-Aktionen scheitern.

Sofort (B):
1. Auf der Betreuer-Seite **Aktualisieren** klicken, danach die Seite mit F5 neu laden.
2. Keine neuen Besucher öffnen. Laufende Besucher ausklingen lassen.
3. L rufen, L ruft T.

Danach (T):
1. `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh status"` zeigt die Zustände (nur Namen, nie Werte).
2. `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh pruefen"` startet den Rauchtest mit 20 Proben.
3. Neustart des Containers `wamo-labor-dienst` `[offen: Befehl und Wirkung in der Rückwegübung bestätigen, steht nicht in den Unterlagen]`.
4. Ist der Dienst nach `[offen: Frist, L und T legen sie fest, zum Beispiel 10 Minuten]` nicht zurück: Rückfall Aufzeichnung (Abschnitt 7).

### 6.3 Spracherkennung (Soniox) fällt aus

Du merkst es: Der Besucher spricht, aber es wird nichts erkannt, die Untertitel bleiben leer, es kommt keine Stimme. Es gibt dafür keinen Ausweichweg im Code.

Sofort (B):
1. Der Besucher nutzt die Texteingabe der Oberfläche (das Eingabefeld, es wirkt wie gesprochen). Die Antworten erscheinen als Untertitel `[offen: Bezeichnung und Ort des Eingabefelds in der Generalprobe bestätigen]`.
2. L rufen, L ruft T.

Danach (T):
1. Prüfen, ob ein Platz oder alle betroffen sind. Ein Platz: 6.5.
2. Alle Plätze: Texteingabe weiterlaufen lassen oder Rückfall Aufzeichnung (Abschnitt 7).
3. Nur wenn vorab gemessen und das Gateway Audiomodelle anbietet: `uv run python wamo_admin.py modellzugang-setzen --user-id <uuid> --stt haus --tts haus` je Profil `[offen: Audiomodelle im Gateway prüfen, Messung]`.

### 6.4 Netz

Du merkst es: "Keine Verbindung", der Ton bricht ab, das Board lädt nicht, eine Zertifikatswarnung erscheint.

Sofort (B):
1. Ein Platz betroffen: Kabel prüfen (Kabel ist besser als WLAN), dann die Verknüpfung **WAMO Labor pruefen** doppelklicken und die Zeilen mit `[FEHLER]` lesen (Tabelle unten).
2. Bleibt es: Wechsel an den Reserveplatz (6.5).
3. Mehrere Plätze betroffen: L rufen. L ruft N und T.

| Zeile von `pruefen` | Bedeutung | Abhilfe |
| --- | --- | --- |
| Adresse (vier Zeilen) | Verbindung oder Zertifikat gescheitert | Kabel und WLAN prüfen, danach N rufen |
| Uhr | die Uhr des Laptops geht falsch (mehr als 5 Minuten) | Zeit abgleichen lassen (N) |
| Mikrofon, Warnung | aktives Bluetooth-Freisprechgerät | Headset mit USB oder Klinke nehmen |
| Zertifikat oder Richtlinien | Einrichtung fehlt | N rufen, das Platz-Skript `einrichten` muss erneut laufen |

Danach (T): Antworten die Ports 55443 bis 55447? Schleife in der Morgencheckliste, Teil B1. Rauchtest `wamo-labor-einrichten.sh pruefen`.

### 6.5 Platz-Laptop hängt

Du merkst es: Fenster eingefroren, kein Ton, Chrome reagiert nicht, Bildschirm schwarz.

Sofort (B):
1. Besucher beruhigen, 20 Sekunden warten.
2. Beide Chrome-Fenster schließen (Alt+F4 je Fenster). Reagiert Chrome nicht: Strg+Umschalt+Esc, "Google Chrome" beenden.
3. Auf der Betreuer-Seite **Platz-Token erzeugen** (3.6).
4. **WAMO Labor starten**, neuen Token einfügen.
5. Der Besucher tippt auf "Gespräch starten". Das Gespräch beginnt von vorn, das Board bleibt.
6. Hilft das nicht nach `[offen: Frist, zum Beispiel 3 Minuten, L legt sie fest]`: Wechsel an den Reserveplatz.
7. Zeigt WAMO die Anmeldung mit Passwort und Code, ist die Anmeldung des Geräts weg. L oder T meldet das Gerät neu an. Die Betreuer kennen das Passwort nicht und geben es nicht weiter (9.3).

Wechsel an den Reserveplatz 9:
1. Platz N: **Durchlauf beenden** und **Daten löschen** (3.4, 3.5).
2. T schaltet das Profil `labor-09` frei: `python scripts/wamo_labor.py aktivieren --kennung labor-09 --ausfuehren`. Es gilt nach etwa einer Minute.
3. Platz 9 wie in 3.1 öffnen. Der Besucher bestätigt den Hinweis noch einmal, denn die Einwilligung gehört zum Platz.
4. Platz N bekommt einen Zettel "Defekt". N rufen. Der Reserve-Laptop gehört zu Platz 9 `[offen: bestätigen]`.

### 6.6 Meldungen und ihre Bedeutung

| Wo | Meldung | Was tun |
| --- | --- | --- |
| Board | "Dieser Platz-Link gilt nicht mehr, bitte beim Betreuer melden" | Der Token ist ungültig. 3.6 |
| Betreuer-Seite | "Der Betreuer-Token gilt nicht." | Du bist abgemeldet. Betreuer-Token neu eingeben, sonst L rufen |
| Betreuer-Seite | "Keine Verbindung zum Dienst." | 6.2 |
| Betreuer-Seite | "Zu viele Anfragen, bitte kurz warten." | 30 Sekunden warten, nicht mehrfach klicken |
| Fenster "Neuer Durchlauf" | Fehlerhinweis, der Dienst kenne schon eine höhere Version | Seite mit F5 neu laden, von vorn beginnen |
| Betreuer-Seite | "Das Löschen ist noch nicht abgeschlossen, die Datei ist nicht bereinigt. Bitte wiederholen." | **Daten löschen** wiederholen, nach drei Versuchen T rufen |
| Freigabekarte | die Karte ist weg | Sie gilt etwa zwei Minuten. Der Besucher fragt WAMO neu |
| Platz | "Gespräch starten" tut nichts | Zeigt die Karte "Durchlauf offen"? Wenn nein: 3.1. Wenn ja: L rufen, T prüft, ob das Profil freigeschaltet ist |

## 7. Rückfall mit Aufzeichnung

### 7.1 Wann

L entscheidet. Typische Anlässe: Claude und Hausmodelle fallen aus, die Spracherkennung fällt aus, der Dienst ist nicht schnell zurück, der Kern ist nach einem Notaus gesperrt. Auch bei Wartezeiten kann die Aufzeichnung am Fernseher laufen.

### 7.2 Schaufenster, die Aufzeichnung am Fernseher

Die Aufzeichnung zeigt einen Entwicklungsauftrag, den WAMO vorab erledigt hat. Sie liegt vorbereitet bereit `[offen: Dateiname, Ort, Gerät, Länge]`.

1. L gibt das Zeichen "Rückfall Aufzeichnung".
2. Aufzeichnung auf dem großen Fernseher starten `[offen: Ort des Fernsehers]`.
3. Besucher an den Fernseher bitten. Die Plätze bleiben frei oder werden geschlossen (3.4).
4. Drei Sätze sagen: "Das ist eine Aufzeichnung, kein Live-Gespräch. WAMO bekam diesen Auftrag und hat ihn so erledigt. Beim Test später kannst du das selbst ausprobieren." `[offen: Erzähltext liefert das Projektteam]`
5. Fragen beantworten. Fachfragen gehen an F.
6. Dabei entsteht nichts Gespeichertes. Es braucht weder Durchlauf noch Haken noch Platz-Token `[offen: D bestätigt]`.

### 7.3 Vorführung ohne Besucher am Betreuer-Platz

Wann: Der Dienst und die Sprachmodelle laufen, aber Besucher sollen nicht selbst sprechen, oder jemand hat nicht zugestimmt (4.1).

1. Der Betreuer-Platz wird wie in 3.1 geöffnet. Der Betreuer ist dabei selbst die Person, die den Hinweis bestätigt `[offen: welcher Platz und welches Profil, zum Beispiel Reserve 9]`.
2. Der Betreuer spricht mit WAMO. Die Zuschauer sehen den Bildschirm und die Untertitel.
3. Die Zuschauer sprechen nicht in das Mikrofon. Raumgeräusche gehen an die Spracherkennung `[offen: D klärt, ob Zuschauer einen Hinweis brauchen]`.
4. Danach 3.4 und 3.5.

### 7.4 Zurück zum Live-Betrieb

1. T meldet: Ursache behoben, Rauchtest grün (`wamo-labor-einrichten.sh pruefen`).
2. L gibt frei. Jeder Platz startet neu mit 3.1.

## 8. Wer ruft wen

Namen und Telefonnummern fehlen noch. Niko trägt sie ein und druckt diese Seite für jeden Betreuer aus.

| Rolle | Name | Telefon oder Kanal |
| --- | --- | --- |
| L, Leitung der Betreuer vor Ort | `[Name offen]` | `[Telefon offen]` |
| T, Technik-Bereitschaft WAMO | `[Name offen]` | `[Telefon offen]` |
| N, Netz und Geräte (Kollegen) | `[Name offen]` | `[Telefon offen]` |
| D, Datenschutz-Ansprechperson | `[Name offen]` | `[Telefon offen]` |
| F, Trainer | `[Name offen]` | `[Telefon offen]` |
| P, Projektverantwortung WAMO | `[Name offen]` | `[Telefon offen]` |
| Raum und Standort des Labors, des Betreuer-Platzes und des Fernsehers | `[offen]` | |

| Anlass | Zuerst | Dann |
| --- | --- | --- |
| Frage eines Besuchers zu Karte, Aufgabe, Lernziel | F | |
| ein Laptop, Headset, Kabel oder WLAN am Platz | L | N |
| mehrere Plätze ohne Verbindung | L | N und T |
| WAMO antwortet nicht, keine Stimme, Board oder Betreuer-Seite meldet einen Fehler | L | T |
| Löschwunsch, Beschwerde, echte persönliche Daten, Zweifel am Hinweis | L | D, bei Löschung zusätzlich T |
| Notaus | L und T sofort | danach P und D |
| Guthaben, Limit, Konsole des Sprachmodells | T | P |

**Was du am Telefon sagst, in dieser Reihenfolge:**

1. Wer du bist: Rolle und Platz.
2. Seit wann es besteht: Uhrzeit.
3. Was du siehst: die Meldung wörtlich.
4. Was du schon getan hast.
5. Wie viele Plätze betroffen sind.

Nie am Telefon, per Chat oder Mail: Token, Passwörter, Namen von Besuchern.

## 9. Für die Technik-Bereitschaft (T)

Voraussetzungen: PC mit dem Repo, ssh-Alias `wamo-sokrates2`, `WAMO_ADMIN_DATABASE_URL` in der Umgebung, Python aus `voice-core/.venv`, Umschlag-Datei mit den Passwörtern unter `%USERPROFILE%\.wamo-labor\umschlag-labor.txt` (nie im Repo), am Server `sudo` ohne Passwort. Alle Befehle im Repo-Hauptordner, wenn nichts anderes steht. Ohne `--ausfuehren` zeigen die Skripte nur den Plan.

### 9.1 Stand lesen

`python scripts/wamo_labor.py status` zeigt je Laborprofil "freigeschaltet" oder "gesperrt" und die Zahl der gespeicherten Zeilen. Es liest nur.

### 9.2 Profile freischalten und sperren

1. Einzeln freischalten: `python scripts/wamo_labor.py aktivieren --kennung labor-01 labor-02 --ausfuehren`.
2. Einzeln sperren: `python scripts/wamo_labor.py sperren --kennung labor-03 --ausfuehren`.
3. Alle sperren: `python scripts/wamo_labor.py sperren --alle --ausfuehren`.
4. Der Kern merkt eine Änderung nach bis zu 60 Sekunden, laufende Sitzungen enden binnen etwa 2 Minuten.

### 9.3 Anmeldecode für ein Gerät

`python scripts/wamo_labor_faktoren.py codes --profil labor-03` gibt einen Code mit den Sekunden bis zum Ablauf aus (lokale Uhr, per NTP abgleichen). Das Passwort steht in der Umschlag-Datei. Beides gehört nur T und L, nie einem Besucher.

### 9.4 Gesprächstext eines Zeitfensters löschen (Löschwunsch)

1. Das Profil des Platzes sperren: `python scripts/wamo_labor.py sperren --kennung labor-03 --ausfuehren`. Der Durchlauf ist vorher beendet (3.4).
2. Plan ansehen, ohne `--ausfuehren`: `python scripts/wamo_labor.py loeschen --kennung labor-03 --ab 2026-11-28T09:00:00+01:00 --bis 2026-11-28T09:30:00+01:00`. Es zählt nur und ändert nichts.
3. Löschen: derselbe Befehl mit `--ausfuehren`. Das Skript wartet die Sperrfrist von 3 Minuten nach dem Sperren selbst ab und prüft danach nach.
4. Profil wieder freischalten (9.2), wenn der Platz weiterlaufen soll.

### 9.5 Server prüfen

| Was | Befehl |
| --- | --- |
| Kern-Slots | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh status"` |
| Labor-Dienst, Zustände | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh status"` |
| Labor-Dienst, Rauchtest mit 20 Proben | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh pruefen"` |
| Kern-Kanal der Entwicklungsaufträge (bleibt zu) | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh status"` |
| Labor-Daten, Prüfsummen und Urteil des Laders | `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-daten-einrichten.sh status"` |

### 9.6 Schlüsseltausch für das Sprachmodell

Fällt der Schlüssel des Servers aus (Limit, Guthaben), kann der Schlüssel eines Kollegen die Aufgabe übernehmen: die Zeile `ANTHROPIC_API_KEY` in `/etc/wamo/core.env` mit dem Hilfsprogramm `wamo-core-env` ändern (der Wert geht nur über stdin) und danach `wamo-core-slots.sh umschalten-kern <release>` ausführen. Es gibt kein neues Release. Der Ablauf ist nicht geprüft `[offen: genaue Befehlszeile und Dauer in der Rückwegübung am 27.11. festhalten]`.

## 10. Nach dem Labortag

Alle Daten der Besucher sind spätestens am **11.12.2026** zu löschen.

1. **Betreuer:** Am Ende an jedem benutzten Platz den Durchlauf beenden und die Daten löschen (3.4, 3.5). Beide Chrome-Fenster schließen.
2. **T, Nachlauf, am Abend des 28.11. oder am Montag 30.11.:**
   1. Trockenlauf: `python scripts/wamo_labor_nachlauf.py nachlauf`. Das Skript zeigt den Plan, zählt davor (nur lesen) und nennt die Erinnerungen. Es ändert nichts.
   2. Ausführen: `python scripts/wamo_labor_nachlauf.py nachlauf --ausfuehren --protokoll <Datei außerhalb des Repos>`. Es sperrt alle Laborprofile, beendet und leert die 16 Plätze im Dienst, löscht den Gesprächstext im Kern und zählt danach. Erwartet: "Ergebnis: Nachweis erbracht", Ausgang 0, Dauer bis zu etwa 10 Minuten.
   3. Bei einem Fehler nennt das Skript Schritt und Grund. Der Lauf ist wiederholbar.
3. **N oder T, je Laptop** (im Kiosk-Konto, Chrome vorher schließen): `python <Ordner>\wamo_labor_platz.py rueckweg --profil-loeschen`. Das nimmt Richtlinien und Zertifikat zurück und löscht das Chrome-Profil mit der angemeldeten Sitzung.
4. **T:** Die Umschlag-Datei und ihre Sicherungen (`umschlag-labor.txt.sicherung-...`) enthalten Passwörter und Seeds. Löschen, sobald die Profile nicht mehr gebraucht werden.
5. **T, ab dem 12.12.2026:** `ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-loeschlauf.sh --bestaetigung LABOR-LOESCHEN"`. Es löscht Container, Token, Image und Kern-Einstellungen und schreibt einen Nachweis ohne Inhalte. Das Skript lässt den Lauf erst ab dem 12.12.2026 zu. Daten von Besuchern hat der Nachlauf dann längst gelöscht.
6. **T:** Laborkonten löschen und alle Sitzungen widerrufen `[offen: Weg, es gibt dafür noch kein Skript]`.
7. **T:** Snapshots und Sicherungen des Servers dürfen den 11.12.2026 nicht überdauern. Von Hand prüfen.
8. **P:** Verbrauch in der Konsole des Sprachmodells notieren, Ausgabenlimit zurücksetzen.

## 11. Offene Angaben

Das muss Niko oder das Projektteam vor der Generalprobe am 26.11.2026 nennen:

1. Namen, Telefonnummern und der Kanal (Telefon oder Chat) aller Rollen aus Abschnitt 8.
2. Raum und Standort des Labors, des Betreuer-Platzes und des Fernsehers.
3. Öffnungszeiten, Pausen und die Dauer eines Besuchs.
4. Der Weg, auf dem die Betreuer den Betreuer-Token bekommen. Das Einrichtungsskript zeigt ihn nie an, das PC-Skript dafür steht als Folgeschritt aus.
5. Wortlaut des Datenschutzhinweises (Altersangabe ist entschieden: mindestens 16, jünger mit Aufsichtsperson; die verantwortliche Person prüft den Text).
6. Die Aufzeichnung: Dateiname, Ort, Gerät, Länge und der Erzähltext.
7. Die Kartenliste der 17 Karten als Ausdruck.
8. Aus der Generalprobe: Einfügen des Platz-Tokens in die verdeckte Eingabe, Nutzung des Besucher-Links im Kiosk, Bezeichnung und Ort des Eingabefelds für Text.
9. Reserve: ob der Reserve-Laptop Platz 9 ist und welche Profile am Labortag freigeschaltet sind.
10. Aus der Rückwegübung am 27.11.: Dauer der Stufen 3 und 4 des Notaus, Befehl zum Neustart des Dienstes, Wiederherstellen des Dienstes nach Stufe 3, Befehlszeile des Schlüsseltauschs.
11. Fristen, die L und T festlegen: Wartezeit bis zum Wechsel an den Reserveplatz, Frist für die Rückkehr des Dienstes.
12. Ob die Qualität mit Hausmodellen reicht und ob das Gateway Audiomodelle für Sprache anbietet.
13. Ob Zuschauer am Betreuer-Platz einen Hinweis brauchen (Datenschutz).
14. Der Weg, die Laborkonten zu löschen.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
