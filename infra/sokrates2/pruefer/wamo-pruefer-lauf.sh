#!/usr/bin/env bash
# WAMO-Prüfer-Sandbox auf Sokrates-2 (Konzept docs/selbstueberarbeitung.md D.3, Schritt 1.2c): führt die Prüfstrecke
# (pruefer_lauf.py) oder einen einzelnen Befehl für genau einen Stand in einer bubblewrap-Sandbox aus, als Benutzer
# wamo-pruefer, ohne Geheimnisse und mit Netz nur über den Erlaubnisliste-Proxy. Installiert als
# /opt/wamo-pruefer/app/wamo-pruefer-lauf.sh (root:root 0755), aufgerufen nur als root (später durch den Anker).
#
#   wamo-pruefer-lauf.sh lauf --basis <ordner> --neu <ordner> --runde <1|2|3> --ausgabe <datei.json>
#                             [--fremdverweise <datei>] [--ohne-audit] [--behalten]
#                             (ohne --fremdverweise gilt /etc/wamo-anker/fremdverweise.txt; fehlt sie, bricht der Lauf ab)
#   wamo-pruefer-lauf.sh befehl [--neu <ordner>] [--zeit <s>] [--behalten] -- <befehl> [argumente ...]
#
# Die Ordner werden in einen frischen Arbeitsordner unter /srv/wamo-pruefer/lauf kopiert (Links bleiben Links und werden
# nie verfolgt); die Sandbox arbeitet nur auf der Kopie. Der Ordner verschwindet nach dem Lauf (--behalten hebt ihn auf).
# Pro Lauf frische Zwischenspeicher (kein Paket bleibt zwischen zwei Läufen liegen).
#
# Was die Sandbox sieht (alles andere existiert darin nicht):
#   /usr (nur lesbar, ohne die Ordner der anderen WAMO-Sandboxen), /etc nur aus einer Allowlist (Zertifikate, eigene passwd
#   und group), /proc (eigener PID-Namensraum), /dev (nur die üblichen Geräte), /sys/devices/system/cpu (nur lesbar, CPU-Angaben),
#   /tmp (tmpfs mit Obergrenze),
#   /opt/wamo-pruefer/app und /opt/wamo-pruefer/werkzeuge nur lesbar (Läufer, Node, uv, ruff, pip-audit),
#   /lauf (beschreibbar): wamo (der zu prüfende Stand; der Ordner heißt so, weil der Selbstschutz des Wächters das Repo am Namen erkennt),
#   aus (Ergebnis), home (HOME und Zwischenspeicher)
#   /basis (nur lesbar): der Stand davor, /fremdverweise.txt (nur lesbar)
#   Nicht sichtbar: /etc/wamo*, /home, /root, /srv/wamo, /var, /run, /opt/wamo*, andere Läufe, Geräte, fremde Prozesse.
#
# Netz: eigenes Netz der Sandbox (bwrap --unshare-net): kein Zugang zum Netz des Servers, auch nicht zu dessen Loopback (Datenbank,
#   SSH, Dienste). Das eigene Loopback der Sandbox funktioniert (Werkzeuge wie Turbopack verbinden sich damit mit eigenen Prozessen).
#   Zum Internet führt nur der Unix-Socket des Proxys "pakete" (Domain-Allowlist, DNS macht der Proxy), den pruefer_bruecke.py in der
#   Sandbox als 127.0.0.1:18080 anbietet.
#   * nft-Tabelle wamo_pruefer_ausgang (uid wamo-pruefer): weist im Netz des Servers alles ab (Rückfall, falls je jemand den Benutzer
#     ohne Sandbox startet).
#   * systemd (IPAddressDeny=any, IPAddressAllow=127.0.0.1 ::1) als weitere Schicht in der Zelle des Laufs.
# Grenzen (systemd-run): MemoryMax, MemorySwapMax=0, TasksMax, CPUQuota, RuntimeMaxSec; Dateigröße und offene Dateien begrenzt.
# Höchstens ein Lauf zugleich (Sperre /run/wamo-pruefer.lock).
#
# Was die Sandbox nicht leistet: Sie schützt vor Zugriff auf Geheimnisse und das Innere, nicht vor Rechenzeit, die der geprüfte
# Code verbraucht (Grenzen oben) und nicht vor Fehlurteilen der Werkzeuge. Code der Pakete aus PyPI und npm läuft hier (Build-
# Backends, Tests); darum enthält die Sandbox nichts, was sich zu stehlen lohnt.
set -euo pipefail
umask 077

