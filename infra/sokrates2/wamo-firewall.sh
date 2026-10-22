#!/usr/bin/env bash
# WAMO-Firewall auf Sokrates-2: Zugriff auf die WAMO-Ports nur aus Loopback, dem Büro-LAN und (nur für HTTP 55321)
# dem Netz des HTTPS-Vorbaus wamo-caddy.
# Installiert als /usr/local/sbin/wamo-firewall, gestartet von wamo-firewall.service (wamo-firewall-einrichten.sh).
#
# Warum DOCKER-USER: Docker veröffentlicht Ports per DNAT an ufw vorbei, die Pakete laufen durch FORWARD und dort
# zuerst durch die Kette DOCKER-USER. Nach dem DNAT steht im Paket der Containerport (z. B. 8000 statt 55321),
# deshalb vergleichen die Regeln den ursprünglichen Zielport aus conntrack (--ctorigdstport, nur Hinrichtung).
# Andere Ports, Ketten und Dienste auf Sokrates-2 bleiben unberührt. Über IPv6 sind die WAMO-Ports nicht
# veröffentlicht (Compose-Ergänzung legt sie auf 0.0.0.0 fest), ufw sperrt IPv6 eingehend zusätzlich.
#
#   Port 55321 (HTTP, Envoy)            127.0.0.0/8, 172.24.0.0/24 (Caddy, nur über eine Docker-Bridge),
#                                       Stufe 1 zusätzlich 192.168.178.0/24
#   Ports 55443, 55444, 55445, 55322, 56543    127.0.0.0/8, 192.168.178.0/24 (55444: HTTPS des Kerns, Phase P3;
#                                       55445: HTTPS der Oberfläche, Browser-Betrieb)
#   Absender aus 172.16.0.0/12, die nicht über eine Docker-Bridge (br-*) kommen, gelten als gefälscht.
#
#   wamo-firewall start    Regeln setzen; wiederholbar und atomar (iptables-restore --noflush); legt DOCKER-USER
#                          bei Bedarf selbst an, damit die Regeln schon vor dem Start von Docker stehen
#   wamo-firewall stop     eigene Regeln und Ketten entfernen
#   wamo-firewall status   Regeln mit Paketzählern anzeigen
#   wamo-firewall regeln   nur ausgeben, was start setzen würde (Probelauf, ohne root)
#
# Einstellung in /etc/wamo/firewall.conf (nur diese eine, die Datei wird nicht als Skript ausgeführt):
#   WAMO_FW_HTTP_NUR_INTERN=nein   Stufe 1: 55321 auch aus dem Büro-LAN (Übergang)
#   WAMO_FW_HTTP_NUR_INTERN=ja     Stufe 2: 55321 nur noch aus Loopback und dem Caddy-Netz
# Andere Werte sind ein Fehler; start ändert dann nichts.
set -euo pipefail

CONF="${WAMO_FW_CONF:-/etc/wamo/firewall.conf}"
HTTP_PORT=55321
LAN_PORTS="55443 55444 55445 55322 56543" # API (HTTPS), Kern (HTTPS, wamo-core über Caddy), Oberfläche (HTTPS, wamo-ui über Caddy), Pooler Session, Transaktion
NETZ_LOOPBACK="127.0.0.0/8"
NETZ_CADDY="172.24.0.0/24"          # wamo-caddy_default (fest in infra/sokrates2/wamo-caddy/docker-compose.yml)
NETZ_DOCKER="172.16.0.0/12"         # alle Docker-Netze, nur zur Erkennung gefälschter Absender
NETZ_LAN="192.168.178.0/24"         # Büro-LAN, auch WireGuard-Clients der Fritzbox
KETTE=WAMO-FW
KETTE_HTTP=WAMO-FW-HTTP
KETTE_LAN=WAMO-FW-LAN
ALTE_KETTEN="WAMO-FW-INTERN"        # aus früheren Fassungen, wird beim Start entfernt
LOG='-m limit --limit 6/min --limit-burst 10 -j LOG --log-prefix "WAMO-FW verworfen: " --log-level 4'

lies_einstellung() {
  HTTP_NUR_INTERN=nein
  [ -e "$CONF" ] || return 0
  [ -r "$CONF" ] || { echo "$CONF ist nicht lesbar." >&2; exit 1; }
  local zeile wert
  zeile="$(grep -E '^[[:space:]]*WAMO_FW_HTTP_NUR_INTERN[[:space:]]*=' "$CONF" | tail -n1 || true)"
  [ -n "$zeile" ] || return 0
  wert="${zeile#*=}"
  wert="${wert%%#*}"
  wert="$(tr -d " \t\r\"'" <<<"$wert" | tr '[:upper:]' '[:lower:]')"
  case "$wert" in
    ja|nein) HTTP_NUR_INTERN="$wert" ;;
    *) echo "Ungültiger Wert für WAMO_FW_HTTP_NUR_INTERN in $CONF: '$wert' (erlaubt: ja, nein)." >&2; exit 1 ;;
  esac
}

