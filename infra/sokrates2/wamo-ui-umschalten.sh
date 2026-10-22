#!/usr/bin/env bash
# WAMO-Oberfläche auf Sokrates-2 blau/grün betreiben: bauen, prüfen, ohne Lücke umschalten, bewähren, zurückschalten.
# Schritt 1.1 des Stufenplans in docs/selbstueberarbeitung.md (Abschnitt B). Als wamocon ausführen (nicht mit sudo) oder
# als root über den Notanker (Schritt-Skript, Schnittstelle unten). Wiederholbar; jeder Befehl, der etwas ändert, hat --trocken.
#
#   bash ~/wamo-infra/wamo-ui-umschalten.sh status [--json]
#   bash ~/wamo-infra/wamo-ui-umschalten.sh eintrag
#   bash ~/wamo-infra/wamo-ui-umschalten.sh bauen [--commit HEX40] [--sauber ja|nein] [--trocken]
#   bash ~/wamo-infra/wamo-ui-umschalten.sh umschalten [--ohne-bau] [--commit HEX40] [--sauber ja|nein]
#                                            [--bewaehrung SEKUNDEN] [--fehlerquote PROZENT] [--trocken]
#   bash ~/wamo-infra/wamo-ui-umschalten.sh zurueck [--trocken]
#   bash ~/wamo-infra/wamo-ui-umschalten.sh anker-rueckweg vX.Y.Z HEX40 blau|gruen [--trocken]
#   bash ~/wamo-infra/wamo-ui-umschalten.sh include-anlegen          (nur für wamo-ui-einrichten.sh)
#
# So läuft es: Zwei Container wamo-ui-blau und wamo-ui-gruen (Compose wamo-ui/docker-compose.yml), je Farbe ein eigenes
# Image-Tag (wamo-ui:blau, wamo-ui:gruen) und ein Archiv-Tag je Fassung (wamo-ui:vX.Y.Z-<Commit, 12 Zeichen>). Welche Farbe
# live ist, steht in einer Datei von Caddy: etc/kern/wamo-ui-aktiv.inc, die dieses Skript erzeugt und die wamo-ui.caddy
# einbindet. "caddy reload" schaltet um (kein Neustart von Caddy, laufende Anfragen enden normal, neue gehen an die neue Farbe).
#
#   umschalten: 1 inaktive Farbe bauen und starten (bauen), 2 am NEUEN Container prüfen (GET /version.json mit Version und
#   Commit, Sicherheitsheader mit Anmeldung und Kern, Kern antwortet ohne Anmeldung 401), 3 Include-Datei prüfen, atomar
#   tauschen und Caddy neu laden, 4 abwarten, bis die neue Fassung antwortet, 5 Bewährung (Fehlerquote über die öffentliche
#   Adresse; drei Fehler in Folge oder eine zu hohe Quote lösen den automatischen Rückweg aus), 6 aufräumen. Die alte Farbe
#   läuft weiter (Rückweg, Chunks offener Seiten) und wird erst beim nächsten Release ersetzt.
#   zurueck: Include-Datei auf die vorige Farbe, Reload. Eine Sekunde, solange die vorige Farbe läuft; steht sie, wird sie
#   erst gestartet (etwa 10 Sekunden). Die bisher aktive Farbe bleibt als vorige stehen, "zurueck" gilt also auch vorwärts.
#
# Schnittstelle zum Notanker (infra/sokrates2/anker, nicht von diesem Skript geändert): Der Anker ruft als root ein festes
# Schritt-Skript rueckweg-ui mit Version, Commit und Ziel aus seiner Zustandsdatei auf. Dieses Skript bedient genau das mit
# "anker-rueckweg vX.Y.Z HEX40 blau|gruen": idempotent (schon aktiv gibt Exit 0), streng geprüfte Parameter, keine Shell, kein
# Pfad aus der Anfrage. Der Eintrag "letzte gute Version" für die Oberfläche steht im Format von stand.json in der Ausgabe
# von "eintrag" und am Ende von "umschalten" (Zeile EINTRAG ...): {"version","commit","ziel","seit"}.
# Die Zustandsdatei des Ankers schreibt nur der Anker.
#
# Ausgabe für Maschinen: Zeilen "ZEIT name=epoche" (Messpunkte), "BEREIT ...", "EINTRAG {...}".
# Rückgabewerte: 0 in Ordnung | 1 Voraussetzung oder Fehler | 2 Aufruf ungültig | 3 Prüfung vor dem Umschalten fehlgeschlagen
# (nichts umgeschaltet) | 4 nach dem Umschalten automatisch zurückgenommen | 5 Rückweg fehlgeschlagen (Handarbeit nötig) |
# 6 Ziel des Rückwegs nicht verfügbar.
#
# Der Rauchtest (wamo_ui_rauchtest.py) übt das alles mit Attrappen gegen echtes Caddy. Dafür gilt WAMO_UI_PRAEFIX=wamo-test-NAME-
# (Testmodus: eigene Namen, Ordner und Ports, nie die Container wamo-ui-* oder wamo-caddy). Ohne diese Variable arbeitet das
# Skript nur mit den festen Namen und Pfaden des Betriebs; eine andere Belegung wird abgelehnt.
set -euo pipefail
umask 022

HERE="$(cd "$(dirname "$0")" && pwd)"

AUFRUF="Aufruf: $0 status [--json] | eintrag | bauen | umschalten | zurueck | anker-rueckweg vX.Y.Z HEX40 blau|gruen | include-anlegen
  Optionen: --trocken  --json  --ohne-bau  --commit HEX40  --sauber ja|nein  --bewaehrung 0..1800  --fehlerquote 0..50"

fehler() { echo "$*" >&2; exit 1; }
aufruf_fehler() { echo "$*" >&2; echo "$AUFRUF" >&2; exit 2; }
zeit() { date +%s.%N; }

# ---------- Eingaben (streng, bevor irgendetwas angefasst wird)
COMMIT_RE='^[0-9a-f]{40}$'
VERSION_V_RE='^v(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$'
VERSION_RE='^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$'
ZAHL_RE='^[0-9]{1,4}$'

# Als Schritt-Skript des Notankers installiert (Name rueckweg-ui, Aufruf: rueckweg-ui vX.Y.Z HEX40 blau|gruen) gilt genau
# der Befehl anker-rueckweg. Der Anker kopiert dieses Skript unverändert; es braucht dafür keine weitere Datei.
if [ "$(basename "$0")" = rueckweg-ui ]; then set -- anker-rueckweg "$@"; fi

[ $# -ge 1 ] || aufruf_fehler "Befehl fehlt."
BEFEHL="$1"; shift
case "$BEFEHL" in
  status|eintrag|bauen|umschalten|zurueck|anker-rueckweg|include-anlegen) ;;
  *) aufruf_fehler "Unbekannter Befehl." ;;
esac

