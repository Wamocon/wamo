#!/usr/bin/env bash
# WAMO-Entwicklungsaufträge auf Sokrates-2 einrichten: Sandbox (bubblewrap, Landlock, seccomp), Auftragsläufer,
# Dispatcher, Wächter, Ausgangsfilter (nft) und den SSH-Schlüssel nur für Aufträge. Voraussetzung:
# wamo-einrichten.sh lief (Benutzer wamo, Claude Code, Node, ECC, REA). Wiederholbar, richtet keine Geheimnisse ein.
#
# Vom PC aus (Repo-Wurzel, Git Bash). Der Ordner liegt im Home von wamocon, nie in /tmp:
#   cd infra/sokrates2 && scp -r wamo-job.sh wamo-job-einrichten.sh wamo-job-ecc.py wamo_job_dispatch.py #     wamo_job_rauchtest.py abschottung.py wamo-egress.nft wamo-egress.service wamo-egress-rueckweg.sh claude-code #     sokrates2:wamo-infra/ && cd ../..
#   scp voice-core/dev_guard.py voice-core/werkzeugkarte.py ~/.ssh/id_ed25519_wamo_jobs.pub sokrates2:wamo-infra/
#   (~/wamo-infra teilen sich mehrere Einrichtungsskripte: nur die eigenen Dateien ersetzen, den Ordner nicht leeren)
#   ssh sokrates2 "chmod -R go-w ~/wamo-infra && sudo bash ~/wamo-infra/wamo-job-einrichten.sh"
# Danach innerhalb von 5 Minuten mit einer NEUEN Verbindung bestätigen, dass die Anmeldung als wamocon weiter geht
# (sonst nimmt ein Zeitschalter den sshd-Drop-in automatisch zurück):
#   ssh sokrates2 "sudo touch /run/wamo-sshd-bestaetigt"
# Rauchtest:  ssh sokrates2 "sudo -u wamo -H /opt/wamo/bin/wamo-job-rauchtest --mcp --attrappe"
#             (nach dem Einrichten der GitHub-App zusätzlich --app-echt, nur lesend, README Abschnitt 2)
#
# Was das Skript am System ändert (nur WAMO):
#   /usr/local/lib/wamo/bwrap            Kopie von /usr/bin/bwrap, root:wamo 0750 (nur Gruppe wamo)
#   /etc/apparmor.d/wamo-bwrap           erlaubt NUR dieser Kopie Benutzer-Namensräume (Ubuntu sperrt sie sonst,
#                                        kernel.apparmor_restrict_unprivileged_userns=1); System-bwrap bleibt gesperrt
#   /usr/local/sbin/wamo-bwrap-sync, /etc/apt/apt.conf.d/80wamo-bwrap   hält die Kopie nach Paket-Updates aktuell
#   loginctl enable-linger wamo          systemd-Benutzerdienst (Grenzen und Abbruch über systemd-run --user)
#   /opt/wamo                            root:wamo 0750, für wamo nur lesbar und ausführbar (nicht umbenennbar):
#     bin/{wamo-job-dispatch,wamo-job.sh,wamo-job-rauchtest}, wamo-job.conf (Grenzen, CLAUDE_VERSION),
#     waechter/{dev_guard,werkzeugkarte,abschottung}.py, waechter/seccomp.bpf,
#     claude-code/{managed-settings.json,managed-mcp.json},
#     claude-plugins/ (Kopie des ECC-Plugins von wamo ohne Hooks, lokaler Marketplace "ecc", wamo-job-ecc.py)
#   /etc/wamo/egress.nft, /etc/systemd/system/wamo-egress.service   Ausgangsfilter (nft inet wamo_egress)
#   Aufräumen der Auftrags-Caches erledigt der Dispatcher als wamo (frühere /etc/tmpfiles.d/wamo-jobs.conf wird entfernt)
#   /etc/ssh/authorized_keys.d/wamo      root, Zeile restrict,from=...,command="/opt/wamo/bin/wamo-job-dispatch"
#   /etc/ssh/sshd_config.d/wamo.conf     Match User wamo: nur Public Key, ForceCommand wamo-job-dispatch, keine
#                                        Weiterleitungen, kein TTY, PermitUserRC no, MaxSessions 2 (geprüft mit sshd -t)
#   Audio-Benutzerdienste (pipewire, wireplumber) von wamo maskiert; Login-Shell /bin/sh
#   entfernt: /srv/wamo/app und /srv/wamo/.ssh aus früheren Fassungen
# Umgebung: WAMO_JOBS_FROM_EXTRA="10.8.0.0/24" ergänzt das Büro-LAN 192.168.178.0/24 im from= des Schlüssels.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WAMO_USER=wamo
HEIM=/srv/wamo
APP=/opt/wamo
BWRAP_KOPIE=/usr/local/lib/wamo/bwrap
REA_VERSION="${REA_VERSION:-6.5.0}"
JOBS_FROM="192.168.178.0/24${WAMO_JOBS_FROM_EXTRA:+,$WAMO_JOBS_FROM_EXTRA}"
DROPIN=/etc/ssh/sshd_config.d/wamo.conf
KEYS=/etc/ssh/authorized_keys.d/wamo

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

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
pruefe_herkunft
for datei in wamo-job.sh wamo_job_dispatch.py wamo_job_rauchtest.py abschottung.py claude-code/managed-settings.json \
  claude-code/managed-mcp.json wamo-job-ecc.py \
  dev_guard.py werkzeugkarte.py wamo-egress.nft wamo-egress.service; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei" >&2; exit 1; }
