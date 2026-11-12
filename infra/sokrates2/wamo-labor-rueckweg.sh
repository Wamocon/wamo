#!/usr/bin/env bash
# Rückweg zu wamo-labor-einrichten.sh: Labor-Dienst auf Sokrates-2 abschalten. Nur als root (sudo), wie die übrigen Rückwege:
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-rueckweg.sh --trocken"     # zeigt nur an, was geschehen würde
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-rueckweg.sh"               # Container weg, Site weg, Firewall zurück
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-rueckweg.sh --daten-loeschen --bestaetigung LABOR-LOESCHEN"
#
# Was geschieht ohne --daten-loeschen:
#   1. Container wamo-labor-dienst und das Netz wamo-labor-lokal entfernen (docker compose down). Daten und Token bleiben.
#   2. Caddy-Site wamo-labor.caddy entfernen und Caddy neu laden (55443 bis 55446 bleiben unberührt)
#   3. Firewall zurück: Die älteste Sicherung der Firewall aus /var/backups/wamo-labor, die noch keine Labor-Kette enthält, wird wieder als
#      /usr/local/sbin/wamo-firewall eingespielt (systemctl reload wamo-firewall.service), danach werden die übrig gebliebenen Ketten
#      WAMO-FW-LABOR und WAMO-FW-LABAUS entfernt. Gibt es keine solche Sicherung, bleibt die Firewall: Ihre Regeln schaden ohne Container nicht.
# Was bleibt: Port 55447 und das Netz wamo-labor-netz im Compose von wamo-caddy (ohne Site lauscht Caddy auf 55447 nicht), die Daten in
# /var/lib/wamo-labor, die Token und Adressdateien in /etc/wamo-labor, das Image, WAMO_LABOR_URL und WAMO_LABOR_TOKEN in /etc/wamo/core.env
# (der Kern liest sie ohnehin erst mit seinem nächsten Release, ohne Dienst meldet er den Labor-Dienst als nicht erreichbar).
#
# Mit --daten-loeschen --bestaetigung LABOR-LOESCHEN zusätzlich der Löschlauf (wamo-labor-loeschlauf.sh --vor-der-frist): Daten, Token,
# Image, Kern-Einstellungen, mit Nachweis. Ohne das Bestätigungswort geschieht gar nichts (Abbruch vor dem ersten Schritt).
#
# Probelauf ohne root (nur für Tests): WAMO_LABOR_WURZEL=/pfad/zu/einem/wegwerf/ordner (siehe wamo-labor-einrichten.sh).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
W="${WAMO_LABOR_WURZEL:-}"
BASIS="$W/opt/wamo-labor"
COMPOSE="$BASIS/docker-compose.yml"
SICHERUNG="$W/var/backups/wamo-labor"
FIREWALL_BIN="$W/usr/local/sbin/wamo-firewall"
CADDY_DIR="${WAMO_LABOR_CADDY_DIR:-/home/wamocon/wamo-tls/caddy}"
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-labor.caddy"
WORT_SOLL="LABOR-LOESCHEN"

TROCKEN=nein
LOESCHEN=nein
WORT=""

