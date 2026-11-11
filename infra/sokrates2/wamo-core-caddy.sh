#!/usr/bin/env bash
# HTTPS 55444 vor dem WAMO-Kern: Site-Datei, Port und Socket-Mount im Container wamo-caddy. Als wamocon ausführen
# (Docker-Gruppe, Dateien in /home/wamocon/wamo-tls/caddy gehören wamocon), nicht mit sudo:
#   ssh sokrates2 "bash ~/wamo-infra/wamo-core-caddy.sh"            # einrichten oder aktualisieren
#   ssh sokrates2 "bash ~/wamo-infra/wamo-core-caddy.sh --zurueck"  # Site entfernen (Port lauscht dann nicht mehr)
#   ssh sokrates2 "bash ~/wamo-infra/wamo-core-caddy.sh --route-zurueck"  # nur die Freigabe-Route weg: die Site der Version vor dem letzten Einrichten
#                                                                       # (Datei wamo-core.caddy.vorher) wird wiederhergestellt, der Kern bleibt erreichbar
#
# Voraussetzungen (prüft das Skript): /run/wamo-core aus wamo-core-einrichten.sh und /run/wamo-freigabe aus freigabe-einrichten.sh (sonst
# legte Docker die Verzeichnisse als root an) und die Firewall-Regel für 55444, bevor der Port veröffentlicht wird.
# Ein geänderter Port oder Mount erzwingt eine Neuanlage des Containers: HTTPS 55443 ist dann wenige Sekunden weg.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CADDY_DIR=/home/wamocon/wamo-tls/caddy
CA=/home/wamocon/wamo-tls/ca/wamo-ca.crt
CADDY_IMAGE=caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d
HOST_IP=192.168.178.136

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten)
pruefe_herkunft() {
  local erlaubt=" 0 $(id -u) ${SUDO_UID:-} " uid modus pfad
  while read -r uid modus pfad; do
    if [[ "$erlaubt" != *" $uid "* ]] || (( 8#$modus & 8#022 )); then
      echo "Unsicherer Ablageort: $pfad (Besitzer $uid, Rechte $modus). Ordner im eigenen Home ablegen und" \
        "mit chmod -R go-w schützen." >&2
      exit 1
    fi
  done < <(stat -c '%u %a %n' "$(dirname "$HERE")"; find "$HERE" \( -type l -printf '%U 777 %p\n' \) -o -printf '%U %m %p\n')
}

[ "$(id -un)" = wamocon ] || { echo "Bitte als wamocon ausführen (nicht mit sudo)." >&2; exit 1; }
pruefe_herkunft
[ -d "$CADDY_DIR/etc" ] || { echo "$CADDY_DIR fehlt (zuerst wamo-tls-einrichten.sh)." >&2; exit 1; }

neu_laden() {
  docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
}

if [ "${1:-}" = --zurueck ]; then
  rm -f "$CADDY_DIR/etc/kern/wamo-core.caddy"
  neu_laden
  echo "Site 55444 entfernt (Port und Mount bleiben im Compose, ohne Site lauscht Caddy dort nicht)."
  exit 0
fi
if [ "${1:-}" = --route-zurueck ]; then
  [ -f "$CADDY_DIR/etc/kern/wamo-core.caddy.vorher" ] || { echo "Keine Sicherung (wamo-core.caddy.vorher), nichts wiederherzustellen." >&2; exit 1; }
  install -m 644 "$CADDY_DIR/etc/kern/wamo-core.caddy.vorher" "$CADDY_DIR/etc/kern/wamo-core.caddy"
  neu_laden
  echo "Site 55444 auf die Fassung vor der Freigabe-Route zurückgesetzt."
  exit 0
fi
[ -z "${1:-}" ] || { echo "Aufruf: $0 [--zurueck|--route-zurueck]" >&2; exit 2; }

for f in wamo-caddy/Caddyfile wamo-caddy/docker-compose.yml wamo-caddy/kern/wamo-core.caddy wamo-caddy/kern/slot.inc.legacy; do
  [ -f "$HERE/$f" ] || { echo "$HERE/$f fehlt." >&2; exit 1; }
done
eigen="$(stat -c '%U:%g:%a' /run/wamo-core 2>/dev/null || true)"
[ "$eigen" = "wamo-core:1000:750" ] || { echo "/run/wamo-core: $eigen (zuerst sudo bash wamo-core-einrichten.sh)." >&2; exit 1; }
freigabe="$(stat -c '%U:%G:%a' /run/wamo-freigabe 2>/dev/null || true)"
[ "$freigabe" = "wamo-anker:wamocon:2750" ] || { echo "/run/wamo-freigabe: $freigabe (zuerst sudo bash freigabe-einrichten.sh)." >&2; exit 1; }
/usr/local/sbin/wamo-firewall regeln | grep -q 'ctorigdstport 55444' \
  || { echo "Firewall ohne Regel für 55444 (zuerst wamo-core-einrichten.sh)." >&2; exit 1; }
sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q 'ctorigdstport 55444' \
  || { echo "Firewall-Regel für 55444 ist nicht aktiv (sudo systemctl reload wamo-firewall)." >&2; exit 1; }
if [ -z "$(docker ps -q -f name=^wamo-caddy$)" ] && ss -tlnH "sport = :55444" | grep -q .; then
  echo "Port 55444 ist schon belegt, Abbruch." >&2; exit 1
fi

echo "== 1. Konfiguration prüfen (Kopie, Wegwerf-Container)"
umask 077
TMP="$(mktemp -d "$CADDY_DIR/.arbeit.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/etc/kern"
install -m 644 "$HERE/wamo-caddy/Caddyfile" "$TMP/etc/Caddyfile"
install -m 644 "$HERE/wamo-caddy/kern/wamo-core.caddy" "$TMP/etc/kern/wamo-core.caddy"
# Kern-Slots (Schritt 2.2): die Site bindet etc/kern/slot.inc ein. Eine live gesetzte Auswahl bleibt, sonst gilt die Standardroute der alten Unit.
if [ -f "$CADDY_DIR/etc/kern/slot.inc" ]; then
  install -m 644 "$CADDY_DIR/etc/kern/slot.inc" "$TMP/etc/kern/slot.inc"
else
  install -m 644 "$HERE/wamo-caddy/kern/slot.inc.legacy" "$TMP/etc/kern/slot.inc"
fi
chmod 755 "$TMP" "$TMP/etc" "$TMP/etc/kern"
docker run --rm -v "$TMP/etc:/etc/caddy:ro" "$CADDY_IMAGE" caddy adapt --config /etc/caddy/Caddyfile >/dev/null
echo "  Caddyfile gültig"

echo "== 2. Installieren"
install -d -m 755 "$CADDY_DIR/etc/kern"
install -m 644 "$TMP/etc/Caddyfile" "$CADDY_DIR/etc/Caddyfile"
if [ -f "$CADDY_DIR/etc/kern/wamo-core.caddy" ] && ! cmp -s "$TMP/etc/kern/wamo-core.caddy" "$CADDY_DIR/etc/kern/wamo-core.caddy"; then
  cp -p "$CADDY_DIR/etc/kern/wamo-core.caddy" "$CADDY_DIR/etc/kern/wamo-core.caddy.vorher"   # Endung .vorher: wird vom Import "kern/*.caddy" nicht geladen
fi
[ -f "$CADDY_DIR/etc/kern/slot.inc" ] || install -m 644 "$TMP/etc/kern/slot.inc" "$CADDY_DIR/etc/kern/slot.inc"   # vor der Site, die sie einbindet
install -m 644 "$TMP/etc/kern/wamo-core.caddy" "$CADDY_DIR/etc/kern/wamo-core.caddy"
alt_id="$(docker ps -q -f name=^wamo-caddy$)"
if ! cmp -s "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_DIR/docker-compose.yml"; then
  install -m 644 "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_DIR/docker-compose.yml"
  echo "  Compose geändert: Container wird neu angelegt (55443 kurz unterbrochen)"
fi
( cd "$CADDY_DIR" && docker compose up -d )
neu_id="$(docker ps -q -f name=^wamo-caddy$)"
if [ -n "$alt_id" ] && [ "$alt_id" = "$neu_id" ]; then
  neu_laden
  echo "  Caddyfile neu geladen"
fi

echo "== 3. Prüfung"
for i in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 --cacert "$CA" "https://$HOST_IP:55443/auth/v1/health" || true)"
  [ "$code" != 000 ] && break
  sleep 2
done
echo "  55443 (API): HTTP $code"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 --cacert "$CA" "https://$HOST_IP:55444/start" || true)"
case "$code" in
  401) echo "  55444 (Kern): HTTP 401 ohne Anmeldung, wie erwartet" ;;
  502) echo "  55444 (Kern): HTTP 502, Caddy läuft, der Kern noch nicht (im Slot-Betrieb: sudo bash ~/wamo-infra/wamo-core-slots.sh status, dann umschalten-kern <release>)" ;;
  *) echo "  55444 (Kern): HTTP $code (unerwartet)" >&2; docker logs --tail 20 wamo-caddy >&2; exit 1 ;;
esac
ergebnis="$(openssl s_client -connect "$HOST_IP:55444" -CAfile "$CA" -verify_ip "$HOST_IP" -verify_return_error -brief </dev/null 2>&1 || true)"
grep -q '^Verification: OK' <<<"$ergebnis" || { echo "  TLS auf 55444: Prüfung FEHLGESCHLAGEN" >&2; exit 1; }
echo "  TLS auf 55444: Kette und IP geprüft"
ipv6="$(ss -tlnH 'sport = :55444' | awk '{print $4}' | grep -F '[' || true)"
[ -z "$ipv6" ] || { echo "  55444 lauscht auf IPv6: $ipv6" >&2; exit 1; }
echo "Fertig."
