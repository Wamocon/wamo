#!/usr/bin/env bash
# WAMO-Freigabe-Dienst auf Sokrates-2 einrichten oder aktualisieren (Schritt 1.4b der Selbstüberarbeitung, Konzept
# docs/selbstueberarbeitung.md, E.2). Wiederholbar. Voraussetzung: Die Datenbank-Zugänge der Dienste sind eingerichtet
# (scripts/wamo_dienst_db.py einrichten anker, Datei /etc/wamo-anker/db.zugang), denn der Dienst schreibt die Freigaben als Rolle wamo_anker_s2.
#
#   ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh sokrates2 "sudo bash ~/wamo-infra/freigabe-einrichten.sh --trocken"   # zeigt nur, was geschähe, ändert nichts
#   ssh sokrates2 "sudo bash ~/wamo-infra/freigabe-einrichten.sh"
#   ssh sokrates2 "sudo python3 -E -s /opt/wamo-freigabe/app/freigabe_rauchtest.py --produktiv"
# Rückweg: freigabe-rueckweg.sh
#
# Probelauf in einem Wegwerf-Ordner (ohne root, ohne systemd, ohne Benutzer, ohne pip, nichts unter /opt, /etc oder /var):
#   WAMO_FREIGABE_WURZEL=/tmp/freigabe-probe bash freigabe-einrichten.sh
#
# Was das Skript am System ändert (nur WAMO, keine Geheimnisse im Skript):
#   Benutzer und Gruppe wamo-anker       System, Shell /usr/sbin/nologin, kein Home, keine weiteren Gruppen
#   /opt/wamo-freigabe/app               root:root 0755: freigabe_dienst.py, freigabe.py, freigabe_pg.py, wamo_pg.py, der Rauchtest und
#                                        SHA256SUMS (ExecStartPre prüft sie vor jedem Start). Der bisherige Baum liegt bis zum
#                                        bestandenen Abschluss als app.vorher daneben.
#   /opt/wamo-freigabe/pylib             root:root: psycopg, PyJWT, cryptography (pip nur mit Prüfsummen aus pg/requirements-freigabe.lock),
#                                        dazu pylib.SHA256SUMS
#   /etc/wamo-freigabe/db.zugang         root:wamo-anker 0640: Kopie der Zugangsdatei des Ankers (die Datei des Ankers bleibt unverändert)
#   /etc/wamo-freigabe-ausgang/regeln.nft und wamo-freigabe-ausgang.service: nft-Tabelle wamo_freigabe_ausgang, der Benutzer wamo-anker
#                                        erreicht nur 192.168.178.136:55322 und :55443; der Dienst verlangt sie
#   /etc/systemd/system/wamo-freigabe.service  aktiviert und gestartet (Socket /run/wamo-freigabe/freigabe.sock)
# Es wird nichts im Notanker, im Release-Dienst, in wamo-core und in der Oberfläche verändert. Eine Caddy-Route gibt es noch nicht
# (Schritt 1.4c): Der Dienst ist vorerst nur für root erreichbar.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_FREIGABE_WURZEL:-}"    # nur für den Probelauf; leer = das echte System
TROCKEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --trocken)" >&2; exit 2 ;;
  esac
done

BASIS="$WURZEL/opt/wamo-freigabe"
APP="$BASIS/app"
PYLIB="$BASIS/pylib"
KONFIG="$WURZEL/etc/wamo-freigabe"
ZUGANG_QUELLE="$WURZEL/etc/wamo-anker/db.zugang"
AUSGANG_DIR="$WURZEL/etc/wamo-freigabe-ausgang"
AUSGANG_UNIT="$WURZEL/etc/systemd/system/wamo-freigabe-ausgang.service"
UNIT="$WURZEL/etc/systemd/system/wamo-freigabe.service"
SOCKET="$WURZEL/run/wamo-freigabe/freigabe.sock"
TMPFILES="$WURZEL/etc/tmpfiles.d/wamo-freigabe.conf"
BENUTZER=wamo-anker
PYTHON=/usr/bin/python3
MODULE=(freigabe_dienst.py freigabe.py)      # liegen in anker/
PG_MODULE=(freigabe_pg.py wamo_pg.py)        # liegen in pg/ (geteilt mit dem Release-Dienst)
OWN=(-o root -g root)
if [ -n "$WURZEL" ]; then            # Probelauf: Dateien gehören dem Aufrufer, der Interpreter darf abweichen
  OWN=()
  PYTHON="${WAMO_FREIGABE_PYTHON:-$PYTHON}"
