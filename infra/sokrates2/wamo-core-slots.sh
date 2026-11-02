#!/usr/bin/env bash
# Kern-Slots auf Sokrates-2 verwalten (Schritt 2.1 der Selbstüberarbeitung, Konzept docs/selbstueberarbeitung.md Abschnitt A).
# Bausteine für den Anker; von Hand nur mit sudo. Wiederholbar. Ändert die laufende Unit wamo-core.service nie.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh einrichten"          Vorlage wamo-core@.service, Ordner releases/ und slots/
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh release v0.1.0-abc123"  baut /opt/wamo-core/releases/<name> aus ~/wamo-infra/kern
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh slot b v0.1.0-abc123"   setzt /opt/wamo-core/slots/b (nur für einen Slot, der nicht läuft)
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh status"
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh inc a"               schaltet Caddy: neue Sitzungen auf Slot a (Include-Datei slot.inc, caddy reload)
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh inc a auslaufend b"   dasselbe, und Slot b nimmt keinen neuen /start mehr an (409)
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh inc legacy"           Rückweg auf die Standardroute der alten Unit
#   bash wamo-core-slots.sh inc-text a [auslaufend b]                              gibt nur den Text aus (ohne root, für Tests)
#
# Aufbau:  /opt/wamo-core/releases/<name>/voice-core   Code (root, nur Laufzeitdateien)
#          /opt/wamo-core/releases/<name>/venv         eigenes venv aus uv.lock des Release
#          /opt/wamo-core/slots/a, b                   Verweise auf je ein Release
# Der Slot-Verweis wird nie unter einer laufenden Instanz getauscht: wer ein anderes Release in einen laufenden Slot will, stoppt ihn vorher.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/wamo-core
BENUTZER=wamo-core
PYTHON=/usr/bin/python3
UNIT=/etc/systemd/system/wamo-core@.service
NAME_RE='^v[0-9A-Za-z._-]{1,40}$'
CADDY_ETC=/home/wamocon/wamo-tls/caddy/etc/kern   # wird als /etc/caddy/kern in den Container von wamo-caddy eingehängt
CADDY_BESITZER=wamocon

befehl="${1:-status}"
[ "$#" -eq 0 ] || shift

fehler() { echo "wamo-core-slots: $*" >&2; exit 1; }
root_pflicht() { [ "$(id -u)" = 0 ] || fehler "Bitte als root ausführen (sudo)."; }
slot_pruefen() { case "${1:-}" in a|b) ;; *) fehler "Slot muss a oder b sein" ;; esac; }
name_pruefen() { [[ "${1:-}" =~ $NAME_RE ]] || fehler "Release-Name ungültig (erlaubt: v plus Buchstaben, Ziffern, Punkt, Strich, Unterstrich)"; }

cmd_einrichten() {
  root_pflicht
  [ -x "$APP/tools/uv" ] || fehler "$APP/tools/uv fehlt (zuerst wamo-core-einrichten.sh)"
  install -d -o root -g "$BENUTZER" -m 0750 "$APP/releases" "$APP/slots"
  install -o root -g root -m 0644 "$HERE/wamo-core@.service" "$UNIT"
  sed -i 's/\r$//' "$UNIT"
  install -o root -g root -m 0755 "$HERE/wamo-core-vorpruefung.sh" "$APP/bin/wamo-core-vorpruefung"
  sed -i 's/\r$//' "$APP/bin/wamo-core-vorpruefung"
  systemd-analyze verify "$UNIT" 2>&1 | grep -v '^$' | sed 's/^/  verify: /' || true
  systemctl daemon-reload
  echo "Vorlage $UNIT installiert, $APP/releases und $APP/slots bereit (keine Instanz gestartet)."
}

