# WAMO-Gerätedienst (`wamo-geraete`)

Gemeinsame Bürogeräte für alle Profile über den Kern auf Sokrates-2: Server-Status (Sokrates-1, Sokrates-2, Caesar),
Drucker und Scanner (Brother MFC-L3750CDW), Router (FRITZ!Box 7590), NAS (WMC_NAS1, WMC-Testelite), Fernseher
(LG OLED83B49LA und Samsung 85 Zoll QLED GQ85Q70DATXZG), Steckdosen (drei Shelly Plug S Gen3), iMac (nur Anwesenheit).
Dazu, sobald eingetragen, Switches und Access Points (SNMPv3, nur lesen). Der Dienst erkennt außerdem passiv, welche Geräte
im Netz er nicht kennt. Persönliche Laptops gehören nicht hierher (die laufen über den Tunnel je Gerät).

```text
Kern (wamo-core) ──/run/wamo-geraete/geraete.sock──> wamo-geraete (eigener Benutzer, SO_PEERCRED = wamo-core)
                                                       ├─ IPAddressDeny=any + IPAddressAllow aus geraete.toml
                                                       ├─ nft inet wamo_geraete_egress (nur Adresse:Port der Geräte)
                                                       └─ Ablage je Person /var/lib/wamo-geraete/ablage/<user_id>/
```

## Protokoll

Je Verbindung eine JSON-Zeile hin, eine JSON-Zeile zurück, danach genau `anhang` Bytes (nur Dateien):

```json
{"v": 1, "werkzeug": "status", "geraet": "drucker", "argumente": {},
 "kontext": {"user_id": "<uuid>", "rolle": "mitglied", "session_id": null, "name": "Anna"}}
{"status": "success", "zustand": "bereit", "toner": [...]}
```

`status`: `success`, `error`, `rejected`, `nicht_eingerichtet`, `grenze`. Der Kontext ist eine Behauptung des Kerns;
der Dienst bedient nur die uid von `wamo-core`.

| Werkzeug | Risiko | Rollen | Geräte |
| --- | --- | --- | --- |
| `ping`, `geraete` | read | alle | |
| `status` (auch `geraet = "alle"`) | read | alle | alle |
| `steuern` (`aktion`: an, aus, lautstaerke, lauter, leiser, stumm, laut, eingang) | change | alle | Fernseher, Lautsprecher, Steckdosen |
| `drucken` (`dokument`, `kopien` 1 bis 5, `farbe`) | critical | alle, Freigabe im Kern | Drucker |
| `scannen` (`quelle` glas oder einzug, `farbe`, `aufloesung` 150 oder 300) | change | alle | Scanner |
| `ablage_liste`, `ablage_holen`, `ablage_loeschen` | read, change | alle, nur eigene | |
| `nas_suchen`, `nas_abrufen` | read | alle | NAS |
| `router_geraete` | read | nur admin | Router (TR-064) |
| `unbekannt_zaehlen` | read | alle | keines (liefert nur eine Zahl und den Zeitstempel) |
| `unbekannte_geraete` | read | nur admin | keines (IP, MAC-Vorsatz, gekürzter Name, Schnittstelle) |

Grenzen: Status 30 je Minute und Profil, Steuern 20 je Minute, Drucken 10 Aufträge je Stunde und 50 Seiten je Tag und
Profil (gesamt 200), Scannen 20 je Stunde und ein Auftrag zugleich, NAS 30 je Minute, Erkennung unbekannter Geräte 12 je
Minute und Profil. Statusantworten 20 s im Zwischenspeicher, die Hostliste des Routers 60 s. Gemessen am 21.10.2026 (ohne Zwischenspeicher, p50): Sokrates-2 24 ms, Sokrates-1 0,66 s (SSH),
Drucker 0,14 s, Scanner 28 ms, Router 0,15 s, Lautsprecher 36 ms, Steckdose 34 ms.

## Einrichten, aktualisieren, Rückweg

Siehe Kopfkommentar von `infra/sokrates2/wamo-geraete-einrichten.sh` (Dateien nach `~/wamo-infra`, dann mit sudo).
Konfiguration `/etc/wamo-geraete/geraete.toml` (Vorlage `infra/sokrates2/geraete.toml.example`), Zugangsdaten
`/etc/wamo-geraete/geraete.env` nur über stdin:

