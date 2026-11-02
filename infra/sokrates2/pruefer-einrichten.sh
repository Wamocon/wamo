#!/usr/bin/env bash
# WAMO-Prüfer-Sandbox auf Sokrates-2 einrichten oder aktualisieren (Schritt 1.2c der Selbstüberarbeitung, Konzept
# docs/selbstueberarbeitung.md, Abschnitt D.3). Wiederholbar. Keine Geheimnisse, keine Verbindung zum Anker, zur Datenbank oder
# zu anderen WAMO-Diensten.
#
#   ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh sokrates2 "sudo bash ~/wamo-infra/pruefer-einrichten.sh --trocken"   # zeigt nur, was geschähe, ändert nichts
#   ssh sokrates2 "sudo bash ~/wamo-infra/pruefer-einrichten.sh"
#   ssh sokrates2 "sudo python3 -E -s /opt/wamo-pruefer/app/pruefer_rauchtest.py"
# Rückweg: pruefer-rueckweg.sh
#
# Optionen:  --trocken          nichts ändern, nur zeigen
#            --werkzeuge-neu    Node, uv, ruff und pip-audit auch dann neu holen, wenn der Stand unverändert ist
#
# Probelauf in einem Wegwerf-Ordner (ohne root, ohne systemd, ohne Benutzer, ohne Netz, nichts unter /opt, /etc, /srv):
#   WAMO_PRUEFER_WURZEL=/tmp/pruefer-probe bash pruefer-einrichten.sh
#
# Was das Skript am System ändert (nur WAMO):
#   Benutzer wamo-pruefer                System, nologin, keine weiteren Gruppen: führt in der Sandbox fremden Code aus
#   Benutzer wamo-pruefer-proxy          System, nologin: der Proxy, geht ins Internet, sieht nur öffentliche Adressen
#   /opt/wamo-pruefer/app                root:root 0755: Läufer (pruefer_lauf.py, pruefer_scans.py, migration_lint.py), Proxy, wamo-pruefer-lauf.sh,
#                                        Rauchtest, SHA256SUMS (ExecStartPre des Proxys prüft sie vor jedem Start)
#   /opt/wamo-pruefer/werkzeuge          root:root: Node (feste Fassung, Prüfsumme festgelegt), uv, ruff und pip-audit (pip nur mit
#                                        Prüfsummen aus pruefer/requirements-werkzeuge.lock), dazu werkzeuge.SHA256SUMS
#   /srv/wamo-pruefer/lauf               root:root 0755: Arbeitsordner der Läufe (je Lauf frisch, danach gelöscht)
#   /usr/local/lib/wamo-pruefer/bwrap    Kopie von /usr/bin/bwrap, root:wamo-pruefer 0750
#   /etc/apparmor.d/wamo-pruefer-bwrap   erlaubt NUR dieser Kopie Benutzer-Namensräume (das System-bwrap bleibt gesperrt)
#   /etc/wamo-pruefer                    regeln.nft (Tabelle wamo_pruefer_ausgang), proxy-pakete.env und proxy-claude.env (Listen und Domainlisten)
#   systemd: wamo-pruefer-ausgang.service, wamo-pruefer-proxy@.service (Instanzen pakete 127.0.0.1:18080 und claude 127.0.0.1:18081),
#            aktiviert und gestartet
# Es wird nichts im Anker, im Release-Dienst, in wamo-core, wamo-geraete, /etc/wamo oder /etc/wamo-anker verändert.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_PRUEFER_WURZEL:-}"    # nur für den Probelauf; leer = das echte System
TROCKEN=0
WERKZEUGE_NEU=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    --werkzeuge-neu) WERKZEUGE_NEU=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --trocken, --werkzeuge-neu)" >&2; exit 2 ;;
  esac
done

