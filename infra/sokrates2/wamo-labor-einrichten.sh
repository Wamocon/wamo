#!/usr/bin/env bash
# Labor-Dienst auf Sokrates-2 (Test-Jira des Testlabors am 28.11.2026): Container wamo-labor-dienst, HTTPS 55447 über wamo-caddy, Firewall,
# Token, Einstellungen für den Kern. Compose-Projekt wamo-labor (infra/sokrates2/wamo-labor/docker-compose.yml), Vertrag des Dienstes
# docs/labor-dienst-vertrag.md. Nur als root (sudo), wie die übrigen Einrichtungsskripte. Wiederholbar (idempotent).
#
# Ablage: Der Ordner liegt im Home von wamocon, nie in /tmp. Dazu die Quellen des Dienstes (labor-dienst/ aus dem Repo), aus denen das Image
# am Server gebaut wird. Vom PC aus (Repo-Wurzel):
#   ssh sokrates2 "rm -rf ~/wamo-infra ~/wamo-labor-quelle" && scp -r infra/sokrates2 sokrates2:wamo-infra && scp -r labor-dienst sokrates2:wamo-labor-quelle
#   ssh sokrates2 "chmod -R go-w ~/wamo-infra ~/wamo-labor-quelle"
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh einrichten --trocken --basis-image python:3.11-slim@sha256:<Digest>"
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-einrichten.sh einrichten --basis-image python:3.11-slim@sha256:<Digest>"
#
#   bash wamo-labor-einrichten.sh [einrichten|status|pruefen] [--trocken] [--basis-image NAME:TAG@sha256:DIGEST] [--quelle ORDNER]
#
#   einrichten  (Standard) alles in dieser Reihenfolge: Vorprüfung, Sicherung, Ordner und Token, Image, Firewall, Caddy, Start, Kern, Prüfung
#   status      zeigt Namen und Zustände (Container, Netze, Site, Firewall, Dateien, Namen der Kern-Einstellungen), nie Werte
#   pruefen     Rauchtest gegen den Betrieb (wamo_labor_rauchtest.py --betrieb)
#   --trocken   Lesen und Prüfen wie sonst, aber jede Änderung (Dateien, docker, Caddy, Firewall, Kern) nur anzeigen
#   --basis-image  Basis-Image des Dienstes mit festem Digest (kein wanderndes Tag). Nur beim ersten Lauf nötig: Der Wert wird in
#               /etc/wamo-labor/basis-image gemerkt. Den Digest holt man auf dem Server mit  docker buildx imagetools inspect python:3.11-slim
#
# Was einrichten tut (vor jeder Änderung eine Sicherung unter /var/backups/wamo-labor/<JJJJMMTT-HHMMSS>/ ohne Token und ohne Daten):
#   Ordner     /etc/wamo-labor (root, 0755), /var/lib/wamo-labor (Besitzer 10001, 0700, einziger beschreibbarer Ort des Dienstes),
#              /opt/wamo-labor (Compose). Kein Benutzer auf dem Host: 10001 ist die Kennung im Image; ist sie auf dem Host schon an ein anderes
#              Konto vergeben, bricht das Skript ab.
#   Token      Beim ersten Lauf entstehen kern-token (Bearer des Kerns) und betreuer-token (Betreuer-Seite): je 32 Zufallsbytes, URL-sicher als
#              Base64 (43 Zeichen, mindestens 16 verschiedene), Besitzer 10001, Modus 0400, in /etc/wamo-labor. Die Werte werden nie ausgegeben,
#              nie als Argument übergeben (Prozessliste) und nie in eine Variable gelesen. Ein späterer Lauf lässt vorhandene Token unberührt.
#   Adressen   /etc/wamo-labor/betreuer-adressen (wer /betreuer und /api/betreuer/ erreicht, zusätzlich zum Server selbst) und
#              /etc/wamo-labor/plaetze-adressen (Quellen für 55447 in der Firewall, Frage Q13: bis dahin das Büro-LAN). Beide entstehen als Vorlage
#              mit Kommentaren, eine Adresse oder ein Netz je Zeile. Betreuer-Adressen setzt dieses Skript in die Caddy-Site ein (nach Änderung
#              der Datei einrichten erneut ausführen); die Plätze liest die Firewall (nach Änderung  systemctl reload wamo-firewall).
#   Image      docker build aus der Quelle (Basis über festen Digest, Abhängigkeiten mit --require-hashes im Dockerfile), Tag wamo-labor-dienst:1
#   Firewall   wamo-firewall-einrichten.sh (Kette WAMO-FW-LABOR für 55447, Sperre des Ausgangs WAMO-FW-LABAUS), nur wenn die Regeln fehlen
#   Caddy      Site wamo-labor.caddy aus der Vorlage (Betreuer-Adressen eingesetzt), Konfiguration vorher in einem Wegwerf-Container ohne Netz
#              geprüft (caddy adapt), Compose von wamo-caddy (Port 55447, Netz wamo-labor-netz). Ändert sich dessen Compose, wird wamo-caddy EINMAL
#              neu angelegt: 55443 bis 55447 sind dann wenige Sekunden weg. Das geschieht nur ohne laufende Sitzung des Kerns.
#   Start      docker compose up -d, warten bis der Container gesund ist
#   Kern       WAMO_LABOR_URL=http://127.0.0.1:18765 und WAMO_LABOR_TOKEN in /etc/wamo/core.env über /usr/local/sbin/wamo-core-env setzen (Werte
#              nur über stdin). Der Kern liest sie erst mit seinem nächsten Release (wamo-core-slots.sh umschalten-kern). Dieses Skript startet
#              wamo-core nie neu.
#
# Fehler: Schritte mit Änderung (Caddy, Start) werden bei einem Fehler zurückgenommen (alte Site und altes Compose, Container weg). Token,
# Ordner, Firewall-Ketten und das Image bleiben, sie schaden ohne Container nicht. Rückweg: wamo-labor-rueckweg.sh. Löschlauf nach der
# Aufbewahrung (11.12.2026): wamo-labor-loeschlauf.sh.
#
# Folgeschritt (hier bewusst kein Befehl): Der Betreuer-Token geht später über ein PC-Skript per ssh in eine lokale Datei auf dem PC des Betreuers
# (Befehl betreuer-token-ausgeben dort, nie hier). Dieses Skript zeigt keinen Token an.
#
# Probelauf ohne root (nur für Tests): WAMO_LABOR_WURZEL=/pfad/zu/einem/wegwerf/ordner stellt allen Pfaden unter /etc, /var, /opt und /usr diesen
# Ordner voran. Dann gibt es weder chown noch Firewall noch Rauchtest; docker, iptables und Ähnliches stammen aus dem PATH (Attrappen).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
W="${WAMO_LABOR_WURZEL:-}"
ETC="$W/etc/wamo-labor"
DATEN="$W/var/lib/wamo-labor"
BASIS="$W/opt/wamo-labor"
SICHERUNG="$W/var/backups/wamo-labor"
CORE_ENV="$W/usr/local/sbin/wamo-core-env"
FIREWALL_BIN="$W/usr/local/sbin/wamo-firewall"
CADDY_DIR="${WAMO_LABOR_CADDY_DIR:-/home/wamocon/wamo-tls/caddy}"
CA="${WAMO_LABOR_CA:-/home/wamocon/wamo-tls/ca/wamo-ca.crt}"
QUELLE="${WAMO_LABOR_QUELLE:-/home/wamocon/wamo-labor-quelle}"
KERN_TOKEN_DATEI="$ETC/kern-token"
BETREUER_TOKEN_DATEI="$ETC/betreuer-token"
BETREUER_ADRESSEN="$ETC/betreuer-adressen"
PLAETZE_ADRESSEN="$ETC/plaetze-adressen"
BASIS_IMAGE_DATEI="$ETC/basis-image"
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-labor.caddy"
CADDY_COMPOSE="$CADDY_DIR/docker-compose.yml"
COMPOSE_QUELLE="$HERE/wamo-labor/docker-compose.yml"
COMPOSE="$BASIS/docker-compose.yml"
DIENST_UID=10001
LABOR_PORT=55447
LABOR_URL="https://192.168.178.136:$LABOR_PORT"
BILD=wamo-labor-dienst:1
KERN_URL="http://127.0.0.1:18765"
INTERN="127.0.0.0/8 172.24.0.1/32 192.168.178.136/32"
NETZE="wamo-labor-netz=172.28.0.0/24 wamo-labor-lokal=172.28.1.0/29"
CADDY_PORTS="55443 55444 55445 55446"   # die Dienste hinter wamo-caddy, die ein Neuanlegen kurz lahmlegt