APP=/opt/wamo-pruefer
ARBEIT=/srv/wamo-pruefer
BWRAP=/usr/local/lib/wamo-pruefer/bwrap
BENUTZER=wamo-pruefer
PROXY_ADRESSE=http://127.0.0.1:18080       # die Brücke in der Sandbox (pruefer_bruecke.py) lauscht dort im eigenen Netz
PROXY_SOCKET=/run/wamo-pruefer-proxy/pakete.sock
KONF=$APP/pruefer.conf
FREMD_STANDARD=/etc/wamo-anker/fremdverweise.txt   # Mustervorlage des Fremdverweis-Greps: liegt im Anker-Ordner, nie im Repo

# Standardwerte; pruefer.conf (root:root 0644) darf sie überschreiben
ZEIT_S=5400
ZEIT_MAX_S=14400
SPEICHER_MAX=24G
CPU_QUOTA=1000%
PROZESSE_MAX=4096
TMP_MB=4096
OFFENE_DATEIEN=16384
DATEI_MAX_MB=4096
KOPIE_MAX_MB=1024
if [ -r "$KONF" ]; then
  if [ "$(stat -c '%u' "$KONF")" != 0 ] || [ $(( 8#$(stat -c '%a' "$KONF") & 8#022 )) -ne 0 ]; then
    echo "$KONF gehört nicht root oder ist beschreibbar, Abbruch." >&2; exit 1
  fi
  # shellcheck disable=SC1090
  . "$KONF"
fi

fehler() { echo "wamo-pruefer-lauf: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fehler "bitte als root ausführen"
id "$BENUTZER" >/dev/null 2>&1 || fehler "Benutzer $BENUTZER fehlt (pruefer-einrichten.sh)"
[ -x /usr/bin/bwrap ] && [ -x /usr/bin/systemd-run ] || fehler "bwrap oder systemd-run fehlt"
[ -d "$APP/app" ] && [ -d "$APP/werkzeuge" ] || fehler "$APP ist nicht eingerichtet"
[ -S "$PROXY_SOCKET" ] || fehler "der Socket des Proxys fehlt: $PROXY_SOCKET (systemctl status wamo-pruefer-proxy@pakete)"

modus="${1:-}"
[ -n "$modus" ] || fehler "Aufruf: lauf ... oder befehl ..."
shift
basis="" neu="" runde="" ausgabe="" fremd="" ohne_audit=0 behalten=0 zeit="" befehl=()
while [ $# -gt 0 ]; do
  case "$1" in
    --basis) basis="${2:?}"; shift 2 ;;
    --neu) neu="${2:?}"; shift 2 ;;
    --runde) runde="${2:?}"; shift 2 ;;
    --ausgabe) ausgabe="${2:?}"; shift 2 ;;
    --fremdverweise) fremd="${2:?}"; shift 2 ;;
    --zeit) zeit="${2:?}"; shift 2 ;;
    --ohne-audit) ohne_audit=1; shift ;;
    --behalten) behalten=1; shift ;;
    --) shift; befehl=("$@"); break ;;
    *) fehler "unbekannte Option: $1" ;;
  esac
done

case "$modus" in
  lauf)
    [ -n "$basis" ] && [ -n "$neu" ] && [ -n "$ausgabe" ] || fehler "lauf braucht --basis, --neu und --ausgabe"
    case "$runde" in 1|2|3) ;; *) fehler "--runde muss 1, 2 oder 3 sein" ;; esac
    [ -z "$zeit" ] || fehler "--zeit gibt es nur beim Befehl"
    ;;
  befehl)
    [ ${#befehl[@]} -gt 0 ] || fehler "befehl braucht ein -- und danach den Befehl"
    [ -z "$basis$runde$ausgabe$fremd" ] || fehler "befehl kennt --basis, --runde, --ausgabe und --fremdverweise nicht"
    ;;
  *) fehler "unbekannter Modus: $modus (lauf oder befehl)" ;;
esac
case "${zeit:-1}" in *[!0-9]*|"") fehler "--zeit muss eine Zahl in Sekunden sein" ;; esac
zeit="${zeit:-$ZEIT_S}"
[ "$zeit" -ge 1 ] && [ "$zeit" -le "$ZEIT_MAX_S" ] || fehler "--zeit außerhalb von 1 bis $ZEIT_MAX_S"