BASIS="$WURZEL/opt/wamo-pruefer"
APP="$BASIS/app"
WERK="$BASIS/werkzeuge"
ARBEIT="$WURZEL/srv/wamo-pruefer"
ETC="$WURZEL/etc/wamo-pruefer"
UNIT_DIR="$WURZEL/etc/systemd/system"
BWRAP_KOPIE="$WURZEL/usr/local/lib/wamo-pruefer/bwrap"
APPARMOR="$WURZEL/etc/apparmor.d/wamo-pruefer-bwrap"
SANDBOX=wamo-pruefer
PROXYBENUTZER=wamo-pruefer-proxy
PYTHON=/usr/bin/python3
# Node: feste Fassung (die Oberfläche läuft in der Produktion auf Node 22). Die Prüfsumme steht hier, nicht im Netz: Ein
# Tausch der Datei auf dem Server von nodejs.org würde beim Vergleich auffallen. Aktualisieren: neue Fassung wählen und die
# Zeile aus https://nodejs.org/dist/<fassung>/SHASUMS256.txt (linux-arm64.tar.xz) übernehmen.
NODE_VERSION=v22.23.4
NODE_SHA256=6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b
NODE_URL="https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-arm64.tar.xz"
OWN=(-o root -g root)
if [ -n "$WURZEL" ]; then            # Probelauf: Dateien gehören dem Aufrufer, der Interpreter darf abweichen
  OWN=()
  PYTHON="${WAMO_PRUEFER_PYTHON:-$PYTHON}"
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
  [ "$(id -u)" -ne 0 ] || { echo "Der Probelauf (WAMO_PRUEFER_WURZEL) läuft nicht als root." >&2; exit 1; }
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      echo "WAMO_PRUEFER_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich" \
        "ohne // und ohne . oder .. als Pfadteil sein." >&2
      exit 1 ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd, kein Benutzer, kein Netz)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

PRUEFER_PY=(migration_lint.py pruefer_annahme.py pruefer_archiv.py pruefer_bruecke.py pruefer_client.py pruefer_lauf.py pruefer_proxy.py pruefer_scans.py)
echo "== 0. Prüfungen (nur lesend)"
for datei in "${PRUEFER_PY[@]/#/release/pruefer/}" pruefer/wamo-pruefer-lauf.sh pruefer/wamo-pruefer-ausgang.nft \
  pruefer/wamo-pruefer-ausgang.service "pruefer/wamo-pruefer-proxy@.service" pruefer/wamo-pruefer-annahme.socket "pruefer/wamo-pruefer-annahme@.service" pruefer/wamo-pruefer-bwrap.apparmor \
  pruefer/requirements-werkzeuge.lock pruefer/pruefer.conf.beispiel pruefer_rauchtest.py; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei" >&2; exit 1; }
done
[ -x "$PYTHON" ] || { echo "Fehlt: $PYTHON" >&2; exit 1; }
"$PYTHON" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 oder neuer nötig." >&2; exit 1; }
for py in "${PRUEFER_PY[@]/#/release/pruefer/}" pruefer_rauchtest.py; do
  "$PYTHON" -I -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$HERE/$py" \
    || { echo "Syntaxfehler in $py" >&2; exit 1; }
done
bash -n "$HERE/pruefer/wamo-pruefer-lauf.sh" || { echo "Syntaxfehler in wamo-pruefer-lauf.sh" >&2; exit 1; }
if find "$HERE/pruefer" "$HERE/release/pruefer" \( -name '.env*' -o -name 'geheim*' -o -name '*.pem' -o -name '*.key' -o -name '*.env' \) | grep -q .; then
  echo "Im Prüfer-Ordner liegt eine Geheimnisdatei, Abbruch." >&2; exit 1
fi
if [ -z "$WURZEL" ]; then
  for prog in systemctl systemd-run nft apparmor_parser flock timeout pkill curl tar xz sha256sum cmp; do
    command -v "$prog" >/dev/null || { echo "Fehlt: $prog" >&2; exit 1; }
  done
  [ -x /usr/bin/bwrap ] || { echo "Fehlt: /usr/bin/bwrap (Paket bubblewrap)" >&2; exit 1; }
  "$PYTHON" -I -m pip --version >/dev/null 2>&1 || { echo "Fehlt: pip für $PYTHON (Paket python3-pip)" >&2; exit 1; }
