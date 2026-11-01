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
#   /opt/wamo-release/pylib              root:root: psycopg samt libpq (pip nur mit Prüfsummen aus pg/requirements.lock),
#                                        dazu pylib.SHA256SUMS. Der Dienst hängt den Ordner hinten an sys.path an.
#   /etc/wamo-release-ausgang/regeln.nft und wamo-release-ausgang.service: nft-Tabelle wamo_release_ausgang, der Benutzer
#                                        wamo-release erreicht nur 192.168.178.136:55322 (Supavisor); der Dienst verlangt sie
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
PG_MODULE=(release_pg.py wamo_pg.py)   # Datenbankschicht, liegt in pg/ (geteilt mit dem Freigabe-Dienst)
PYLIB="$BASIS/pylib"                   # psycopg, nur aus pg/requirements.lock mit Prüfsummen
AUSGANG_DIR="$WURZEL/etc/wamo-release-ausgang"
AUSGANG_UNIT="$WURZEL/etc/systemd/system/wamo-release-ausgang.service"
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
for datei in "${MODULE[@]/#/release/}" "${PG_MODULE[@]/#/pg/}" release/wamo-release.sh release/wamo-release.service \
  release/wamo-release-ausgang.service release/wamo-release-ausgang.nft pg/requirements.lock release_rauchtest.py; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei" >&2; exit 1; }
done
[ -x "$PYTHON" ] || { echo "Fehlt: $PYTHON" >&2; exit 1; }
"$PYTHON" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 oder neuer nötig." >&2; exit 1; }
for py in "${MODULE[@]/#/release/}" "${PG_MODULE[@]/#/pg/}" release_rauchtest.py; do
  "$PYTHON" -I -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$HERE/$py" \
    || { echo "Syntaxfehler in $py" >&2; exit 1; }
done
if find "$HERE/release" \( -name '.env*' -o -name 'geheim*' -o -name '*.pem' -o -name '*.key' -o -name '*.env' \) | grep -q .; then
  echo "Im Release-Ordner liegt eine Geheimnisdatei, Abbruch." >&2; exit 1
fi
if [ -z "$WURZEL" ]; then
  command -v systemctl >/dev/null || { echo "Fehlt: systemctl" >&2; exit 1; }
  command -v chattr >/dev/null || { echo "Fehlt: chattr (Paket e2fsprogs)" >&2; exit 1; }
  command -v nft >/dev/null || { echo "Fehlt: nft (Paket nftables)" >&2; exit 1; }
  "$PYTHON" -I -m pip --version >/dev/null 2>&1 || { echo "Fehlt: pip für $PYTHON (Paket python3-pip)" >&2; exit 1; }
  if [ -f /etc/wamo-release/db.zugang ]; then
    echo "  Zugangsdatei /etc/wamo-release/db.zugang vorhanden (Inhalt wird nicht gelesen)"
  else
    echo "  Hinweis: /etc/wamo-release/db.zugang fehlt, der Dienst läuft dann ohne Datenbank (scripts/wamo_dienst_db.py einrichten release)"
  fi
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
  local d
  for d in "$BASIS" "$DATEN" "$LOG" "$LOG/audit.jsonl"; do   # root arbeitet hier in Ordnern, die dem Dienst gehören: keine Links
    if [ -L "$d" ]; then echo "$d ist ein Link, Abbruch." >&2; exit 1; fi
  done
  mkdir -p "$(dirname "$BIN")" "$(dirname "$DATEN")" "$(dirname "$LOG")" "$(dirname "$BASIS")"
  install -d "${OWN[@]}" -m 0755 "$BASIS"
  install -d "${DIENST_OWN[@]}" -m 0700 "$DATEN"
  install -d "${DIENST_OWN[@]}" -m 0750 "$LOG"
}
block "legt an: $BASIS (root, 0755), $DATEN (0700), $LOG (0750)" verzeichnisse