TROCKEN=nein
BEFEHL=einrichten
BASIS_IMAGE=""
ADRESSEN=""
ARBEIT=""
TOKEN_TMP=""
CADDY_OWNER=""
CADDY_GROUP=""
CADDY_UNDO=nein
CADDY_SITE_ALT=""
CADDY_COMPOSE_ALT=""
CADDY_COMPOSE_NEU=nein
UNDO=()

usage() {
  echo "Aufruf: $0 [einrichten|status|pruefen] [--trocken] [--basis-image NAME:TAG@sha256:DIGEST] [--quelle ORDNER]" >&2
  exit 2
}
fehler() { echo "$*" >&2; exit 1; }
# Im Trockenlauf wird eine Vorbedingung nur gemeldet (im Echtlauf bricht das Skript ab)
muss() {
  if [ "$TROCKEN" = ja ]; then echo "  WARNUNG (im Echtlauf Abbruch): $*" >&2; else fehler "$*"; fi
}
# Jede Änderung läuft über tu: im Trockenlauf wird sie nur angezeigt
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}
ipt() { iptables -w 5 "$@"; }
# Ordner mit Modus und (nur im echten Lauf) Besitzer: ordner MODUS BESITZER ORDNER...
ordner() {
  local modus="$1" besitzer="$2"
  shift 2
  mkdir -p "$@"
  chmod "$modus" "$@"
  if [ -n "$besitzer" ] && [ -z "$W" ]; then chown "$besitzer:$besitzer" "$@"; fi
}

