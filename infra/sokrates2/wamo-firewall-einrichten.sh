#!/usr/bin/env bash
# Richtet die WAMO-Firewall auf Sokrates-2 ein: Sicherung des Regelstands, Skript, Einstellung, systemd-Unit.
#
# Ausführen auf Sokrates-2 mit sudo, vom PC aus. Der Ordner liegt im Home von wamocon, nie in /tmp (dort könnte ein
# anderer Benutzer Dateien vorab anlegen oder austauschen, die dann als root laufen):
#   ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-firewall-einrichten.sh"
#
# Wiederholbar: Jeder Lauf legt eine neue Sicherung an (iptables-save und ip6tables-save mit Zeitstempel in
# /var/backups/wamo-firewall), aktualisiert Skript und Unit und übernimmt die Regeln per "systemctl reload"
# (atomar, ohne Lücke). Der Wert in /etc/wamo/firewall.conf (Stufe 1 oder 2) bleibt erhalten.
# Rückweg: wamo-firewall-rueckweg.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SICHERUNG=/var/backups/wamo-firewall
CONF=/etc/wamo/firewall.conf

# Schutz gegen untergeschobene Dateien: Der Ordner dieses Skripts, sein Elternordner und alles darin müssen dem
# Aufrufer (bei sudo dem aufrufenden Benutzer) oder root gehören und dürfen weder für die Gruppe noch für alle
# beschreibbar sein. Symbolische Links sind nicht erlaubt.
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
for f in wamo-firewall.sh wamo-firewall.service; do
  [ -f "$HERE/$f" ] || { echo "$HERE/$f fehlt." >&2; exit 1; }
done

echo "== 1. Prüfung vorab"
bash -n "$HERE/wamo-firewall.sh"
stufe=nein
if [ -f "$CONF" ]; then
  zeile="$(grep -E '^[[:space:]]*WAMO_FW_HTTP_NUR_INTERN[[:space:]]*=' "$CONF" | tail -n1 || true)"
  if [ -n "$zeile" ]; then
    wert="${zeile#*=}"
    wert="${wert%%#*}"
    stufe="$(tr -d " \t\r\"'" <<<"$wert" | tr '[:upper:]' '[:lower:]')"
  fi
fi
case "$stufe" in ja|nein) ;; *) echo "Ungültiger Wert in $CONF: '$stufe' (erlaubt: ja, nein)." >&2; exit 1 ;; esac
echo "  HTTP 55321 nur intern (Stufe 2): $stufe"
if grep -qE '^IPV6=yes' /etc/default/ufw 2>/dev/null; then
  echo "  ufw filtert IPv6 (IPV6=yes)"
else
  echo "  WARNUNG: /etc/default/ufw hat nicht IPV6=yes; IPv6 eingehend ist dann nicht durch ufw gesperrt." >&2
fi

echo "== 2. Sicherung des aktuellen Regelstands"
zeit="$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$SICHERUNG"
iptables-save > "$SICHERUNG/iptables-$zeit.rules"
ip6tables-save > "$SICHERUNG/ip6tables-$zeit.rules"
chmod 600 "$SICHERUNG/iptables-$zeit.rules" "$SICHERUNG/ip6tables-$zeit.rules"
echo "  $SICHERUNG/iptables-$zeit.rules"
echo "  $SICHERUNG/ip6tables-$zeit.rules"

echo "== 3. Skript, Einstellung und systemd-Unit"
install -m 755 -o root -g root "$HERE/wamo-firewall.sh" /usr/local/sbin/wamo-firewall
install -m 644 -o root -g root "$HERE/wamo-firewall.service" /etc/systemd/system/wamo-firewall.service
[ -d /etc/wamo ] || install -d -m 755 /etc/wamo
cat > "$CONF.neu" <<EOF
# WAMO-Firewall (wamo-firewall.service). Nach einer Änderung: sudo systemctl reload wamo-firewall
# Erlaubte Werte: nein (Stufe 1, Port 55321 auch aus dem Büro-LAN) oder ja (Stufe 2, Port 55321 nur noch aus
# Loopback und dem Caddy-Netz; das Büro-LAN erreicht WAMO dann nur über HTTPS 55443). Stufe 2 erst nach dem Import
# von wamo-ca.crt auf allen PCs (wamo-stufe2-http-intern.sh).
WAMO_FW_HTTP_NUR_INTERN=$stufe
EOF
chmod 644 "$CONF.neu"
chown root:root "$CONF.neu"
mv -f "$CONF.neu" "$CONF"
systemctl daemon-reload
systemctl enable wamo-firewall.service >/dev/null 2>&1
if systemctl is-active --quiet wamo-firewall.service; then
  systemctl reload wamo-firewall.service
  echo "  Regeln per reload übernommen"
else
  systemctl start wamo-firewall.service
  echo "  Unit gestartet"
fi

echo "== 4. Stand"
systemctl is-active wamo-firewall.service
systemctl show -p Before,After,Requires,PartOf,WantedBy wamo-firewall.service | sed 's/^/  /'
/usr/local/sbin/wamo-firewall status