fi
echo "  ok."

echo "== 1. Systembenutzer $SANDBOX und $PROXYBENUTZER"
benutzer() {
  local b gruppen
  if [ -n "$WURZEL" ]; then echo "  (Probelauf: kein Benutzer)"; return 0; fi
  for b in "$SANDBOX" "$PROXYBENUTZER"; do
    if ! id "$b" >/dev/null 2>&1; then
      useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin \
        --comment "WAMO Prüfer-Sandbox ($b)" "$b"
      echo "  $b angelegt"
    fi
    gruppen="$(id -nG "$b")"
    [ "$gruppen" = "$b" ] || { echo "$b ist in weiteren Gruppen ($gruppen), Abbruch." >&2; exit 1; }
    passwd -l "$b" >/dev/null 2>&1 || true
    echo "  $(id "$b")"
  done
}
block "legt die Systembenutzer $SANDBOX und $PROXYBENUTZER an (nologin, keine weiteren Gruppen), falls sie fehlen" benutzer

echo "== 2. Verzeichnisse"
verzeichnisse() {
  local d
  for d in "$BASIS" "$ARBEIT" "$ETC"; do
    if [ -L "$d" ]; then echo "$d ist ein Link, Abbruch." >&2; exit 1; fi
  done
  mkdir -p "$(dirname "$BASIS")" "$(dirname "$ARBEIT")" "$(dirname "$ETC")" "$(dirname "$BWRAP_KOPIE")"
  install -d "${OWN[@]}" -m 0755 "$BASIS" "$ARBEIT" "$ARBEIT/lauf" "$ETC"
  install -d "${OWN[@]}" -m 0700 "$ARBEIT/annahme"   # Zwischenablage des Annahme-Dienstes: nur root
}
block "legt an: $BASIS, $ARBEIT (mit lauf/), $ETC (alle root, 0755)" verzeichnisse

