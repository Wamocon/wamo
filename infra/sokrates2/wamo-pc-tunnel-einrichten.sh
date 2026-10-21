#!/usr/bin/env bash
# Rücktunnel je Gerät (Laptops): ein Unix-Benutzer und ein Socket-Ordner je Gerät, sshd-Drop-in, Zwangsbefehl,
# Abgleich mit der Datenbank alle 10 s und Streuner-Prüfung. Wiederholbar. Läuft neben wamo-tunnel-einrichten.sh
# (der alte Tunnel für genau einen PC bleibt unberührt).
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-pc-tunnel-einrichten.sh"
#   danach binnen 5 Minuten über eine NEUE Verbindung bestätigen (sonst nimmt ein Zeitschalter den Drop-in zurück):
#   ssh sokrates2 "sudo touch /run/wamo-pc-sshd-bestaetigt"
#   danach: ssh sokrates2 "sudo python3 -I /opt/wamo-pc/bin/wamo-pc-rauchtest"
# Rückweg: wamo-pc-tunnel-rueckweg.sh
# Umgebung (nur bei der ersten Einrichtung): WAMO_PC_SCHEMA=wamo_dev|wamo_test|wamo_prod legt das Schema fest, aus dem
# der Abgleich die Geräte liest. Ohne Angabe wird das Schema des Kerns (/etc/wamo/core.env) genommen, wenn dort die
# Funktion pc_abgleich_liste() schon existiert, sonst wamo_test. WAMO_PC_FROM_EXTRA="10.8.0.0/24" ergänzt das Büro-LAN
# 192.168.178.0/24 im from= der Schlüssel (zum Beispiel WireGuard).
#
# Warum ein Benutzer je Gerät: OpenSSH 9.6 prüft Socket-Pfade bei Rückweiterleitungen weder mit PermitListen noch mit
# permitlisten. Getrennt wird deshalb über das Dateisystem: Jeder Benutzer darf nur in seinem Ordner /run/wamo-pc/tNN
# (2750 wamo-t-NN:wamo-core) einen Socket anlegen oder ersetzen. Verbinden kann nur der Kern (Gruppe wamo-core), und er
# prüft per SO_PEERCRED, dass am anderen Ende der richtige Benutzer lauscht.
#
# Was das Skript am System ändert (nur WAMO):
#   /opt/wamo-pc/bin/wamo-pc-token         Zwangsbefehl und Login-Shell der Tunnelbenutzer (root 0755)
#   /opt/wamo-pc/bin/wamo-pc-abgleich      Abgleich Datenbank -> Benutzer, Schlüssel, Ordner (root 0755)
#   /opt/wamo-pc/bin/wamo-pc-rauchtest     Rauchtest mit Probe-Geräten
#   /etc/wamo/pc-abgleich.conf             Schema und Containername (root 0644, keine Geheimnisse)
#   /etc/tmpfiles.d/wamo-pc-wurzel.conf    /run/wamo-pc 0711 root:root
#   /etc/ssh/sshd_config.d/wamo-pc.conf    Match User wamo-t-*: nur Public Key, nur Rückweiterleitung auf Unix-Sockets,
#                                          Zwangsbefehl, kein TTY, eine Sitzung (ausführlich im Drop-in)
#   wamo-pc-abgleich.service/.timer        alle 10 s (Benutzer, Schlüssel, Ordner passend zur Liste)
#   wamo-pc-streuner.service/.timer        alle 5 Minuten (Sockets der Tunnelbenutzer außerhalb von /run/wamo-pc)
#   Benutzer wamo-t-NN, /etc/ssh/authorized_keys.d/wamo-t-NN und /etc/tmpfiles.d/wamo-pc.conf legt der Abgleich an.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
KERN_GRUPPE=wamo-core
INSTALL=/opt/wamo-pc
DROPIN=/etc/ssh/sshd_config.d/wamo-pc.conf
MARKE=/run/wamo-pc-sshd-bestaetigt
KONFIG=/etc/wamo/pc-abgleich.conf
FROM="192.168.178.0/24${WAMO_PC_FROM_EXTRA:+,$WAMO_PC_FROM_EXTRA}"
DATEIEN=(wamo_pc_token.py wamo_pc_abgleich.py wamo_pc_rauchtest.py wamo-pc-abgleich.service wamo-pc-abgleich.timer
         wamo-pc-streuner.service wamo-pc-streuner.timer)

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
[[ $FROM =~ ^[0-9./,:a-fA-F]+$ ]] || { echo "WAMO_PC_FROM_EXTRA ungültig" >&2; exit 1; }
for d in "${DATEIEN[@]}"; do [ -f "$HERE/$d" ] || { echo "Fehlt: $HERE/$d" >&2; exit 1; }; done
[ -x /usr/sbin/sshd ] || { echo "Fehlt: /usr/sbin/sshd" >&2; exit 1; }
[ -x /usr/bin/python3 ] || { echo "Fehlt: /usr/bin/python3" >&2; exit 1; }

