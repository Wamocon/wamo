#!/usr/bin/env bash
# Rückweg zu wamo-tunnel-einrichten.sh: SSH-Rücktunnel vom PC abschalten.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-tunnel-rueckweg.sh"             # Drop-in und Schlüssel weg, Sitzungen beendet
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-tunnel-rueckweg.sh --entfernen" # zusätzlich Benutzer und tmpfiles
# Vor dem reload immer sshd -t; danach prüft das Skript, dass sshd für wamocon unverändert ist.
# Auf dem PC: scripts\wamo-tunnel-entfernen.ps1 [-Alles].
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DROPIN=/etc/ssh/sshd_config.d/wamo-tunnel.conf
KEYS=/etc/ssh/authorized_keys.d/wamo-tunnel

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

rm -f "$KEYS"
echo "Schlüssel entfernt: $KEYS"
if [ -f "$DROPIN" ]; then
  sicherung=$(mktemp)
  cp -p "$DROPIN" "$sicherung"
  rm -f "$DROPIN"
  if /usr/sbin/sshd -t; then
    systemctl reload ssh.service
    echo "sshd-Drop-in entfernt, sshd neu geladen"
  else
    install -o root -g root -m 0644 "$sicherung" "$DROPIN"
    echo "sshd -t meldet einen Fehler ohne den Drop-in: unverändert gelassen" >&2
    rm -f "$sicherung"
    exit 1
  fi
  rm -f "$sicherung"
fi
# laufende Tunnel-Sitzungen beenden (nur Prozesse des Benutzers wamo-tunnel)
if id wamo-tunnel >/dev/null 2>&1; then
  pkill -u wamo-tunnel sshd 2>/dev/null || true
fi
rm -f /run/wamo-tunnel/pc.sock
/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2 | grep -E '^(permittty|allowtcpforwarding|maxsessions) ' | sed 's/^/  wamocon: /'

if [ "${1:-}" = --entfernen ]; then
  rm -f /etc/tmpfiles.d/wamo-tunnel.conf
  rmdir /run/wamo-tunnel 2>/dev/null || true
  if id wamo-tunnel >/dev/null 2>&1; then userdel wamo-tunnel; echo "Benutzer wamo-tunnel entfernt"; fi
fi
echo "Fertig. Kontrolle: ssh sokrates2 true (neue Verbindung als wamocon)"