```bash
# Zeilen WAMO_GERAETE_<ZUGANG>_BENUTZER=... und _PASSWORT=... in einer Datei, die nie ins Repo kommt
ssh sokrates2 "sudo wamo-geraete-env setzen && sudo systemctl restart wamo-geraete" < zugang.txt
ssh sokrates2 "sudo wamo-geraete-env liste"                       # nur Namen
ssh sokrates2 "sudo bash ~/wamo-infra/wamo-geraete-einrichten.sh --fritzbox-zertifikat"   # tls_sha256 für TR-064
```

Status der Sparks: `infra/sparks/wamo-status-einrichten.sh` je Spark (Benutzer `wamo-status`, nur lesen).
Kopplung eines Fernsehers (die Person am Fernseher bestätigt):

```bash
ssh sokrates2 "sudo -u wamo-geraete env WAMO_GERAETE_KONFIG=/etc/wamo-geraete/geraete.toml \
  WAMO_GERAETE_KOPPLUNG=/var/lib/wamo-geraete/kopplung sh -c 'cd /opt/wamo-geraete/app && \
  /opt/wamo-geraete/venv/bin/python -s -m wamo_geraete.koppeln fernseher'"
```

Rückweg: `sudo bash ~/wamo-infra/wamo-geraete-rueckweg.sh [--entfernen | --alles]`, auf den Sparks
`wamo-status-rueckweg.sh [--entfernen]`.

## Server-Status und Dienste von Sokrates-2

Sokrates-2 fragt der Dienst lokal ab (Treiber `server-lokal`): Laufzeit, Last, Speicher und Platte aus `/proc` und `statvfs`, die
GPU über `nvidia-smi` und je Eintrag in `dienste` den Zustand über `systemctl is-active` (feste Befehlszeile ohne Shell, Zeitlimit
2 Sekunden). Die Büro-Karte zählt jeden Dienst mit `failed`, `inactive` oder `deactivating` als Störung („läuft, 1 Dienst nicht aktiv“).

- **Kern-Slots:** Der Kern läuft als Vorlage `wamo-core@.service` in Instanz `a` oder `b`, bei jedem Release im Wechsel
  (`infra/sokrates2/wamo-core-slots.sh`). Die alte Unit `wamo-core.service` ist absichtlich aus und wird nie gestartet. In `dienste`
  steht deshalb `wamo-core@*.service`. Dafür ruft der Dienst `systemctl is-active wamo-core@*.service` auf: systemd erweitert das
  Muster selbst über die geladenen Instanzen und gibt je Instanz eine Zeile aus. Das Ergebnis steht unter dem Schlüssel
  `wamo-core@*.service`: `active`, sobald eine Instanz läuft, sonst der Zustand der besten Instanz (Rangfolge `active`, `reloading`,
  `refreshing`, `activating`, `deactivating`, `maintenance`, `inactive`, `failed`). Ist keine Instanz geladen, heißt es `inactive`;
  scheitert `systemctl` selbst (Rückgabewert außer 0, 3 und 4), heißt es `unbekannt`.
- **Erlaubte Einträge:** `name.service`, eine feste Instanz `name@x.service` und als einziger Platzhalter `name@*.service`. Andere
  Muster (`*.service`, `wamo-*.service`, `name@?.service`, `name@[ab].service`, `name@a*.service`) verhindern den Start.
- **Getestet** mit einer Attrappe für `systemctl` (`tests/test_server_status.py`); am Server prüft man es mit
  `systemctl is-active 'wamo-core@*.service'` (eine Zeile je geladene Instanz).

## SNMPv3 für Switches und Access Points

Treiber `snmp` (Klassen `switch` und `accesspoint`), nur lesen. Derzeit ist **kein Switch eingetragen**: Ohne Eintrag gibt es
nichts zu fragen, und ein Eintrag ohne Zugangsdaten in `geraete.env` meldet `nicht_eingerichtet`.