echo "== 1. Voraussetzungen"
getent group "$KERN_GRUPPE" >/dev/null || { echo "Gruppe $KERN_GRUPPE fehlt (zuerst wamo-core-einrichten.sh)." >&2; exit 1; }
mitglieder="$(getent group "$KERN_GRUPPE" | cut -d: -f4)"
if [ -n "$mitglieder" ] && [ "$mitglieder" != "wamo-core" ]; then
  echo "Die Gruppe $KERN_GRUPPE hat weitere Mitglieder ($mitglieder): jedes Mitglied öffnet alle Geräte-Sockets. Abbruch." >&2
  exit 1
fi
# getent group zeigt nur Zusatzmitglieder; Konten mit wamo-core als PRIMÄRER Gruppe stehen nur in passwd
kern_gid="$(getent group "$KERN_GRUPPE" | cut -d: -f3)"
primaer="$(getent passwd | awk -F: -v g="$kern_gid" '$4==g {print $1}' | grep -vx 'wamo-core' || true)"
[ -z "$primaer" ] || { echo "Konten mit primärer Gruppe $KERN_GRUPPE außer wamo-core: $primaer. Abbruch." >&2; exit 1; }
# Kein fremdes Konto darf in den Namensraum der Tunnelbenutzer fallen (Match User wamo-t-*)
fremd="$(getent passwd | cut -d: -f1 | grep -E '^wamo-t-' | grep -vE '^wamo-t-(0[1-9]|[1-5][0-9]|6[0-4]|p[a-z])$' || true)"
[ -z "$fremd" ] || { echo "Konten im Namensraum wamo-t-* außerhalb des Schemas: $fremd" >&2; exit 1; }
[ ! -e /etc/ssh/sshrc ] || { echo "/etc/ssh/sshrc existiert und liefe für Tunnelbenutzer. Abbruch." >&2; exit 1; }
if grep -qiE '^\s*(AllowUsers|AllowGroups|DenyUsers|DenyGroups)\b' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null; then
  echo "  Hinweis: sshd hat AllowUsers/AllowGroups/Deny*-Regeln (Datei eines anderen Verantwortlichen, bleibt unberührt)."
  echo "  Das Drop-in ergänzt innerhalb von Match User wamo-t-* ein eigenes AllowUsers wamo-t-*."
  if grep -qiE '^\s*(AllowGroups|DenyUsers|DenyGroups)\b' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null; then
    echo "  AllowGroups/DenyUsers/DenyGroups gefunden: wamo-t-* muss dort erlaubt sein (sonst scheitert der Rauchtest)." >&2
  fi
fi
command -v docker >/dev/null || { echo "Fehlt: docker (der Abgleich liest die Datenbank per docker exec)." >&2; exit 1; }
echo "  ok. Hinweis: pam_systemd legt je Verbindung eine Sitzung an (optional im PAM-Stapel); Sockets in /run/user/<uid>"
echo "  meldet der Streuner, den PAM-Stapel lässt dieses Skript unverändert."

echo "== 2. Programme nach $INSTALL/bin"
install -d -o root -g root -m 0755 "$INSTALL" "$INSTALL/bin"
install -o root -g root -m 0755 "$HERE/wamo_pc_token.py" "$INSTALL/bin/wamo-pc-token"
install -o root -g root -m 0755 "$HERE/wamo_pc_abgleich.py" "$INSTALL/bin/wamo-pc-abgleich"
install -o root -g root -m 0755 "$HERE/wamo_pc_rauchtest.py" "$INSTALL/bin/wamo-pc-rauchtest"
for p in wamo-pc-token wamo-pc-abgleich wamo-pc-rauchtest; do python3 -I -m py_compile "$INSTALL/bin/$p"; done
rm -rf "$INSTALL/bin/__pycache__"
stat -c '  %n %U:%G %a' "$INSTALL/bin/wamo-pc-token" "$INSTALL/bin/wamo-pc-abgleich"

