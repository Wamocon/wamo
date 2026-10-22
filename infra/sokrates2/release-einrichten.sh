#!/usr/bin/env bash
# WAMO-Release-Dienst auf Sokrates-2 einrichten oder aktualisieren (Schritt 0.3 der Selbstüberarbeitung, Konzept
# docs/selbstueberarbeitung.md, Abschnitt D). Wiederholbar. Voraussetzung: Der Notanker ist eingerichtet
# (anker-einrichten.sh), denn der Dienst spricht nur über dessen Socket mit dem System und hängt per systemd an ihm.
#
#   ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh sokrates2 "sudo bash ~/wamo-infra/release-einrichten.sh --trocken"   # zeigt nur, was geschähe, ändert nichts
#   ssh sokrates2 "sudo bash ~/wamo-infra/release-einrichten.sh"
#   ssh sokrates2 "sudo wamo-release status"
#   ssh sokrates2 "sudo python3 -E -s /opt/wamo-release/app/release_rauchtest.py --produktiv"
# Rückweg: release-rueckweg.sh
#
# Probelauf in einem Wegwerf-Ordner (ohne root, ohne systemd, ohne Benutzer, nichts unter /opt, /etc oder /var):
#   WAMO_RELEASE_WURZEL=/tmp/release-probe bash release-einrichten.sh
#
# Was das Skript am System ändert (nur WAMO, keine Geheimnisse):
#   Benutzer und Gruppe wamo-release     System, Shell /usr/sbin/nologin, Home /var/lib/wamo-release, keine weiteren Gruppen
#   /opt/wamo-release/app                root:root 0755: die Python-Module des Dienstes, wamo-release.sh,
#                                        release_rauchtest.py und SHA256SUMS (Prüfsumme aller Dateien; ExecStartPre prüft sie
#                                        vor jedem Start). Der bisherige Baum liegt bis zum bestandenen Abschluss als app.vorher daneben.
#   /usr/local/bin/wamo-release          Verknüpfung auf wamo-release.sh (Aufruf als root: sudo wamo-release status)
#   /var/lib/wamo-release                wamo-release 0700: zustand.json (legt der Dienst an)
#   /var/log/wamo-release                wamo-release 0750: audit.jsonl (Hashkette, chattr +a)
#   /etc/tmpfiles.d/wamo-release.conf    /run/wamo-release 2750 wamo-release:wamo-core (Socket ctl.sock, 0660)
#   /etc/systemd/system/wamo-release.service  aktiviert und gestartet
# Es wird nichts im Anker, in den Diensten wamo-core und wamo-geraete, in /etc/wamo oder /etc/wamo-anker verändert.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_RELEASE_WURZEL:-}"    # nur für den Probelauf; leer = das echte System
TROCKEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --trocken)" >&2; exit 2 ;;
  esac
done

BASIS="$WURZEL/opt/wamo-release"
APP="$BASIS/app"
BIN="$WURZEL/usr/local/bin/wamo-release"
DATEN="$WURZEL/var/lib/wamo-release"
LOG="$WURZEL/var/log/wamo-release"
UNIT="$WURZEL/etc/systemd/system/wamo-release.service"
TMPFILES="$WURZEL/etc/tmpfiles.d/wamo-release.conf"
SOCKET="$WURZEL/run/wamo-release/ctl.sock"
BENUTZER=wamo-release
KERN=wamo-core
PYTHON=/usr/bin/python3
MODULE=(release_anker.py release_automat.py release_db.py release_kette.py release_stufen.py wamo_release.py)
OWN=(-o root -g root)
DIENST_OWN=(-o "$BENUTZER" -g "$BENUTZER")
if [ -n "$WURZEL" ]; then            # Probelauf: Dateien gehören dem Aufrufer, der Interpreter darf abweichen
  OWN=()
  DIENST_OWN=()
  PYTHON="${WAMO_RELEASE_PYTHON:-$PYTHON}"
fi
umask 022

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten): Der Ordner dieses Skripts, sein
# Elternordner und alles darin müssen dem Aufrufer (bei sudo dem aufrufenden Benutzer) oder root gehören und dürfen
# weder für die Gruppe noch für alle beschreibbar sein. Symbolische Links sind nicht erlaubt.
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

# block "Beschreibung" funktion: führt die Funktion aus, im Trockenlauf nur die Beschreibung zeigen
block() {
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] $1"; else "$2"; fi
}

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_herkunft
else
  # Der Probelauf schreibt nur in einen Wegwerf-Ordner und läuft nie als root (sonst könnte eine gesetzte Variable das
  # Skript auf fremde Pfade oder einen fremden Interpreter lenken).
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_RELEASE_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_RELEASE_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd, kein Benutzer)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