echo "== 3. Programme nach $APP (neuer Baum, Prüfsumme, dann Austausch)"
programme() {
  local neu m
  rm -rf -- "$BASIS"/.app.neu.*   # Reste eines abgebrochenen Laufs
  neu="$(mktemp -d "$BASIS/.app.neu.XXXXXX")"
  install -d "${OWN[@]}" -m 0755 "$neu"
  for m in "${PRUEFER_PY[@]}"; do
    install "${OWN[@]}" -m 0644 "$HERE/release/pruefer/$m" "$neu/$m"
  done
  install "${OWN[@]}" -m 0755 "$HERE/pruefer/wamo-pruefer-lauf.sh" "$neu/wamo-pruefer-lauf.sh"
  install "${OWN[@]}" -m 0644 "$HERE/pruefer/wamo-pruefer-ausgang.nft" "$neu/wamo-pruefer-ausgang.nft"
  install "${OWN[@]}" -m 0644 "$HERE/pruefer_rauchtest.py" "$neu/pruefer_rauchtest.py"
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
block "baut $APP neu aus release/pruefer/ und pruefer/, schreibt SHA256SUMS, prüft sie, tauscht dann aus" programme

echo "== 4. Werkzeuge nach $WERK (Node mit fester Prüfsumme, uv, ruff und pip-audit nur mit Prüfsummen aus der Sperrdatei)"
werkzeuge() {
  local neu stand alt tmp
  if [ -n "$WURZEL" ]; then echo "  (Probelauf: kein Netz, keine Werkzeuge)"; return 0; fi
  stand="$(printf '%s\n%s\n%s\n' "$NODE_VERSION" "$NODE_SHA256" "$(sha256sum "$HERE/pruefer/requirements-werkzeuge.lock" | cut -d' ' -f1)" | sha256sum | cut -d' ' -f1)"
  alt="$(cat "$WERK/.stand" 2>/dev/null || true)"
  if [ "$WERKZEUGE_NEU" = 0 ] && [ "$stand" = "$alt" ] && ( cd "$WERK" && sha256sum -c --quiet "$BASIS/werkzeuge.SHA256SUMS" ) 2>/dev/null; then
    echo "  Werkzeuge unverändert (Stand $stand), nichts zu holen"
    return 0
  fi
  rm -rf -- "$BASIS"/.werk.neu.* "$BASIS"/.werk.dl.*
  neu="$(mktemp -d "$BASIS/.werk.neu.XXXXXX")"
  tmp="$(mktemp -d "$BASIS/.werk.dl.XXXXXX")"
  # Node: Prüfsumme gegen die feste Zeile oben; erst danach wird entpackt
  curl -fsSL --proto '=https' --tlsv1.2 --retry 3 -o "$tmp/node.tar.xz" "$NODE_URL" \
    || { rm -rf "$neu" "$tmp"; echo "Node konnte nicht geladen werden ($NODE_URL), Abbruch." >&2; exit 1; }
  echo "$NODE_SHA256  $tmp/node.tar.xz" | sha256sum -c --quiet \
    || { rm -rf "$neu" "$tmp"; echo "Die Prüfsumme von Node stimmt nicht, Abbruch." >&2; exit 1; }
  install -d -m 0755 "$neu/node"
  tar -xJf "$tmp/node.tar.xz" -C "$neu/node" --strip-components=1 --no-same-owner \
    || { rm -rf "$neu" "$tmp"; echo "Node lässt sich nicht entpacken, Abbruch." >&2; exit 1; }
  rm -rf "$tmp"
  # uv, ruff, pip-audit: saubere Umgebung, nur Räder mit Prüfsumme, keine weiteren Abhängigkeiten außer den gesperrten
  env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C.UTF-8 HOME=/nonexistent PIP_CONFIG_FILE=/dev/null \
    "$PYTHON" -I -m pip install --no-deps --only-binary=:all: --require-hashes --no-cache-dir --no-compile \
    --disable-pip-version-check --root-user-action=ignore --quiet --target "$neu/pylib" -r "$HERE/pruefer/requirements-werkzeuge.lock" \
    || { rm -rf "$neu"; echo "pip-Installation aus requirements-werkzeuge.lock fehlgeschlagen, Abbruch." >&2; exit 1; }
  find "$neu/pylib" -name '__pycache__' -prune -exec rm -rf {} +
  find "$neu/pylib" -type l -exec rm -f {} +                # nichts, was aus dem Ordner herauszeigt
  install -d -m 0755 "$neu/bin"
  ln -s ../pylib/bin/uv "$neu/bin/uv"
  ln -s ../pylib/bin/ruff "$neu/bin/ruff"
  # pip-audit ist ein Python-Programm: kleiner Aufruf, der die gesperrten Pakete (Ordner pylib neben bin) vor die Systempakete stellt
  cat > "$neu/bin/pip-audit" <<'AUDIT'
#!/bin/sh
PYTHONPATH="$(dirname "$0")/../pylib" exec /usr/bin/python3 -s -B -m pip_audit "$@"
AUDIT
  chmod 0755 "$neu/bin/pip-audit"
  chown -R root:root "$neu"
  chmod -R go-w,a+rX "$neu"
  printf '%s\n' "$stand" > "$neu/.stand"
  # Proben mit den Programmen selbst, bevor sie ausgetauscht werden
  [ "$("$neu/node/bin/node" --version)" = "$NODE_VERSION" ] || { rm -rf "$neu"; echo "Node meldet nicht $NODE_VERSION, Abbruch." >&2; exit 1; }
  # npm aus dem Wurzelordner starten: im Ordner des Aufrufers könnte eine .npmrc liegen, die nur stört
  echo "  node $("$neu/node/bin/node" --version), npm $(cd / && env -i PATH="$neu/node/bin:/usr/bin:/bin" HOME=/nonexistent "$neu/node/bin/npm" --version)"
  echo "  $("$neu/bin/uv" --version), $("$neu/bin/ruff" --version)"
  env -i PATH=/usr/bin:/bin "$neu/bin/pip-audit" --version | sed 's/^/  /' \
    || { rm -rf "$neu"; echo "pip-audit lässt sich nicht starten, Abbruch." >&2; exit 1; }
  ( cd "$neu" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$BASIS/.werkzeuge.summen" )
  install -o root -g root -m 0644 "$BASIS/.werkzeuge.summen" "$BASIS/werkzeuge.SHA256SUMS"
  rm -f "$BASIS/.werkzeuge.summen"
  rm -rf "$WERK.vorher"
  if [ -e "$WERK" ]; then mv "$WERK" "$WERK.vorher"; fi
  if ! mv "$neu" "$WERK"; then
    if [ -e "$WERK.vorher" ]; then mv "$WERK.vorher" "$WERK"; fi
    echo "Austausch von $WERK fehlgeschlagen, bisheriger Stand wiederhergestellt." >&2
    exit 1
  fi
  rm -rf "$WERK.vorher"
  echo "  $(wc -l < "$BASIS/werkzeuge.SHA256SUMS") Dateien"
}
block "holt Node $NODE_VERSION (Prüfsumme fest), installiert uv, ruff und pip-audit (hash-gesichert), prüft sie und tauscht dann aus" werkzeuge

if [ -z "$WURZEL" ]; then
  echo "== 5. bwrap-Kopie und AppArmor-Profil (Benutzer-Namensräume nur für diese Kopie)"
  bubblewrap() {
    install -d -o root -g root -m 0755 "$(dirname "$BWRAP_KOPIE")"
    install -o root -g "$SANDBOX" -m 0750 /usr/bin/bwrap "$BWRAP_KOPIE"
    install -o root -g root -m 0644 "$HERE/pruefer/wamo-pruefer-bwrap.apparmor" "$APPARMOR"
    sed -i 's/\r$//' "$APPARMOR"
    apparmor_parser -r -W "$APPARMOR"
    echo "  $(stat -c '%n %U:%G %a' "$BWRAP_KOPIE"), Profil geladen"
  }
  block "kopiert bwrap nach $BWRAP_KOPIE (root:$SANDBOX 0750) und lädt das AppArmor-Profil $APPARMOR" bubblewrap

  echo "== 6. Ausgangsregeln und Proxy"
  dienste() {
    install -d -o root -g root -m 0755 "$ETC"
    install -o root -g root -m 0644 "$HERE/pruefer/wamo-pruefer-ausgang.nft" "$ETC/regeln.nft"
    sed -i 's/\r$//' "$ETC/regeln.nft"
    nft -c -f "$ETC/regeln.nft" || { echo "Die Ausgangsregeln sind ungültig (nft -c), Abbruch." >&2; exit 1; }
    cat > "$ETC/proxy-pakete.env" <<'ENV'
# Proxy "pakete" der Prüfer-Sandbox: Loopback-Adresse und die einzigen erlaubten Namen (wörtlich, ohne Platzhalter).
LISTEN=127.0.0.1:18080
ERLAUBT=registry.npmjs.org,pypi.org,files.pythonhosted.org,github.com,api.github.com
ENV
    chmod 0644 "$ETC/proxy-pakete.env"
    # Proxy "claude": nur die Claude-Schnittstelle. Er gehört zur Claude-Stufe (Review und Refactor, Schritt 3.3), deren Sandbox keinen Zugang zu
    # Paketquellen hat, und umgekehrt: "Code ausführen und Token halten geschieht nie im selben Prozess" (Konzept D.3). Die Sandbox der
    # Prüfstrecke bindet seinen Socket nie ein.
    cat > "$ETC/proxy-claude.env" <<'ENV'
# Proxy "claude" der Prüfer-Sandbox: Loopback-Adresse und der einzige erlaubte Name.
LISTEN=127.0.0.1:18081
ERLAUBT=api.anthropic.com
ENV
    chmod 0644 "$ETC/proxy-claude.env"
    install -o root -g root -m 0644 "$HERE/pruefer/pruefer.conf.beispiel" "$BASIS/pruefer.conf.beispiel"
    install -o root -g root -m 0644 "$HERE/pruefer/wamo-pruefer-ausgang.service" "$UNIT_DIR/wamo-pruefer-ausgang.service"
    install -o root -g root -m 0644 "$HERE/pruefer/wamo-pruefer-proxy@.service" "$UNIT_DIR/wamo-pruefer-proxy@.service"
    install -o root -g root -m 0644 "$HERE/pruefer/wamo-pruefer-annahme.socket" "$UNIT_DIR/wamo-pruefer-annahme.socket"
    install -o root -g root -m 0644 "$HERE/pruefer/wamo-pruefer-annahme@.service" "$UNIT_DIR/wamo-pruefer-annahme@.service"
    sed -i 's/\r$//' "$UNIT_DIR/wamo-pruefer-ausgang.service" "$UNIT_DIR/wamo-pruefer-proxy@.service" "$UNIT_DIR/wamo-pruefer-annahme.socket" "$UNIT_DIR/wamo-pruefer-annahme@.service"
    systemd-analyze verify "$UNIT_DIR/wamo-pruefer-ausgang.service" "$UNIT_DIR/wamo-pruefer-proxy@pakete.service" 2>&1 \
      | grep -v '^$' | sed 's/^/  verify: /' || true
    systemctl daemon-reload
    systemctl enable wamo-pruefer-ausgang.service wamo-pruefer-proxy@pakete.service wamo-pruefer-proxy@claude.service wamo-pruefer-annahme.socket >/dev/null 2>&1
    systemctl restart wamo-pruefer-ausgang.service
    nft list table inet wamo_pruefer_ausgang >/dev/null || { echo "Die Tabelle wamo_pruefer_ausgang fehlt, Abbruch." >&2; exit 1; }
    systemctl restart wamo-pruefer-proxy@pakete.service wamo-pruefer-proxy@claude.service
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      if "$PYTHON" -I -c 'import socket; socket.create_connection(("127.0.0.1", 18080), 1).close(); socket.create_connection(("127.0.0.1", 18081), 1).close()' 2>/dev/null; then break; fi
      sleep 1
    done
    systemctl is-active wamo-pruefer-proxy@pakete.service | sed 's/^/  Proxy pakete: /' || true
    systemctl is-active wamo-pruefer-proxy@claude.service | sed 's/^/  Proxy claude: /' || true
    systemctl restart wamo-pruefer-annahme.socket
    systemctl is-active wamo-pruefer-annahme.socket | sed 's/^/  Annahme-Socket: /' || true
  }
  block "legt $ETC/regeln.nft, $ETC/proxy-pakete.env und die Units an, lädt die Ausgangsregeln und startet den Proxy" dienste

  echo "== 7. Prüfung"
  if [ "$TROCKEN" = 1 ]; then
    echo "  [trocken] würde Prüfsummen, Regeln und den Proxy prüfen"
  else
    ( cd "$APP" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme der Programme stimmt"
    ( cd "$WERK" && sha256sum -c --quiet "$BASIS/werkzeuge.SHA256SUMS" ) && echo "  Prüfsumme der Werkzeuge stimmt"
    echo "  Ausgangsregeln: $(nft list table inet wamo_pruefer_ausgang | grep -c 'counter') Regeln geladen"
    "$PYTHON" -I -c 'import socket; socket.create_connection(("127.0.0.1", 18080), 2).close(); socket.create_connection(("127.0.0.1", 18081), 2).close()' \
      || { echo "Ein Proxy antwortet nicht (18080 pakete, 18081 claude). Logs: journalctl -u wamo-pruefer-proxy@pakete -u wamo-pruefer-proxy@claude -n 50" >&2; exit 1; }
    echo "  Proxys antworten auf 127.0.0.1:18080 (pakete) und 127.0.0.1:18081 (claude)"
    stat -c '  %n %U:%G %a' "$BASIS" "$APP" "$WERK" "$ARBEIT" "$BWRAP_KOPIE"
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
echo "Fertig. Rückweg: sudo bash pruefer-rueckweg.sh   Rauchtest: sudo python3 -E -s $APP/pruefer_rauchtest.py"
