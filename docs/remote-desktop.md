# Remote Desktop der Sparks über ein WAMO-Gateway

Konzept vom 31.10.2026 (Basis Commit 86c4d37), umgesetzt. **Stand 10.11.2026:** Das Gateway ist gebaut und auf Sokrates-2 eingerichtet
(Bau ab 31.10.2026, Ende-zu-Ende-Probe am 01.11.2026, Test am Laptop am 04.11.2026). Ziele sind Sokrates-1, Sokrates-2 und Caesar
(RDP über xrdp mit dem Konto `wamo-rd`), der Zugang läuft im Browser über die Oberfläche (Einstellungen, Fernzugriff, nur Admin).
Quellen im Repo: Nutzung und Grenzen `README.md` (Abschnitt „Fernzugriff (Remote Desktop der Sparks)“), Einrichtung und Betrieb
`infra/sokrates2/README.md` (Abschnitt 16), Kern `voice-core/rd_api.py`, Zugangsdienst `infra/sokrates2/wamo-rd/zugang/`, Oberfläche
`ui/src/components/einstellungen/Fernzugriff.tsx`, Rauchtest `infra/sokrates2/wamo_rd_rauchtest.py`, Änderungen `CHANGELOG.md`.
Dieses Dokument bleibt das Konzept mit den Begründungen; wo der gebaute Stand vom Konzept abweicht, steht es im Text.
Alle Aussagen über die Sparks stammen aus lesenden Abfragen vom 31.10.2026 (Beleg jeweils genannt); nichts wurde geändert.
Ziel: Ein Admin sieht und bedient unterwegs vom iPhone (WAMO-App über WireGuard ins Büronetz) den Desktop von Sokrates-1,
Sokrates-2 und Caesar. Gewählt ist Weg A: ein eigenes Gateway auf Sokrates-2, gehärtet wie `wamo-ui`, hinter `wamo-caddy`.
Gegliedert nach den Kernfragen A bis F. Bezeichner, Pfade und Env-Namen stehen in ASCII.

## 0. Kurzfassung

| Frage | Antwort |
| --- | --- |
| A Desktop der Sparks | Alle drei laufen mit GNOME 46 auf **Xorg** (nicht Wayland), ohne Monitor (xrandr: HDMI und USB-C getrennt, virtuell 1920x1200). Auf jedem ist `wamocon` dauerhaft am Bildschirm angemeldet (seat0, tty2). Die Desktop-Freigabe dieser Sitzung geht mit GNOME 46 unter Xorg nicht und ist auch unerwünscht (das Konto hat sudo und docker). Gebraucht wird eine **eigene, getrennte Sitzung** für ein eigenes Konto `wamo-rd`. Bewährt auf dieser Hardware ist nur xrdp (Caesar), `gnome-remote-desktop` im Systemmodus ist ungeprüft. |
| B Gateway | **Apache Guacamole** (Apache-2.0) mit dem Modul "Encrypted JSON authentication": keine eigene Benutzerverwaltung, keine Datenbank, Verbindungen entstehen nur aus einer signierten, kurzlebigen Anweisung. Davor ein kleiner **Zugangsdienst** (Python, im selben Compose) und im Kern ein Modul `rd_api.py`, das die vorhandene Zugangskontrolle nutzt (Admin, aal2, Profil). Eigener Port 55446 hinter `wamo-caddy`, eigene Herkunft (kein iframe). |
| C Unterwegs | Auf Sokrates-2 gibt es **kein WireGuard** (keine Schnittstelle, keine Werkzeuge). WireGuard läuft in der Fritzbox (`infra/sokrates2/README.md`, Abschnitt 6). Das iPhone braucht nur die Fritzbox-Verbindung mit `AllowedIPs = 192.168.178.0/24`. Ob sie schon eingerichtet ist, wurde nicht geprüft. |
| D Zugangsdaten | Ein Konto `wamo-rd` je Spark, ohne sudo, ohne docker, ohne SSH. Passwort zufällig, nur in `/etc/wamo-rd/ziele.env` (0600) im Zugangsdienst, nie im Browser, Repo oder Log, rotierbar mit einem Skript. Kurzlebige Einmalpasswörter sind möglich, kosten aber einen privilegierten Helfer je Spark und stehen erst in Stufe 8. |
| E Sicherheit | Größtes Restrisiko: ein Browser mit gültiger Anmeldung bedient einen Server-Desktop (seit 11.11.2026 ohne frischen Code je Start, Abschnitt 3.6). Gegenmaßnahmen: Admin, aal2 beim Anmelden, optional ein Höchstalter der Anmeldung, Hinweis an alle Admins bei jedem Start, ein Zuschauer je Ziel, Zeitlimit, Inaktivitätsende, kein Zwischenspeicher, keine Aufzeichnung, Quelladresse am Ziel. |
| F Aufwand | Acht Stufen, rund 53 Stunden für Stufe 1 bis 7 (Tabelle in Abschnitt 8). Drei größte Risiken in Abschnitt 9. |

**IQ:** Der Zugang entsteht aus drei getrennten Prüfungen: Kern (wer darf), Zugangsdienst (was genau, wie lange, wohin), Ziel
(nur von der Gateway-Adresse, nur das Konto `wamo-rd`). Kein Teil vertraut dem anderen mehr als nötig.
**EQ:** Der Admin am Handy braucht wenige, eindeutige Schritte (Ziel wählen, Hinweis lesen, Verbinden; ein Code nur, wenn die Anmeldung zu alt ist). Andere Personen
auf den Sparks sehen und merken nichts, weil ihre Sitzungen nie berührt werden. **VQ:** Ein Ablaufbild, Tabellen für Ziele,
Rechte und Stufen, eine Seite "Fernzugriff" im WAMO-Design.
**Synthese:** Erst das leere Gateway bauen und abschotten (Stufe 1), dann ein einziges Ziel (Sokrates-2, Stufe 2), erst danach
Anmeldung und Oberfläche (3, 4), zuletzt die anderen Sparks (5, 6). Jede Stufe hat Rauchtest, Verbotsprobe und Rückweg.

## 1. Ausgangslage (mit Belegen)