aufraeumen() {
  local rc=$? i
  trap - EXIT
  if [ "$rc" -ne 0 ] && [ "${#UNDO[@]}" -gt 0 ]; then
    for ((i = ${#UNDO[@]} - 1; i >= 0; i--)); do "${UNDO[$i]}" || true; done
  fi
  case "$ARBEIT" in */.arbeit.*) rm -rf "$ARBEIT" ;; esac
  [ -z "$TOKEN_TMP" ] || rm -f "$TOKEN_TMP"
  exit "$rc"
}
trap aufraeumen EXIT

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten): alles gehört root oder dem aufrufenden Benutzer, nichts ist
# für die Gruppe oder alle beschreibbar, keine symbolischen Links
pruefe_ordner() {
  local ordner="$1" erlaubt uid modus pfad
  erlaubt=" 0 $(id -u) ${SUDO_UID:-} "
  while read -r uid modus pfad; do
    if [[ "$erlaubt" != *" $uid "* ]] || (( 8#$modus & 8#022 )); then
      echo "Unsicherer Ablageort: $pfad (Besitzer $uid, Rechte $modus). Ordner im eigenen Home ablegen und" \
        "mit chmod -R go-w schützen." >&2
      exit 1
    fi
  done < <(stat -c '%u %a %n' "$(dirname "$ordner")"; find "$ordner" \( -type l -printf '%U 777 %p\n' \) -o -printf '%U %m %p\n')
}

# Ruhe: keine Sitzung im Kern (wie wamo-rd-einrichten.sh): Ein Neustart von wamo-caddy unterbräche das Gespräch kurz
kern_ruhig() {
  # Keine Sitzung in einer der Instanzen: die alte Unit wamo-core und die Slots wamo-core@a und wamo-core@b. Läuft keine, gilt der Kern als ruhig.
  local unit pid udp log roh_ss roh_log
  for unit in wamo-core wamo-core@a wamo-core@b; do
    pid="$(systemctl show "$unit" -p MainPID --value 2>/dev/null || echo 0)"
    if [ -z "$pid" ] || [ "$pid" = 0 ]; then continue; fi
    # Fail-closed: Lässt sich der Zustand nicht lesen, gilt der Kern als nicht ruhig
    roh_ss="$(ss -uanpH 2>/dev/null)" || { echo "  Kern nicht prüfbar: ss fehlgeschlagen" >&2; return 1; }
    udp="$(grep -c "pid=$pid," <<<"$roh_ss" || true)"
    roh_log="$(journalctl -u "$unit" --since '-5 min' --no-pager -q 2>/dev/null)" \
      || { echo "  Kern nicht prüfbar: journalctl fehlgeschlagen" >&2; return 1; }
    # abgewiesene Anfragen ohne Anmeldung (401) und die Hintergrundabfrage einer offenen Oberfläche (/wamo/api/hinweise, ein offenes Fenster fragt sie alle
    # paar Minuten ab) sind keine Sitzung: Eine Sitzung hat einen Audiostrom (UDP, oben gezählt) und ihre eigenen Zeilen
    log="$(grep -vE 'Zugang abgelehnt|401 Unauthorized|/wamo/api/hinweise|^$' <<<"$roh_log" | wc -l || true)"
    if [ "${udp:-0}" != 0 ] || [ "${log:-0}" != 0 ]; then
      echo "  Kern nicht ruhig ($unit): UDP-Sockets $udp, Journalzeilen der letzten 5 Minuten $log" >&2
      return 1
    fi
  done
  return 0
}

# Offene Verbindungen an 55443 bis 55447: Das Neuanlegen von wamo-caddy trennt sie. Gezählt wird im Container (netstat aus dem Busybox des Images),
# fehlt es dort, auf dem Host mit ss.
verbindungen_offen() {
  local roh
  if roh="$(docker exec wamo-caddy netstat -tn 2>/dev/null)"; then
    awk '$6 == "ESTABLISHED" && $4 ~ /:5544[3-7]$/' <<<"$roh" | wc -l
  else
    ss -tn state established 2>/dev/null | awk '$3 ~ /:5544[3-7]$/' | wc -l
  fi
}

# Ruhe vor dem Neuanlegen von wamo-caddy: keine Sitzung im Kern und keine offene Verbindung an den Ports von Caddy
ruhe_pruefen() {
  local n
  kern_ruhig || return 1
  n="$(verbindungen_offen)"
  if [ "${n:-0}" != 0 ]; then
    echo "  Es bestehen $n Verbindungen an 55443 bis 55447 (wamo-caddy würde sie trennen)" >&2
    return 1
  fi
  echo "  ruhig: keine Sitzung im Kern, keine offene Verbindung an 55443 bis 55447"
}

# ---------- Vorprüfung
UEBERSCHNEIDUNG='
import ipaddress, sys
name, netz = sys.argv[1], ipaddress.ip_network(sys.argv[2])
for zeile in sys.stdin.read().splitlines():
    teile = zeile.split()
    if not teile or teile[0] == name:
        continue
    for t in teile[1:]:
        try:
            if ipaddress.ip_network(t, strict=False).overlaps(netz):
                print(f"Netz {teile[0]} ({t}) überschneidet sich mit {name} ({netz})")
                sys.exit(1)
        except ValueError:
            pass
'

netze_pruefen() {
  # Keine fremde Docker-Bridge darf eine der festen Adressen belegen (Überschneidung); die eigenen Netze zählen nicht
  local eintrag name netz roh
  command -v python3 >/dev/null 2>&1 || { muss "python3 fehlt (Prüfung der Netzadressen)."; return 0; }
  roh="$(docker network ls -q 2>/dev/null | xargs -r docker network inspect -f '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null || true)"
  for eintrag in $NETZE; do
    name="${eintrag%%=*}"
    netz="${eintrag#*=}"
    if ! python3 -I -c "$UEBERSCHNEIDUNG" "$name" "$netz" <<<"$roh"; then
      muss "Adresse $netz für $name ist anderweitig belegt (siehe oben)."
    fi
  done
  echo "  Netzadressen frei: $NETZE"
}

# Eine Adresse für die Betreuer: a.b.c.d oder a.b.c.d/n mit n von 24 bis 32, vier Zahlen ohne führende Nullen, nie 0.x, nie Loopback (gilt
# ohnehin), nie Multicast und Link-Local
adresse_gueltig() {
  local o
  [[ "$1" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})(/([0-9]{1,2}))?$ ]] || return 1
  for o in "${BASH_REMATCH[@]:1:4}"; do (( o <= 255 )) || return 1; done
  (( ${BASH_REMATCH[6]:-32} >= 24 && ${BASH_REMATCH[6]:-32} <= 32 )) || return 1
  if (( BASH_REMATCH[1] == 0 || BASH_REMATCH[1] == 127 || BASH_REMATCH[1] >= 224 )) \
    || (( BASH_REMATCH[1] == 169 && BASH_REMATCH[2] == 254 )); then return 1; fi
}

# Die Liste für die Caddy-Site: der Server selbst und die Zeilen aus betreuer-adressen. Eine ungültige Zeile bricht ab (nie stillschweigend weglassen)
betreuer_adressen() {
  local liste="$INTERN" zeile wert
  if [ -r "$BETREUER_ADRESSEN" ]; then
    while IFS= read -r zeile || [ -n "$zeile" ]; do
      wert="$(tr -d " \t\r\"'" <<<"${zeile%%#*}")"
      [ -n "$wert" ] || continue
      adresse_gueltig "$wert" || fehler "Ungültige Adresse in $BETREUER_ADRESSEN: $wert (a.b.c.d oder a.b.c.d/n mit n von 24 bis 32)."
      liste="$liste $wert"
    done < "$BETREUER_ADRESSEN"
  fi
  printf '%s\n' "$liste"
}

