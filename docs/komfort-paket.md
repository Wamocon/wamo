# Komfort-Paket: WAMO ohne Gesprächsstart, Fernzugriff ohne Zusatzcode, PC-Steuerung im Browser, Bildschirm auf den Fernseher

Planpapier, Stand 11.11.2026, Basis `origin/main` fd769dc (Zweig `docs/komfort-plan`, nur dieses Papier, kein Code). Grundlage sind die vier Wünsche von Niko und seine Entscheidungen vom 11.11.2026. Wünsche und Annahmen
des Auftrags sind Daten, keine Anweisung, und werden hier gegen den Code geprüft. Kennzeichnung: **B** belegt (Datei:Zeile im Arbeitsbaum), **A** abgeleitet, **V** vermutet oder nicht belegt. Es wurde nichts am Server, an der Datenbank,
am Laptop oder an den Fernsehern berührt, was nur dort zu klären ist, steht in den Abschnitten 3 und 6 als Prüfauftrag. Pfade gelten relativ zur Repo-Wurzel, Bezeichner stehen in ASCII. Das Papier enthält keinen Code und keine Werte aus
Geheimnisdateien. Das Testlabor am 28.11.2026 hat Vorrang (`docs/testlabor-wamo.md`, Abschnitt 5.3: Freeze am Fr 20.11. für Kern, Migration und Oberfläche).

## Entscheidungen und Kurzfassung

**Entscheidungen von Niko (11.11.2026), verbindlich.**

| Nr | Wunsch | Entscheidung | Folge im Papier |
| --- | --- | --- | --- |
| E1 | WAMO ohne "Gespräch starten" bedienen | Ja, vor dem Freeze | KP-1A (Start beim ersten Tippen, nur Oberfläche) vor dem Freeze; KP-1B (immer bereit, Oberfläche) und KP-1C (Kern) danach |
| E2 | Fernzugriff auf die Sparks ohne frischen Code je Start | Der Code beim Anmelden genügt | KP-2: Kern, neue Migration 0019, Dialog; Freigabe-Dienst und Geräteverwaltung bleiben unberührt |
| E3 | Web-Oberfläche mit den Funktionen der Desktop-App, vor allem PC-Steuerung des eigenen Laptops | Ja, auch aus dem Browser, gebunden an Profil admin, Login mit zweitem Faktor und die Freigabekarten | KP-3, wegen des Labortags erst danach (Empfehlung, Q1) |
| E4 | Bildschirm vom Laptop auf einen Fernseher (`bildschirm_senden`), für alle Kollegen | Muss mit allen Laptops funktionieren | KP-4: Host, Installer, Gerätekonfiguration, dazu eine kleine Vorregistrierung im Kern |

**Kurzfassung.**

1. **Wunsch 1: Start beim ersten Tippen (Variante L) statt beim Öffnen (Variante P).** Das Eingabefeld ist aktiv, der erste Tastendruck startet still eine Sitzung (ohne Mikrofon, Antworten als Text), die Zeile wird gehalten und gesendet, sobald WAMO bereit ist; nach 20 Minuten
   Ruhe trennt die Oberfläche wieder. Das ist rein Oberfläche (KP-1A, 12 Stunden), braucht keinen Kern-Release und passt vor den Freeze. Es hängt an einem Geräteschalter, Standard aus, deshalb nie auf den Laborgeräten. Nikos Vorschlag "beim Öffnen still starten" (P) bleibt als
   Schalter (KP-1B, 8 Stunden) und als Kernpaket (KP-1C, 10 Stunden) für nach dem Labortag. **Wichtiger Befund gegen den Vorschlag:** Eine dauernd offene stille Sitzung zählt für die Ruheprüfung von `wamo-core-slots.sh` als laufende Sitzung und hält den alten Slot
   am Leben (3.1). Der Preis von L: Die erste Zeile wartet 2 bis 4 Sekunden (A, am Server zu messen).
2. **Wunsch 2 ist klein und sicher machbar**, aber er berührt eine Migration und braucht deshalb einen Kern-Release vor dem Freeze (13 Stunden). Die Datenbankfunktion `rd_sitzung_beginnen` prüft heute zweimal gegen den Wunsch: Der Code darf höchstens
   120 Sekunden alt sein, und derselbe Code gilt für genau einen Start. Beides muss in Migration 0019 fallen, sonst schlägt schon der zweite Start nach einer Anmeldung fehl. Das Höchstalter wird als Stufenschalter `WAMO_RD_ANMELDUNG_MAX_MIN`
   (Standard 0 = aus) gebaut: Ist die Anmeldung älter, verlangt der Start wieder einen frischen Code.
3. **Wunsch 3: Der Kern hindert den Browser heute nicht.** Die Sperre "nur in der Desktop-App" ist eine reine Entscheidung der Oberfläche (`ui/src/lib/geraet.ts:17`, `infra/sokrates2/README.md:757`). Der Kern ordnet den Laptop allein über
   das Profil und das Geräteregister zu (`voice-core/pc_geraet.py:192`), und Tunnel und Host laufen als geplante Aufgabe "WAMO Tunnel" ohne die App. Der Auftrag nennt `WAMO_PC_HOST_PROFILE`, aber im Betrieb gilt `WAMO_PC_REGISTER=an`
   (`infra/sokrates2/wamo-core@.service:25`), dann ist dieser Weg tot. Der Aufwand liegt deshalb bei den Schutzmaßnahmen (Ablauf, Sichtbarkeit am Laptop), nicht bei der Technik: 27 Stunden.
4. **Wunsch 4: Der Kern übernimmt Host-Werkzeuge nicht dynamisch.** `LLM_TOOLS` (`voice-core/pc_host.py:61`) und die Freigabelisten (`voice-core/activity.py:48` und `:50`) sind feste Erlaubnislisten. Ein neues Werkzeug braucht dort Einträge.
   Sie lassen sich folgenlos vorab eintragen, bevor der Host das Werkzeug hat. Zweiter Befund: Mitglieder bekommen heute gar keine PC-Werkzeuge (`pc_geraet.py:194`), "für alle Kollegen" heißt also eine eigene, enge Werkzeugliste für Mitglieder.
   Empfohlen ist Miracast über die vorhandene UI-Automation des Hosts, mit Plan B MJPEG-Seite. Entscheidend ist ein Test von vier Stunden mit Niko an beiden Fernsehern, bevor gebaut wird. Aufwand 30 Stunden plus 10 für Mitglieder.
5. **Vor dem Freeze passen 27 Stunden in einen zusätzlichen Strang** (KP-0, KP-1A, KP-2; 29 mit KP-4B). Das Labor hat dafür keine freie Kapazität (Laborpapier: 131 Stunden gegen 81 bis 108). Ohne eigenen Umsetzer entfällt zuerst KP-4B, dann KP-2, zuletzt KP-1A;
   gibt es bis Do 12.11. gar keinen, ist alles nach dem Labortag die sichere Rückfallebene. KP-3 und der Rest von KP-4 beginnen nach dem Labortag, Gesamtaufwand 112 Stunden ohne Optionen.

**IQ:** Jede Erleichterung wird dort begrenzt, wo man sie nicht umgehen kann (Kern, Datenbank, Host), nicht in der Oberfläche. **EQ:** Niko bekommt, was er will, ohne dass ein Besucher am Labortag oder ein Dritter im Büro etwas davon merkt oder
nutzen kann; jede neue Fähigkeit ist sichtbar, befristet und abschaltbar. **VQ:** Tabellen mit immer gleichen Spalten, ein Kalender mit Strängen. **Synthese:** Erst die Oberfläche (reversibel in einer Sekunde), dann der kleine Kern-Release mit
Migration, alles mit Schutzwirkung und Ablauf nach dem Labortag.

## 1. Ziel und Abgrenzung

**Ziel.** Niko öffnet WAMO und tippt sofort. Er startet eine Fernsitzung zu einem Spark mit Ziel und Klick. Er bedient seinen Laptop aus dem Browser im Büro. Er sagt "zeig das auf dem Fernseher", und der Bildschirm eines Laptops erscheint
dort, bei jedem Kollegen mit seinem Laptop. Nichts davon darf das Labor am 28.11. stören, nichts schwächt ein Schutzmittel, das nicht ausdrücklich entschieden wurde.

**Erfolgsmaße.** (1) Ein Tastendruck genügt: WAMO wird ohne Klick bereit und beantwortet die erste Zeile nach höchstens 4 Sekunden Wartezeit (Messung, 3.1); ohne Eingabe gibt es weder Sprachkosten noch Mikrofonzugriff noch eine offene Sitzung. (2) Ein Admin startet eine
Fernsitzung ohne Codefeld, solange seine Anmeldung gültig ist, und der zweite Start nach derselben Anmeldung klappt (Test gegen die neue Datenbankfunktion). (3) Die PC-Steuerung im Browser ist nie ohne Schalter an, läuft höchstens 30 Minuten
und ist am Laptop sichtbar. (4) `bildschirm_senden` bringt das Bild in mindestens 9 von 10 Versuchen je Laptop und Fernseher auf den Schirm und ist mit "Bildschirm nicht mehr senden" in 10 Sekunden beendet.

**Nicht-Ziele.** Änderungen am frischen Code der Release-Freigabe (`infra/sokrates2/freigabe/`) und der Geräteverwaltung (`voice-core/geraete_admin_api.py`), am Fernanfrage-Code des Live-Bilds (`voice-core/bild_fern.py`), an den Laborprofilen und
ihrer Sperre, an `wamo-core-slots.sh`, PC-Werkzeuge für Mitglieder über die bisherigen Wege, Streaming mit Ton, ein Updater für die Desktop-App.

**Abgrenzung zum Labor.** Das Labor hat Vorrang. Nach dem Freeze am Fr 20.11. gilt für Kern, Migration und Oberfläche: nur Daten und Konfiguration. Vor dem Freeze sind Änderungen erlaubt, wenn sie getestet, geprüft und ausgerollt sind und die
Laborprofile nicht stören (Laborpapier 5.3). Host, Installer und Gerätedienst fallen nicht unter den Freeze, brauchen aber für Wunsch 4 eine Erlaubnisliste im Kern (2.4).

## 2. Ist-Stand mit Belegen und Prüfung der Annahmen

### 2.1 Wunsch 1: Sitzung, Text und Werkzeuge hängen an "Gespräch starten"

