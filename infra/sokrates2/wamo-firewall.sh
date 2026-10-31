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
#   Port 55446 (HTTPS, Remote-Desktop-Gateway wamo-rd, docs/remote-desktop.md) wie die übrigen LAN-Ports: Loopback und Büro-LAN
#   (damit auch WireGuard-Clients der Fritzbox), alles andere verworfen.
#   Ausgang des Gateways (Netz wamo-rd-ziel 172.27.0.0/24, nur der Container wamo-rd-guacd): Kette WAMO-FW-RD. Durch kommt
#   ausschließlich TCP 3389 zu den Adressen, die in /etc/wamo-rd/ziele.toml als  adresse = "..."  stehen; ohne Eintrag geht nichts
#   hinaus. Für Pakete an den Host selbst (INPUT, z. B. sshd) gilt die Kette WAMO-FW-RDIN: aus dem Netz des Gateways nur TCP 3389
#   (dort entscheidet danach ufw), alles andere verworfen. Beide Ketten und der Sprung in INPUT entstehen und verschwinden mit
#   start und stop; fremde Regeln bleiben unberührt.
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
LAN_PORTS="55443 55444 55445 55446 55322 56543" # API (HTTPS), Kern (HTTPS, wamo-core über Caddy), Oberfläche (HTTPS, wamo-ui über Caddy), Remote-Desktop-Gateway (HTTPS, wamo-rd über Caddy), Pooler Session, Transaktion
NETZ_LOOPBACK="127.0.0.0/8"
NETZ_CADDY="172.24.0.0/24"          # wamo-caddy_default (fest in infra/sokrates2/wamo-caddy/docker-compose.yml)
NETZ_DOCKER="172.16.0.0/12"         # alle Docker-Netze, nur zur Erkennung gefälschter Absender
NETZ_LAN="192.168.178.0/24"         # Büro-LAN, auch WireGuard-Clients der Fritzbox
KETTE=WAMO-FW
KETTE_HTTP=WAMO-FW-HTTP
KETTE_LAN=WAMO-FW-LAN
KETTE_RD=WAMO-FW-RD                 # Ausgang des Gateways wamo-rd (FORWARD, über DOCKER-USER)
KETTE_RDIN=WAMO-FW-RDIN             # Pakete des Gateways an den Host selbst (INPUT)
NETZ_RD_ZIEL="172.27.0.0/24"        # wamo-rd-ziel, fest in infra/sokrates2/wamo-rd/docker-compose.yml
RD_PORT=3389
ZIELE_DATEI="${WAMO_FW_RD_ZIELE:-/etc/wamo-rd/ziele.toml}"
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

