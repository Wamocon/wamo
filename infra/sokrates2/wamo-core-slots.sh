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
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh umstellen <release>"  einmalig: Live-Betrieb von wamo-core.service auf Slot a (nur bei Ruhe)
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh umstellung-zurueck"    zurück auf wamo-core.service und die Standardroute
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh umschalten-kern <release>"  Kern-Release ohne Lücke: freien Slot starten, prüfen, Caddy umschalten, nach dem Nachlauf die alte Instanz auslaufen lassen
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh bewaehren [minuten]"        überwacht den neuen Slot (Standard 30 Minuten), bei Ausfall automatisch zurück
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh ausleiten"                  beendet die auslaufende Instanz, wenn sie keine Sitzung mehr hat und die Bewährung vorbei ist
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-core-slots.sh rueckweg-kern"              schaltet auf die vorige Instanz zurück (solange sie noch läuft)
#   bash wamo-core-slots.sh inc-text a [auslaufend b]                              gibt nur den Text aus (ohne root, für Tests)
#
# Aufbau:  /opt/wamo-core/releases/<name>/voice-core   Code (root, nur Laufzeitdateien)
#          /opt/wamo-core/releases/<name>/venv         eigenes venv aus uv.lock des Release
#          /opt/wamo-core/slots/a, b                   Verweise auf je ein Release
# Der Slot-Verweis wird nie unter einer laufenden Instanz getauscht: wer ein anderes Release in einen laufenden Slot will, stoppt ihn vorher.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# Als Schritt-Skript des Ankers heißt diese Datei rueckweg-kern (root-eigene, per Prüfsumme gesicherte Kopie in /usr/local/lib/wamo-anker/schritte):
# Der Anker ruft sie mit Version, Commit und Slot aus seiner Zustandsdatei auf. Die Vorlagen der Include-Datei liegen dann neben dem Skript.
if [ "$(basename "$0")" = rueckweg-kern ]; then set -- rueckweg-zu "$@"; fi
VORLAGEN="$HERE/wamo-caddy/kern"
[ -d "$VORLAGEN" ] || VORLAGEN="$HERE"
APP=/opt/wamo-core
BENUTZER=wamo-core
PYTHON=/usr/bin/python3
UNIT=/etc/systemd/system/wamo-core@.service
NAME_RE='^v[0-9A-Za-z._-]{1,40}$'
CADDY_ETC=/home/wamocon/wamo-tls/caddy/etc/kern   # wird als /etc/caddy/kern in den Container von wamo-caddy eingehängt
CADDY_BESITZER=wamocon
# Einstellungen, die andere Einrichtungsskripte der alten Unit als Drop-in mitgeben (erlaubter Ursprung der Oberfläche, Fernzugriff): sie gelten
# auch für die Slots. Ein Slot ohne sie weist die Oberfläche ab (CORS) und kennt den Fernzugriff nicht (Fehler der ersten Umstellung, 02.11.2026).
DROPIN_ALT="${WAMO_DROPIN_ALT:-/etc/systemd/system/wamo-core.service.d}"
DROPIN_NEU="${WAMO_DROPIN_NEU:-/etc/systemd/system/wamo-core@.service.d}"
DROPIN_MARKE="# gespiegelt von wamo-core.service.d durch wamo-core-slots.sh, nicht von Hand ändern"

befehl="${1:-status}"
[ "$#" -eq 0 ] || shift

fehler() { echo "wamo-core-slots: $*" >&2; exit 1; }
root_pflicht() { [ "$(id -u)" = 0 ] || fehler "Bitte als root ausführen (sudo)."; }
slot_pruefen() { case "${1:-}" in a|b) ;; *) fehler "Slot muss a oder b sein" ;; esac; }
name_pruefen() { [[ "${1:-}" =~ $NAME_RE ]] || fehler "Release-Name ungültig (erlaubt: v plus Buchstaben, Ziffern, Punkt, Strich, Unterstrich)"; }