echo "== 3. Konfiguration $KONFIG"
[ -d /etc/wamo ] || { echo "/etc/wamo fehlt (zuerst wamo-core-einrichten.sh)." >&2; exit 1; }
if [ ! -f "$KONFIG" ]; then
  schema="${WAMO_PC_SCHEMA:-}"
  if [ -z "$schema" ] && [ -r /etc/wamo/core.env ]; then
    kandidat="$(grep -E '^WAMO_DB_SCHEMA=' /etc/wamo/core.env | tail -n1 | cut -d= -f2 | tr -d '[:space:]"' || true)"
    if [[ $kandidat =~ ^wamo_(dev|test|prod)$ ]] && \
       [ "$(docker exec wamo-supabase-db psql -U postgres -At -c "select to_regproc('${kandidat}.pc_abgleich_liste') is not null" 2>/dev/null || true)" = t ]; then
      schema="$kandidat"
    fi
  fi
  schema="${schema:-wamo_test}"
  [[ $schema =~ ^wamo_(dev|test|prod)$ ]] || { echo "WAMO_PC_SCHEMA ungültig" >&2; exit 1; }
  cat > "$KONFIG" <<KONF
# Quelle der Tunnelgeräte für wamo-pc-abgleich (keine Geheimnisse). Umstellen:
#   sudo sed -i 's/^WAMO_DB_SCHEMA=.*/WAMO_DB_SCHEMA=wamo_dev/' $KONFIG
WAMO_DB_SCHEMA=$schema
WAMO_DB_CONTAINER=wamo-supabase-db
WAMO_PC_FROM=$FROM
KONF
  chmod 0644 "$KONFIG"
  echo "  neu angelegt, Schema $schema"
else
  grep -q '^WAMO_PC_FROM=' "$KONFIG" || echo "WAMO_PC_FROM=$FROM" >> "$KONFIG"
  echo "  vorhanden, unverändert ($(grep '^WAMO_DB_SCHEMA=' "$KONFIG"))"
fi

echo "== 4. Wurzelordner /run/wamo-pc"
cat > /etc/tmpfiles.d/wamo-pc-wurzel.conf <<'TMP'
# WAMO-Tunnel je Gerät: Wurzel der Geräteordner. 0711 = betreten ja, auflisten nein. Die Ordner je Gerät
# (/run/wamo-pc/tNN, 2750 wamo-t-NN:wamo-core) schreibt wamo-pc-abgleich nach /etc/tmpfiles.d/wamo-pc.conf.
d /run/wamo-pc 0711 root root -
TMP
chmod 0644 /etc/tmpfiles.d/wamo-pc-wurzel.conf
systemd-tmpfiles --create /etc/tmpfiles.d/wamo-pc-wurzel.conf
stat -c '  %n %U:%G %a' /run/wamo-pc

echo "== 5. sshd-Drop-in $DROPIN"
neu=$(mktemp)
cat > "$neu" <<'SSHD'
# WAMO: Rücktunnel je Gerät. Jeder Laptop meldet sich als eigener Benutzer wamo-t-NN und darf nur einen Socket in
# seinem eigenen Ordner /run/wamo-pc/tNN anlegen. Installiert durch infra/sokrates2/wamo-pc-tunnel-einrichten.sh.
# "Match all" am Ende beendet den Block.
Match User wamo-t-*
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
    AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u
    # Ein globales AllowUsers (anderes Drop-in) kennt wamo-t-* nicht; im Match-Block wird die Liste nur für diese
    # Benutzer ergänzt. Für alle anderen Benutzer ändert sich nichts (wird unten mit sshd -T geprüft).
    AllowUsers wamo-t-*
    # "remote" statt "no": OpenSSH 9.6 prüft Rückweiterleitungen auf Unix-Sockets über dieselbe Rechteliste wie
    # TCP-Rückweiterleitungen; mit "no" wird auch der Socket abgelehnt. TCP-Listen verhindert PermitListen none (Pfade
    # sind davon ausgenommen), Lokalweiterleitungen (-L, -W) verhindern "remote" und PermitOpen none.
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
    MaxSessions 1
    # der Zwangsbefehl berechnet den Fingerabdruck des benutzten Schlüssels aus SSH_USER_AUTH
    ExposeAuthInfo yes
    ForceCommand /opt/wamo-pc/bin/wamo-pc-token
    ClientAliveInterval 15
    ClientAliveCountMax 2