- **Was gelesen wird:** sysName, sysDescr, sysUpTime (je ein GET) und über einen Walk der Schnittstellentabelle ifType und
  ifOperStatus (Zahl der Ethernet-Ports gesamt, oben und unten), auf Wunsch (`fehlerzaehler = true`) die Summen von ifInErrors und
  ifOutErrors. Das Ergebnis heißt `name`, `beschreibung`, `laufzeit_stunden`, `ports_gesamt`, `ports_oben`, `ports_unten` und
  optional `fehler_ein`, `fehler_aus`. Die Büro-Karte zeigt „läuft, 9 von 24 Ports aktiv“; Ports ohne Kabel sind keine Störung.
- **Nur SNMPv3 authPriv:** Anmeldung mit SHA-2 (`auth` = sha224, sha256, sha384, sha512; Standard sha256) und Verschlüsselung mit AES
  (`verschluesselung` = aes128, aes192, aes256; Standard aes128). Es gibt kein SNMPv1, kein v2c (keine Community), kein SHA-1,
  kein MD5, kein DES, kein SET und keine Traps. Die OIDs stehen fest in `treiber/snmp.py` (`ERLAUBTE_OIDS`); eine andere OID weist
  `_lesen()` ab, bevor ein Paket auf das Netz geht. Ein Test prüft, dass der Quelltext keinen Schreibweg und keine v1- oder
  v2c-Namen enthält.
- **Zugangsdaten** nur in `/etc/wamo-geraete/geraete.env` (über `sudo wamo-geraete-env setzen`, Werte über stdin), je Zugang drei
  Werte: `WAMO_GERAETE_<ZUGANG>_BENUTZER`, `WAMO_GERAETE_<ZUGANG>_PASSWORT` (Anmeldung) und `WAMO_GERAETE_<ZUGANG>_PRIVPASSWORT`
  (Verschlüsselung), jede Passphrase mindestens 8 Zeichen. Am Gerät legt der Admin dafür einen eigenen Lese-Benutzer an
  (Sicherheitsstufe authPriv, nur lesen). Die Werte erscheinen nie in Meldungen, Audit oder `repr()`.
- **Ausgang:** `konfig.ziele()` liefert für `snmp` das Ziel `udp <Adresse> 161`. Daraus entstehen die nft-Regel
  `ip daddr <Adresse> udp dport 161 accept` und der Eintrag in `IPAddressAllow` (systemd). Die Unit erlaubt AF_INET schon, das
  Einrichtungsskript meldet die Zahl der SNMP-Ziele. Port 161 ist der einzige erlaubte Port.
- **Beispiel** (auskommentiert, ohne echte Adresse) in `infra/sokrates2/geraete.toml.example`.
- **Bibliothek:** `pysnmp` 7.1.30 (von LeXtudio gepflegte Fortsetzung von pysnmp, Lizenz BSD-2-Clause, monatliche Ausgaben,
  zuletzt 7.1.32 am 07.11.2026), im Lock auf 7.1.30 festgehalten (`uv.lock`, mit Prüfsumme; bewusst nicht die zwei Tage alte
  Ausgabe). Sie hängt nur von `pyasn1` ab, läuft unter Python 3.11 und 3.12 (geprüft) und wird erst beim ersten Abruf geladen.
  Für AES braucht pysnmp `cryptography` ab Version 47 (`hazmat.decrepit`); bei einer älteren Ausgabe meldet pysnmp nur
  „EncryptionError“, der Treiber nennt deshalb die Ursache und `pyproject.toml` verlangt `cryptography>=47`.
- **Getestet:** Auswertung und Fehler mit einer Attrappe (`tests/test_snmp.py`) und die echte Verschlüsselung gegen einen
  Wegwerf-Container mit net-snmp (`tests/test_snmp_container.py`, nur mit `WAMO_SNMP_TESTZIEL`, Anleitung im Kopf der Datei):
  SHA-256 mit AES-128 funktioniert, falsches Passwort, falscher Benutzer und falsches Verfahren werden abgewiesen. AES-192 und
  AES-256 sind nicht gegen ein Gerät geprüft (die Schlüsselableitung unterscheidet sich je Hersteller), SHA-224, SHA-384 und
  SHA-512 ebenso nicht. Gegen einen echten Switch ist nichts getestet.

## Passive Erkennung unbekannter Geräte