cmd_einrichten() {
  root_pflicht
  dropins_spiegeln
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
    cat "$VORLAGEN/slot.inc.legacy"
    return 0
  fi
  if [ -n "${2:-}" ]; then
    [ "$2" = auslaufend ] || fehler "inc: der dritte Teil muss auslaufend heißen"
    slot_pruefen "$aus"
    [ "$aus" != "$modus" ] || fehler "inc: der aktive Slot kann nicht auslaufen"
    sed -e 's/^#AUSLAUF# //' "$VORLAGEN/slot.inc.vorlage" | sed -e "s/@AKTIV@/$modus/g" -e "s/@AUSLAUFEND@/$aus/g"
  else
    sed -e '/^#AUSLAUF# /d' "$VORLAGEN/slot.inc.vorlage" | sed -e "s/@AKTIV@/$modus/g"
  fi
}

cmd_inc() {
  root_pflicht
  local tmp vorher
  modus_pruefen "${1:-}"
  [ -d "$CADDY_ETC" ] || fehler "$CADDY_ETC fehlt (zuerst wamo-core-caddy.sh)"
  # Aktiv darf nur ein Slot sein, dessen Instanz läuft: sonst bekäme Caddy einen Socket, den es nicht gibt
  if [ "$1" != legacy ]; then
    systemctl is-active --quiet "wamo-core@$1.service" || fehler "wamo-core@$1 läuft nicht: erst starten und prüfen, dann schalten"
    [ -S "/run/wamo-core/core-$1.sock" ] || fehler "/run/wamo-core/core-$1.sock fehlt"
  fi
  tmp="$(mktemp "$CADDY_ETC/.slot.inc.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  inc_text "$@" > "$tmp"
  chown "$CADDY_BESITZER":"$CADDY_BESITZER" "$tmp"
  chmod 0644 "$tmp"
  vorher=""
  if [ -f "$CADDY_ETC/slot.inc" ]; then
    vorher="$CADDY_ETC/slot.inc.vorher"
    cp -p "$CADDY_ETC/slot.inc" "$vorher"
  fi
  mv -f "$tmp" "$CADDY_ETC/slot.inc"
  trap - EXIT
  if ! docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    echo "caddy reload fehlgeschlagen, vorige Fassung wird wiederhergestellt." >&2
    if [ -n "$vorher" ]; then mv -f "$vorher" "$CADDY_ETC/slot.inc"; else rm -f "$CADDY_ETC/slot.inc"; fi
    docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
    exit 1
  fi
  echo "Caddy schaltet jetzt: $*"
}