fi
umask 022

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten): Der Ordner dieses Skripts, sein Elternordner und
# alles darin müssen dem Aufrufer (bei sudo dem aufrufenden Benutzer) oder root gehören und dürfen weder für die Gruppe noch für alle
# beschreibbar sein. Symbolische Links sind nicht erlaubt.
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
  # Der Probelauf schreibt nur in einen Wegwerf-Ordner und läuft nie als root (sonst könnte eine gesetzte Variable das Skript auf
  # fremde Pfade oder einen fremden Interpreter lenken).
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_FREIGABE_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_FREIGABE_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd, kein Benutzer, kein pip)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

echo "== 0. Prüfungen (nur lesend)"
for datei in "${MODULE[@]/#/anker/}" "${PG_MODULE[@]/#/pg/}" anker/wamo-freigabe.service anker/wamo-freigabe-ausgang.service \
  anker/wamo-freigabe-ausgang.nft pg/requirements-freigabe.lock freigabe_rauchtest.py; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei" >&2; exit 1; }
done
[ -x "$PYTHON" ] || { echo "Fehlt: $PYTHON" >&2; exit 1; }
"$PYTHON" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 oder neuer nötig." >&2; exit 1; }
for py in "${MODULE[@]/#/anker/}" "${PG_MODULE[@]/#/pg/}" freigabe_rauchtest.py; do
  "$PYTHON" -I -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$HERE/$py" \
    || { echo "Syntaxfehler in $py" >&2; exit 1; }
done
if find "$HERE/anker" \( -name '.env*' -o -name 'geheim*' -o -name '*.pem' -o -name '*.key' -o -name '*.env' \) | grep -q .; then
  echo "Im Anker-Ordner liegt eine Geheimnisdatei, Abbruch." >&2; exit 1
fi
if [ -z "$WURZEL" ]; then
  command -v systemctl >/dev/null || { echo "Fehlt: systemctl" >&2; exit 1; }
  command -v nft >/dev/null || { echo "Fehlt: nft (Paket nftables)" >&2; exit 1; }
  "$PYTHON" -I -m pip --version >/dev/null 2>&1 || { echo "Fehlt: pip für $PYTHON (Paket python3-pip)" >&2; exit 1; }
  [ -f "$ZUGANG_QUELLE" ] || { echo "Zugangsdatei $ZUGANG_QUELLE fehlt (scripts/wamo_dienst_db.py einrichten anker), Abbruch." >&2; exit 1; }
  [ -f /etc/wamo-anker/wamo-ca.crt ] || { echo "Zertifikat /etc/wamo-anker/wamo-ca.crt fehlt, Abbruch." >&2; exit 1; }
  echo "  Zugangsdatei $ZUGANG_QUELLE vorhanden (Inhalt wird nicht gelesen)"
fi
echo "  ok."

echo "== 1. Systembenutzer $BENUTZER"
benutzer() {
  if [ -n "$WURZEL" ]; then echo "  (Probelauf: kein Benutzer)"; return 0; fi
  if ! id "$BENUTZER" >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
      --comment "WAMO Freigabe-Dienst" "$BENUTZER"
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
  for d in "$BASIS" "$KONFIG"; do   # root arbeitet hier: keine Links
    if [ -L "$d" ]; then echo "$d ist ein Link, Abbruch." >&2; exit 1; fi
  done
  mkdir -p "$(dirname "$BASIS")" "$(dirname "$KONFIG")"
  install -d "${OWN[@]}" -m 0755 "$BASIS"
  install -d "${OWN[@]}" -m 0755 "$KONFIG"
}
block "legt an: $BASIS (root, 0755), $KONFIG (root, 0755)" verzeichnisse