| Befund | Beleg | Kennzeichen |
| --- | --- | --- |
| Die Oberfläche startet eine Sitzung nur im Klick auf "Gespräch starten": `Aktionen` ruft `aktionen.connect()` | `ui/src/components/WamoAnsicht.tsx:103`, `ui/src/lib/useWamo.ts:651` | B |
| Der Client wird mit `enableMic: true` gebaut, das Mikrofon öffnet sich beim Verbinden (WavMediaManager, `getUserMedia`) | `useWamo.ts:720` bis `:726` | B |
| Texteingabe geht nur mit laufender Sitzung (`textMoeglich`) und fordert Sprachantwort an (`audio_response: true`); Pipecat kennt `audio_response=false` (kein TTS) | `useWamo.ts:1147` bis `:1165`; `pipecat/processors/frameworks/rtvi/processor.py:482` | B |
| Der Kern spricht bei jedem Verbindungsaufbau eine Begrüßung, wärmt den Prompt-Cache mit einem Claude-Aufruf und legt eine Sitzungszeile an | `voice-core/bot.py:1191` bis `:1200` | B |
| Der Kern beendet jede Sitzung nach `WAMO_MAX_SITZUNG_MIN` (Standard 120) mit "Die maximale Sitzungsdauer ist erreicht." und prüft das Profil alle 60 Sekunden | `bot.py:235`, `:423` bis `:477`, `:234` | B |
| Je Profil gilt eine Sitzung, die neue löst die alte ab (`WAMO_SITZUNG_EINZELN`, Standard an); die alte bekommt `sitzung_beendet` mit festem Text und verliert die PC-Sperre | `voice-core/sitzungen.py:40`, `:99` bis `:118`, `:32` | B |
| Der Audio-Wächter meldet nach 20 Sekunden ohne Audiopaket "Mikrofon fehlt"; die Oberfläche zeigt die Meldung nur bei `micOn` | `voice-core/audio_monitor.py:39`, `useWamo.ts:856` bis `:862` | B |
| Der Server liest Audio weiter, auch bei ausgeschaltetem Mikrofon (die Spur sendet Stille), und gibt es an Soniox; Pipecat kann das über `STTMuteFrame` unterbinden | `pipecat/transports/smallwebrtc/connection.py:149`, `pipecat/services/stt_service.py:438`, `:508` | B (Verhalten der Spur im Browser: A) |
| Die PC-Sperre wird nur genommen, wenn die PC-Steuerung eingeschaltet wird, nicht beim Sitzungsbeginn | `bot.py:1086` bis `:1094`, `voice-core/pc_host.py:162` | B |
| Ruheprüfung für Release und `ausleiten`: Journal der Unit seit 15 Minuten still und keine Sitzungsdatei der Brücke; "eine laufende Sitzung schreibt laufend" | `infra/sokrates2/wamo-core-slots.sh:203` bis `:208`, `:265` bis `:270`, `:420` | B |
| Neuladen warnt nur bei laufendem Entwicklungsauftrag, Aufträge enden mit der Sitzung | `ui/src/lib/useNeuladenWarnung.ts:14` | B |
| Der Browser meldet sich nach 24 Stunden ab (Komfortgrenze), die Desktop-App nach 7 Tagen, der Kern setzt 168 Stunden | `ui/src/lib/anmeldung.ts:5`, `voice-core/auth_gate.py:78` | B |

**Prüfung der Annahmen des Auftrags.** (1) "Die Sitzung wird vor `WAMO_MAX_SITZUNG_MIN` unmerklich erneuert": Der Kontext des Gesprächs geht dabei verloren, das Gedächtnis bleibt. Unmerklich geht es nur im Leerlauf (3.1). (2) "Labor-Sitzungen bleiben
vom Betreuer gestartet": Die Oberfläche kennt das Laborprofil vor dem Verbinden nicht (`profil` kommt erst vom Kern, `ui/src/lib/wamo.ts:480`). Ein automatischer Start muss deshalb auf dem Gerät opt-in sein (Standard aus), die Ablehnung im Kern kommt mit KP-1C.
(3) "Nur das aktive Fenster hält die Sitzung": Browser und App lassen sich nur über das Ablösen im Kern abstimmen, nicht untereinander; das braucht eine Regel gegen Hin und Her (3.1). (4) "Kosten im Leerlauf (Soniox, Claude)": Ohne
Audiospur entstehen keine Sprachkosten, je Start aber eine Begrüßung (TTS) und ein Claude-Aufruf zum Cache-Wärmen; die Höhe ist am Server zu messen (V).

### 2.2 Wunsch 2: Der frische Code steckt an drei Stellen, dazu kommt eine zweite Regel in der Datenbank

| Stelle | Heute | Beleg |
| --- | --- | --- |
| Kern | `start` verlangt `aal2_frisch(zeit)`: Code höchstens 120 Sekunden alt (`AAL2_FRISCH_SECS`), sonst 403 mit Code `zweiter_faktor_zu_alt` | `voice-core/rd_api.py:417` bis `:420`, `:342` bis `:345`, `voice-core/geraete_admin_api.py:47`, `:92` bis `:97` |
| Kern an Datenbank | Der Kern reicht die Zeit des Codes als `p_aal2_zeit` an `rd_sitzung_beginnen` | `rd_api.py:431` bis `:436` |
| Datenbank | `rd_sitzung_beginnen` lehnt ab, wenn die Zeit älter als 120 Sekunden ist (23514) **und** wenn dasselbe Profil schon mit gleichem oder jüngerem Code gestartet hat ("ein Code, ein Start") | `supabase/migrations/0015_rd_sitzungen.sql:113` bis `:118`, `:127` bis `:132` |
| Oberfläche | Der Dialog verlangt den Code, bestätigt ihn bei Supabase (`codeBestaetigen`) und startet erst dann; `startFehler` kennt `zweiter_faktor_zu_alt` bereits und hält den Dialog mit Codefeld offen | `ui/src/components/einstellungen/Fernzugriff.tsx:249` bis `:289`, `:340`, `ui/src/lib/fernzugriff.ts:187` bis `:191`, `:206` |
| Texte und Tests | Persona nennt den Code, README und Remote-Desktop-Papier ebenfalls; Tests prüfen 121 und 600 Sekunden | `voice-core/persona.py:222` bis `:229`, `README.md:1252`, `:1276`, `docs/remote-desktop.md:23`, `:157`, `voice-core/test_rd_api.py:371` bis `:390`, `supabase/tests/0015_rd_sitzungen_test.sql:114` |

**Prüfung der Annahmen.** (1) "Kleinste Änderung über alle drei Stellen": Es sind vier, weil die Datenbankfunktion zwei Regeln hat. Mit nur dem Wegfall der 120 Sekunden scheitert der zweite Start nach derselben Anmeldung an der Regel "ein
Code, ein Start", denn die Zeit des Codes bleibt bis zum Ende der Anmeldung gleich. (2) "Freigabe-Dienst und Geräteverwaltung unberührt": Ja, `aal2_frisch` und `aal2_zeit` bleiben unverändert in `geraete_admin_api.py`, `rd_api.py` importiert sie weiter
(`rd_api.py:63`). Die Änderung liegt nur in `rd_api.py` und der Funktion `rd_sitzung_beginnen`. (3) "Der Code beim Anmelden genügt": Das Token trägt den Zeitpunkt des Codes im Claim `amr`, die Zugangskontrolle verlangt aal2 für jede Route
(`voice-core/auth_gate.py:22` bis `:27`); das Verhalten von GoTrue beim Erneuern (der Zeitstempel bleibt) ist im Code als Annahme dokumentiert (V, am Server prüfbar).

### 2.3 Wunsch 3: Was die Desktop-App kann und der Browser nicht

Befund vorab: Der Kern unterscheidet nicht zwischen App und Browser. `pc_zugang` hängt nur an Profil, Rolle und Register (`voice-core/pc_host.py:122`, `voice-core/pc_geraet.py:192` bis `:214`), der Schalter `pc_steuerung` wird von jeder Admin-Sitzung
angenommen (`bot.py:1086` bis `:1098`). Die Oberfläche blendet den Schalter im Browser aus (`ui/src/components/einstellungen/Geraete.tsx:43` bis `:47`, `ui/src/lib/einstellungen.ts:21`, `ui/src/lib/geraet.ts:34`), und das ist nach eigener
Aussage "kein Schutz" (`geraet.ts:17`, `infra/sokrates2/README.md:757` bis `:759`, dort steht auch "Eine Erzwingung im Kern ist möglich und offen").

| Funktion | Desktop-App | Browser heute | Browser nach der Änderung (KP-3) | Nur mit lokalem Fenster nicht möglich |
| --- | --- | --- | --- | --- |
| Gespräch, Text, Canvas, Karten, Vorgänge, Einstellungen, Fernzugriff, Büro-Karte, Bürogeräte | ja | ja | ja, unverändert | |
| PC-Werkzeuge am eigenen Laptop (Fenster, Tasten, Klicks, Browser lesen, GitHub) über Host und Rücktunnel | ja | Schalter ausgeblendet, Kern würde annehmen | ja, mit Schalter, Ablauf und Anzeige | |
| Autostart der PC-Steuerung | ja, nur wenn Rechnername zum Gerät passt (`useWamo.ts:1222`) | nein | nein, bewusst | braucht `rechnername` aus der App |
| Laptop einrichten (Geräteschlüssel, Bindung, Tunnelstart, Datei `geraet.json`) | ja (`desktop/geraet.js`, `preload.js:63` bis `:68`) | nein | nein | privater Schlüssel, Dateien, Aufgabe "WAMO Tunnel" |
| Tunnel und Host laufen ohne App | ja, geplante Aufgabe bei Anmeldung und alle 5 Minuten (`scripts/wamo-tunnel.ps1`, Kopf) | gleich, unabhängig vom Fenster | gleich | |
| Stimmen-Rand (`overlay.html`), Welle am Klickpunkt | ja | nein | nein | durchsichtige Fenster auf allen Monitoren |
| Lichtrahmen ums Zielfenster (Markierung) | ja, nur auf dem Laptop, der handelt (`lichtrahmen.js`) | nein | nein, Ersatz über 3.3 (Anzeige am Laptop) | Zeichnen auf dem Laptop |
| Live-Bild ansehen | ja | ja, schon heute (`BildschirmAnsicht.tsx`) | ja | |
| Live-Bild einschalten | Tray, Einwilligung am Laptop | Fernstart mit frischem Code vom Handy (`BildschirmAnsicht.tsx:56`) | unverändert, Code bleibt (Q4) | Tray und Einwilligungsdialog |
| Tray, "Mit Windows starten", Weiterlaufen bei geschlossenem Fenster, Einzelinstanz | ja | nein | nein | Prozess der App |
| Betriebsart Büro oder Unterwegs | ja (Installer nur Büro) | nur Büro (`NUR_BUERO_BUILD`) | unverändert | lokaler Kern |
| Vertrauen in die WAMO-CA | ja, nur in der App (`desktop/zertifikat.js`) | CA im Betriebssystem nötig | unverändert | |
| Mikrofon, Windows-Datenschutzstatus, ohne Hintergrunddrosselung | ja (`mikrofon:status`) | Browserdialog, Chrome drosselt Hintergrundtabs | unverändert | Drosselung lässt sich im Browser nicht abstellen |
| Anmeldedauer | 7 Tage | 24 Stunden | unverändert | |

**Prüfung der Annahmen.** (1) "Der Kern kann nur den in `WAMO_PC_HOST_PROFILE` gebundenen Laptop steuern": falsch für den Betrieb. Dieser Weg (`pc_host_fuer`, `pc_host.py:129` bis `:147`) liefert bei `WAMO_PC_REGISTER=an` immer False; gültig ist das Register
mit einem aktiven persönlichen Gerät je Profil, Sperre je Gerät, Tunnel je Laptop (`pc_geraet.py:1` bis `:20`, `infra/sokrates2/README.md:901` bis `:911`). (2) "Tunnel und Host laufen unabhängig von der App": ja nach der Bindung; nur
die Einrichtung braucht die App. Heute kann also jede Admin-Sitzung, auch vom Handy mit gestohlenem Token, den Laptop steuern, solange er läuft; der Browser erweitert nur die Bedienung, nicht die Reichweite des Kerns. (3) Für Kollegen: Mitglieder
bekommen keine PC-Werkzeuge, solange `WAMO_PC_MITGLIEDER` aus ist (Standard, `pc_geraet.py:61`, `:194`); `CLAUDE.md` nennt PC-Werkzeuge vorerst nur für admin. Wunsch 3 gilt damit für Admins, ob Kollegen als Admin geführt werden, ist offen (Q6).
(4) Der Host bedient die eigene Oberfläche nie, auch nicht den Browsertab mit Titel "WAMO" (`host/windows_control.py:51` bis `:79`); die Freigabekarten bleiben damit im Browser auf dem Laptop selbst unerreichbar für WAMO (B).

### 2.4 Wunsch 4: Kein Werkzeug, aber eine feste Erlaubnisliste im Kern