# Quellordner: echte Ordner, keine Links (root kopiert hier, ein untergeschobener Link würde fremde Dateien mitnehmen)
quelle_ok() {
  [ -d "$1" ] && [ ! -L "$1" ] || fehler "kein Ordner oder ein Link: $1"
  local mb
  mb="$(du -sm --apparent-size -- "$1" | cut -f1)"
  [ "$mb" -le "$KOPIE_MAX_MB" ] || fehler "$1 ist mit $mb MB zu groß (Obergrenze $KOPIE_MAX_MB MB)"
}
[ -z "$basis" ] || quelle_ok "$basis"
[ -z "$neu" ] || quelle_ok "$neu"
if [ "$modus" = lauf ]; then
  case "$ausgabe" in /*) ;; *) fehler "--ausgabe muss ein absoluter Pfad sein" ;; esac
  [ ! -L "$ausgabe" ] || fehler "--ausgabe ist ein Link"
  [ -d "$(dirname "$ausgabe")" ] || fehler "Ordner der Ausgabe fehlt"
  if [ -z "$fremd" ]; then
    fremd="$FREMD_STANDARD"
    [ -f "$fremd" ] && [ ! -L "$fremd" ] || fehler "die Mustervorlage fehlt: $fremd (Handarbeit, steht nicht im Repo; siehe Konzept D.3)"
    if [ "$(stat -c '%u' "$fremd")" != 0 ] || [ $(( 8#$(stat -c '%a' "$fremd") & 8#022 )) -ne 0 ]; then
      fehler "$fremd gehört nicht root oder ist beschreibbar"
    fi
  else
    [ -f "$fremd" ] && [ ! -L "$fremd" ] || fehler "--fremdverweise ist keine Datei"
  fi
fi

# Höchstens ein Lauf zugleich
exec 9>/run/wamo-pruefer.lock
flock -n 9 || { echo "wamo-pruefer-lauf: es läuft schon ein Lauf" >&2; exit 75; }

# bwrap: eigene Kopie, der AppArmor allein Benutzer-Namensräume erlaubt (/etc/apparmor.d/wamo-pruefer-bwrap). Wird bei Bedarf
# an das System-bwrap angeglichen (nach Paket-Updates).
install -d -o root -g root -m 0755 /usr/local/lib/wamo-pruefer
if ! cmp -s /usr/bin/bwrap "$BWRAP" 2>/dev/null; then
  install -o root -g "$BENUTZER" -m 0750 /usr/bin/bwrap "$BWRAP"
fi

install -d -o root -g root -m 0755 "$ARBEIT" "$ARBEIT/lauf"
dir="$(mktemp -d "$ARBEIT/lauf/l.XXXXXX")"
chmod 0755 "$dir"
aufraeumen() {
  [ "$behalten" = 1 ] || rm -rf --one-file-system -- "$dir"
}
trap aufraeumen EXIT
install -d -o root -g root -m 0755 "$dir/ro" "$dir/ro/etc"
install -d -o "$BENUTZER" -g "$BENUTZER" -m 0700 "$dir/rw" "$dir/rw/wamo" "$dir/rw/aus" "$dir/rw/home"

kopiere() {   # kopiere <quelle> <ziel> : Inhalt, Links bleiben Links
  cp -a --no-dereference --one-file-system -- "$1/." "$2/"
}
if [ -n "$neu" ]; then
  kopiere "$neu" "$dir/rw/wamo"
  chown -R -h "$BENUTZER:$BENUTZER" "$dir/rw/wamo"
fi
install -d -o root -g root -m 0755 "$dir/ro/basis"
if [ -n "$basis" ]; then
  kopiere "$basis" "$dir/ro/basis"
  chown -R -h root:root "$dir/ro/basis"
  chmod -R go+rX,go-w "$dir/ro/basis"
fi
if [ -n "$fremd" ]; then
  install -o root -g root -m 0644 "$fremd" "$dir/ro/fremdverweise.txt"
else
  install -o root -g root -m 0644 /dev/null "$dir/ro/fremdverweise.txt"
fi
uid="$(id -u "$BENUTZER")"
gid="$(id -g "$BENUTZER")"
printf 'root:x:0:0:root:/nonexistent:/usr/sbin/nologin\n%s:x:%s:%s:pruefer:/lauf/home:/usr/sbin/nologin\n' "$BENUTZER" "$uid" "$gid" > "$dir/ro/etc/passwd"
printf 'root:x:0:\n%s:x:%s:\n' "$BENUTZER" "$gid" > "$dir/ro/etc/group"
chmod 0644 "$dir/ro/etc/passwd" "$dir/ro/etc/group"

bw=("$BWRAP" --unshare-net --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try --die-with-parent --new-session --cap-drop ALL
    --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/sbin /sbin --symlink usr/lib /lib)
[ ! -e /lib64 ] || bw+=(--symlink usr/lib64 /lib64)
bw+=(--tmpfs /usr/local/lib/wamo-pruefer)
[ ! -d /usr/local/lib/wamo ] || bw+=(--tmpfs /usr/local/lib/wamo)
bw+=(--proc /proc --dev /dev --size $((TMP_MB * 1024 * 1024)) --tmpfs /tmp)
[ ! -d /sys/devices/system/cpu ] || bw+=(--ro-bind /sys/devices/system/cpu /sys/devices/system/cpu)   # onnxruntime liest auf ARM die CPU-Daten und bricht sonst beim Import ab
for e in /etc/ssl /etc/ca-certificates /etc/alternatives /etc/ld.so.cache /etc/nsswitch.conf; do
  [ ! -e "$e" ] || bw+=(--ro-bind "$e" "$e")
done
bw+=(--ro-bind "$dir/ro/etc/passwd" /etc/passwd --ro-bind "$dir/ro/etc/group" /etc/group
     --ro-bind "$APP/app" "$APP/app" --ro-bind "$APP/werkzeuge" "$APP/werkzeuge"
     --bind "$dir/rw" /lauf --ro-bind "$dir/ro/basis" /basis --ro-bind "$dir/ro/fremdverweise.txt" /fremdverweise.txt
     --ro-bind "$PROXY_SOCKET" /proxy/pakete.sock
     --remount-ro /
     --chdir /lauf/wamo --clearenv
     --setenv PATH "$APP/werkzeuge/bin:$APP/werkzeuge/node/bin:/usr/local/bin:/usr/bin:/bin"
     --setenv HOME /lauf/home --setenv TMPDIR /tmp --setenv LANG C.UTF-8 --setenv CI 1
     --setenv HTTPS_PROXY "$PROXY_ADRESSE" --setenv https_proxy "$PROXY_ADRESSE"
     --setenv NO_PROXY "" --setenv no_proxy "")

if [ "$modus" = lauf ]; then
  innen=(/usr/bin/python3 -I -B "$APP/app/pruefer_bruecke.py" -- /usr/bin/python3 -I -B "$APP/app/pruefer_lauf.py" --basis /basis --neu /lauf/wamo --runde "$runde"
         --fremdverweise /fremdverweise.txt --ausgabe /lauf/aus/ergebnis.json)
  [ "$ohne_audit" = 0 ] || innen+=(--ohne-audit)
else
  innen=(/usr/bin/python3 -I -B "$APP/app/pruefer_bruecke.py" -- "${befehl[@]}")
fi

einheit="wamo-pruefer-lauf-$(basename "$dir" | tr -c 'A-Za-z0-9\n' '-')"
sd=(/usr/bin/systemd-run --quiet --wait --collect --pipe "--unit=$einheit" "--uid=$BENUTZER" "--gid=$BENUTZER"
    -p "MemoryMax=$SPEICHER_MAX" -p MemorySwapMax=0 -p "TasksMax=$PROZESSE_MAX" -p "CPUQuota=$CPU_QUOTA"
    -p "RuntimeMaxSec=$((zeit + 30))" -p "LimitNOFILE=$OFFENE_DATEIEN" -p LimitCORE=0 -p "LimitFSIZE=${DATEI_MAX_MB}M"
    -p IPAddressDeny=any -p "IPAddressAllow=127.0.0.1 ::1" -p "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK"
    -p CPUWeight=50 -p IOWeight=50)

code=0
timeout --kill-after=20 "$zeit" "${sd[@]}" "${bw[@]}" -- "${innen[@]}" || code=$?
# Reste beseitigen: nach dem Lauf darf kein Prozess des Benutzers übrig sein
systemctl stop "$einheit.service" >/dev/null 2>&1 || true
systemctl reset-failed "$einheit.service" >/dev/null 2>&1 || true
pkill -KILL -u "$BENUTZER" >/dev/null 2>&1 || true

if [ "$modus" = lauf ]; then
  erg="$dir/rw/aus/ergebnis.json"
  if [ -f "$erg" ] && [ ! -L "$erg" ] && [ "$(stat -c '%s' "$erg")" -le 2097152 ] \
     && /usr/bin/python3 -I -c 'import json, sys; d = json.load(open(sys.argv[1], encoding="utf-8")); assert isinstance(d, dict) and "pruefstand" in d' "$erg"; then
    install -o root -g root -m 0600 "$erg" "$ausgabe"
  else
    echo "wamo-pruefer-lauf: kein gültiges Ergebnis (Lauf-Code $code), $ausgabe wird nicht geschrieben" >&2
    [ "$code" -ne 0 ] || code=70
  fi
fi
exit "$code"
