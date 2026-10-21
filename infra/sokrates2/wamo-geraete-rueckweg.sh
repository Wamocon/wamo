#!/usr/bin/env bash
# Rückweg für den WAMO-Gerätedienst (wamo-geraete-einrichten.sh).
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-geraete-rueckweg.sh"              # Dienst und Filter aus, Dateien bleiben
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-geraete-rueckweg.sh --entfernen"  # zusätzlich Units, Code, venv, Hilfsprogramm
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-geraete-rueckweg.sh --alles"      # zusätzlich Konfiguration, Zugangsdaten,
#                                                                                 # Ablage (Scans!), Schlüssel, Benutzer
# Danach hat der Kern keine Bürogeräte-Werkzeuge mehr (er meldet "Bürogeräte nicht erreichbar"). Auf den Sparks den
# Benutzer wamo-status mit infra/sparks/wamo-status-rueckweg.sh entfernen.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
pruefe_herkunft() {
  local erlaubt=" 0 $(id -u) ${SUDO_UID:-} " uid modus pfad
  while read -r uid modus pfad; do
    if [[ "$erlaubt" != *" $uid "* ]] || (( 8#$modus & 8#022 )); then
      echo "Unsicherer Ablageort: $pfad (Besitzer $uid, Rechte $modus)." >&2
      exit 1
    fi
  done < <(stat -c '%u %a %n' "$(dirname "$HERE")"; find "$HERE" \( -type l -printf '%U 777 %p\n' \) -o -printf '%U %m %p\n')
}
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
pruefe_herkunft

systemctl disable --now wamo-geraete.service >/dev/null 2>&1 || true
systemctl disable --now wamo-geraete-egress.service >/dev/null 2>&1 || true
nft delete table inet wamo_geraete_egress 2>/dev/null || true
echo "Dienst und Ausgangsfilter aus."

if [ "${1:-}" = "--entfernen" ] || [ "${1:-}" = "--alles" ]; then
  rm -f /etc/systemd/system/wamo-geraete.service /etc/systemd/system/wamo-geraete-egress.service
  rm -rf /etc/systemd/system/wamo-geraete.service.d
  rm -f /etc/tmpfiles.d/wamo-geraete.conf /etc/wamo/geraete-egress.nft /usr/local/sbin/wamo-geraete-env
  rm -rf /opt/wamo-geraete /var/cache/wamo-geraete-build /run/wamo-geraete
  systemctl daemon-reload
  echo "Units, Code, venv und Hilfsprogramm entfernt."
fi
if [ "${1:-}" = "--alles" ]; then
  rm -rf /etc/wamo-geraete /var/lib/wamo-geraete
  if id wamo-geraete >/dev/null 2>&1; then userdel wamo-geraete; fi
  echo "Konfiguration, Zugangsdaten, Ablage, Schlüssel und Benutzer entfernt."
fi
if nft list table inet wamo_geraete_egress >/dev/null 2>&1; then
  echo "Tabelle inet wamo_geraete_egress besteht noch." >&2
  exit 1
fi
