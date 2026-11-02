#!/usr/bin/env bash
# Rückweg zu release-einrichten.sh: WAMO-Release-Dienst auf Sokrates-2 abschalten (Schritt 0.3). Wiederholbar.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/release-rueckweg.sh"                       # Dienst aus, alle Dateien bleiben
#   ssh sokrates2 "sudo bash ~/wamo-infra/release-rueckweg.sh --entfernen"           # entfernt den Dienst ganz (siehe unten)
#   ssh sokrates2 "sudo bash ~/wamo-infra/release-rueckweg.sh --entfernen --trocken" # zeigt nur, was geschähe
# Probelauf in einem Wegwerf-Ordner: WAMO_RELEASE_WURZEL=/tmp/release-probe bash release-rueckweg.sh --entfernen
#
# Den Dienst abzuschalten gefährdet nichts: Er führt nie selbst etwas aus, alles Schaltende liegt beim Notanker, und der
# Rückweg der Produktion (wamo-anker rueckweg) braucht ihn nicht.
# --entfernen löscht Programme, Treiber (pylib), Ausgangsregel, Unit, Verknüpfung, tmpfiles-Datei, Laufordner und zustand.json. Bewusst NICHT gelöscht,
# sondern gesichert oder behalten:
#   Audit-Protokoll   wird nach /var/backups/wamo-release/audit.<Zeit>.jsonl verschoben (Beweismittel, nie gelöscht)
#   zustand.json      eine Kopie liegt daneben im Sicherungsordner
#   Benutzer wamo-release  bleibt; Entfernen nur bewusst: sudo userdel wamo-release
# Nichts, was andere Teile von WAMO betrifft (Anker, Kern, Gerätedienst), wird angefasst.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_RELEASE_WURZEL:-}"
TROCKEN=0
ENTFERNEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    --entfernen) ENTFERNEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --entfernen, --trocken)" >&2; exit 2 ;;
  esac
done

BASIS="$WURZEL/opt/wamo-release"
APP="$BASIS/app"
BIN="$WURZEL/usr/local/bin/wamo-release"
DATEN="$WURZEL/var/lib/wamo-release"
LOG="$WURZEL/var/log/wamo-release"
SICHERUNG="$WURZEL/var/backups/wamo-release"
UNIT="$WURZEL/etc/systemd/system/wamo-release.service"
TMPFILES="$WURZEL/etc/tmpfiles.d/wamo-release.conf"
LAUF="$WURZEL/run/wamo-release"
PYLIB="$BASIS/pylib"
AUSGANG_DIR="$WURZEL/etc/wamo-release-ausgang"
AUSGANG_UNIT="$WURZEL/etc/systemd/system/wamo-release-ausgang.service"

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

# Löschen nur für die festen Ordner des Release-Dienstes (nie ein leerer Pfad, nie die Wurzel, nie ein Link)
loesche_ordner() {
  case "$1" in
    "$APP"|"$APP.vorher"|"$PYLIB"|"$PYLIB.vorher"|"$DATEN"|"$LAUF"|"$AUSGANG_DIR") ;;
    *) echo "Unerwarteter Pfad zum Löschen: $1" >&2; exit 1 ;;
  esac
  if [ -z "$1" ] || [ "$1" = / ] || [ -L "$1" ]; then echo "Pfad nicht erlaubt: $1" >&2; exit 1; fi
  tu "löscht $1" rm -rf -- "$1"
}

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_herkunft
else
  # Der Probelauf löscht nur in einem Wegwerf-Ordner und läuft nie als root (siehe release-einrichten.sh)
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_RELEASE_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_RELEASE_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

echo "== 1. Dienst aus"
if [ -z "$WURZEL" ]; then
  tu "stoppt und deaktiviert wamo-release.service" systemctl disable --now wamo-release.service || true
  tu "stoppt und deaktiviert die Ausgangsregel (löscht die nft-Tabelle wamo_release_ausgang)" \n    systemctl disable --now wamo-release-ausgang.service || true
  echo "  wamo-release: $(systemctl is-active wamo-release.service 2>/dev/null || true)"
fi
tu "löscht $LAUF/ctl.sock" rm -f -- "$LAUF/ctl.sock"

if [ "$ENTFERNEN" = 0 ]; then
  echo "Fertig. Programme, Zustand und Audit-Protokoll bleiben. Ganz entfernen: --entfernen"
  exit 0
fi

echo "== 2. Zustand und Audit-Protokoll sichern"
sichern() {
  local zeit d
  for d in "$DATEN" "$LOG" "$DATEN/zustand.json" "$LOG/audit.jsonl"; do   # root arbeitet in Ordnern des Dienstes: keine Links
    if [ -L "$d" ]; then echo "$d ist ein Link, Abbruch." >&2; exit 1; fi
  done
  zeit="$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -m 0700 "$SICHERUNG"
  if [ -f "$DATEN/zustand.json" ]; then cp -p "$DATEN/zustand.json" "$SICHERUNG/zustand.$zeit.json"; fi
  if [ -f "$LOG/audit.jsonl" ]; then
    if [ -z "$WURZEL" ] && command -v chattr >/dev/null; then chattr -a "$LOG/audit.jsonl" 2>/dev/null || true; fi
    mv "$LOG/audit.jsonl" "$SICHERUNG/audit.$zeit.jsonl"
    echo "  Audit-Protokoll liegt jetzt in $SICHERUNG/audit.$zeit.jsonl"
  fi
}
if [ "$TROCKEN" = 1 ]; then echo "  [trocken] sichert zustand.json und verschiebt audit.jsonl nach $SICHERUNG"; else sichern; fi

echo "== 3. Programme, Unit und Verknüpfungen entfernen"
if [ -L "$BIN" ]; then tu "löscht die Verknüpfung $BIN" rm -f -- "$BIN"; fi
loesche_ordner "$APP"
if [ -e "$APP.vorher" ]; then loesche_ordner "$APP.vorher"; fi
loesche_ordner "$PYLIB"
if [ -e "$PYLIB.vorher" ]; then loesche_ordner "$PYLIB.vorher"; fi
loesche_ordner "$AUSGANG_DIR"
tu "löscht $UNIT, $AUSGANG_UNIT, $TMPFILES und pylib.SHA256SUMS" rm -f -- "$UNIT" "$AUSGANG_UNIT" "$TMPFILES" "$BASIS/pylib.SHA256SUMS"
if [ -z "$WURZEL" ]; then tu "systemctl daemon-reload" systemctl daemon-reload; fi
if [ -d "$BASIS" ] && [ -z "$(ls -A "$BASIS" 2>/dev/null)" ]; then tu "löscht den leeren Ordner $BASIS" rmdir -- "$BASIS"; fi

echo "== 4. Zustand und Laufordner entfernen"
loesche_ordner "$DATEN"
loesche_ordner "$LAUF"
if [ -d "$LOG" ] && [ -z "$(ls -A "$LOG" 2>/dev/null)" ]; then tu "löscht den leeren Ordner $LOG" rmdir -- "$LOG"; fi
echo "Fertig. Der Release-Dienst ist entfernt (Benutzer wamo-release bleibt). Neu einrichten: sudo bash release-einrichten.sh"