# Spiegelt die Drop-ins der alten Unit in die Vorlage (und löscht gespiegelte, die es oben nicht mehr gibt). Eigene Drop-ins der Vorlage bleiben,
# erkennbar daran, dass sie nicht mit der Marke beginnen.
dropins_spiegeln() {
  local f n
  if [ -d "$DROPIN_ALT" ]; then
    install -d -m 0755 "$DROPIN_NEU"
    for f in "$DROPIN_ALT"/*.conf; do
      [ -f "$f" ] || continue
      n="$(basename "$f")"
      { echo "$DROPIN_MARKE"; cat "$f"; } > "$DROPIN_NEU/.$n.neu"
      chmod 0644 "$DROPIN_NEU/.$n.neu"
      mv -f "$DROPIN_NEU/.$n.neu" "$DROPIN_NEU/$n"
    done
  fi
  [ -d "$DROPIN_NEU" ] || return 0
  for f in "$DROPIN_NEU"/*.conf; do
    [ -f "$f" ] || continue
    [ "$(head -n 1 "$f")" = "$DROPIN_MARKE" ] || continue
    [ -f "$DROPIN_ALT/$(basename "$f")" ] || rm -f "$f"
  done
  if [ -z "${WAMO_DROPIN_NEU:-}" ]; then systemctl daemon-reload; fi
}

cmd_dropins_spiegeln() {
  [ -n "${WAMO_DROPIN_NEU:-}" ] || root_pflicht
  dropins_spiegeln
  echo "Drop-ins gespiegelt: $(ls -1 "$DROPIN_NEU" 2>/dev/null | grep -c '\.conf$' || true) in $DROPIN_NEU"
}

# Ruhe: im Journal der angegebenen Unit steht seit N Minuten nichts (eine laufende Sitzung schreibt laufend)
ruhe_pruefen() {
  local unit="$1" min="${RUHE_MIN:-15}" n
  # nicht angemeldete, abgelehnte Aufrufe (Gesundheitsproben) sind keine Aktivität; jede Sitzung schreibt dagegen laufend
  n="$(journalctl -u "$unit" --since "-${min}min" --no-pager 2>/dev/null | grep -vc -e '^-- ' -e 'No entries' -e 'Zugang abgelehnt' -e ' 401 Unauthorized' || true)"
  [ "${n:-0}" = 0 ] || fehler "$unit war in den letzten $min Minuten aktiv ($n Zeilen im Journal): nicht umstellen (RUHE_MIN anpassen oder warten)"
}

warte_auf_slot() {
  local slot="$1" code=""
  for _ in $(seq 1 45); do
    if [ -S "/run/wamo-core/core-$slot.sock" ]; then
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 --unix-socket "/run/wamo-core/core-$slot.sock" http://localhost/start || true)"
      [ "$code" = 401 ] && return 0
    fi
    systemctl is-active --quiet "wamo-core@$slot.service" || fehler "wamo-core@$slot ist ausgefallen (journalctl -u wamo-core@$slot)"
    sleep 2
  done
  fehler "wamo-core@$slot antwortet nicht mit 401 auf /start (letzter Code: ${code:-keiner})"
}

# Einmalige Umstellung: erst startet Slot a neben der alten Unit, dann schaltet Caddy um, dann endet die alte Unit. Neue Sitzungen sehen keine Lücke.
cmd_umstellen() {
  root_pflicht
  local name
  dropins_spiegeln
  name="${1:-}"; name_pruefen "$name"
  systemctl is-active --quiet wamo-core.service || fehler "wamo-core.service läuft nicht: nichts umzustellen (Rückweg: umstellung-zurueck)"
  ! systemctl is-active --quiet wamo-core@a.service || fehler "wamo-core@a läuft schon"
  ruhe_pruefen wamo-core.service
  cmd_slot a "$name"
  systemctl start wamo-core@a.service
  warte_auf_slot a
  cmd_inc a
  systemctl disable --now wamo-core.service >/dev/null 2>&1
  systemctl enable wamo-core@a.service >/dev/null 2>&1
  echo "Umgestellt: Slot a (Release $name) bedient neue Sitzungen, wamo-core.service ist beendet und deaktiviert."
  echo "Rückweg: sudo bash wamo-core-slots.sh umstellung-zurueck"
}

cmd_umstellung_zurueck() {
  root_pflicht
  systemctl enable --now wamo-core.service >/dev/null 2>&1 || fehler "wamo-core.service ließ sich nicht starten"
  for _ in $(seq 1 45); do
    [ -S /run/wamo-core/core.sock ] && [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 --unix-socket /run/wamo-core/core.sock http://localhost/start || true)" = 401 ] && break
    sleep 2
  done
  [ -S /run/wamo-core/core.sock ] || fehler "wamo-core.service antwortet nicht"
  cmd_inc legacy
  systemctl disable --now wamo-core@a.service wamo-core@b.service >/dev/null 2>&1 || true
  echo "Zurückgestellt: wamo-core.service bedient wieder alle Sitzungen, die Slots sind beendet."
}

# Der Slot, auf den Caddy neue Sitzungen schickt (steht in der Include-Datei, die dieses Skript erzeugt)
aktiver_slot() {
  local a
  a="$(awk '/^handle \{$/ {f=1; next} f && /import kern_weiter/ {print $3; exit}' "$CADDY_ETC/slot.inc" 2>/dev/null | sed -n 's#.*/core-\([ab]\)\.sock$#\1#p')"
  [ -n "$a" ] || fehler "aktiver Slot nicht lesbar (slot.inc ist die Standardroute der alten Unit oder fehlt: erst umstellen)"
  echo "$a"
}
anderer_slot() { [ "$1" = a ] && echo b || echo a; }

# Sitzungen einer Instanz: Dateien der Brücke (Kern mit Schritt 2.3); ältere Kerne kennen sie nicht, dann zählt die Ruhe im Journal
sitzungen_von() {
  local n=0
  [ -d /run/wamo-core/sitzungen ] && n="$(find /run/wamo-core/sitzungen -maxdepth 1 -name "*.$1" ! -name '*.abloesen.*' 2>/dev/null | wc -l)"
  echo "$n"
}