Match all
SSHD
if ! cmp -s "$neu" "$DROPIN"; then
  global_vorher=$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)
  tunnel_vorher=$(/usr/sbin/sshd -T -C user=wamo-tunnel,host=pc,addr=192.168.178.2 2>/dev/null || true)
  vorher=$(mktemp)
  [ -f "$DROPIN" ] && cp -p "$DROPIN" "$vorher" || rm -f "$vorher"
  install -o root -g root -m 0644 "$neu" "$DROPIN"
  zuruecknehmen() { if [ -f "$vorher" ]; then install -o root -g root -m 0644 "$vorher" "$DROPIN"; else rm -f "$DROPIN"; fi; }
  if ! /usr/sbin/sshd -t; then
    zuruecknehmen
    echo "sshd -t meldet einen Fehler: Drop-in zurückgenommen, sshd unverändert." >&2
    exit 1
  fi
  # Für alle anderen (Probe: wamocon, wamo-tunnel) darf sich nichts ändern: der Match-Block endet wirklich
  if [ "$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)" != "$global_vorher" ] || \
     [ "$(/usr/sbin/sshd -T -C user=wamo-tunnel,host=pc,addr=192.168.178.2 2>/dev/null || true)" != "$tunnel_vorher" ]; then
    zuruecknehmen
    echo "Drop-in wirkt über wamo-t-* hinaus, zurückgenommen." >&2
    exit 1
  fi
  # Und für die Tunnelbenutzer wirkt er wirklich (sonst stünde ein globales AllowUsers/AllowGroups im Weg)
  wirkt=$(/usr/sbin/sshd -T -C user=wamo-t-05,host=pc,addr=192.168.178.2)
  if ! grep -qx 'allowusers wamo-t-\*' <<<"$wirkt" || ! grep -qx 'maxsessions 1' <<<"$wirkt" \
     || ! grep -qx 'forcecommand /opt/wamo-pc/bin/wamo-pc-token' <<<"$wirkt"; then
    zuruecknehmen
    echo "Drop-in wirkt für wamo-t-* nicht wie vorgesehen, zurückgenommen." >&2
    exit 1
  fi
  rm -f "$MARKE"
  if systemctl is-active --quiet ssh.service; then systemctl reload ssh.service; fi
  # Totmannschalter: ohne Bestätigung über eine neue Verbindung binnen 5 Minuten wird zurückgenommen
  systemd-run --quiet --unit="wamo-pc-sshd-rueckfall-$(date +%s)" --on-active=300 /bin/sh -c \
    "test -e $MARKE || { rm -f $DROPIN; systemctl reload ssh.service || true; logger -t wamo 'sshd-Drop-in wamo-pc ohne Bestätigung zurückgenommen'; }"
  rm -f "$vorher"
  echo "  sshd-Drop-in aktiv. BESTÄTIGEN binnen 5 Minuten über eine NEUE Verbindung:"
  echo "    ssh sokrates2 \"sudo touch $MARKE\""
else
  echo "  sshd-Drop-in unverändert"
fi
rm -f "$neu"

echo "== 6. Units (Abgleich alle 10 s, Streuner alle 5 Minuten)"
for u in wamo-pc-abgleich.service wamo-pc-abgleich.timer wamo-pc-streuner.service wamo-pc-streuner.timer; do
  install -o root -g root -m 0644 "$HERE/$u" "/etc/systemd/system/$u"
done
systemd-analyze verify /etc/systemd/system/wamo-pc-abgleich.service /etc/systemd/system/wamo-pc-streuner.service 2>&1 | sed 's/^/  /' || true
systemctl daemon-reload
systemctl enable --now wamo-pc-abgleich.timer wamo-pc-streuner.timer
systemctl start wamo-pc-abgleich.service || true
systemctl list-timers 'wamo-pc-*' --no-legend | sed 's/^/  /'

echo "== 7. Prüfung (sshd -T für wamo-t-05)"
/usr/sbin/sshd -T -C user=wamo-t-05,host=pc,addr=192.168.178.2 | grep -E '^(authorizedkeysfile|allowusers|allowtcpforwarding|allowstreamlocalforwarding|permitopen|permitlisten|maxsessions|permittty|forcecommand|streamlocalbindmask|streamlocalbindunlink|clientaliveinterval|clientalivecountmax|authenticationmethods|exposeauthinfo) ' | sed 's/^/  /'
echo "  Gruppe $KERN_GRUPPE: $(getent group "$KERN_GRUPPE")"
echo "Fertig. Rauchtest: sudo python3 -I $INSTALL/bin/wamo-pc-rauchtest"