cmd_release() {
  root_pflicht
  name="${1:-}"; name_pruefen "$name"
  quelle="${2:-$HERE/kern}"
  ziel="$APP/releases/$name"
  [ ! -e "$ziel" ] || fehler "Release $name gibt es schon (Releases werden nie überschrieben)"
  [ -f "$quelle/bot.py" ] && [ -f "$quelle/uv.lock" ] && [ -f "$quelle/pyproject.toml" ] || fehler "$quelle ist kein Kern-Ordner (bot.py, uv.lock, pyproject.toml)"
  arbeit="$(mktemp -d "$APP/releases/.neu.XXXXXX")"
  trap 'rm -rf "$arbeit"' EXIT
  install -d -o root -g root -m 0755 "$arbeit/voice-core"
  for f in "$quelle"/*.py; do
    n="$(basename "$f")"
    case "$n" in test_*|conftest.py) continue ;; esac
    install -o root -g root -m 0644 "$f" "$arbeit/voice-core/$n"
  done
  install -o root -g root -m 0644 "$quelle/pyproject.toml" "$quelle/uv.lock" "$arbeit/voice-core/"
  sed -i 's/\r$//' "$arbeit"/voice-core/*.py
  install -d -o root -g root -m 0700 /var/cache/wamo-core-build
  ( cd "$arbeit/voice-core" && env -i PATH=/usr/bin:/bin HOME=/root UV_CACHE_DIR=/var/cache/wamo-core-build \
      UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT="$arbeit/venv" UV_NO_PROGRESS=1 \
      "$APP/tools/uv" sync --frozen --no-dev --no-install-project --python "$PYTHON" --quiet )
  chown -R root:root "$arbeit"
  chmod -R go-w "$arbeit"
  chmod 0755 "$arbeit"
  echo "  Import-Probe als $BENUTZER"
  ( cd "$arbeit/voice-core" && runuser -u "$BENUTZER" -- env -i PATH=/usr/bin:/bin HOME=/var/lib/wamo-core PYTHONDONTWRITEBYTECODE=1 \
      WAMO_PC_HOST=off "$arbeit/venv/bin/python" -c 'import bot, kern_dienst, pc_tunnel; print("  Import ok")' ) || fehler "Import-Probe fehlgeschlagen, Release nicht angelegt"
  mv -T "$arbeit" "$ziel"
  trap - EXIT
  echo "Release $name angelegt: $(ls "$ziel/voice-core" | wc -l) Dateien, $("$ziel/venv/bin/python" --version)."
}

cmd_slot() {
  root_pflicht
  slot_pruefen "${1:-}"; name_pruefen "${2:-}"
  slot="$1"; name="$2"
  [ -f "$APP/releases/$name/voice-core/bot.py" ] && [ -x "$APP/releases/$name/venv/bin/python" ] || fehler "Release $name fehlt oder ist unvollständig"
  if systemctl is-active --quiet "wamo-core@$slot.service"; then
    fehler "wamo-core@$slot läuft: erst stoppen (der Verweis wird nie unter einer laufenden Instanz getauscht)"
  fi
  ln -sfn "$APP/releases/$name" "$APP/slots/.neu.$slot"
  mv -T "$APP/slots/.neu.$slot" "$APP/slots/$slot"
  echo "Slot $slot zeigt jetzt auf Release $name."
}

cmd_status() {
  echo "Releases:"
  ls -1 "$APP/releases" 2>/dev/null | grep -v '^\.' | sed 's/^/  /' || true
  for s in a b; do
    ziel="$(readlink "$APP/slots/$s" 2>/dev/null || true)"
    echo "Slot $s: ${ziel:-(leer)}  $(systemctl is-active "wamo-core@$s.service" 2>/dev/null || true)"
  done
  echo "Alte Unit wamo-core.service: $(systemctl is-active wamo-core.service 2>/dev/null || true)"
}

# Text der Include-Datei für Caddy: legacy (Standardroute auf die alte Unit) oder ein aktiver Slot, optional mit auslaufender Instanz
modus_pruefen() { case "${1:-}" in legacy|a|b) ;; *) fehler "inc: legacy | a | b [auslaufend a|b]" ;; esac; }

inc_text() {
  local modus="${1:-}" aus="${3:-}"
  modus_pruefen "$modus"
  if [ "$modus" = legacy ]; then
    [ -z "${2:-}" ] || fehler "inc legacy kennt keine auslaufende Instanz"
    cat "$HERE/wamo-caddy/kern/slot.inc.legacy"
    return 0
  fi
  if [ -n "${2:-}" ]; then
    [ "$2" = auslaufend ] || fehler "inc: der dritte Teil muss auslaufend heißen"
    slot_pruefen "$aus"
    [ "$aus" != "$modus" ] || fehler "inc: der aktive Slot kann nicht auslaufen"
    sed -e 's/^#AUSLAUF# //' "$HERE/wamo-caddy/kern/slot.inc.vorlage" | sed -e "s/@AKTIV@/$modus/g" -e "s/@AUSLAUFEND@/$aus/g"
  else
    sed -e '/^#AUSLAUF# /d' "$HERE/wamo-caddy/kern/slot.inc.vorlage" | sed -e "s/@AKTIV@/$modus/g"
  fi
}

cmd_inc() {
  root_pflicht
  modus_pruefen "${1:-}"
  [ -d "$CADDY_ETC" ] || fehler "$CADDY_ETC fehlt (zuerst wamo-core-caddy.sh)"
  # Aktiv darf nur ein Slot sein, dessen Instanz läuft: sonst bekäme Caddy einen Socket, den es nicht gibt
  if [ "$1" != legacy ]; then
    systemctl is-active --quiet "wamo-core@$1.service" || fehler "wamo-core@$1 läuft nicht: erst starten und prüfen, dann schalten"
    [ -S "/run/wamo-core/core-$1.sock" ] || fehler "/run/wamo-core/core-$1.sock fehlt"
  fi
  neu="$(mktemp "$CADDY_ETC/.slot.inc.XXXXXX")"
  trap 'rm -f "$neu"' EXIT
  inc_text "$@" > "$neu"
  chown "$CADDY_BESITZER":"$CADDY_BESITZER" "$neu"
  chmod 0644 "$neu"
  alt=""
  if [ -f "$CADDY_ETC/slot.inc" ]; then
    alt="$CADDY_ETC/slot.inc.vorher"
    cp -p "$CADDY_ETC/slot.inc" "$alt"
  fi
  mv -f "$neu" "$CADDY_ETC/slot.inc"
  trap - EXIT
  if ! docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    echo "caddy reload fehlgeschlagen, vorige Fassung wird wiederhergestellt." >&2
    if [ -n "$alt" ]; then mv -f "$alt" "$CADDY_ETC/slot.inc"; else rm -f "$CADDY_ETC/slot.inc"; fi
    docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
    exit 1
  fi
  echo "Caddy schaltet jetzt: $*"
}

case "$befehl" in
  einrichten) cmd_einrichten ;;
  release) cmd_release "$@" ;;
  slot) cmd_slot "$@" ;;
  status) cmd_status ;;
  inc) cmd_inc "$@" ;;
  inc-text) inc_text "$@" ;;
  *) fehler "Befehl: einrichten | release <name> [<ordner>] | slot <a|b> <name> | inc <legacy|a|b> [auslaufend <a|b>] | status" ;;
esac
