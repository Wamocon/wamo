#!/usr/bin/env bash
# Remote-Desktop-Gateway auf Sokrates-2 (Stufe 1 des Konzepts docs/remote-desktop.md): Gateway OHNE Ziel und OHNE Zugangsdienst.
# Container wamo-rd-web (Apache Guacamole) und wamo-rd-guacd, HTTPS 55446 über wamo-caddy, Firewall, Benutzer wamo-rd-gateway,
# Schlüssel für die JSON-Anmeldung. Als wamocon ausführen (Docker-Gruppe, Dateien in /home/wamocon), nicht mit sudo; für Firewall
# und Konten ruft das Skript selbst "sudo -n" auf. Wiederholbar (idempotent).
#
# Ablage: Der Ordner liegt im Home von wamocon, nie in /tmp. Vom PC aus (wie bei wamo-ui, siehe README Abschnitt "Ablage"):
#   ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh sokrates2 "bash ~/wamo-infra/wamo-rd-einrichten.sh --trocken"          # zeigt nur an, was geschehen würde
#   ssh sokrates2 "bash ~/wamo-infra/wamo-rd-einrichten.sh"                     # alles
#
#   bash wamo-rd-einrichten.sh [alles|konto|bilder|firewall|caddy|start|pruefen|status] [--trocken]
#
#   konto      Benutzer wamo-rd-gateway (kein Login, keine Gruppen), /etc/wamo-rd, /var/lib/wamo-rd, Schlüssel json.key
#              (zufällig, 0640 root:wamo-rd-gateway, wird nie ausgegeben), leere Zielliste ziele.toml
#   bilder     die beiden Images über ihren Digest holen (nur wenn sie fehlen)
#   firewall   Port 55446 und die Ketten WAMO-FW-RD und WAMO-FW-RDIN (wamo-firewall-einrichten.sh, per systemctl reload)
#   caddy      Site-Datei erzeugen und prüfen, Compose von wamo-caddy anwenden (Port 55446, Netz wamo-rd-netz), Caddy neu laden.
#              Legt wamo-caddy EINMAL neu an, wenn sich dessen Compose ändert: 55443 bis 55445 sind dann wenige Sekunden weg.
#              Das geschieht nur ohne laufende Sitzung des Kerns.
#   start      Container wamo-rd-guacd und wamo-rd-web starten und auf "gesund" warten
#   pruefen    Rauchtest gegen den Betrieb (wamo_rd_rauchtest.py --betrieb)
#   status     nur anzeigen
#   --trocken  Lesen und Prüfen wie sonst, aber jede Änderung (sudo, docker pull/run, Dateien, Caddy, Firewall) nur anzeigen
#
# Reihenfolge bei "alles": konto, bilder, firewall, caddy, start, pruefen. Rückweg: wamo-rd-rueckweg.sh.
# Es werden keine Ziele eingetragen, kein Passwort angelegt und kein Zugangsdienst gestartet. Ohne Zugangsdienst kann sich
# niemand anmelden (die Webanwendung nimmt nur ein signiertes JSON aus der festen Adresse des Zugangsdienstes an).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASIS="${WAMO_RD_BASIS:-/home/wamocon/wamo-rd}"
CADDY_DIR="${WAMO_RD_CADDY_DIR:-/home/wamocon/wamo-tls/caddy}"
CA="${WAMO_RD_CA:-/home/wamocon/wamo-tls/ca/wamo-ca.crt}"
ETC_RD=/etc/wamo-rd
VAR_RD=/var/lib/wamo-rd
SCHLUESSEL="$ETC_RD/json.key"
ZIELE="$ETC_RD/ziele.toml"
DIENST_BENUTZER=wamo-rd-gateway
RD_PORT=55446
RD_URL="https://192.168.178.136:$RD_PORT"
CADDY_IMAGE=caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-rd.caddy"
COMPOSE_QUELLE="$HERE/wamo-rd/docker-compose.yml"
COMPOSE="$BASIS/docker-compose.yml"
# Netze: Name und Adresse stehen fest in den beiden Compose-Dateien (wamo-caddy legt wamo-rd-netz an, wamo-rd das Übrige)
NETZE="wamo-rd-netz=172.26.0.0/24 wamo-rd-guacd-netz=172.26.1.0/29 wamo-rd-ziel=172.27.0.0/24"