dateien_pruefen() {
  local f
  for f in wamo-labor/docker-compose.yml wamo-caddy/kern/wamo-labor.caddy wamo-caddy/docker-compose.yml wamo-firewall.sh \
    wamo-firewall-einrichten.sh; do
    [ -f "$HERE/$f" ] || fehler "$HERE/$f fehlt (infra/sokrates2 vollständig verteilen)."
  done
  # der Rauchtest läuft am Ende von einrichten: Fehlt er, soll das Skript nicht erst nach den Änderungen scheitern
  [ -n "$W" ] || [ -f "$HERE/wamo_labor_rauchtest.py" ] || muss "$HERE/wamo_labor_rauchtest.py fehlt (infra/sokrates2 vollständig verteilen)."
}

basis_image_pruefen() {
  if [ -z "$BASIS_IMAGE" ] && [ -f "$BASIS_IMAGE_DATEI" ]; then BASIS_IMAGE="$(head -n1 "$BASIS_IMAGE_DATEI" | tr -d '\r')"; fi
  [ -n "$BASIS_IMAGE" ] || { muss "Das Basis-Image fehlt: beim ersten Lauf --basis-image NAME:TAG@sha256:DIGEST angeben."; return 0; }
  [[ "$BASIS_IMAGE" =~ ^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$ ]] \
    || muss "Das Basis-Image braucht einen festen Digest (--basis-image NAME:TAG@sha256:DIGEST), kein wanderndes Tag."
}

# Ein Port muss frei sein oder dem eigenen Stack gehören (zweiter Lauf: 55447 hat wamo-caddy, 18765 der Dienst). Ohne Pipe zu grep -q (pipefail).
port_frei() {
  local port="$1" container="$2" belegt eigener
  belegt="$(ss -tlnH "sport = :$port" 2>/dev/null || true)"
  [ -n "$belegt" ] || return 0
  eigener="$(docker port "$container" 2>/dev/null || true)"
  case "$eigener" in *":$port"*) return 0 ;; esac
  muss "Port $port ist schon belegt (nicht von $container)."
}

# Beide Compose-Dateien müssen gültig sein, bevor etwas geändert wird
compose_pruefen() {
  docker compose -f "$COMPOSE_QUELLE" config -q >/dev/null || muss "docker compose config -q schlägt für $COMPOSE_QUELLE fehl."
  docker compose -f "$HERE/wamo-caddy/docker-compose.yml" --project-directory "$CADDY_DIR" config -q >/dev/null \
    || muss "docker compose config -q schlägt für wamo-caddy/docker-compose.yml fehl."
}

quelle_und_benutzer_pruefen() {
  # Die Kennung 10001 steht im Image; auf dem Host darf sie keinem anderen Konto gehören (es könnte die Daten und die Token lesen)
  local name
  name="$(getent passwd "$DIENST_UID" | cut -d: -f1 || true)"
  if [ -n "$name" ] && [ "$name" != wamo-labor ]; then muss "Die Kennung $DIENST_UID gehört auf diesem Rechner dem Konto $name."; fi
  [ -d "$QUELLE/wamo_labor_dienst" ] && [ -f "$QUELLE/Dockerfile" ] \
    || muss "$QUELLE/Dockerfile fehlt (labor-dienst/ aus dem Repo nach $QUELLE kopieren)."
}

vorpruefung() {
  echo "== Vorprüfung"
  local w
  for w in docker curl ss journalctl; do
    command -v "$w" >/dev/null 2>&1 || muss "$w fehlt (gebraucht für die Prüfung vor und nach dem Neuanlegen von wamo-caddy)."
  done
  docker compose version >/dev/null 2>&1 || muss "docker compose fehlt."
  [ -n "$(docker ps -q -f name=^wamo-caddy$ 2>/dev/null || true)" ] || muss "wamo-caddy läuft nicht (zuerst wamo-tls-einrichten.sh)."
  [ -d "$CADDY_DIR/etc" ] && [ -f "$CADDY_COMPOSE" ] || muss "$CADDY_DIR fehlt oder ist unvollständig (zuerst wamo-tls-einrichten.sh)."
  [ -x "$CORE_ENV" ] || muss "$CORE_ENV fehlt (zuerst wamo-core-einrichten.sh)."
  [ -x "$FIREWALL_BIN" ] || muss "Die WAMO-Firewall ist nicht eingerichtet (zuerst wamo-firewall-einrichten.sh)."
  dateien_pruefen
  basis_image_pruefen
  quelle_und_benutzer_pruefen
  netze_pruefen
  port_frei "$LABOR_PORT" wamo-caddy
  port_frei 18765 wamo-labor-dienst
  compose_pruefen
  ADRESSEN="$(betreuer_adressen)"
  echo "  Betreuer-Adressen: $ADRESSEN"
  if ! cmp -s "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_COMPOSE"; then
    ruhe_pruefen || muss "Abbruch: Gerade ist Betrieb (siehe oben). wamo-caddy wird neu angelegt, sobald niemand spricht (Skript erneut ausführen)."
  fi
}

# ---------- Sicherung
kopie() { if [ -f "$1" ]; then cp -p "$1" "$2"; fi; }

