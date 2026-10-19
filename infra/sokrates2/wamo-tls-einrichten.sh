#!/usr/bin/env bash
# TLS für den WAMO-Supabase auf Sokrates-2: interne CA, HTTPS-Vorbau (Caddy) und TLS am Datenbank-Pooler.
#
# Ausführen auf Sokrates-2 als wamocon (docker-Gruppe, sudo wird nicht gebraucht), vom PC aus. Der Ordner liegt
# im Home von wamocon, nie in /tmp (dort könnte ein anderer Benutzer Dateien vorab anlegen oder austauschen):
#   ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh sokrates2 "bash ~/wamo-infra/wamo-tls-einrichten.sh"            # einrichten oder prüfen
#   ssh sokrates2 "bash ~/wamo-infra/wamo-tls-einrichten.sh --neue-ca"  # CA bewusst neu ausstellen
#   scp sokrates2:/home/wamocon/wamo-tls/ca/wamo-ca.crt infra/sokrates2/wamo-ca.crt   # nach neuer CA
#
# Wiederholbar. Eine vorhandene CA bleibt erhalten (sonst müssten alle PCs neu vertrauen). Passt sie nicht mehr zu
# den Vorgaben unten, bricht das Skript ab; dann bewusst mit --neue-ca neu ausstellen. Zwischenzertifikat (Caddy) und
# Pooler-Zertifikat werden neu ausgestellt, wenn sie fehlen, nicht zur CA passen oder in weniger als 60 Tagen ablaufen.
# Neu erstellt werden nur WAMO-Container, deren Compose-Konfiguration sich tatsächlich geändert hat; alle anderen
# Dienste auf Sokrates-2 bleiben unberührt.
#
# Ergebnis:
#   https://192.168.178.136:55443   API und Anmeldung (auch https://sokrates2:55443 und https://spark-651f.local:55443),
#                                   Container wamo-caddy, leitet weiter an Envoy (http, Port 55321)
#   Supavisor 55322 und 56543       nimmt TLS an, Clients prüfen mit sslmode=verify-full gegen wamo-ca.crt
#   GoTrue                          API_EXTERNAL_URL = https://192.168.178.136:55443/auth/v1 (= Aussteller iss)
#   Envoy und Supavisor             nur über IPv4 veröffentlicht (kein docker-proxy auf [::])
# Rückweg: wamo-tls-rueckweg.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_IP=192.168.178.136
TLS_PORT=55443
STACK=/home/wamocon/supabase-wamo
BASE=/home/wamocon/wamo-tls
CA_DIR="$BASE/ca"             # Root mit Schlüssel, bleibt auf dem Host
CADDY_DIR="$BASE/caddy"
CADDY_CA="$CADDY_DIR/ca"      # im Container /ca: Root-Zertifikat, Zwischenzertifikat und dessen Schlüssel
POOLER_TLS="$STACK/volumes/pooler/tls"
PUBLIC_URL="https://${HOST_IP}:${TLS_PORT}"
CADDY_IMAGE=caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d
CA_DAYS=3650
INTER_DAYS=730
DB_DAYS=397
RENEW_DAYS=60
# Wofür die CA überhaupt Zertifikate ausstellen darf (Name Constraints, gilt auch für das Zwischenzertifikat).
# Ein PC, der der CA vertraut, nimmt von ihr nur Zertifikate für genau diese Adressen und Namen an, und nur für
# Server (serverAuth). Für jede andere Adresse oder Webseite lehnt er ein Zertifikat dieser CA ab, auch wenn
# jemand den CA-Schlüssel hätte.
CA_CONSTRAINTS="critical,permitted;IP:${HOST_IP}/255.255.255.255,permitted;IP:127.0.0.1/255.255.255.255,permitted;DNS:sokrates2,permitted;DNS:spark-651f.local,permitted;DNS:localhost,permitted;DNS:supavisor,permitted;DNS:wamo-supabase-pooler"
# Pooler: nur die LAN-Adresse und die Namen im Docker-Netz (Phase P3)
DB_SAN="IP:${HOST_IP},DNS:supavisor,DNS:wamo-supabase-pooler"
DB_SAN_TEXT="IP Address:${HOST_IP}, DNS:supavisor, DNS:wamo-supabase-pooler"

NEUE_CA=0
case "${1:-}" in
  "") ;;
  --neue-ca) NEUE_CA=1 ;;
  *) echo "Aufruf: $0 [--neue-ca]" >&2; exit 2 ;;
esac

