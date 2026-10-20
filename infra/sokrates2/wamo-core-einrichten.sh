#!/usr/bin/env bash
# WAMO-Kern (Voice-Core) auf Sokrates-2 einrichten oder aktualisieren: Systembenutzer wamo-core, Code und venv unter
# /opt/wamo-core (root-eigen), systemd-Dienst wamo-core, Socket-Verzeichnis, Hilfsprogramm für core.env, Firewall-Port
# 55444. Wiederholbar. Richtet keine Geheimnisse ein (die kommen vom PC über stdin: scripts/wamo_kern_sokrates2.py).
#
# Vom PC aus (Repo-Wurzel). Der Ordner liegt im Home von wamocon, nie in /tmp:
#   python scripts/wamo_kern_sokrates2.py verteilen          # infra/sokrates2 nach ~/wamo-infra, Kern nach ~/wamo-infra/kern
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-einrichten.sh"
#   ssh sokrates2 "bash ~/wamo-infra/wamo-core-caddy.sh"     # HTTPS 55444 (als wamocon, Docker-Gruppe)
#   python scripts/wamo_kern_sokrates2.py alles --email <admin>   # Geheimnisse, DB-Rolle, Token, PC-Bindung
# Rückweg: wamo-core-rueckweg.sh
#
# Was das Skript am System ändert (nur WAMO):
#   Benutzer und Gruppe wamo-core       System, Shell /usr/sbin/nologin, Home /var/lib/wamo-core (StateDirectory),
#                                       keine weiteren Gruppen (kein docker, kein sudo)
#   /opt/wamo-core                      root:wamo-core 0750: app/voice-core (Code), venv (uv 0.13.1 aus uv.lock),
#                                       tools/uv, bin/wamo-core-vorpruefung, wamo-ca.crt
#   /etc/tmpfiles.d/wamo-core.conf      /run/wamo-core 0750 wamo-core:wamocon (Socket für Caddy, gid 1000)
#   /etc/wamo/core.env                  root:wamo-core 0640, nur angelegt, wenn sie fehlt
#   /usr/local/sbin/wamo-core-env       Werte setzen über stdin, nie anzeigen
#   /etc/systemd/system/wamo-core.service  aktiviert; gestartet nur, wenn core.env vollständig ist
#   wamo-firewall                       Port 55444 in WAMO-FW-LAN (über wamo-firewall-einrichten.sh)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/wamo-core
BENUTZER=wamo-core
CADDY_GID=1000          # wamo-caddy läuft als 1000:1000 (wamocon)
UV_VERSION=0.13.1
UV_SHA256=9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14   # uv-aarch64-unknown-linux-gnu.tar.gz
PYTHON=/usr/bin/python3.12
ENV_DATEI=/etc/wamo/core.env

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
for datei in wamo-core.service wamo_core_env.py wamo-core-vorpruefung.sh wamo-ca.crt wamo-firewall.sh \
  wamo-firewall-einrichten.sh kern/pyproject.toml kern/uv.lock kern/bot.py; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei (python scripts/wamo_kern_sokrates2.py verteilen)" >&2; exit 1; }
done
[ -x "$PYTHON" ] || { echo "Fehlt: $PYTHON" >&2; exit 1; }
[ "$(getent group wamocon | cut -d: -f3)" = "$CADDY_GID" ] || { echo "Gruppe wamocon hat nicht gid $CADDY_GID" >&2; exit 1; }
if find "$HERE/kern" -name '.env*' | grep -q .; then echo "Im Kern-Ordner liegt eine .env, Abbruch." >&2; exit 1; fi

echo "== 1. Systembenutzer $BENUTZER"
if ! id "$BENUTZER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/wamo-core --no-create-home --shell /usr/sbin/nologin \
    --comment "WAMO Voice-Core" "$BENUTZER"
  echo "  angelegt"
fi
gruppen="$(id -nG "$BENUTZER")"
[ "$gruppen" = "$BENUTZER" ] || { echo "$BENUTZER ist in weiteren Gruppen ($gruppen), Abbruch." >&2; exit 1; }
passwd -l "$BENUTZER" >/dev/null 2>&1 || true
echo "  $(id "$BENUTZER")"

echo "== 2. Socket-Verzeichnis /run/wamo-core (für Caddy, gid $CADDY_GID)"
cat > /etc/tmpfiles.d/wamo-core.conf <<'TMP'
# WAMO-Kern: Unix-Socket für wamo-caddy (Bind-Mount nur lesend, Container läuft als 1000:1000).
# Das Verzeichnis bleibt über Neustarts des Dienstes bestehen (kein RuntimeDirectory=, sonst verlöre Caddy den Mount).
d /run/wamo-core 0750 wamo-core wamocon -
TMP
chmod 0644 /etc/tmpfiles.d/wamo-core.conf
systemd-tmpfiles --create /etc/tmpfiles.d/wamo-core.conf
stat -c '  %n %U:%G %a' /run/wamo-core

echo "== 3. uv $UV_VERSION (aarch64, Prüfsumme fest)"
install -d -o root -g "$BENUTZER" -m 0750 "$APP"
install -d -o root -g root -m 0755 "$APP/tools" "$APP/bin"
if [ ! -x "$APP/tools/uv" ] || [ "$("$APP/tools/uv" --version | awk '{print $2}')" != "$UV_VERSION" ]; then
  arbeit="$(mktemp -d "$APP/.uv.XXXXXX")"
  curl -fsSL --proto '=https' --tlsv1.2 -o "$arbeit/uv.tgz" \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-unknown-linux-gnu.tar.gz"
  echo "$UV_SHA256  $arbeit/uv.tgz" | sha256sum -c --quiet -
  tar -xzf "$arbeit/uv.tgz" -C "$arbeit" --strip-components=1 uv-aarch64-unknown-linux-gnu/uv
  install -o root -g root -m 0755 "$arbeit/uv" "$APP/tools/uv"
  rm -rf "$arbeit"
