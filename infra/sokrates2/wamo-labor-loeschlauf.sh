#!/usr/bin/env bash
# Löschlauf des Labor-Dienstes auf Sokrates-2 nach der Aufbewahrung (Vertrag D10: höchstens bis 11.12.2026, 14 Tage nach dem Labortag).
# Nur als root (sudo). Löscht Daten, Token, Container, Image und die Kern-Einstellungen und schreibt einen Nachweis mit der Liste, was
# gelöscht wurde, ohne Inhalte (nur Namen, Größen und Zählungen):
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-loeschlauf.sh"                                  # zeigt nur, was gelöscht würde (Ausgang 3)
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-loeschlauf.sh --bestaetigung LABOR-LOESCHEN"    # löscht (frühestens am 12.12.2026)
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-loeschlauf.sh --bestaetigung LABOR-LOESCHEN --trocken"
#
#   --bestaetigung WORT  ohne das Wort LABOR-LOESCHEN ändert das Skript nichts (ein falsches Wort ist ein Aufruffehler, Ausgang 2)
#   --vor-der-frist      erlaubt den Lauf auch vor dem 12.12.2026 (zum Beispiel über wamo-labor-rueckweg.sh --daten-loeschen)
#   --trocken            zeigt jeden Schritt nur an
#
# Reihenfolge: 1. Pfadkontrolle (feste Pfade, kein symbolischer Link), 2. Dienst stoppen (docker compose down, Container samt Protokollen und
# das Netz wamo-labor-lokal weg), 3. Dateien in /var/lib/wamo-labor überschreiben (shred, wo vorhanden) und löschen, dann die leeren Ordner,
# 4. kern-token und betreuer-token überschreiben und löschen, 5. WAMO_LABOR_URL und WAMO_LABOR_TOKEN aus /etc/wamo/core.env entfernen
# (wamo-core-env loeschen; die laufende Kern-Instanz hält die Werte bis zu ihrem nächsten Release im Speicher), 6. alle Tags des Images
# wamo-labor-dienst entfernen, 7. Nachweis nach /var/log/wamo-labor/loeschnachweis-<Zeit>.txt (root, 0640) und auf den Bildschirm.
# Es bleiben nur Konfiguration ohne personenbezogene Daten (betreuer-adressen, plaetze-adressen, basis-image), die Sicherungen des Einrichtens
# (/var/backups/wamo-labor: Compose-, Firewall- und Caddy-Stand, keine Token, keine Daten) und der Nachweis.
#
# Was dieses Skript nicht kann: Dateisystemblöcke, Snapshots des Volumes und Sicherungen des Servers liegen außerhalb des Dienstes und dieses
# Skripts. Der Betrieb sorgt dafür, dass es über den 11.12.2026 hinaus keine gibt, und prüft das von Hand (der Nachweis nennt es).
#
# Ausgang: 0 gelöscht (oder nichts zu löschen), 1 Fehler (zum Beispiel ein Pfad ist nicht so, wie er sein muss), 2 Aufruf falsch, 3 nur
# der Plan gezeigt oder die Aufbewahrung läuft noch.
#
# Probelauf ohne root (nur für Tests): WAMO_LABOR_WURZEL=/pfad/zu/einem/wegwerf/ordner, dazu WAMO_LABOR_HEUTE=JJJJMMTT für das Datum.
set -euo pipefail

W="${WAMO_LABOR_WURZEL:-}"
ETC="$W/etc/wamo-labor"
DATEN="$W/var/lib/wamo-labor"
COMPOSE="$W/opt/wamo-labor/docker-compose.yml"
NACHWEIS_DIR="$W/var/log/wamo-labor"
CORE_ENV="$W/usr/local/sbin/wamo-core-env"
TOKEN_NAMEN="kern-token betreuer-token"
BILD_NAME=wamo-labor-dienst
WORT_SOLL="LABOR-LOESCHEN"
FRIST=20261211

TROCKEN=nein
WORT=""
VOR_FRIST=nein
LISTE=""
IMAGES=""