# Schutz gegen untergeschobene Dateien: Der Ordner dieses Skripts, sein Elternordner und alles darin müssen dem
# Aufrufer (bei sudo dem aufrufenden Benutzer) oder root gehören und dürfen weder für die Gruppe noch für alle
# beschreibbar sein. Symbolische Links sind nicht erlaubt.
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

[ "$(id -un)" = wamocon ] || { echo "Bitte als wamocon ausführen (nicht mit sudo)." >&2; exit 1; }
pruefe_herkunft
[ -f "$STACK/.env" ] || { echo "$STACK/.env fehlt, zuerst supabase-wamo-einrichten.sh ausführen." >&2; exit 1; }
for f in wamo-caddy/Caddyfile wamo-caddy/docker-compose.yml supabase-wamo/docker-compose.wamo-tls.yml; do
  [ -f "$HERE/$f" ] || { echo "$HERE/$f fehlt (ganzen Ordner infra/sokrates2 kopieren)." >&2; exit 1; }
done
umask 077
mkdir -p "$BASE"
chmod 700 "$BASE"
TMP="$(mktemp -d "$BASE/.arbeit.XXXXXX")"  # im geschützten Ordner, damit kein Schlüssel nach /tmp gelangt
trap 'rm -rf "$TMP"' EXIT

set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*$|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
erweiterungen() { openssl x509 -in "$1" -noout -ext basicConstraints,keyUsage,extendedKeyUsage,nameConstraints 2>/dev/null || true; }
schluessel_passt() { [ "$(openssl x509 -in "$1" -noout -pubkey | sha256sum)" = "$(openssl pkey -in "$2" -pubout | sha256sum)" ]; }
neuer_schluessel() { openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1" 2>/dev/null; }

# Dienste des Compose-Projekts im aktuellen Ordner, deren laufender Container nicht zur Konfiguration passt
geaenderte_dienste() {
  local svc hash cid cur
  docker compose config --hash '*' | while read -r svc hash; do
    cid="$(docker compose ps -q "$svc" 2>/dev/null | head -n1)"
    cur=""
    [ -n "$cid" ] && cur="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$cid")"
    [ "$cur" = "$hash" ] || printf '%s ' "$svc"
  done
}

warte_gesund() {
  local i offen
  for i in $(seq 1 60); do
    offen="$(docker compose ps --format '{{.Name}} {{.Health}} {{.State}}' | grep -vE ' (healthy|) running$' || true)"
    [ -z "$offen" ] && return 0
    sleep 3
  done
  echo "Noch nicht gesund:" >&2; echo "$offen" >&2; return 1
}

echo "== 1. Interne CA ($CA_DIR)"
mkdir -p "$CA_DIR"
chmod 700 "$CA_DIR"
cat > "$TMP/ca.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
C = DE
O = WAMOCON GmbH
CN = WAMO interne CA
[v3_ca]
basicConstraints = critical,CA:TRUE,pathlen:1
keyUsage = critical,keyCertSign,cRLSign
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
nameConstraints = ${CA_CONSTRAINTS}
EOF
ca_da=0
[ -f "$CA_DIR/wamo-ca.key" ] && [ -f "$CA_DIR/wamo-ca.crt" ] && ca_da=1
ca_neu=0
if [ "$ca_da" = 1 ] && [ "$NEUE_CA" = 0 ]; then
  neuer_schluessel "$TMP/probe.key"
  openssl req -x509 -new -config "$TMP/ca.cnf" -key "$TMP/probe.key" -days 1 -out "$TMP/probe.crt"
  if [ "$(erweiterungen "$CA_DIR/wamo-ca.crt")" != "$(erweiterungen "$TMP/probe.crt")" ]; then
    echo "Die vorhandene CA entspricht nicht den Vorgaben (Name Constraints, Verwendungszweck). Mit --neue-ca neu" \
      "ausstellen; danach muss jeder PC die neue wamo-ca.crt importieren." >&2
    exit 1
  fi
  schluessel_passt "$CA_DIR/wamo-ca.crt" "$CA_DIR/wamo-ca.key" || { echo "CA-Schlüssel passt nicht zum Zertifikat." >&2; exit 1; }
  echo "  vorhandene CA bleibt"