| Thema | Befund (lesend, 31.10.2026) | Folge |
| --- | --- | --- |
| Betriebssystem | Alle drei Ubuntu 24.04.4, aarch64, GNOME Shell 46.0, GDM 46.2, NVIDIA GB10 (Treiber 580.159 bis 580.173) | Images und Pakete müssen arm64 sein |
| Sitzungsart | `loginctl`: je Spark Sitzung `wamocon` vom Typ **x11**, Seat `seat0`, tty2, aktiv (Sokrates-1 seit 2 Tagen, Caesar seit 3 Wochen). `61-gdm.rules` wählt für NVIDIA auf diesen Geräten Xorg | Desktop-Freigabe der Konsole (GRD Benutzermodus) braucht Wayland und scheidet aus |
| Bildschirm | `xrandr :1`: HDMI-0, USB-C-0, USB-C-1 getrennt, 1920x1200 virtuell | Kein Monitor, "Remote Login" statt Spiegeln ist der natürliche Fall |
| GRD | `gnome-remote-desktop` 46.3 auf Sokrates-1 und -2 installiert, Systemdienst aktiv und eingeschaltet (`--system`, Benutzer `gnome-remote-desktop`). RDP im Benutzermodus aus (`enable false`, `view-only true`, kein Zertifikat). Auf Caesar Dienst aus. Nichts lauscht auf 3389 | Systemmodus (Remote Login) wäre die Alternative, ist aber unter Xorg und NVIDIA ungeprüft |
| xrdp auf Caesar | 0.9.24 (ESM-Paket), `xorgxrdp`, `tigervnc-standalone-server`. Lauscht auf `*:3389` (auch IPv6). `sesman.ini`: `AllowRootLogin=true`, `AlwaysGroupCheck=false`, `KillDisconnected=false`, kein Limit für Leerlauf. `xrdp.ini`: `security_layer=negotiate`, Zertifikat leer, Zwischenablage und Laufwerke (`cliprdr`, `rdpdr`) an. Aktuell keine xrdp-Sitzung | Jedes lokale Konto (auch `wamocon`) kann sich per Passwort aus dem LAN anmelden. Siehe 2.3 |
| Konten | Auf allen drei: `wamocon` und vier Personenkonten (Moretz, Schefner). SSH nur mit `AllowUsers`, Personenkonten nur aus dem Büro-LAN | Neue Konten wie `wamo-rd` haben automatisch **kein** SSH |
| ufw Sokrates-2 | Erlaubt alles aus 192.168.178.0/24 und aus den ConnectX-Netzen 10.100.104.0/24 und 10.100.105.0/24 | Ein offener Port wäre sofort im ganzen LAN erreichbar |
| ufw Sokrates-1 | Erlaubt alles aus 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 und 169.254.0.0/16 | Zusätzlich jedes Docker-Netz. "Nur vom Gateway" verlangt dort eine **Sperrregel vor** diesen Erlaubnissen |
| ufw Caesar | Nicht lesbar (`sudo` verlangt ein Passwort) | Zustand beim Nutzer erfragen oder in Stufe 6 mit sudo lesen |
| Sokrates-2, Netz | Docker 29.2.1, `ip_forward=1`, Brücken 172.17 bis 172.22 belegt, `wamo-ui-netz` 172.25.0.0/24, `wamo-caddy_default` 172.24.0.0/24. Kein WireGuard | Neues Netz 172.26.0.0/24 und 172.27.0.0/24 sind frei (vor Stufe 1 erneut prüfen) |
| Kern-API | Alle Routen laufen durch `AccessMiddleware`: Token (ES256, JWKS), `aal2`, Höchstalter der Anmeldung, aktives Profil, `Identity.rolle`. Module werden über `ROUTEN_MODULE` in `wamo_api.py` eingehängt | Ein neues Modul `rd_api.py` erbt diese Prüfung ohne eigenen Token-Code |
| Oberfläche | CSP `connect-src` nur Kern und Anmeldung, `form-action 'self'`, `frame-ancestors 'none'`, `X-Frame-Options DENY`, COOP und CORP `same-origin`. Token liegt in `localStorage` oder `sessionStorage`, nicht in Cookies | iframe scheidet aus. Eine andere Herkunft (Port) sieht den Speicher der Oberfläche nie |

## 2. Frage A: Desktop und RDP-Betriebsart

### 2.1 Betriebsarten

| Betriebsart | Funktioniert hier? | Bewertung |
| --- | --- | --- |
| GRD Benutzermodus (Freigabe der angemeldeten Sitzung) | Nein, GNOME 46 teilt nur Wayland-Sitzungen. Die Sparks laufen auf Xorg | Entfällt. Es wäre auch falsch: Das Konto der Konsole hat sudo und docker |
| GRD Systemmodus (Remote Login, neue Sitzung über GDM) | Ungeprüft. Braucht eine Wayland-Sitzung ohne Monitor auf NVIDIA 580 und zwei Anmeldungen (RDP-Konto des Dienstes, dann GDM). Zertifikat und Konto müssten mit `grdctl --system` gesetzt werden | Kandidat G, nur über einen Test auf Sokrates-2 entscheidbar |
| xrdp mit `xorgxrdp` (eigene Xorg-Sitzung je Anmeldung, Display ab :10) | **Ja, auf genau dieser Hardware belegt** (Caesar: Pakete und Dienst laufen, derselbe Treiber 580.173) | Kandidat X. Eine Anmeldung, PAM-Konto, nie die Konsole des Geräts |
| TigerVNC (`Xvnc -localhost`) | Auf Caesar installiert, aber ohne Dienst | Reserve, falls RDP nicht tragen sollte (Guacamole kann VNC) |

**Empfehlung:** Kandidat X (xrdp) als Zielzustand, weil nur er auf dieser Hardware nachweislich läuft. Stufe 2 prüft
zuerst Kandidat G auf Sokrates-2 (halber Tag Begrenzung). Läuft G ohne zweite Anmeldung, ist G vorzuziehen: der Dienst läuft
als unprivilegierter Benutzer, xrdp dagegen mit `xrdp-sesman` als root. Läuft G nicht, gilt X. Die Entscheidung ist reversibel,
das Gateway ändert sich dadurch nicht (nur `security`-Wert und Zertifikatsprüfung in der Verbindung).
**Umgesetzt (Stand 10.11.2026):** xrdp (Kandidat X). Gebaut ist nur xrdp, GRD im Systemmodus wurde nicht erprobt (Beleg:
`infra/sparks/wamo-rd-ziel-einrichten.sh`, Kopfkommentar; `infra/sokrates2/README.md`, Abschnitt 16, Stufe 2).

### 2.2 Mehrere Konten, Sitzungen anderer

- Die Sitzung entsteht nur für `wamo-rd`. Die Konsole von `wamocon` und die SSH-Sitzungen der Personenkonten werden **nie**
  gespiegelt oder übernommen: GRD-Benutzermodus bleibt aus (`enable false` bleibt), xrdp hängt sich nie an `seat0`.
- Anmeldung am Ziel nur für `wamo-rd`: xrdp mit `AlwaysGroupCheck=true` und `TerminalServerUsers=wamo-rd-ts` (Gruppe mit genau
  einem Mitglied), `AllowRootLogin=false`. Alle anderen Konten werden vom RDP-Dienst abgewiesen, auch mit richtigem Passwort.
- Ende der Sitzung am Ziel: `KillDisconnected=true`, `DisconnectedTimeLimit=900`, `IdleTimeLimit=600` (Leerlauf ohne Eingabe).
  Eine vergessene Sitzung lebt also nie länger als 10 bis 25 Minuten.
- Andere Personen auf dem Spark merken die Sitzung höchstens als zusätzlichen Prozess unter `wamo-rd`. Ein Hinweis auf dem Gerät
  ist möglich (Anmeldeskript schreibt eine Zeile ins Journal, Stufe 8), nötig ist er nicht.

### 2.3 Caesar: xrdp heute, Risiko, Empfehlung

| Punkt | Heute | Risiko |
| --- | --- | --- |
| Erreichbarkeit | `*:3389` im LAN offen, nach Angabe des Nutzers ohne Beschränkung | Jedes Gerät im Büro und jeder WireGuard-Client kann Passwörter versuchen |
| Konten | Jedes PAM-Konto, `AllowRootLogin=true` | Passwort-Raten gegen `wamocon` (sudo, docker) oder `root` |
| Schutz gegen Raten | Keiner im xrdp selbst sichtbar | Brute Force ungebremst |
| Version | 0.9.24 (Ubuntu ESM). xrdp hatte 2025 eine Schwachstelle ohne Anmeldung, **CVE-2025-68670** (Pufferüberlauf, behoben ab 0.10.5, Rückportierung 0.9.27 gemeldet), 2026 weitere Meldungen für 0.10.6 und älter | Ob das ESM-Paket gepatcht ist, ist nicht belegt (`apt changelog xrdp` vor jeder Entscheidung prüfen) |
| Dienst läuft als | `xrdp-sesman` als root | Ein Fehler vor der Anmeldung wiegt schwer |
| Kanäle | Zwischenablage und Laufwerke an | Datenabfluss und Einschleusen aus dem Client |

**Empfehlung:** (1) Sofort und unabhängig vom Projekt, mit Rückfrage beim Nutzer: Zugriff auf 3389 am Caesar auf die Gateway-Adresse
beschränken oder xrdp stoppen, solange er nicht gebraucht wird (zurzeit läuft keine xrdp-Sitzung). (2) Zielzustand in Stufe 6:
xrdp bleibt (damit Caesar dieselbe Technik wie die anderen hat), aber gepatcht, mit Gruppenprüfung, ohne Root-Anmeldung, ohne
Laufwerke und Zwischenablage, mit Leerlaufgrenzen und nur von der Gateway-Adresse erreichbar. Abschalten ist die Rückfalloption,
falls die Patchlage nicht beweisbar ist.

### 2.4 Quelladresse: nur vom Gateway