sichern() {
  echo "== Sicherung (vor jeder Änderung, ohne Token und ohne Daten)"
  local zeit ziel n=1
  zeit="$(date +%Y%m%d-%H%M%S)"
  ziel="$SICHERUNG/$zeit"
  while [ -e "$ziel" ]; do n=$((n + 1)); ziel="$SICHERUNG/$zeit-$n"; done
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] Sicherung nach $ziel: Compose, Caddyfile und Site von wamo-caddy, Firewall-Skript, iptables-save"
    return 0
  fi
  ordner 0700 "" "$SICHERUNG" "$ziel"
  kopie "$CADDY_COMPOSE" "$ziel/wamo-caddy-compose.vorher"
  kopie "$CADDY_DIR/etc/Caddyfile" "$ziel/Caddyfile.vorher"
  kopie "$SITE_DATEI" "$ziel/wamo-labor.caddy.vorher"
  kopie "$FIREWALL_BIN" "$ziel/wamo-firewall.vorher"
  kopie "$COMPOSE" "$ziel/wamo-labor-compose.vorher"
  iptables-save > "$ziel/iptables-save.txt" 2>/dev/null || true
  echo "  $ziel"
}

# ---------- Ordner, Token, Adressdateien
# Form und Vielfalt wie die Konfiguration des Dienstes: 43 bis 200 Zeichen aus A-Z a-z 0-9 und ._~+/=-, mindestens 16 verschiedene
token_gueltig() {
  grep -qE '^[A-Za-z0-9._~+/=-]{43,200}$' "$1" || return 1
  [ "$(grep -o . "$1" | sort -u | wc -l)" -ge 16 ]
}

# Der Wert entsteht in einer Pipe aus /dev/urandom und geht nur in die Datei: nie als Argument, nie in eine Variable, nie auf den Bildschirm
token_anlegen() {
  local ziel="$1" versuch
  umask 077
  TOKEN_TMP="$(mktemp "$ETC/.token.XXXXXX")"
  for versuch in 1 2 3 4 5; do
    head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > "$TOKEN_TMP"
    if token_gueltig "$TOKEN_TMP"; then break; fi
  done
  token_gueltig "$TOKEN_TMP" || fehler "Es ließ sich kein brauchbares Token erzeugen."
  if [ -z "$W" ]; then chown "$DIENST_UID:$DIENST_UID" "$TOKEN_TMP"; fi
  chmod 0400 "$TOKEN_TMP"
  mv -f "$TOKEN_TMP" "$ziel"
  TOKEN_TMP=""
}

token_sicherstellen() {
  local datei="$1" name="$2"
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] $name: 32 Zufallsbytes (URL-sicher, 43 Zeichen), 0400, Besitzer $DIENST_UID, falls er fehlt; wird nie ausgegeben"
    return 0
  fi
  if [ -f "$datei" ]; then
    token_gueltig "$datei" || fehler "$name ($datei) hat nicht die erwartete Form (43 bis 200 Zeichen, mindestens 16 verschiedene)."
    if [ -z "$W" ]; then chown "$DIENST_UID:$DIENST_UID" "$datei"; fi
    chmod 0400 "$datei"
    echo "  $name vorhanden (Inhalt wird nicht angezeigt)"
  else
    token_anlegen "$datei"
    echo "  $name angelegt (Inhalt wird nicht angezeigt)"
  fi
}

# Eine Datei mit Kommentaren, nur wenn sie fehlt (der Inhalt gehört dem Betreiber und bleibt unberührt)
adressdatei_vorlage() {
  local datei="$1" kopf="$2"
  if [ "$TROCKEN" = ja ]; then echo "  [trocken] $datei (Vorlage mit Kommentaren), falls sie fehlt"; return 0; fi
  if [ -f "$datei" ]; then echo "  $(basename "$datei") vorhanden (unverändert)"; return 0; fi
  printf '%s\n' "$kopf" > "$datei"
  chmod 0644 "$datei"
  echo "  $(basename "$datei") als Vorlage angelegt"
}

ordner_und_token() {
  echo "== Ordner und Token"
  tu ordner 0755 "" "$ETC" "$BASIS"
  tu ordner 0700 "$DIENST_UID" "$DATEN"
  token_sicherstellen "$KERN_TOKEN_DATEI" kern-token
  token_sicherstellen "$BETREUER_TOKEN_DATEI" betreuer-token
  if [ "$TROCKEN" = nein ] && cmp -s "$KERN_TOKEN_DATEI" "$BETREUER_TOKEN_DATEI"; then fehler "Kern-Token und Betreuer-Token sind gleich."; fi
  adressdatei_vorlage "$BETREUER_ADRESSEN" "# Adressen der Betreuer-PCs: eine Adresse (a.b.c.d) oder ein Netz (a.b.c.d/n, n von 24 bis 32) je Zeile. Nur von hier und vom Server selbst
# erreicht man /betreuer und /api/betreuer/. Nach einer Änderung wamo-labor-einrichten.sh einrichten erneut ausführen."
  adressdatei_vorlage "$PLAETZE_ADRESSEN" "# Adressen der Plätze (Frage Q13): eine Adresse (a.b.c.d) oder ein Netz (a.b.c.d/n, n von 8 bis 32) je Zeile. Mit Einträgen ersetzt diese Liste
# das Büro-LAN für den Port 55447, ohne Einträge gilt das Büro-LAN. Nach einer Änderung:  systemctl reload wamo-firewall"
  if [ -n "$BASIS_IMAGE" ] && [ "$TROCKEN" = nein ]; then
    printf '%s\n' "$BASIS_IMAGE" > "$BASIS_IMAGE_DATEI"
    chmod 0644 "$BASIS_IMAGE_DATEI"
  fi
}

