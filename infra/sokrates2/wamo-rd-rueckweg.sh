#!/usr/bin/env bash
# Rückweg zu wamo-rd-einrichten.sh: Remote-Desktop-Gateway auf Sokrates-2 abschalten. Als wamocon ausführen (nicht mit sudo):
#   ssh sokrates2 "bash ~/wamo-infra/wamo-rd-rueckweg.sh --trocken"      # zeigt nur an, was geschehen würde
#   ssh sokrates2 "bash ~/wamo-infra/wamo-rd-rueckweg.sh"                 # Container weg, Netze weg, Site weg
#   ssh sokrates2 "bash ~/wamo-infra/wamo-rd-rueckweg.sh --entfernen"     # zusätzlich Images, Compose-Ordner, Schlüssel, Benutzer
#
# Was geschieht ohne --entfernen:
#   1. Container wamo-rd-web und wamo-rd-guacd und ihre Netze wamo-rd-guacd-netz und wamo-rd-ziel entfernen (docker compose down)
#   2. Caddy-Site wamo-rd.caddy entfernen und Caddy neu laden (55443 bis 55445 bleiben unberührt)
# Was bleibt: Port 55446 im Compose von wamo-caddy, das Netz wamo-rd-netz (gehört zu wamo-caddy), die Regeln in der
# WAMO-Firewall (Port 55446, Ketten WAMO-FW-RD und WAMO-FW-RDIN) und die Dateien in /etc/wamo-rd. Ohne Site-Datei lauscht Caddy
# auf 55446 nicht, die Firewall-Regeln schaden nicht, und ohne Container gibt es keinen Ausgang. Wer die Regeln und den Port
# auch entfernen will, spielt die vorige Fassung von wamo-firewall.sh und wamo-caddy/docker-compose.yml ein (git revert, dann
# wamo-firewall-einrichten.sh bzw. wamo-core-caddy.sh).
#
# Zusätzlich mit --entfernen: Images (Guacamole), /home/wamocon/wamo-rd, /etc/wamo-rd/json.key, die leere Zielliste (nur wenn sie
# der Vorlage entspricht) und das leere Verzeichnis, der Benutzer wamo-rd-gateway. /var/lib/wamo-rd (Protokoll) bleibt, wenn
# es nicht leer ist.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASIS="${WAMO_RD_BASIS:-/home/wamocon/wamo-rd}"
CADDY_DIR="${WAMO_RD_CADDY_DIR:-/home/wamocon/wamo-tls/caddy}"
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-rd.caddy"
ETC_RD=/etc/wamo-rd
VAR_RD=/var/lib/wamo-rd
DIENST_BENUTZER=wamo-rd-gateway
# Schutz vor einer verirrten Umgebungsvariablen: gelöscht wird nur ein Ordner namens wamo-rd, nie die Wurzel oder ein Home
case "$BASIS" in /?*/wamo-rd) ;; *) echo "Ungültiger Ordner WAMO_RD_BASIS: $BASIS" >&2; exit 2 ;; esac

TROCKEN=nein
ENTFERNEN=nein
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=ja ;;
    --entfernen) ENTFERNEN=ja ;;
    *) echo "Aufruf: $0 [--trocken] [--entfernen]" >&2; exit 2 ;;
  esac
done

fehler() { echo "$*" >&2; exit 1; }
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}

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

echo "== 1. Container wamo-rd-web und wamo-rd-guacd, Netze"
if [ -f "$BASIS/docker-compose.yml" ]; then
  tu env -u WAMO_RD_UID -u WAMO_RD_GID docker compose -f "$BASIS/docker-compose.yml" down --remove-orphans
else
  echo "  kein Compose in $BASIS"
fi
# Nur die beiden festen Namen, nie ein Muster
tu docker rm -f wamo-rd-web wamo-rd-guacd
tu docker network rm wamo-rd-guacd-netz wamo-rd-ziel || true
echo "  entfernt (das Netz wamo-rd-netz gehört zu wamo-caddy und bleibt)"

echo "== 2. Caddy-Site"
if [ -f "$SITE_DATEI" ]; then
  tu rm -f "$SITE_DATEI"
  if [ "$TROCKEN" = ja ] || [ -n "$(docker ps -q -f name=^wamo-caddy$ 2>/dev/null || true)" ]; then
    # Schlägt der Reload fehl, geht der Rückweg trotzdem weiter (Site ist weg, Caddy übernimmt sie beim nächsten Laden)
    if tu docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
      echo "  Site entfernt, Caddy neu geladen"
    else
      echo "  WARNUNG: caddy reload fehlgeschlagen, Site-Datei ist entfernt" >&2
    fi
  fi
else
  echo "  keine Site vorhanden"
fi

if [ "$ENTFERNEN" = ja ]; then
  echo "== 3. Images, Compose-Ordner, Schlüssel, Benutzer"
  if [ -f "$HERE/wamo-rd/docker-compose.yml" ]; then
    while read -r bild; do
      [ -n "$bild" ] || continue
      tu docker image rm "$bild" || true
    done < <(grep -E '^[[:space:]]+image: ' "$HERE/wamo-rd/docker-compose.yml" | sed -E 's/^[[:space:]]+image:[[:space:]]+//' | tr -d '\r')
  fi
  tu rm -rf "$BASIS"
  tu sudo -n rm -f "$ETC_RD/json.key"
  if [ "$TROCKEN" = ja ] || { [ -f "$HERE/wamo-rd/ziele.toml.beispiel" ] && sudo -n cmp -s "$HERE/wamo-rd/ziele.toml.beispiel" "$ETC_RD/ziele.toml"; }; then
    tu sudo -n rm -f "$ETC_RD/ziele.toml"
  else
    echo "  $ETC_RD/ziele.toml enthält eigene Einträge und bleibt"
  fi
  tu sudo -n rmdir "$ETC_RD" || echo "  $ETC_RD ist nicht leer und bleibt"
  tu sudo -n rmdir "$VAR_RD" || echo "  $VAR_RD ist nicht leer und bleibt (Protokoll)"
  if [ "$TROCKEN" = ja ] || id "$DIENST_BENUTZER" >/dev/null 2>&1; then
    tu sudo -n userdel "$DIENST_BENUTZER"
  fi
  echo "  entfernt"
fi
echo "Fertig."