TROCKEN=nein
BEFEHL=alles
for arg in "$@"; do
  case "$arg" in
    alles|konto|bilder|firewall|caddy|start|pruefen|status) BEFEHL="$arg" ;;
    --trocken) TROCKEN=ja ;;
    *) echo "Aufruf: $0 [alles|konto|bilder|firewall|caddy|start|pruefen|status] [--trocken]" >&2; exit 2 ;;
  esac
done

fehler() { echo "$*" >&2; exit 1; }
# Im Trockenlauf wird eine Vorbedingung nur gemeldet (im Echtlauf bricht das Skript ab)
muss() {
  if [ "$TROCKEN" = ja ]; then echo "  WARNUNG (im Echtlauf Abbruch): $*" >&2; else fehler "$*"; fi
}
# Jede Änderung läuft über tu: im Trockenlauf wird sie nur angezeigt
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}
ARBEIT=""
trap 'rm -rf "${ARBEIT:-}"' EXIT

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten)
pruefe_herkunft() {
  local erlaubt uid modus pfad
  erlaubt=" 0 $(id -u) ${SUDO_UID:-} "
  while read -r uid modus pfad; do
    if [[ "$erlaubt" != *" $uid "* ]] || (( 8#$modus & 8#022 )); then
      echo "Unsicherer Ablageort: $pfad (Besitzer $uid, Rechte $modus). Ordner im eigenen Home ablegen und" \
        "mit chmod -R go-w schützen." >&2
      exit 1
    fi
  done < <(stat -c '%u %a %n' "$(dirname "$HERE")"; find "$HERE" \( -type l -printf '%U 777 %p\n' \) -o -printf '%U %m %p\n')
}

if [ "$TROCKEN" = nein ]; then
  [ "$(id -un)" = wamocon ] || fehler "Bitte als wamocon ausführen (nicht mit sudo)."
  pruefe_herkunft
fi
for f in wamo-rd/docker-compose.yml wamo-rd/wamo-rd.caddy.vorlage wamo-rd/ziele.toml.beispiel wamo-firewall.sh \
  wamo-firewall-einrichten.sh wamo-caddy/docker-compose.yml wamo_rd_rauchtest.py; do
  [ -f "$HERE/$f" ] || fehler "$HERE/$f fehlt (infra/sokrates2 vollständig verteilen)."
done

# Die beiden Images stehen nur in der Compose-Datei (eine Quelle der Wahrheit)
bilder_liste() { grep -E '^[[:space:]]+image: ' "$COMPOSE_QUELLE" | sed -E 's/^[[:space:]]+image:[[:space:]]+//' | tr -d '\r'; }

# Ruhe: keine Sitzung im Kern (wie wamo-ui-einrichten.sh): ein Neustart von wamo-caddy unterbräche das Gespräch kurz
kern_ruhig() {
  local pid udp log roh
  pid="$(systemctl show wamo-core -p MainPID --value 2>/dev/null || echo 0)"
  if [ -z "$pid" ] || [ "$pid" = 0 ]; then return 0; fi
  # Fail-closed: Lässt sich der Zustand nicht lesen, gilt der Kern als nicht ruhig
  sudo -n true 2>/dev/null || { echo "  Kern nicht prüfbar: sudo -n nicht verfügbar" >&2; return 1; }
  roh="$(sudo -n ss -uanpH 2>/dev/null)" || { echo "  Kern nicht prüfbar: ss fehlgeschlagen" >&2; return 1; }
  udp="$(grep -c "pid=$pid," <<<"$roh" || true)"
  roh="$(sudo -n journalctl -u wamo-core --since '-5 min' --no-pager -q 2>/dev/null)" \
    || { echo "  Kern nicht prüfbar: journalctl fehlgeschlagen" >&2; return 1; }
  log="$(grep -vE 'Zugang abgelehnt|401 Unauthorized|^$' <<<"$roh" | wc -l || true)"
  if [ "${udp:-0}" != 0 ] || [ "${log:-0}" != 0 ]; then
    echo "  Kern nicht ruhig: UDP-Sockets $udp, Journalzeilen der letzten 5 Minuten $log" >&2
    return 1
  fi
  return 0
}

caddy_neu_laden() { docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; }

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

vorpruefung() {
  echo "== Vorprüfung"
  command -v docker >/dev/null 2>&1 || muss "docker fehlt."
  docker compose version >/dev/null 2>&1 || muss "docker compose fehlt."
  netze_pruefen
  if [ -z "$(docker ps -q -f name=^wamo-caddy$ 2>/dev/null || true)" ] && ss -tlnH "sport = :$RD_PORT" 2>/dev/null | grep -q .; then
    muss "Port $RD_PORT ist schon belegt."
  fi
  echo "  Port $RD_PORT frei oder bei wamo-caddy"
}

# ---------- Konto, Verzeichnisse, Schlüssel
konto() {
  echo "== Benutzer $DIENST_BENUTZER, $ETC_RD, Schlüssel"
  if ! id "$DIENST_BENUTZER" >/dev/null 2>&1; then
    tu sudo -n useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
      --comment "WAMO Remote-Desktop-Gateway" "$DIENST_BENUTZER"
  else
    local gruppen
    gruppen="$(id -nG "$DIENST_BENUTZER")"
    [ "$gruppen" = "$DIENST_BENUTZER" ] || fehler "$DIENST_BENUTZER ist in weiteren Gruppen ($gruppen), Abbruch."
    echo "  Benutzer vorhanden: $(id "$DIENST_BENUTZER")"
  fi
  tu sudo -n passwd -l "$DIENST_BENUTZER"
  tu sudo -n install -d -m 0750 -o root -g "$DIENST_BENUTZER" "$ETC_RD"
  tu sudo -n install -d -m 0750 -o "$DIENST_BENUTZER" -g "$DIENST_BENUTZER" "$VAR_RD"
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] Schlüssel $SCHLUESSEL (32 Hexzeichen aus openssl rand, 0640 root:$DIENST_BENUTZER), falls er fehlt; wird nie ausgegeben"
  elif sudo -n test -f "$SCHLUESSEL"; then
    sudo -n grep -qE '^[0-9a-f]{32}$' "$SCHLUESSEL" || fehler "$SCHLUESSEL hat nicht das erwartete Format (32 Hexzeichen)."
    echo "  Schlüssel vorhanden (Inhalt wird nicht angezeigt)"
  else
    # Der Wert geht nur durch eine Pipe in die Datei: nie als Argument, nie auf den Bildschirm
    # (ohne Zeilenumbruch am Ende: Guacamole liest die Datei unverändert und lehnt ein "\n" als ungültiges Hex ab)
    openssl rand -hex 16 | tr -d '\n' | sudo -n bash -c 'umask 0137; cat > "$1"' _ "$SCHLUESSEL"
    echo "  Schlüssel angelegt (Inhalt wird nicht angezeigt)"
  fi
  tu sudo -n chown "root:$DIENST_BENUTZER" "$SCHLUESSEL"
  tu sudo -n chmod 0640 "$SCHLUESSEL"
  if [ "$TROCKEN" = ja ] || ! sudo -n test -f "$ZIELE"; then
    tu sudo -n install -m 0644 -o root -g root "$HERE/wamo-rd/ziele.toml.beispiel" "$ZIELE"
  else
    echo "  Zielliste vorhanden: $ZIELE (unverändert)"
  fi
}

# ---------- Images
bilder() {
  echo "== Images (über Digest)"
  local bild arch
  while read -r bild; do
    [ -n "$bild" ] || continue
    if docker image inspect "$bild" >/dev/null 2>&1; then
      echo "  vorhanden: ${bild%@*}"
    else
      tu docker pull "$bild"
    fi
    if arch="$(docker image inspect -f '{{.Architecture}}' "$bild" 2>/dev/null)"; then
      echo "    Architektur $arch (Host $(uname -m))"
    fi
  done < <(bilder_liste)
}

# ---------- Firewall
firewall() {
  echo "== Firewall: Port $RD_PORT, Ketten WAMO-FW-RD und WAMO-FW-RDIN"
  grep -q "$RD_PORT" "$HERE/wamo-firewall.sh" || fehler "$HERE/wamo-firewall.sh ohne Port $RD_PORT (infra/sokrates2 vollständig verteilen)."
  grep -q 'WAMO-FW-RDIN' "$HERE/wamo-firewall.sh" || fehler "$HERE/wamo-firewall.sh ohne Kette WAMO-FW-RDIN."
  if [ "$TROCKEN" = nein ] && sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $RD_PORT" \
    && sudo -n iptables -w 5 -S WAMO-FW-RD >/dev/null 2>&1 && sudo -n iptables -w 5 -S WAMO-FW-RDIN >/dev/null 2>&1; then
    echo "  Regeln stehen bereits"
    return 0
  fi
  tu sudo -n bash "$HERE/wamo-firewall-einrichten.sh"
  if [ "$TROCKEN" = nein ]; then
    sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $RD_PORT" || fehler "Firewall-Regel für $RD_PORT ist nicht aktiv."
    sudo -n iptables -w 5 -S WAMO-FW-RD >/dev/null 2>&1 || fehler "Kette WAMO-FW-RD fehlt."
    sudo -n iptables -w 5 -S INPUT | grep -q 'WAMO-FW-RDIN' || fehler "Sprung nach WAMO-FW-RDIN in INPUT fehlt."
    echo "  Regeln aktiv (per systemctl reload, ohne Lücke)"
  fi
}

# ---------- Caddy
site_erzeugen() {
  local text
  text="$(cat "$HERE/wamo-rd/wamo-rd.caddy.vorlage")"
  text="${text//__ADRESSE__/$RD_URL}"
  text="${text//__TLS__/tls internal}"
  text="${text//__WSS__/wss://${RD_URL#https://}}"
  printf '%s\n' "$text"
}

caddy() {
  echo "== Caddy: Site $RD_URL"
  if [ ! -d "$CADDY_DIR/etc" ]; then
    muss "$CADDY_DIR fehlt (zuerst wamo-tls-einrichten.sh)."
    echo "  [trocken] Site-Datei $SITE_DATEI aus der Vorlage; wamo-caddy-Compose; caddy reload"
    return 0
  fi
  local compose_neu=nein
  cmp -s "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_DIR/docker-compose.yml" || compose_neu=ja
  if [ "$compose_neu" = ja ]; then
    kern_ruhig || muss "Abbruch: Gerade läuft eine Sitzung. wamo-caddy wird neu angelegt, sobald niemand spricht (Skript erneut ausführen)."
  fi
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] Konfiguration prüfen (caddy adapt in einem Wegwerf-Container ohne Netz)"
    echo "  [trocken] Site-Datei $SITE_DATEI installieren"
    [ "$compose_neu" = nein ] || echo "  [trocken] Compose von wamo-caddy ändern (Port $RD_PORT, Netz wamo-rd-netz): Container wird neu angelegt"
    echo "  [trocken] caddy reload"
    return 0
  fi
  umask 077
  local tmp
  tmp="$(mktemp -d "$CADDY_DIR/.arbeit.XXXXXX")"
  ARBEIT="$tmp"
  mkdir -p "$tmp/etc"
  cp -a "$CADDY_DIR/etc/." "$tmp/etc/"
  mkdir -p "$tmp/etc/kern"
  site_erzeugen > "$tmp/etc/kern/wamo-rd.caddy"
  chmod 755 "$tmp" "$tmp/etc" "$tmp/etc/kern"
  chmod 644 "$tmp/etc/kern/"* "$tmp/etc/Caddyfile"
  # NET_BIND_SERVICE: Das Image versieht caddy mit dieser Fähigkeit, ohne sie im Begrenzungssatz startet das Programm nicht
  docker run --rm --network none --cap-drop ALL --cap-add NET_BIND_SERVICE --read-only --security-opt no-new-privileges:true \
    -v "$tmp/etc:/etc/caddy:ro" "$CADDY_IMAGE" caddy adapt --config /etc/caddy/Caddyfile >/dev/null
  echo "  Konfiguration gültig"
  # Rückweg bei einem Fehler: alte Site-Datei (oder keine) und altes Compose wiederherstellen
  local site_alt="$tmp/site.vorher" compose_alt="$tmp/compose.vorher"
  if [ -f "$SITE_DATEI" ]; then cp -a "$SITE_DATEI" "$site_alt"; fi
  if [ -f "$CADDY_DIR/docker-compose.yml" ]; then cp -a "$CADDY_DIR/docker-compose.yml" "$compose_alt"; fi
  zuruecksetzen() {
    echo "  FEHLER: Caddy-Änderung wird zurückgenommen" >&2
    if [ -f "$site_alt" ]; then install -m 644 "$site_alt" "$SITE_DATEI"; else rm -f "$SITE_DATEI"; fi
    if [ "$compose_neu" = ja ] && [ -f "$compose_alt" ]; then
      install -m 644 "$compose_alt" "$CADDY_DIR/docker-compose.yml"
      ( cd "$CADDY_DIR" && docker compose up -d ) >&2 || true
    else
      caddy_neu_laden >&2 || true
    fi
    fehler "Zurückgenommen. Bitte Protokoll prüfen (docker logs wamo-caddy)."
  }
  install -m 644 "$tmp/etc/kern/wamo-rd.caddy" "$SITE_DATEI"
  if [ "$compose_neu" = ja ]; then
    install -m 644 "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_DIR/docker-compose.yml"
    echo "  Compose von wamo-caddy geändert (Port $RD_PORT, Netz wamo-rd-netz): Container wird neu angelegt"
    ( cd "$CADDY_DIR" && docker compose up -d ) || zuruecksetzen
  else
    caddy_neu_laden || zuruecksetzen
    echo "  Caddy neu geladen"
  fi
  local code=000
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 --cacert "$CA" "https://192.168.178.136:55443/auth/v1/health" || true)"
    [ "$code" != 000 ] && break
    sleep 2
  done
  echo "  55443 (API): HTTP $code"
  [ "$code" != 000 ] || zuruecksetzen
}

