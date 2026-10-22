#!/usr/bin/env bash
# WAMO-Notanker auf Sokrates-2 einrichten oder aktualisieren (Schritt 0.1 der Selbstüberarbeitung, Konzept
# docs/selbstueberarbeitung.md, Abschnitt G). Wiederholbar. Der Anker wird nur von Hand installiert, nie durch den
# Release-Weg, und liegt außerhalb von /opt/wamo*. WAMO und die Dienstbenutzer können ihn nicht ändern.
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/anker-einrichten.sh --trocken"   # zeigt nur, was geschähe, ändert nichts
#   ssh sokrates2 "sudo bash ~/wamo-infra/anker-einrichten.sh"
#   ssh sokrates2 "wamo-anker status"
#   ssh sokrates2 "sudo python3 -I /usr/local/lib/wamo-anker/anker_rauchtest.py --produktiv"
# Rückweg: anker-rueckweg.sh
#
# Probelauf in einem Wegwerf-Ordner (ohne root, ohne systemd, nichts unter /usr, /etc oder /var):
#   WAMO_ANKER_WURZEL=/tmp/anker-probe bash anker-einrichten.sh
#
# Was das Skript am System ändert (nur WAMO):
#   /usr/local/lib/wamo-anker           root:root 0755: wamo_anker.py, wamo-anker.sh, anker_rauchtest.py, schritte/,
#                                       SHA256SUMS (Prüfsumme aller Dateien; der Dienst prüft sie vor jedem Start)
#                                       Der bisherige Baum liegt bis zum bestandenen Abschluss als wamo-anker.vorher daneben.
#                                       Systemordner wie /usr/local/lib bleiben unverändert (nur die eigenen Ordner).
#   /usr/local/bin/wamo-anker           Verknüpfung auf wamo-anker.sh (Aufruf als wamocon oder root)
#   /etc/wamo-anker                     root:root 0755, ohne Geheimnisse; dort liegt die Sperre selbstaenderung.aus
#   /var/lib/wamo-anker                 root:root 0700: stand.json (letzte gute Stände), Schreibsperre
#   /var/log/wamo-anker                 root:root 0750: audit.jsonl (Hashkette, chattr +a)
#   /etc/tmpfiles.d/wamo-anker.conf     /run/wamo-anker 2750 root:wamocon (Socket anker.sock, 0660)
#   /etc/systemd/system/wamo-anker.service  aktiviert und gestartet
# Erstinstallation: stand.json wird leer angelegt und die Sperre ist GESETZT (Selbständerung aus, fail closed).
# Bei jedem weiteren Lauf bleiben stand.json, Audit-Protokoll und Sperre unverändert.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_ANKER_WURZEL:-}"      # nur für den Probelauf; leer = das echte System
TROCKEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    *) echo "Unbekannte Option: $arg (erlaubt: --trocken)" >&2; exit 2 ;;
  esac
done

LIB="$WURZEL/usr/local/lib/wamo-anker"
BIN="$WURZEL/usr/local/bin/wamo-anker"
KONFIG="$WURZEL/etc/wamo-anker"
ZUSTAND="$WURZEL/var/lib/wamo-anker"
LOG="$WURZEL/var/log/wamo-anker"
UNIT="$WURZEL/etc/systemd/system/wamo-anker.service"
TMPFILES="$WURZEL/etc/tmpfiles.d/wamo-anker.conf"
SOCKET="$WURZEL/run/wamo-anker/anker.sock"
SOCKET_GRUPPE=wamocon
PYTHON=/usr/bin/python3
OWN=(-o root -g root)
if [ -n "$WURZEL" ]; then            # Probelauf: Dateien gehören dem Aufrufer, der Interpreter darf abweichen
  OWN=()
  PYTHON="${WAMO_ANKER_PYTHON:-$PYTHON}"
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

echo "== 0. Prüfungen (nur lesend)"
for datei in anker/wamo_anker.py anker/wamo-anker.sh anker/wamo-anker.service anker_rauchtest.py; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei" >&2; exit 1; }
done
[ -x "$PYTHON" ] || { echo "Fehlt: $PYTHON" >&2; exit 1; }
"$PYTHON" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "Python 3.9 oder neuer nötig." >&2; exit 1; }
for py in anker/wamo_anker.py anker_rauchtest.py; do
  "$PYTHON" -I -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$HERE/$py" \
    || { echo "Syntaxfehler in $py" >&2; exit 1; }
done
if find "$HERE/anker" \( -name '.env*' -o -name 'geheim*' -o -name '*.pem' -o -name '*.key' \) | grep -q .; then
  echo "Im Anker-Ordner liegt eine Geheimnisdatei, Abbruch." >&2; exit 1
