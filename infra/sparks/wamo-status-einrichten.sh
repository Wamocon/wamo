#!/usr/bin/env bash
# Nur-Lese-Benutzer wamo-status auf einem Spark (Sokrates-1 oder Caesar, F6): der WAMO-Gerätedienst auf Sokrates-2
# holt darüber Auslastung, GPU und Dienststatus. Sonst ändert dieses Skript nichts am Server. Wiederholbar.
#
# Vom PC aus (Repo-Wurzel); Ablage im Home des Admins, nie in /tmp. Den öffentlichen Schlüssel des Gerätedienstes
# zeigt wamo-geraete-einrichten.sh auf Sokrates-2 am Ende an:
#   ssh sokrates2 "sudo cat /var/lib/wamo-geraete/ssh/id_ed25519.pub" > wamo-status.pub
#   ssh sokrates1 "rm -rf ~/wamo-infra-sparks && mkdir ~/wamo-infra-sparks"
#   scp infra/sparks/* wamo-status.pub sokrates1:wamo-infra-sparks/
#   ssh sokrates1 "chmod -R go-w ~/wamo-infra-sparks && sudo bash ~/wamo-infra-sparks/wamo-status-einrichten.sh"
#   danach binnen 5 Minuten über eine NEUE Verbindung bestätigen (sonst nimmt ein Zeitschalter den Drop-in zurück):
#   ssh sokrates1 "sudo touch /run/wamo-status-sshd-bestaetigt"
# Rückweg: wamo-status-rueckweg.sh
#
# Was das Skript am System ändert (nur WAMO):
#   Benutzer wamo-status                  System, keine Gruppen (insbesondere nicht docker), Home /nonexistent,
#                                         Login-Shell /usr/local/bin/wamo-status (das Programm selbst, keine Shell)
#   /usr/local/bin/wamo-status            root 0755, Nur-Lese-Bericht (status, gpu, dienste, alles)
#   /etc/wamo/status-dienste.conf         Units, deren Zustand gemeldet wird (nur angelegt, wenn sie fehlt)
#   /etc/ssh/authorized_keys.d/wamo-status   restrict,from="192.168.178.136",command="/usr/local/bin/wamo-status"
#   /etc/ssh/sshd_config.d/wamo-status.conf  Match User wamo-status: nur Public Key, keine Weiterleitungen, kein TTY,
#                                         ForceCommand, MaxSessions 2; "Match all" beendet den Block
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BENUTZER=wamo-status
PROGRAMM=/usr/local/bin/wamo-status
KEYS=/etc/ssh/authorized_keys.d/$BENUTZER
DROPIN=/etc/ssh/sshd_config.d/wamo-status.conf
MARKE=/run/wamo-status-sshd-bestaetigt
FROM=192.168.178.136

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
for datei in wamo_status.py wamo-status.pub; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei" >&2; exit 1; }
done
[ -x /usr/bin/python3 ] && [ -x /usr/sbin/sshd ] || { echo "python3 oder sshd fehlt" >&2; exit 1; }
if grep -qiE '^\s*(AllowUsers|AllowGroups|DenyUsers|DenyGroups)\b' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null; then
  echo "Hinweis: sshd hat AllowUsers/AllowGroups/Deny*-Regeln; $BENUTZER muss dort erlaubt sein." >&2
fi

echo "== 1. Programm $PROGRAMM"
install -o root -g root -m 0755 "$HERE/wamo_status.py" "$PROGRAMM"
sed -i 's/\r$//' "$PROGRAMM"
install -d -o root -g root -m 0755 /etc/wamo
if [ ! -f /etc/wamo/status-dienste.conf ]; then
  printf '# Units, deren Zustand WAMO meldet (je Zeile eine, nur name.service). Nur allgemeine Infrastruktur.\n%s\n' \
    "docker.service" "nvidia-persistenced.service" "ssh.service" > /etc/wamo/status-dienste.conf
  chmod 0644 /etc/wamo/status-dienste.conf
fi

echo "== 2. Benutzer $BENUTZER (Login-Shell ist das Programm, keine Gruppen)"
if ! id "$BENUTZER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --no-create-home --shell "$PROGRAMM" \
    --comment "WAMO Nur-Lese-Status" "$BENUTZER"
  echo "  angelegt"
