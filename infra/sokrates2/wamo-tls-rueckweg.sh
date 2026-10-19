#!/usr/bin/env bash
# Rückweg zu wamo-tls-einrichten.sh: WAMO-Stack wieder über HTTP 55321, Pooler ohne TLS, Caddy entfernt.
#
# Ausführen auf Sokrates-2 als wamocon (Ordner im Home, siehe wamo-tls-einrichten.sh):
#   ssh sokrates2 "bash ~/wamo-infra/wamo-tls-rueckweg.sh"
# CA und Zertifikate bleiben liegen, damit ein erneutes Einrichten ohne neuen Import auf den PCs auskommt.
# Mit der Compose-Ergänzung entfällt auch die Festlegung der Ports auf IPv4 (docker-proxy lauscht dann wieder
# auf [::]; ufw sperrt IPv6 eingehend weiterhin).
# Danach auf jedem PC: SUPABASE_URL und NEXT_PUBLIC_SUPABASE_URL auf http://192.168.178.136:55321, in
# WAMO_DATABASE_URL bzw. WAMO_ADMIN_DATABASE_URL den Zusatz ?sslmode=verify-full entfernen und WAMO_CA_FILE leeren
# (mit gesetztem WAMO_CA_FILE verlangt der Voice-Core geprüftes TLS zur Datenbank).
# Neu erstellt werden nur WAMO-Container, deren Konfiguration sich dadurch ändert.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_IP=192.168.178.136
API_PORT=55321
STACK=/home/wamocon/supabase-wamo
CADDY_DIR=/home/wamocon/wamo-tls/caddy
HTTP_URL="http://${HOST_IP}:${API_PORT}"

# Schutz gegen untergeschobene Dateien (wie in wamo-tls-einrichten.sh)
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
set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*$|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
geaenderte_dienste() {
  local svc hash cid cur
  docker compose config --hash '*' | while read -r svc hash; do
    cid="$(docker compose ps -q "$svc" 2>/dev/null | head -n1)"
    cur=""
    [ -n "$cid" ] && cur="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$cid")"
    [ "$cur" = "$hash" ] || printf '%s ' "$svc"
  done
}

echo "== 1. WAMO-Stack auf HTTP zurückstellen"
cd "$STACK"
set_env SUPABASE_PUBLIC_URL "$HTTP_URL"
set_env API_EXTERNAL_URL "$HTTP_URL/auth/v1"
compose_file="$(grep -m1 '^COMPOSE_FILE=' .env | cut -d= -f2- || true)"
compose_file="$(tr ':' '\n' <<<"${compose_file:-docker-compose.yml}" | grep -vx 'docker-compose.wamo-tls.yml' | paste -sd: -)"
set_env COMPOSE_FILE "${compose_file:-docker-compose.yml}"
chmod 600 .env
geaendert="$(geaenderte_dienste)"
if [ -n "${geaendert// /}" ]; then
  echo "  neu erstellt werden: $geaendert"
  # shellcheck disable=SC2086
  docker compose up -d --no-deps $geaendert
fi
rm -f docker-compose.wamo-tls.yml

echo "== 2. HTTPS-Vorbau entfernen"
if [ -f "$CADDY_DIR/docker-compose.yml" ]; then
  ( cd "$CADDY_DIR" && docker compose down )
fi

echo "== 3. Prüfung"
curl -s -o /dev/null -w "  Auth über HTTP: HTTP %{http_code}\n" --max-time 10 "$HTTP_URL/auth/v1/health" || true
echo "  Token-Aussteller (iss): $(docker exec wamo-supabase-auth printenv GOTRUE_JWT_ISSUER 2>/dev/null || echo unbekannt)"
echo "Fertig. Die Firewall-Regeln (wamo-firewall.service) bleiben unverändert; Port 55443 ist dann ungenutzt."