| Befund | Beleg | Kennzeichen |
| --- | --- | --- |
| Der Fernseher-Treiber kann nur an, aus, Lautstärke, stumm, Eingang; LG über SSAP, Samsung über Fernbedienungs-Schnittstelle und UPnP | `geraete/wamo_geraete/treiber/lg_tv.py:30` bis `:40`, `treiber/fernseher.py:1` bis `:15`, `geraete/wamo_geraete/dienst.py:43` bis `:47` | B |
| Beide Fernseher stehen in `geraete.toml` (Kennungen `fernseher` und `fernseher-2`, Treiber `lg-webos` und `samsung-tv`); unbekannte Schlüssel verhindern den Start, ein neues Feld `miracast_name` braucht zuerst Dienstcode | `infra/sokrates2/geraete.toml.example`, `geraete/wamo_geraete/konfig.py:56` bis `:59`, `:167` | B |
| Der Kern baut die Aktionsliste von `buerogeraet_steuern` aus der Antwort des Gerätedienstes: Eine neue Aktion des Dienstes erscheint ohne Kernänderung | `voice-core/geraete_werkzeuge.py:188` bis `:194`, `:266` bis `:271`; `dienst.py:205` bis `:211` | B |
| Host-Werkzeuge erreichen das Sprachmodell nur, wenn sie in `LLM_TOOLS` stehen; eine Freigabekarte entsteht nur für Werkzeuge in `FREIGABE_WERKZEUGE` und Arten in `FREIGABE_AKTIONEN` | `voice-core/pc_host.py:61` bis `:66`, `voice-core/activity.py:48` bis `:52`, `:84` bis `:86` | B |
| Die Oberfläche zeigt jede Art von Freigabekarte, sie listet keine Arten auf | `ui/src/lib/nachrichten.ts` (keine Aktionsliste für Freigaben), `Freigaben.tsx` | B |
| Der Kern kann ein Host-Werkzeug auch ohne Filter aufrufen (`call_host_tool`), das Sprachmodell aber nicht | `pc_host.py:329` bis `:339` | B |
| Mitglieder: Ohne `WAMO_PC_MITGLIEDER` gibt es für sie keinen Host-Zugang | `voice-core/pc_geraet.py:194` | B |
| Der Installer enthält nur `host/**/*.py` (keine `.toml`), keine neue Abhängigkeit ohne Änderung von `host/pyproject.toml` und `uv.lock` | `desktop/package.json:48` bis `:61`, `host/pyproject.toml:6` bis `:14` | B |
| Der Host hat UI-Automation (`comtypes`), `mss` und Pillow, nichts für WinRT, kein ffmpeg | `host/uia_control.py:1` bis `:20`, `host/pyproject.toml` | B |
| Laptop (Intel AX211) unterstützt drahtlose Anzeige laut `netsh`; Fernseher im WLAN, Segmentierung nicht bekannt | Angabe von Niko im Auftrag | V |

**Prüfung der Annahmen.** (1) "Er ändert nur Host, Installer und Gerätekonfiguration, nicht den Kern": nicht ganz. Ohne Einträge in `LLM_TOOLS`, `FREIGABE_WERKZEUGE` und `FREIGABE_AKTIONEN` sieht WAMO das Werkzeug nicht und zeigt keine Karte. Die Einträge
sind drei Zeilen und mit Tests folgenlos vorab machbar (KP-4B). (2) "Der Kern reicht das Ziel durch, `miracast_name` in `geraete.toml`": möglich, aber der Host darf den Namen nicht aus dem Gespräch übernehmen, sonst kann ein eingeschleuster Text den
Bildschirm auf ein fremdes Gerät in Reichweite schicken. Empfohlen ist eine feste Liste im Host (Kennung zu Anzeigename) als einzige Quelle für den Host, `miracast_name` in `geraete.toml` als Prüfwert (3.4, Q5). (3) "Für alle Kollegen": Kollegen
sind in der Regel Mitglieder (V, ihre Rollen sind nicht geprüft) und damit ohne Host-Zugang; es braucht eine enge Werkzeugliste für Mitglieder (KP-4F). (4) "Miracast per dokumentierter API": Die WinRT-Projektion (`ProjectionManager`) braucht eine App-Ansicht und eine neue Abhängigkeit (V), `DisplaySwitch.exe`
schaltet nur den Anzeigemodus, nicht die Verbindung. Der gangbare Weg für den Host ist die Verbindungsleiste per UI-Automation (V, nur mit Test am Gerät zu klären).

## 3. Entwurf je Wunsch

### 3.1 Wunsch 1: WAMO ohne "Gespräch starten" (KP-1A 12 h, KP-1B 8 h, KP-1C 10 h)

**Kernentscheidung: Start beim ersten Tippen (Variante L), nicht beim Öffnen (Variante P).** Niko schlug vor, beim Öffnen still eine Sitzung zu starten (P). Der Vergleich gegen den Code fällt für L aus:

| Kriterium | L: Start beim ersten Tippen | P: still beim Öffnen und halten |
| --- | --- | --- |
| Wartezeit der ersten Zeile | Verbindungsaufbau plus Cache-Wärmen, geschätzt 2 bis 4 Sekunden (A, am Server zu messen), die Zeile wird in der Zwischenzeit gehalten und gesendet | keine |
| Kosten im Leerlauf | keine (ohne Eingabe gibt es keine Sitzung) | je Start Begrüßung (Soniox TTS) und ein Claude-Aufruf zum Cache-Wärmen, je Erneuerung wieder; Höhe unbekannt (V) |
| Ruheprüfung und `ausleiten` | Sitzung endet 20 Minuten nach der letzten Eingabe, die Ruhe bleibt erreichbar | die Sitzung läuft dauernd und hält den alten Slot, `umstellen` und `ausleiten` scheitern an der Ruhe (`wamo-core-slots.sh:203` bis `:208`, `:420`) |
| 120-Minuten-Grenze, Erneuerung, Kontextverlust | entfällt | Erneuerung nur im Leerlauf, sonst Kontextverlust (`bot.py:235`, `:460`) |
| Hin und Her zwischen App und Browser | tritt nur beim Tippen auf, die neue Sitzung gewinnt wie bisher | Regeln für Fokus nötig, sonst lösen sich zwei Fenster gegenseitig ab (`sitzungen.py:99` bis `:118`) |
| Labor | Geräteschalter "Beim Tippen verbinden", Standard aus, Laborgeräte kennen ihn nicht | Geräteschalter "Beim Öffnen bereit sein", Standard aus; beide: erst KP-1C lehnt Laborprofile im Kern ab, danach darf der Standard auf an |
| Aufwand Oberfläche | 12 Stunden | 8 Stunden zusätzlich |

**Empfehlung:** KP-1A (L) vor dem Freeze. P (KP-1B) bleibt als Schalter "Immer bereit" ebenfalls nur Oberfläche, wird aber erst nach dem Labortag und nach der Messung der Leerlaufkosten freigegeben. Ob die Wartezeit von 2 bis 4 Sekunden für
Niko reicht, ist Q2.

**Verhalten KP-1A.** (1) Auf Geräten mit dem Schalter "Beim Tippen verbinden" (`wamo.startBeimTippen`, Standard aus) ist das Eingabefeld auch ohne Sitzung aktiv. Die erste Taste (der Fokus zählt erst nach einer Sekunde) startet still `connect({ still: true })`: Client ohne Mikrofon (`enableMic: false`, kein `getUserMedia`, keine Berechtigungsfrage, kein Aufnahmepunkt im
Tab), `micOn: false` im Store, Wiedergabe stumm (`audioRef.muted`), Antworten als Text (`audio_response: false`). (2) Die erste Zeile wird gehalten und nach `onBotReady` gesendet (Textfeld zeigt "Verbinde, deine Zeile wird gleich gesendet");
Verbindungsfehler lassen die Zeile stehen. (3) "Sprechen" (Mikrofonknopf) öffnet das Mikrofon in der laufenden Sitzung (`initDevices`, `sendeSpurErsetzen`, vorhanden in `ui/src/lib/mikrofon.ts:210`) und schaltet Wiedergabe und `audio_response`
ein; ohne Klick bleibt WAMO stumm. (4) Nach 20 Minuten ohne Eingabe trennt die Oberfläche still, sofern keine Antwort läuft und weder Karte, Auftrag, Vorgang noch Live-Bild offen sind; eine eingeschaltete PC-Steuerung allein hält die Sitzung nicht. (5) "Gespräch starten" bleibt wie heute und
beginnt mit Mikrofon und Stimme. (6) `sitzung_beendet` mit dem Übernahmetext (`sitzungen.py:32`) startet nie von selbst neu, die Meldung steht wie bisher.
**KP-1B (P):** Schalter "Beim Öffnen bereit sein" je Gerät (`localStorage`, Standard aus): stiller Start bei sichtbarem und fokussiertem Fenster (Verzögerung 1 Sekunde), Trennen nach 5 Minuten ohne Fokus oder 2 Minuten verborgen,
Erneuerung ab 100 Minuten Alter nur im Leerlauf (60 Sekunden), reaktiv bei "Die maximale Sitzungsdauer ist erreicht.", Wiederholung mit Pause 2, 10, 30, 60 Sekunden und höchstens 6 Starts je 10 Minuten.
**KP-1C (Kern, nach dem Labortag):** `/start` trägt `modus: still` (nur "still" oder "normal" gelten, alles andere wird verworfen; ob der Schlüssel den Weg durch `voice-core/auth_gate.py` bis `runner_args.body` übersteht, ist zu prüfen, V); stiller Modus: keine Begrüßung, Cache-Wärmen erst bei der
ersten Eingabe (`bot.py:1195` bis `:1198`), Sitzungszeile erst beim ersten Beitrag (`:1200`), `STTMuteFrame` bis "Sprechen", Audio-Wächter aus, `sitzung_beendet` mit Feld `code`, **Laborprofile werden mit festem Code abgelehnt**, und ein ablaufender Slot
beendet stille ruhende Sitzungen mit `erneuern: true`, damit sie auf den neuen Slot wechseln.

**Wirkung auf PC-Steuerung und Sperre.** Eine stille Sitzung nimmt die PC-Sperre nicht (`bot.py:1086` bis `:1094`). Der Autostart der PC-Steuerung der Desktop-App (`useWamo.ts:1208` bis `:1236`) läuft nach dem stillen Start wie bisher, sobald der
Host verbunden ist; mit dem Trennen nach 20 Minuten Leerlauf fällt auch die Sperre. Eine Sitzung des Browsers löst die der App ab und verliert dabei nichts, was nicht schon heute verloren geht (`sitzungen.py:109` bis `:115`).

**Sicherheitsmodell.**

| Bedrohung | Gegenmaßnahme |
| --- | --- |
| Laborgerät startet von selbst eine Sitzung | Beide Verfahren hängen an einem Geräteschalter, Standard aus; Platz-Skript und Chrome-Richtlinien der Laborgeräte setzen ihn nicht. Test: Laborstart nur über den Knopf. Erst KP-1C lehnt Laborprofile im Kern ab, und erst danach darf der Standard auf an |
| Dauernd offene Sitzung hält einen Besitzer ohne Nutzer | L: Trennen nach 20 Minuten. P: Fokusregeln, Höchstalter 120 Minuten, Kern beendet weiter selbst (`SessionGuard`) |
| Mikrofon ohne Wissen | Kein `getUserMedia` bis zum Klick, Anzeige "Mikrofon aus" in Schrift und Symbol, Test mit gefälschtem Gerät: kein Aufruf vor dem Klick |
| Zeile geht an die falsche, ältere Sitzung | Die Zeile wird erst nach `onBotReady` der neuen Verbindung gesendet, `gen`-Zähler wie in `textSenden` (`useWamo.ts:1148`) |
| Schleife bei Fehlern | höchstens 6 Starts je 10 Minuten, Pause wächst, Hinweis statt Dauerfeuer |