done
[[ $REA_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "REA_VERSION ungültig" >&2; exit 1; }
[[ $JOBS_FROM =~ ^[0-9./,:a-fA-F]+$ ]] || { echo "WAMO_JOBS_FROM_EXTRA ungültig" >&2; exit 1; }
id "$WAMO_USER" >/dev/null 2>&1 || { echo "Benutzer $WAMO_USER fehlt, erst wamo-einrichten.sh ausführen." >&2; exit 1; }
for prog in /usr/bin/bwrap /usr/bin/python3 /usr/bin/systemd-run /usr/bin/prlimit /usr/bin/timeout \
  /usr/sbin/apparmor_parser /usr/sbin/nft /usr/sbin/sshd /usr/bin/git; do
  [ -x "$prog" ] || { echo "Fehlt: $prog" >&2; exit 1; }
done
UID_WAMO=$(id -u "$WAMO_USER")
[[ $UID_WAMO =~ ^[0-9]+$ ]] || { echo "uid von $WAMO_USER unklar" >&2; exit 1; }
# GitHub-App-Modus (kurzlebige Token je Auftrag) signiert mit dem Systempaket python3-cryptography; ohne das Paket
# lehnt der Dispatcher im App-Modus jeden Start mit GitHub ab (fail-closed), der Rückfall auf GH_TOKEN bleibt möglich
/usr/bin/python3 -I -c 'import cryptography' 2>/dev/null \
  || echo "Hinweis: python3-cryptography fehlt; der GitHub-App-Modus braucht es (apt-get install python3-cryptography)." >&2

echo "== 1. bubblewrap-Kopie nur für die Gruppe wamo, AppArmor-Profil mit Benutzer-Namensräumen"
install -d -o root -g "$WAMO_USER" -m 0750 /usr/local/lib/wamo
cat > /etc/apparmor.d/wamo-bwrap <<'AA'
# WAMO: nur diese bwrap-Kopie (root:wamo 0750) darf Benutzer-Namensräume anlegen (Auftrags-Sandbox).
abi <abi/4.0>,
include <tunables/global>

profile wamo-bwrap /usr/local/lib/wamo/bwrap flags=(unconfined) {
  userns,

  include if exists <local/wamo-bwrap>
}
AA
cat > /usr/local/sbin/wamo-bwrap-sync <<'SYNC'
#!/bin/sh
# Hält /usr/local/lib/wamo/bwrap auf dem Stand von /usr/bin/bwrap (aufgerufen nach jedem dpkg-Lauf).
set -e
[ -x /usr/bin/bwrap ] || exit 0
if ! cmp -s /usr/bin/bwrap /usr/local/lib/wamo/bwrap; then
  install -o root -g wamo -m 0750 /usr/bin/bwrap /usr/local/lib/wamo/bwrap
fi
apparmor_parser -r -W /etc/apparmor.d/wamo-bwrap >/dev/null 2>&1 || true
SYNC
chmod 0755 /usr/local/sbin/wamo-bwrap-sync
cat > /etc/apt/apt.conf.d/80wamo-bwrap <<'APT'
// WAMO: bwrap-Kopie der Auftrags-Sandbox nach Paket-Updates aktualisieren
DPkg::Post-Invoke { "if [ -x /usr/local/sbin/wamo-bwrap-sync ]; then /usr/local/sbin/wamo-bwrap-sync || true; fi"; };
APT
install -o root -g "$WAMO_USER" -m 0750 /usr/bin/bwrap "$BWRAP_KOPIE"
apparmor_parser -r -W /etc/apparmor.d/wamo-bwrap

echo "== 2. Lingering für $WAMO_USER (systemd-Benutzerdienst für Grenzen und Abbruch)"
loginctl enable-linger "$WAMO_USER"
for _ in $(seq 1 20); do
  systemctl is-active --quiet "user@$UID_WAMO.service" && break
  sleep 0.5
done
systemctl is-active --quiet "user@$UID_WAMO.service" || { echo "user@$UID_WAMO.service läuft nicht" >&2; exit 1; }
# N9: Audio- und Mediendienste des Benutzers wamo maskieren (nie gebraucht). D-Bus NICHT maskieren, systemd-run
# --user braucht ihn. Nach dem Maskieren wird unten (Prüfung) ein echter systemd-run --user getestet.
sudo -u "$WAMO_USER" -H env XDG_RUNTIME_DIR="/run/user/$UID_WAMO" systemctl --user mask --now \
  pipewire.service pipewire.socket pipewire-pulse.service pipewire-pulse.socket wireplumber.service >/dev/null 2>&1 || true

echo "== 3. Programme nach $APP (root-eigen, für wamo nur lesbar bzw. ausführbar)"
install -d -o root -g "$WAMO_USER" -m 0750 "$APP" "$APP/bin" "$APP/waechter" "$APP/claude-code"
install -o root -g "$WAMO_USER" -m 0750 "$HERE/wamo_job_dispatch.py" "$APP/bin/wamo-job-dispatch"
install -o root -g "$WAMO_USER" -m 0750 "$HERE/wamo-job.sh" "$APP/bin/wamo-job.sh"
install -o root -g "$WAMO_USER" -m 0750 "$HERE/wamo_job_rauchtest.py" "$APP/bin/wamo-job-rauchtest"
install -o root -g "$WAMO_USER" -m 0640 "$HERE/dev_guard.py" "$HERE/werkzeugkarte.py" "$HERE/abschottung.py" "$APP/waechter/"
install -o root -g "$WAMO_USER" -m 0640 "$HERE/claude-code/managed-settings.json" "$APP/claude-code/managed-settings.json"
install -o root -g "$WAMO_USER" -m 0640 "$HERE/claude-code/managed-mcp.json" "$APP/claude-code/managed-mcp.json"
sed -i 's/\r$//' "$APP/bin/wamo-job.sh" "$APP/bin/wamo-job-dispatch" "$APP/bin/wamo-job-rauchtest"
bpf=$(mktemp)
/usr/bin/python3 -I "$APP/waechter/abschottung.py" --seccomp-bpf > "$bpf"
[ -s "$bpf" ] || { echo "seccomp-Filter leer" >&2; exit 1; }
install -o root -g "$WAMO_USER" -m 0640 "$bpf" "$APP/waechter/seccomp.bpf"
rm -f "$bpf"
KONF="$APP/wamo-job.conf"
if [ ! -f "$KONF" ]; then
  install -o root -g "$WAMO_USER" -m 0640 /dev/null "$KONF"
  if [ -f "$HEIM/app/wamo-job.conf" ]; then
    cat "$HEIM/app/wamo-job.conf" > "$KONF"  # Werte aus der früheren Ablage übernehmen
  else
    cat > "$KONF" <<'KONF'
# Grenzen der WAMO-Auftrags-Sandbox (gelesen von wamo-job.sh). Nur root darf ändern.
ZEITLIMIT_S=1800
ZEITLIMIT_MAX_S=7200
SPEICHER_MAX=8G
CPU_QUOTA=400%
PROZESSE_MAX=1024
TMP_MB=4096
HEIM_TMP_MB=1024
DATEI_MAX_MB=4096
OFFENE_DATEIEN=8192
# Zusätzlich zu allen Ports, auf denen Sokrates-2 lauscht: im LAN bekannte Dienste (LiteLLM, vLLM, Ollama)
NETZ_SPERRPORTS="4000 8000 8001 11434"
NETZ_IMMER_ERLAUBT="53 80 443"
ABSCHOTTUNG=an
KONF
  fi
fi
# Claude-Code-Version festschreiben: nach einem Update startet nichts, bis dieses Skript und der Rauchtest liefen
claude_echt=$(readlink -f "$HEIM/.local/bin/claude")
claude_version=${claude_echt##*/}
[[ $claude_echt == "$HEIM/.local/share/claude/versions/$claude_version" && $claude_version =~ ^[0-9.]+$ ]] \
  || { echo "Claude Code unter $HEIM/.local/bin/claude nicht gefunden" >&2; exit 1; }
sed -i '/^CLAUDE_VERSION=/d' "$KONF"
echo "CLAUDE_VERSION=$claude_version" >> "$KONF"
chown root:"$WAMO_USER" "$KONF"
chmod 0640 "$KONF"
find "$APP" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$HEIM/app"  # frühere Ablage (wamo konnte dort umbenennen)
echo "  Claude Code $claude_version festgeschrieben"

echo "== 4. Ordner von wamo"
install -d -o "$WAMO_USER" -g "$WAMO_USER" -m 0750 "$HEIM/jobs" "$HEIM/projekte" "$HEIM/logs"
install -d -o "$WAMO_USER" -g "$WAMO_USER" -m 0775 "$HEIM/.claude/plugins" "$HEIM/.claude/plugins/data"

echo "== 4b. REA fest in .npm-global (in der Sandbox nur lesbar; der Dispatcher startet MCP-Server daraus)"
sudo -u "$WAMO_USER" -H env -i HOME="$HEIM" PATH="$HEIM/.fnm/aliases/default/bin:/usr/bin:/bin" \
  bash -c "npm ls -g 'rea-agents@$REA_VERSION' >/dev/null 2>&1 || npm install -g --no-fund --no-audit 'rea-agents@$REA_VERSION' >/dev/null"
[ -x "$HEIM/.npm-global/bin/rea-agents" ] || { echo "REA-Installation fehlgeschlagen" >&2; exit 1; }

echo "== 4c. ECC-Plugin ohne Hooks für die Sandbox (/opt/wamo/claude-plugins; nach ECC-Updates erneut ausführen)"
/usr/bin/python3 -I "$HERE/wamo-job-ecc.py" --quelle "$HEIM/.claude/plugins" --ziel "$APP/claude-plugins" \
  --heim "$HEIM" --gruppe "$WAMO_USER"

echo "== 5. Ausgangsfilter für uid $UID_WAMO (nft inet wamo_egress, wamo-egress.service)"
install -d -o root -g "$WAMO_USER" -m 0750 /etc/wamo
neu=$(mktemp)
sed "s/@WAMO_UID@/$UID_WAMO/g" "$HERE/wamo-egress.nft" > "$neu"
grep -q '@WAMO_UID@' "$neu" && { echo "Platzhalter in wamo-egress.nft nicht ersetzt" >&2; exit 1; }
/usr/sbin/nft -c -f "$neu"
install -o root -g root -m 0644 "$neu" /etc/wamo/egress.nft
rm -f "$neu"
install -o root -g root -m 0644 "$HERE/wamo-egress.service" /etc/systemd/system/wamo-egress.service
systemctl daemon-reload
systemctl enable wamo-egress.service >/dev/null 2>&1
systemctl restart wamo-egress.service
/usr/sbin/nft list table inet wamo_egress >/dev/null || { echo "Tabelle inet wamo_egress fehlt" >&2; exit 1; }

echo "== 6. Aufräumen (der Dispatcher räumt als wamo auf; die frühere root-tmpfiles-Regel wird entfernt, N5)"
rm -f /etc/tmpfiles.d/wamo-jobs.conf
echo "  Caches der Aufträge (npm, cache, plugin-daten) nach 7 Tagen, claude/ nach 30 Tagen: aufraeumen() im Dispatcher"

echo "== 7. SSH: Schlüssel nur für Aufträge (Forced Command, from=, kein Shell-Zugang), Login-Shell /bin/sh"
# N7: der Auftragsschlüssel heißt *wamo_jobs*.pub; höchstens einer davon im Quellordner (andere .pub-Dateien, etwa
# der Tunnelschlüssel, bleiben unbeachtet). Fehlt er, wird ein schon eingetragener Schlüssel wiederverwendet.
anzahl_pub=$(find "$HERE" -maxdepth 1 -name '*wamo_jobs*.pub' | wc -l)
[ "$anzahl_pub" -le 1 ] || { echo "Mehr als ein Auftragsschlüssel (*wamo_jobs*.pub) in $HERE." >&2; exit 1; }
SCHLUESSEL=$(find "$HERE" -maxdepth 1 -name '*wamo_jobs*.pub' | head -n1)
install -d -o root -g root -m 0755 /etc/ssh/authorized_keys.d
if [ -n "$SCHLUESSEL" ]; then
  zeile=$(tr -d '\r' < "$SCHLUESSEL" | head -n1)
  [[ $zeile =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ .*)?$ ]] || { echo "Kein ssh-ed25519-Schlüssel: $SCHLUESSEL" >&2; exit 1; }
  typ_und_wert=$(echo "$zeile" | awk '{print $1" "$2}')
  ssh-keygen -l -f <(echo "$typ_und_wert") >/dev/null || { echo "Schlüssel unlesbar" >&2; exit 1; }
elif [ -f "$KEYS" ]; then
  typ_und_wert=$(grep -m1 -oE 'ssh-ed25519 [A-Za-z0-9+/=]+' "$KEYS")
elif [ -f "$HEIM/.ssh/authorized_keys" ]; then
  typ_und_wert=$(grep -m1 -E ' wamo-jobs$' "$HEIM/.ssh/authorized_keys" | grep -oE 'ssh-ed25519 [A-Za-z0-9+/=]+')
fi
[ -n "${typ_und_wert:-}" ] || { echo "Kein Auftragsschlüssel (*.pub im Quellordner) gefunden" >&2; exit 1; }
neu=$(mktemp)
echo "restrict,from=\"$JOBS_FROM\",command=\"$APP/bin/wamo-job-dispatch\" $typ_und_wert wamo-jobs" > "$neu"
install -o root -g root -m 0644 "$neu" "$KEYS"
rm -f "$neu"
echo "  Schlüssel: $(ssh-keygen -l -f <(echo "$typ_und_wert") | awk '{print $2}'), from=$JOBS_FROM"

neu=$(mktemp)
cat > "$neu" <<'SSHD'
# WAMO: Benutzer wamo nur mit dem Auftragsschlüssel (Forced Command /opt/wamo/bin/wamo-job-dispatch).
# Installiert durch infra/sokrates2/wamo-job-einrichten.sh. "Match all" am Ende beendet den Block, damit die
# Zeilen nach dem Include in sshd_config wieder für alle gelten.
Match User wamo
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
    AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u
    ForceCommand /opt/wamo/bin/wamo-job-dispatch
    PermitUserRC no
    MaxSessions 2
    ClientAliveInterval 30
    ClientAliveCountMax 3
    AllowTcpForwarding no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitTunnel no
    X11Forwarding no
    PermitTTY no
Match all
SSHD
if ! cmp -s "$neu" "$DROPIN"; then
  vorher=$(mktemp)
  [ -f "$DROPIN" ] && cp -p "$DROPIN" "$vorher" || rm -f "$vorher"
  install -o root -g root -m 0644 "$neu" "$DROPIN"
  if ! /usr/sbin/sshd -t; then
    if [ -f "$vorher" ]; then install -o root -g root -m 0644 "$vorher" "$DROPIN"; else rm -f "$DROPIN"; fi
    echo "sshd -t meldet einen Fehler: Drop-in zurückgenommen, sshd unverändert." >&2
    exit 1
  fi
  # Globale Einstellungen dürfen sich nicht ändern (Match-Block endet wirklich)
  global_t=$(/usr/sbin/sshd -T -C user=wamocon,host=pc,addr=192.168.178.2)
  grep -qx 'permittty yes' <<<"$global_t" && grep -qx 'allowtcpforwarding yes' <<<"$global_t" \
    || { rm -f "$DROPIN"; echo "Drop-in wirkt über wamo hinaus, zurückgenommen." >&2; exit 1; }
  rm -f /run/wamo-sshd-bestaetigt
  if systemctl is-active --quiet ssh.service; then systemctl reload ssh.service; fi
  # Totmannschalter: ohne Bestätigung über eine neue Verbindung binnen 5 Minuten wird zurückgenommen
  systemd-run --quiet --unit="wamo-sshd-rueckfall-$(date +%s)" --on-active=300 /bin/sh -c \
    "test -e /run/wamo-sshd-bestaetigt || { rm -f $DROPIN; systemctl reload ssh.service || true; logger -t wamo 'sshd-Drop-in ohne Bestätigung zurückgenommen'; }"
  rm -f "$vorher"
  echo "  sshd-Drop-in aktiv. BESTÄTIGEN binnen 5 Minuten über eine NEUE Verbindung:"
  echo "    ssh sokrates2 \"sudo touch /run/wamo-sshd-bestaetigt\""
else
  echo "  sshd-Drop-in unverändert"
fi
rm -f "$neu"
rm -rf "$HEIM/.ssh"  # frühere Ablage der authorized_keys (jetzt $KEYS)
usermod -s /bin/sh "$WAMO_USER"

echo "== 8. Prüfung"
sudo -u "$WAMO_USER" -H "$APP/bin/wamo-job-dispatch" ping
sudo -u "$WAMO_USER" -H "$BWRAP_KOPIE" --unshare-user --unshare-pid --ro-bind /usr /usr --symlink usr/lib /lib \
  --proc /proc /usr/bin/true && echo "Sandbox: Benutzer-Namensraum ok"
if sudo -u "$WAMO_USER" -H /usr/bin/bwrap --unshare-user --ro-bind /usr /usr --symlink usr/lib /lib /usr/bin/true 2>/dev/null; then
  echo "Hinweis: das System-bwrap darf ebenfalls Namensräume anlegen (AppArmor-Sperre aus?)."
else
  echo "System-bwrap bleibt gesperrt (wie gewollt)."
fi
sshd -T -C user=wamo,host=pc,addr=192.168.178.2 | grep -E '^(authorizedkeysfile|permittty|allowtcpforwarding|clientaliveinterval|passwordauthentication) '
/usr/sbin/nft list chain inet wamo_egress ausgang | grep -E 'skuid|counter' || true
echo "Fertig. Rauchtest: sudo -u $WAMO_USER -H $APP/bin/wamo-job-rauchtest --mcp --attrappe" \
  "(mit eingerichteter GitHub-App zusätzlich --app-echt; \"github\" im ping oben: app, token oder fehler)"
