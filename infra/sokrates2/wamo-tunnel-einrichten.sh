#!/usr/bin/env bash
# SSH-Rücktunnel vom PC zum WAMO-Kern: Benutzer wamo-tunnel ohne Shell, Schlüssel nur für die Rückweiterleitung auf
# den Unix-Socket /run/wamo-tunnel/pc.sock, sshd-Drop-in mit Totmannschalter. Wiederholbar.
# Voraussetzung: wamo-core-einrichten.sh lief (Gruppe wamo-core). Der öffentliche Schlüssel des PCs liegt als
# wamo-tunnel.pub neben diesem Skript (scripts\wamo-tunnel-einrichten.ps1 nennt den scp-Befehl).
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-tunnel-einrichten.sh"
#   danach binnen 5 Minuten über eine NEUE Verbindung bestätigen (sonst nimmt ein Zeitschalter den Drop-in zurück):
#   ssh sokrates2 "sudo touch /run/wamo-tunnel-sshd-bestaetigt"
# Rückweg: wamo-tunnel-rueckweg.sh
#
# Warum ein Unix-Socket statt -R 127.0.0.1:<port>: Einen TCP-Port auf Loopback könnte bei einem Tunnelausfall jeder
# lokale Benutzer belegen und so Anfragen und Token des Kerns abgreifen. Den Socket legt nur wamo-tunnel an
# (Verzeichnis 2750 wamo-tunnel:wamo-core, setgid: der Socket gehört der Gruppe wamo-core), verbinden kann nur der Kern.
# Grenze: OpenSSH prüft Socket-Pfade nicht (PermitListen gilt nur für TCP). wamo-tunnel kann Sockets überall anlegen,
# wo es schreiben darf (/tmp, /dev/shm); dort verbindet sich niemand. Jeder Schlüssel dieses Benutzers könnte pc.sock
# ersetzen: deshalb genau ein Schlüssel, ein PC. Für weitere PCs je PC ein eigener Benutzer und Socket.
#
# Was das Skript am System ändert (nur WAMO):
#   Benutzer wamo-tunnel                  System, Shell /usr/sbin/nologin, Home /nonexistent, keine weiteren Gruppen
#   /etc/tmpfiles.d/wamo-tunnel.conf      /run/wamo-tunnel 2750 wamo-tunnel:wamo-core
#   /etc/ssh/authorized_keys.d/wamo-tunnel   root, restrict,port-forwarding,from=...,command="/bin/false"
#   /etc/ssh/sshd_config.d/wamo-tunnel.conf  Match User wamo-tunnel: nur Public Key, nur Rückweiterleitung auf
#                                         Unix-Sockets (Maske 0117, alte Sockets ersetzen), keine Lokalweiterleitung,
#                                         kein TCP-Listen (PermitListen none),
#                                         keine Sitzung (MaxSessions 0), kein TTY, Agent, X11, Tunnel; ClientAlive 15 s x 2
# Umgebung: WAMO_TUNNEL_FROM_EXTRA="10.8.0.0/24" ergänzt das Büro-LAN 192.168.178.0/24 im from= des Schlüssels.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BENUTZER=wamo-tunnel
KERN_GRUPPE=wamo-core
FROM="192.168.178.0/24${WAMO_TUNNEL_FROM_EXTRA:+,$WAMO_TUNNEL_FROM_EXTRA}"
KEYS=/etc/ssh/authorized_keys.d/$BENUTZER
DROPIN=/etc/ssh/sshd_config.d/wamo-tunnel.conf
MARKE=/run/wamo-tunnel-sshd-bestaetigt

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten)
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
[[ $FROM =~ ^[0-9./,:a-fA-F]+$ ]] || { echo "WAMO_TUNNEL_FROM_EXTRA ungültig" >&2; exit 1; }
getent group "$KERN_GRUPPE" >/dev/null || { echo "Gruppe $KERN_GRUPPE fehlt (zuerst wamo-core-einrichten.sh)." >&2; exit 1; }
[ -x /usr/sbin/sshd ] || { echo "Fehlt: /usr/sbin/sshd" >&2; exit 1; }
if grep -qiE '^\s*(AllowUsers|AllowGroups|DenyUsers|DenyGroups)\b' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null; then
  echo "Hinweis: sshd hat AllowUsers/AllowGroups/Deny*-Regeln; wamo-tunnel muss dort erlaubt sein." >&2
fi

echo "== 1. Benutzer $BENUTZER (ohne Shell, ohne Home)"
if ! id "$BENUTZER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin \
    --comment "WAMO SSH-Ruecktunnel" "$BENUTZER"
  echo "  angelegt"
fi
gruppen="$(id -nG "$BENUTZER")"
[ "$gruppen" = "$BENUTZER" ] || { echo "$BENUTZER ist in weiteren Gruppen ($gruppen), Abbruch." >&2; exit 1; }
usermod -s /usr/sbin/nologin "$BENUTZER"
passwd -l "$BENUTZER" >/dev/null 2>&1 || true
echo "  $(id "$BENUTZER")"

echo "== 2. Socket-Verzeichnis /run/wamo-tunnel"
cat > /etc/tmpfiles.d/wamo-tunnel.conf <<'TMP'
# WAMO-Tunnel: sshd legt hier als wamo-tunnel den Socket pc.sock an (StreamLocalBindMask 0117 -> 0660). Das setgid-Bit
# gibt dem Socket die Gruppe wamo-core: nur der Kern verbindet sich, nur wamo-tunnel legt an.
d /run/wamo-tunnel 2750 wamo-tunnel wamo-core -
TMP
chmod 0644 /etc/tmpfiles.d/wamo-tunnel.conf
systemd-tmpfiles --create /etc/tmpfiles.d/wamo-tunnel.conf
stat -c '  %n %U:%G %a' /run/wamo-tunnel