else
  mkdir "$TMP/ca"
  neuer_schluessel "$TMP/ca/wamo-ca.key"
  openssl req -x509 -new -config "$TMP/ca.cnf" -key "$TMP/ca/wamo-ca.key" -sha256 -days "$CA_DAYS" -out "$TMP/ca/wamo-ca.crt"
  chmod 600 "$TMP/ca/wamo-ca.key"
  chmod 644 "$TMP/ca/wamo-ca.crt"
  if [ "$ca_da" = 1 ]; then
    alt="$(openssl x509 -in "$CA_DIR/wamo-ca.crt" -noout -serial | cut -d= -f2)"
    cp -p "$CA_DIR/wamo-ca.crt" "$CA_DIR/abgeloest-$alt.crt"  # nur der öffentliche Teil, zum Nachsehen
    shred -u "$CA_DIR/wamo-ca.key"
    echo "  alte CA (Seriennummer $alt) ersetzt, ihr Schlüssel ist gelöscht"
  fi
  mv -f "$TMP/ca/wamo-ca.key" "$CA_DIR/wamo-ca.key"
  mv -f "$TMP/ca/wamo-ca.crt" "$CA_DIR/wamo-ca.crt"
  ca_neu=1
  echo "  neue CA erzeugt"
fi
chmod 600 "$CA_DIR/wamo-ca.key"
chmod 644 "$CA_DIR/wamo-ca.crt"
openssl x509 -in "$CA_DIR/wamo-ca.crt" -noout -subject -serial -enddate -fingerprint -sha256 | sed 's/^/  /'
openssl x509 -in "$CA_DIR/wamo-ca.crt" -noout -fingerprint -sha1 | sed 's/^/  /'

echo "== 2. Zwischenzertifikat für Caddy ($CADDY_CA)"
mkdir -p "$CADDY_DIR" "$CADDY_CA"
chmod 755 "$CADDY_DIR"
chmod 700 "$CADDY_CA"
zwischen_ok() {
  local crt="$CADDY_CA/wamo-intermediate.crt" key="$CADDY_CA/wamo-intermediate.key"
  [ -f "$crt" ] && [ -f "$key" ] || return 1
  cmp -s "$CA_DIR/wamo-ca.crt" "$CADDY_CA/wamo-ca.crt" || return 1
  openssl verify -CAfile "$CA_DIR/wamo-ca.crt" "$crt" >/dev/null 2>&1 || return 1
  openssl x509 -in "$crt" -noout -checkend $((RENEW_DAYS * 86400)) >/dev/null || return 1
  schluessel_passt "$crt" "$key"
}
zwischen_neu=0
if zwischen_ok; then
  echo "  vorhandenes Zwischenzertifikat ist gültig und passt"
else
  cat > "$TMP/zwischen.cnf" <<EOF
[ext]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
nameConstraints = ${CA_CONSTRAINTS}
EOF
  neuer_schluessel "$TMP/wamo-intermediate.key"
  openssl req -new -key "$TMP/wamo-intermediate.key" -subj "/C=DE/O=WAMOCON GmbH/CN=WAMO interne CA Caddy" \
    -out "$TMP/zwischen.csr"
  openssl x509 -req -in "$TMP/zwischen.csr" -CA "$CA_DIR/wamo-ca.crt" -CAkey "$CA_DIR/wamo-ca.key" \
    -set_serial "0x$(openssl rand -hex 16)" -days "$INTER_DAYS" -sha256 -extfile "$TMP/zwischen.cnf" -extensions ext \
    -out "$TMP/wamo-intermediate.crt" 2>/dev/null
  install -m 644 "$CA_DIR/wamo-ca.crt" "$CADDY_CA/wamo-ca.crt"
  install -m 600 "$TMP/wamo-intermediate.key" "$CADDY_CA/wamo-intermediate.key"
  install -m 644 "$TMP/wamo-intermediate.crt" "$CADDY_CA/wamo-intermediate.crt"
  zwischen_ok || { echo "Neues Zwischenzertifikat ist ungültig, Abbruch." >&2; exit 1; }
  zwischen_neu=1
  echo "  neues Zwischenzertifikat ausgestellt"
fi
rm -f "$CADDY_CA/wamo-ca.key"  # Der Root-Schlüssel gehört nie in den Container-Ordner
openssl x509 -in "$CADDY_CA/wamo-intermediate.crt" -noout -subject -enddate | sed 's/^/  /'

echo "== 3. Zertifikat für den Datenbank-Pooler ($POOLER_TLS)"
mkdir -p "$POOLER_TLS"
chmod 700 "$POOLER_TLS"
pooler_ok() {
  local crt="$POOLER_TLS/server.crt" key="$POOLER_TLS/server.key"
  [ -f "$crt" ] && [ -f "$key" ] || return 1
  openssl verify -CAfile "$CA_DIR/wamo-ca.crt" "$crt" >/dev/null 2>&1 || return 1
  openssl x509 -in "$crt" -noout -checkend $((RENEW_DAYS * 86400)) >/dev/null || return 1
  schluessel_passt "$crt" "$key" || return 1
  [ "$(openssl x509 -in "$crt" -noout -ext subjectAltName | tail -n +2 | xargs)" = "$DB_SAN_TEXT" ]
}
pooler_neu=0
if pooler_ok; then
  echo "  vorhandenes Zertifikat ist gültig und passt"