# ---------- Image
# Der Bau geschieht auf dem Server selbst (kein --platform): Basis-Image und gebautes Image müssen die Architektur des Servers haben, sonst
# bräche der Start später mit einem Format-Fehler ab. Der Container, der danach gesund meldet (Rauchtest P01), belegt, dass das Image läuft.
arch_pruefen() {
  local host erwartet img got
  host="$(uname -m)"
  case "$host" in x86_64) erwartet=amd64 ;; aarch64|arm64) erwartet=arm64 ;; *) erwartet="$host" ;; esac
  for img in "$BASIS_IMAGE" "$BILD"; do
    got="$(docker image inspect -f '{{.Architecture}}' "$img" 2>/dev/null || true)"
    [ "$got" = "$erwartet" ] || fehler "Das Image $img hat die Architektur '$got', der Server ist $erwartet ($host): Es muss auf dem Server gebaut werden."
  done
  echo "  Architektur $erwartet (wie der Server, $host)"
}

bilder() {
  echo "== Image $BILD (Basis über festen Digest, Bau auf dem Server)"
  docker image inspect "$BASIS_IMAGE" >/dev/null 2>&1 || tu docker pull "$BASIS_IMAGE"
  tu docker build --pull=false --build-arg "BASIS=$BASIS_IMAGE" --tag "$BILD" "$QUELLE"
  [ "$TROCKEN" = ja ] && return 0
  arch_pruefen
  echo "  fertig (die Abhängigkeiten installiert das Dockerfile mit --require-hashes)"
}

# ---------- Firewall
firewall_steht() {
  ipt -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $LABOR_PORT" && ipt -S WAMO-FW-LABOR >/dev/null 2>&1 \
    && ipt -S WAMO-FW-LABAUS >/dev/null 2>&1 && ipt -S WAMO-FW 2>/dev/null | grep -q -- '-s 172.28.1.0/29'
}

firewall() {
  echo "== Firewall: Port $LABOR_PORT (Kette WAMO-FW-LABOR) und Sperre des Ausgangs (Kette WAMO-FW-LABAUS)"
  grep -q 'WAMO-FW-LABOR' "$HERE/wamo-firewall.sh" && grep -q "$LABOR_PORT" "$HERE/wamo-firewall.sh" \
    || fehler "$HERE/wamo-firewall.sh ohne die Kette für $LABOR_PORT (infra/sokrates2 vollständig verteilen)."
  if [ "$TROCKEN" = ja ]; then echo "  [trocken] bash $HERE/wamo-firewall-einrichten.sh (nur wenn die Regeln fehlen)"; return 0; fi
  if [ -n "$W" ]; then echo "  (Probelauf: keine Änderung der Firewall)"; return 0; fi
  if firewall_steht; then echo "  Regeln stehen bereits"; return 0; fi
  bash "$HERE/wamo-firewall-einrichten.sh"
  firewall_steht || fehler "Die Firewall-Regeln für $LABOR_PORT sind nicht aktiv."
  echo "  Regeln aktiv (per systemctl reload, ohne Lücke)"
}

# ---------- Caddy
site_erzeugen() {
  local text
  text="$(cat "$HERE/wamo-caddy/kern/wamo-labor.caddy")"
  text="${text//__BETREUER_ADRESSEN__/$ADRESSEN}"
  printf '%s\n' "$text"
}

caddy_neu_laden() { docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; }

# Datei in den Ordner von wamo-caddy legen, mit dessen Besitzer (die Ordner gehören wamocon)
caddy_installieren() {
  if [ -n "$CADDY_OWNER" ]; then install -m "$1" -o "$CADDY_OWNER" -g "$CADDY_GROUP" "$2" "$3"; else install -m "$1" "$2" "$3"; fi
}

# Die ganze Konfiguration samt neuer Site in einem Wegwerf-Ordner prüfen (caddy adapt in einem Container ohne Netz), bevor etwas installiert wird
caddy_pruefen() {
  local image
  image="$(grep -E '^[[:space:]]+image: caddy:' "$HERE/wamo-caddy/docker-compose.yml" | head -n1 | sed -E 's/^[[:space:]]+image:[[:space:]]+//' | tr -d '\r')"
  [ -n "$image" ] || fehler "Das Caddy-Image steht nicht in wamo-caddy/docker-compose.yml."
  umask 077
  ARBEIT="$(mktemp -d "$CADDY_DIR/.arbeit.XXXXXX")"
  mkdir -p "$ARBEIT/etc/kern"
  cp -a "$CADDY_DIR/etc/." "$ARBEIT/etc/"
  site_erzeugen > "$ARBEIT/etc/kern/wamo-labor.caddy"
  chmod 755 "$ARBEIT" "$ARBEIT/etc" "$ARBEIT/etc/kern"
  chmod 644 "$ARBEIT/etc/kern/"* "$ARBEIT/etc/Caddyfile"
  # NET_BIND_SERVICE: Das Image versieht caddy mit dieser Fähigkeit, ohne sie im Begrenzungssatz startet das Programm nicht
  docker run --rm --network none --cap-drop ALL --cap-add NET_BIND_SERVICE --read-only --security-opt no-new-privileges:true \
    -v "$ARBEIT/etc:/etc/caddy:ro" "$image" caddy adapt --config /etc/caddy/Caddyfile >/dev/null
  echo "  Konfiguration gültig"
}

undo_caddy() {
  [ "$CADDY_UNDO" = aktiv ] || return 0
  local start
  echo "  FEHLER: Caddy-Änderung wird zurückgenommen" >&2
  start="$(date +%s)"
  if [ -n "$CADDY_SITE_ALT" ]; then caddy_installieren 644 "$CADDY_SITE_ALT" "$SITE_DATEI"; else rm -f "$SITE_DATEI"; fi
  if [ "$CADDY_COMPOSE_NEU" = ja ] && [ -n "$CADDY_COMPOSE_ALT" ]; then
    caddy_installieren 644 "$CADDY_COMPOSE_ALT" "$CADDY_COMPOSE"
    ( cd "$CADDY_DIR" && docker compose up -d ) >&2 || true
  else
    caddy_neu_laden >&2 || true
  fi
  # Nach jeder Rücknahme müssen 55443 bis 55446 wieder antworten (der Lauf endet ohnehin mit einem Fehler)
  caddy_ports_pruefen "$start" >&2 || echo "  FEHLER: Nach der Rücknahme antworten 55443 bis 55446 nicht, bitte sofort ansehen (docker logs wamo-caddy)." >&2
  echo "  Zurückgenommen. Bitte das Protokoll prüfen (docker logs wamo-caddy)." >&2
}

