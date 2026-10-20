#!/usr/bin/env bash
# Eigener Supabase-Stack fuer WAMO auf Sokrates-2 (getrennt von allen anderen Diensten).
#
# Ausfuehren auf Sokrates-2 als wamocon (Mitglied der docker-Gruppe, kein sudo noetig):
#   SRC=<Ordner eines offiziellen Supabase-Self-Hosting-Stacks> bash supabase-wamo-einrichten.sh
#
# Vorlage ist der vorhandene offizielle Self-Hosting-Stack im Ordner SRC
# (gleiche Versionen, Images liegen schon lokal). Kopiert werden nur Konfigurationsdateien, nie Daten
# oder Geheimnisse. Getrennt werden: Compose-Projektname, Containernamen, Docker-Volumes, Ports,
# Geheimnisse und Datenordner. Die Registrierung ist geschlossen: Konten legt nur der Admin an
# (scripts/wamo_nutzer.py).
#
# Ports:  55321 API und Auth (Envoy) | 55322 Postgres ueber Supavisor (Session) | 56543 Supavisor (Transaktion)
# Danach: wamo-tls-einrichten.sh (HTTPS 55443, eigene CA, Pooler-TLS) und mit sudo wamo-firewall-einrichten.sh.
#
# Sitzungsgrenzen von GoTrue (Schritt 3b, bei jedem Lauf): Zeitbox SITZUNG_ZEITBOX (Standard 168h, wie
# WAMO_ANMELDUNG_MAX_STUNDEN im Voice-Core) und Inaktivitaet SITZUNG_INAKTIV (Standard 72h) als SESSIONS_TIMEBOX und
# SESSIONS_INACTIVITY_TIMEOUT in der .env, an den Auth-Container ueber supabase-wamo/docker-compose.wamo-sitzungen.yml
# (GOTRUE_SESSIONS_TIMEBOX, GOTRUE_SESSIONS_INACTIVITY_TIMEOUT; Namen geprueft gegen gotrue v2.196.0).
# Nur diese Grenzen fuer einen laufenden Stack setzen (ohne SRC; erstellt nur den Auth-Container neu):
#   NUR_SITZUNGSGRENZEN=1 bash supabase-wamo-einrichten.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITZUNG_ZEITBOX="${SITZUNG_ZEITBOX:-168h}"
SITZUNG_INAKTIV="${SITZUNG_INAKTIV:-72h}"
NUR_SITZUNGSGRENZEN="${NUR_SITZUNGSGRENZEN:-0}"

SRC="${SRC:-}"
DST="${DST:-/home/wamocon/supabase-wamo}"
HOST_IP="${HOST_IP:-192.168.178.136}"
API_PORT="${API_PORT:-55321}"
PG_PORT="${PG_PORT:-55322}"
TX_PORT="${TX_PORT:-56543}"
SUBNET="${SUBNET:-172.23.0.0/16}"

if [ "$NUR_SITZUNGSGRENZEN" = 1 ]; then
  [ -f "$DST/.env" ] || { echo "$DST/.env fehlt: zuerst den Stack einrichten (mit SRC)." >&2; exit 1; }
else
  [ -n "$SRC" ] && [ -f "$SRC/docker-compose.yml" ] || { echo "Vorlage fehlt: SRC auf den Ordner eines offiziellen Supabase-Self-Hosting-Stacks setzen." >&2; exit 1; }
fi

# Wert in der .env des Stacks setzen (im aktuellen Ordner; Werte nie ausgeben)
set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*$|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }

if [ ! -f "$DST/.env" ]; then
  echo "== 1. Konfiguration kopieren (ohne Daten und Geheimnisse)"
  mkdir -p "$DST"
  ( cd "$SRC" && tar --exclude='./.env' --exclude='./.env.old' --exclude='./volumes/db/data' -cf - . ) | ( cd "$DST" && tar -xf - )

  echo "== 2. Compose-Projekt trennen"
  cd "$DST"
  sed -i -E 's/^name: supabase$/name: supabase-wamo/' docker-compose.yml
  sed -i -E 's/container_name: supabase-/container_name: wamo-supabase-/' docker-compose.yml
  # Envoy erreicht Realtime ueber diesen Hostnamen; im eigenen Netz als Alias statt Containername
  sed -i -E 's/^(    )container_name: realtime-dev\.supabase-realtime$/\1container_name: wamo-supabase-realtime\n\1networks:\n\1  default:\n\1    aliases:\n\1      - realtime-dev.supabase-realtime/' docker-compose.yml
  # Die Vorlage legt das Docker-Subnetz fest (172.21.0.0/16, auf Sokrates-2 bereits belegt)
  sed -i -E "s|^(        - subnet: )172\.21\.0\.0/16$|\1${SUBNET}|" docker-compose.yml
  rm -f docker-compose.yml.old
  grep -q '^name: supabase-wamo$' docker-compose.yml || { echo "Projektname nicht gesetzt, Abbruch." >&2; exit 1; }
  grep -q "subnet: ${SUBNET}" docker-compose.yml || { echo "Subnetz nicht gesetzt, Abbruch." >&2; exit 1; }
  if grep -nE 'container_name: (supabase-|realtime-dev\.)' docker-compose.yml; then
    echo "Containernamen kollidieren noch mit der Vorlage, Abbruch." >&2; exit 1
  fi

  echo "== 3. .env mit neuen Geheimnissen"
  cp .env.example .env
  chmod 600 .env
  sh utils/generate-keys.sh --update-env >/dev/null 2>&1
  sh utils/add-new-auth-keys.sh --update-env >/dev/null 2>&1
  rm -f .env.old
  set_env SUPABASE_PUBLIC_URL "http://${HOST_IP}:${API_PORT}"
  set_env API_EXTERNAL_URL "http://${HOST_IP}:${API_PORT}/auth/v1"
  set_env SITE_URL "http://localhost:3000"
  set_env ADDITIONAL_REDIRECT_URLS "http://127.0.0.1:3000"
  set_env KONG_HTTP_PORT "$API_PORT"
  set_env API_GW_HTTP_PORT "$API_PORT"
  set_env POSTGRES_PORT "$PG_PORT"
  set_env POOLER_PROXY_PORT_TRANSACTION "$TX_PORT"
  set_env POOLER_TENANT_ID "wamo"
  set_env DISABLE_SIGNUP "true"
  set_env ENABLE_ANONYMOUS_USERS "false"
  set_env ENABLE_PHONE_SIGNUP "false"
  set_env STUDIO_DEFAULT_ORGANIZATION "WAMOCON"
  set_env STUDIO_DEFAULT_PROJECT "WAMO"
  chmod 600 .env
else
  echo "== $DST/.env existiert, Kopie und Schluesselerzeugung werden uebersprungen"
  cd "$DST"
fi

echo "== 3b. Sitzungsgrenzen von GoTrue (Zeitbox $SITZUNG_ZEITBOX, Inaktivitaet $SITZUNG_INAKTIV)"
for dauer in "$SITZUNG_ZEITBOX" "$SITZUNG_INAKTIV"; do
  [[ "$dauer" =~ ^[1-9][0-9]{0,3}h$ ]] || { echo "Ungueltige Dauer $dauer (Form 168h, 1h bis 9999h)." >&2; exit 1; }
done
set_env SESSIONS_TIMEBOX "$SITZUNG_ZEITBOX"
set_env SESSIONS_INACTIVITY_TIMEOUT "$SITZUNG_INAKTIV"
compose_file="$(grep -m1 '^COMPOSE_FILE=' .env | cut -d= -f2- || true)"
compose_file="${compose_file:-docker-compose.yml}"
pruef_args=()
while IFS= read -r f; do
  [ -n "$f" ] && [ "$f" != docker-compose.wamo-sitzungen.yml ] && pruef_args+=(-f "$f")