# ---------- Container
start() {
  echo "== Container wamo-rd-guacd und wamo-rd-web"
  local uid gid
  if id "$DIENST_BENUTZER" >/dev/null 2>&1; then
    uid="$(id -u "$DIENST_BENUTZER")"
    gid="$(id -g "$DIENST_BENUTZER")"
  elif [ "$TROCKEN" = ja ]; then
    uid=64000; gid=64000
  else
    fehler "Benutzer $DIENST_BENUTZER fehlt (zuerst der Schritt konto)."
  fi
  [[ "$uid" =~ ^[0-9]+$ ]] && [[ "$gid" =~ ^[0-9]+$ ]] && [ "$uid" -ge 100 ] && [ "$gid" -ge 100 ] \
    || fehler "Unerwartete Kennung von $DIENST_BENUTZER ($uid:$gid)."
  tu install -d -m 755 "$BASIS"
  tu install -m 644 "$COMPOSE_QUELLE" "$COMPOSE"
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] WAMO_RD_UID=$uid WAMO_RD_GID=$gid docker compose -f $COMPOSE up -d"
    return 0
  fi
  docker network inspect wamo-rd-netz >/dev/null 2>&1 || fehler "Netz wamo-rd-netz fehlt (zuerst der Schritt caddy)."
  WAMO_RD_UID="$uid" WAMO_RD_GID="$gid" docker compose -f "$COMPOSE" up -d
  local c ok
  for _ in $(seq 1 60); do
    ok=ja
    for c in wamo-rd-guacd wamo-rd-web; do
      [ "$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo fehlt)" = healthy ] || ok=nein
    done
    [ "$ok" = ja ] && break
    sleep 2
  done
  [ "$ok" = ja ] || { docker logs --tail 30 wamo-rd-web >&2 || true; fehler "Container sind nach 2 Minuten nicht gesund."; }
  echo "  beide Container gesund"
}