# 55443 (API) darf durch die Änderung nie wegfallen, und die neue Site muss antworten (ohne Dienst mit 502 von Caddy, aber mit TLS)
# Antwortet eine Adresse mit irgendeinem HTTP-Status (nur 000 heißt keine Antwort)? Je Adresse höchstens WARTE_RUNDEN mal WARTE_SEKUNDEN.
antwortet() {
  local url="$1" code runde runden="${WAMO_LABOR_WARTE_RUNDEN:-60}" sek="${WAMO_LABOR_WARTE_SEKUNDEN:-2}"
  [[ "$runden" =~ ^[0-9]+$ ]] || runden=60
  [[ "$sek" =~ ^[0-9]+$ ]] || sek=2
  for ((runde = 0; runde < runden; runde++)); do
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)"
    [ "$code" != 000 ] && return 0
    sleep "$sek"
  done
  return 1
}

# 55443 bis 55446 müssen antworten (nach einer Änderung an Caddy und nach jeder Rücknahme). Die Dauer ab START wird genannt: Sie ist am Server
# zu messen, vorab gibt es dafür keine Zusage.
caddy_ports_pruefen() {
  local start="$1" p fehlt=""
  for p in $CADDY_PORTS; do antwortet "https://192.168.178.136:$p/" || fehlt="$fehlt $p"; done
  if [ -n "$fehlt" ]; then
    echo "  FEHLER: Von wamo-caddy antworten die Ports$fehlt nicht (55443 bis 55446 gehören anderen Diensten, docker logs wamo-caddy)" >&2
    return 1
  fi
  echo "  Ausfalldauer: $(( $(date +%s) - start )) Sekunden (Zeit zwischen Stopp und erster Antwort aller Ports 55443 bis 55446; am Server zu messen)"
}

# Stufe 1: Site installieren und Caddy neu laden. Das prüft die Konfiguration atomar und ohne Ausfall; weil der Port 55447 im Container noch nicht
# veröffentlicht ist, gilt die Site vorerst nur im Container (gewollt).
caddy_stufe1() {
  local start
  caddy_installieren 644 "$ARBEIT/etc/kern/wamo-labor.caddy" "$SITE_DATEI"
  start="$(date +%s)"
  caddy_neu_laden
  echo "  Stufe 1: Site geladen (nur im Container aktiv, 55447 ist noch nicht veröffentlicht)"
  caddy_ports_pruefen "$start"
}

# Stufe 2 (nur wenn sich das Compose von wamo-caddy ändert): Port 55447 und das Netz wamo-labor-netz, wamo-caddy wird EINMAL neu angelegt
caddy_stufe2() {
  local start
  ruhe_pruefen || fehler "Abbruch: Gerade ist Betrieb (siehe oben). wamo-caddy wird neu angelegt, sobald niemand spricht (Skript erneut ausführen)."
  CADDY_COMPOSE_NEU=ja
  caddy_installieren 644 "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_COMPOSE"
  echo "  Stufe 2: Compose von wamo-caddy geändert (Port $LABOR_PORT, Netz wamo-labor-netz): Container wird neu angelegt"
  start="$(date +%s)"
  ( cd "$CADDY_DIR" && docker compose up -d )
  caddy_ports_pruefen "$start"
}

caddy_anwenden() {
  local compose_neu="$1"
  CADDY_COMPOSE_NEU=nein
  if [ -f "$SITE_DATEI" ]; then CADDY_SITE_ALT="$ARBEIT/site.vorher"; cp -a "$SITE_DATEI" "$CADDY_SITE_ALT"; fi
  CADDY_COMPOSE_ALT="$ARBEIT/compose.vorher"
  cp -a "$CADDY_COMPOSE" "$CADDY_COMPOSE_ALT"
  CADDY_UNDO=aktiv
  UNDO+=(undo_caddy)
  caddy_stufe1
  if [ "$compose_neu" = ja ]; then caddy_stufe2; fi
  antwortet "$LABOR_URL/gesundheit" || fehler "Keine Antwort von $LABOR_URL nach der Caddy-Änderung."
  CADDY_UNDO=fertig
}

caddy() {
  echo "== Caddy: Site $LABOR_URL (in zwei Stufen)"
  local compose_neu=nein
  cmp -s "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_COMPOSE" || compose_neu=ja
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] Konfiguration prüfen (caddy adapt in einem Wegwerf-Container ohne Netz)"
    echo "  [trocken] Stufe 1: Site-Datei $SITE_DATEI installieren und caddy reload (ohne Ausfall)"
    [ "$compose_neu" = nein ] || echo "  [trocken] Stufe 2: Ruhe prüfen, Compose von wamo-caddy ändern (Port $LABOR_PORT, Netz wamo-labor-netz), Container neu anlegen, dann 55443 bis 55446 prüfen"
    return 0
  fi
  if [ -z "$W" ]; then CADDY_OWNER="$(stat -c %U "$CADDY_DIR")"; CADDY_GROUP="$(stat -c %G "$CADDY_DIR")"; fi
  caddy_pruefen
  caddy_anwenden "$compose_neu"
}

# ---------- Start
undo_start() {
  echo "  FEHLER: Der Container wamo-labor-dienst wird wieder entfernt (Daten und Token bleiben)" >&2
  docker compose -f "$COMPOSE" down --remove-orphans >&2 || true
}