# Kern-Release ohne Lücke (Konzept A.4): erst startet der freie Slot und wird geprüft (nichts ist umgeschaltet, wenn das scheitert), dann
# schickt Caddy neue Sitzungen dorthin, laufende bleiben auf ihrem Pfad. Nach dem Nachlauf nimmt die alte Instanz keinen neuen /start mehr an.
cmd_umschalten_kern() {
  root_pflicht
  local name alt neu
  dropins_spiegeln
  name="${1:-}"; name_pruefen "$name"
  alt="$(aktiver_slot)"; neu="$(anderer_slot "$alt")"
  systemctl is-active --quiet "wamo-core@$alt.service" || fehler "aktiver Slot $alt läuft nicht: kein geordneter Wechsel (umstellung-zurueck oder Slot starten)"
  ! systemctl is-active --quiet "wamo-core@$neu.service" || fehler "Slot $neu läuft noch (erst ausleiten)"
  cmd_slot "$neu" "$name"
  systemctl start "wamo-core@$neu.service"
  if ! ( warte_auf_slot "$neu" ); then
    systemctl stop "wamo-core@$neu.service" || true
    fehler "Slot $neu wurde nicht gesund: nichts umgeschaltet, Slot $alt bedient weiter"
  fi
  systemctl enable "wamo-core@$neu.service" >/dev/null 2>&1   # nach einem Neustart des Servers soll der aktive Slot wieder laufen
  cmd_inc "$neu"
  echo "Neue Sitzungen gehen jetzt auf Slot $neu (Release $name). Nachlauf ${NACHLAUF_S:-60} s, dann nimmt Slot $alt keinen neuen Start mehr an."
  sleep "${NACHLAUF_S:-60}"
  cmd_inc "$neu" auslaufend "$alt"
  echo "Slot $alt läuft aus ($(sitzungen_von "$alt") Sitzungen, die Brücke zählt nur Kerne ab Schritt 2.3). Weiter mit: ausleiten (nach der Bewährung) oder rueckweg-kern."
}