fi
if [ -z "$WURZEL" ]; then
  command -v systemctl >/dev/null || { echo "Fehlt: systemctl" >&2; exit 1; }
  getent group "$SOCKET_GRUPPE" >/dev/null || { echo "Gruppe $SOCKET_GRUPPE fehlt." >&2; exit 1; }
fi
ERSTINSTALLATION=0
[ -e "$ZUSTAND/stand.json" ] || ERSTINSTALLATION=1
echo "  ok. Erstinstallation: $([ "$ERSTINSTALLATION" = 1 ] && echo ja || echo nein)"

echo "== 1. Verzeichnisse"
verzeichnisse() {
  # Systemordner wie /usr/local/lib bleiben, wie sie sind (kein Ändern von Besitzer und Rechten); nur die eigenen Ordner
  mkdir -p "$(dirname "$LIB")" "$(dirname "$BIN")" "$(dirname "$KONFIG")" "$(dirname "$ZUSTAND")" "$(dirname "$LOG")"
  install -d "${OWN[@]}" -m 0755 "$KONFIG"
  install -d "${OWN[@]}" -m 0700 "$ZUSTAND"
  install -d "${OWN[@]}" -m 0750 "$LOG"
}
block "legen an: $KONFIG (0755), $ZUSTAND (0700), $LOG (0750)" verzeichnisse