Zwei Varianten, beide in Stufe 2 bis 6 prüfbar:

| Variante | Wirkung | Haken |
| --- | --- | --- |
| 1 (Vorgabe) ufw-Regel am Ziel: erlauben von Gateway-Adresse, **davor** Sperre für 3389 von allen anderen | Port bleibt am LAN sichtbar, antwortet aber nur dem Gateway | Die Quelladresse ist die von Sokrates-2 (Docker setzt die Adresse des Hosts ein). Alles, was auf Sokrates-2 läuft, hat dieselbe Adresse. Besser: **eigene Quelladresse** für das Gateway (zusätzliche LAN-Adresse auf `enP7s7` plus SNAT-Regel nur für das Netz des Gateways). Dafür braucht es eine freie Adresse außerhalb des DHCP-Bereichs der Fritzbox (siehe offene Fragen) |
| 2 (Stärkung) RDP nur auf Loopback des Ziels, Zugang über einen SSH-Tunnel mit eingeschränktem Schlüssel (`restrict,permitopen="127.0.0.1:3389",from=...`) | Es gibt keinen offenen RDP-Port im LAN mehr | Nur für xrdp (GRD lauscht auf allen Adressen). Braucht ein Tunnelkonto je Spark, eine Änderung der `AllowUsers`-Liste und einen Tunnelbaustein im Gateway. Mehr Teile, dafür der stärkste Schutz |

**Empfehlung:** Variante 1 mit eigener Quelladresse für Stufe 2 bis 6. Variante 2 als Option, wenn der Nutzer RDP im LAN
gar nicht mehr sichtbar haben will. Auf Sokrates-2 selbst gilt: Das Ziel liegt auf demselben Host, das Gateway erreicht es über
die Adresse seines Netzes (Regel `von 172.26.0.0/24 nach 3389`), und die ufw-Sperre gilt für alles andere.

## 3. Frage B: Gateway, Einbettung, Datenbank

### 3.1 Wahl des Gateways

| Kandidat | Lizenz | Pflege, Lage | Größe | Handy | Urteil |
| --- | --- | --- | --- | --- | --- |
| **Apache Guacamole** 1.6.x (guacd in C, Webanwendung in Java auf Tomcat) | Apache-2.0 | Apache-Projekt, regelmäßige Versionen. Bekannte Lücke CVE-2024-35164 betrifft nur Terminal-Protokolle (SSH, Telnet), behoben in 1.6.0; ältere Funde 2023 in der RDP-Audioverarbeitung | Web rund 400 bis 600 MB Image, guacd klein. Speicher 512 MB bis 1 GB | Touch als Mauszeiger, Tippen, Rechtsklick durch Zwei-Finger-Tippen, Zoomen, Bildschirmtastatur. Qualität auf iOS muss am Gerät geprüft werden | **Gewählt** |
| Eigener schlanker RDP-nach-WebSocket-Weg | eigene | Ein RDP-Client ist ein Jahr Arbeit und eine Sicherheitsfläche für sich. Nur sinnvoll als Hülle um FreeRDP, das Guacamole (guacd) schon tut | klein | offen | Verworfen |
| noVNC mit websockify | MPL-2.0 und LGPL | Gepflegt, klein, nur VNC. Zielseitig bräuchte jeder Spark einen VNC-Dienst; Touch schwach | sehr klein | schwach | Reserve, falls RDP scheitert |
| KasmVNC | GPL-2.0 | Sitzung und Weboberfläche in einem Paket, Installation je Spark | mittel | gut | Verworfen: ein zusätzlicher Dienst mit eigener Anmeldung auf jedem Spark |
| MeshCentral | Apache-2.0 | Eigene Benutzerverwaltung und Datenbank, Agent auf dem Ziel | groß | mittel | Verworfen: widerspricht "keine eigene Benutzerverwaltung" |

Telemetrie: Guacamole sendet nach bisherigem Kenntnisstand keine Nutzungsdaten nach außen; die Container brauchen keinen
Ausgang ins Internet und bekommen keinen (Netz 4.1). Die Lage der CVEs ist am Tag der Stufe 1 gegen die Sicherheitsseite des
Projekts zu prüfen und im Rauchtest-Protokoll festzuhalten. **Image-Frage:** Ob die offiziellen Images (`guacamole/guacamole`,
`guacamole/guacd`) für arm64 gebaut sind, ist unbelegt (Suche fand nur Fremd-Builds). Stufe 1 prüft das mit
`docker buildx imagetools inspect`; sonst baut ein eigenes Dockerfile aus dem signierten Quellarchiv (Prüfsumme und Signatur
prüfen, Versionen über Digest festhalten, wie bei `caddy`).

### 3.2 Authentifizierung ohne zweites Passwort: Modul "Encrypted JSON authentication"

Guacamole kennt dieses Modul (Schlüssel `JSON_SECRET_KEY`, 128 Bit, `JSON_TRUSTED_NETWORKS` begrenzt die Absender). Eine Anmeldung
besteht aus einem mit HMAC-SHA256 signierten und mit AES-128 verschlüsselten JSON, das `username`, `expires` (Millisekunden) und die
`connections` enthält. Daraus folgt alles Gewünschte:

- Es gibt **keine Benutzerliste, keine Datenbank, keine Erweiterung für JDBC oder LDAP.** Die Webanwendung hat kein Anmeldeformular
  und kein Konto, das man erraten könnte.
- **Nur der Zugangsdienst kennt den Schlüssel.** Nur er kann Verbindungen erzeugen. Ein Nutzer kann kein Ziel, keinen Host, kein
  Protokoll und keinen Benutzernamen wählen. Das Protokoll ist immer `rdp`.
- Die Zugangsdaten des Spark stehen **nie im Browser**: Der Zugangsdienst reicht das JSON selbst bei Guacamole ein
  (`POST /api/tokens` im internen Netz) und gibt dem Browser nur das daraus entstehende Sitzungstoken.
- Zeitlimit hart: Der Zugangsdienst hält das Token und beendet die Sitzung mit `DELETE /api/session/tokens/<token>`, das die
  Tunnel schließt. Ob ein laufender Tunnel beim Löschen sofort stirbt und ob `expires` allein genügt, ist **Prüfpunkt 2.3**
  der Stufe 2. Rückfall: ein kleiner Aufseher auf dem Host (systemd-Zeitgeber nach dem Muster von `wamo-pc-abgleich`) startet `wamo-rd-guacd` neu; der Zugangsdienst selbst bekommt keinen Zugriff auf Docker.

Verbindungsparameter (Auszug, alle vom Zugangsdienst gesetzt): `protocol=rdp`, `hostname` aus der Zielliste, `security=tls` (xrdp) oder
`nla` (GRD), `disable-copy=true`, `disable-paste=true`, `enable-drive=false`, `enable-printing=false`, `enable-audio=false`,
`enable-wallpaper=false`, `resize-method=display-update`, `color-depth=24`, keine `recording-path`, `read-only=false`. Das
Zertifikat des Ziels: xrdp stellt ein selbstsigniertes aus, die WAMO-CA ist auf die Adressen von Sokrates-2 beschränkt (README
Abschnitt 5) und stellt keine für Sokrates-1 oder Caesar aus. Also entweder Fingerabdruck pinnen (falls die Guacamole-Version es kennt,
Prüfpunkt Stufe 2) oder `ignore-cert=true` auf einem Pfad, den nur das Gateway nutzt. Das ist ein bewusst benanntes Restrisiko
(Abschnitt 7).

### 3.3 Einbettung in die WAMO-Oberfläche