**Fehlerfälle.** Kern nicht erreichbar (Büro ohne WireGuard): `verbindungsfrist` (20 Sekunden, `useWamo.ts:459`) meldet es, die Zeile bleibt im Feld. Anmeldung zu alt: wie heute `neuAnmelden`. 401 und 409 laufen über den vorhandenen zweiten Versuch
(`useWamo.ts:973` bis `:983`). Neuladen: kein Warnhinweis, denn die Warnung gilt nur bei Aufträgen (`useNeuladenWarnung.ts:14`); eine laufende Antwort geht verloren, der Text der Zeile nicht. Verbindungsabbruch einer stillen Sitzung: keine rote
Fehlerzeile, nur "Nicht verbunden" und beim nächsten Tippen neuer Start.

**Barrierefreiheit.** Der Fokus wird nur gesetzt, wenn nichts anderes den Fokus hat (kein Fokusraub). Statuswechsel "Verbinde", "Bereit, Mikrofon aus", "Gespräch beendet" laufen einmal über die vorhandene `role="status"`-Zeile (`WamoAnsicht.tsx:126`
bis `:133`), keine Wiederholung bei jedem Trennen. Zustände stehen in Schrift, nicht nur als Farbe. `prefers-reduced-motion` gilt wie bisher. Tastatur: Feld, Mikrofonknopf und "Gespräch starten" sind in der Tab-Reihenfolge, Enter sendet.

**Tests (Rot zuerst).** `ui/src/lib/bereit.test.ts` (neu): Zustandsautomat mit Uhr und Attrappe (Start nur beim Tippen, Zeile gehalten, Leerlauf 20 Minuten, nie trennen bei Karte, Auftrag, PC-Steuerung, Live-Bild, kein Neustart nach Übernahmetext, Ratenbegrenzung,
Abbruch während des Verbindens). Komponententest für Feld und Status. Browsertest mit gefälschtem Gerät (Playwright): vor "Sprechen" kein `getUserMedia`, danach Spur auf der Verbindung (Prüfauftrag 4 Stunden, Ergebnis vor dem Bau). Kerntest `voice-core/test_bot_still.py` (nur KP-1C).
Vertragstest, dass die Texte des Kerns (`GRUND_UEBERNOMMEN`, Höchstdauer) mit denen der Oberfläche übereinstimmen, bis KP-1C das Feld `code` liefert.

**Wirkung auf das Labor.** Keine, solange die Laborgeräte den Schalter nicht kennen. KP-1A ändert `useWamo.ts`, die Datei der Laborkarten und des Neuladens: Die Laborkomponententests (`LaborKarte.comp.test.tsx`, `laborNeuladen`) laufen in der
Prüfstrecke mit. In der Laborwoche (23. bis 28.11.) gilt: Schalter P aus, Nikos Fenster zu, sonst zählt seine Sitzung zur Last und zum gemeinsamen Ratenlimit des Schlüssels (Laborpapier Abschnitt 7).

**Einfacherer Weg, geprüft und verworfen.** Eine leichte Textsitzung ohne WebRTC bräuchte eine eigene Streaming-Route (WebSockets lehnt die Zugangskontrolle ab, `auth_gate.py:27`), eine Pipeline ohne Transport und einen zweiten Weg für alle
Servernachrichten (Karten, Canvas, Vorgänge, Betrieb), dazu eine neue Angriffsfläche neben der Laborsperre. Aufwand mindestens 40 Stunden, Risiko hoch, Gewinn gegenüber KP-1A gleich null. Verworfen.

### 3.2 Wunsch 2: Fernzugriff ohne frischen Code je Start (KP-2, 13 h, optional KP-2b 2 h)

**Verhalten.** Der Dialog zeigt Ziel, Datenschutzhinweis und "Verbinden", ohne Codefeld. Der Kern verlangt: Rolle admin, Token mit aal2 und einem `totp`-Eintrag (Anmeldung mit Code), gültige Anmeldung (`WAMO_ANMELDUNG_MAX_STUNDEN`, schon in der
Zugangskontrolle). Optional, Standard aus: `WAMO_RD_ANMELDUNG_MAX_MIN` (ganze Zahl 1 bis 43200, 0 oder leer aus). Ist der Code der Anmeldung älter und liegt kein frischer vor (höchstens 120 Sekunden), antwortet der Kern mit 403 und
`zweiter_faktor_zu_alt`, und der Dialog zeigt das Codefeld mit dem Satz "Deine Anmeldung ist älter als N Stunden". Dieser Stufenweg nutzt den vorhandenen Pfad der Oberfläche (`fernzugriff.ts:206`) und die vorhandene Prüfung
(`geraete_admin_api.aal2_frisch`); es entsteht kein zweiter Mechanismus. Unverändert: Admin, ein Zuschauer je Ziel, 3 gleichzeitige, 6 Starts je Stunde, Zeitlimit 60 Minuten, Leerlauf 10 Minuten, feste Zielliste, keine Zwischenablage, kein Laufwerk,
kein Ton, keine Aufzeichnung, Raten je Profil (`rd_api.py:86` bis `:88`).

**Datenbank (Migration 0019).** `rd_sitzung_beginnen` behält Signatur, Rechte und Rückgabe (`create or replace`, `grant` unverändert). Entfallen: die 120-Sekunden-Prüfung (`0015:116` bis `:118`) und die Regel "ein Code, ein Start" (`:127` bis `:132`).
Neu: eine Obergrenze von 31 Tagen für `p_aal2_zeit` mit Fehlercode 23514 (das Maximum der Kerneinstellung ist 720 Stunden, `auth_gate.py:79`), als letzte Schranke gegen einen Kern mit falscher Zeit. Der Spaltenkommentar von `aal2_at` nennt den
Code der Anmeldung. `.down.sql` stellt die Funktion aus 0015 wieder her (der Rückweg ist ohne Datenverlust, die Tabelle bleibt). Ohne die zweite Änderung scheitert der zweite Start nach derselben Anmeldung mit "der zweite Faktor wurde schon für einen Start verwendet" (23514): Das ist der
Fund dieser Prüfung.

**Dateien.** `voice-core/rd_api.py` (neue Funktion `anmeldung_genuegt`, Leser der Variable, Aufruf statt `:418`), `supabase/migrations/0019_rd_anmeldung_genuegt.sql` und `.down.sql`, `supabase/tests/0019_rd_anmeldung_genuegt_test.sql`, Anpassung von
`supabase/tests/0015_rd_sitzungen_test.sql` (Test, keine Migration), `ui/src/components/einstellungen/Fernzugriff.tsx` (Codefeld nur nach 403, Satz `:340` und `:443` neu), `ui/src/lib/fernzugriff.ts`, `voice-core/persona.py:222` bis `:229` (kein Code mehr
im Satz, "frischen Code" bei schwarzem Bildschirm streichen), `voice-core/test_fernzugriff_sprache.py:90`, Doku (`README.md:1252`, `:1276`, `docs/remote-desktop.md:23`, `:157`, `:193`, `:268`, `:296`, `:318`, `infra/sokrates2/README.md:1139`, `CHANGELOG.md`),
Kernvariablen-Listen (`infra/sokrates2/wamo_core_env.py`, `scripts/wamo_kern_sokrates2.py`). **Nicht angefasst:** `voice-core/geraete_admin_api.py`, `voice-core/bild_fern.py`, `infra/sokrates2/freigabe/`. Die gemeinsame Hilfsfunktion zwingt nicht zur Änderung: `rd_api.py`
importiert `aal2_frisch` und `aal2_zeit` weiter.

**Bedrohungen und Gegenmaßnahmen.**

| Bedrohung | Wirkung | Gegenmaßnahme | Restrisiko |
| --- | --- | --- | --- |
| Gestohlenes Sitzungstoken (Browserspeicher, Schadsoftware, offener Laptop) | Zugriff auf den Desktop eines Sparks, bis die Anmeldung abläuft: im Kern bis `WAMO_ANMELDUNG_MAX_STUNDEN` (Standard 168 Stunden), im Browser 24, in der App 7 Tage (`anmeldung.ts:5`) | Admin (Kern und Datenbank), aal2 beim Login, ein Zuschauer je Ziel, 60 Minuten, 10 Minuten Leerlauf, nichts aufgezeichnet, 6 Starts je Stunde, Protokoll in `rd_sitzungen`, Schalter `WAMO_RD_AKTIV`, optional `WAMO_RD_ANMELDUNG_MAX_MIN` | **Bleibt.** Wer das Token hat, kann Sitzungen starten, bis es abläuft |
| Unbeaufsichtigter Browser eines Admins | wie oben | wie oben; KP-2b: ein Start meldet sich bei den Admins (Hinweis `system`, Muster der Geräteverwaltung, `README.md:446` bis `:448`), damit ein fremder Start auffällt | mittel, Weg nur teilweise geprüft (V) |
| Alter Kern gegen neue Datenbank und umgekehrt | Start schlägt fehl oder verlangt Code | Migration zuerst, dann Kern; der Dialog hält das Codefeld als Rückfall (`fernzugriff.ts:206`) | gering |
| Zeit des Kerns falsch | zu alte oder zukünftige Zeit | Datenbank prüft Zukunft (5 Sekunden) und 31 Tage | gering |

**Fehlerfälle.** Token ohne `totp` oder aal1: 403 wie heute. 23514 aus der Datenbank: bestehende Abbildung auf "Code verlangt" (`rd_api.py:443`). Ungültiger Wert der Variable: Standard aus, Zeile im Journal. Doppelklick und zweiter Start nach Tabwechsel:
die eigene Sitzung wird abgelöst, wie heute (`0015:148` bis `:150`).

**Tests (Rot zuerst).** `voice-core/test_rd_api.py`: ein Code von 600 Sekunden startet (Stufe aus); ohne `totp` und mit aal1 weiter 403 (`:371` bis `:390` angepasst); Stufe 60 Minuten: 59:59 geht, 60:01 ohne frischen Code 403, mit frischem Code geht; Zukunft; ungültige Werte; zweiter Start
nach derselben Anmeldung löst den ersten ab; Mitglied 403, belegt 409, Raten unverändert. SQL `0019`-Test: 10 Minuten alter Code geht, 31 Tage plus eine Sekunde 23514, Zukunft 23514, zweiter Start mit gleicher Zeit geht, Nicht-Admin 42501, siebter Start
`rate`, ein Zuschauer je Ziel, Rechte unverändert, zweimal anwendbar. UI: Dialog startet ohne Feld, 403 öffnet das Feld, ohne Feld kein `codeBestaetigen`. Diff-Test: `geraete_admin_api.py` bleibt gleich. Am Server (V): `wamo_rd_rauchtest.py` mit alter
Anmeldung. Reviews: `database-reviewer` vor jedem Apply, `security-reviewer` für den Kern.

### 3.3 Wunsch 3: PC-Steuerung und App-Funktionen im Browser (KP-3: 27 h, optional S2 14 h)

**Grundgedanke.** Die Technik ist da: Der Kern erreicht den Laptop über Profil, Register und Rücktunnel, egal woher die Sitzung kommt. Was fehlt, sind der Schalter in der Oberfläche und Schutzmaßnahmen, die dort wirken, wo die Desktop-App bisher
unbemerkt mitgeschützt hat: Die App ist am Laptop und zeigt Rand und Lichtrahmen. Der Browser sitzt irgendwo im Büro.

