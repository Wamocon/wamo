#!/usr/bin/env bash
# Rückweg zu wamo-pc-tunnel-einrichten.sh: Rücktunnel je Gerät abschalten.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-pc-tunnel-rueckweg.sh"             # Drop-in weg, alle Geräte gesperrt
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-pc-tunnel-rueckweg.sh --entfernen" # zusätzlich Programme, Units, Konfiguration
# Reihenfolge: zuerst Zeitgeber aus (sonst legt der Abgleich alles in 10 s wieder an), dann alle Schlüssel sperren, Sitzungen
# beenden, Ordner leeren, dann den sshd-Drop-in entfernen (vor dem reload immer sshd -t) und prüfen, dass sshd für wamocon
# und wamo-tunnel unverändert ist. Benutzer wamo-t-NN werden nie gelöscht (keine uid-Wiederverwendung), nur gesperrt.
# Der alte Tunnel (wamo-tunnel-einrichten.sh) bleibt unberührt. Die Datenbank wird nicht geändert; die Geräte bleiben im
# Register, ihr Tunnel ist aber aus, bis das Einrichtungsskript wieder läuft.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DROPIN=/etc/ssh/sshd_config.d/wamo-pc.conf
SCHLUESSEL=/etc/ssh/authorized_keys.d
GESPERRT="$SCHLUESSEL/gesperrt"

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

echo "== 1. Zeitgeber aus"
systemctl disable --now wamo-pc-abgleich.timer wamo-pc-streuner.timer 2>/dev/null || true
systemctl stop wamo-pc-abgleich.service wamo-pc-streuner.service 2>/dev/null || true

echo "== 2. Alle Geräte sperren (Schlüssel, Sitzungen, Ordner)"
mkdir -p -m 0700 "$GESPERRT"
anzahl=0
while IFS=: read -r name _; do
  [[ "$name" =~ ^wamo-t-(0[1-9]|[1-5][0-9]|6[0-4]|p[a-z])$ ]] || continue
  ordner="${name#wamo-t-}"
  [[ "$ordner" =~ ^p ]] || ordner="t$ordner"
  if [ -f "$SCHLUESSEL/$name" ]; then mv -f "$SCHLUESSEL/$name" "$GESPERRT/$name.$(date +%s)"; fi
  pkill -u "$name" 2>/dev/null || true
  # Inhalt des Geräteordners entfernen (ohne Links zu folgen), dann den Ordner
  if [ -d "/run/wamo-pc/$ordner" ] && [ ! -L "/run/wamo-pc/$ordner" ]; then
    find "/run/wamo-pc/$ordner" -mindepth 1 -maxdepth 1 -not -type d -delete 2>/dev/null || true
    rmdir "/run/wamo-pc/$ordner" 2>/dev/null || true
  fi
  passwd -l "$name" >/dev/null 2>&1 || true
  anzahl=$((anzahl + 1))
done < <(getent passwd)
echo "  $anzahl Gerätebenutzer gesperrt"
rm -f /etc/tmpfiles.d/wamo-pc.conf /etc/tmpfiles.d/wamo-pc-probe.conf

echo "== 3. sshd-Drop-in entfernen"
if [ -f "$DROPIN" ]; then
  global_vorher=$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)
  tunnel_vorher=$(/usr/sbin/sshd -T -C user=wamo-tunnel,host=pc,addr=192.168.178.2 2>/dev/null || true)
  sicherung=$(mktemp)
  cp -p "$DROPIN" "$sicherung"
  rm -f "$DROPIN"
  if /usr/sbin/sshd -t; then
    systemctl reload ssh.service
    echo "  Drop-in entfernt, sshd neu geladen"
  else
    install -o root -g root -m 0644 "$sicherung" "$DROPIN"
    rm -f "$sicherung"
    echo "  sshd -t meldet einen Fehler ohne den Drop-in: unverändert gelassen" >&2
    exit 1
  fi
  rm -f "$sicherung"
  if [ "$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)" = "$global_vorher" ] && \
     [ "$(/usr/sbin/sshd -T -C user=wamo-tunnel,host=pc,addr=192.168.178.2 2>/dev/null || true)" = "$tunnel_vorher" ]; then
    echo "  sshd für wamocon und wamo-tunnel unverändert"
  else
    echo "  Hinweis: sshd für wamocon oder wamo-tunnel weicht ab (ohne unseren Drop-in zu erwarten: nur wenn ein anderes Drop-in dazukam)" >&2
  fi
else
  echo "  kein Drop-in vorhanden"
fi
# Sitzungen, die der Reload nicht beendet hat
for n in $(getent passwd | cut -d: -f1 | grep -E '^wamo-t-' || true); do pkill -u "$n" 2>/dev/null || true; done

if [ "${1:-}" = --entfernen ]; then
  echo "== 4. Programme, Units und Konfiguration entfernen"
  rm -f /etc/systemd/system/wamo-pc-abgleich.service /etc/systemd/system/wamo-pc-abgleich.timer \
        /etc/systemd/system/wamo-pc-streuner.service /etc/systemd/system/wamo-pc-streuner.timer
  systemctl daemon-reload
  rm -rf /opt/wamo-pc
  rm -f /etc/wamo/pc-abgleich.conf /etc/tmpfiles.d/wamo-pc-wurzel.conf /run/wamo-pc-abgleich.lock
  rmdir /run/wamo-pc 2>/dev/null || true
  echo "  entfernt (die gesperrten Benutzer wamo-t-NN bleiben bewusst bestehen)"
fi
echo "Fertig. Kontrolle: ssh sokrates2 true (neue Verbindung als wamocon); der alte Tunnel wamo-tunnel läuft unverändert."