Der Dienst liest über TR-064 (Dienst Hosts:1, `X_AVM-DE_GetHostListPath` und die Datei `devicehostlist.lua`, derselbe Weg und
Parser wie bei `router_geraete`) die Hostliste der FRITZ!Box und vergleicht die **aktiven** Einträge mit den Geräten, die er kennt. Es gibt keinen nmap, keinen Portscan und keinen
ARP-Sweep: Er fragt nur den Router, was dieser ohnehin weiß.

- **Bekannt** ist ein Gerät, wenn seine Adresse zu einem Gerät aus `geraete.toml` gehört (oder Sokrates-2 ist), seine MAC als `mac`
  bei einem Gerät steht, sein Name als `mdns_name` bei einem Gerät steht, oder wenn es in `/etc/wamo-geraete/bekannt.toml` steht.
  Diese optionale Datei (Vorlage `infra/sokrates2/bekannt.toml.example`, vom Einrichtungsskript nur angelegt, wenn sie fehlt) nennt
  Geräte, die dem Dienst bewusst unbekannt bleiben, etwa Laptops und Handys: je `[[bekannt]]` ein `mac`, eine `adresse` oder ein `name`
  (Groß- und Kleinschreibung egal, ohne `.fritz.box`), dazu eine freie `notiz`. Unbekannte Schlüssel verhindern den Start.
  Ohne die Datei zählen auch Laptops als unbekannt, der erste Wert wird also größer als null sein, bis die Datei gepflegt ist.
- **`unbekannt_zaehlen`** (read, alle Rollen) liefert nur `anzahl` und `stand` (Zeitpunkt der Messung, UTC), bei zu langer Liste
  zusätzlich `abgeschnitten: true`. **`unbekannte_geraete`**
  (read, nur admin) liefert je Eintrag `ip`, `mac_vorsatz` (die ersten drei Byte), `name` (auf 24 Zeichen gekürzt) und
  `schnittstelle`, höchstens 100 Einträge. Beide gehören zur Grenze „unbekannt“ (12 je Minute und Profil).
- **Zwischenspeicher:** höchstens eine Messung in 60 Sekunden für alle Profile zusammen. Ist die letzte Messung älter (bis 10
  Minuten), bekommt der Aufrufer sie sofort und die neue läuft im Hintergrund, damit die Zahl der Büro-Karte nicht flackert. Nach
  einem Fehler wartet der Dienst 30 Sekunden. Ein Aufrufer, der abbricht, bricht die Messung nicht ab.
- **Last auf der Box:** je Messung zwei Anfragen (Pfad der Hostliste, dann die Datei), unabhängig von der Zahl der Geräte, mit
  Anmeldung (Digest) über HTTPS 49443. Früher war es ein Aufruf je Eintrag (bis 257). Gelesen werden höchstens 256 Einträge (die Box
  merkt sich auch Geräte, die lange nicht da waren); darüber tragen beide Werkzeuge `abgeschnitten: true`, die Zahl ist dann ein
  Mindestwert. Die Datei trägt je Eintrag IP, MAC, Name, Schnittstelle und Aktivität; fehlt oder kippt eine MAC, vergleicht die
  Erkennung weiter nach Adresse und Name. Die Datei darf höchstens 512 KB groß sein (sonst Fehler statt stiller Kürzung).
- **Datenschutz:** Namen und MAC-Adressen sind personenbezogen. Sie stehen nie im Audit, nie im Protokoll des Kerns und nicht im
  `repr()`; die Büro-Karte und alle Rollen sehen nur die Zahl, die Liste sieht nur ein Admin und nur gekürzt, im Kern nur als
  Tabelle auf dem Canvas, nie vorgelesen. Texte von Geräten sind Daten: begrenzt, ohne Steuer- und Klammerzeichen.
- **Einrichtung:** dieselben TR-064-Angaben wie bei `router_geraete` (`tr064_port`, `tls_sha256`, `zugang` am Router, Benutzer
  und Passwort in `geraete.env`). Ohne sie antwortet das Werkzeug `nicht_eingerichtet` und die Karte zeigt nichts. Gegen die echte
  FRITZ!Box ist das nicht getestet (nur gegen eine Attrappe), die Erstprüfung geschieht beim Ausrollen.

## Router-Status über TR-064

