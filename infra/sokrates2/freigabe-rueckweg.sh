#!/usr/bin/env bash
# Rückweg zu freigabe-einrichten.sh: WAMO-Freigabe-Dienst auf Sokrates-2 abschalten (Schritt 1.4b). Wiederholbar.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/freigabe-rueckweg.sh"                       # Dienst und Ausgangsregel aus, alle Dateien bleiben
#   ssh sokrates2 "sudo bash ~/wamo-infra/freigabe-rueckweg.sh --entfernen"           # entfernt den Dienst ganz (siehe unten)
#   ssh sokrates2 "sudo bash ~/wamo-infra/freigabe-rueckweg.sh --entfernen --trocken" # zeigt nur, was geschähe
# Probelauf in einem Wegwerf-Ordner: WAMO_FREIGABE_WURZEL=/tmp/freigabe-probe bash freigabe-rueckweg.sh --entfernen
#
# Den Dienst abzuschalten gefährdet nichts: Er schreibt nur Freigaben in die Datenbank; ohne ihn gibt es keine neuen Freigaben, bereits
# geschriebene bleiben, und der Rückweg der Produktion (wamo-anker rueckweg) braucht ihn nicht.
# --entfernen löscht Programme, Bibliotheken, die Kopie der Zugangsdatei (/etc/wamo-freigabe, die Datei des Ankers bleibt), die
# Ausgangsregel, die Units und den Laufordner. Der Benutzer wamo-anker bleibt; Entfernen nur bewusst: sudo userdel wamo-anker.
# Nichts, was andere Teile von WAMO betrifft (Anker, Release-Dienst, Kern, Oberfläche), wird angefasst.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_FREIGABE_WURZEL:-}"
TROCKEN=0
ENTFERNEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    --entfernen) ENTFERNEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --entfernen, --trocken)" >&2; exit 2 ;;
  esac
done

BASIS="$WURZEL/opt/wamo-freigabe"
APP="$BASIS/app"
PYLIB="$BASIS/pylib"
KONFIG="$WURZEL/etc/wamo-freigabe"
AUSGANG_DIR="$WURZEL/etc/wamo-freigabe-ausgang"
AUSGANG_UNIT="$WURZEL/etc/systemd/system/wamo-freigabe-ausgang.service"
UNIT="$WURZEL/etc/systemd/system/wamo-freigabe.service"
LAUF="$WURZEL/run/wamo-freigabe"

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

# Löschen nur für die festen Ordner des Freigabe-Dienstes (nie ein leerer Pfad, nie die Wurzel, nie ein Link)
loesche_ordner() {
  case "$1" in
    "$APP"|"$APP.vorher"|"$PYLIB"|"$PYLIB.vorher"|"$KONFIG"|"$AUSGANG_DIR"|"$LAUF") ;;
    *) echo "Unerwarteter Pfad zum Löschen: $1" >&2; exit 1 ;;
  esac
  if [ -z "$1" ] || [ "$1" = / ] || [ -L "$1" ]; then echo "Pfad nicht erlaubt: $1" >&2; exit 1; fi
  tu "löscht $1" rm -rf -- "$1"
}

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_herkunft
else
  # Der Probelauf löscht nur in einem Wegwerf-Ordner und läuft nie als root (siehe freigabe-einrichten.sh)
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_FREIGABE_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_FREIGABE_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

echo "== 1. Dienst aus"
if [ -z "$WURZEL" ]; then
  tu "stoppt und deaktiviert wamo-freigabe.service" systemctl disable --now wamo-freigabe.service || true
  tu "stoppt und deaktiviert die Ausgangsregel (löscht die nft-Tabelle wamo_freigabe_ausgang)" \
    systemctl disable --now wamo-freigabe-ausgang.service || true
  echo "  wamo-freigabe: $(systemctl is-active wamo-freigabe.service 2>/dev/null || true)"
fi
tu "löscht $LAUF/freigabe.sock" rm -f -- "$LAUF/freigabe.sock"

if [ "$ENTFERNEN" = 0 ]; then
  echo "Fertig. Programme und Bibliotheken bleiben. Ganz entfernen: --entfernen"
  exit 0
fi

echo "== 2. Programme, Bibliotheken, Konfiguration, Units entfernen"
loesche_ordner "$APP"
if [ -e "$APP.vorher" ]; then loesche_ordner "$APP.vorher"; fi
loesche_ordner "$PYLIB"
if [ -e "$PYLIB.vorher" ]; then loesche_ordner "$PYLIB.vorher"; fi
loesche_ordner "$KONFIG"
loesche_ordner "$AUSGANG_DIR"
tu "löscht $UNIT, $AUSGANG_UNIT und pylib.SHA256SUMS" rm -f -- "$UNIT" "$AUSGANG_UNIT" "$BASIS/pylib.SHA256SUMS"
if [ -z "$WURZEL" ]; then tu "systemctl daemon-reload" systemctl daemon-reload; fi
if [ -d "$BASIS" ] && [ -z "$(ls -A "$BASIS" 2>/dev/null)" ]; then tu "löscht den leeren Ordner $BASIS" rmdir -- "$BASIS"; fi

echo "== 3. Laufordner entfernen"
loesche_ordner "$LAUF"
echo "Fertig. Der Freigabe-Dienst ist entfernt (Benutzer wamo-anker und die Zugangsdatei des Ankers bleiben). Neu einrichten: sudo bash freigabe-einrichten.sh"
