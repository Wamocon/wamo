#!/usr/bin/env bash
# Rückweg für den WAMO-Ausgangsfilter (wamo-egress.service, nft-Tabelle inet wamo_egress).
#
# Ausführen auf Sokrates-2 mit sudo (Ordner im Home, siehe wamo-job-einrichten.sh):
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-egress-rueckweg.sh"              # Filter abschalten, Dateien bleiben
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-egress-rueckweg.sh --entfernen"  # zusätzlich Unit und Regeldatei löschen
#
# Achtung: Ohne den Filter besteht der Selbsttest des Auftragsdispatchers nicht mehr (Kanarienvogel), es startet
# dann kein Entwicklungsauftrag. Andere Tabellen (ufw, Docker, iptables) bleiben unberührt.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

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

[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
pruefe_herkunft

systemctl disable --now wamo-egress.service >/dev/null 2>&1 || true
nft delete table inet wamo_egress 2>/dev/null || true

if [ "${1:-}" = "--entfernen" ]; then
  rm -f /etc/systemd/system/wamo-egress.service /etc/wamo/egress.nft
  systemctl daemon-reload
  echo "Unit und Regeldatei entfernt."
fi
if nft list table inet wamo_egress >/dev/null 2>&1; then
  echo "Tabelle inet wamo_egress besteht noch." >&2
  exit 1
fi
echo "WAMO-Ausgangsfilter entfernt."