**Verhalten.** (1) Der Schalter "PC-Steuerung" erscheint für Admins auch im Browser, Standard aus je Sitzung, nie Autostart (ohne `rechnername` gilt die Bedingung in `useWamo.ts:1222` ohnehin nicht). (2) Einschalten sendet `pc_steuerung` mit
`quelle: "browser"` (App: `"desktop"`). Der Kern nimmt `browser` nur an, wenn `WAMO_PC_BROWSER=ja` gilt und die Notaus-Datei `/etc/wamo/pc-browser.aus` nicht existiert (Prüfung bei jedem Einschalten, ohne Release). (3) Ablauf im Kern für jede Quelle
außer `desktop`: höchstens `WAMO_PC_BROWSER_MAX_MIN` (Standard 30) seit dem Einschalten und `WAMO_PC_BROWSER_LEERLAUF_MIN` (Standard 10) ohne PC-Werkzeug; danach `pc_steuerung` aus, Sperre frei, Meldung an Oberfläche und Stimme, neues Einschalten nur per Klick.
Zwei Minuten vorher steht ein Hinweis in der Oberfläche. (4) Die Oberfläche zeigt einen Chip "PC-Steuerung an, Browser, endet in mm:ss" (neues Feld `pcEndet` in `betrieb`) und sagt Ein, Warnung und Aus einmal über `role="status"`. (5) Freigabekarten, PC-Sperre je
Gerät und Rolle admin bleiben unverändert (`pc_freigaben.py:12` bis `:15`, `pc_host.py:154` bis `:168`). (6) **Anzeige am Laptop (KP-3C):** Der Host schreibt bei jeder ausgeführten Aktion `%LOCALAPPDATA%\WAMO\steuerung.status` (Zeit, Quelle aus festem Wertevorrat,
Zähler, keine Inhalte, atomar, nur für den Benutzer lesbar); die Desktop-App im Tray liest sie alle 2 Sekunden, färbt das Symbol und zeigt "WAMO steuert diesen Laptop (aus dem Browser)". Läuft die App nicht, gibt es keine Anzeige (A, ehrlich genannt; eine
Windows-Meldung vom Host aus ist ein Prüfauftrag, V). (7) **Mehrere Laptops:** Das Register ordnet je Profil das eine aktive persönliche Gerät zu, der Tunnel ist je Laptop, die Sperre je Gerät (`pc_geraet.py:192` bis `:214`). Für weitere Admins mit eigenem Laptop gilt
dasselbe ohne Änderung; Mitglieder bekommen PC-Werkzeuge nur nach Q6 (KP-4F). Der Abgleich von Gerät und Rechnername (`infra/sokrates2/README.md:906`) bleibt.

**Sicherheitsmodell.** Der Kern kann die Herkunft nicht beglaubigen: `quelle` ist eine Angabe des Clients. Ablauf und Chip schützen deshalb vor Vergessen und Versehen, nicht vor einem gestohlenen Token. Dieses Token kann schon heute mit der App-Sitzung oder vom Handy dieselben Sitzungen
aufbauen (`README.md` iPhone-Abschnitt, `infra/sokrates2/README.md:757`); die harten Grenzen bleiben Rolle, Register, Host-Regeln und Karten.

| Bedrohung | Wirkung | Gegenmaßnahme | Restrisiko |
| --- | --- | --- | --- |
| Fremde Person am entsperrten Büro-PC, auf dem Niko angemeldet ist | Laptop wird gesteuert | Schalter Standard aus je Sitzung, Ablauf 30 und 10 Minuten, Chip, Anzeige am Laptop, Browser meldet nach 24 Stunden ab, Karten bleiben Klicks, Notaus-Datei | mittel: Wer vor dem Browser sitzt, kann Karten klicken |
| Gestohlenes Token | wie in der App und vom Handy | unverändert: Rolle, Register, Host-Regeln, Karten; `WAMO_PC_BROWSER=aus` verhindert nur die ehrliche Quelle | **bleibt**, gilt schon heute |
| Schadcode in der Oberfläche (CSP erlaubt Inline-Skripte, `infra/sokrates2/README.md:760`) | sendet `pc_steuerung` und klickt Karten | wie in der App; kein neuer Weg | **bleibt** |
| Browser auf demselben Laptop klickt Karten | Freigabe in der eigenen Oberfläche | Der Host bedient Fenster der Oberfläche nie (`host/windows_control.py:56` bis `:72`), auch Tabs mit Titel "WAMO"; Test mit Chrome-Titel | gering |
| Steuerung unbemerkt am Laptop | Person am Laptop wundert sich | Anzeige am Laptop (6), Lichtrahmen nur mit der App | mittel ohne laufende App |
| Zwei Browser zugleich | Streit um die Sperre | Eine Sitzung je Profil und Sperre je Gerät, die neue löst die alte ab | gering |
| Mitglied sieht den Schalter | Hoffnung ohne Wirkung | Schalter nur für `rolle: admin`, Kern lehnt ab (`bot.py:1089`) | keines |

**Optional S2 (KP-3E, 14 h, Q3): Tagesfreigabe am Laptop.** Nach dem Muster des Live-Bilds (`desktop/livebild.js`, `host/live_bild.py`) erlaubt die Person am Laptop im Tray "Fernsteuerung aus anderen Geräten zulassen" für höchstens 12 Stunden (Datei
`fernsteuerung.json` mit Ablauf und Herzschlag). Der Host verlangt sie für jede Anfrage, die nicht aus dem eigenen Fenster der App kommt (die App meldet ihre eigene laufende Sitzung per Herzschlag). Dann hilft auch ein gestohlenes Token nichts, solange am Laptop
nichts freigegeben ist. Preis: Niko gibt einmal am Morgen am Laptop frei, was dem Wunsch "aus dem Browser im Büro" etwas widerspricht. Empfehlung: erst S1, S2 nach Erfahrung.

**Fehlerfälle.** Kein Gerät im Register oder Tunnel weg: `pc: getrennt`, Schalter zeigt "kein Laptop erreichbar" (`bot.py:1029` bis `:1034`). Host mit älterer Version ohne Statusdatei: Anzeige entfällt, Steuerung läuft. Ablauf mitten in einer Freigabe: die Karte
wird abgelehnt, weil `entscheiden` die PC-Steuerung prüft (`pc_freigaben.py:12` bis `:15`). Sitzung wird abgelöst: Sperre frei, wie heute.

**Dateien.** Kern: neues `voice-core/pc_quelle.py` (Fenster, Frist, Notaus, Quelle), Haken von höchstens fünf Zeilen im Handler `pc_steuerung` und im Wächter von `bot.py` (`:1086` bis `:1098`), Feld `pcEndet` in `betrieb_message`, Header `X-WAMO-Quelle` in `voice-core/pc_tunnel.py`, Listen
in `infra/sokrates2/wamo_core_env.py` und `scripts/wamo_kern_sokrates2.py`. Oberfläche: `ui/src/components/einstellungen/Geraete.tsx`, `ui/src/lib/einstellungen.ts`, `ui/src/lib/geraet.ts` (Kommentar und Funktion `quelle()`), `ui/src/lib/useWamo.ts`, `ui/src/lib/wamo.ts`,
neue Komponente `PcSteuerungChip`. Host und App: `host/host_http.py` oder `host/wamo_host.py` (Statusdatei), `desktop/main.js` und neues `desktop/steuerung.js` mit Test. Doku: `infra/sokrates2/README.md:757`, `README.md` (iPhone-Abschnitt "bleibt der Desktop-App vorbehalten"), `CHANGELOG.md`.

**Tests (Rot zuerst).** Kern `voice-core/test_pc_quelle.py`: Browser nur mit Schalter und ohne Notaus-Datei; Frist 30 und Leerlauf 10 Minuten mit Uhrattrappe; Aktion setzt den Leerlauf zurück; Ablauf gibt die Sperre frei und lehnt offene Karten ab; ungültige `quelle` gilt als `browser`;
`desktop` bleibt ohne Frist; Mitglied lehnt ab; abgelöste Sitzung nimmt die Sperre nie wieder. UI: Schalter im Browser sichtbar, nie Autostart, `quelle` gesendet, Chip mit Restzeit, Hinweis vor Ablauf, Text ohne "nur in der Desktop-App". Host: Statusdatei ohne Inhalte, nur feste
Quellen, Rechte, atomar. App: `desktop/steuerung.test.js` wie `livebild.test.js`. Am Gerät mit Niko: Browser an PC B steuert Laptop A, Anzeige auf A, Ablauf, Karte im Browser, Sperre, Tunnelneustart (nur dort prüfbar, V). Reviews: `security-reviewer`, `react-reviewer`, `python-reviewer`.

### 3.4 Wunsch 4: Bildschirm auf den Fernseher (KP-4: 30 h plus 10 h für Mitglieder, Plan B 30 h)

**Wege im ehrlichen Vergleich.** Alle Angaben zu Windows und den Fernsehern sind nicht am Gerät belegt (V) und werden im Spike geprüft.

| Weg | Aufwand | Zuverlässigkeit | Voraussetzungen und Preis |
| --- | --- | --- | --- |
| (a) Miracast über Windows: Verbindungsleiste per UI-Automation bedienen, Modus mit `DisplaySwitch.exe` | 18 h (Spike 4, Bau 14) | mittel: hängt von Windows-Version und Sprache, Funktion "Drahtlose Anzeige", Miracast-Modus am Fernseher und der Stabilität der Verbindung ab | WLAN-Modul an (auch an der Dockingstation mit Kabel, Wi-Fi Direct braucht es), keine Netzfreigabe nötig, keine neue Abhängigkeit, kein ffmpeg. Die Funktion "Drahtlose Anzeige" ist unter Windows 11 eine optionale Funktion, ihre Installation braucht Adminrechte (V) |
| (b) Der Host streamt selbst, der Fernseher öffnet die Adresse im eigenen Browser (Plan B: MJPEG-Seite aus `mss` und Pillow, ohne Ton; HLS mit ffmpeg wäre mehr) | 30 h (MJPEG), 45 h (HLS, Installer plus etwa 80 MB, V) | hoch bei MJPEG, wenn der Fernseherbrowser mitspielt (V); LG über `ssap://system.launcher/open` (Recht `LAUNCH` ist schon angefragt, `lg_tv.py:32`), Samsung über die Tizen-Schnittstelle (V) | Fernseher muss den Laptop erreichen (gleiches Segment), eine eingehende Firewallregel verlangt einmal Adminrechte je Laptop (bricht "ohne Adminrechte" des Installers), URL mit 256-Bit-Token, genau ein Zuschauer, Ablauf |
| (c) AirPlay oder DLNA | über 40 h, laufende Pflege | niedrig bis mittel | Windows hat kein Bordmittel für AirPlay-Spiegelung, DLNA "Auf Gerät wiedergeben" kennt nur Mediendateien (V); Fremdsoftware mit Lizenz und Adminrechten. Nicht empfohlen |

**Empfehlung:** (a) als Hauptweg, zuerst ein Spike von 4 Stunden mit Niko an beiden Fernsehern (KP-4A), dann Entscheidung je Fernseher. Fällt Samsung durch (V: Samsung hat Miracast bei neueren Modellen teils zugunsten eigener Verfahren zurückgenommen), kommt (b) nur für diesen Fernseher.
Inhalt des Spikes: (1) `netsh wlan show drivers` und Windows-Funktion; (2) Win+K von Hand an beiden Fernsehern mit Modus, Dauer, Latenz, PIN, Name; (3) das Flyout mit dem vorhandenen Werkzeug `elemente_auflisten` lesen: gibt es Namen und Invoke; (4) `DisplaySwitch.exe /extend`,
`/clone`, `/internal`, und ob `/internal` die Verbindung wirklich beendet; (5) Dockingstation mit Kabel; (6) Go oder Plan B.