echo "== 0. Prüfungen (nur lesend)"
for datei in "${MODULE[@]/#/release/}" release/wamo-release.sh release/wamo-release.service release_rauchtest.py; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei" >&2; exit 1; }
done
[ -x "$PYTHON" ] || { echo "Fehlt: $PYTHON" >&2; exit 1; }
"$PYTHON" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 oder neuer nötig." >&2; exit 1; }
for py in "${MODULE[@]/#/release/}" release_rauchtest.py; do
  "$PYTHON" -I -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$HERE/$py" \
    || { echo "Syntaxfehler in $py" >&2; exit 1; }
done
if find "$HERE/release" \( -name '.env*' -o -name 'geheim*' -o -name '*.pem' -o -name '*.key' -o -name '*.env' \) | grep -q .; then
  echo "Im Release-Ordner liegt eine Geheimnisdatei, Abbruch." >&2; exit 1
fi
if [ -z "$WURZEL" ]; then
  command -v systemctl >/dev/null || { echo "Fehlt: systemctl" >&2; exit 1; }
  command -v chattr >/dev/null || { echo "Fehlt: chattr (Paket e2fsprogs)" >&2; exit 1; }
  [ -f /etc/systemd/system/wamo-anker.service ] || { echo "Der Notanker ist nicht eingerichtet (zuerst anker-einrichten.sh)." >&2; exit 1; }
  getent group wamocon >/dev/null || { echo "Gruppe wamocon fehlt (Zugang zum Socket des Ankers)." >&2; exit 1; }
  getent group "$KERN" >/dev/null || { echo "Gruppe $KERN fehlt (zuerst wamo-core-einrichten.sh)." >&2; exit 1; }
  mitglieder="$(getent group "$KERN" | cut -d: -f4)"
  [ -z "$mitglieder" ] || [ "$mitglieder" = "$KERN" ] || { echo "Gruppe $KERN hat weitere Mitglieder ($mitglieder), Abbruch." >&2; exit 1; }
fi
echo "  ok."

echo "== 1. Systembenutzer $BENUTZER"
benutzer() {
  if [ -n "$WURZEL" ]; then echo "  (Probelauf: kein Benutzer)"; return 0; fi
  if ! id "$BENUTZER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/wamo-release --no-create-home --shell /usr/sbin/nologin \
      --comment "WAMO Release-Dienst" "$BENUTZER"
    echo "  angelegt"
  fi
  local gruppen
  gruppen="$(id -nG "$BENUTZER")"
  [ "$gruppen" = "$BENUTZER" ] || { echo "$BENUTZER ist in weiteren Gruppen ($gruppen), Abbruch." >&2; exit 1; }
  passwd -l "$BENUTZER" >/dev/null 2>&1 || true
  echo "  $(id "$BENUTZER")"
}
block "legt den Systembenutzer $BENUTZER an (nologin, keine weiteren Gruppen), falls er fehlt" benutzer

echo "== 2. Verzeichnisse"
verzeichnisse() {
  mkdir -p "$(dirname "$BIN")" "$(dirname "$DATEN")" "$(dirname "$LOG")" "$(dirname "$BASIS")"
  install -d "${OWN[@]}" -m 0755 "$BASIS"
  install -d "${DIENST_OWN[@]}" -m 0700 "$DATEN"
  install -d "${DIENST_OWN[@]}" -m 0750 "$LOG"
}
block "legt an: $BASIS (root, 0755), $DATEN (0700), $LOG (0750)" verzeichnisse

