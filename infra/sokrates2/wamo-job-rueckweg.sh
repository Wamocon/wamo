#!/usr/bin/env bash
# Rückweg zu wamo-job-einrichten.sh: stellt die Dateien wieder her, die der letzte ändernde Lauf des Einrichtungsskripts ersetzt hat
# (Auftragsdispatcher, Läufer, Wächter-Kopien, Rauchtests, Managed-Settings). Grundlage ist die neueste Sicherung unter
# /opt/wamo/vorher/<Zeitstempel>/: ihre Dateien kommen zurück, die Dateien aus NEU.txt (gab es vorher nicht, zum Beispiel
# abnahme_befehl.py und wamo_job_pruefen.py des Befehls pruefen) werden entfernt. Wiederholbar: ein zweiter Lauf ändert nichts mehr.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-job-rueckweg.sh"             # Stand vor der letzten Änderung wiederherstellen
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-job-rueckweg.sh --trocken"   # zeigt nur, was geschähe
# Probelauf in einem Wegwerf-Ordner (kein root, kein sudo): WAMO_JOB_WURZEL=/tmp/job-probe bash wamo-job-rueckweg.sh
#
# Der Rückweg berührt nur die Dateien dieser Liste unter /opt/wamo (bin, waechter, claude-code). Nicht berührt werden: die Geheimnisse
# (/etc/wamo/geheim.env, dort bleibt eine Zeile WAMO_PRUEFEN=an stehen, sie wirkt beim alten Dispatcher nicht), wamo-job.conf,
# die Aufträge und Projekte, der Ausgangsfilter, die SSH-Einrichtung und die Sicherungen selbst. Nach dem Rückweg meldet ping
# wieder die vorherige Version.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_JOB_WURZEL:-}"
TROCKEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --trocken)" >&2; exit 2 ;;
  esac
done

APP="$WURZEL/opt/wamo"
ERLAUBT_PFAD='^(bin|waechter|claude-code)/[A-Za-z0-9._-]+$'

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

# tu Beschreibung Befehl...: führt den Befehl aus, im Trockenlauf nur die Beschreibung zeigen
tu() {
  local text="$1"
  shift
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] $text"; else "$@"; fi
}

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_herkunft
else
  # Der Probelauf ändert nur einen Wegwerf-Ordner und läuft nie als root
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_JOB_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_JOB_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

echo "== 1. Neueste Sicherung suchen ($APP/vorher)"
NEUESTE=""
if [ -d "$APP/vorher" ] && [ ! -L "$APP/vorher" ]; then
  while IFS= read -r name; do
    [[ $name =~ ^[0-9]{8}T[0-9]{6}Z$ ]] && [ -d "$APP/vorher/$name" ] && [ ! -L "$APP/vorher/$name" ] && NEUESTE="$APP/vorher/$name"
  done < <(ls -1 "$APP/vorher" | sort)
fi
if [ -z "$NEUESTE" ]; then
  echo "Keine Sicherung unter $APP/vorher gefunden: das Einrichtungsskript hat noch nichts ersetzt, es gibt nichts zurückzunehmen." >&2
  exit 1
fi
echo "  $NEUESTE"

echo "== 2. Gesicherte Dateien wiederherstellen"
anzahl=0
while IFS= read -r -d '' datei; do
  rel="${datei#"$NEUESTE"/}"
  [ "$rel" != NEU.txt ] || continue
  [[ $rel =~ $ERLAUBT_PFAD ]] || { echo "Unerwarteter Pfad in der Sicherung: $rel" >&2; exit 1; }
  # erst daneben kopieren, dann ersetzen: nie eine halbe Datei an Ort und Stelle (Eigentümer und Rechte bleiben erhalten)
  tu "stellt $rel wieder her" cp -p -- "$datei" "$APP/$rel.zurueck"
  tu "ersetzt $rel" mv -f -- "$APP/$rel.zurueck" "$APP/$rel"
  anzahl=$((anzahl + 1))
done < <(find "$NEUESTE" -type f -print0 | sort -z)
echo "  $anzahl Dateien"

echo "== 3. Dateien entfernen, die es vor der Einrichtung nicht gab (NEU.txt)"
entfernt=0
if [ -f "$NEUESTE/NEU.txt" ] && [ ! -L "$NEUESTE/NEU.txt" ]; then
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    [[ $rel =~ $ERLAUBT_PFAD ]] || { echo "Unerwarteter Pfad in NEU.txt: $rel" >&2; exit 1; }
    if [ -e "$APP/$rel" ] || [ -L "$APP/$rel" ]; then
      tu "entfernt $rel" rm -f -- "$APP/$rel"
      entfernt=$((entfernt + 1))
    fi
  done < "$NEUESTE/NEU.txt"
fi
echo "  $entfernt Dateien"

if [ -z "$WURZEL" ] && [ "$TROCKEN" = 0 ]; then
  echo "== 4. Prüfung"
  sudo -u wamo -H "$APP/bin/wamo-job-dispatch" ping || echo "Hinweis: ping scheiterte, bitte prüfen." >&2
fi
echo "Fertig. Stand vor der letzten Änderung ist wieder da. Neu einrichten: sudo bash ~/wamo-infra/wamo-job-einrichten.sh"