```text
Handy (WAMO-App, Safari)                                                          WireGuard (Fritzbox)
  1  Einstellungen > Fernzugriff: Ziel wählen, Hinweis lesen, Verbinden (Code der Authenticator-App nur, wenn der Kern ihn verlangt)
  2  POST https://192.168.178.136:55444/wamo/api/rd/start   (Bearer wie bei allen Kern-Routen, kein neues CORS, keine CSP-Änderung)
        Kern (wamo-core): Token, aal2 mit totp (Code der Anmeldung, Stufe), Profil, Rolle admin, Rate-Grenze, Ziel erlaubt?
        Kern ──Unix-Socket /run/wamo-rd/ctl.sock (SO_PEERCRED wamo-core)──> Zugangsdienst (wamo-rd-zugang)
        Zugangsdienst: ein Zuschauer je Ziel? Sperre setzen, JSON bauen, bei Guacamole einreichen, Token halten, Audit "gestartet"
  3  Antwort: { url: "https://192.168.178.136:55446/#/?token=...", endet, ziel, sitzung }
  4  window.open(url, noopener)  ──> Gateway-Seite (eigene Herkunft, Guacamole-Oberfläche im WAMO-Design)
  5  Browser ──HTTPS 55446──> wamo-caddy ──HTTP, Netz wamo-rd-netz──> wamo-rd-web ──guacd-Protokoll──> wamo-rd-guacd
  6  wamo-rd-guacd ──RDP (TLS), Netz wamo-rd-ziel, Quelladresse nur Gateway──> Spark:3389 (nur Konto wamo-rd, eigene Sitzung)
  7  Ende: Zeitlimit, Leerlauf, "Beenden", Profil nicht mehr aktiv ──> Zugangsdienst löscht Token, gibt Sperre frei, Audit "beendet"
```

- **Cookie, CSP, COOP:** Die Oberfläche (Port 55445) und das Gateway (Port 55446) sind verschiedene Herkünfte. Der Speicher der
  Oberfläche mit dem WAMO-Token ist für die Gateway-Seite unsichtbar, und die Gateway-Seite bekommt nie das WAMO-Token, nur ihr
  eigenes Sitzungstoken. Die Oberfläche braucht **keine** CSP-Änderung (der Aufruf geht an den Kern, der schon in `connect-src`
  steht; das Öffnen der Gateway-Seite ist eine Navigation, kein `fetch`). COOP `same-origin` der Oberfläche stört nicht, weil
  `noopener` ohnehin gilt. Das Gateway bekommt eigene Kopfzeilen in Caddy: CSP (Quelle `'self'`, `connect-src 'self' wss:`, `frame-ancestors 'none'`),
  `Referrer-Policy: no-referrer`, `X-Content-Type-Options`, `X-Frame-Options: DENY`, COOP und CORP `same-origin`, Cookie-Attribute
  `Secure; HttpOnly; SameSite=Strict`, falls Tomcat überhaupt eines setzt. Genaue CSP-Werte klärt der Test in Stufe 1.
- **Anmeldung ohne zweites Passwort:** Es gibt keine. Wer die Seite ohne gültiges Token aufruft, sieht eine leere Seite und kommt
  nirgends hin.
- **WAMO-Design:** Anfangs Guacamole mit einer Branding-Erweiterung (reine CSS- und Bilddateien: Rot `#F40E0E`, Weiß, Schwarz,
  Grautöne, Arial Narrow, WMC-Logo, keine blauen Töne). Später optional eine eigene Seite in der Oberfläche auf Basis von
  `guacamole-common-js` (Apache-2.0), die nur den Bildschirm zeigt und deren Steuerung in WAMO-Komponenten sitzt. Nicht in der ersten Fassung.
- **iPhone als installierte App (Standalone):** Das Öffnen einer fremden Herkunft kann in einem Overlay statt im Hauptfenster erscheinen.
  Prüfpunkt am echten Gerät (Stufe 4), Rückfall: Safari-Tab.

### 3.4 Datenbank des Gateways

Keine. Guacamole läuft ohne JDBC-Modul. Zustand (Sitzungen, Sperren, Token) liegt nur im Speicher des Zugangsdienstes, das
Protokoll in einer Datei (`/var/lib/wamo-rd/audit.jsonl`, Abschnitt 5). Kein Gast-Konto, keine Verbindungsliste in Guacamole.
Die Ziel-Liste steht in `/etc/wamo-rd/ziele.toml` (Namen, Adressen, Anzeigenamen; keine Geheimnisse). Eine Tabelle für das Protokoll
in `wamo_*` ist denkbar, aber erst nötig, wenn die Oberfläche die Historie anzeigen soll (Stufe 8).

### 3.5 Sprachweg: WAMO kennt den Fernzugriff (Nachtrag 02.11.2026)

Der Fernzugriff war zunächst nur ein Knopf in den Einstellungen. Auf die Bitte, die Monitore der Sparks zu zeigen, konnte WAMO deshalb nur antworten, dass er das nicht kenne: Weder die Persona noch ein Werkzeug erwähnten ihn. Jetzt gilt (nur für Admins und nur bei `WAMO_RD_AKTIV=ja`): Die Persona kennt den Fernzugriff (`persona.FERNZUGRIFF_PROMPT`), und das Werkzeug `fernzugriff_oeffnen` schickt die Servernachricht `fernzugriff_oeffnen` an die Oberfläche, die den Abschnitt Fernzugriff öffnet. Die Stimme startet **nie** eine Sitzung: Das Ziel wählt der Nutzer im Dialog, und jeder Start braucht einen echten Klick der Person auf Verbinden (seit KP-2 meist ohne Code, Abschnitt 3.6). Die Nachricht trägt keine Nutzdaten (kein Ziel, keine Adresse, kein Token), und der Handler berührt `rd_api` nie.

### 3.6 Der Code beim Anmelden genügt (KP-2, Nachtrag 11.11.2026)

Entscheidung von Niko am 11.11.2026: Der Code der Anmeldung genügt, ein frischer Code je Start entfällt (frühere Frage 4 in Abschnitt 10).
Bis dahin verlangte jeder Start einen Code der Authenticator-App, der höchstens 120 Sekunden alt war und nur für einen Start galt. Umgesetzt
in `voice-core/rd_api.py` (Funktion `anmeldung_genuegt`), Migration `0019_rd_anmeldung_genuegt.sql` und dem Dialog der Oberfläche.

**Was jetzt gilt.**

| Stelle | Regel |
| --- | --- |
| Kern | Token mit aal2 und einem totp-Eintrag im Claim `amr` (Prüfung der Zugangskontrolle samt Höchstalter `WAMO_ANMELDUNG_MAX_STUNDEN`, Standard 168 Stunden, höchstens 720), Rolle admin, aktives Profil. Ohne totp, mit aal1 oder mit einer Zeit aus der Zukunft bleibt es bei 403 und dem Code `zweiter_faktor_zu_alt` |
| Stufenschalter | `WAMO_RD_ANMELDUNG_MAX_MIN` (ganze Zahl 1 bis 43200): Ist der Code der Anmeldung älter, lehnt der Kern mit 403 und `zweiter_faktor_zu_alt` ab, und der Dialog zeigt das Codefeld (Satz „Deine Anmeldung ist älter als N Stunden“). Leer oder 0 heißt aus (Standard). **Jeder andere Wert, auch ein Tippfehler, gilt als strengste Stufe** (frischer Code bei jedem Start wie vor KP-2) und schreibt eine Fehlerzeile ins Journal: Ein Fehler lockert den Fernzugriff nie. Der Kern entscheidet, nicht die Oberfläche |
| Datenbank | Migration 0019 ersetzt nur `rd_sitzung_beginnen`: Die Sperren „Code höchstens 120 Sekunden alt“ und „ein Code, ein Start“ entfallen (ohne die zweite scheiterte der zweite Start nach derselben Anmeldung), neu ist die Obergrenze von 31 Tagen für die Zeit des Codes. Signatur, Rechte und Prüfungen (Admin, Zukunft, Raten, ein Zuschauer je Ziel) bleiben |
| Oberfläche | „Verbinden“ startet ohne Code. Das Codefeld erscheint nur, wenn der Kern den Code verlangt |
| Wettlauf | Ohne „ein Code, ein Start“ kann ein zweiter Start die Datenbankzeile des ersten ablösen, bevor dieser am Gateway angemeldet hat. Das Beenden am Gateway liefe dann ins Leere, und der erste Start legte danach eine Gateway-Sitzung ohne offene Zeile an (zwei Zuschauer auf einem Ziel, für den Aufseher unsichtbar). Gegenmaßnahme: ein Lock je Profil um Datenbank und Gateway, und nach dem Anmelden prüft der Kern die Datenbankzeile noch einmal und beendet die Gateway-Sitzung sofort, wenn sie abgelöst wurde. Die zweite Prüfung läuft über die Datenbank und gilt damit auch für zwei Kerne (Slot a und b) |
| Sprachweg | Das Werkzeug `fernzugriff_oeffnen` zeigt nur den Abschnitt. Das Codefeld war früher auch die Schranke gegen ein Öffnen durch das Modell; jetzt ist es der echte Klick der Person: Der Fokus liegt beim Öffnen des Dialogs nie auf „Verbinden“ |

