#!/usr/bin/env bash
# Rückweg zu anker-einrichten.sh: WAMO-Notanker auf Sokrates-2 abschalten.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/anker-rueckweg.sh"                       # Dienst aus, alle Dateien bleiben
#   ssh sokrates2 "sudo bash ~/wamo-infra/anker-rueckweg.sh --entfernen"           # entfernt den Anker ganz (siehe unten)
#   ssh sokrates2 "sudo bash ~/wamo-infra/anker-rueckweg.sh --entfernen --trocken" # zeigt nur, was geschähe
# Probelauf in einem Wegwerf-Ordner: WAMO_ANKER_WURZEL=/tmp/anker-probe bash anker-rueckweg.sh --entfernen
#
# --entfernen löscht Programme, Unit, Verknüpfung, tmpfiles-Datei, stand.json und die Schreibsperre. Bewusst NICHT
# gelöscht, sondern gesichert oder behalten:
#   Audit-Protokoll   wird nach /var/backups/wamo-anker/audit.<Zeit>.jsonl verschoben (Beweismittel, nie gelöscht)
#   stand.json        eine Kopie liegt daneben im Sicherungsordner
#   selbstaenderung.aus  die Sperre bleibt, auch ohne Anker: sie hält die Selbständerung angehalten (fail closed).
#                        Entfernen nur bewusst: sudo rm /etc/wamo-anker/selbstaenderung.aus
# Nichts, was andere Teile von WAMO betrifft, wird angefasst.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_ANKER_WURZEL:-}"
TROCKEN=0
ENTFERNEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    --entfernen) ENTFERNEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --entfernen, --trocken)" >&2; exit 2 ;;
  esac
done

LIB="$WURZEL/usr/local/lib/wamo-anker"
BIN="$WURZEL/usr/local/bin/wamo-anker"
KONFIG="$WURZEL/etc/wamo-anker"
ZUSTAND="$WURZEL/var/lib/wamo-anker"
LOG="$WURZEL/var/log/wamo-anker"
SICHERUNG="$WURZEL/var/backups/wamo-anker"
UNIT="$WURZEL/etc/systemd/system/wamo-anker.service"
TMPFILES="$WURZEL/etc/tmpfiles.d/wamo-anker.conf"
LAUF="$WURZEL/run/wamo-anker"

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

# Löschen nur für die festen Anker-Ordner (nie ein leerer Pfad, nie die Wurzel)
loesche_ordner() {
  case "$1" in
    "$LIB"|"$LIB.vorher"|"$ZUSTAND"|"$LAUF") ;;
    *) echo "Unerwarteter Pfad zum Löschen: $1" >&2; exit 1 ;;
  esac
  if [ -z "$1" ] || [ "$1" = / ] || [ -L "$1" ]; then echo "Pfad nicht erlaubt: $1" >&2; exit 1; fi
  tu "löscht $1" rm -rf -- "$1"
}

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_herkunft
else
  # Der Probelauf löscht nur in einem Wegwerf-Ordner und läuft nie als root (siehe anker-einrichten.sh)
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_ANKER_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_ANKER_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

echo "== 1. Dienst aus"
if [ -z "$WURZEL" ]; then
  tu "stoppt und deaktiviert wamo-anker.service" systemctl disable --now wamo-anker.service || true
  echo "  wamo-anker: $(systemctl is-active wamo-anker.service 2>/dev/null || true)"
fi
tu "löscht $LAUF/anker.sock" rm -f -- "$LAUF/anker.sock"

if [ "$ENTFERNEN" = 0 ]; then
  echo "Fertig. Programme, Zustand, Audit-Protokoll und Sperre bleiben. Ganz entfernen: --entfernen"
  exit 0
fi

echo "== 2. Zustand und Audit-Protokoll sichern"
sichern() {
  local zeit
  zeit="$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -m 0700 "$SICHERUNG"
  if [ -f "$ZUSTAND/stand.json" ]; then cp -p "$ZUSTAND/stand.json" "$SICHERUNG/stand.$zeit.json"; fi
  if [ -f "$LOG/audit.jsonl" ]; then
    if [ -z "$WURZEL" ] && command -v chattr >/dev/null; then chattr -a "$LOG/audit.jsonl" 2>/dev/null || true; fi
    mv "$LOG/audit.jsonl" "$SICHERUNG/audit.$zeit.jsonl"
    echo "  Audit-Protokoll liegt jetzt in $SICHERUNG/audit.$zeit.jsonl"
  fi
}
if [ "$TROCKEN" = 1 ]; then echo "  [trocken] sichert stand.json und verschiebt audit.jsonl nach $SICHERUNG"; else sichern; fi

echo "== 3. Programme, Unit und Verknüpfungen entfernen"
if [ -L "$BIN" ]; then tu "löscht die Verknüpfung $BIN" rm -f -- "$BIN"; fi
loesche_ordner "$LIB"
if [ -e "$LIB.vorher" ]; then loesche_ordner "$LIB.vorher"; fi
tu "löscht $UNIT und $TMPFILES" rm -f -- "$UNIT" "$TMPFILES"
if [ -z "$WURZEL" ]; then tu "systemctl daemon-reload" systemctl daemon-reload; fi

echo "== 4. Zustand und Laufzeitordner entfernen (Sperre und Konfiguration bleiben)"
loesche_ordner "$ZUSTAND"
loesche_ordner "$LAUF"
if [ -d "$LOG" ] && [ -z "$(ls -A "$LOG" 2>/dev/null)" ]; then tu "löscht den leeren Ordner $LOG" rmdir -- "$LOG"; fi
if [ -e "$KONFIG/selbstaenderung.aus" ]; then
  echo "  Die Sperre $KONFIG/selbstaenderung.aus bleibt bestehen."
fi
echo "Fertig. Der Anker ist entfernt. Neu einrichten: sudo bash anker-einrichten.sh"