**Verhalten.** Die Person sagt "zeig meinen Bildschirm auf dem großen Fernseher". WAMO schaltet den Fernseher mit `buerogeraet_steuern` ein (vorhandene Aktion `an`), ruft `bildschirm_senden(fernseher, modus)` und zeigt die Karte. Erst nach dem Klick verbindet der Host
(Zeitlimit 25 Sekunden), setzt den Modus und meldet das Ergebnis. Sagt die Person "Bildschirm nicht mehr senden", ruft WAMO `bildschirm_senden_beenden`, das in 10 Sekunden beendet. Modus `erweitern` (Standard) macht den Fernseher zum zusätzlichen Monitor, es erscheint nur, was die Person dorthin schiebt; `spiegeln` zeigt alles und lehnt der Host bei mehr
als zwei aktiven Monitoren ab (Windows klont nur zwei, V). Höchstdauer 60 Minuten (Host-Konstante), Ende bei Sperre oder Abmelden des Laptops.

**Schnittstellen und Sicherheit.** Der Host kennt nur Fernseher aus einer festen Liste (`host/bildschirm_ziele.py`: Kennung, Anzeigename, Miracast-Name; als `.py`, weil der Installer keine `.toml` mitnimmt, `desktop/package.json:48` bis `:61`). Freie Namen aus dem Gespräch gelten nie: ein
eingeschleuster Text könnte sonst auf ein fremdes Gerät in Reichweite zielen. Einzige Quelle für den Host ist die Liste, `miracast_name` in `geraete.toml` dient als Prüfwert (ein Test vergleicht beide). **Risikoklasse:** `critical`, immer mit Klick-Freigabe (sichtbare Wirkung
im Raum), Karte nennt Laptop, Fernseher, Modus, Dauer und bei `spiegeln` den Satz "Alles auf diesem Bildschirm ist im Raum zu sehen, auch E-Mails, Chats und Meldungen". Beenden und Status brauchen keine Karte. Prüfung im Host, nicht im Prompt: Stufe, Sperre, Liste, Raten (3 je 10 Minuten),
Zustand des WLAN, Audit ohne Inhalte. **Datenschutz:** Entscheidet die Person am eigenen Laptop mit Klick, nie ein anderer; die Liste der Fernseher pflegt Niko; am Laptop zeigt Windows die Verbindung (V) und die App einen Tray-Hinweis über `bildschirm.status`; nichts wird aufgezeichnet.

**Was der Kern und das Gerät tun müssen.** (1) **Kern, KP-4B, 2 h, folgenlos vorab:** `bildschirm_senden` und `bildschirm_senden_beenden` in `LLM_TOOLS` (`pc_host.py:61`), `bildschirm_senden` in `FREIGABE_WERKZEUGE`, `bildschirm` in `FREIGABE_AKTIONEN` (`activity.py:48` bis `:52`), Test wie
`voice-core/test_github_kern.py:34`; die Endliste der Laborwerkzeuge bleibt unberührt, weil Laborsitzungen keine PC-Werkzeuge bekommen (`bot.py:558`). Die Beschreibung des Werkzeugs kommt aus dem Schema des Hosts, `persona.py` nennt PC-Werkzeuge nicht einzeln; ein Satz "erst den Fernseher einschalten, dann senden" ist optional und gehört in denselben Kern-Release. Ein Werkzeug, das der Host noch nicht hat, filtert `tools_filter` weg; die Einträge ändern vor dem Host-Release nichts. (2) **Mitglieder, KP-4F, 10 h, Q6:** Eine Liste `LLM_TOOLS_MITGLIED` mit genau diesen zwei Werkzeugen, die `pc_zugang_fuer` Mitgliedern gibt
(`pc_geraet.py:194`), das Tor `pc_steuerung` gilt für sie nicht (die Karte ist die Hürde), der Host prüft die Stufe selbst; Live-Bild und alles andere bleiben admin. Ohne KP-4F gilt Wunsch 4 nur für Admins. (3) **Gerätedienst, KP-4D, 4 h:** Option `miracast_name` für `lg-webos` und `samsung-tv`
(`konfig.py:56` bis `:59`; zuerst den Dienstcode ausrollen, dann `geraete.toml`, weil unbekannte Schlüssel den Start verhindern), nach dem Spike die Aktion `miracast_bereit` (LG: Bildschirmfreigabe-App starten, Samsung: Eingang, V). Die Aktion erscheint im Kern ohne Änderung, denn die Aktionsliste kommt vom Dienst
(`geraete_werkzeuge.py:188` bis `:194`). (4) **Installer, KP-4E:** `host/*.py` wird automatisch mitgenommen, keine neue Abhängigkeit, neue Version in `desktop/package.json`, Prüfskript `scripts/wamo-bildschirm-pruefen.ps1` (WLAN, Funktion, `DisplaySwitch.exe`), Update durch neuen Installer (kein Updater, `README.md:311`).

**Fehlerfälle.**

| Fall | Verhalten |
| --- | --- |
| WLAN-Modul aus oder fehlt | Karte wird nicht gezeigt, Meldung: Wi-Fi Direct braucht das WLAN-Modul, auch bei Kabel an der Dockingstation |
| Funktion "Drahtlose Anzeige" fehlt | Meldung mit Anleitung, Prüfskript und Tray-Hinweis der App; keine Installation durch WAMO |
| Fernseher aus oder nicht im Empfangsmodus | Kern schaltet ein, Host meldet "nicht gefunden", Meldung nennt den Handgriff am Fernseher (nach Spike) |
| Mehrere Treffer oder Name unklar | Abbruch ohne Klick |
| Windows fragt nach PIN oder Bestätigung | Abbruch nach 25 Sekunden, Meldung |
| Verbindung bricht ab | Host meldet "getrennt", kein automatisches Wiederverbinden |
| Laptop gesperrt | Host beendet, kein Bild bleibt auf dem Fernseher |
| Flyout verdeckt oder fremde Sprache | Suche über Automations-Kennung und Namenliste Deutsch und Englisch, sonst Abbruch ohne Klick |

**Tests (Rot zuerst).** `host/test_bildschirm_senden.py` mit Attrappen für UI-Automation, `DisplaySwitch.exe` und Monitorzahl: unbekannter oder freier Name abgelehnt, ohne Klick keine Verbindung, Karte enthält Fernseher, Modus, Dauer, `spiegeln` bei mehr als zwei Monitoren abgelehnt, mehrere Treffer
abgelehnt, Ablauf und Ende bei Sperre, Audit ohne Inhalte, Raten, Stufe `mitglied` nur diese Werkzeuge. Kern wie `test_github_kern`. Gerätedienst: Erweiterung von `geraete/tests/test_fernseher.py`. **Test am Gerät mit Niko** (Matrix, nur dort prüfbar): Laptops {Nikos AX211, ein Kollegen-Laptop mit anderem Chip,
einer an der Dockingstation} mal Fernseher {LG, Samsung} mal Modus {erweitern, spiegeln} mal Zustand {Fernseher aus, anderer Eingang, bereit}, je 10 Läufe; Annahme 9 von 10, erstes Bild in unter 25 Sekunden, Ende in 10 Sekunden. **Annahme:** Alle Kollegen-Laptops sind Windows 11 mit dem Installer, Macs und Linux haben keinen Host (V).

## 4. Pakete, Reihenfolge, Stränge und Machbarkeit

### 4.1 Annahmen und Pakete

Heute ist Mittwoch, 11.11. Bis zum Freeze am Freitag, 20.11., gibt es sechs Kodiertage (12., 13., 16., 17., 18. und 19.11.), der 20.11. ist Releasetag. Das Laborpapier rechnete für seine drei Stränge 131 Stunden gegen 81 bis 108 verfügbare (`docs/testlabor-wamo.md`,
Abschnitt 4.1); nach dem Merge von PR 83 am 11.11. ist der Rest nicht belegt (V). **Annahme (A):** Dieses Papier bekommt einen eigenen Umsetzer mit 27 bis 36 Stunden bis zum Freeze (6 Tage mal 4,5 bis 6 Stunden, wie im Laborpapier) und für Host und Installer einen zweiten ab dem Labortag.
Ohne eigenen Umsetzer gilt die Streichreihenfolge in 4.3. Stränge: **K3** Kern, Oberfläche, Datenbank. **H** Host, Installer, Gerätedienst (nicht vom Freeze betroffen). Aufwand ist eine Schätzung (A) mit Tests zuerst, Review-Agenten und Korrekturen, ohne Wartezeit auf Niko.

| Paket | Inhalt | Dateien | Std | Strang | Wann | Wenn die Zeit fehlt |
| --- | --- | --- | --- | --- | --- | --- |
| KP-0 | Papier prüfen, Fragen Q1 bis Q9 klären, Abgleich mit dem Laborstrang (gemeinsame Dateien `bot.py`, `useWamo.ts`) | dieses Papier | 2 | K3 | Do 12.11. | nichts |
| KP-1A | Start beim ersten Tippen, nur Oberfläche, mit Geräteschalter (Standard aus): Zustandsautomat, Feld ohne Sitzung aktiv, still ohne Mikrofon, Zeile halten, Trennen nach 20 Minuten, Browsertest mit gefälschtem Gerät | `ui/src/lib/bereit.ts` (neu) und Test, `useWamo.ts`, `ui/src/components/kommando/Bedienung.tsx`, Statuszeile | 12 | K3 | Do 12. bis Mo 16.11. | entfällt zuletzt (nur Oberfläche, Rückweg eine Sekunde) |
| KP-2 | Fernzugriff ohne frischen Code: `rd_api.py`, Migration 0019, Dialog, Persona, Doku | siehe 3.2 | 13 | K3 | Fr 13. bis Di 17.11. | entfällt als erstes der drei Posten vor dem Freeze (Migration und Kern-Release nahe am Labor) |
| KP-2b | Hinweis an alle Admins beim Start einer Fernsitzung | `rd_api.py`, Hinweise-Weg (V) | 2 | K3 | mit KP-2, optional | entfällt |
| KP-4B | Kern: Vorregistrierung von `bildschirm_senden` und `bildschirm_senden_beenden` in den drei Listen | `pc_host.py:61`, `activity.py:48`, `:50`, Test | 2 | K3 | im Kern-Release vom 17.11., optional | entfällt zuerst, wandert in den Release danach |
| KP-1B | Schalter "Immer bereit" (Variante P): Fokusregeln, Erneuerung, Wiederholung | `bereit.ts`, `useWamo.ts`, Einstellungen | 8 | K3 | ab Mo 30.11. | nach der Messung der Leerlaufkosten |
| KP-1C | Kern: stiller Modus (`modus`, `code`, keine Begrüßung, Mute, Laborablehnung, Erneuerung bei Slotwechsel) | `bot.py`, `auth_gate.py`, `audio_monitor.py`, Tests | 10 | K3 | ab Mo 30.11. | Voraussetzung für Standard an |
| KP-3A | Kern: `pc_quelle.py`, Haken, Ablauf, Notaus, Header, Listen | siehe 3.3 | 7 | K3 | ab Mo 30.11. | |
| KP-3B | Oberfläche: Schalter im Browser, Chip, Hinweise, Texte | siehe 3.3 | 6 | K3 | ab Mo 30.11. | |
| KP-3C | Host und App: Statusdatei, Tray-Anzeige | `host/`, `desktop/steuerung.js` | 10 | H | ab Mo 23.11. baubar, Release mit KP-3A | |
| KP-3D | Reviews, Doku, Test am Gerät | | 4 | K3, H | nach 3A bis 3C | |
| KP-3E | Tagesfreigabe am Laptop (S2), nur wenn Niko es will | `host/`, `desktop/` | 14 | H | optional | |
| KP-4A | Spike Miracast mit Niko an beiden Fernsehern | Messprotokoll | 4 | H | Do 12. oder Fr 13.11., sonst nach dem Labortag | Niko vor Ort, etwa 2 Stunden |
| KP-4C | Host: `bildschirm_senden`, `bildschirm_senden_beenden`, Liste, Karte, Status, Tests | `host/bildschirm_senden.py`, `host/bildschirm_ziele.py`, `host/wamo_host.py`, Test | 14 | H | nach KP-4A | Plan B (b) 30 Stunden statt dessen |
| KP-4D | Gerätedienst: `miracast_name`, `miracast_bereit` | `geraete/wamo_geraete/konfig.py`, Treiber, Tests, `geraete.toml.example` | 4 | H | nach KP-4A | |
| KP-4E | Installer, Prüfskript, Doku, Test an beiden Fernsehern | `desktop/package.json`, `scripts/wamo-bildschirm-pruefen.ps1`, `README.md` | 6 | H | nach KP-4C | |
| KP-4F | Mitglieder: enge Werkzeugliste | `pc_geraet.py`, `pc_host.py`, `bot.py`, Host-Stufe | 10 | K3, H | nach Q6 | Wunsch 4 gilt dann nur für Admins |