Unverändert bleiben Rolle admin, aktives Profil, ein Zuschauer je Ziel, 3 Sitzungen gleichzeitig, 6 Starts je Stunde, 60 Minuten, 10 Minuten
Leerlauf, die Verbindung (RDP, keine Zwischenablage, kein Laufwerk, kein Ton, keine Aufzeichnung) und die Raten der Routen. Mitglieder und
Laborprofile kommen weiterhin nicht heran. Eigenen frischen Code behalten die Release-Freigabe (Migration 0013, 60 Sekunden), die
Geräteverwaltung (Migration 0014, 120 Sekunden, `voice-core/geraete_admin_api.py`) und die Fernanfrage des Live-Bilds.

**Neue Fähigkeit und Restrisiko (Befund M1).** Ein gestohlenes Sitzungstoken ist dadurch mächtiger als vorher. Bisher half ein Token allein
nicht: Jeder Start brauchte zusätzlich einen frischen Code aus der Authenticator-App. Jetzt startet das Token allein eine Sitzung auf einem
Spark-Desktop, bis die Anmeldung abläuft: im Kern nach `WAMO_ANMELDUNG_MAX_STUNDEN` (Standard 168 Stunden), in der Oberfläche im Browser nach
24 Stunden (Komfortgrenze der Oberfläche, kein Schutz des Kerns), in der Desktop-App nach 7 Tagen. Dieses Restrisiko bleibt, es ist
ausdrücklich akzeptiert und wird nicht beseitigt, sondern begrenzt und sichtbar gemacht:

1. Die harten Grenzen bleiben (Admin, ein Zuschauer, 60 Minuten, 10 Minuten Leerlauf, nichts aufgezeichnet, Raten, `WAMO_RD_AKTIV` als Notaus).
2. Der Stufenschalter begrenzt das Alter der Anmeldung nur für den Fernzugriff, zum Beispiel auf 1440 Minuten (24 Stunden), mit dem Codefeld als
   Rückfall. Er ist im Standard aus; wer ein Netz will, schaltet ihn zu (`wamo-core-env`, ohne Release).
3. **Hinweis an alle Admins bei jedem Start (KP-2b).** Die Datenbankfunktion schreibt in derselben Transaktion wie die Sitzungszeile einen Hinweis
   (Tabelle `hinweise`, art `system`, Titel „Fernzugriff gestartet: <Ziel>“) an alle aktiven Admins, auch an die startende Person. Der Text nennt die
   Person, den Kurznamen des Ziels, das Alter des Codes und die Höchstdauer, nie eine Adresse, ein Konto oder Zugangsdaten. Die Oberfläche zeigt ihn
   wie die Hinweise der Geräteverwaltung (Karte in der Präsenzspalte, Ansage als Toast, Liste unter Einstellungen, Info). Ohne den Hinweis kommt kein
   Start zustande, denn beides geschieht in einer Transaktion.

Grenzen dieser Erkennung, ehrlich: Es ist eine Erkennung, kein Schutz. Die Oberfläche fragt die Hinweise jede Minute ab. Hat WAMO nur einen
Admin, bekommt nur dieses Profil den Hinweis, und ein Angreifer mit dem Token der Person kann ihn selbst als gelesen markieren. Dann bleibt das
Protokoll in `rd_sitzungen` (wer, Ziel, Start, Ende, Grund, Zeit des Codes). Bei Verdacht: sofort ein anderer Admin sperrt das Profil (das beendet
die Sitzungen) und setzt `WAMO_RD_AKTIV=nein`. Das Abmelden in der Oberfläche gilt nur für das eigene Gerät und macht ein gestohlenes Token nicht ungültig.
Der Hinweis entsteht beim Start in der Datenbank. Scheitert danach die Anmeldung am Gateway, bleibt er als Fehlalarm stehen (höchstens 6 je Stunde und Profil).
**Reihenfolge und Rückweg.** Kern vor Migration, Oberfläche zuletzt. Ein neuer Kern mit der alten Funktion bekommt für den Code der Anmeldung die
Antwort 23514, der Dialog fragt den Code ab: sicher, nur unbequem. Ein alter Kern mit der neuen Funktion ist nicht sicher genug: Er kennt weder die
Startsperre je Profil noch die Nachprüfung der Datenbankzeile und verließ sich auf „ein Code, ein Start“. Darum wird die Migration nie vor dem Kern
angewendet. Rückweg: Oberfläche zurück, Kern über `rueckweg-kern`, dann `0019_rd_anmeldung_genuegt.down.sql` (stellt die Funktion aus 0015
wortgleich wieder her, keine Daten gehen verloren). Der Dialog behält das Codefeld als Rückfall.

## 4. Frage C: Zugang unterwegs (WireGuard)

| Punkt | Befund | Folge |
| --- | --- | --- |
| Sokrates-2 | Keine WireGuard-Schnittstelle, keine `wg`-Werkzeuge, `ip_forward=1` (Docker), Standardroute über die Fritzbox 192.168.178.1 | Nichts auf Sokrates-2 ändern, WireGuard dort nicht aufsetzen |
| Fritzbox | README und Firewall-Doku nennen "WireGuard-Clients der Fritzbox" im Büro-LAN (192.168.178.0/24). Zustand der Fritzbox wurde **nicht** gelesen | Nutzer prüft im Fritzbox-Menü, ob "WireGuard" für das Handy eingerichtet ist |
| Firewall | `WAMO-FW-LAN` lässt 192.168.178.0/24 zu, damit auch jeden WireGuard-Client | Der VPN-Zugang ist so vertrauenswürdig wie das Büro-LAN. Darum zählen Admin und der zweite Faktor der Anmeldung im Gateway |
| Erreichbarkeit der Fritzbox von außen | Offen: öffentliche IPv4 oder IPv6, MyFRITZ-Name, keine Doppel-NAT (DS-Lite) | Entscheidung beim Nutzer, prüfbar nur an der Fritzbox |

**Anleitung iPhone (ohne dass hier Schlüssel entstehen):**

1. In der Fritzbox unter Heimnetz oder Internet, Freigaben, VPN (WireGuard) eine Verbindung für "Einzelnes Gerät" anlegen. Die
   Fritzbox erzeugt die Schlüssel selbst und zeigt einen QR-Code und eine Konfigurationsdatei an. Beides bleibt beim Nutzer.
2. App "WireGuard" aus dem App Store installieren, "Tunnel hinzufügen", "QR-Code scannen" (Kamera auf den QR-Code der Fritzbox).
3. Im Tunnel prüfen: `Allowed IPs` ist nur das Büronetz (`192.168.178.0/24`), nicht `0.0.0.0/0`. So läuft nur der Bürozugriff durch
   den Tunnel, alles andere geht direkt.
4. Tunnel einschalten, in Safari `https://192.168.178.136:55445` öffnen (die WAMO-CA ist laut README bereits installiert und
   vertraut; sie gilt für alle Ports der Adresse, auch 55446).
5. Probe unterwegs: Mobilfunk, WLAN aus, Tunnel an, WAMO öffnen, anmelden. Danach erst "Fernzugriff" testen.

## 5. Frage D: Zugangsdaten zu den Sparks

### 5.1 Entwurf