echo "== 2. Programme nach $LIB (neuer Baum, Prüfsumme, dann Austausch)"
programme() {
  local neu f
  neu="$(mktemp -d "$(dirname "$LIB")/.wamo-anker.neu.XXXXXX")"
  install -d "${OWN[@]}" -m 0755 "$neu" "$neu/schritte"
  install "${OWN[@]}" -m 0644 "$HERE/anker/wamo_anker.py" "$neu/wamo_anker.py"
  install "${OWN[@]}" -m 0755 "$HERE/anker/wamo-anker.sh" "$neu/wamo-anker.sh"
  install "${OWN[@]}" -m 0644 "$HERE/anker_rauchtest.py" "$neu/anker_rauchtest.py"
  if [ -d "$HERE/anker/schritte" ]; then   # Schritt-Skripte späterer Stufen (rueckweg-ui, rueckweg-kern)
    for f in "$HERE"/anker/schritte/*; do
      [ -f "$f" ] || continue
      install "${OWN[@]}" -m 0755 "$f" "$neu/schritte/$(basename "$f")"
    done
  fi
  find "$neu" -type f -exec sed -i 's/\r$//' {} +   # Windows-Zeilenenden aus der Ablage entfernen
  ( cd "$neu" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$neu.summen" )
  mv "$neu.summen" "$neu/SHA256SUMS"
  chmod 0644 "$neu/SHA256SUMS"
  if [ -z "$WURZEL" ]; then chown root:root "$neu/SHA256SUMS"; fi
  ( cd "$neu" && sha256sum -c --quiet SHA256SUMS ) || { rm -rf "$neu"; echo "Prüfsumme des neuen Baums stimmt nicht." >&2; exit 1; }
  # Der bisherige Baum bleibt als $LIB.vorher liegen, bis die Prüfung am Ende bestanden ist (Rückfall für den Anker selbst)
  rm -rf "$LIB.vorher"
  if [ -e "$LIB" ]; then mv "$LIB" "$LIB.vorher"; fi
  if ! mv "$neu" "$LIB"; then
    if [ -e "$LIB.vorher" ]; then mv "$LIB.vorher" "$LIB"; fi
    echo "Austausch von $LIB fehlgeschlagen, bisheriger Stand wiederhergestellt." >&2
    exit 1
  fi
  echo "  $(wc -l < "$LIB/SHA256SUMS") Dateien, Prüfsumme geprüft"
}
block "baut $LIB neu aus anker/ und anker_rauchtest.py, schreibt SHA256SUMS, prüft sie, tauscht dann aus" programme

echo "== 3. Aufruf $BIN"
verknuepfung() {
  ln -sfn "$LIB/wamo-anker.sh" "$BIN"
}
block "verknüpft $BIN auf $LIB/wamo-anker.sh" verknuepfung

echo "== 4. Zustand und Sperre"
zustand() {
  if [ "$ERSTINSTALLATION" = 1 ]; then
    ( umask 077 && "$PYTHON" -B -I -c '
import sys
sys.path.insert(0, sys.argv[1])
import wamo_anker as a
a.stand_schreiben(sys.argv[2], a.leerer_stand())' "$LIB" "$ZUSTAND/stand.json" )
    echo "  stand.json leer angelegt: Die Tabelle der letzten guten Stände füllen die Stufen 1 und 2"
    if [ ! -e "$KONFIG/selbstaenderung.aus" ]; then
      printf 'gesperrt seit %s von anker-einrichten\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$KONFIG/selbstaenderung.aus"
      chmod 0644 "$KONFIG/selbstaenderung.aus"
      if [ -z "$WURZEL" ]; then chown root:root "$KONFIG/selbstaenderung.aus"; fi
    fi
    echo "  Sperre gesetzt (Selbständerung aus). Lösen nur bewusst: wamo-anker sperre aus"
  else
    echo "  stand.json und Sperre bleiben unverändert"
  fi
}
block "legt bei der Erstinstallation stand.json (leer, 0600) und die Sperre an, sonst nichts" zustand

echo "== 5. Audit-Protokoll (nur anhängen)"
audit() {
  if [ ! -e "$LOG/audit.jsonl" ]; then
    install "${OWN[@]}" -m 0600 /dev/null "$LOG/audit.jsonl"
  fi
  if [ -z "$WURZEL" ]; then
    if command -v chattr >/dev/null && chattr +a "$LOG/audit.jsonl" 2>/dev/null; then
      echo "  chattr +a gesetzt: das Protokoll lässt sich nur noch anhängen"
    else
      echo "  Hinweis: chattr +a ging nicht (Dateisystem?). Das Protokoll ist dann nicht gegen Löschen durch root geschützt." >&2
    fi
  fi
}
block "legt $LOG/audit.jsonl an (0600, falls es fehlt) und setzt chattr +a" audit

if [ -z "$WURZEL" ]; then
  echo "== 6. systemd-Dienst wamo-anker"
  dienst() {
    mkdir -p "$(dirname "$TMPFILES")" "$(dirname "$UNIT")"
    cat > "$TMPFILES" <<TMP
# WAMO-Notanker: Verzeichnis des Sockets. 2750 root:$SOCKET_GRUPPE: nur root und die Gruppe dürfen den Socket erreichen,
# das setgid-Bit vererbt die Gruppe an anker.sock (0660). Im Programm prüft SO_PEERCRED zusätzlich jeden Aufrufer.
d /run/wamo-anker 2750 root $SOCKET_GRUPPE -
TMP
    chmod 0644 "$TMPFILES"
    systemd-tmpfiles --create "$TMPFILES"
    install -o root -g root -m 0644 "$HERE/anker/wamo-anker.service" "$UNIT"
    sed -i 's/\r$//' "$UNIT"
    systemd-analyze verify "$UNIT" 2>&1 | grep -v '^$' | sed 's/^/  verify: /' || true
    systemctl daemon-reload
    systemctl enable wamo-anker.service >/dev/null 2>&1
    systemctl restart wamo-anker.service
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S "$SOCKET" ] && break; sleep 1; done
    systemctl is-active wamo-anker.service | sed 's/^/  Dienst: /' || true
    if [ -S "$SOCKET" ]; then
      stat -c '  Socket %n %U:%G %a' "$SOCKET"
    else
      echo "Der Socket $SOCKET fehlt. Logs: journalctl -u wamo-anker -n 50" >&2
      exit 1
    fi
  }
  block "legt $TMPFILES und $UNIT an, aktiviert und startet wamo-anker.service" dienst

  echo "== 7. Prüfung"
  if [ "$TROCKEN" = 1 ]; then
    echo "  [trocken] würde 'wamo-anker status' ausführen"
  else
    ( cd "$LIB" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme der Installation stimmt"
    stat -c '  %n %U:%G %a' "$LIB" "$LIB/wamo_anker.py" "$KONFIG" "$ZUSTAND" "$LOG" "$LOG/audit.jsonl"
    if ! "$BIN" status; then
      echo "wamo-anker status meldete einen Fehler. Der bisherige Baum liegt noch in $LIB.vorher." >&2
      echo "Rückfall: sudo rm -rf $LIB && sudo mv $LIB.vorher $LIB && sudo systemctl restart wamo-anker" >&2
      exit 1
    fi
    rm -rf "$LIB.vorher"   # erst jetzt: der neue Stand läuft und besteht die Prüfung
  fi
else
  echo "== 6. Prüfung (Probelauf)"
  if [ "$TROCKEN" = 0 ]; then
    ( cd "$LIB" && sha256sum -c --quiet SHA256SUMS ) && echo "  Prüfsumme stimmt"
    rm -rf "$LIB.vorher"
    find "$WURZEL" -mindepth 1 -printf '  %M %p\n' | sort -k2
  fi
fi
echo "Fertig. Rückweg: sudo bash anker-rueckweg.sh   Rauchtest: sudo python3 -I $LIB/anker_rauchtest.py --produktiv"