else
  cat > "$TMP/pooler.cnf" <<EOF
[ext]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = ${DB_SAN}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF
  openssl req -new -newkey rsa:2048 -nodes -keyout "$TMP/server.key" -subj "/O=WAMOCON GmbH/CN=wamo-supabase-pooler" \
    -out "$TMP/server.csr" 2>/dev/null
  openssl x509 -req -in "$TMP/server.csr" -CA "$CA_DIR/wamo-ca.crt" -CAkey "$CA_DIR/wamo-ca.key" \
    -set_serial "0x$(openssl rand -hex 16)" -days "$DB_DAYS" -sha256 -extfile "$TMP/pooler.cnf" -extensions ext \
    -out "$TMP/server.crt" 2>/dev/null
  install -m 600 "$TMP/server.key" "$POOLER_TLS/server.key"
  install -m 644 "$TMP/server.crt" "$POOLER_TLS/server.crt"
  pooler_ok || { echo "Neues Pooler-Zertifikat ist ungültig, Abbruch." >&2; exit 1; }
  pooler_neu=1
  echo "  neues Zertifikat ausgestellt"
fi
openssl x509 -in "$POOLER_TLS/server.crt" -noout -enddate -ext subjectAltName | sed 's/^/  /'

echo "== 4. HTTPS-Vorbau wamo-caddy ($CADDY_DIR)"
mkdir -p "$CADDY_DIR/etc" "$CADDY_DIR/data" "$CADDY_DIR/config"
chmod 700 "$CADDY_DIR/data" "$CADDY_DIR/config"
chmod 755 "$CADDY_DIR/etc"
alt_id="$(docker ps -q -f name=^wamo-caddy$)"
if [ -z "$alt_id" ] && ss -tlnH "sport = :$TLS_PORT" | grep -q .; then
  echo "Port $TLS_PORT ist schon belegt, Abbruch." >&2; exit 1
fi
# Caddyfile zuerst als Kopie prüfen, erst dann installieren
mkdir "$TMP/etc"
install -m 644 "$HERE/wamo-caddy/Caddyfile" "$TMP/etc/Caddyfile"
chmod 755 "$TMP/etc"
docker run --rm -v "$TMP/etc:/etc/caddy:ro" "$CADDY_IMAGE" caddy adapt --config /etc/caddy/Caddyfile >/dev/null
install -m 644 "$TMP/etc/Caddyfile" "$CADDY_DIR/etc/Caddyfile"
install -m 644 "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_DIR/docker-compose.yml"
if [ "$zwischen_neu" = 1 ]; then
  # Zertifikate der alten Kette nicht weiterverwenden (Caddy hält sie auch im Speicher, daher Neustart)
  rm -rf "$CADDY_DIR/data/caddy/certificates/local" "$CADDY_DIR/data/caddy/pki"
  ( cd "$CADDY_DIR" && docker compose up -d --force-recreate )
else
  ( cd "$CADDY_DIR" && docker compose up -d )
fi
neu_id="$(docker ps -q -f name=^wamo-caddy$)"
if [ -n "$alt_id" ] && [ "$alt_id" = "$neu_id" ]; then
  docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  echo "  Caddyfile neu geladen"
fi
for i in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 --cacert "$CA_DIR/wamo-ca.crt" "$PUBLIC_URL/auth/v1/health" || true)"
  [ "$code" != "000" ] && break
  sleep 2
done
[ "$code" != "000" ] || { echo "HTTPS auf $PUBLIC_URL antwortet nicht." >&2; docker logs --tail 20 wamo-caddy >&2; exit 1; }
echo "  $PUBLIC_URL antwortet (HTTP $code ohne Schlüssel)"

echo "== 5. WAMO-Stack auf HTTPS, Pooler-TLS und IPv4-Ports umstellen ($STACK)"
cd "$STACK"
# Sicherung nur für die Dauer dieses Laufs; nach erfolgreicher Prüfung wird sie gelöscht (enthält Geheimnisse)
[ -f .env.vor-tls ] || { cp -p .env .env.vor-tls; chmod 600 .env.vor-tls; }
compose_file="$(grep -m1 '^COMPOSE_FILE=' .env | cut -d= -f2- || true)"
compose_file="${compose_file:-docker-compose.yml}"
# Ergänzung vorab gegen die übrigen Compose-Dateien prüfen
pruef_args=()
while IFS= read -r f; do
  [ -n "$f" ] && [ "$f" != docker-compose.wamo-tls.yml ] && pruef_args+=(-f "$f")