| Baustein | Festlegung |
| --- | --- |
| Konto | `wamo-rd` je Spark (nicht `wamocon`). Normales Konto mit Home `/home/wamo-rd` (0700), Shell `bash` (die Desktopsitzung braucht eine), **keine** Gruppen außer der Hauptgruppe, `wamo-rd-ts` (nur für xrdp) und, falls nötig, `render`/`video`. Nicht in `sudo`, `docker`, `adm`, `ollama`, `lpadmin`. SSH: nicht in `AllowUsers`, also gesperrt. |
| Passwort | 32 Zeichen zufällig, je Spark ein eigenes. Erzeugt von `wamo-rd-konto-einrichten.sh` (Option `--rotieren`) auf dem Ziel (über `chpasswd`, ohne Ausgabe), dem Zugangsdienst übergeben über stdin in die Datei `/etc/wamo-rd/ziele.env` (Besitzer `root:wamo-rd`, 0640, nur im Container `wamo-rd-zugang` eingehängt). Wie `wamo-geraete-env setzen`. Nie im Repo, nie im Log, nie in `docker inspect` (kein Env-Eintrag, sondern Datei). |
| Rotation | Auf Anforderung und planmäßig alle 90 Tage, außerdem sofort nach jedem Verdacht. Das Skript schreibt nur Namen und Zeit ins Audit, nie den Wert. |
| Einmalpasswort (Stufe 8, optional) | Je Sitzung ein neues Passwort, das am Ziel nur 120 Sekunden lang gilt. Braucht einen privilegierten Helfer am Ziel (`sudoers` auf genau einen Befehl) und einen Weg dorthin (SSH-Schlüssel mit `command=`). Zusätzliche Angriffsfläche für kleinen Gewinn, solange das Konto ohnehin nur aus der Gateway-Adresse erreichbar ist. Darum nicht Teil des ersten Wurfs. |
| Anzeige | Das Passwort erscheint nie in Antworten, Fehlertexten oder im Audit. Guacamole bekommt es nur im JSON, das der Zugangsdienst selbst einreicht. |

### 5.2 Folgen: was darf `wamo-rd` auf dem Spark

- Es darf, was ein normales Konto ohne sudo darf: einen Desktop bedienen, Programme starten (Browser, Terminal), im eigenen Home
  schreiben, **Dateien lesen, die für alle lesbar sind** (unter Ubuntu 24.04 sind neue Homes standardmäßig 0750; am Ziel zu prüfen).
- Es darf **nicht**: Container steuern (docker), root werden, die Modelle verwalten, in fremde Homes sehen, die Konsole von
  `wamocon` sehen, per SSH zu anderen Rechnern, soweit dort nur Schlüssel von Personen gelten.
- Es darf von dort aus das Netz erreichen (LAN und ConnectX). Das ist die größte Folge. Maßnahme in Stufe 8 oder früher: nft-Regel
  `meta skuid wamo-rd` am Ziel, die nur Internet (80, 443, DNS) und die eigene Adresse zulässt, nicht das LAN und nicht die
  anderen Sparks. Entscheidung des Nutzers (offene Fragen), weil sie das Nutzen von LAN-Diensten aus der Sitzung verbietet.
- **Es ist kein Ersatz für `wamocon`.** Wer am Spark administrieren muss, nutzt SSH. Falls der Nutzer Administration im
  Desktop will, ist das eine eigene Entscheidung mit eigenem Risikoprofil (Rolle, Freigabe, kein dauerhaftes Passwort) und nicht Teil dieses Konzepts.

## 6. Zielzustand der Bausteine (Namensvertrag-Vorschlag)

| Bereich | Wert |
| --- | --- |
| Compose-Projekt, Ordner | `wamo-rd`, auf dem Server `/home/wamocon/wamo-rd` |
| Container | `wamo-rd-web` (Guacamole), `wamo-rd-guacd`, `wamo-rd-zugang` (Python) |
| Härtung, alle drei | wie `wamo-ui`: Benutzer ohne Root, `read_only`, `cap_drop: ALL`, `no-new-privileges`, `mem_limit` (web 768 MB, guacd 256 MB, zugang 128 MB), `cpus`, `pids_limit`, `tmpfs` mit `noexec`, Healthcheck, `json-file` mit Größengrenze, `pull_policy: never`, Images über Digest |
| Netze | `wamo-rd-netz` (172.26.0.0/24, `internal`, ohne Bridge-Adresse): Caddy, web, zugang. `wamo-rd-guacd-netz` (172.26.1.0/29, `internal`): nur web und guacd, guacd ist so für Caddy unerreichbar. `wamo-rd-ziel` (172.27.0.0/24, **nicht** `internal`, Ausgang nur zu den Zielen aus `ziele.toml`, nur TCP 3389): guacd und für dessen Probe der Zugangsdienst (Beleg: `infra/sokrates2/wamo-rd/docker-compose.yml`) |
| Port und Site | 55446 (HTTPS) an `wamo-caddy`, eigene Datei `kern/wamo-rd.caddy`, Aufruf Methoden GET, HEAD, POST nur wo Guacamole es braucht, WebSocket durchgereicht |
| Firewall | `wamo-firewall.sh`: 55446 in `WAMO-FW-LAN` (Loopback und Büro-LAN, damit auch WireGuard-Clients), neue Kette `WAMO-FW-RD` für das Netz 172.27.0.0/24 (erlaubt nur Ziel-Adressen:3389, alles andere verworfen und gezählt) |
| Zugangsdienst | Unix-Socket `/run/wamo-rd/ctl.sock` (Gruppe `wamo-core`), `SO_PEERCRED`, kein Netzport |
| Dateien | `/etc/wamo-rd/ziele.toml`, `/etc/wamo-rd/ziele.env`, `/etc/wamo-rd/json.key` (alle 0600/0640), `/var/lib/wamo-rd/audit.jsonl` |
| Kern | `voice-core/rd_api.py` (Routen `/wamo/api/rd/ziele`, `/start`, `/ende`), Eintrag in `ROUTEN_MODULE`, Env `WAMO_RD_SOCKET`, `WAMO_RD_AKTIV` (Standard `nein`) |
| Oberfläche | Abschnitt `fernzugriff` in `ui/src/lib/einstellungenAbschnitte.ts`, `ui/src/components/einstellungen/Fernzugriff.tsx` (nur Rolle admin sichtbar), `ui/src/lib/fernzugriff.ts` |
| Ziele am Spark | Konto `wamo-rd`, Gruppe `wamo-rd-ts`, `/etc/xrdp/` (Konfiguration), Skripte `infra/sparks/wamo-rd-konto-einrichten.sh` (Konto, Passwort, Rotation, Rückweg), `wamo-rd-ziel-einrichten.sh` (xrdp als Ziel, `--zurueck`), `wamo-rd-ausgang-einrichten.sh` (Ausgangsregel), `xrdp-einhegen.sh` und `wamo-sudo-einrichten.sh` |

Der Compose-Aufruf für Port 55446 legt `wamo-caddy` einmal neu an (wie beim ersten `wamo-ui`): 55443 bis 55445 sind dann wenige
Sekunden weg. Nur ohne laufende Sitzung (gleiche Prüfung wie `wamo_ui_sokrates2.py`).

## 7. Frage E: Sicherheitsbewertung

