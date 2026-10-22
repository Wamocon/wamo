#!/usr/bin/env bash
# Rückweg zu wamo-ui-einrichten.sh: Oberfläche auf Sokrates-2 abschalten. Als wamocon ausführen (nicht mit sudo):
#   ssh sokrates2 "bash ~/wamo-infra/wamo-ui-rueckweg.sh"                       # Container weg, Site weg, Drop-in weg
#   ssh sokrates2 "bash ~/wamo-infra/wamo-ui-rueckweg.sh --neustart"            # zusätzlich wamo-core neu starten (nur ohne Sitzung)
#   ssh sokrates2 "bash ~/wamo-infra/wamo-ui-rueckweg.sh --entfernen"           # zusätzlich Images, Einstellungen, Quellen löschen
#
# Was bleibt: Port 55445 im Compose von wamo-caddy und in WAMO-FW-LAN sowie das Netz wamo-ui-netz. Ohne Site-Datei
# lauscht Caddy auf 55445 nicht, die Firewall-Regel schadet nicht. Wer sie entfernen will, spielt die vorige Fassung von
# wamo-firewall.sh und wamo-caddy/docker-compose.yml ein (git revert, dann wamo-firewall-einrichten.sh bzw.
# wamo-core-caddy.sh). 55443 und 55444 bleiben bei der Rücknahme der Site unberührt (caddy reload).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASIS=/home/wamocon/wamo-ui
CADDY_DIR=/home/wamocon/wamo-tls/caddy
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-ui.caddy"
INCLUDE_DATEI="$CADDY_DIR/etc/kern/wamo-ui-aktiv.inc"
DROPIN_DIR=/etc/systemd/system/wamo-core.service.d
DROPIN="$DROPIN_DIR/wamo-ui.conf"

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

NEUSTART=nein
ENTFERNEN=nein
for arg in "$@"; do
  case "$arg" in
    --neustart) NEUSTART=ja ;;
    --entfernen) ENTFERNEN=ja ;;
    *) echo "Aufruf: $0 [--neustart] [--entfernen]" >&2; exit 2 ;;
  esac
done

echo "== 1. Container wamo-ui-blau, wamo-ui-gruen und wamo-ui (frühere Fassung)"
if [ -f "$BASIS/docker-compose.yml" ]; then
  ( cd "$BASIS" && docker compose down --remove-orphans )
fi
docker rm -f wamo-ui-blau wamo-ui-gruen wamo-ui >/dev/null 2>&1 || true
echo "  entfernt"

echo "== 2. Caddy-Site und Include-Datei"
if [ -f "$SITE_DATEI" ] || [ -f "$INCLUDE_DATEI" ]; then
  # Erst die Site (sie bindet die Include-Datei ein), dann die Include-Datei
  rm -f "$SITE_DATEI" "$INCLUDE_DATEI"
  if [ -n "$(docker ps -q -f name=^wamo-caddy$)" ]; then
    # Schlägt der Reload fehl, geht der Rückweg trotzdem weiter (Site ist weg, Caddy übernimmt sie beim nächsten Laden)
    docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile       && echo "  Site entfernt, Caddy neu geladen" || echo "  WARNUNG: caddy reload fehlgeschlagen, Site-Datei ist entfernt" >&2
  fi
else
  echo "  keine Site vorhanden"
fi

echo "== 3. Erlaubte Herkunft des Kerns"
if sudo -n test -f "$DROPIN"; then
  sudo -n rm -f "$DROPIN"
  sudo -n rmdir "$DROPIN_DIR" 2>/dev/null || true
  sudo -n systemctl daemon-reload
  echo "  Drop-in entfernt (der laufende Kern kennt die Herkunft bis zu seinem nächsten Start weiter)"
  if [ "$NEUSTART" = ja ]; then
    # Fail-closed wie in wamo-ui-einrichten.sh: Nur wenn sich der Zustand sicher lesen lässt und keine Sitzung läuft
    pid="$(systemctl show wamo-core -p MainPID --value)"
    ruhig=nein
    if sudo -n true 2>/dev/null && roh_ss="$(sudo -n ss -uanpH 2>/dev/null)"         && roh_log="$(sudo -n journalctl -u wamo-core --since '-5 min' --no-pager -q 2>/dev/null)"; then
      udp="$(grep -c "pid=$pid," <<<"$roh_ss" || true)"
      log="$(grep -vE 'Zugang abgelehnt|401 Unauthorized|^$' <<<"$roh_log" | wc -l || true)"
      if [ "${udp:-0}" = 0 ] && [ "${log:-0}" = 0 ]; then ruhig=ja; fi
    fi
    if [ "$ruhig" = ja ]; then
      sudo -n systemctl restart wamo-core
      sleep 5
      systemctl is-active --quiet wamo-core && echo "  wamo-core neu gestartet" || { echo "  wamo-core läuft nicht!" >&2; exit 1; }
    else
      echo "  Neustart NICHT ausgeführt: Es läuft eine Sitzung (später: sudo systemctl restart wamo-core)." >&2
    fi
  else
    echo "  Neustart von wamo-core (wenn niemand spricht): sudo systemctl restart wamo-core"
  fi
else
  echo "  kein Drop-in vorhanden"
fi

if [ "$ENTFERNEN" = ja ]; then
  echo "== 4. Images, Einstellungen, Quellen"
  docker image ls wamo-ui --format '{{.Repository}}:{{.Tag}}' | while read -r bild; do docker image rm "$bild" >/dev/null 2>&1 || true; done
  rm -rf "$BASIS" "$HERE/ui"
  echo "  entfernt (Einstellungen $BASIS und Quellen $HERE/ui)"
fi
echo "Fertig."