usage() {
  echo "Aufruf: $0 [--bestaetigung $WORT_SOLL] [--vor-der-frist] [--trocken]" >&2
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
    --vor-der-frist) VOR_FRIST=ja ;;
    --bestaetigung) [ $# -ge 2 ] || usage; WORT="$2"; shift ;;
    *) usage ;;
  esac
  shift
done

HEUTE="$(date +%Y%m%d)"
if [ -n "$W" ]; then
  [[ "$W" =~ ^/[A-Za-z0-9/_.:~-]+$ ]] || fehler "WAMO_LABOR_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9 und / _ . : ~ - sein."
  HEUTE="${WAMO_LABOR_HEUTE:-$HEUTE}"
  echo "Probelauf unter $W (kein root)"
else
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
fi
[[ "$HEUTE" =~ ^[0-9]{8}$ ]] || fehler "Das Datum ist ungültig: $HEUTE"
if [ -n "$WORT" ] && [ "$WORT" != "$WORT_SOLL" ]; then
  echo "Falsches Bestätigungswort (verlangt: $WORT_SOLL). Es wurde nichts geändert." >&2
  exit 2
fi

# Ein Pfad muss ein echter Ordner sein, kein symbolischer Link, und physisch dort liegen, wo er steht (nie ein Umweg an eine fremde Stelle)
pfad_pruefen() {
  local pfad="$1" basis
  [ -e "$pfad" ] || return 0
  [ ! -L "$pfad" ] && [ -d "$pfad" ] || fehler "$pfad ist kein gewöhnlicher Ordner (symbolischer Link?), Abbruch ohne Löschen."
  basis="$(cd "${W:-/}" && pwd -P)"
  [ "$(cd "$pfad" && pwd -P)" = "${basis%/}${pfad#"$W"}" ] || fehler "$pfad liegt physisch an einer anderen Stelle, Abbruch ohne Löschen."
}

# Namen und Größen, nie Inhalte: eine Zeile je Datei, Pfade ohne den Wurzel-Vorsatz des Probelaufs
liste_dateien() {
  local f
  if [ -d "$DATEN" ]; then
    while IFS= read -r -d '' f; do
      printf '  Datei  /var/lib/wamo-labor/%s  Größe %s Byte\n' "${f#"$DATEN"/}" "$(stat -c %s "$f")"
    done < <(find "$DATEN" -mindepth 1 \( -type f -o -type l \) -print0 | sort -z)
  fi
  for f in $TOKEN_NAMEN; do
    if [ -f "$ETC/$f" ]; then printf '  Datei  /etc/wamo-labor/%s  Größe %s Byte\n' "$f" "$(stat -c %s "$ETC/$f")"; fi
  done
}

liste_images() { docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep "^$BILD_NAME:" || true; }

# Dateien überschreiben (wo shred da ist) und löschen; das Überschreiben ist auf Journal- und Copy-on-Write-Dateisystemen keine Garantie
datei_loeschen() {
  shred -n 1 "$1" 2>/dev/null || true
  rm -f "$1"
}

daten_loeschen() {
  local f
  [ -d "$DATEN" ] || return 0
  while IFS= read -r -d '' f; do datei_loeschen "$f"; done < <(find "$DATEN" -mindepth 1 \( -type f -o -type l \) -print0)
  find "$DATEN" -depth -type d -empty -delete
  [ ! -e "$DATEN" ] || fehler "$DATEN ist nach dem Löschen nicht leer (andere Dateiarten?), bitte ansehen."
}

