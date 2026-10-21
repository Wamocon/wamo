#!/usr/bin/env bash
# Rückweg für wamo-status auf einem Spark: Schlüssel und Drop-in weg (sshd -t, reload), Sitzungen beenden, mit
# --entfernen auch Programm, Konfiguration und Benutzer.
#   ssh sokrates1 "sudo bash ~/wamo-infra-sparks/wamo-status-rueckweg.sh [--entfernen]"
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
rm -f /etc/ssh/authorized_keys.d/wamo-status
if [ -f /etc/ssh/sshd_config.d/wamo-status.conf ]; then
  mv /etc/ssh/sshd_config.d/wamo-status.conf /root/wamo-status.conf.entfernt
  if /usr/sbin/sshd -t; then
    systemctl reload ssh.service || true
  else
    mv /root/wamo-status.conf.entfernt /etc/ssh/sshd_config.d/wamo-status.conf
    echo "sshd -t ohne Drop-in fehlerhaft: Drop-in bleibt, bitte von Hand prüfen." >&2
    exit 1
  fi
fi
pkill -u wamo-status 2>/dev/null || true
if [ "${1:-}" = "--entfernen" ]; then
  id wamo-status >/dev/null 2>&1 && userdel wamo-status
  rm -f /usr/local/bin/wamo-status /etc/wamo/status-dienste.conf /root/wamo-status.conf.entfernt
  rmdir /etc/wamo 2>/dev/null || true
fi
echo "wamo-status abgeschaltet."