echo "== 3. Programme nach $APP (neuer Baum, Prüfsumme, dann Austausch)"
programme() {
  local neu m
  neu="$(mktemp -d "$BASIS/.app.neu.XXXXXX")"
  install -d "${OWN[@]}" -m 0755 "$neu"
  for m in "${MODULE[@]}"; do
    install "${OWN[@]}" -m 0644 "$HERE/release/$m" "$neu/$m"
  done
  install "${OWN[@]}" -m 0755 "$HERE/release/wamo-release.sh" "$neu/wamo-release.sh"
  install "${OWN[@]}" -m 0644 "$HERE/release_rauchtest.py" "$neu/release_rauchtest.py"
  find "$neu" -type f -exec sed -i 's/\r$//' {} +   # Windows-Zeilenenden aus der Ablage entfernen
  ( cd "$neu" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$neu.summen" )
  mv "$neu.summen" "$neu/SHA256SUMS"
  chmod 0644 "$neu/SHA256SUMS"
  if [ -z "$WURZEL" ]; then chown root:root "$neu/SHA256SUMS"; fi
  ( cd "$neu" && sha256sum -c --quiet SHA256SUMS ) || { rm -rf "$neu"; echo "Prüfsumme des neuen Baums stimmt nicht." >&2; exit 1; }
  # Der bisherige Baum bleibt als app.vorher liegen, bis die Prüfung am Ende bestanden ist (Rückfall)
  rm -rf "$APP.vorher"
  if [ -e "$APP" ]; then mv "$APP" "$APP.vorher"; fi
  if ! mv "$neu" "$APP"; then
    if [ -e "$APP.vorher" ]; then mv "$APP.vorher" "$APP"; fi
    echo "Austausch von $APP fehlgeschlagen, bisheriger Stand wiederhergestellt." >&2
    exit 1
  fi
  echo "  $(wc -l < "$APP/SHA256SUMS") Dateien, Prüfsumme geprüft"
}
block "baut $APP neu aus release/ und release_rauchtest.py, schreibt SHA256SUMS, prüft sie, tauscht dann aus" programme

echo "== 4. Aufruf $BIN"
verknuepfung() {
  ln -sfn "$APP/wamo-release.sh" "$BIN"
}
block "verknüpft $BIN auf $APP/wamo-release.sh" verknuepfung

echo "== 5. Audit-Protokoll (nur anhängen)"
audit() {
  if [ ! -e "$LOG/audit.jsonl" ]; then
    install "${DIENST_OWN[@]}" -m 0600 /dev/null "$LOG/audit.jsonl"
  fi
  if [ -z "$WURZEL" ]; then
    if chattr +a "$LOG/audit.jsonl" 2>/dev/null; then
      echo "  chattr +a gesetzt: das Protokoll lässt sich nur noch anhängen"
    else
      echo "  Hinweis: chattr +a ging nicht (Dateisystem?). Das Protokoll ist dann nicht gegen Löschen geschützt." >&2
    fi
  fi
}
block "legt $LOG/audit.jsonl an (0600, falls es fehlt) und setzt chattr +a" audit

if [ -z "$WURZEL" ]; then
  echo "== 6. systemd-Dienst wamo-release"
  dienst() {
    mkdir -p "$(dirname "$TMPFILES")" "$(dirname "$UNIT")"
    cat > "$TMPFILES" <<TMP
# WAMO-Release-Dienst: Verzeichnis des Sockets. 2750 $BENUTZER:$KERN: nur der Dienst und der Kern erreichen den Socket
# ctl.sock (0660); das setgid-Bit vererbt die Gruppe. Im Programm prüft SO_PEERCRED zusätzlich jeden Aufrufer.
d /run/wamo-release 2750 $BENUTZER $KERN -
TMP
    chmod 0644 "$TMPFILES"
    systemd-tmpfiles --create "$TMPFILES"
    install -o root -g root -m 0644 "$HERE/release/wamo-release.service" "$UNIT"
    sed -i 's/\r$//' "$UNIT"
    systemd-analyze verify "$UNIT" 2>&1 | grep -v '^$' | sed 's/^/  verify: /' || true
    systemctl daemon-reload
    systemctl enable wamo-release.service >/dev/null 2>&1
    systemctl restart wamo-release.service
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCKET" ] && break; sleep 1; done
    systemctl is-active wamo-release.service | sed 's/^/  Dienst: /' || true
    if [ -S "$SOCKET" ]; then
      stat -c '  Socket %n %U:%G %a' "$SOCKET"
    else
      echo "Der Socket $SOCKET fehlt. Logs: journalctl -u wamo-release -n 50" >&2
      exit 1
    fi
  }
  block "legt $TMPFILES und $UNIT an, aktiviert und startet wamo-release.service" dienst

  echo "== 7. Prüfung"
  if [ "$TROCKEN" = 1 ]; then
    echo "  [trocken] würde 'wamo-release status' ausführen"
  else
    ( cd "$APP" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme der Installation stimmt"
    stat -c '  %n %U:%G %a' "$APP" "$APP/wamo_release.py" "$DATEN" "$LOG" "$LOG/audit.jsonl"
    if ! "$BIN" status; then
      echo "wamo-release status meldete einen Fehler. Der bisherige Baum liegt noch in $APP.vorher." >&2
      echo "Rückfall: sudo rm -rf $APP && sudo mv $APP.vorher $APP && sudo systemctl restart wamo-release" >&2
      exit 1
    fi
    rm -rf "$APP.vorher"   # erst jetzt: der neue Stand läuft und besteht die Prüfung
  fi
else
  echo "== 6. Prüfung (Probelauf)"
  if [ "$TROCKEN" = 0 ]; then
    ( cd "$APP" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme stimmt"
    rm -rf "$APP.vorher"
    find "$WURZEL" -mindepth 1 -printf '  %M %p\n' | sort -k2
  fi
fi
echo "Fertig. Rückweg: sudo bash release-rueckweg.sh   Rauchtest: sudo python3 -E -s $APP/release_rauchtest.py --produktiv"
