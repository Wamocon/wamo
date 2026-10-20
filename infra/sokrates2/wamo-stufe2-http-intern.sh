#!/usr/bin/env bash
# Stufe 2 der WAMO-Firewall: Port 55321 (HTTP) nur noch für Loopback (127.0.0.0/8) und das Netz des HTTPS-Vorbaus
# wamo-caddy (172.24.0.0/24). Das Büro-LAN erreicht API und Anmeldung dann ausschließlich über HTTPS 55443; die
# Pooler-Ports 55322 und 56543 bleiben wie in Stufe 1 für das Büro-LAN offen.
#
# NICHT vorschnell ausführen. Voraussetzungen:
#   1. Auf jedem PC ist infra/sokrates2/wamo-ca.crt importiert (siehe infra/sokrates2/README.md, Abschnitt 5.2).
#   2. Auf jedem PC zeigen SUPABASE_URL (.env) und NEXT_PUBLIC_SUPABASE_URL (ui/.env.local) auf
#      https://192.168.178.136:55443 (python scripts/wamo_db_rolle.py stellt das um).
#   3. Supabase Studio wird nur noch über https://192.168.178.136:55443 aufgerufen.
#
# Ausführen auf Sokrates-2 mit sudo (Ordner im Home, siehe wamo-firewall-einrichten.sh):
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-stufe2-http-intern.sh --ja"        # Stufe 2 einschalten
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-stufe2-http-intern.sh --zurueck"   # zurück zu Stufe 1
# Die Regeln wechseln per "systemctl reload" atomar, ohne Lücke.
# Prüfen vom PC: curl -s -m 5 http://192.168.178.136:55321/auth/v1/health  -> Zeitüberschreitung (Stufe 2)
#                curl -s https://192.168.178.136:55443/auth/v1/health      -> Antwort (401 ohne Schlüssel)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONF=/etc/wamo/firewall.conf

# Schutz gegen untergeschobene Dateien (wie in wamo-firewall-einrichten.sh)
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

case "${1:-}" in
  --ja) wert=ja ;;
  --zurueck) wert=nein ;;
  *) echo "Aufruf: $0 --ja | --zurueck   (Voraussetzungen im Kopf dieses Skripts lesen)" >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
pruefe_herkunft
[ -x /usr/local/sbin/wamo-firewall ] && [ -f "$CONF" ] && systemctl is-active --quiet wamo-firewall.service \
  || { echo "WAMO-Firewall ist nicht eingerichtet oder nicht aktiv (zuerst wamo-firewall-einrichten.sh)." >&2; exit 1; }

if grep -qE '^[[:space:]]*WAMO_FW_HTTP_NUR_INTERN[[:space:]]*=' "$CONF"; then
  sed -i -E "s/^[[:space:]]*WAMO_FW_HTTP_NUR_INTERN[[:space:]]*=.*/WAMO_FW_HTTP_NUR_INTERN=$wert/" "$CONF"
else
  echo "WAMO_FW_HTTP_NUR_INTERN=$wert" >> "$CONF"
fi
systemctl reload wamo-firewall.service
/usr/local/sbin/wamo-firewall status