echo "== 3. Programme nach $APP (neuer Baum, Prüfsumme, dann Austausch)"
programme() {
  local neu m
  rm -rf -- "$BASIS"/.app.neu.*   # Reste eines abgebrochenen Laufs
  neu="$(mktemp -d "$BASIS/.app.neu.XXXXXX")"
  install -d "${OWN[@]}" -m 0755 "$neu"
  for m in "${MODULE[@]}"; do
    install "${OWN[@]}" -m 0644 "$HERE/release/$m" "$neu/$m"
  done
  for m in "${PG_MODULE[@]}"; do
    install "${OWN[@]}" -m 0644 "$HERE/pg/$m" "$neu/$m"
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
block "baut $APP neu aus release/, pg/ und release_rauchtest.py, schreibt SHA256SUMS, prüft sie, tauscht dann aus" programme

echo "== 3b. Treiber nach $PYLIB (pip nur mit Prüfsummen aus pg/requirements.lock)"
pylib() {
  local neu
  if [ -n "$WURZEL" ]; then echo "  (Probelauf: kein pip, kein Netz)"; return 0; fi
  rm -rf -- "$BASIS"/.pylib.neu.*
  neu="$(mktemp -d "$BASIS/.pylib.neu.XXXXXX")"
  # Saubere Umgebung: keine PIP_*-Einstellungen, kein Benutzerordner. --require-hashes lehnt jede Datei ab, deren Prüfsumme
  # nicht in der Sperrdatei steht (auch eine neuere Fassung desselben Namens); --no-deps, weil die Sperrdatei alles nennt.
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C.UTF-8 HOME=/nonexistent PIP_CONFIG_FILE=/dev/null \
    "$PYTHON" -I -m pip install --no-deps --only-binary=:all: --require-hashes --no-cache-dir --no-compile \
    --disable-pip-version-check --quiet --target "$neu" -r "$HERE/pg/requirements.lock" \
    || { rm -rf "$neu"; echo "pip-Installation aus pg/requirements.lock fehlgeschlagen, Abbruch." >&2; exit 1; }
  find "$neu" -name '__pycache__' -prune -exec rm -rf {} +
  find "$neu" -type l -exec rm -f {} +                      # nichts, was aus dem Ordner herauszeigt
  chown -R root:root "$neu"
  chmod -R go-w,a+rX "$neu"
  ( cd "$neu" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$BASIS/.pylib.summen" )
  # Import-Probe mit genau dem Interpreter des Dienstes: ohne Datenbank, nur ob psycopg samt eingebauter libpq lädt
  PYTHONPATH="$neu" "$PYTHON" -E -s -B -c 'import psycopg; psycopg.pq.version()' \
    || { rm -rf "$neu" "$BASIS/.pylib.summen"; echo "psycopg lässt sich nicht laden (Architektur? libpq?), Abbruch." >&2; exit 1; }
  install -o root -g root -m 0644 "$BASIS/.pylib.summen" "$BASIS/pylib.SHA256SUMS"
  rm -f "$BASIS/.pylib.summen"
  rm -rf "$PYLIB.vorher"
  if [ -e "$PYLIB" ]; then mv "$PYLIB" "$PYLIB.vorher"; fi
  if ! mv "$neu" "$PYLIB"; then
    if [ -e "$PYLIB.vorher" ]; then mv "$PYLIB.vorher" "$PYLIB"; fi
    echo "Austausch von $PYLIB fehlgeschlagen, bisheriger Stand wiederhergestellt." >&2
    exit 1
  fi
  rm -rf "$PYLIB.vorher"
  echo "  $(wc -l < "$BASIS/pylib.SHA256SUMS") Dateien, psycopg lädt"
}
block "installiert psycopg (hash-gesichert) nach $PYLIB, schreibt pylib.SHA256SUMS, prüft den Import, tauscht dann aus" pylib

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
    # fail closed: Ein Protokoll, das der Dienst (oder jemand mit seinem Benutzer) löschen könnte, ist kein Beweismittel
    if chattr +a "$LOG/audit.jsonl" 2>/dev/null; then
      echo "  chattr +a gesetzt: das Protokoll lässt sich nur noch anhängen"
    else
      echo "chattr +a auf $LOG/audit.jsonl ging nicht (Dateisystem?), Abbruch." >&2
      exit 1
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
    # Ausgangsregel zuerst: Der Dienst verlangt sie (Requires), startet ohne sie nicht. Erst prüfen (nft -c), dann laden.
    install -d -o root -g root -m 0755 "$AUSGANG_DIR"
    install -o root -g root -m 0644 "$HERE/release/wamo-release-ausgang.nft" "$AUSGANG_DIR/regeln.nft"
    sed -i 's/\r$//' "$AUSGANG_DIR/regeln.nft"
    nft -c -f "$AUSGANG_DIR/regeln.nft" || { echo "Die Ausgangsregel ist ungültig (nft -c), Abbruch." >&2; exit 1; }
    install -o root -g root -m 0644 "$HERE/release/wamo-release-ausgang.service" "$AUSGANG_UNIT"
    sed -i 's/\r$//' "$AUSGANG_UNIT"
    install -o root -g root -m 0644 "$HERE/release/wamo-release.service" "$UNIT"
    sed -i 's/\r$//' "$UNIT"
    systemd-analyze verify "$AUSGANG_UNIT" "$UNIT" 2>&1 | grep -v '^$' | sed 's/^/  verify: /' || true
    systemctl daemon-reload
    systemctl enable wamo-release-ausgang.service wamo-release.service >/dev/null 2>&1
    systemctl restart wamo-release-ausgang.service
    nft list table inet wamo_release_ausgang >/dev/null || { echo "Die Tabelle wamo_release_ausgang fehlt, Abbruch." >&2; exit 1; }
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
  block "legt $TMPFILES, $AUSGANG_DIR/regeln.nft, $AUSGANG_UNIT und $UNIT an, lädt die Ausgangsregel, startet wamo-release.service" dienst

  echo "== 7. Prüfung"
  if [ "$TROCKEN" = 1 ]; then
    echo "  [trocken] würde 'wamo-release status' ausführen"
  else
    ( cd "$APP" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme der Installation stimmt"
    ( cd "$PYLIB" && sha256sum -c --quiet "$BASIS/pylib.SHA256SUMS" ) && echo "  Prüfsumme der Treiber stimmt"
    echo "  Ausgangsregel: $(nft list table inet wamo_release_ausgang | grep -c 'counter') Regeln geladen"
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