**Summen.** Vor dem Freeze: KP-0, KP-1A, KP-2 = 27 Stunden (29 mit KP-4B, 31 mit KP-2b). Nach dem Labortag: KP-1B, KP-1C, KP-3A bis D, KP-4A, C, D, E, F = 83 Stunden (97 mit KP-3E). Gesamt 112 Stunden ohne die optionalen Posten und ohne Plan B (30 Stunden).

### 4.2 Kalender

| Tag | K3 (Kern, Oberfläche, Datenbank) | H (Host, Installer, Gerätedienst) | Niko und Labor |
| --- | --- | --- | --- |
| Do 12.11. | KP-0, Rot-Tests KP-1A (Zustandsautomat), Spike Bereit ohne Mikrofon | KP-4A, wenn Niko Zeit hat | Q1 bis Q9 beantworten |
| Fr 13.11. | KP-1A Hauptteil, KP-2 Rot-Tests und Migration 0019 auf `wamo_dev` und `wamo_test`, `database-reviewer` | KP-4A sonst hier | Niko am Fernseher, 2 Stunden |
| Mo 16.11. | KP-1A Komponenten und Browsertest, KP-2 Kern und Dialog | Entwurf KP-4C nur bei Spike-Erfolg | |
| Di 17.11. | Reviews (`code-reviewer`, `security-reviewer`, `react-reviewer`), Migration auf das Büro-Schema nach Nikos Ja, Kern-Release über die Slots, Oberfläche verteilen | | Niko: Ja zu Migration und Release |
| Mi 18.11. | Bewährung, Messung der Wartezeit, `ausleiten` nach Ruhe, Puffer | | Laborpapier: Go für das Schaufenster 12:00 |
| Do 19.11. | Puffer und Korrekturen, nichts Neues | | |
| Fr 20.11. | **Freeze** (Laborpapier: Lab-Release am Abend), keine Komfortänderung | | |
| Mo 23. bis Fr 27.11. | nichts im Kern, Oberfläche und Datenbank | KP-4C und KP-3C baubar, nichts wird ausgerollt | Laborwoche, Schalter P aus, Nikos Fenster zu |
| Sa 28.11. | Labortag | | keine anderen Sitzungen |
| ab Mo 30.11. | KP-1B, KP-1C, KP-3A, B, D, jedes Paket mit eigener Bewährung | KP-4C bis F, KP-3C, Installer-Verteilung | Niko: Ja je Release |

### 4.3 Machbarkeit, ehrlich

**Mit eigenem Umsetzer ja, ohne nein.** 27 Stunden vor dem Freeze sind die Untergrenze der Kapazität eines Strangs (27 bis 36 Stunden) ohne Puffer und ohne Wartezeit auf Niko. Der Posten mit dem größten Fehlerrisiko ist die Migration mit dem Kern-Release am 17.11., denn sie
liegt zwei Tage vor dem Freeze und das Labor baut auf demselben Kern. **Was zuerst entfällt, in dieser Reihenfolge:** (1) KP-4B (2 Stunden, wandert ohne Verlust nach dem Labortag, nur ändert sich dann die Reihenfolge: erst Kern, dann Host). (2) KP-2b. (3) KP-2 (13 Stunden): Der Fernzugriff bleibt dann
mit Code, das Labor ist unberührt. (4) KP-1A entfällt zuletzt, weil er nur die Oberfläche berührt und in einer Sekunde zurückgeht; er wartet, wenn die Laborarbeiten die Oberfläche noch brauchen. **Nie entfallen:** die Laborarbeiten selbst. Wenn der Umsetzer fehlt und das Labor die Kapazität der Stränge ausschöpft, gilt: alles nach dem Labortag, mit
einem Plan, der dann in Ruhe getestet wird. Das ist die sichere Rückfallebene und gleichzeitig meine Empfehlung für den Fall, dass bis Do 12.11. kein eigener Umsetzer feststeht (Q7).

**Nach dem Labortag** gibt es keinen Termin von außen. Zwei Stränge schaffen die 83 Stunden in etwa zehn Arbeitstagen (A), also bis etwa Mitte Dezember. Der Host-Teil von Wunsch 4 ist der längste Pfad und hängt am Spike.

## 5. Rollout und Rückweg

Vor jedem Merge und jedem Rollout fragt der Umsetzer Niko gezielt, der Klassifizierer verweigert beides ohne ausdrückliches Ja (Erfahrung vom 11.11.). Nur bei Ruhe (kein Gespräch, Journal leer; ab KP-1B auch keine stille Sitzung), Server-Skripte wiederholbar mit `--trocken`,
**nie `systemctl restart wamo-core`**. Die Reihenfolge ist je Paket gleich: Migration vor Kern, Kern vor Oberfläche, Kern-Vorregistrierung vor Host, Dienstcode vor `geraete.toml`.

| Paket | Reihenfolge | Rückweg |
| --- | --- | --- |
| KP-1A | Oberfläche: `python scripts/wamo_ui_sokrates2.py verteilen`, dann `umschalten` (blau und grün). Nach `wamo_kern_sokrates2.py verteilen` muss `verteilen` für die Oberfläche erneut laufen (Stolperstein vom 11.11.) | `wamo_ui_sokrates2.py zurueck`, eine Sekunde |
| KP-1B | wie KP-1A | wie KP-1A |
| KP-1C | Kern über die Slots: `release`, `umschalten-kern`, `bewaehren 30`, danach `ausleiten`; die Oberfläche kennt das Feld `code` bereits | `rueckweg-kern`, solange der alte Slot läuft |
| KP-2 | 1. Migration 0019 auf `wamo_dev` und `wamo_test` (`render-migration.sh`, `psql -v ON_ERROR_STOP=1 --single-transaction`, zweimal), `database-reviewer`. 2. Nach Nikos Ja auf das Büro-Schema. 3. Kern wie oben. 4. Oberfläche | Oberfläche zurück, Kern `rueckweg-kern`, dann `0019...down.sql` (stellt die Funktion aus 0015 wieder her, keine Daten gehen verloren). Der Dialog behält das Codefeld als Rückfall |
| KP-3A bis B | Kern und Oberfläche wie oben, `WAMO_PC_BROWSER=aus` bleibt Standard bis zum Test am Gerät | `WAMO_PC_BROWSER` zurück oder Notaus-Datei anlegen (sofort), Oberfläche und Kern zurück |
| KP-3C | neuer Installer auf den Laptops (`npm run dist`, `scripts/wamo-dist-pruefen.cjs`, Prüfsumme getrennt weitergeben, unsigniert, `README.md:305` bis `:310`) | alter Installer, die Statusdatei ist rein informativ |
| KP-4B | im Kern-Release, folgenlos | `rueckweg-kern` |
| KP-4C bis E | Gerätedienst (`wamo-geraete-einrichten.sh`, erst Code, dann `geraete.toml`), danach Installer mit Host | Hostschalter per Marker-Datei `%LOCALAPPDATA%\WAMO\bildschirm.aus`, alter Installer, `wamo-geraete-rueckweg.sh` |
| KP-4F | Kern über die Slots, Host mit Stufenprüfung zuerst | `rueckweg-kern`, Mitglieder bekommen wieder nichts |

**Einschränkung des Freezes.** Laborwoche: Keine Komfortänderung an Kern, Migration und Oberfläche. Der Installer und der Gerätedienst dürfen verteilt werden, wenn sie keinen Laborpfad berühren; empfohlen ist trotzdem das Ende der Laborwoche abzuwarten (Q1).

## 6. Offene Fragen an Niko

Nur echte Entscheidungen, ohne Antwort gilt die Empfehlung. Nikos Hände sind mit **N** gekennzeichnet.

| Nr | Frage | Empfehlung | Frist |
| --- | --- | --- | --- |
| Q1 | Wann kommen Wunsch 3 und 4? Alles nach dem Labortag, oder Teile davor? | KP-1A und KP-2 vor dem Freeze, KP-3 und KP-4 ab Mo 30.11. Wunsch 3 vor dem Labortag öffnet den Browser für die PC-Steuerung in der Woche, in der Gäste im Büronetz sind; das ist keine gute Woche dafür | Do 12.11. |
| Q2 | Reicht der Start beim ersten Tippen (Wartezeit der ersten Zeile 2 bis 4 Sekunden, A) oder soll es "sofort bereit" (P) sein? | L zuerst, P als Schalter nach dem Labortag und nach der Kostenmessung. P hält dauernd Sitzungen, die Ruheprüfung und `ausleiten` stören | Do 12.11. |
| Q3 | Schutzstufe für den Browser: S1 (Schalter, Ablauf, Anzeige am Laptop) oder S2 (zusätzlich Tagesfreigabe am Laptop, 14 Stunden mehr)? | S1, dazu ausdrücklich: Das schützt nicht vor einem gestohlenen Token (das kann der Kern heute schon nicht verhindern). S2 nur, wenn Niko das will | vor KP-3 |
| Q4 | Soll auch die Fernanfrage des Live-Bilds ohne frischen Code gehen? | Nein, bleibt. Der Bildschirm eines Laptops mit Kundendaten gehört nicht in dieselbe Erleichterung wie der Spark-Desktop (Entscheidung Niko 04.11.) | jetzt |
| Q5 | Woher kennt der Host die Fernseher? | Feste Liste im Host (Kennung, Anzeigename, Miracast-Name), `miracast_name` in `geraete.toml` als Prüfwert. Alternative: der Kern reicht den Namen durch, das braucht ein Kern-Werkzeug (mehr Aufwand, mehr Angriffsfläche) | vor KP-4C |
| Q6 | Sollen Kollegen (Mitglieder) `bildschirm_senden` bekommen, mit enger Werkzeugliste statt `WAMO_PC_MITGLIEDER=an`? Und gilt Wunsch 3 nur für Admins? | Ja für die zwei Werkzeuge (KP-4F, 10 Stunden), nein für alles andere. Wunsch 3 nur Admin, wie `CLAUDE.md` | vor KP-4F |
| Q7 | Gibt es einen eigenen Umsetzer für dieses Paket bis Do 12.11.? | Wenn nein: alles nach dem Labortag, bis dahin nur das Papier und der Spike mit Niko | Do 12.11. |
| Q8 | Fernzugriff: Stufenschalter `WAMO_RD_ANMELDUNG_MAX_MIN` aus lassen, und den Hinweis an alle Admins (KP-2b) bauen? | Schalter aus wie entschieden, Hinweis bauen (2 Stunden). Wer ein Netz will: 1440 (24 Stunden) | mit KP-2 |
| Q9 | **N:** Zwei Stunden am Fernseher für den Spike (Laptop mit AX211, beide Fernseher, Fernbedienung, Zeit zum Kopplungsfenster) | Do 12.11. oder Fr 13.11., sonst Mo 30.11. | Do 12.11. |