| Bedrohung | Weg | Gegenmaßnahme | Rest |
| --- | --- | --- | --- |
| Gateway als Einstieg ins Netz | Fehler in Guacamole, Tomcat oder guacd, dann Sprung nach 3389 an drei Rechnern | Dienste ohne Internet, `read_only`, ohne Capabilities, Netz `wamo-rd-ziel` nur zu drei Adressen und einem Port, Quelladresse am Ziel, nur JSON-Anmeldungen vom Zugangsdienst (`JSON_TRUSTED_NETWORKS`), Aktualisierungsplan | Ein Fehler in guacd (C-Code) bleibt das größte technische Risiko. Eine Pufferlücke gibt dem Angreifer die Rechte des Containers, nicht die des Hosts |
| Sitzungsübernahme | Gestohlenes Gateway-Token | Token nur im Speicher des Zugangsdienstes und im Tab, gültig bis Ende der Sitzung, ein Zuschauer je Ziel, `Referrer-Policy: no-referrer`, kein Cookie | Wer den Tab hat, hat die Sitzung. Bildschirmsperre des Handys ist die Grenze |
| Tastatur und Zwischenablage | Abfluss von Passwörtern ins Handy oder Einschleusen | Zwischenablage in beide Richtungen aus (`disable-copy`, `disable-paste`), keine Laufwerke, kein Druck, keine Aufzeichnung | Auf dem Bildschirm Sichtbares ist per Screenshot am Handy kopierbar |
| Bildschirminhalte anderer Personen | Spiegeln der Konsole oder fremder Sitzungen | Eigene Sitzung für `wamo-rd`, Benutzermodus von GRD bleibt aus, Anmeldung nur für `wamo-rd-ts` | Eigene Sitzung kann Dateien der Gemeinschaft lesen, die für alle lesbar sind |
| Brute Force | Passwort von `wamo-rd` raten | Ziel nur vom Gateway erreichbar, Passwort 32 Zeichen, Gateway-Eingang nur nach Kern (Token und Code), Rate-Grenzen (3 Fehler in 10 Minuten, dann 15 Minuten Sperre je Profil und global) | Wer im Büro-LAN die Quelladresse fälscht, könnte Pakete senden (ARP). Eigene Quelladresse und Variante 2 aus 2.4 senken das |
| Replay | Altes Token oder altes JSON | JSON mit `expires` unter 60 Sekunden und nur einmal eingereicht, Token wird bei Ende gelöscht, Kern nimmt einen Start nur mit dem Code der Anmeldung an (aal2 mit totp, optional Höchstalter) | Kein Rest bekannt |
| Unbefugter Admin | Gestohlenes Handy, eingeloggt, VPN an | Code der Anmeldung (aal2), optional Höchstalter (Stufenschalter), Hinweis an alle Admins bei jedem Start, Profil nur `admin`, SessionGuard prüft Profil weiter, Sitzung endet bei Rollenwechsel | Seit 11.11.2026 genügt ein gestohlenes Token allein, bis die Anmeldung abläuft (Abschnitt 3.6). Wer das Handy mit angemeldeter App hat, kommt hinein |
| Zertifikat am Ziel | MITM zwischen Gateway und Spark im LAN | Pinning wenn möglich, Quelladresse, getrennte Brücken | `ignore-cert` ist ein Rest, solange nicht gepinnt wird |
| Protokolle | Datenschutz | Nur wer, wann, welcher Spark, Dauer, Grund, Quell-IP des Handys im Büro-LAN. **Keine** Bildschirminhalte, keine Eingaben, keine Aufzeichnung | |