Am Router-Eintrag wählt `status_quelle` die Quelle des Status: `"upnp"` (**Standard**, UPnP-IGD ohne Anmeldung, wie bisher) oder
`"tr064"` (lesend über HTTPS 49443 mit Anmeldung und festgehaltenem Zertifikat: `WANCommonInterfaceConfig`
`GetCommonLinkProperties`, `GetTotalBytesSent`, `GetTotalBytesReceived` und `WANIPConnection` `GetStatusInfo`). Die Felder sind
dieselben (`internet`, `leitung`, `zugang`, `hoch_mbit`, `runter_mbit`, `laufzeit_stunden`, `gesendet_gb`, `empfangen_gb`). `tr064`
verlangt `tr064_port`, `tls_sha256` und `zugang`, sonst startet der Dienst nicht. Ob UPnP an der Box abgeschaltet wird, entscheidet
Niko später; bis dahin ändert sich nichts. Nicht abgedeckt ist ein PPPoE-Anschluss (`WANPPPConnection`). Gegen die echte Box ist die
Quelle `tr064` nicht getestet.

## Router schreibend (Entwurf, nicht gebaut)

Der Router bleibt in diesem Dienst **ausschließlich lesbar** (Entscheidung von Niko). Die folgende Beschreibung ist nur ein Entwurf
für später, ohne Code und ohne Zusage. Schreibaktionen kämen als eigenes Werkzeug mit fester Aktionsliste (nie eine freie
SOAP-Durchreichung), Risikoklasse im Dienst geprüft (nicht im Prompt), Karte in der Oberfläche vor jeder Ausführung, Audit ohne
Inhalte, Grenze je Profil und Rückmeldung nach der Wirkung (Zustand erneut lesen).

| Aktion | Risikoklasse | Freigabe |
| --- | --- | --- |
| Gast-WLAN an und aus | change | Karte in der Oberfläche |
| Wake-on-LAN (ein bekanntes Gerät im Netz einschalten) | change | Karte in der Oberfläche |
| Portfreigabe anlegen oder entfernen | critical | Karte und frischer zweiter Faktor |
| Router neu starten | critical | Karte und frischer zweiter Faktor |
| Firmware-Update anstoßen | critical | Karte und frischer zweiter Faktor |
| Passwörter, WLAN-Schlüssel, Sicherungen (Einstellungen exportieren), Telefonbuch, Anrufliste | nie | nicht vorgesehen, auch nicht lesend |

- **Eigener Benutzer mit knappen Rechten:** ein zweiter FRITZ!Box-Benutzer nur für die Schreibaktionen (eigener Zugang in
  `geraete.env`, getrennt vom lesenden Benutzer, damit sich beide einzeln entziehen lassen). Er bekommt nur die Rechte, die die
  gewählten Aktionen brauchen. Die Beschreibung von AVM nennt je Aktion die nötigen Rechte mit den Kürzeln C, A, P, N und H; P
  (Telefonie) und N (NAS) bleiben aus, damit Telefonbuch, Anrufliste und Dateien technisch unerreichbar sind. Welche Aktion welches
  Kürzel verlangt, wird vor dem Bau gegen die AVM-Beschreibung der eingesetzten Firmware geprüft, ebenso die genauen Aktionsnamen.
- **Bestätigung am Gerät:** Bei manchen Aktionen verlangt die FRITZ!Box zusätzlich eine Bestätigung an der Box selbst (Tastendruck).
  WAMO umgeht das nie; die Antwort lautet dann „wartet auf Bestätigung an der Box“, und nach Ablauf der Frist passiert nichts.
- **Reihenfolge, falls gebaut:** zuerst Gast-WLAN, dann Wake-on-LAN, die kritischen Aktionen zuletzt und nur mit frischem zweitem
  Faktor wie beim Live-Bild. Nach Inhalten von außen im selben Zug gilt wie bei Drucken und Steuern immer die Karte.

## Tests

```powershell
cd geraete; uv run pytest -q
cd ..\voice-core; uv run pytest -q test_geraete_client.py test_geraete_werkzeuge.py test_buero_api.py
# nur Server-Status und Konfiguration
cd ..\geraete; uv run pytest -q tests/test_server_status.py tests/test_konfig_text.py
```

SNMP gegen einen echten Agenten (Docker, Wegwerf-Container): siehe den Kopf von `geraete/tests/test_snmp_container.py`.