fi
usermod -s "$PROGRAMM" "$BENUTZER"
gruppen="$(id -nG "$BENUTZER")"
[ "$gruppen" = "$BENUTZER" ] || { echo "$BENUTZER ist in weiteren Gruppen ($gruppen), Abbruch." >&2; exit 1; }
passwd -l "$BENUTZER" >/dev/null 2>&1 || true
echo "  $(id "$BENUTZER")"
echo "  Probe: $(runuser -u "$BENUTZER" -- env -i SSH_ORIGINAL_COMMAND=status "$PROGRAMM" -c x | head -c 200)"

echo "== 3. Schlüssel des Gerätedienstes (from=$FROM)"
zeile=$(tr -d '\r' < "$HERE/wamo-status.pub" | head -n1)
[[ $zeile =~ ^ssh-ed25519\ [A-Za-z0-9+/]{68}(\ .*)?$ ]] || { echo "Kein ssh-ed25519-Schlüssel" >&2; exit 1; }
typ_und_wert=$(echo "$zeile" | awk '{print $1" "$2}')
install -d -o root -g root -m 0755 /etc/ssh/authorized_keys.d
neu=$(mktemp)
echo "restrict,from=\"$FROM\",command=\"$PROGRAMM\" $typ_und_wert wamo-geraete" > "$neu"
install -o root -g root -m 0644 "$neu" "$KEYS"
rm -f "$neu"
echo "  $(ssh-keygen -l -f <(echo "$typ_und_wert") | awk '{print $2}')"

echo "== 4. sshd-Drop-in $DROPIN"
neu=$(mktemp)
cat > "$neu" <<SSHD
# WAMO: Benutzer wamo-status nur für den Nur-Lese-Bericht an den Gerätedienst auf Sokrates-2.
# Installiert durch infra/sparks/wamo-status-einrichten.sh. "Match all" am Ende beendet den Block.
Match User wamo-status
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
    AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u
    DisableForwarding yes
    PermitTTY no
    PermitUserRC no
    PermitTunnel no
    MaxSessions 2
    ForceCommand $PROGRAMM
Match all
SSHD
if ! cmp -s "$neu" "$DROPIN"; then
  global_vorher=$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.78)
  vorher=$(mktemp)
  [ -f "$DROPIN" ] && cp -p "$DROPIN" "$vorher" || rm -f "$vorher"
  install -o root -g root -m 0644 "$neu" "$DROPIN"
  if ! /usr/sbin/sshd -t; then
    if [ -f "$vorher" ]; then install -o root -g root -m 0644 "$vorher" "$DROPIN"; else rm -f "$DROPIN"; fi
    echo "sshd -t meldet einen Fehler: Drop-in zurückgenommen, sshd unverändert." >&2
    exit 1
  fi
  if [ "$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.78)" != "$global_vorher" ]; then
    if [ -f "$vorher" ]; then install -o root -g root -m 0644 "$vorher" "$DROPIN"; else rm -f "$DROPIN"; fi
    echo "Drop-in wirkt über wamo-status hinaus, zurückgenommen." >&2
    exit 1
  fi
  rm -f "$MARKE"
  if systemctl is-active --quiet ssh.service; then systemctl reload ssh.service; fi
  systemd-run --quiet --unit="wamo-status-sshd-rueckfall-$(date +%s)" --on-active=300 /bin/sh -c \
    "test -e $MARKE || { rm -f $DROPIN; systemctl reload ssh.service || true; logger -t wamo 'sshd-Drop-in wamo-status ohne Bestätigung zurückgenommen'; }"
  rm -f "$vorher"
  echo "  sshd-Drop-in aktiv. BESTÄTIGEN binnen 5 Minuten über eine NEUE Verbindung:"
  echo "    ssh $(hostname) \"sudo touch $MARKE\""
else
  echo "  sshd-Drop-in unverändert"
fi
rm -f "$neu"

echo "== 5. Prüfung (sshd -T für wamo-status)"
/usr/sbin/sshd -T -C user=wamo-status,host=sokrates2,addr=$FROM | grep -E '^(authorizedkeysfile|allowtcpforwarding|allowstreamlocalforwarding|x11forwarding|permittty|forcecommand|maxsessions|authenticationmethods) ' | sed 's/^/  /'
echo "Fertig."