done < <(tr ':' '\n' <<<"$compose_file")
docker compose "${pruef_args[@]}" -f "$HERE/supabase-wamo/docker-compose.wamo-tls.yml" config -q
install -m 644 "$HERE/supabase-wamo/docker-compose.wamo-tls.yml" docker-compose.wamo-tls.yml
case ":$compose_file:" in
  *:docker-compose.wamo-tls.yml:*) ;;
  *) set_env COMPOSE_FILE "${compose_file}:docker-compose.wamo-tls.yml" ;;
esac
set_env SUPABASE_PUBLIC_URL "$PUBLIC_URL"
set_env API_EXTERNAL_URL "$PUBLIC_URL/auth/v1"
chmod 600 .env
geaendert="$(geaenderte_dienste)"
if [ -n "${geaendert// /}" ]; then
  echo "  neu erstellt werden: $geaendert"
  # shellcheck disable=SC2086
  docker compose up -d --no-deps $geaendert
else
  echo "  Konfiguration unverändert, kein Container wird neu erstellt"
fi
if [ "$pooler_neu" = 1 ] && [[ " $geaendert " != *" supavisor "* ]]; then
  docker compose restart supavisor
  echo "  Pooler neu gestartet (neues Zertifikat)"
fi
warte_gesund

echo "== 6. Prüfung"
apikey="$(grep -m1 '^SUPABASE_PUBLISHABLE_KEY=' .env | cut -d= -f2-)"
curl -s -o /dev/null -w "  Auth-Gesundheit über HTTPS: HTTP %{http_code}\n" --cacert "$CA_DIR/wamo-ca.crt" \
  -H "apikey: $apikey" "$PUBLIC_URL/auth/v1/health"
curl -s --cacert "$CA_DIR/wamo-ca.crt" "$PUBLIC_URL/auth/v1/.well-known/jwks.json" \
  | python3 -c 'import json,sys; print("  JWKS über HTTPS:", len(json.load(sys.stdin).get("keys", [])), "Schlüssel")'
echo "  Token-Aussteller (iss): $(docker exec wamo-supabase-auth printenv GOTRUE_JWT_ISSUER)"
for ziel in "ip $HOST_IP" "name sokrates2" "name spark-651f.local"; do
  set -- $ziel
  if [ "$1" = ip ]; then pruef=(-verify_ip "$2"); else pruef=(-servername "$2" -verify_hostname "$2"); fi
  ergebnis="$(openssl s_client -connect "${HOST_IP}:${TLS_PORT}" -CAfile "$CA_DIR/wamo-ca.crt" "${pruef[@]}" \
    -verify_return_error -brief </dev/null 2>&1 || true)"
  grep -q '^Verification: OK' <<<"$ergebnis" || { echo "  HTTPS für $2: Prüfung FEHLGESCHLAGEN" >&2; echo "$ergebnis" | head -5 >&2; exit 1; }
  echo "  HTTPS für $2: Kette und Name geprüft"
done
for port in 55322 56543; do
  ergebnis="$(openssl s_client -starttls postgres -connect "${HOST_IP}:${port}" -CAfile "$CA_DIR/wamo-ca.crt" \
    -verify_ip "$HOST_IP" -verify_return_error -brief </dev/null 2>&1 || true)"
  if grep -q '^Verification: OK' <<<"$ergebnis"; then
    echo "  Pooler $port: TLS geprüft ($(grep -m1 '^Protocol version' <<<"$ergebnis" | cut -d: -f2 | xargs))"
  else
    echo "  Pooler $port: TLS-Prüfung FEHLGESCHLAGEN" >&2; echo "$ergebnis" | head -5 >&2; exit 1
  fi
done
ipv6="$(ss -tlnH '( sport = :55321 or sport = :55322 or sport = :56543 or sport = :55443 )' | awk '{print $4}' | grep -F '[' || true)"
[ -z "$ipv6" ] || { echo "  WAMO-Ports lauschen noch auf IPv6: $ipv6" >&2; exit 1; }
echo "  WAMO-Ports nur über IPv4 veröffentlicht"
rm -f .env.vor-tls
echo "  Sicherung .env.vor-tls gelöscht"
if [ "$ca_neu" = 1 ]; then
  echo "NEUE CA: wamo-ca.crt ins Repo kopieren und auf jedem PC neu importieren (alte vorher entfernen)."
fi
echo "Fertig. Öffentliches Root-Zertifikat: $CA_DIR/wamo-ca.crt (im Repo infra/sokrates2/wamo-ca.crt)."