nachweis_text() {
  local anzahl="$1" bytes="$2" tag
  echo "Löschnachweis Labor-Dienst (keine Inhalte, nur Namen, Größen und Zählungen)"
  echo "Zeit: $(date +%Y-%m-%dT%H:%M:%S%z)   Rechner: $(uname -n)   Aufgerufen von: ${SUDO_USER:-root}"
  echo "Gelöscht:"
  printf '%s' "$LISTE"
  echo "  Zusammen $anzahl Dateien, $bytes Byte (Größe vor dem Löschen)"
  echo "  Container wamo-labor-dienst samt Container-Protokollen und Netz wamo-labor-lokal"
  for tag in $IMAGES; do echo "  Image $tag"; done
  echo "  Kern-Einstellungen WAMO_LABOR_URL und WAMO_LABOR_TOKEN in /etc/wamo/core.env (die laufende Kern-Instanz hält sie bis zum nächsten Kern-Release im Speicher)"
  echo "Was bleibt: /etc/wamo-labor/betreuer-adressen, plaetze-adressen und basis-image (Konfiguration ohne personenbezogene Daten),"
  echo "  /var/backups/wamo-labor (Stand von Compose, Firewall und Caddy vor dem Einrichten, keine Token, keine Daten) und dieser Nachweis."
  echo "Von Hand zu prüfen (liegt außerhalb des Dienstes, Vertrag D10): keine Snapshots oder Sicherungen von /var/lib/wamo-labor oder des ganzen"
  echo "  Servers, die über den 11.12.2026 hinaus bestehen; auch Hostsicherungen und Datenträgerabbilder zählen."
}

stoppen_und_loeschen() {
  local f tag
  echo "== Dienst stoppen"
  if [ -f "$COMPOSE" ]; then tu docker compose -f "$COMPOSE" down --remove-orphans || true; fi
  tu docker rm -f wamo-labor-dienst || true
  tu docker network rm wamo-labor-lokal || true
  echo "== Daten und Token löschen"
  if [ "$TROCKEN" = ja ]; then
    printf '%s' "$LISTE" | sed 's/^  /  [trocken] löschen: /'
  else
    daten_loeschen
    for f in $TOKEN_NAMEN; do datei_loeschen "$ETC/$f"; done
  fi
  echo "== Kern-Einstellungen"
  if [ -x "$CORE_ENV" ] || [ "$TROCKEN" = ja ]; then
    tu "$CORE_ENV" loeschen WAMO_LABOR_URL WAMO_LABOR_TOKEN
  else
    echo "  $CORE_ENV fehlt: WAMO_LABOR_URL und WAMO_LABOR_TOKEN von Hand aus /etc/wamo/core.env entfernen" >&2
  fi
  echo "== Image"
  for tag in $IMAGES; do tu docker image rm "$tag" || true; done
}

pfad_pruefen "$DATEN"
pfad_pruefen "$ETC"
LISTE="$(liste_dateien)"
[ -z "$LISTE" ] || LISTE="$LISTE"$'\n'
ANZAHL="$(printf '%s' "$LISTE" | grep -c . || true)"
BYTES="$(printf '%s' "$LISTE" | awk '/Größe [0-9]+ Byte$/ { s += $(NF - 1) } END { print s + 0 }')"
IMAGES="$(liste_images)"
if [ "$ANZAHL" = 0 ] && [ -z "$IMAGES" ]; then
  echo "Es gibt nichts zu löschen (keine Daten, keine Token, kein Image)."
  exit 0
fi

if [ -z "$WORT" ]; then
  echo "== Plan (es wurde nichts geändert)"
  nachweis_text "$ANZAHL" "$BYTES"
  echo "Zum Löschen dieselbe Zeile mit --bestaetigung $WORT_SOLL aufrufen (frühestens am 12.12.2026)."
  exit 3
fi
if [ "$HEUTE" -le "$FRIST" ] && [ "$VOR_FRIST" = nein ]; then
  echo "Die Aufbewahrung läuft bis zum 11.12.2026 (heute ist $HEUTE). Löschen vorher nur mit --vor-der-frist." >&2
  exit 3
fi

stoppen_und_loeschen
ZEIT="$(date +%Y%m%d-%H%M%S)"
NACHWEIS="$NACHWEIS_DIR/loeschnachweis-$ZEIT.txt"
echo "== Nachweis"
if [ "$TROCKEN" = ja ]; then
  echo "  [trocken] Nachweis nach $NACHWEIS"
else
  mkdir -p "$NACHWEIS_DIR"
  chmod 0750 "$NACHWEIS_DIR"
  nachweis_text "$ANZAHL" "$BYTES" > "$NACHWEIS"
  chmod 0640 "$NACHWEIS"
  cat "$NACHWEIS"
  echo "  Nachweis gespeichert: ${NACHWEIS#"$W"}"
fi
echo "Fertig."