warten_gesund() {
  local runden="${WAMO_LABOR_WARTE_RUNDEN:-60}" sek="${WAMO_LABOR_WARTE_SEKUNDEN:-2}" i status=""
  [[ "$runden" =~ ^[0-9]+$ ]] || runden=60
  [[ "$sek" =~ ^[0-9]+$ ]] || sek=2
  for ((i = 0; i < runden; i++)); do
    status="$(docker inspect -f '{{.State.Health.Status}}' wamo-labor-dienst 2>/dev/null || echo fehlt)"
    if [ "$status" = healthy ]; then echo "  Container wamo-labor-dienst gesund"; return 0; fi
    sleep "$sek"
  done
  docker logs --tail 30 wamo-labor-dienst >&2 || true
  fehler "Container wamo-labor-dienst ist nach $((runden * sek)) Sekunden nicht gesund (Status: $status)."
}

start() {
  echo "== Start: Container wamo-labor-dienst"
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] $COMPOSE_QUELLE nach $COMPOSE installieren"
    echo "  [trocken] docker compose -f $COMPOSE up -d --no-build"
    return 0
  fi
  docker network inspect wamo-labor-netz >/dev/null 2>&1 || fehler "Das Netz wamo-labor-netz fehlt (zuerst der Schritt Caddy)."
  [ -f "$KERN_TOKEN_DATEI" ] && [ -f "$BETREUER_TOKEN_DATEI" ] || fehler "Die Token-Dateien fehlen (zuerst der Schritt Ordner und Token)."
  install -m 0644 "$COMPOSE_QUELLE" "$COMPOSE"
  UNDO+=(undo_start)
  docker compose -f "$COMPOSE" up -d --no-build
  warten_gesund
}

# ---------- Kern
kern_anbinden() {
  echo "== Kern: WAMO_LABOR_URL und WAMO_LABOR_TOKEN in /etc/wamo/core.env"
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] $CORE_ENV setzen: WAMO_LABOR_URL=$KERN_URL und der Kern-Token aus $KERN_TOKEN_DATEI (beides nur über stdin, nie angezeigt)"
  else
    { printf 'WAMO_LABOR_URL=%s\n' "$KERN_URL"; printf 'WAMO_LABOR_TOKEN='; tr -d '\r\n' < "$KERN_TOKEN_DATEI"; printf '\n'; } | "$CORE_ENV" setzen
  fi
  echo "  Der Kern liest die Werte erst mit seinem nächsten Kern-Release (sudo bash ~/wamo-infra/wamo-core-slots.sh umschalten-kern <release>)."
  echo "  Der Kern wird hier nie neu gestartet. Bis dahin gibt es keine Laborsitzung."
}

pruefen() {
  echo "== Prüfung (Rauchtest gegen den Betrieb)"
  if [ "$TROCKEN" = ja ]; then echo "  [trocken] python3 $HERE/wamo_labor_rauchtest.py --betrieb --ca $CA"; return 0; fi
  if [ -n "$W" ]; then echo "  (Probelauf: kein Rauchtest)"; return 0; fi
  python3 -I "$HERE/wamo_labor_rauchtest.py" --betrieb --ca "$CA"
}

datei_status() {
  if [ -f "$2" ]; then echo "  $1: vorhanden (Modus $(stat -c %a "$2"), Besitzer $(stat -c %u "$2"))"; else echo "  $1: fehlt"; fi
}

status() {
  echo "== Status"
  docker ps -a --filter 'name=^wamo-labor-' --format '  Container: {{.Names}} {{.Status}} ({{.Image}})' 2>/dev/null || true
  docker network inspect -f '  Netz: {{.Name}} intern={{.Internal}}' wamo-labor-netz wamo-labor-lokal 2>/dev/null || true
  if [ -f "$SITE_DATEI" ]; then echo "  Caddy-Site: vorhanden"; else echo "  Caddy-Site: fehlt"; fi
  if ipt -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $LABOR_PORT"; then
    echo "  Firewall: Port $LABOR_PORT hat eine Regel"
  else
    echo "  Firewall: Regel für $LABOR_PORT fehlt (oder keine Berechtigung)"
  fi
  datei_status kern-token "$KERN_TOKEN_DATEI"
  datei_status betreuer-token "$BETREUER_TOKEN_DATEI"
  datei_status betreuer-adressen "$BETREUER_ADRESSEN"
  datei_status plaetze-adressen "$PLAETZE_ADRESSEN"
  datei_status basis-image "$BASIS_IMAGE_DATEI"
  echo "  Kern-Einstellungen (nur Namen):"
  "$CORE_ENV" liste 2>/dev/null | grep '^WAMO_LABOR_' | sed 's/^/    /' || echo "    keine"
}

# ---------- Aufruf
while [ $# -gt 0 ]; do
  case "$1" in
    einrichten|status|pruefen) BEFEHL="$1" ;;
    --trocken) TROCKEN=ja ;;
    --basis-image) [ $# -ge 2 ] || usage; BASIS_IMAGE="$2"; shift ;;
    --quelle) [ $# -ge 2 ] || usage; QUELLE="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

if [ -n "$W" ]; then
  [[ "$W" =~ ^/[A-Za-z0-9/_.:~-]+$ ]] || fehler "WAMO_LABOR_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9 und / _ . : ~ - sein."
  echo "Probelauf unter $W (kein root, keine Firewall, kein Rauchtest)"
  [ "${WAMO_LABOR_HERKUNFT:-}" != ja ] || pruefe_ordner "$HERE"   # nur für Tests: der Herkunftsschutz auch im Probelauf
elif [ "$TROCKEN" = nein ] && { [ "$BEFEHL" = einrichten ] || [ "$BEFEHL" = pruefen ]; }; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_ordner "$HERE"
  [ "$BEFEHL" != einrichten ] || pruefe_ordner "$QUELLE"
fi

case "$BEFEHL" in
  status) status ;;
  pruefen) pruefen ;;
  einrichten)
    vorpruefung
    sichern
    ordner_und_token
    bilder
    firewall
    caddy
    start
    kern_anbinden
    pruefen
    ;;
esac
echo "Fertig."