echo "== 3. Schlüssel des PCs (nur Rückweiterleitung, from=$FROM)"
[ -f "$HERE/wamo-tunnel.pub" ] || [ -f "$KEYS" ] || { echo "Fehlt: $HERE/wamo-tunnel.pub" >&2; exit 1; }
if [ -f "$HERE/wamo-tunnel.pub" ]; then
  zeile=$(tr -d '\r' < "$HERE/wamo-tunnel.pub" | head -n1)
  [[ $zeile =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo "Kein ssh-ed25519-Schlüssel" >&2; exit 1; }
  typ_und_wert=$(echo "$zeile" | awk '{print $1" "$2}')
else
  typ_und_wert=$(grep -m1 -oE 'ssh-ed25519 [A-Za-z0-9+/=]+' "$KEYS")
fi
ssh-keygen -l -f <(echo "$typ_und_wert") >/dev/null || { echo "Schlüssel unlesbar" >&2; exit 1; }
install -d -o root -g root -m 0755 /etc/ssh/authorized_keys.d
neu=$(mktemp)
echo "restrict,port-forwarding,from=\"$FROM\",command=\"/bin/false\" $typ_und_wert wamo-tunnel" > "$neu"
install -o root -g root -m 0644 "$neu" "$KEYS"
rm -f "$neu"
echo "  $(ssh-keygen -l -f <(echo "$typ_und_wert") | awk '{print $2}')"

echo "== 4. sshd-Drop-in $DROPIN"
neu=$(mktemp)
cat > "$neu" <<'SSHD'
# WAMO: Benutzer wamo-tunnel nur für die Rückweiterleitung PC -> Unix-Socket /run/wamo-tunnel/pc.sock.
# Installiert durch infra/sokrates2/wamo-tunnel-einrichten.sh. "Match all" am Ende beendet den Block.
Match User wamo-tunnel
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
    AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u
    # "remote" statt "no": OpenSSH 9.6 prüft Rückweiterleitungen auf Unix-Sockets über dieselbe Rechteliste wie
    # TCP-Rückweiterleitungen; mit "no" wird auch der Socket abgelehnt. TCP-Ports verhindert PermitListen none
    # (Pfade sind davon ausgenommen), Lokalweiterleitungen (-L) verhindern "remote" und PermitOpen none.
    AllowTcpForwarding remote
    PermitOpen none
    PermitListen none
    GatewayPorts no
    AllowStreamLocalForwarding remote
    StreamLocalBindMask 0117
    StreamLocalBindUnlink yes
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitTTY no
    PermitUserRC no
    MaxSessions 0
    ForceCommand /bin/false
    ClientAliveInterval 15
    ClientAliveCountMax 2
Match all
SSHD
if ! cmp -s "$neu" "$DROPIN"; then
  global_vorher=$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)
  vorher=$(mktemp)
  [ -f "$DROPIN" ] && cp -p "$DROPIN" "$vorher" || rm -f "$vorher"
  install -o root -g root -m 0644 "$neu" "$DROPIN"
  if ! /usr/sbin/sshd -t; then
    if [ -f "$vorher" ]; then install -o root -g root -m 0644 "$vorher" "$DROPIN"; else rm -f "$DROPIN"; fi
    echo "sshd -t meldet einen Fehler: Drop-in zurückgenommen, sshd unverändert." >&2
    exit 1
  fi
  # Für alle anderen (Probe: wamocon) darf sich nichts ändern: der Match-Block endet wirklich
  if [ "$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)" != "$global_vorher" ]; then
    if [ -f "$vorher" ]; then install -o root -g root -m 0644 "$vorher" "$DROPIN"; else rm -f "$DROPIN"; fi
    echo "Drop-in wirkt über wamo-tunnel hinaus, zurückgenommen." >&2
    exit 1
  fi
  rm -f "$MARKE"
  if systemctl is-active --quiet ssh.service; then systemctl reload ssh.service; fi
  # Totmannschalter: ohne Bestätigung über eine neue Verbindung binnen 5 Minuten wird zurückgenommen
  systemd-run --quiet --unit="wamo-tunnel-sshd-rueckfall-$(date +%s)" --on-active=300 /bin/sh -c \
    "test -e $MARKE || { rm -f $DROPIN; systemctl reload ssh.service || true; logger -t wamo 'sshd-Drop-in wamo-tunnel ohne Bestätigung zurückgenommen'; }"
  rm -f "$vorher"
  echo "  sshd-Drop-in aktiv. BESTÄTIGEN binnen 5 Minuten über eine NEUE Verbindung:"
  echo "    ssh sokrates2 \"sudo touch $MARKE\""
else
  echo "  sshd-Drop-in unverändert"
fi
rm -f "$neu"

echo "== 5. Prüfung (sshd -T für wamo-tunnel)"
/usr/sbin/sshd -T -C user=wamo-tunnel,host=pc,addr=192.168.178.2 | grep -E '^(authorizedkeysfile|allowtcpforwarding|allowstreamlocalforwarding|permitopen|permitlisten|maxsessions|permittty|forcecommand|streamlocalbindmask|streamlocalbindunlink|clientaliveinterval|clientalivecountmax|authenticationmethods) ' | sed 's/^/  /'
echo "Fertig. Tunnel vom PC: scripts\\wamo-tunnel-einrichten.ps1 -Starten"