## 7. Risiken und Restrisiken

| Risiko | Wirkung | Gegenmaßnahme | Restrisiko |
| --- | --- | --- | --- |
| Zeit: Das Labor hat keine freie Kapazität, bei fehlendem Umsetzer entsteht Druck auf den Freeze | Laborfehler durch Komfortcode | Streichreihenfolge 4.3, eigener Strang, Migration nur nach `database-reviewer` und Ja | **hoch**, wenn kein eigener Umsetzer da ist |
| Gemeinsame Dateien `bot.py`, `useWamo.ts` mit dem Laborstrang | Konflikte, Rückbau nötig | Haken von höchstens fünf Zeilen, neue Module (`pc_quelle.py`, `bereit.ts`), Rebase vor jedem Merge, Laborkomponententests in der Prüfstrecke | mittel |
| Stille Sitzungen halten den alten Slot und stören die Ruheprüfung (nur P) | Release oder `ausleiten` scheitern oder warten | L statt P, P erst nach dem Labortag, Kern beendet stille Sitzungen beim Slotwechsel (KP-1C) | gering mit L |
| Mikrofon ohne Wissen oder Aufnahmepunkt im Tab | Datenschutz, Vertrauen | `enableMic: false`, Browsertest, Anzeige | gering |
| Die Annahme "kein Audio ohne Mikrofon" stimmt nicht (Spur sendet Stille an Soniox) | Leerlaufkosten | Spike, bei L ohnehin nur kurze Sitzungen; `STTMuteFrame` in KP-1C | gering |
| Gestohlenes Token bei Wunsch 2 | Zugriff auf Spark-Desktop bis zum Ablauf der Anmeldung (Standard 168 Stunden im Kern) | siehe 3.2, KP-2b als Erkennung | **bleibt**, ausdrücklich akzeptiert |
| Browser auf fremdem Büro-PC bedient den Laptop | Steuerung ohne Wissen der Person am Laptop | Ablauf, Chip, Anzeige am Laptop, Notaus, Karten | mittel, ohne laufende App ohne Anzeige |
| `quelle` ist nur eine Angabe des Clients | Ablauf lässt sich umgehen | offen so genannt; harte Grenzen bleiben Rolle, Register, Host, Karten; S2 als Option | **bleibt** |
| Miracast bei einem Fernseher nicht brauchbar (Samsung, V) | Wunsch 4 nur halb | Spike zuerst, Plan B nur für diesen Fernseher | mittel |
| "Drahtlose Anzeige" fehlt oder braucht Adminrechte auf Kollegen-Laptops | Funktion fällt bei einem Teil aus | Prüfskript, Anleitung, kein stilles Installieren durch WAMO | mittel |
| UI-Automation des Flyouts ist empfindlich für Windows-Updates und Sprache | Ausfall nach Update | Tests mit Attrappe, Namenliste Deutsch und Englisch, Abbruch ohne Klick statt Raten, Meldung | mittel |
| Bildschirm eines Laptops erscheint ungewollt im Raum | Datenschutz | Klick-Freigabe immer, `erweitern` als Standard, 60 Minuten, Ende bei Sperre, Hinweis am Laptop | gering bis mittel |
| Mitglieder bekommen einen Host-Zugang | größere Fläche | nur zwei Werkzeuge, Host prüft die Stufe selbst, Review, Q6 | gering |
| Installer unsigniert, neue Version auf allen Laptops | SmartScreen, Austausch der `WAMO.exe` | Prüfsumme getrennt weitergeben, bekannte Grenze (`README.md:305` bis `:310`) | mittel, schon heute |

**Restrisiken, die bleiben.** (1) Wer ein gültiges Token eines Admins hat, bedient Spark-Desktops und Laptops, bis es abläuft: Das war vor diesem Papier so und wird durch Wunsch 2 und 3 bequemer, nicht mächtiger. (2) Die Angaben zu Windows, Miracast und den Fernsehern sind nicht am Gerät belegt. (3) Die
Wartezeit von 2 bis 4 Sekunden und die Leerlaufkosten sind nicht gemessen. (4) Ob der Kern beim Erneuern des Tokens den Zeitstempel des Codes beibehält, ist als Annahme dokumentiert und nur am Server prüfbar.

## 8. Neue Namen (passend zum Namensvertrag in `CLAUDE.md`)

| Bereich | Name |
| --- | --- |
| Migration | `0019_rd_anmeldung_genuegt.sql` und `.down.sql`, Funktion `__SCHEMA__.rd_sitzung_beginnen` (gleiche Signatur), Test `supabase/tests/0019_rd_anmeldung_genuegt_test.sql`, Schemas `wamo_dev`, `wamo_test`, `wamo_prod`, keine neue Tabelle, keine neue DB-Rolle |
| Kern, Umgebung | `WAMO_RD_ANMELDUNG_MAX_MIN` (Standard 0 = aus), `WAMO_PC_BROWSER` (an oder aus, Standard aus), `WAMO_PC_BROWSER_MAX_MIN` (30), `WAMO_PC_BROWSER_LEERLAUF_MIN` (10), Notaus-Datei `/etc/wamo/pc-browser.aus`, `WAMO_STILL_START` (nur KP-1C, an oder aus) |
| Kern, Module und Nachrichten | `voice-core/pc_quelle.py`, Funktion `anmeldung_genuegt` in `voice-core/rd_api.py`, Feld `quelle` in der Client-Nachricht `pc_steuerung`, Feld `pcEndet` in `betrieb`, Schlüssel `modus` im Start (nur KP-1C), Feld `code` in `sitzung_beendet`, Fehlercode `still_nicht_erlaubt`, Header `X-WAMO-Quelle`, Listen `LLM_TOOLS_MITGLIED` (nur KP-4F) |
| Werkzeuge im Host | `bildschirm_senden(fernseher, modus)`, `bildschirm_senden_beenden()`; Freigabeart und Aktion `bildschirm`; Module `host/bildschirm_senden.py` und `host/bildschirm_ziele.py`; Marker `%LOCALAPPDATA%\WAMO\bildschirm.aus`; Dateien `bildschirm.status` und `steuerung.status` (nur Zeit, Quelle, Zähler); Skript `scripts/wamo-bildschirm-pruefen.ps1` |
| Gerätedienst | Option `miracast_name` in `geraete.toml` (Treiber `lg-webos` und `samsung-tv`), Aktion `miracast_bereit` (nach dem Spike), Kennungen `fernseher` und `fernseher-2` unverändert |
| Oberfläche | `ui/src/lib/bereit.ts`, Einstellungen `wamo.startBeimTippen` (KP-1A) und `wamo.bereitBeimOeffnen` (KP-1B), beide Standard aus, Komponente `PcSteuerungChip`, Funktion `quelle()` in `ui/src/lib/geraet.ts` |
| Desktop-App | `desktop/steuerung.js` und Test, `desktop/package.json` neue Version, Tray-Hinweis "WAMO steuert diesen Laptop" und "Bildschirm wird gesendet" |
| Tests | `ui/src/lib/bereit.test.ts`, `voice-core/test_pc_quelle.py`, `voice-core/test_bot_still.py` (KP-1C), `host/test_bildschirm_senden.py`, Erweiterungen von `voice-core/test_rd_api.py`, `ui/src/lib/fernzugriff.test.ts`, `voice-core/test_github_kern.py` (Muster) und `geraete/tests/test_fernseher.py` |
| Zweig | `docs/komfort-plan` (dieses Papier), später `feat/komfort-*` je Paket |

## 9. Nachtrag 11.11.2026 abends: Stand der Umsetzung vor dem Freeze

**Umgesetzt und geprüft** im Integrationszweig `feat/komfort` (Stränge `kp/1a` und `kp/2`, Korrekturen in den Commits `ef62fad` und `b7b5742`): Start beim ersten Tippen (KP-1A, nur Oberfläche) und Fernzugriff ohne Extra-Code (KP-2, Migration 0019, Kern, Dialog, Stufenschalter, Hinweis an Admins). Abweichung von Abschnitt 4: Der stille Start nutzt einen eigenen `StillMedienManager` (`ui/src/lib/stillMedien.ts`), weil der Standardmanager des Transports das Mikrofon beim Verbinden immer öffnet. Das ist durch einen Test belegt.

**Prüfungen.** Voller voice-core-Lauf: 6 bekannte Fehler dieser Maschine, sonst grün. Oberfläche: node 510, vitest 181, `tsc` und `eslint` ohne Fehler. Migration 0019 auf `wamo_test`: zweimal angewendet, `down` zweimal, wieder `up`, SQL-Tests 0015 und 0019 bestanden. Datenbank-Review und Sicherheitsreview: kein CRITICAL, kein HIGH, beide „mit Änderungen“, die Änderungen sind eingearbeitet (die neuen Tests sind gegen den alten Stand rot).

**Eingearbeitet.** (1) Reihenfolge am Server ist **Kern vor Migration**, nicht umgekehrt: Ein alter Kern mit der neuen Funktion kennt die Startsperre je Profil und die Nachprüfung nicht. (2) Der Hinweis rät nicht mehr zum Abmelden an allen Geräten (gilt nur lokal), sondern: den anderen Admins melden, die das Profil sperren. (3) Das Mikrofon bleibt zu, wenn die Sitzung während der Berechtigungsfrage endet. (4) Die Ruhezeit beginnt auch bei „Hier übernehmen“. (5) „Verbinden“ im Dialog ignoriert den zweiten Klick eines Doppelklicks. (6) Alter des Codes in Tagen, „1 Minute“, eine Probe „ohne Hinweis kein Start“.

**Offen, bewusst nicht Teil des Pakets.** (a) Die Sperre gegen das Verdrängen einer laufenden Sitzung (M6) gilt nur im eigenen Tab. Auf einem zweiten Gerät kann ein Tippen die Sitzung des Profils ablösen, bis der Kern die Abfrage übernimmt (KP-1C): Der Schalter „Start beim Tippen“ gehört vorerst auf ein Gerät je Person. (b) Die Laborsperre des stillen Starts gilt nur in der Oberfläche, ohne Rechtegewinn, die Kernablehnung folgt mit KP-1C. (c) Der Hinweis an die Admins kann ein Fehlalarm sein, wenn nach dem Datenbankstart die Anmeldung am Gateway scheitert (höchstens 6 je Stunde und Profil).

**Rollout-Reihenfolge (nur mit Nikos Ja, Ziel Di 17.11.).** 1. `wamo_dev` lesend prüfen (`schema_migrations` hat 0001, 0002, 0015, 0018, keine offene Fernsitzung). 2. Kern-Release über `wamo-core-slots.sh` (mit der alten Funktion: nur Codeabfrage, sicher). 3. Migration 0019 auf `wamo_dev` mit `psql -v ON_ERROR_STOP=1 --single-transaction`, zweimal, dann beide SQL-Tests. 4. Oberfläche über `wamo_ui_sokrates2.py verteilen` und `umschalten`. 5. Stufe `WAMO_RD_ANMELDUNG_MAX_MIN` bleibt aus; wer sie setzt, nimmt `wamo-core-env setzen` und prüft vorher mit `wamo-core-env liste`, ob der Name bekannt ist (`scripts/wamo_kern_sokrates2.py` überträgt nur die Namen in `AUS_ENV`). 6. Am Server prüfen: Behält das Erneuern des Tokens den Zeitstempel des Codes im `amr`? (nur für die optionale Stufe wichtig). 7. Rauchprobe: Start, zweiter Start, Hinweis bei allen Admins sichtbar. 8. Abnahme des stillen Starts im Büro-Browser mit Niko (kein Berechtigungsdialog beim Öffnen, kein Mikrofon bis „Sprechen“, Antwort als Text). `wamo_prod` gibt es nicht.

---

WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn
