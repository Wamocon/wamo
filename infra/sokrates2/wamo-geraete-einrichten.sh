#!/usr/bin/env bash
# WAMO-Gerätedienst auf Sokrates-2 einrichten oder aktualisieren: Benutzer wamo-geraete, Code und venv unter
# /opt/wamo-geraete (root-eigen), Konfiguration /etc/wamo-geraete/geraete.toml, Zugangsdaten /etc/wamo-geraete/geraete.env,
# SSH-Schlüssel für wamo-status, Ausgangsfilter (systemd und nft), Dienst wamo-geraete. Wiederholbar.
#
# Vom PC aus (Repo-Wurzel); Ablage nur im Home von wamocon, nie in /tmp:
#   scp infra/sokrates2/wamo-geraete-einrichten.sh infra/sokrates2/wamo-geraete-rueckweg.sh \
#       infra/sokrates2/wamo-geraete.service infra/sokrates2/wamo-geraete-egress.service \
#       infra/sokrates2/wamo_geraete_env.py infra/sokrates2/geraete.toml.example sokrates2:wamo-infra/
#   ssh sokrates2 "rm -rf ~/wamo-infra/geraete && mkdir -p ~/wamo-infra/geraete"
#   scp -r geraete/wamo_geraete geraete/pyproject.toml geraete/uv.lock sokrates2:wamo-infra/geraete/
#   ssh sokrates2 "chmod -R go-w ~/wamo-infra && sudo bash ~/wamo-infra/wamo-geraete-einrichten.sh"
# Optionen:
#   --hostschluessel-neu      Hostschlüssel der Sparks neu holen (nach einer Neuinstallation eines Sparks)
#   --fritzbox-zertifikat     nur den SHA-256-Fingerabdruck des FRITZ!Box-Zertifikats (TR-064, 49443) anzeigen
# Rückweg: wamo-geraete-rueckweg.sh
#
# Was das Skript am System ändert (nur WAMO):
#   Benutzer und Gruppe wamo-geraete      System, Shell /usr/sbin/nologin, Home /var/lib/wamo-geraete, keine Gruppen
#   /opt/wamo-geraete                     root:wamo-geraete 0750: app (Code), venv (uv aus uv.lock), schriften
#   /etc/tmpfiles.d/wamo-geraete.conf     /run/wamo-geraete 2750 wamo-geraete:wamo-core (Socket für den Kern)
#   /etc/wamo-geraete/geraete.toml        root:wamo-geraete 0640, aus der Vorlage nur, wenn sie fehlt
#   /etc/wamo-geraete/geraete.env                 root:wamo-geraete 0640, leer angelegt; /usr/local/sbin/wamo-geraete-env
#   /var/lib/wamo-geraete/ssh             Schlüssel für wamo-status auf den Sparks, known_hosts
#   /etc/wamo/geraete-egress.nft          nft-Tabelle inet wamo_geraete_egress (aus geraete.toml erzeugt)
#   /etc/systemd/system/wamo-geraete*.service und wamo-geraete.service.d/ausgang.conf (IPAddressAllow, erzeugt)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/wamo-geraete
BENUTZER=wamo-geraete
KERN=wamo-core
UV_VERSION=0.13.1
UV_SHA256=9041c73c99fdc5892cf128140beb354eca9231d502068cc36e6cb53a8e59ad14   # uv-aarch64-unknown-linux-gnu.tar.gz
PYTHON=/usr/bin/python3.12
KONFIG_ORDNER=/etc/wamo-geraete
KONFIG=$KONFIG_ORDNER/geraete.toml
ENV_DATEI=/etc/wamo-geraete/geraete.env
ZUSTAND=/var/lib/wamo-geraete
NFT=/etc/wamo/geraete-egress.nft

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

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
pruefe_herkunft

if [ "${1:-}" = "--fritzbox-zertifikat" ]; then
  echo | timeout 10 openssl s_client -connect 192.168.178.1:49443 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256 | sed 's/^.*=/tls_sha256 = "/; s/$/"/'
  exit 0
fi
HOSTSCHLUESSEL_NEU=nein
[ "${1:-}" = "--hostschluessel-neu" ] && HOSTSCHLUESSEL_NEU=ja

for datei in wamo-geraete.service wamo-geraete-egress.service wamo_geraete_env.py geraete.toml.example \
  geraete/pyproject.toml geraete/uv.lock geraete/wamo_geraete/__main__.py geraete/wamo_geraete/assets/wmc-logo.png; do
  [ -f "$HERE/$datei" ] || { echo "Fehlt: $HERE/$datei (siehe Kopfkommentar)" >&2; exit 1; }
done
[ -x "$PYTHON" ] || { echo "Fehlt: $PYTHON" >&2; exit 1; }
getent group "$KERN" >/dev/null || { echo "Gruppe $KERN fehlt (zuerst wamo-core-einrichten.sh)" >&2; exit 1; }
mitglieder="$(getent group "$KERN" | cut -d: -f4)"
[ -z "$mitglieder" ] || [ "$mitglieder" = "$KERN" ] || { echo "Gruppe $KERN hat weitere Mitglieder ($mitglieder), Abbruch." >&2; exit 1; }

echo "== 1. Systembenutzer $BENUTZER"
if ! id "$BENUTZER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$ZUSTAND" --no-create-home --shell /usr/sbin/nologin \
    --comment "WAMO Geraetedienst" "$BENUTZER"
  echo "  angelegt"
fi
gruppen="$(id -nG "$BENUTZER")"
[ "$gruppen" = "$BENUTZER" ] || { echo "$BENUTZER ist in weiteren Gruppen ($gruppen), Abbruch." >&2; exit 1; }
passwd -l "$BENUTZER" >/dev/null 2>&1 || true
echo "  $(id "$BENUTZER")"

echo "== 2. Socket-Verzeichnis /run/wamo-geraete (Gruppe $KERN)"
cat > /etc/tmpfiles.d/wamo-geraete.conf <<'TMP'
# WAMO-Gerätedienst: Socket geraete.sock (0660). setgid: der Socket gehört der Gruppe wamo-core, nur der Kern verbindet.
d /run/wamo-geraete 2750 wamo-geraete wamo-core -
TMP
chmod 0644 /etc/tmpfiles.d/wamo-geraete.conf
systemd-tmpfiles --create /etc/tmpfiles.d/wamo-geraete.conf
stat -c '  %n %U:%G %a' /run/wamo-geraete

echo "== 3. uv $UV_VERSION"
install -d -o root -g "$BENUTZER" -m 0750 "$APP"
install -d -o root -g root -m 0755 "$APP/tools" "$APP/schriften"
if [ -x /opt/wamo-core/tools/uv ] && [ "$(/opt/wamo-core/tools/uv --version | awk '{print $2}')" = "$UV_VERSION" ]; then
  install -o root -g root -m 0755 /opt/wamo-core/tools/uv "$APP/tools/uv"
elif [ ! -x "$APP/tools/uv" ] || [ "$("$APP/tools/uv" --version | awk '{print $2}')" != "$UV_VERSION" ]; then
  arbeit="$(mktemp -d "$APP/.uv.XXXXXX")"
  curl -fsSL --proto '=https' --tlsv1.2 -o "$arbeit/uv.tgz" \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-unknown-linux-gnu.tar.gz"
  echo "$UV_SHA256  $arbeit/uv.tgz" | sha256sum -c --quiet -
  tar -xzf "$arbeit/uv.tgz" -C "$arbeit" --strip-components=1 uv-aarch64-unknown-linux-gnu/uv
  install -o root -g root -m 0755 "$arbeit/uv" "$APP/tools/uv"
  rm -rf "$arbeit"
fi
echo "  $("$APP/tools/uv" --version)"

echo "== 4. Code nach $APP/app (root-eigen, ohne Tests)"
neu="$(mktemp -d "$APP/.app.XXXXXX")"
cp -r "$HERE/geraete/wamo_geraete" "$neu/wamo_geraete"
find "$neu" -name '__pycache__' -prune -exec rm -rf {} +
install -o root -g root -m 0644 "$HERE/geraete/pyproject.toml" "$HERE/geraete/uv.lock" "$neu/"
find "$neu" -name '*.py' -exec sed -i 's/\r$//' {} +
chown -R root:root "$neu"
find "$neu" -type d -exec chmod 0755 {} + && find "$neu" -type f -exec chmod 0644 {} +
chmod 0755 "$neu"
[ ! -e "$APP/app" ] || mv "$APP/app" "$APP/.app.vorher.$$"
mv "$neu" "$APP/app"
rm -rf "$APP/.app.vorher.$$"
echo "  $(find "$APP/app" -name '*.py' | wc -l) Python-Dateien"

echo "== 5. venv aus uv.lock"
install -d -o root -g root -m 0700 /var/cache/wamo-geraete-build
( cd "$APP/app" && env -i PATH=/usr/bin:/bin HOME=/root UV_CACHE_DIR=/var/cache/wamo-geraete-build \
    UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT="$APP/venv" UV_NO_PROGRESS=1 \
    "$APP/tools/uv" sync --frozen --no-dev --no-install-project --python "$PYTHON" --quiet )
chown -R root:root "$APP/venv"
chmod -R go-w "$APP/venv"
echo "  $("$APP/venv/bin/python" --version)"
# Liberation Sans Narrow (F18) nur, wenn ein Admin die Dateien bereitstellt (nicht in Ubuntu 24.04 enthalten)
for f in "$HERE"/schriften/LiberationSansNarrow-*.ttf; do
  [ -f "$f" ] && install -o root -g root -m 0644 "$f" "$APP/schriften/"
done
ls "$APP/schriften" | grep -q Narrow && echo "  Schrift: Liberation Sans Narrow" || echo "  Schrift: Ersatz (DejaVu Sans Condensed)"

echo "== 6. Konfiguration und Zugangsdaten"
install -d -o root -g "$BENUTZER" -m 0750 "$KONFIG_ORDNER"
if [ ! -f "$KONFIG" ]; then
  install -o root -g "$BENUTZER" -m 0640 "$HERE/geraete.toml.example" "$KONFIG"
  sed -i 's/\r$//' "$KONFIG"
  echo "  $KONFIG aus der Vorlage angelegt"
fi
chown root:"$BENUTZER" "$KONFIG" && chmod 0640 "$KONFIG"
if [ ! -f "$ENV_DATEI" ]; then
  install -o root -g "$BENUTZER" -m 0640 /dev/null "$ENV_DATEI"
fi
chown root:"$BENUTZER" "$ENV_DATEI" && chmod 0640 "$ENV_DATEI"
install -o root -g root -m 0755 "$HERE/wamo_geraete_env.py" /usr/local/sbin/wamo-geraete-env
sed -i 's/\r$//' /usr/local/sbin/wamo-geraete-env
pruefung=$(cd "$APP/app" && runuser -u "$BENUTZER" -- env -i PATH=/usr/bin:/bin WAMO_GERAETE_KONFIG="$KONFIG" \
  WAMO_GERAETE_ENV="$ENV_DATEI" "$APP/venv/bin/python" -s -m wamo_geraete.ausgang pruefen) \
  || { echo "geraete.toml ist ungültig, Abbruch (Dienst bleibt unverändert)." >&2; exit 1; }
echo "  $pruefung"
/usr/local/sbin/wamo-geraete-env liste | sed 's/^/  /'

echo "== 7. SSH-Schlüssel und Hostschlüssel der Sparks (wamo-status)"
install -d -o "$BENUTZER" -g "$BENUTZER" -m 0700 "$ZUSTAND" "$ZUSTAND/ssh" "$ZUSTAND/kopplung"
if [ ! -f "$ZUSTAND/ssh/id_ed25519" ]; then
  runuser -u "$BENUTZER" -- ssh-keygen -q -t ed25519 -N '' -C "wamo-geraete@sokrates2" -f "$ZUSTAND/ssh/id_ed25519"
fi
echo "  öffentlicher Schlüssel: $(cut -d' ' -f1,2 "$ZUSTAND/ssh/id_ed25519.pub")"
adressen=$(cd "$APP/app" && env -i PATH=/usr/bin:/bin WAMO_GERAETE_KONFIG="$KONFIG" WAMO_GERAETE_ENV=/dev/null \
  "$APP/venv/bin/python" -s -c 'from wamo_geraete.konfig import lade
print(" ".join(g.adresse for g in lade().geraete.values() if g.treiber == "server-ssh" and g.adresse))')
bekannt="$ZUSTAND/ssh/known_hosts"
[ "$HOSTSCHLUESSEL_NEU" = ja ] && rm -f "$bekannt"
touch "$bekannt" && chown "$BENUTZER:$BENUTZER" "$bekannt" && chmod 0600 "$bekannt"
for ip in $adressen; do
  if ! grep -q "^$ip ssh-ed25519 " "$bekannt"; then
    zeile=$(ssh-keyscan -T 5 -t ed25519 "$ip" 2>/dev/null | grep "^$ip ssh-ed25519 " | head -n1 || true)
    if [ -n "$zeile" ]; then
      echo "$zeile" >> "$bekannt"
      echo "  $ip neu: $(ssh-keygen -l -f <(echo "$zeile") | awk '{print $2}') (mit known_hosts des Admin-PCs vergleichen)"
    else
      echo "  $ip: kein Hostschlüssel (Server aus?)"
    fi
  else
    echo "  $ip: $(ssh-keygen -l -f <(grep "^$ip ssh-ed25519 " "$bekannt") | awk '{print $2}')"
  fi
done

echo "== 8. Ausgangsfilter (nft inet wamo_geraete_egress und IPAddressAllow)"
uid=$(id -u "$BENUTZER")
neu=$(mktemp)
( cd "$APP/app" && env -i PATH=/usr/bin:/bin WAMO_GERAETE_KONFIG="$KONFIG" WAMO_GERAETE_ENV=/dev/null \
    "$APP/venv/bin/python" -s -m wamo_geraete.ausgang nft "$uid" ) > "$neu"
nft -c -f "$neu" || { rm -f "$neu"; echo "nft-Regeln ungültig, Abbruch." >&2; exit 1; }
install -o root -g root -m 0644 "$neu" "$NFT"
rm -f "$neu"
install -o root -g root -m 0644 "$HERE/wamo-geraete-egress.service" /etc/systemd/system/wamo-geraete-egress.service
sed -i 's/\r$//' /etc/systemd/system/wamo-geraete-egress.service
install -d -o root -g root -m 0755 /etc/systemd/system/wamo-geraete.service.d
erlauben=$(cd "$APP/app" && env -i PATH=/usr/bin:/bin WAMO_GERAETE_KONFIG="$KONFIG" WAMO_GERAETE_ENV=/dev/null \
  "$APP/venv/bin/python" -s -m wamo_geraete.ausgang erlauben)
[[ $erlauben =~ ^IPAddressAllow=[0-9.\ ]*$ ]] || { echo "IPAddressAllow unerwartet, Abbruch." >&2; exit 1; }
printf '# Erzeugt von wamo-geraete-einrichten.sh aus geraete.toml, nicht von Hand ändern.\n[Service]\n%s\n' "$erlauben" \
  > /etc/systemd/system/wamo-geraete.service.d/ausgang.conf
chmod 0644 /etc/systemd/system/wamo-geraete.service.d/ausgang.conf
echo "  $erlauben"

echo "== 9. Dienst wamo-geraete"
install -o root -g root -m 0644 "$HERE/wamo-geraete.service" /etc/systemd/system/wamo-geraete.service
sed -i 's/\r$//' /etc/systemd/system/wamo-geraete.service
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/wamo-geraete.service /etc/systemd/system/wamo-geraete-egress.service 2>&1 \
  | grep -v '^$' | sed 's/^/  verify: /' || true
systemctl enable wamo-geraete-egress.service wamo-geraete.service >/dev/null 2>&1
if systemctl is-active --quiet wamo-geraete-egress.service; then systemctl reload wamo-geraete-egress.service
else systemctl start wamo-geraete-egress.service; fi
nft list table inet wamo_geraete_egress >/dev/null || { echo "Tabelle inet wamo_geraete_egress fehlt" >&2; exit 1; }
systemctl restart wamo-geraete.service
sleep 3
systemctl is-active wamo-geraete.service | sed 's/^/  Dienst: /'
[ -S /run/wamo-geraete/geraete.sock ] && stat -c '  Socket %n %U:%G %a' /run/wamo-geraete/geraete.sock
journalctl -u wamo-geraete -n 5 --no-pager -o cat | sed 's/^/  /'
echo "Fertig. Schlüssel für die Sparks: $(cut -d' ' -f1,2 "$ZUSTAND/ssh/id_ed25519.pub")"