**Auflagen wie beim Live-Bild:** keine Aufzeichnung (Guacamole-Aufzeichnung und `recording-path` bleiben ungesetzt, per Test
belegt), vor jedem Start ein Hinweisfenster (Text: "Du steuerst den Desktop von Sokrates-X. Es wird nichts aufgezeichnet. Wer,
wann und wie lange wird protokolliert. Der Zugriff gilt nur dir, endet nach 60 Minuten oder 10 Minuten ohne Eingabe."),
nur Rolle admin, hartes Zeitlimit, ein Zuschauer je Ziel. Aufbewahrung des Protokolls 90 Tage, danach Löschen durch den Zugangsdienst.

**Ein Zuschauer je Ziel, begründet:** Zwei Admins in derselben Sitzung stören sich, und zwei Personen auf einem Konto
`wamo-rd` machten das Protokoll unscharf. Der zweite Admin sieht "belegt von <Name> seit <Zeit>" und kann
warten. Beenden durch Dritte gibt es nicht. Ausnahme später möglich: Mitschauen (nur ansehen), nicht jetzt.

**Limits:** Sitzung höchstens 60 Minuten (Konzept: Verlängerung um 30 Minuten per Knopf, höchstens 4 Stunden insgesamt, Warnung
2 Minuten vor Ende; **so nicht gebaut**: Kern und Oberfläche kennen nur die feste Höchstdauer `DAUER_SECS` von 60 Minuten und die
Restzeit, kein Verlängern und keine Warnung, der Zugangsdienst begrenzt eine Anweisung auf höchstens 4 Stunden). Leerlauf 10 Minuten (am Ziel durch `IdleTimeLimit`). Höchstens 6 Starts je Stunde und Profil, drei Sitzungen gleichzeitig im Gateway.

## 8. Frage F: Stufenplan

Reihenfolge: 0 (optional) | 1 | 2 | 3, 5, 6 (3 und 4 in Reihe; 5 und 6 können parallel zu 3 und 4 laufen) | 4 | 7.
Alle Änderungen auf Servern nur nach Rückfrage und nach der Vorprüfung, die jedes Skript des Repos schon kennt (Ablageort,
Rechte, keine laufende Sitzung).

| Stufe | Ziel | Dateien (neu oder geändert) | Rauchtest und Verbotsprobe | Rückweg | Std | Hängt ab von |
| --- | --- | --- | --- | --- | --- | --- |
| 0 (optional, unabhängig) | Caesar: Offener RDP-Port sofort schließen | keine im Repo; Handarbeit am Ziel (ufw oder `systemctl stop xrdp`) | Von einem LAN-Gerät: Verbindung auf 3389 scheitert; xrdp ohne Sitzung gestoppt | `systemctl start xrdp` | 1 | Zustimmung des Nutzers |
| 1 | Gateway-Container und Firewall **ohne Ziel**: Images (arm64), Netze, Caddy-Site 55446, Härtung, Kette `WAMO-FW-RD`, leere Zielliste | `infra/sokrates2/wamo-rd/` (Dockerfile oder Image-Pin, `docker-compose.yml`, `wamo-rd.caddy.vorlage`), `wamo-rd-einrichten.sh`, `wamo-rd-rueckweg.sh`, `wamo_rd_rauchtest.py`, Änderung `wamo-firewall.sh`, `wamo-caddy/docker-compose.yml`, `CLAUDE.md` (Namensvertrag) | Rauch: Container gesund, 55446 antwortet aus dem LAN mit Gateway-Seite ohne Konten; Header geprüft. Verbot: von Container `wamo-rd-web` kein Ausgang ins Internet, nicht zum Host, nicht ins LAN; `wamo-rd-guacd` erreicht nur Sparks:3389; Anfrage ohne gültiges JSON gibt keinen Zugang; Port 55446 aus 10.100.x und aus fremden Netzen verworfen; `JSON_TRUSTED_NETWORKS` weist fremde Absender ab | `wamo-rd-rueckweg.sh` (Container, Site, Regeln weg; Port bleibt im Compose, ohne Site lauscht Caddy dort nicht) | 8 | keine |
| 2 | **Ein Ziel: Sokrates-2.** Konto `wamo-rd`, Kandidat G oder X entscheiden, Quelladresse, Passwort, Klärung der Prüfpunkte (2.1 GRD Remote Login oder xrdp; 2.2 Zertifikat pinnen; 2.3 Token löschen beendet den Tunnel; 2.4 `expires` und Leerlauf) | `infra/sparks/wamo-rd-konto-einrichten.sh` (Konto, Rotation und Rückweg), `wamo-rd-ziel-einrichten.sh` (xrdp, Rückweg `--zurueck`), `wamo-rd-ausgang-einrichten.sh`, `infra/sokrates2/wamo-rd/ziele.toml.beispiel`; Prüfskript im Gateway-Container (nur lesend, Testverbindung ohne Oberfläche: `infra/sokrates2/wamo-rd/zugang/pruefe_gegen_guacamole.py`) | Rauch: Testverbindung des Gateways öffnet Desktop von `wamo-rd` auf Sokrates-2, Bild kommt, Maus und Tastatur wirken. Verbot: anderes Konto (`wamocon`, Personenkonto) wird abgewiesen; `wamo-rd` hat kein sudo, kein SSH, keine Gruppe `docker`; 3389 von einem anderen LAN-Gerät verworfen; kein Zugriff auf `/home/wamocon`; Konsole von `wamocon` nie sichtbar | Konto sperren, Dienst aus (GRD `--system ... disable` oder `systemctl disable --now xrdp`), ufw-Regeln zurück | 8 | 1 |
| 3 | Anmeldung über WAMO: Kernmodul, Zugangsdienst, Sperre je Ziel, Limits, Audit, frischer Zweitfaktor | `voice-core/rd_api.py`, `voice-core/test_rd_api.py`, `voice-core/wamo_api.py` (`ROUTEN_MODULE`), `infra/sokrates2/wamo-rd/zugang/` (Python, Tests), `/etc/wamo-rd/*` Anlage im Einrichtungsskript | Rauch: Admin mit frischem Code erhält URL, Sitzung zeigt Spark-Desktop. Verbot: `mitglied` 403; ohne aal2 403; Zweitfaktor älter als 120 s abgewiesen (bis KP-2, danach Abschnitt 3.6); zweiter Start auf dasselbe Ziel 409; Ende nach Zeitlimit und nach Leerlauf; Profil deaktiviert beendet Sitzung; Audit enthält keine Bildschirmdaten und kein Passwort (Grep); Fehlversuche sperren; JSON zweimal einreichen scheitert | `WAMO_RD_AKTIV=nein` in der Unit, `systemctl restart wamo-core`; Gateway bleibt ohne Zugang | 14 | 1, 2 |
| 4 | Oberfläche: Abschnitt "Fernzugriff", Hinweisfenster, Start und Ende, Branding, Test am iPhone (Touch, Scrollen, Tastatur, Standalone) | `ui/src/components/einstellungen/Fernzugriff.tsx`, `ui/src/lib/fernzugriff.ts`, `ui/src/lib/einstellungenAbschnitte.ts`, Erweiterung `WAMO Eingabe` (Tastaturleiste, Stile im WAMO-Design) unter `infra/sokrates2/wamo-rd/erweiterung/` statt eines Ordners `branding/`, Tests der Oberfläche | Rauch: iPhone, Mobilfunk, WireGuard an, Spark-Desktop sichtbar, Eingabe möglich, Beenden wirkt. Verbot: `mitglied` sieht den Abschnitt nicht und der Aufruf scheitert im Kern; ohne WireGuard nichts erreichbar; keine Zwischenablage; Gateway-Seite ohne Token leer | Abschnitt per Merkmal ausblenden, `wamo-ui` zurückschalten (`zurueck`, 1 Sekunde) | 12 | 3 |
| 5 | Sokrates-1 als Ziel (gleiche Technik wie Stufe 2) | `/etc/wamo-rd/ziele.toml` ergänzen, ufw-Sperre vor den breiten Erlaubnissen (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) | Wie Stufe 2 für Sokrates-1; Verbot: 3389 aus den ConnectX-Netzen verworfen | `wamo-rd-ziel-einrichten.sh --zurueck` am Ziel | 4 | 2 (parallel zu 3) |
| 6 | Caesar: xrdp einhegen (Patch, Gruppenprüfung, kein Root, keine Kanäle, Limits, nur Gateway) und als Ziel aufnehmen | `infra/sparks/xrdp-einhegen.sh` (statt eines Zweigs in `wamo-rd-einrichten.sh` und eines `xrdp-haerten.conf`-Beispiels) | Wie Stufe 2. Verbot: `wamocon` und `root` per RDP abgewiesen; `security_layer` ohne Rückfall auf Standard-RDP; Version geprüft | Alte Konfiguration aus der Sicherung, oder xrdp aus | 4 | 2 |
| 7 | Abnahme am iPhone unterwegs und Übergabe (Anleitung WireGuard, README, Betriebs- und Rückwegkapitel) | `infra/sokrates2/README.md` (neuer Abschnitt), `README.md`, `CHANGELOG.md` | Vollständiger Lauf draußen: VPN, Anmeldung, Code, Spark wählen, arbeiten, beenden, Protokoll prüfen | wie Stufen 1 bis 4 | 3 | 4, 5, 6 |
| 8 (optional) | Härtung: Einmalpasswort je Sitzung, Ausgangsfilter für `wamo-rd` am Ziel, Verlauf in der Oberfläche, Hinweis am Gerät | je nach Entscheidung | nach Umsetzung | je Baustein | 6 | 7 |

Gesamtaufwand Stufe 1 bis 7: **53 Stunden** (8 + 8 + 14 + 12 + 4 + 4 + 3). Mit Stufe 0 und 8 rund 60 Stunden. Stufe 2 trägt die
größte Unsicherheit (Kandidat G oder X, Prüfpunkte); danach sind 3 bis 7 gut planbar.

Jede Stufe ist ein eigener PR, mit `verification-loop` (Typen, Lint, Tests, Secret-Scan) und dem Agenten `security-reviewer` vor
dem Einspielen. Vor Stufe 3 zusätzlich `python-reviewer`, vor Stufe 4 `react-reviewer`. Jede Stufe endet mit einem Satz im
Änderungsprotokoll und ohne Verweise auf andere Systeme.

## 9. Die drei größten Risiken

1. **Technische Machbarkeit am Ziel.** GNOME 46 läuft auf allen Sparks unter Xorg ohne Monitor. Remote Login über GRD ist dort
   ungeprüft, xrdp ist bewährt, aber als root laufender Dienst mit Vorfällen vor der Anmeldung. Stufe 2 klärt es, mit festem
   Zeitrahmen und Rückfall (xrdp, danach VNC).
2. **Das Gateway als Einstieg ins Büronetz.** Ein Fehler in guacd oder Tomcat gibt Zugriff auf einen Container, der RDP-Ziele
   erreicht. Härtung, getrennte Netze, ein Ausgang nur zu drei Adressen und regelmäßige Aktualisierung senken es, ein Rest bleibt.
   Ein Plan für Aktualisierungen (Digest-Wechsel, Rauchtest, Rückweg) gehört in Stufe 1, nicht später.
3. **Handy und Bedienbarkeit.** Ein entsperrtes Handy mit WireGuard und angemeldeter App öffnet Server-Desktops; der Code der Anmeldung
   und der Hinweis an alle Admins mindern das, heben es nicht auf (Abschnitt 3.6). Dazu ist die Touchbedienung eines Desktops auf einem iPhone mühsam (kleine Ziele, Tastatur
   verdeckt den Bildschirm). Wenn Stufe 4 am echten Gerät enttäuscht, bleibt der Nutzen klein, bevor Stufe 5 und 6 gelaufen sind.
   Darum Stufe 4 vor 5 und 6 und ein Test am iPhone schon nach Stufe 2 mit einem Behelfsaufruf.

## 10. Offene Fragen an den Nutzer (echte Entscheidungen)

1. **Eigene Quelladresse für das Gateway?** Eine freie LAN-Adresse außerhalb des DHCP-Bereichs der Fritzbox (z. B. 192.168.178.2xx),
   damit die Sperre an den Zielen nur das Gateway meint und nicht alles auf Sokrates-2. Ohne sie gilt die Adresse von Sokrates-2.
2. **Ist WireGuard in der Fritzbox für das Handy schon eingerichtet und von außen erreichbar?** (öffentliche Adresse oder MyFRITZ,
   kein DS-Lite). Das ist nur dort prüfbar.
3. **Ausgang des Kontos `wamo-rd` ins LAN erlauben?** Empfehlung: nein (nur Internet), mit der Folge, dass LAN-Dienste aus der
   Fernsitzung nicht aufrufbar sind.
4. **Frischer Zweitfaktor bei jedem Start (höchstens 120 Sekunden alt)?** Empfehlung war: ja. **Entschieden am 11.11.2026: nein, der Code beim
   Anmelden genügt** (Abschnitt 3.6, Stufenschalter `WAMO_RD_ANMELDUNG_MAX_MIN`).
5. **Caesar: Soll xrdp sofort (Stufe 0) beschränkt oder gestoppt werden?** Wird er derzeit von jemandem genutzt? (keine laufende
   Sitzung am 31.10.2026 gesehen).
6. **Dauer:** 60 Minuten Höchstdauer, 10 Minuten Leerlauf, ein Zuschauer je Ziel. Passen die Werte?
