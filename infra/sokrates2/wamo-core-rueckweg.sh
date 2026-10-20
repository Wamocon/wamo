#!/usr/bin/env bash
# Rückweg zu wamo-core-einrichten.sh: WAMO-Kern auf Sokrates-2 abschalten.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-rueckweg.sh"             # Dienst aus, DB-Login gesperrt, Dateien bleiben
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-rueckweg.sh --entfernen" # zusätzlich Code, venv, core.env, Benutzer,
#                                                                              Rolle wamo_core_s2, tmpfiles, Hilfsprogramm
# Caddy-Site 55444: bash ~/wamo-infra/wamo-core-caddy.sh --zurueck (als wamocon). Firewall-Port 55444 bleibt in
# WAMO-FW-LAN (schadet nicht); entfernen über das vorherige wamo-firewall.sh und wamo-firewall-einrichten.sh.
# Tunnel: wamo-tunnel-rueckweg.sh. Die Oberfläche und die Desktop-App auf Unterwegs stellen.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DB_CONTAINER=wamo-supabase-db
ROLLE=wamo_core_s2

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

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
pruefe_herkunft

systemctl disable --now wamo-core.service >/dev/null 2>&1 || true
echo "wamo-core: $(systemctl is-active wamo-core.service 2>/dev/null || true)"
rm -f /run/wamo-core/core.sock

psql_db() { docker exec -i "$DB_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -q -tA; }
if docker ps -q -f "name=^${DB_CONTAINER}$" | grep -q .; then
  if [ "${1:-}" = --entfernen ]; then
    printf "do \$\$ begin if exists (select 1 from pg_roles where rolname = '%s') then\n  execute 'drop role %s';\nend if; end \$\$;\n" "$ROLLE" "$ROLLE" | psql_db
    echo "Rolle $ROLLE entfernt"
  else
    printf "do \$\$ begin if exists (select 1 from pg_roles where rolname = '%s') then\n  execute 'alter role %s nologin';\nend if; end \$\$;\n" "$ROLLE" "$ROLLE" | psql_db
    echo "Rolle $ROLLE: Anmeldung gesperrt (nologin)"
  fi
  printf "select pg_terminate_backend(pid) from pg_stat_activity where usename = '%s';\n" "$ROLLE" | psql_db >/dev/null || true
fi

if [ "${1:-}" = --entfernen ]; then
  rm -f /etc/systemd/system/wamo-core.service
  systemctl daemon-reload
  rm -rf /opt/wamo-core /var/cache/wamo-core-build /var/lib/wamo-core /var/cache/wamo-core
  rm -f /etc/wamo/core.env /usr/local/sbin/wamo-core-env /etc/tmpfiles.d/wamo-core.conf
  rmdir /run/wamo-core 2>/dev/null || true
  if id wamo-core >/dev/null 2>&1; then
    if id wamo-tunnel >/dev/null 2>&1; then
      echo "Benutzer wamo-core bleibt (Gruppe wird noch von /run/wamo-tunnel genutzt; zuerst wamo-tunnel-rueckweg.sh --entfernen)."
    else
      userdel wamo-core
      echo "Benutzer wamo-core entfernt"
    fi
  fi
  echo "Code, venv, core.env, Hilfsprogramm und tmpfiles entfernt."
fi