done < <(tr ':' '\n' <<<"$compose_file")
# Ergaenzung vorab gegen die uebrigen Compose-Dateien pruefen (-q: ohne Ausgabe, also ohne Werte)
docker compose "${pruef_args[@]}" -f "$HERE/supabase-wamo/docker-compose.wamo-sitzungen.yml" config -q
install -m 644 "$HERE/supabase-wamo/docker-compose.wamo-sitzungen.yml" docker-compose.wamo-sitzungen.yml
case ":$compose_file:" in
  *:docker-compose.wamo-sitzungen.yml:*) ;;
  *) set_env COMPOSE_FILE "${compose_file}:docker-compose.wamo-sitzungen.yml" ;;
esac
chmod 600 .env
if [ "$NUR_SITZUNGSGRENZEN" = 1 ]; then
  # Nur der Auth-Container wird neu erstellt; alle anderen Dienste laufen weiter
  docker compose up -d --no-deps auth
  for i in $(seq 1 30); do
    zustand="$(docker inspect --format '{{.State.Health.Status}}' wamo-supabase-auth 2>/dev/null || true)"
    [ "$zustand" = healthy ] && break
    sleep 2
  done
  echo "  Auth: ${zustand:-unbekannt}"
  # Nur Namen, nie Werte: sind beide Grenzen im Container angekommen?
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' wamo-supabase-auth | cut -d= -f1 \
    | grep -E '^GOTRUE_SESSIONS_(TIMEBOX|INACTIVITY_TIMEOUT)$' | sed 's/^/  gesetzt: /'
  exit 0
fi

echo "== 4. Geheimnisse pruefen (nur ob gesetzt und neu, ohne Werte)"
for k in POSTGRES_PASSWORD JWT_SECRET ANON_KEY SERVICE_ROLE_KEY SUPABASE_PUBLISHABLE_KEY SUPABASE_SECRET_KEY JWT_KEYS DASHBOARD_PASSWORD SECRET_KEY_BASE VAULT_ENC_KEY PG_META_CRYPTO_KEY; do
  new="$(grep -m1 "^$k=" .env | cut -d= -f2-)"
  old="$(grep -m1 "^$k=" .env.example | cut -d= -f2- || true)"
  src="$(grep -m1 "^$k=" "$SRC/.env" | cut -d= -f2- || true)"
  if [ -z "$new" ]; then echo "  $k: FEHLT"; exit 1
  elif [ "$new" = "$old" ]; then echo "  $k: noch Beispielwert"; exit 1
  elif [ "$new" = "$src" ]; then echo "  $k: gleich wie in der Vorlage"; exit 1
  else echo "  $k: neu gesetzt"; fi
done

echo "== 5. Starten"
docker compose up -d
for i in $(seq 1 60); do
  unhealthy="$(docker compose ps --format '{{.Name}} {{.Health}} {{.State}}' | grep -vE ' (healthy|) running$' || true)"
  [ -z "$unhealthy" ] && break
  sleep 5
done
docker compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Health}}\t{{.Ports}}'

echo "== 6. Rauchtest"
curl -s -o /dev/null -w "  Auth-Gesundheit: HTTP %{http_code}\n" -H "apikey: $(grep -m1 '^ANON_KEY=' .env | cut -d= -f2-)" "http://localhost:${API_PORT}/auth/v1/health"
curl -s "http://localhost:${API_PORT}/auth/v1/.well-known/jwks.json" | python3 -c 'import json,sys; print("  JWKS-Schluessel:", len(json.load(sys.stdin).get("keys", [])))'
docker exec wamo-supabase-db psql -U postgres -tAc "select '  Postgres ' || current_setting('server_version') || ', Schemas: ' || count(*) from pg_namespace where nspname not like 'pg_%'"
echo "Fertig: WAMO-Supabase laeuft unter http://${HOST_IP}:${API_PORT} (Postgres ueber Pooler ${PG_PORT})."