echo "== 3. Programme nach $APP (neuer Baum, Prüfsumme, dann Austausch)"
programme() {
  local neu m
  rm -rf -- "$BASIS"/.app.neu.*   # Reste eines abgebrochenen Laufs
  neu="$(mktemp -d "$BASIS/.app.neu.XXXXXX")"
  install -d "${OWN[@]}" -m 0755 "$neu"
  for m in "${MODULE[@]}"; do install "${OWN[@]}" -m 0644 "$HERE/anker/$m" "$neu/$m"; done
  for m in "${PG_MODULE[@]}"; do install "${OWN[@]}" -m 0644 "$HERE/pg/$m" "$neu/$m"; done
  install "${OWN[@]}" -m 0644 "$HERE/freigabe_rauchtest.py" "$neu/freigabe_rauchtest.py"
  install "${OWN[@]}" -m 0644 "$HERE/anker/wamo-freigabe-ausgang.nft" "$neu/wamo-freigabe-ausgang.nft"
  find "$neu" -type f -exec sed -i 's/\r$//' {} +   # Windows-Zeilenenden aus der Ablage entfernen
  ( cd "$neu" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$neu.summen" )
  mv "$neu.summen" "$neu/SHA256SUMS"
  chmod 0644 "$neu/SHA256SUMS"
  if [ -z "$WURZEL" ]; then chown root:root "$neu/SHA256SUMS"; fi
  ( cd "$neu" && sha256sum -c --quiet SHA256SUMS ) || { rm -rf "$neu"; echo "Prüfsumme des neuen Baums stimmt nicht." >&2; exit 1; }
  rm -rf "$APP.vorher"
  if [ -e "$APP" ]; then mv "$APP" "$APP.vorher"; fi
  if ! mv "$neu" "$APP"; then
    if [ -e "$APP.vorher" ]; then mv "$APP.vorher" "$APP"; fi
    echo "Austausch von $APP fehlgeschlagen, bisheriger Stand wiederhergestellt." >&2
    exit 1
  fi
  echo "  $(wc -l < "$APP/SHA256SUMS") Dateien, Prüfsumme geprüft"
}
block "baut $APP neu aus anker/, pg/ und freigabe_rauchtest.py, schreibt SHA256SUMS, prüft sie, tauscht dann aus" programme

echo "== 3b. Bibliotheken nach $PYLIB (pip nur mit Prüfsummen aus pg/requirements-freigabe.lock)"
pylib() {
  local neu
  if [ -n "$WURZEL" ]; then echo "  (Probelauf: kein pip, kein Netz)"; return 0; fi
  rm -rf -- "$BASIS"/.pylib.neu.*
  neu="$(mktemp -d "$BASIS/.pylib.neu.XXXXXX")"
  # Saubere Umgebung: keine PIP_*-Einstellungen, kein Benutzerordner. --require-hashes lehnt jede Datei ab, deren Prüfsumme nicht in
  # der Sperrdatei steht; --no-deps, weil die Sperrdatei alles nennt.
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C.UTF-8 HOME=/nonexistent PIP_CONFIG_FILE=/dev/null \
    "$PYTHON" -I -m pip install --no-deps --only-binary=:all: --require-hashes --no-cache-dir --no-compile \
    --disable-pip-version-check --root-user-action=ignore --quiet --target "$neu" -r "$HERE/pg/requirements-freigabe.lock" \
    || { rm -rf "$neu"; echo "pip-Installation aus pg/requirements-freigabe.lock fehlgeschlagen, Abbruch." >&2; exit 1; }
  find "$neu" -name '__pycache__' -prune -exec rm -rf {} +
  find "$neu" -type l -exec rm -f {} +                      # nichts, was aus dem Ordner herauszeigt
  chown -R root:root "$neu"
  chmod -R go-w,a+rX "$neu"
  ( cd "$neu" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$BASIS/.pylib.summen" )
  # Import-Probe mit genau dem Interpreter des Dienstes (-E ignoriert PYTHONPATH, darum der Pfad als Argument). Der Ordner kommt vor die
  # Systempakete wie im Dienst, und die Probe verlangt, dass die Module wirklich daraus kommen (Ubuntu liefert eigene, ältere Fassungen mit).
  "$PYTHON" -E -s -B -c 'import sys; d = sys.argv[1]; i = next((k for k, p in enumerate(sys.path) if p.endswith(("dist-packages", "site-packages"))), len(sys.path)); sys.path.insert(i, d); import psycopg, jwt, cryptography; from cryptography.hazmat.primitives.asymmetric import ec; ec.generate_private_key(ec.SECP256R1()); assert all(m.__file__.startswith(d + "/") for m in (psycopg, jwt, cryptography)), "Modul nicht aus dem gesicherten Ordner"; print("  psycopg", psycopg.__version__, "PyJWT", jwt.__version__, "cryptography", cryptography.__version__)' "$neu" \
    || { rm -rf "$neu" "$BASIS/.pylib.summen"; echo "Bibliotheken lassen sich nicht laden (Architektur?), Abbruch." >&2; exit 1; }
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
  echo "  $(wc -l < "$BASIS/pylib.SHA256SUMS") Dateien, Bibliotheken laden"
}
block "installiert psycopg, PyJWT und cryptography (hash-gesichert) nach $PYLIB, schreibt pylib.SHA256SUMS, prüft den Import, tauscht aus" pylib

echo "== 4. Zugangsdatei $KONFIG/db.zugang (Kopie der Datei des Ankers)"
zugang() {
  if [ -n "$WURZEL" ]; then echo "  (Probelauf: keine Zugangsdatei)"; return 0; fi
  [ ! -L "$KONFIG/db.zugang" ] || { echo "$KONFIG/db.zugang ist ein Link, Abbruch." >&2; exit 1; }
  # Kopie mit Besitzer und Rechten in einem Zug: Der Inhalt wird nie gelesen oder angezeigt
  install -o root -g "$BENUTZER" -m 0640 "$ZUGANG_QUELLE" "$KONFIG/db.zugang"
  stat -c '  %n %U:%G %a' "$KONFIG/db.zugang"
}
block "kopiert $ZUGANG_QUELLE nach $KONFIG/db.zugang (root:$BENUTZER 0640), Inhalt unberührt" zugang

if [ -z "$WURZEL" ]; then
  echo "== 5. systemd-Dienst wamo-freigabe"
  dienst() {
    mkdir -p "$(dirname "$UNIT")"
    # Ausgangsregel zuerst: Der Dienst verlangt sie (Requires), startet ohne sie nicht. Erst prüfen (nft -c), dann laden.
    install -d -o root -g root -m 0755 "$AUSGANG_DIR"
    install -o root -g root -m 0644 "$HERE/anker/wamo-freigabe-ausgang.nft" "$AUSGANG_DIR/regeln.nft"
    sed -i 's/\r$//' "$AUSGANG_DIR/regeln.nft"
    nft -c -f "$AUSGANG_DIR/regeln.nft" || { echo "Die Ausgangsregel ist ungültig (nft -c), Abbruch." >&2; exit 1; }
    install -o root -g root -m 0644 "$HERE/anker/wamo-freigabe-ausgang.service" "$AUSGANG_UNIT"
    sed -i 's/\r$//' "$AUSGANG_UNIT"
    # Laufordner: bleibt über Neustarts bestehen, Gruppe wamocon mit Setgid-Bit (der Socket erbt sie), damit der Container wamo-caddy (1000:1000)
    # den Socket erreicht; ohne diese Gruppe käme nur root hin. Docker braucht den Ordner vor dem Mount (sonst legte er ihn als root an).
    getent group wamocon >/dev/null || { echo "Gruppe wamocon fehlt (Zugang von Caddy zum Socket), Abbruch." >&2; exit 1; }
    mkdir -p "$(dirname "$TMPFILES")"
    cat > "$TMPFILES" <<TMP
# WAMO-Freigabe-Dienst: Ordner des Sockets. 2750 $BENUTZER:wamocon: nur der Dienst schreibt, die Gruppe wamocon (Caddy) erreicht den
# Socket freigabe.sock (0660); das Setgid-Bit vererbt die Gruppe. Im Programm prüft SO_PEERCRED zusätzlich jeden Aufrufer.
d /run/wamo-freigabe 2750 $BENUTZER wamocon -
TMP
    chmod 0644 "$TMPFILES"
    systemd-tmpfiles --create "$TMPFILES"
    install -o root -g root -m 0644 "$HERE/anker/wamo-freigabe.service" "$UNIT"
    sed -i 's/\r$//' "$UNIT"
    systemd-analyze verify "$AUSGANG_UNIT" "$UNIT" 2>&1 | grep -v '^$' | sed 's/^/  verify: /' || true
    systemctl daemon-reload
    systemctl enable wamo-freigabe-ausgang.service wamo-freigabe.service >/dev/null 2>&1
    systemctl restart wamo-freigabe-ausgang.service
    nft list table inet wamo_freigabe_ausgang >/dev/null || { echo "Die Tabelle wamo_freigabe_ausgang fehlt, Abbruch." >&2; exit 1; }
    systemctl restart wamo-freigabe.service
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCKET" ] && break; sleep 1; done
    systemctl is-active wamo-freigabe.service | sed 's/^/  Dienst: /' || true
    if [ -S "$SOCKET" ]; then
      stat -c '  Socket %n %U:%G %a' "$SOCKET"
    else
      echo "Der Socket $SOCKET fehlt. Logs: journalctl -u wamo-freigabe -n 50" >&2
      exit 1
    fi
  }
  block "legt $AUSGANG_DIR/regeln.nft, $AUSGANG_UNIT und $UNIT an, lädt die Ausgangsregel, startet wamo-freigabe.service" dienst

  echo "== 6. Prüfung"
  if [ "$TROCKEN" = 1 ]; then
    echo "  [trocken] würde die Prüfsummen und den Rauchtest ausführen"
  else
    ( cd "$APP" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme der Installation stimmt"
    ( cd "$PYLIB" && sha256sum -c --quiet "$BASIS/pylib.SHA256SUMS" ) && echo "  Prüfsumme der Bibliotheken stimmt"
    echo "  Ausgangsregel: $(nft list table inet wamo_freigabe_ausgang | grep -c 'counter') Regeln geladen"
    stat -c '  %n %U:%G %a' "$APP" "$APP/freigabe_dienst.py" "$PYLIB"
    if ! "$PYTHON" -E -s -B "$APP/freigabe_rauchtest.py" --produktiv; then
      echo "Der Rauchtest meldete einen Fehler. Der bisherige Baum liegt noch in $APP.vorher." >&2
      echo "Rückfall: sudo rm -rf $APP && sudo mv $APP.vorher $APP && sudo systemctl restart wamo-freigabe" >&2
      exit 1
    fi
    rm -rf "$APP.vorher"   # erst jetzt: der neue Stand läuft und besteht die Prüfung
  fi
else
  echo "== 5. Prüfung (Probelauf)"
  if [ "$TROCKEN" = 0 ]; then
    ( cd "$APP" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme stimmt"
    rm -rf "$APP.vorher"
    find "$WURZEL" -mindepth 1 -printf '  %M %p\n' | sort -k2
  fi
fi
echo "Fertig. Rückweg: sudo bash freigabe-rueckweg.sh   Rauchtest: sudo python3 -E -s $APP/freigabe_rauchtest.py --produktiv"