ipt() { iptables -w 5 "$@"; }

regeln() {
  local port
  echo "*filter"
  echo ":$KETTE_HTTP - [0:0]"
  echo ":$KETTE_LAN - [0:0]"
  echo ":$KETTE - [0:0]"
  # 55321: Docker-Absender nur echt, wenn sie über eine Docker-Bridge kommen
  echo "-A $KETTE_HTTP -s $NETZ_DOCKER ! -i br-+ -j DROP"
  echo "-A $KETTE_HTTP -s $NETZ_LOOPBACK -j RETURN"
  echo "-A $KETTE_HTTP -s $NETZ_CADDY -i br-+ -j RETURN"
  [ "$HTTP_NUR_INTERN" = ja ] || echo "-A $KETTE_HTTP -s $NETZ_LAN -j RETURN"
  echo "-A $KETTE_HTTP $LOG"
  echo "-A $KETTE_HTTP -j DROP"
  # 55443, 55444, 55445 und Pooler: Loopback und Büro-LAN
  echo "-A $KETTE_LAN -s $NETZ_LOOPBACK -j RETURN"
  echo "-A $KETTE_LAN -s $NETZ_LAN -j RETURN"
  echo "-A $KETTE_LAN $LOG"
  echo "-A $KETTE_LAN -j DROP"
  echo "-A $KETTE -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport $HTTP_PORT -j $KETTE_HTTP"
  for port in $LAN_PORTS; do
    echo "-A $KETTE -p tcp -m conntrack --ctdir ORIGINAL --ctorigdstport $port -j $KETTE_LAN"
  done
  echo "COMMIT"
}

# Genau ein Sprung nach WAMO-FW, und zwar an Position 1 von DOCKER-USER
sprung_an_position_1() {
  local n
  [ "$(ipt -S DOCKER-USER | sed -n '2p')" = "-A DOCKER-USER -j $KETTE" ] || ipt -I DOCKER-USER 1 -j "$KETTE"
  while n="$(ipt -n -L DOCKER-USER --line-numbers | awk -v k="$KETTE" 'NR > 2 && $2 == k && $1 > 1 { print $1; exit }')"
    [ -n "$n" ]; do
    ipt -D DOCKER-USER "$n"
  done
}

start() {
  local k
  lies_einstellung
  ipt -n -L DOCKER-USER >/dev/null 2>&1 || ipt -N DOCKER-USER  # Docker übernimmt die Kette später unverändert
  regeln | iptables-restore -w 5 --noflush
  sprung_an_position_1
  for k in $ALTE_KETTEN; do ipt -F "$k" 2>/dev/null && ipt -X "$k" 2>/dev/null || true; done
  echo "WAMO-Firewall aktiv: HTTP $HTTP_PORT nur intern: $HTTP_NUR_INTERN; LAN-Ports $LAN_PORTS"
}

stop() {
  local k
  while ipt -C DOCKER-USER -j "$KETTE" 2>/dev/null; do ipt -D DOCKER-USER -j "$KETTE"; done
  for k in "$KETTE" "$KETTE_HTTP" "$KETTE_LAN" $ALTE_KETTEN; do ipt -F "$k" 2>/dev/null || true; done
  for k in "$KETTE" "$KETTE_HTTP" "$KETTE_LAN" $ALTE_KETTEN; do ipt -X "$k" 2>/dev/null || true; done
  echo "WAMO-Firewall entfernt"
}

status() {
  local k
  ipt -n -v -L DOCKER-USER --line-numbers
  for k in "$KETTE" "$KETTE_HTTP" "$KETTE_LAN"; do
    echo
    ipt -n -v -L "$k" 2>/dev/null || echo "Kette $k fehlt"
  done
}

als_root() { [ "$(id -u)" = 0 ] || { echo "Bitte als root ausführen (sudo)." >&2; exit 1; }; }
case "${1:-}" in
  start) als_root; start ;;
  stop) als_root; stop ;;
  status) als_root; status ;;
  regeln) lies_einstellung; regeln ;;  # nur anzeigen, was start setzen würde (ohne root, ändert nichts)
  *) echo "Aufruf: $0 start|stop|status|regeln" >&2; exit 2 ;;
esac