pruefen() {
  echo "== Prüfung (Rauchtest gegen den Betrieb)"
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] python3 $HERE/wamo_rd_rauchtest.py --betrieb"
    return 0
  fi
  python3 -I "$HERE/wamo_rd_rauchtest.py" --betrieb --ca "$CA"
}

status() {
  echo "== Status"
  docker ps -a --filter 'name=^wamo-rd-' --format '  Container: {{.Names}} {{.Status}} ({{.Image}})' 2>/dev/null || true
  [ -f "$SITE_DATEI" ] && echo "  Caddy-Site: vorhanden" || echo "  Caddy-Site: fehlt"
  if sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $RD_PORT"; then
    echo "  Firewall: Port $RD_PORT offen für Loopback und Büro-LAN"
  else
    echo "  Firewall: Regel für $RD_PORT fehlt (oder sudo -n nicht möglich)"
  fi
  sudo -n iptables -w 5 -S WAMO-FW-RD 2>/dev/null | sed 's/^/  /' || true
  if id "$DIENST_BENUTZER" >/dev/null 2>&1; then echo "  Benutzer: $(id "$DIENST_BENUTZER")"; else echo "  Benutzer $DIENST_BENUTZER fehlt"; fi
  sudo -n ls -l "$ETC_RD" 2>/dev/null | sed 's/^/  /' || true
}

case "$BEFEHL" in
  status) status ;;
  konto) vorpruefung; konto ;;
  bilder) vorpruefung; bilder ;;
  firewall) firewall ;;
  caddy) vorpruefung; caddy ;;
  start) start ;;
  pruefen) pruefen ;;
  alles)
    vorpruefung
    konto
    bilder
    firewall
    caddy
    start
    pruefen
    ;;
esac
echo "Fertig."