# Bewährung: der aktive Slot muss antworten; dreimal hintereinander nicht gesund heißt zurück auf die vorige Instanz
cmd_bewaehren() {
  root_pflicht
  local min akt ende schlecht code
  min="${1:-30}"
  [[ "$min" =~ ^[0-9]{1,3}$ ]] || fehler "bewaehren: Minuten als Zahl"
  akt="$(aktiver_slot)"
  ende=$(( $(date +%s) + min * 60 )); schlecht=0
  while [ "$(date +%s)" -lt "$ende" ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 --unix-socket "/run/wamo-core/core-$akt.sock" http://localhost/start || true)"
    if [ "$code" = 401 ] && systemctl is-active --quiet "wamo-core@$akt.service"; then schlecht=0; else schlecht=$(( schlecht + 1 )); fi
    if [ "$schlecht" -ge 3 ]; then
      echo "Slot $akt ist nicht gesund (3 Proben): automatischer Rückweg." >&2
      cmd_rueckweg_kern || true
      exit 1
    fi
    sleep "${PROBE_S:-10}"
  done
  echo "Bewährung bestanden: Slot $akt war $min Minuten gesund."
}

cmd_rueckweg_kern() {
  root_pflicht
  local alt neu
  neu="$(aktiver_slot)"; alt="$(anderer_slot "$neu")"
  systemctl is-active --quiet "wamo-core@$alt.service" || fehler "Slot $alt läuft nicht mehr: kein schneller Rückweg (das vorige Release erneut bereitstellen)"
  [ -S "/run/wamo-core/core-$alt.sock" ] || fehler "/run/wamo-core/core-$alt.sock fehlt"
  systemctl enable "wamo-core@$alt.service" >/dev/null 2>&1
  cmd_inc "$alt" auslaufend "$neu"
  echo "Zurück auf Slot $alt. Slot $neu nimmt keine neue Sitzung mehr an; seine laufenden enden von selbst."
}

# Rückweg des Ankers (Schritt rueckweg-kern): neue Sitzungen gehen wieder auf diesen Slot, in dem genau das Release der letzten guten Fassung läuft.
# Idempotent. Läuft der Slot nicht, wird er (mit seinem Verweis auf dieses Release) gestartet und erst nach der Gesundheitsprobe geschaltet.
# Version und Commit stammen aus der Zustandsdatei des Ankers, nie aus einer Anfrage; der Release-Name ist <Version>-<erste 7 Zeichen des Commits>.
cmd_rueckweg_zu() {
  local ver="${1:-}" commit="${2:-}" slot="${3:-}" name anderer
  [[ "$ver" =~ ^v(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$ ]] || fehler "rueckweg-zu: Version ungültig"
  [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || fehler "rueckweg-zu: Commit ungültig"
  slot_pruefen "$slot"
  name="$ver-${commit:0:7}"
  name_pruefen "$name"
  root_pflicht   # erst nach der Prüfung der Parameter: so lassen sie sich ohne root testen
  anderer="$(anderer_slot "$slot")"
  dropins_spiegeln
  if systemctl is-active --quiet "wamo-core@$slot.service"; then
    [ "$(basename "$(readlink "$APP/slots/$slot" 2>/dev/null)")" = "$name" ] || fehler "Slot $slot läuft mit einem anderen Release als $name: kein Rückweg dorthin"
  else
    cmd_slot "$slot" "$name"
    systemctl start "wamo-core@$slot.service"
    if ! ( warte_auf_slot "$slot" ); then
      systemctl stop "wamo-core@$slot.service" || true
      fehler "Slot $slot wurde nicht gesund: nichts umgeschaltet"
    fi
  fi
  systemctl enable "wamo-core@$slot.service" >/dev/null 2>&1
  if systemctl is-active --quiet "wamo-core@$anderer.service"; then
    cmd_inc "$slot" auslaufend "$anderer"
  else
    cmd_inc "$slot"
  fi
  echo "Rückweg: Slot $slot (Release $name) bedient neue Sitzungen."
}

# Beendet die auslaufende Instanz, aber nur ohne Sitzung und nach der Bewährung (so lange bleibt der Rückweg sofort möglich)
cmd_ausleiten() {
  root_pflicht
  local akt alt alter
  akt="$(aktiver_slot)"; alt="$(anderer_slot "$akt")"
  systemctl is-active --quiet "wamo-core@$alt.service" || { echo "Nichts auszuleiten: Slot $alt läuft nicht."; return 0; }
  alter=$(( $(date +%s) - $(stat -c %Y "$CADDY_ETC/slot.inc") ))
  [ "$alter" -ge "${BEWAEHRUNG_S:-1800}" ] || fehler "Die Bewährung läuft noch ($alter s von ${BEWAEHRUNG_S:-1800} s): der Rückweg soll sofort möglich bleiben"
  [ "$(sitzungen_von "$alt")" = 0 ] || fehler "Slot $alt hat noch $(sitzungen_von "$alt") Sitzung(en)"
  ruhe_pruefen "wamo-core@$alt.service"
  systemctl stop "wamo-core@$alt.service"
  systemctl disable "wamo-core@$alt.service" >/dev/null 2>&1 || true
  systemctl enable "wamo-core@$akt.service" >/dev/null 2>&1 || true
  echo "Slot $alt beendet und für den Neustart abgewählt. Aktiv bleibt Slot $akt."
}

case "$befehl" in
  einrichten) cmd_einrichten ;;
  release) cmd_release "$@" ;;
  slot) cmd_slot "$@" ;;
  status) cmd_status ;;
  inc) cmd_inc "$@" ;;
  inc-text) inc_text "$@" ;;
  umstellen) cmd_umstellen "$@" ;;
  dropins-spiegeln) cmd_dropins_spiegeln ;;
  umschalten-kern) cmd_umschalten_kern "$@" ;;
  bewaehren) cmd_bewaehren "$@" ;;
  ausleiten) cmd_ausleiten ;;
  rueckweg-kern) cmd_rueckweg_kern ;;
  rueckweg-zu) cmd_rueckweg_zu "$@" ;;
  umstellung-zurueck) cmd_umstellung_zurueck ;;
  *) fehler "Befehl: einrichten | release <name> [<ordner>] | slot <a|b> <name> | inc <legacy|a|b> [auslaufend <a|b>] | dropins-spiegeln | umstellen <release> | umstellung-zurueck | umschalten-kern <release> | bewaehren [min] | ausleiten | rueckweg-kern | rueckweg-zu <version> <commit> <slot> | status" ;;
esac