# Ziele des Remote-Desktop-Gateways: Zeilen  adresse = "a.b.c.d"  in ZIELE_DATEI (keine Datei oder kein Eintrag: keine Ziele).
# Ungültige Adressen werden ausgelassen und gemeldet (weniger erlaubt ist hier der sichere Fall); höchstens 8.
RD_ZIELE=""
lies_ziele() {
  RD_ZIELE=""
  [ -r "$ZIELE_DATEI" ] || return 0
  local zeile wert o n=0 ok
  while IFS= read -r zeile; do
    wert="${zeile#*=}"
    wert="${wert%%#*}"
    wert="$(tr -d " \t\r\"'" <<<"$wert")"
    ok=ja
    if [[ "$wert" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
      for o in "${BASH_REMATCH[@]:1}"; do (( 10#$o <= 255 )) || ok=nein; done
      { [ "$wert" != 0.0.0.0 ] && [[ "$wert" != 127.* ]]; } || ok=nein
    else
      ok=nein
    fi
    if [ "$ok" = nein ]; then echo "Zieladresse '$wert' in $ZIELE_DATEI ist ungültig und wird ausgelassen." >&2; continue; fi
    n=$((n + 1))
    if (( n > 8 )); then echo "Mehr als 8 Ziele in $ZIELE_DATEI, der Rest wird ausgelassen." >&2; break; fi
    RD_ZIELE="$RD_ZIELE $wert"
  done < <(grep -E '^[[:space:]]*adresse[[:space:]]*=' "$ZIELE_DATEI" || true)
}

ipt() { iptables -w 5 "$@"; }

regeln() {
  local port
  echo "*filter"
  echo ":$KETTE_HTTP - [0:0]"
  echo ":$KETTE_LAN - [0:0]"
  echo ":$KETTE - [0:0]"
  echo ":$KETTE_RD - [0:0]"
  echo ":$KETTE_RDIN - [0:0]"
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
  # Remote-Desktop-Gateway, Ausgang (Container wamo-rd-guacd im Netz wamo-rd-ziel): nur TCP 3389 zu den Zielen
  local ziel
  echo "-A $KETTE_RD ! -i br-+ -j DROP"
  for ziel in $RD_ZIELE; do
    echo "-A $KETTE_RD -d $ziel/32 -p tcp --dport $RD_PORT -j RETURN"
  done
  echo "-A $KETTE_RD $LOG"
  echo "-A $KETTE_RD -j DROP"
  echo "-A $KETTE -s $NETZ_RD_ZIEL -j $KETTE_RD"
  # Remote-Desktop-Gateway an den Host selbst (INPUT): nur TCP 3389, danach entscheidet ufw; alles andere verworfen
  echo "-A $KETTE_RDIN -p tcp --dport $RD_PORT -j RETURN"
  echo "-A $KETTE_RDIN $LOG"
  echo "-A $KETTE_RDIN -j DROP"
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

# Genau ein Sprung nach WAMO-FW-RDIN, und zwar an Position 1 von INPUT (nur für Pakete aus dem Netz des Gateways)
sprung_input_an_position_1() {
  local n
  [ "$(ipt -S INPUT | sed -n '2p')" = "-A INPUT -s $NETZ_RD_ZIEL -j $KETTE_RDIN" ] || ipt -I INPUT 1 -s "$NETZ_RD_ZIEL" -j "$KETTE_RDIN"
  while n="$(ipt -n -L INPUT --line-numbers | awk -v k="$KETTE_RDIN" 'NR > 2 && $2 == k && $1 > 1 { print $1; exit }')"
    [ -n "$n" ]; do
    ipt -D INPUT "$n"
  done
}

start() {
  local k
  lies_einstellung
  lies_ziele
  ipt -n -L DOCKER-USER >/dev/null 2>&1 || ipt -N DOCKER-USER  # Docker übernimmt die Kette später unverändert
  regeln | iptables-restore -w 5 --noflush
  sprung_an_position_1
  sprung_input_an_position_1
  for k in $ALTE_KETTEN; do ipt -F "$k" 2>/dev/null && ipt -X "$k" 2>/dev/null || true; done
  echo "WAMO-Firewall aktiv: HTTP $HTTP_PORT nur intern: $HTTP_NUR_INTERN; LAN-Ports $LAN_PORTS; Remote-Desktop-Ziele:${RD_ZIELE:- keine}"
}

stop() {
  local k
  while ipt -C DOCKER-USER -j "$KETTE" 2>/dev/null; do ipt -D DOCKER-USER -j "$KETTE"; done
  while ipt -C INPUT -s "$NETZ_RD_ZIEL" -j "$KETTE_RDIN" 2>/dev/null; do ipt -D INPUT -s "$NETZ_RD_ZIEL" -j "$KETTE_RDIN"; done
  for k in "$KETTE" "$KETTE_HTTP" "$KETTE_LAN" "$KETTE_RD" "$KETTE_RDIN" $ALTE_KETTEN; do ipt -F "$k" 2>/dev/null || true; done
  for k in "$KETTE" "$KETTE_HTTP" "$KETTE_LAN" "$KETTE_RD" "$KETTE_RDIN" $ALTE_KETTEN; do ipt -X "$k" 2>/dev/null || true; done
  echo "WAMO-Firewall entfernt"
}

status() {
  local k
  ipt -n -v -L DOCKER-USER --line-numbers
  for k in "$KETTE" "$KETTE_HTTP" "$KETTE_LAN" "$KETTE_RD" "$KETTE_RDIN"; do
    echo
    ipt -n -v -L "$k" 2>/dev/null || echo "Kette $k fehlt"
  done
}

als_root() { [ "$(id -u)" = 0 ] || { echo "Bitte als root ausführen (sudo)." >&2; exit 1; }; }
case "${1:-}" in
  start) als_root; start ;;
  stop) als_root; stop ;;
  status) als_root; status ;;
  regeln) lies_einstellung; lies_ziele; regeln ;;  # nur anzeigen, was start setzen würde (ohne root, ändert nichts)
  *) echo "Aufruf: $0 start|stop|status|regeln" >&2; exit 2 ;;
esac