usage() {
  echo "Aufruf: $0 [--trocken] [--daten-loeschen --bestaetigung $WORT_SOLL]" >&2
  exit 2
}
fehler() { echo "$*" >&2; exit 1; }
# Jede Änderung läuft über tu: im Trockenlauf wird sie nur angezeigt
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --trocken) TROCKEN=ja ;;
    --daten-loeschen) LOESCHEN=ja ;;
    --bestaetigung) [ $# -ge 2 ] || usage; WORT="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

if [ -n "$W" ]; then
  [[ "$W" =~ ^/[A-Za-z0-9/_.:~-]+$ ]] || fehler "WAMO_LABOR_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9 und / _ . : ~ - sein."
  echo "Probelauf unter $W (kein root)"
elif [ "$TROCKEN" = nein ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
fi
if [ "$LOESCHEN" = ja ] && [ "$WORT" != "$WORT_SOLL" ]; then
  echo "--daten-loeschen verlangt --bestaetigung $WORT_SOLL. Es wurde nichts geändert." >&2
  exit 2
fi

dienst_entfernen() {
  echo "== 1. Container wamo-labor-dienst und Netz wamo-labor-lokal"
  if [ -f "$COMPOSE" ]; then
    tu docker compose -f "$COMPOSE" down --remove-orphans || true
  else
    echo "  kein Compose in $BASIS"
  fi
  # Nur die festen Namen, nie ein Muster
  tu docker rm -f wamo-labor-dienst || true
  tu docker network rm wamo-labor-lokal || true
  echo "  entfernt (das Netz wamo-labor-netz gehört zu wamo-caddy und bleibt)"
}

site_entfernen() {
  echo "== 2. Caddy-Site"
  if [ ! -f "$SITE_DATEI" ]; then echo "  keine Site vorhanden"; return 0; fi
  tu rm -f "$SITE_DATEI"
  if [ "$TROCKEN" = ja ] || [ -n "$(docker ps -q -f name=^wamo-caddy$ 2>/dev/null || true)" ]; then
    # Schlägt der Reload fehl, geht der Rückweg trotzdem weiter (die Site ist weg, Caddy übernimmt das beim nächsten Laden)
    if tu docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
      echo "  Site entfernt, Caddy neu geladen"
    else
      echo "  WARNUNG: caddy reload fehlgeschlagen, die Site-Datei ist entfernt" >&2
    fi
  fi
}

# Die älteste Sicherung der Firewall, in der es die Labor-Kette noch nicht gab (spätere Läufe sichern schon die neue Fassung)
alte_firewall() {
  local d
  for d in "$SICHERUNG"/*/; do
    if [ -f "${d}wamo-firewall.vorher" ] && ! grep -q 'WAMO-FW-LABOR' "${d}wamo-firewall.vorher"; then
      printf '%s\n' "${d}wamo-firewall.vorher"
      return 0
    fi
  done
  return 0
}

firewall_zurueck() {
  echo "== 3. Firewall: Kette für 55447 und Sperre des Ausgangs zurück"
  local alt k
  alt="$(alte_firewall)"
  if [ -z "$alt" ]; then
    echo "  keine Sicherung ohne Labor-Kette in $SICHERUNG gefunden: Die Firewall bleibt. Zum Entfernen die vorige Fassung von wamo-firewall.sh"
    echo "  einspielen (git revert, dann wamo-firewall-einrichten.sh). Die Regeln schaden ohne Container nicht."
    return 0
  fi
  tu install -m 0755 "$alt" "$FIREWALL_BIN"
  tu systemctl reload wamo-firewall.service || echo "  WARNUNG: reload der Firewall fehlgeschlagen (systemctl status wamo-firewall)" >&2
  # Die alte Fassung kennt die Ketten nicht mehr: nach dem Reload sind sie leer und unbenutzt
  for k in WAMO-FW-LABOR WAMO-FW-LABAUS; do
    tu iptables -w 5 -F "$k" || true
    tu iptables -w 5 -X "$k" || true
  done
  echo "  alte Fassung von wamo-firewall.sh wieder eingespielt"
}

daten_loeschen() {
  echo "== 4. Daten, Token, Image und Kern-Einstellungen (Löschlauf)"
  local zusatz=()
  [ "$TROCKEN" = nein ] || zusatz=(--trocken)
  bash "$HERE/wamo-labor-loeschlauf.sh" --vor-der-frist --bestaetigung "$WORT" "${zusatz[@]}"
}

dienst_entfernen
site_entfernen
firewall_zurueck
if [ "$LOESCHEN" = ja ]; then
  daten_loeschen
else
  echo "Daten und Token bleiben (wamo-labor-rueckweg.sh --daten-loeschen --bestaetigung $WORT_SOLL löscht sie)."
  echo "WAMO_LABOR_URL und WAMO_LABOR_TOKEN stehen noch in /etc/wamo/core.env: wamo-core-env loeschen WAMO_LABOR_URL WAMO_LABOR_TOKEN"
  echo "(wirkt mit dem nächsten Kern-Release, der Kern wird hier nie neu gestartet)."
fi
echo "Fertig."
