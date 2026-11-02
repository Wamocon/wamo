#!/usr/bin/env bash
# Rückweg zu pruefer-einrichten.sh: WAMO-Prüfer-Sandbox auf Sokrates-2 abschalten (Schritt 1.2c). Wiederholbar.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/pruefer-rueckweg.sh"                       # Proxy und Regeln aus, alle Dateien bleiben
#   ssh sokrates2 "sudo bash ~/wamo-infra/pruefer-rueckweg.sh --entfernen"           # entfernt alles (siehe unten)
#   ssh sokrates2 "sudo bash ~/wamo-infra/pruefer-rueckweg.sh --entfernen --trocken" # zeigt nur, was geschähe
# Probelauf in einem Wegwerf-Ordner: WAMO_PRUEFER_WURZEL=/tmp/pruefer-probe bash pruefer-rueckweg.sh --entfernen
#
# Die Prüfer-Sandbox abzuschalten gefährdet nichts: Sie hängt von keinem anderen WAMO-Dienst ab, kein Dienst hängt von ihr ab
# (der Release-Dienst ruft sie erst in einem späteren Schritt über den Anker auf).
# --entfernen löscht Programme, Werkzeuge, Arbeitsordner, Regeln, Units, die bwrap-Kopie und das AppArmor-Profil. Bewusst NICHT
# gelöscht: die Benutzer wamo-pruefer und wamo-pruefer-proxy (Entfernen nur bewusst: sudo userdel <name>).
# Nichts, was andere Teile von WAMO betrifft (Anker, Release-Dienst, Kern, Gerätedienst), wird angefasst.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_PRUEFER_WURZEL:-}"
TROCKEN=0
ENTFERNEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    --entfernen) ENTFERNEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --entfernen, --trocken)" >&2; exit 2 ;;
  esac
done

BASIS="$WURZEL/opt/wamo-pruefer"
ARBEIT="$WURZEL/srv/wamo-pruefer"
ETC="$WURZEL/etc/wamo-pruefer"
UNIT_DIR="$WURZEL/etc/systemd/system"
BWRAP_DIR="$WURZEL/usr/local/lib/wamo-pruefer"
APPARMOR="$WURZEL/etc/apparmor.d/wamo-pruefer-bwrap"

pruefe_herkunft() {
  local erlaubt uid modus pfad
  erlaubt=" 0 $(id -u) ${SUDO_UID:-} "
  while read -r uid modus pfad; do
    if [[ "$erlaubt" != *" $uid "* ]] || (( 8#$modus & 8#022 )); then
      echo "Unsicherer Ablageort: $pfad (Besitzer $uid, Rechte $modus). Ordner im eigenen Home ablegen und" \
        "mit chmod -R go-w schützen." >&2
      exit 1
    fi
  done < <(stat -c '%u %a %n' "$(dirname "$HERE")"; find "$HERE" \( -type l -printf '%U 777 %p\n' \) -o -printf '%U %m %p\n')
}

# tu Beschreibung Befehl...: führt den Befehl aus, im Trockenlauf nur die Beschreibung zeigen
tu() {
  local text="$1"
  shift
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] $text"; else "$@"; fi
}

# Löschen nur für die festen Ordner der Prüfer-Sandbox (nie ein leerer Pfad, nie die Wurzel, nie ein Link)
loesche_ordner() {
  case "$1" in
    "$BASIS"|"$ARBEIT"|"$ETC"|"$BWRAP_DIR") ;;
    *) echo "Unerwarteter Pfad zum Löschen: $1" >&2; exit 1 ;;
  esac
  if [ -z "$1" ] || [ "$1" = / ] || [ -L "$1" ]; then echo "Pfad nicht erlaubt: $1" >&2; exit 1; fi
  tu "löscht $1" rm -rf --one-file-system -- "$1"
}

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_herkunft
else
  # Der Probelauf löscht nur in einem Wegwerf-Ordner und läuft nie als root (siehe pruefer-einrichten.sh)
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_PRUEFER_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_PRUEFER_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

echo "== 1. Proxy und Regeln aus"
if [ -z "$WURZEL" ]; then
  tu "beendet laufende Prüfungen (Einheiten wamo-pruefer-lauf-*)" \
    bash -c 'systemctl stop "wamo-pruefer-lauf-*.service" 2>/dev/null; pkill -KILL -u wamo-pruefer 2>/dev/null; true'
  tu "stoppt und deaktiviert den Annahme-Socket" systemctl disable --now wamo-pruefer-annahme.socket || true
  tu "beendet laufende Anfragen an die Annahme" bash -c 'systemctl stop "wamo-pruefer-annahme@*.service" 2>/dev/null; true'
  tu "stoppt und deaktiviert die Proxys pakete und claude" systemctl disable --now wamo-pruefer-proxy@pakete.service wamo-pruefer-proxy@claude.service || true
  tu "stoppt und deaktiviert die Ausgangsregeln (löscht die nft-Tabelle wamo_pruefer_ausgang)" \
    systemctl disable --now wamo-pruefer-ausgang.service || true
  echo "  Proxy pakete: $(systemctl is-active wamo-pruefer-proxy@pakete.service 2>/dev/null || true), claude: $(systemctl is-active wamo-pruefer-proxy@claude.service 2>/dev/null || true)"
fi

if [ "$ENTFERNEN" = 0 ]; then
  echo "Fertig. Programme, Werkzeuge und Regeln bleiben liegen. Ganz entfernen: --entfernen"
  exit 0
fi

echo "== 2. Units, Profil und Ordner entfernen"
if [ -z "$WURZEL" ] && [ -f "$APPARMOR" ]; then
  tu "entlädt das AppArmor-Profil wamo-pruefer-bwrap" apparmor_parser -R "$APPARMOR" || true
fi
tu "löscht die Units und das AppArmor-Profil" rm -f -- "$UNIT_DIR/wamo-pruefer-ausgang.service" "$UNIT_DIR/wamo-pruefer-proxy@.service" "$UNIT_DIR/wamo-pruefer-annahme.socket" "$UNIT_DIR/wamo-pruefer-annahme@.service" "$APPARMOR"
if [ -z "$WURZEL" ]; then
  tu "systemctl daemon-reload" systemctl daemon-reload
  tu "löscht /run/wamo-pruefer.lock" rm -f -- /run/wamo-pruefer.lock
fi
for d in "$BASIS" "$ARBEIT" "$ETC" "$BWRAP_DIR"; do
  if [ -e "$d" ]; then loesche_ordner "$d"; fi
done
echo "Fertig. Die Prüfer-Sandbox ist entfernt (die Benutzer wamo-pruefer und wamo-pruefer-proxy bleiben). Neu einrichten: sudo bash pruefer-einrichten.sh"