ARG_VERSION=""; ARG_COMMIT=""; ARG_ZIEL=""
if [ "$BEFEHL" = anker-rueckweg ]; then
  [ $# -ge 3 ] || aufruf_fehler "anker-rueckweg braucht Version, Commit und Ziel."
  ARG_VERSION="$1"; ARG_COMMIT="$2"; ARG_ZIEL="$3"; shift 3
  [[ "$ARG_VERSION" =~ $VERSION_V_RE ]] || aufruf_fehler "Version ungültig (vX.Y.Z)."
  [[ "$ARG_COMMIT" =~ $COMMIT_RE ]] || aufruf_fehler "Commit ungültig (40 Hexzeichen, klein)."
  [[ "$ARG_ZIEL" =~ ^(blau|gruen)$ ]] || aufruf_fehler "Ziel ungültig (blau oder gruen)."
fi

TROCKEN=nein; JSON=nein; OHNE_BAU=nein; COMMIT_OPT=""; SAUBER_OPT=""; BEWAEHRUNG=30; FEHLERQUOTE=10
option_erlaubt() {   # $1 Option: gilt sie für diesen Befehl?
  case "$1" in
    --trocken) [[ "$BEFEHL" =~ ^(bauen|umschalten|zurueck|anker-rueckweg)$ ]] ;;
    --json) [ "$BEFEHL" = status ] ;;
    --ohne-bau) [ "$BEFEHL" = umschalten ] ;;
    --commit|--sauber) [[ "$BEFEHL" =~ ^(bauen|umschalten)$ ]] ;;
    --bewaehrung|--fehlerquote) [ "$BEFEHL" = umschalten ] ;;
    *) return 1 ;;
  esac
}
while [ $# -gt 0 ]; do
  opt="$1"; shift
  option_erlaubt "$opt" || aufruf_fehler "Option '$opt' gibt es nicht oder gilt nicht für '$BEFEHL'."
  case "$opt" in
    --trocken) TROCKEN=ja ;;
    --json) JSON=ja ;;
    --ohne-bau) OHNE_BAU=ja ;;
    --commit)
      [ $# -gt 0 ] || aufruf_fehler "--commit braucht einen Wert."
      [[ "$1" =~ $COMMIT_RE ]] || aufruf_fehler "--commit: 40 Hexzeichen, klein."
      COMMIT_OPT="$1"; shift ;;
    --sauber)
      [ $# -gt 0 ] || aufruf_fehler "--sauber braucht einen Wert."
      [[ "$1" =~ ^(ja|nein)$ ]] || aufruf_fehler "--sauber: ja oder nein."
      SAUBER_OPT="$1"; shift ;;
    --bewaehrung)
      [ $# -gt 0 ] || aufruf_fehler "--bewaehrung braucht einen Wert."
      [[ "$1" =~ $ZAHL_RE ]] && [ "$1" -le 1800 ] || aufruf_fehler "--bewaehrung: ganze Zahl von 0 bis 1800 (Sekunden)."
      BEWAEHRUNG="$((10#$1))"; shift ;;
    --fehlerquote)
      [ $# -gt 0 ] || aufruf_fehler "--fehlerquote braucht einen Wert."
      [[ "$1" =~ $ZAHL_RE ]] && [ "$1" -le 50 ] || aufruf_fehler "--fehlerquote: ganze Zahl von 0 bis 50 (Prozent)."
      FEHLERQUOTE="$((10#$1))"; shift ;;
  esac
done

# ---------- Namen und Pfade (Betrieb fest; Testmodus nur mit Präfix wamo-test-NAME-)
PRAEFIX="${WAMO_UI_PRAEFIX:-wamo-}"
TESTMODUS=nein
if [ "$PRAEFIX" != "wamo-" ]; then
  [[ "$PRAEFIX" =~ ^wamo-test-[a-z0-9]{1,16}-$ ]] \
    || aufruf_fehler "WAMO_UI_PRAEFIX ist ungültig (erlaubt: nichts oder wamo-test-NAME-)."
  TESTMODUS=ja
fi
C_BLAU="${PRAEFIX}ui-blau"
C_GRUEN="${PRAEFIX}ui-gruen"
C_ALT="${PRAEFIX}ui"          # der einzelne Container vor der Umstellung auf blau/grün
C_CADDY="${PRAEFIX}caddy"
NETZ="${PRAEFIX}ui-netz"
BILD="${PRAEFIX}ui"
PROJEKT="${PRAEFIX}ui"
BASIS=/home/wamocon/wamo-ui
CADDY_DIR=/home/wamocon/wamo-tls/caddy
CA=/home/wamocon/wamo-tls/ca/wamo-ca.crt
SUPABASE_ENV=/home/wamocon/supabase-wamo/.env
QUELLE="$HERE"                # enthält ui/ und wamo-ui/ (verteilt nach ~/wamo-infra)
BAUDATEI=""                   # leer: wamo-ui/Dockerfile
BAUKONTEXT=""                 # leer: ui/
PLATTFORM=linux/arm64
BEREIT_S=90                   # so lange darf ein neuer Container zum Starten brauchen
CADDY_IMAGE=caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d
if [ "$TESTMODUS" = ja ]; then
  pfad_pruefen() {
    [[ "$2" =~ ^/[A-Za-z0-9._/-]{1,200}$ ]] && [[ "$2" != *..* ]] || aufruf_fehler "$1 ist ungültig."
    # Der Testmodus darf nie die Ordner des Betriebs treffen (sie liegen im Home von wamocon)
    [[ "$2" != /home/* && "$2" != /root/* ]] || aufruf_fehler "$1 darf im Testmodus nicht unter /home oder /root liegen."
  }
  for v in WAMO_UI_BASIS WAMO_UI_CADDY_DIR WAMO_UI_QUELLE WAMO_UI_BAUDATEI WAMO_UI_BAUKONTEXT; do
    if [ -n "${!v:-}" ]; then pfad_pruefen "$v" "${!v}"; fi
  done
  BASIS="${WAMO_UI_BASIS:?Im Testmodus muss WAMO_UI_BASIS gesetzt sein.}"
  CADDY_DIR="${WAMO_UI_CADDY_DIR:?Im Testmodus muss WAMO_UI_CADDY_DIR gesetzt sein.}"
  QUELLE="${WAMO_UI_QUELLE:-$HERE}"
  BAUDATEI="${WAMO_UI_BAUDATEI:-}"
  BAUKONTEXT="${WAMO_UI_BAUKONTEXT:-}"
  PLATTFORM=""
  if [ -n "${WAMO_UI_BEREIT_S:-}" ]; then
    [[ "$WAMO_UI_BEREIT_S" =~ ^[0-9]{1,3}$ ]] || aufruf_fehler "WAMO_UI_BEREIT_S ist ungültig."
    BEREIT_S="$((10#$WAMO_UI_BEREIT_S))"
  fi
fi
KONF="$BASIS/wamo-ui.conf"
COMPOSE="$BASIS/docker-compose.yml"
INC="$CADDY_DIR/etc/kern/wamo-ui-aktiv.inc"
LOCK="$BASIS/.umschalten.lock"
UI="${BAUKONTEXT:-$QUELLE/ui}"
ARBEIT=""
BAULOG=""
trap 'rm -rf "${ARBEIT:-}"; rm -f "${BAULOG:-}"' EXIT
trap 'echo "Abgebrochen. Zustand prüfen: $0 status" >&2; exit 130' INT TERM

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten): Der Ordner dieses Skripts, sein
# Elternordner und alles darin gehören dem Aufrufer oder root und sind weder für die Gruppe noch für alle beschreibbar.
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

if [ "$TESTMODUS" = nein ]; then
  case "$(id -un)" in
    wamocon|root) ;;
    *) fehler "Bitte als wamocon ausführen (nicht mit sudo) oder über den Notanker als root." ;;
  esac
  pruefe_herkunft
fi

# ---------- Einstellungen lesen (nie sourcen): nur die Schlüssel von wamo-ui.conf, mit denselben Prüfungen wie wamo-ui-einrichten.sh
# (WAMO_UI_TLS und WAMO_UI_HSTS gehören zu wamo-ui-einrichten.sh; hier sind es nur bekannte Schlüssel)
WAMO_UI_URL=""; NEXT_PUBLIC_SUPABASE_URL=""; NEXT_PUBLIC_WAMO_KERN_BUERO=""
NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER=""

lies_konfig() {
  [ -f "$KONF" ] || fehler "$KONF fehlt (zuerst: python scripts/wamo_ui_sokrates2.py einrichten)."
  local zeile name wert url muster
  while IFS= read -r zeile || [ -n "$zeile" ]; do
    zeile="${zeile%$'\r'}"
    case "$zeile" in ''|'#'*) continue ;; esac
    [[ "$zeile" == *=* ]] || fehler "$KONF: Zeile ohne KEY=VALUE."
    name="${zeile%%=*}"
    wert="${zeile#*=}"
    wert="${wert%%#*}"
    wert="$(sed -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' <<<"$wert")"
    case "$name" in
      WAMO_UI_URL|NEXT_PUBLIC_SUPABASE_URL|NEXT_PUBLIC_WAMO_KERN_BUERO|WAMO_UI_TLS|WAMO_UI_HSTS|NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER)
        printf -v "$name" '%s' "$wert" ;;
      *) fehler "$KONF: Schlüssel '$name' ist nicht vorgesehen." ;;
    esac
  done < "$KONF"
  muster='^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'
  if [ "$TESTMODUS" = ja ]; then muster='^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'; fi
  for url in "$WAMO_UI_URL" "$NEXT_PUBLIC_SUPABASE_URL" "$NEXT_PUBLIC_WAMO_KERN_BUERO"; do
    [[ "$url" =~ $muster ]] || fehler "$KONF: Adresse '$url' ungültig (nur https://Host:Port, ohne Pfad)."
  done
  [[ "$NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER" =~ ^([0-9]{1,3})?$ ]] \
    || fehler "$KONF: NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER muss leer oder eine ganze Zahl sein."
  if [ "$TESTMODUS" = nein ]; then
    local besitzer modus
    read -r besitzer modus < <(stat -c '%U %a' "$KONF")
    if { [ "$besitzer" != wamocon ] && [ "$besitzer" != root ]; } || (( 8#$modus & 8#022 )); then
      fehler "$KONF gehört weder wamocon noch root oder ist für Gruppe/alle beschreibbar ($besitzer, $modus)."
    fi
  fi
}

CURL_OPT=(-s --max-time 4)
if [ "$TESTMODUS" = nein ]; then CURL_OPT+=(--cacert "$CA"); fi

# ---------- Docker, Caddy
host_von() {   # Farbe -> Containername
  case "$1" in
    blau) echo "$C_BLAU" ;;
    gruen) echo "$C_GRUEN" ;;
    alt) echo "$C_ALT" ;;
    *) return 1 ;;
  esac
}
laeuft() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" = true ]; }
existiert() { docker inspect "$1" >/dev/null 2>&1; }
caddy_laeuft() { laeuft "$C_CADDY"; }
caddy_neu_laden() {
  local aus
  aus="$(docker exec "$C_CADDY" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1)" \
    || { echo "  caddy reload fehlgeschlagen:" >&2; tail -n 6 <<<"$aus" >&2; return 1; }
}

# Syntaxprobe einer Konfiguration in einem Wegwerf-Container ohne Netz und ohne eingehängte Ordner: Die Dateien werden per
# tar hineinkopiert, so braucht es keinen Pfad, den auch der Docker-Daemon kennen müsste.
caddy_adapt_pruefen() {   # $1 = Ordner "etc" (Caddyfile, kern/)
  local cid rc=0 aus=""
  cid="$(docker create --network none --cap-drop ALL --cap-add NET_BIND_SERVICE --security-opt no-new-privileges:true \
    "$CADDY_IMAGE" caddy adapt --config /etc/caddy/Caddyfile)" || return 1
  if tar -C "$1" -cf - . | docker cp - "$cid:/etc/caddy" >/dev/null 2>&1; then
    aus="$(docker start -a "$cid" 2>&1 >/dev/null)" || rc=1
  else
    rc=1; aus="Konfiguration ließ sich nicht in den Prüf-Container kopieren."
  fi
  docker rm -f "$cid" >/dev/null 2>&1 || true
  if [ "$rc" != 0 ]; then echo "  Caddy-Konfiguration ungültig:" >&2; tail -n 6 <<<"$aus" >&2; return 1; fi
}

# ---------- Include-Datei (der einzige Ort, der die Farbe festlegt)
# Aufbau: zwei Kopfzeilen mit dem Zustand (live, vorige, seit), dann die Anweisungen. Erster Upstream ist die live gesetzte
# Farbe, zweiter die vorige. Weitere Hinweise stehen im Text selbst.
include_text() {   # $1 live, $2 vorige, $3 seit
  local live="$1" vorige="$2" seit="$3" h1 h2 gesundheit
  if [ "$live" = keine ]; then
    cat <<EOF
# live=keine vorige=keine
# seit=$seit
# Noch keine Oberfläche gebaut: bash ~/wamo-infra/wamo-ui-umschalten.sh umschalten
handle {
	respond "Die Oberfläche wird eingerichtet." 503
}
EOF
    return 0
  fi
  h1="$(host_von "$live")"
  if [ "$vorige" = keine ]; then h2="$h1"; else h2="$(host_von "$vorige")"; fi
  # Der Container der früheren Fassung (alt) kennt /version.json nicht: Solange er live ist, prüft Caddy die Startseite.
  # Steht er nur als vorige Farbe da, gilt er als nicht gesund und ist keine automatische Rückfallebene (Rückweg von Hand geht).
  gesundheit=/version.json
  if [ "$live" = alt ]; then gesundheit=/; fi
  cat <<EOF
# live=$live vorige=$vorige
# seit=$seit
# Erzeugt von wamo-ui-umschalten.sh, nie von Hand ändern (wamo-ui.caddy bindet die Datei ein).
# Erster Upstream: live gesetzte Farbe. Zweiter: die vorige Farbe. Fällt der erste aus (Verbindungsfehler oder Gesundheits-
# probe /version.json alle 2 Sekunden), übernimmt der zweite ohne Eingriff. Gibt es keine vorige Farbe, steht der erste
# Upstream doppelt.
# Statische Dateien (/_next/static/): Jeder Build hat eigene Dateinamen. Offene Seiten der anderen Fassung fragen sie
# weiter an. Antwortet die live gesetzte Farbe mit 404, fragt Caddy die vorige und gibt deren Antwort weiter (nur GET und
# HEAD kommen bis hierher, die Site weist alles andere ab). Beim Rückweg gilt dasselbe in die andere Richtung.
handle /_next/static/* {
	reverse_proxy $h1:3000 $h2:3000 {
		lb_policy first
		lb_try_duration 5s
		lb_try_interval 250ms
		health_uri $gesundheit
		health_interval 2s
		health_timeout 2s
		fail_duration 10s
		transport http {
			dial_timeout 1s
			response_header_timeout 30s
		}
		@fehlt status 404
		handle_response @fehlt {
			reverse_proxy $h2:3000 {
				transport http {
					dial_timeout 1s
					response_header_timeout 30s
				}
			}
		}
	}
}
handle {
	reverse_proxy $h1:3000 $h2:3000 {
		lb_policy first
		lb_try_duration 5s
		lb_try_interval 250ms
		health_uri $gesundheit
		health_interval 2s
		health_timeout 2s
		fail_duration 10s
		transport http {
			dial_timeout 1s
			response_header_timeout 30s
		}
	}
}
EOF
}

ERW_VERSION=""; ERW_COMMIT=""   # die Fassung, die der neue Container tragen muss
LIVE=""; VORIGE=""; SEIT=""   # LIVE: blau|gruen|alt|keine ("" = noch keine Include-Datei), VORIGE: blau|gruen|alt|keine
lies_zustand() {
  LIVE=""; VORIGE=""; SEIT=""
  [ -f "$INC" ] || return 0
  local k1 k2
  k1="$(sed -n '1p' "$INC")"; k2="$(sed -n '2p' "$INC")"
  [[ "$k1" =~ ^#\ live=(blau|gruen|alt|keine)\ vorige=(blau|gruen|alt|keine)$ ]] \
    || fehler "$INC: erste Zeile ungültig (die Datei erzeugt wamo-ui-umschalten.sh, nicht von Hand ändern)."
  LIVE="${BASH_REMATCH[1]}"; VORIGE="${BASH_REMATCH[2]}"
  [[ "$k2" =~ ^#\ seit=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)$ ]] \
    || fehler "$INC: zweite Zeile ungültig."
  SEIT="${BASH_REMATCH[1]}"
  if [ "$LIVE" = "$VORIGE" ] && [ "$LIVE" != keine ]; then fehler "$INC: live und vorige sind gleich."; fi
  if [ "$(cat "$INC")" != "$(include_text "$LIVE" "$VORIGE" "$SEIT")" ]; then
    echo "  Hinweis: $INC weicht von der Vorlage in diesem Skript ab (von Hand geändert oder älteres Skript);" \
      "der nächste Schaltvorgang erzeugt sie neu." >&2
  fi
}

naechste_farbe() { if [ "$LIVE" = blau ]; then echo gruen; else echo blau; fi; }

# Schreibt die Include-Datei neu, prüft die ganze Konfiguration vorher in einer Kopie und lädt Caddy neu. Bei jedem Fehler
# bleibt oder kommt der alte Stand zurück (die Datei auf der Platte ist immer gültig, auch für einen Neustart von Caddy).
include_setzen() {   # $1 live, $2 vorige
  local live="$1" vorige="$2" seit work tmp
  seit="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -n "$ARBEIT" ]; then rm -rf "$ARBEIT"; fi
  work="$(mktemp -d "$CADDY_DIR/.umschalten.XXXXXX")"
  ARBEIT="$work"
  mkdir -p "$work/etc"
  cp -a "$CADDY_DIR/etc/." "$work/etc/"
  mkdir -p "$work/etc/kern"
  include_text "$live" "$vorige" "$seit" > "$work/etc/kern/wamo-ui-aktiv.inc"
  caddy_adapt_pruefen "$work/etc" || return 1
  [ ! -L "$INC" ] || fehler "$INC ist ein symbolischer Link: abgelehnt."
  [ ! -f "$INC" ] || cp -a "$INC" "$work/inc.vorher"
  tmp="$(mktemp "$INC.neu.XXXXXX")"
  install -m 644 "$work/etc/kern/wamo-ui-aktiv.inc" "$tmp"
  if [ "$(id -u)" = 0 ]; then chown -h "$(stat -c '%u:%g' "$CADDY_DIR/etc/kern")" "$tmp"; fi
  mv -f "$tmp" "$INC"
  if ! caddy_neu_laden; then
    echo "  Include-Datei wird zurückgenommen" >&2
    if [ -f "$work/inc.vorher" ]; then
      install -m 644 "$work/inc.vorher" "$tmp" && mv -f "$tmp" "$INC"
    else
      rm -f "$INC"
    fi
    caddy_neu_laden >&2 || true
    return 1
  fi
  rm -rf "$work"
  ARBEIT=""
}

# ---------- Antworten der Oberfläche lesen
VERSION_JSON_RE='^\{"name":"wamo-ui","version":"([0-9.]+)","commit":"([0-9a-f]{40}|unbekannt)","gebaut":"([0-9TZ:-]+|unbekannt)","sauber":(true|false)\}$'
P_VERSION=""; P_COMMIT=""; P_GEBAUT=""; P_SAUBER=""
parse_version() {   # $1 Antworttext; strenges Muster, alles andere ist keine gültige Antwort
  [[ "$1" =~ $VERSION_JSON_RE ]] || return 1
  P_VERSION="${BASH_REMATCH[1]}"; P_COMMIT="${BASH_REMATCH[2]}"; P_GEBAUT="${BASH_REMATCH[3]}"; P_SAUBER="${BASH_REMATCH[4]}"
  [[ "$P_VERSION" =~ $VERSION_RE ]]   # dieselbe Form wie vX.Y.Z des Ankers (ohne v)
}
# Die Antwort ist auf 600 Byte begrenzt (die gültige hat etwa 150)
version_von_container() { docker exec "$C_CADDY" wget -q -O - -T 3 "http://$1:3000/version.json" 2>/dev/null | head -c 600; }
public_holen() { curl "${CURL_OPT[@]}" "$WAMO_UI_URL/version.json" 2>/dev/null | head -c 600; }

bereit_warten() {   # $1 Containername, $2 erwartete Version, $3 erwarteter Commit, $4 Sekunden
  local host="$1" ende=$((SECONDS + $4)) body="" grund="noch keine Antwort"
  while (( SECONDS < ende )); do
    if ! laeuft "$host"; then
      grund="Container läuft nicht"
    elif body="$(version_von_container "$host")" && parse_version "$body"; then
      if [ "$P_VERSION" = "$2" ] && [ "$P_COMMIT" = "$3" ]; then return 0; fi
      grund="falsche Fassung ($P_VERSION, $P_COMMIT)"
    else
      grund="keine gültige Antwort auf /version.json"
    fi
    sleep 1
  done
  echo "  $host nicht bereit: $grund" >&2
  docker logs --tail 15 "$host" >&2 2>&1 || true
  return 1
}

# Der neue Container im Netz von Caddy: Seite antwortet 200, Sicherheitsheader stehen da, und die CSP kennt Anmeldung und Kern
# (nur dann zeigt der Build auf die richtigen Adressen).
fassung_pruefen() {   # $1 Containername
  local kopf csp
  kopf="$(docker exec "$C_CADDY" wget -S -q -O /dev/null -T 5 "http://$1:3000/" 2>&1 || true)"
  grep -qiE '^[[:space:]]*HTTP/1\.[01] 200' <<<"$kopf" || { echo "  $1: Startseite antwortet nicht mit 200" >&2; return 1; }
  csp="$(grep -iE '^[[:space:]]*content-security-policy:' <<<"$kopf" || true)"
  [ -n "$csp" ] || { echo "  $1: keine Content-Security-Policy" >&2; return 1; }
  if ! grep -qF "$NEXT_PUBLIC_SUPABASE_URL" <<<"$csp" || ! grep -qF "$NEXT_PUBLIC_WAMO_KERN_BUERO" <<<"$csp"; then
    echo "  $1: CSP ohne Anmeldung oder Büro-Kern (falsche Adressen im Build?)" >&2
    return 1
  fi
  grep -qiE '^[[:space:]]*x-content-type-options:' <<<"$kopf" || { echo "  $1: nosniff fehlt" >&2; return 1; }
  echo "  Seite, Header und Adressen des neuen Containers in Ordnung"
}

# Kernverbindung, falls der Kern erreichbar ist: ohne Anmeldung muss er 401 antworten (er lebt, die Zugangskontrolle ist geladen).
kern_pruefen() {
  local code
  code="$(curl "${CURL_OPT[@]}" -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' \
    "$NEXT_PUBLIC_WAMO_KERN_BUERO/start" 2>/dev/null || true)"
  case "$code" in
    401) echo "  Kern antwortet ohne Anmeldung mit 401 (lebt, Zugangskontrolle geladen)" ;;
    000|"") echo "  Hinweis: Kern nicht erreichbar, Kernprobe übersprungen" ;;
    *) echo "  Kern antwortet ohne Anmeldung mit HTTP $code, erwartet 401" >&2; return 1 ;;
  esac
}

wirkung_abwarten() {   # $1 Version, $2 Commit, $3 Sekunden: wartet, bis die öffentliche Adresse die Fassung liefert
  local ende=$((SECONDS + $3)) body
  while (( SECONDS < ende )); do
    if body="$(public_holen)" && parse_version "$body" && [ "$P_VERSION" = "$1" ] && [ "$P_COMMIT" = "$2" ]; then return 0; fi
    sleep 0.2
  done
  return 1
}

# Bewährung: Proben über die öffentliche Adresse, zwei je Sekunde. Drei Fehler in Folge oder mehr Fehler als die Quote
# erlaubt (Prozent aller Proben) bedeuten durchgefallen. Ein Fehler ist alles außer "Antwort mit der erwarteten Fassung".
# Rückweg auf den Container der früheren Fassung (alt): Er kennt /version.json nicht. Die Wirkung zeigt sich daran, dass die
# Startseite antwortet und /version.json 404 liefert (die neue Fassung hat sie).
wirkung_alt() {   # $1 Sekunden
  local ende=$((SECONDS + $1)) c_start c_ver
  while (( SECONDS < ende )); do
    c_start="$(curl "${CURL_OPT[@]}" -o /dev/null -w '%{http_code}' "$WAMO_UI_URL/" 2>/dev/null || true)"
    c_ver="$(curl "${CURL_OPT[@]}" -o /dev/null -w '%{http_code}' "$WAMO_UI_URL/version.json" 2>/dev/null || true)"
    if [ "$c_start" = 200 ] && [ "$c_ver" = 404 ]; then return 0; fi
    sleep 0.2
  done
  return 1
}

bewaehren() {   # $1 Version, $2 Commit, $3 Sekunden, $4 Fehlerquote in Prozent
  local ende=$((SECONDS + $3)) n=0 f=0 folge=0 body
  if [ "$3" -eq 0 ]; then echo "  Bewährung übersprungen (0 Sekunden)"; return 0; fi
  while (( SECONDS < ende )); do
    n=$((n + 1))
    if body="$(public_holen)" && parse_version "$body" && [ "$P_VERSION" = "$1" ] && [ "$P_COMMIT" = "$2" ]; then
      folge=0
    else
      f=$((f + 1)); folge=$((folge + 1))
      echo "  Bewährung: Fehler $f bei Probe $n" >&2
    fi
    if (( folge >= 3 )); then echo "  Bewährung nicht bestanden: drei Fehler in Folge" >&2; return 1; fi
    sleep 0.5
  done
  echo "  Bewährung: $n Proben, $f Fehler"
  if (( f * 100 > $4 * n )); then echo "  Bewährung nicht bestanden: Fehlerquote über $4 Prozent" >&2; return 1; fi
  return 0
}

# ---------- Bauen
publishable_key() {
  local wert rolle
  if [ "$TESTMODUS" = ja ]; then printf '%s' "sb_publishable_testplatzhalter"; return 0; fi
  [ -r "$SUPABASE_ENV" ] || fehler "$SUPABASE_ENV nicht lesbar."
  wert="$(grep -m1 '^SUPABASE_PUBLISHABLE_KEY=' "$SUPABASE_ENV" | cut -d= -f2- | tr -d "\"'" || true)"
  [ -n "$wert" ] || fehler "SUPABASE_PUBLISHABLE_KEY fehlt in $SUPABASE_ENV."
  if [[ "$wert" =~ ^sb_publishable_[A-Za-z0-9_-]+$ ]]; then
    :
  elif [[ "$wert" =~ ^eyJ[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+$ ]]; then
    # Alter Stil (JWT): nur die Rolle anon ist für den Browser bestimmt
    rolle="$(python3 -c 'import base64,json,sys; p=sys.argv[1]; p+="="*(-len(p)%4); print(json.loads(base64.urlsafe_b64decode(p)).get("role",""))' "${BASH_REMATCH[1]}" 2>/dev/null || true)"
    [ "$rolle" = anon ] || fehler "Der Schlüssel in $SUPABASE_ENV hat die Rolle '$rolle', erwartet anon. Abbruch."
  else
    fehler "SUPABASE_PUBLISHABLE_KEY hat kein erwartetes Format (sb_publishable_... oder JWT mit Rolle anon). Abbruch."
  fi
  printf '%s' "$wert"
}

BAU_COMMIT="unbekannt"; BAU_SAUBER="nein"; BAU_VERSION=""
bau_angaben() {   # Commit und Sauberkeit aus wamo-ui/bau.info (schreibt "verteilen") oder den Optionen; Version aus ui/package.json
  local datei="$QUELLE/wamo-ui/bau.info" zeile w
  BAU_COMMIT="unbekannt"; BAU_SAUBER="nein"
  if [ -f "$datei" ]; then
    while IFS= read -r zeile || [ -n "$zeile" ]; do
      case "$zeile" in
        '') ;;
        WAMO_COMMIT=*)
          w="${zeile#*=}"; [[ "$w" =~ $COMMIT_RE ]] || fehler "$datei: WAMO_COMMIT ungültig."; BAU_COMMIT="$w" ;;
        WAMO_SAUBER=*)
          w="${zeile#*=}"; [[ "$w" =~ ^(ja|nein)$ ]] || fehler "$datei: WAMO_SAUBER ungültig."; BAU_SAUBER="$w" ;;
        *) fehler "$datei: unbekannte Zeile." ;;
      esac
    done < "$datei"
  fi
  if [ -n "$COMMIT_OPT" ]; then BAU_COMMIT="$COMMIT_OPT"; fi
  if [ -n "$SAUBER_OPT" ]; then BAU_SAUBER="$SAUBER_OPT"; fi
  [ -f "$UI/package.json" ] || fehler "$UI/package.json fehlt (python scripts/wamo_ui_sokrates2.py verteilen)."
  BAU_VERSION="$(sed -n 's/^  "version": "\([0-9][0-9.]*\)",\{0,1\}$/\1/p' "$UI/package.json" | head -n1)"
  [[ "$BAU_VERSION" =~ $VERSION_RE ]] || fehler "$UI/package.json: Version fehlt oder ist ungültig."
}

# Archiv-Tag der Fassung: Version und Commit. Nur ein sauberer Stand (aus unverändertem Git) bekommt ihn; alles andere trägt
# einen Zeitstempel, damit ein Bau mit lokalen Änderungen nie den Tag eines sauberen Standes überschreibt und der Notanker
# nur auf saubere Stände zurückgehen kann.
archiv_tag() {
  if [ "$BAU_COMMIT" = unbekannt ] || [ "$BAU_SAUBER" != ja ]; then
    echo "v${BAU_VERSION}-lokal-$(date -u +%Y%m%d%H%M%S)"
  else
    echo "v${BAU_VERSION}-${BAU_COMMIT:0:12}"
  fi
}

compose_ausfuehren() {
  [ -f "$COMPOSE" ] || fehler "$COMPOSE fehlt (zuerst bauen)."
  [ ! -L "$COMPOSE" ] || fehler "$COMPOSE ist ein symbolischer Link: abgelehnt."
  # --env-file /dev/null: keine .env aus dem Ordner (die Werte kommen nur aus dem Aufruf)
  ( cd "$BASIS" && WAMO_UI_PRAEFIX="$PRAEFIX" WAMO_UI_BILD="$BILD" WAMO_UI_NETZ="$NETZ" \
      docker compose --env-file /dev/null -p "$PROJEKT" "$@" )
}

# Startet genau eine Farbe neu. Die live gesetzte Farbe wird nie angefasst: Sie würde Anfragen verlieren.
farbe_starten() {   # $1 Farbe
  [ "$1" != "$LIVE" ] || fehler "Interner Fehler: die live gesetzte Farbe $1 darf nicht neu gestartet werden."
  compose_ausfuehren up -d --no-deps --force-recreate "$1"
}

farbe_anhalten() {   # $1 Farbe; behält den Container samt Protokoll zur Fehlersuche
  local host
  host="$(host_von "$1")"
  if laeuft "$host"; then docker stop "$host" >/dev/null 2>&1 || true; fi
}

bauen_und_starten() {   # $1 Zielfarbe; setzt ERW_VERSION und ERW_COMMIT
  local ziel="$1" tag key log
  bau_angaben
  [ -f "$QUELLE/wamo-ui/docker-compose.yml" ] || fehler "$QUELLE/wamo-ui/docker-compose.yml fehlt."
  docker network inspect "$NETZ" >/dev/null 2>&1 \
    || fehler "Netz $NETZ fehlt (zuerst der Schritt caddy von wamo-ui-einrichten.sh: Compose von wamo-caddy legt es an)."
  tag="$(archiv_tag)"
  echo "== Image bauen: $BILD:$tag (Farbe $ziel, Version $BAU_VERSION, Commit $BAU_COMMIT, sauber $BAU_SAUBER)"
  key="$(publishable_key)"
  export NEXT_PUBLIC_SUPABASE_URL NEXT_PUBLIC_WAMO_KERN_BUERO NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY="$key"
  export NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
  install -d -m 755 "$BASIS"
  log="$(mktemp "$BASIS/.bau.XXXXXX")"
  BAULOG="$log"
  local args=(build --progress=plain -f "${BAUDATEI:-$QUELLE/wamo-ui/Dockerfile}")
  if [ -n "$PLATTFORM" ]; then args+=(--platform "$PLATTFORM"); fi
  # Adressen und Schlüssel ohne Wert in der Befehlszeile: Docker nimmt sie aus der Umgebung dieses Prozesses
  args+=(--build-arg NEXT_PUBLIC_SUPABASE_URL --build-arg NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
    --build-arg NEXT_PUBLIC_WAMO_KERN_BUERO --build-arg NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER
    --build-arg "WAMO_COMMIT=$BAU_COMMIT" --build-arg "WAMO_BAUZEIT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    --build-arg "WAMO_SAUBER=$BAU_SAUBER")
  if [ "$TESTMODUS" = ja ] && [ -n "${ATTRAPPE_BAUARGS:-}" ]; then
    # nur der Rauchtest: zusätzliche Build-Argumente der Attrappe, Form NAME=WERT mit engem Muster
    local a
    for a in $ATTRAPPE_BAUARGS; do
      [[ "$a" =~ ^[A-Z_]{3,30}=[A-Za-z0-9._:-]{0,40}$ ]] || fehler "ATTRAPPE_BAUARGS: ungültig."
      args+=(--build-arg "$a")
    done
  fi
  args+=(-t "$BILD:$tag" -t "$BILD:$ziel" "$UI")
  if ! docker "${args[@]}" >"$log" 2>&1; then
    echo "Bau fehlgeschlagen, Ende des Protokolls:" >&2
    tail -n 40 "$log" >&2
    exit 1
  fi
  unset NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
  tail -n 3 "$log" | sed 's/^/  /'
  echo "  Image $BILD:$tag (auch als $BILD:$ziel)"
  install -m 644 "$QUELLE/wamo-ui/docker-compose.yml" "$COMPOSE"
  echo "== Farbe $ziel starten"
  farbe_starten "$ziel"
  ERW_VERSION="$BAU_VERSION"; ERW_COMMIT="$BAU_COMMIT"
}

# Räumt alte Archiv-Tags ab: die drei neuesten bleiben (neben den Farb-Tags und den Tags aus der Zeit vor blau/grün).
alte_bilder_entfernen() {
  local tag tags
  tags="$(docker image ls "$BILD" --format '{{.Tag}}' 2>/dev/null | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+-([0-9a-f]{12}|lokal-[0-9]{14})$' | sort -u || true)"
  [ -n "$tags" ] || return 0
  while read -r tag; do
    printf '%s %s\n' "$(docker image inspect -f '{{.Created}}' "$BILD:$tag" 2>/dev/null || echo 0)" "$tag"
  done <<<"$tags" | sort -r | tail -n +4 | while read -r _ tag; do
    docker image rm "$BILD:$tag" >/dev/null 2>&1 || true
  done
  return 0
}

# ---------- Rückweg im Anschluss an einen Fehler beim Umschalten
auto_rueckweg() {   # $1 bisher live (Farbe), $2 neue (fehlerhafte) Farbe
  echo "== Automatischer Rückweg auf $1" >&2
  if [ "$1" = keine ]; then
    echo "  Es gab keine vorige Fassung: kein Rückweg möglich. Zustand: $0 status" >&2
    return 1
  fi
  if ! include_setzen "$1" keine; then
    echo "  FEHLER: Rückweg ließ sich nicht schalten. Handarbeit nötig: $0 zurueck oder docker exec $C_CADDY caddy reload" >&2
    return 1
  fi
  farbe_anhalten "$2"
  if [ "$1" = alt ]; then
    wirkung_alt 20 || echo "  Hinweis: $WAMO_UI_URL liefert die frühere Fassung noch nicht: $0 status" >&2
  fi
  echo "ZEIT rueckweg_ende=$(zeit)"
  echo "  $1 ist wieder live, $2 ist angehalten (Protokoll: docker logs $(host_von "$2"))" >&2
  return 0
}

# ---------- Befehle
fassung_text() {   # $1 Containername -> "Version, Commit, gebaut" oder Grund
  local body
  if ! laeuft "$1"; then echo "läuft nicht"; return 0; fi
  if [ "$1" = "$C_ALT" ]; then echo "läuft (Fassung vor der Umstellung, ohne /version.json)"; return 0; fi
  if body="$(version_von_container "$1")" && parse_version "$body"; then echo "Version $P_VERSION, Commit ${P_COMMIT:0:12}, gebaut $P_GEBAUT, sauber $P_SAUBER"
  else echo "läuft, antwortet nicht gültig"; fi
}

eintrag_von() {   # $1 Farbe -> JSON-Eintrag im Format von stand.json (komponenten.ui.aktiv)
  local body seit="$2"
  if ! { body="$(version_von_container "$(host_von "$1")")" && parse_version "$body"; }; then
    fehler "Farbe $1 antwortet nicht mit einer gültigen /version.json."
  fi
  [[ "$P_COMMIT" =~ $COMMIT_RE ]] || fehler "Farbe $1 trägt keine Commit-ID ($P_COMMIT): kein Eintrag für den Notanker möglich."
  [ "$P_SAUBER" = true ] || fehler "Farbe $1 wurde nicht aus unverändertem Git-Stand gebaut: kein Eintrag für den Notanker."
  printf '{"version":"v%s","commit":"%s","ziel":"%s","seit":"%s"}\n' "$P_VERSION" "$P_COMMIT" "$1" "$seit"
}

cmd_status() {
  lies_konfig; lies_zustand
  if [ "$JSON" = ja ]; then
    local ver="" com=""
    if [[ "$LIVE" =~ ^(blau|gruen|alt)$ ]] && laeuft "$(host_von "$LIVE")" && body="$(version_von_container "$(host_von "$LIVE")")" && parse_version "$body"; then
      ver="v$P_VERSION"; com="$P_COMMIT"
    fi
    printf '{"live":"%s","vorige":"%s","seit":"%s","version":"%s","commit":"%s"}\n' "${LIVE:-leer}" "${VORIGE:-leer}" "$SEIT" "$ver" "$com"
    return 0
  fi
  echo "== WAMO-Oberfläche (blau/grün)"
  if [ -z "$LIVE" ]; then echo "  Include-Datei fehlt: noch nicht eingerichtet (wamo-ui-einrichten.sh caddy)"; else
    echo "  live: $LIVE seit $SEIT, vorige: $VORIGE"
  fi
  local f
  for f in blau gruen; do echo "  $f ($(host_von "$f")): $(fassung_text "$(host_von "$f")")"; done
  if existiert "$C_ALT"; then echo "  alt ($C_ALT, Container vor der Umstellung): $(laeuft "$C_ALT" && echo läuft || echo gestoppt)"; fi
  caddy_laeuft && echo "  Caddy ($C_CADDY): läuft" || echo "  Caddy ($C_CADDY): läuft nicht"
  if body="$(public_holen)" && parse_version "$body"; then echo "  öffentlich $WAMO_UI_URL/version.json: Version $P_VERSION, Commit ${P_COMMIT:0:12}"
  else echo "  öffentlich $WAMO_UI_URL/version.json: keine gültige Antwort"; fi
}

cmd_eintrag() {
  lies_konfig; lies_zustand
  [[ "$LIVE" =~ ^(blau|gruen|alt)$ ]] || fehler "Keine live gesetzte Farbe."
  [ "$LIVE" != alt ] || fehler "Live ist noch der Container vor der Umstellung (alt): kein Eintrag für den Notanker."
  caddy_laeuft || fehler "$C_CADDY läuft nicht."
  eintrag_von "$LIVE" "$SEIT"
}

cmd_include_anlegen() {
  schloss
  lies_zustand
  if [ -n "$LIVE" ]; then echo "  Include-Datei vorhanden: live $LIVE, vorige $VORIGE"; return 0; fi
  [ -d "$CADDY_DIR/etc/kern" ] || fehler "$CADDY_DIR/etc/kern fehlt (zuerst wamo-tls-einrichten.sh)."
  [ ! -L "$INC" ] || fehler "$INC ist ein symbolischer Link: abgelehnt."
  local live=keine tmp
  if existiert "$C_ALT"; then live=alt; fi    # Umstellung: der bisherige Container bleibt zunächst live
  tmp="$(mktemp "$INC.neu.XXXXXX")"
  include_text "$live" keine "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp"
  chmod 644 "$tmp"
  if [ "$(id -u)" = 0 ]; then chown -h "$(stat -c '%u:%g' "$CADDY_DIR/etc/kern")" "$tmp"; fi
  mv -f "$tmp" "$INC"
  echo "  Include-Datei angelegt: live $live"
}

schloss() {
  install -d -m 755 "$BASIS"
  [ ! -L "$LOCK" ] || fehler "$LOCK ist ein symbolischer Link: abgelehnt."
  exec 9>"$LOCK"
  # Legt root (Notanker) die Sperrdatei an, gehört sie danach dem Besitzer des Ordners: sonst könnte wamocon sie nicht mehr öffnen
  if [ "$(id -u)" = 0 ]; then chown -h "$(stat -c '%u:%g' "$BASIS")" "$LOCK"; fi
  flock -n 9 || fehler "Ein anderer Schaltvorgang läuft gerade ($LOCK)."
}

cmd_bauen() {
  if [ "$TROCKEN" != ja ]; then schloss; fi   # erst sperren, dann den Zustand lesen: sonst entscheidet ein veralteter Zustand
  lies_konfig; lies_zustand
  [ -n "$LIVE" ] || fehler "Include-Datei fehlt (zuerst wamo-ui-einrichten.sh caddy)."
  local ziel
  ziel="$(naechste_farbe)"
  if [ "$TROCKEN" = ja ]; then
    bau_angaben
    echo "Trockenlauf: baut $BILD:$(archiv_tag) als Farbe $ziel (Version $BAU_VERSION, Commit $BAU_COMMIT) und startet $(host_von "$ziel"); live bleibt $LIVE."
    return 0
  fi
  caddy_laeuft || fehler "$C_CADDY läuft nicht."
  bauen_und_starten "$ziel"
  bereit_warten "$(host_von "$ziel")" "$ERW_VERSION" "$ERW_COMMIT" "$BEREIT_S" || { farbe_anhalten "$ziel"; exit 3; }
  fassung_pruefen "$(host_von "$ziel")" || { farbe_anhalten "$ziel"; exit 3; }
  echo "BEREIT farbe=$ziel version=$ERW_VERSION commit=$ERW_COMMIT"
}

cmd_umschalten() {
  if [ "$TROCKEN" != ja ]; then schloss; fi
  lies_konfig; lies_zustand
  [ -n "$LIVE" ] || fehler "Include-Datei fehlt (zuerst wamo-ui-einrichten.sh caddy)."
  local ziel alt_live alt_vorige host
  ziel="$(naechste_farbe)"; alt_live="$LIVE"; alt_vorige="$VORIGE"; host="$(host_von "$ziel")"
  if [ "$TROCKEN" = ja ]; then
    echo "Trockenlauf: live $LIVE (vorige $VORIGE) -> $ziel"
    if [ "$OHNE_BAU" = ja ]; then
      echo "  1 kein Bau (--ohne-bau): $host muss die gewünschte Fassung schon tragen"
    else
      bau_angaben
      echo "  1 baut $BILD:$(archiv_tag) (Version $BAU_VERSION, Commit $BAU_COMMIT) als Farbe $ziel und startet $host"
    fi
    echo "  2 prüft $host: /version.json, Seite, Header, CSP mit Anmeldung und Kern, Kern antwortet 401"
    echo "  3 tauscht $INC (live $ziel, vorige $LIVE), prüft vorher die ganze Konfiguration, lädt Caddy neu"
    echo "  4 wartet auf die neue Fassung über $WAMO_UI_URL, dann Bewährung $BEWAEHRUNG Sekunden (Fehlerquote $FEHLERQUOTE Prozent)"
    echo "  5 bei einem Fehler danach: automatischer Rückweg auf $LIVE"
    return 0
  fi
  caddy_laeuft || fehler "$C_CADDY läuft nicht."
  echo "== Umschalten: live $LIVE (vorige $VORIGE), neue Farbe $ziel"
  local t0
  t0="$(zeit)"
  echo "ZEIT start=$t0"
  if [ "$OHNE_BAU" = ja ]; then
    laeuft "$host" || fehler "Farbe $ziel läuft nicht (zuerst: $0 bauen)."
    local body
    if ! { body="$(version_von_container "$host")" && parse_version "$body"; }; then
      fehler "$host antwortet nicht mit einer gültigen /version.json."
    fi
    ERW_VERSION="$P_VERSION"; ERW_COMMIT="$P_COMMIT"
    if [ -n "$COMMIT_OPT" ] && [ "$COMMIT_OPT" != "$ERW_COMMIT" ]; then
      fehler "$host trägt Commit $ERW_COMMIT, verlangt war $COMMIT_OPT."
    fi
  else
    bauen_und_starten "$ziel"
  fi
  echo "== Prüfung des neuen Containers $host"
  if ! { bereit_warten "$host" "$ERW_VERSION" "$ERW_COMMIT" "$BEREIT_S" && fassung_pruefen "$host" && kern_pruefen; }; then
    farbe_anhalten "$ziel"
    echo "Prüfung fehlgeschlagen, nichts umgeschaltet ($alt_live bleibt live)." >&2
    exit 3
  fi
  echo "ZEIT geprueft=$(zeit)"
  echo "== Caddy umschalten auf $ziel"
  echo "ZEIT schalten_beginn=$(zeit)"
  if ! include_setzen "$ziel" "$alt_live"; then
    farbe_anhalten "$ziel"
    echo "Umschalten fehlgeschlagen, $alt_live bleibt live." >&2
    exit 3
  fi
  echo "ZEIT schalten_ende=$(zeit)"
  if wirkung_abwarten "$ERW_VERSION" "$ERW_COMMIT" 20; then
    echo "ZEIT wirkung=$(zeit)"
    echo "  Die neue Fassung antwortet über $WAMO_UI_URL"
  else
    echo "  Die neue Fassung antwortet nicht über $WAMO_UI_URL" >&2
    auto_rueckweg "$alt_live" "$ziel" || exit 5
    exit 4
  fi
  echo "== Bewährung ($BEWAEHRUNG Sekunden, Fehlerquote höchstens $FEHLERQUOTE Prozent)"
  if ! bewaehren "$ERW_VERSION" "$ERW_COMMIT" "$BEWAEHRUNG" "$FEHLERQUOTE"; then
    auto_rueckweg "$alt_live" "$ziel" || exit 5
    exit 4
  fi
  echo "ZEIT bewaehrt=$(zeit)"
  # Aufräumen: Der Container vor der Umstellung (alt) wird abgeräumt, sobald er nicht mehr die vorige Farbe ist.
  if [ "$alt_vorige" = alt ] && existiert "$C_ALT"; then
    docker rm -f "$C_ALT" >/dev/null 2>&1 && echo "  Container $C_ALT (vor der Umstellung) abgeräumt"
  fi
  alte_bilder_entfernen
  echo "Die Farbe $ziel ist live ($alt_live läuft als vorige weiter)."
  lies_zustand
  # Eintrag "letzte gute Version" für den Notanker (Format von stand.json), nur für einen sauberen Stand mit Commit-ID
  local zeile
  if [ "$LIVE" = "$ziel" ] && zeile="$(eintrag_von "$ziel" "$SEIT" 2>&1)"; then
    echo "EINTRAG $zeile"
  else
    echo "Hinweis: kein Eintrag für den Notanker (${zeile:-live passt nicht})" >&2
  fi
}

cmd_zurueck() {
  if [ "$TROCKEN" != ja ]; then schloss; fi
  lies_konfig; lies_zustand
  [ -n "$LIVE" ] || fehler "Include-Datei fehlt."
  [[ "$VORIGE" =~ ^(blau|gruen|alt)$ ]] || { echo "Es gibt keine vorige Fassung (live $LIVE, vorige $VORIGE)." >&2; exit 6; }
  local host_v erw_ver erw_com body
  host_v="$(host_von "$VORIGE")"
  if [ "$TROCKEN" = ja ]; then
    echo "Trockenlauf: live $LIVE -> $VORIGE (vorige danach: $LIVE); $host_v $(laeuft "$host_v" && echo "läuft" || echo "müsste erst gestartet werden")"
    return 0
  fi
  caddy_laeuft || fehler "$C_CADDY läuft nicht."
  echo "ZEIT start=$(zeit)"
  echo "== Rückweg: live $LIVE -> $VORIGE"
  if ! laeuft "$host_v"; then
    existiert "$host_v" || { echo "  $host_v gibt es nicht mehr: kein schneller Rückweg ($0 anker-rueckweg baut aus dem Archiv)." >&2; exit 6; }
    echo "  $host_v steht, wird gestartet"
    docker start "$host_v" >/dev/null || { echo "  Start fehlgeschlagen." >&2; exit 6; }
  fi
  # Erwartet wird, was die vorige Farbe selbst meldet; ein Container, der nicht antwortet, wird nicht live gesetzt.
  # Der Container der früheren Fassung (alt) hat keine /version.json: Für ihn zählt die Startseite.
  local ende=$((SECONDS + 40)) ok=nein
  while (( SECONDS < ende )); do
    if [ "$VORIGE" = alt ]; then
      if docker exec "$C_CADDY" wget -q -O /dev/null -T 3 "http://$host_v:3000/" 2>/dev/null; then ok=ja; break; fi
    elif body="$(version_von_container "$host_v")" && parse_version "$body"; then
      ok=ja; break
    fi
    sleep 1
  done
  [ "$ok" = ja ] || { echo "  $host_v antwortet nicht: kein Rückweg." >&2; exit 6; }
  erw_ver="$P_VERSION"; erw_com="$P_COMMIT"
  local ziel="$VORIGE" bisher="$LIVE" wirkt=nein
  echo "ZEIT schalten_beginn=$(zeit)"
  include_setzen "$ziel" "$bisher" || { echo "Rückweg ließ sich nicht schalten." >&2; exit 5; }
  echo "ZEIT schalten_ende=$(zeit)"
  if [ "$ziel" = alt ]; then
    if wirkung_alt 20; then wirkt=ja; fi
    erw_ver="der früheren Fassung"; erw_com="$C_ALT"
  elif wirkung_abwarten "$erw_ver" "$erw_com" 20; then
    wirkt=ja
  fi
  if [ "$wirkt" = ja ]; then
    echo "ZEIT wirkung=$(zeit)"
    echo "Rückweg fertig: $ziel ist live (Version $erw_ver, Commit ${erw_com:0:12}), $bisher läuft als vorige weiter."
  else
    echo "Rückweg geschaltet, aber die Adresse $WAMO_UI_URL liefert die Fassung nicht: $0 status" >&2
    exit 5
  fi
}

# Schnittstelle des Notankers: rueckweg-ui <Version> <Commit> <Ziel>, alles bereits streng geprüft.
cmd_anker_rueckweg() {
  if [ "$TROCKEN" != ja ]; then schloss; fi
  lies_konfig; lies_zustand
  [ -n "$LIVE" ] || fehler "Include-Datei fehlt."
  local ver="${ARG_VERSION#v}" com="$ARG_COMMIT" ziel="$ARG_ZIEL" host archiv bereit=nein body
  host="$(host_von "$ziel")"
  archiv="$BILD:v${ver}-${com:0:12}"
  # Als Ziel des Ankers gilt nur ein sauberer Stand: Version und Commit stimmen und der Bau stammte unverändert aus Git
  fassung_passt() { local b; b="$(version_von_container "$1")" && parse_version "$b" && [ "$P_VERSION" = "$ver" ] && [ "$P_COMMIT" = "$com" ] && [ "$P_SAUBER" = true ]; }
  if [ "$LIVE" = "$ziel" ]; then
    if fassung_passt "$host"; then
      # Stand der Datei und des Containers stimmen. Liefert Caddy sie auch? Nach einem Abbruch zwischen Tausch der Datei und
      # Neuladen (etwa durch das Zeitlimit des Ankers) wäre die Buchführung sonst voreilig.
      if [ "$TROCKEN" = ja ] || wirkung_abwarten "$ver" "$com" 3; then
        echo "Schon erledigt: $ziel ist live mit v$ver ($com)."
        return 0
      fi
      echo "  Die Include-Datei nennt $ziel, Caddy liefert die Fassung aber noch nicht: Caddy wird neu geladen" >&2
      caddy_neu_laden || exit 5
      wirkung_abwarten "$ver" "$com" 20 || { echo "Caddy liefert v$ver nicht: $0 status" >&2; exit 5; }
      echo "Rückweg fertig: $ziel ist live mit v$ver ($com)."
      return 0
    fi
    echo "Das Ziel $ziel ist die live gesetzte Farbe, trägt aber nicht v$ver ($com) als sauberen Stand. Ein Neustart der aktiven Farbe würde Anfragen verlieren." >&2
    exit 6
  fi
  if laeuft "$host" && fassung_passt "$host"; then
    bereit=ja
  elif [ "$(docker image inspect -f '{{index .Config.Labels "wamo.commit"}}' "$archiv" 2>/dev/null || true)" = "$com" ]; then
    bereit=archiv
  else
    echo "Weder läuft $host mit v$ver ($com), noch gibt es das Archiv-Image $archiv." >&2
    exit 6
  fi
  if [ "$TROCKEN" = ja ]; then
    echo "Trockenlauf: live $LIVE -> $ziel (v$ver, $com); Quelle: $([ "$bereit" = ja ] && echo "$host läuft schon" || echo "Archiv $archiv, $host wird neu gestartet")"
    return 0
  fi
  caddy_laeuft || fehler "$C_CADDY läuft nicht."
  echo "ZEIT start=$(zeit)"
  if [ "$bereit" = archiv ]; then
    echo "== $host aus $archiv starten"
    docker tag "$archiv" "$BILD:$ziel"
    farbe_starten "$ziel"
    # Das Zeitlimit des Ankers für die Oberfläche beträgt 120 Sekunden: höchstens 60 für den Start
    bereit_warten "$host" "$ver" "$com" "$(( BEREIT_S < 60 ? BEREIT_S : 60 ))" || { farbe_anhalten "$ziel"; exit 6; }
  fi
  echo "ZEIT schalten_beginn=$(zeit)"
  include_setzen "$ziel" "$LIVE" || { echo "Rückweg ließ sich nicht schalten." >&2; exit 5; }
  echo "ZEIT schalten_ende=$(zeit)"
  wirkung_abwarten "$ver" "$com" 20 || { echo "Rückweg geschaltet, aber $WAMO_UI_URL liefert v$ver nicht." >&2; exit 5; }
  echo "ZEIT wirkung=$(zeit)"
  echo "Rückweg fertig: $ziel ist live mit v$ver ($com)."
}

case "$BEFEHL" in
  status) cmd_status ;;
  eintrag) cmd_eintrag ;;
  include-anlegen) cmd_include_anlegen ;;
  bauen) cmd_bauen ;;
  umschalten) cmd_umschalten ;;
  zurueck) cmd_zurueck ;;
  anker-rueckweg) cmd_anker_rueckweg ;;
esac