fi
echo "  $("$APP/tools/uv" --version)"

echo "== 4. Code nach $APP/app/voice-core (nur Laufzeitdateien, keine Tests, keine .env)"
neu="$(mktemp -d "$APP/.app.XXXXXX")"
install -d -o root -g root -m 0755 "$neu/voice-core"
for f in "$HERE"/kern/*.py; do
  name="$(basename "$f")"
  case "$name" in test_*|conftest.py) continue ;; esac
  install -o root -g root -m 0644 "$f" "$neu/voice-core/$name"
done
install -o root -g root -m 0644 "$HERE/kern/pyproject.toml" "$HERE/kern/uv.lock" "$neu/voice-core/"
sed -i 's/\r$//' "$neu"/voice-core/*.py
chmod 0755 "$neu"
alt="$APP/app"
[ ! -e "$alt" ] || mv "$alt" "$APP/.app.vorher.$$"
mv "$neu" "$alt"
rm -rf "$APP/.app.vorher.$$"
install -o root -g root -m 0644 "$HERE/wamo-ca.crt" "$APP/wamo-ca.crt"
install -o root -g root -m 0755 "$HERE/wamo-core-vorpruefung.sh" "$APP/bin/wamo-core-vorpruefung"
sed -i 's/\r$//' "$APP/bin/wamo-core-vorpruefung"
echo "  $(ls "$alt/voice-core" | wc -l) Dateien"

echo "== 5. venv aus uv.lock ($APP/venv, root-eigen, Bytecode vorab übersetzt)"
install -d -o root -g root -m 0700 /var/cache/wamo-core-build
( cd "$alt/voice-core" && env -i PATH=/usr/bin:/bin HOME=/root UV_CACHE_DIR=/var/cache/wamo-core-build \
    UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT="$APP/venv" UV_NO_PROGRESS=1 \
    "$APP/tools/uv" sync --frozen --no-dev --no-install-project --python "$PYTHON" --quiet )
chown -R root:root "$APP/venv"
chmod -R go-w "$APP/venv"
echo "  $("$APP/venv/bin/python" --version), $(ls "$APP/venv/lib/python3.12/site-packages" | wc -l) Einträge"

echo "== 6. core.env und Hilfsprogramm"
install -o root -g root -m 0755 "$HERE/wamo_core_env.py" /usr/local/sbin/wamo-core-env
sed -i 's/\r$//' /usr/local/sbin/wamo-core-env
[ -d /etc/wamo ] || install -d -o root -g root -m 0755 /etc/wamo
if [ ! -f "$ENV_DATEI" ]; then
  install -o root -g "$BENUTZER" -m 0640 /dev/null "$ENV_DATEI"
  echo "  $ENV_DATEI leer angelegt"
fi
chown root:"$BENUTZER" "$ENV_DATEI"
chmod 0640 "$ENV_DATEI"
/usr/local/sbin/wamo-core-env liste | sed 's/^/  /'

echo "== 7. Firewall: Port 55444 nur Loopback und Büro-LAN"
grep -q '55444' "$HERE/wamo-firewall.sh" || { echo "wamo-firewall.sh ohne Port 55444" >&2; exit 1; }
bash "$HERE/wamo-firewall-einrichten.sh" | grep -E '^(==|  )' | head -20
/usr/local/sbin/wamo-firewall regeln | grep -q 'ctorigdstport 55444' || { echo "Firewall-Regel für 55444 fehlt" >&2; exit 1; }

echo "== 8. systemd-Dienst wamo-core"
install -o root -g root -m 0644 "$HERE/wamo-core.service" /etc/systemd/system/wamo-core.service
sed -i 's/\r$//' /etc/systemd/system/wamo-core.service
systemd-analyze verify /etc/systemd/system/wamo-core.service 2>&1 | grep -v '^$' | sed 's/^/  verify: /' || true
systemctl daemon-reload
systemctl enable wamo-core.service >/dev/null 2>&1
echo "  Import-Probe als $BENUTZER"
( cd "$alt/voice-core" && runuser -u "$BENUTZER" -- env -i PATH=/usr/bin:/bin HOME=/var/lib/wamo-core \
    PYTHONDONTWRITEBYTECODE=1 WAMO_PC_HOST=off "$APP/venv/bin/python" -c 'import bot, kern_dienst, pc_tunnel; print("  Import ok")' 2>&1 \
    | grep -E 'Import ok|Error|error' | head -5 )
if /usr/local/sbin/wamo-core-env pruefen; then
  systemctl restart wamo-core.service
  sleep 4
  systemctl is-active wamo-core.service | sed 's/^/  Dienst: /'
  [ -S /run/wamo-core/core.sock ] && stat -c '  Socket %n %U:%G %a' /run/wamo-core/core.sock
else
  echo "  core.env unvollständig: Dienst nicht gestartet. Vom PC: python scripts/wamo_kern_sokrates2.py alles --email <admin>"
fi
echo "Fertig. Logs: sudo journalctl -u wamo-core -n 50"
