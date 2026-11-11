#!/usr/bin/env bash
# WAMO-Oberfläche auf Sokrates-2: Image bauen, Container wamo-ui-blau und wamo-ui-gruen betreiben, HTTPS 55445 über
# wamo-caddy, Firewall, erlaubte Herkunft des Kerns. Als wamocon ausführen (Docker-Gruppe, Dateien in /home/wamocon), nicht
# mit sudo; für Firewall und Kern-Herkunft ruft das Skript selbst "sudo -n" auf. Wiederholbar (idempotent). Bauen, Umschalten
# und Rückweg der Farben macht wamo-ui-umschalten.sh; dieses Skript ruft es nur auf (und legt dessen Include-Datei für Caddy an).
#
# Ablage: Der Ordner liegt im Home von wamocon, nie in /tmp (dort könnte ein anderer Benutzer Dateien vorab anlegen
# oder austauschen). Vom PC aus, mit der Oberfläche als Quelle in ~/wamo-infra/ui:
#   python scripts/wamo_ui_sokrates2.py verteilen     # Quellen und diese Skripte nach ~/wamo-infra
#   python scripts/wamo_ui_sokrates2.py einrichten    # alles unten (ohne Neustart des Kerns)
# oder auf dem Server:
#   bash ~/wamo-infra/wamo-ui-einrichten.sh [alles|bauen|umschalten|caddy|firewall|herkunft|pruefen|status|zurueck] [--neustart]
#
#   alles      Firewall, Caddy, Umschalten (neue Fassung bauen und live setzen), Herkunft, Prüfung (Standard)
#   bauen      neue Fassung in der inaktiven Farbe bauen und starten, ohne umzuschalten (wamo-ui-umschalten.sh bauen)
#   umschalten bauen, prüfen, ohne Lücke umschalten, bewähren (wamo-ui-umschalten.sh umschalten). Das erste Mal löst es
#              den einzelnen Container wamo-ui der früheren Fassung ohne Lücke durch wamo-ui-blau ab
#   caddy      Site-Datei und Include-Datei erzeugen und prüfen, Compose von wamo-caddy anwenden, Caddy neu laden
#   firewall   Port 55445 in der WAMO-Firewall (wamo-firewall-einrichten.sh, per systemctl reload)
#   herkunft   Drop-in für wamo-core: PIPECAT_ALLOWED_ORIGINS um die Herkunft der Oberfläche ergänzen
#              (mit --neustart startet wamo-core neu, aber nur, wenn gerade keine Sitzung läuft; im Slot-Betrieb nie:
#              dann meldet das Skript den Hinweis, umschalten-kern zu nutzen, und endet mit Exit 0)
#   pruefen    Zertifikatskette, Header, Container-Härtung, Netz-Isolation, Ladezeit
#   status     nur anzeigen
#   zurueck    auf die vorige Farbe zurückschalten (wamo-ui-umschalten.sh zurueck)
#
# Einstellungen: /home/wamocon/wamo-ui/wamo-ui.conf (aus wamo-ui/wamo-ui.conf.beispiel, bleibt unverändert, wenn sie
# existiert). Adresse und Zertifikat stehen nur dort, nicht im Code der Oberfläche (Variante B: README, Abschnitt 12.4).
#
# Veröffentlicht wird genau ein Port, 55445 am Container wamo-caddy. Die Container wamo-ui-blau und wamo-ui-gruen haben
# keinen Port, hängen nur im internen Netz wamo-ui-netz (kein Weg nach außen) und bekommen nur öffentliche Werte als Build-Argumente
# (Adressen, Publishable Key). Der Publishable Key kommt aus /home/wamocon/supabase-wamo/.env, ohne Ausgabe; nie ein
# Schlüssel mit der Rolle service_role (wird geprüft).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASIS=/home/wamocon/wamo-ui
KONF="$BASIS/wamo-ui.conf"
CADDY_DIR=/home/wamocon/wamo-tls/caddy
CA=/home/wamocon/wamo-tls/ca/wamo-ca.crt
CADDY_IMAGE=caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d
UNIT=/etc/systemd/system/wamo-core.service
DROPIN_DIR=/etc/systemd/system/wamo-core.service.d
DROPIN="$DROPIN_DIR/wamo-ui.conf"
UI_PORT_ERLAUBT=55445
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-ui.caddy"
INCLUDE_DATEI="$CADDY_DIR/etc/kern/wamo-ui-aktiv.inc"   # legt die live gesetzte Farbe fest (wamo-ui-umschalten.sh)

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten): Der Ordner dieses Skripts, sein
# Elternordner und alles darin (auch die Quellen der Oberfläche) gehören dem Aufrufer oder root und sind weder für
# die Gruppe noch für alle beschreibbar; keine symbolischen Links.
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

fehler() { echo "$*" >&2; exit 1; }
ARBEIT=""
BAULOG=""
trap 'rm -rf "${ARBEIT:-}"; rm -f "${BAULOG:-}"' EXIT

[ "$(id -un)" = wamocon ] || fehler "Bitte als wamocon ausführen (nicht mit sudo)."
pruefe_herkunft

BEFEHL=alles
NEUSTART=nein
for arg in "$@"; do
  case "$arg" in
    alles|bauen|umschalten|caddy|firewall|herkunft|pruefen|status|zurueck) BEFEHL="$arg" ;;
    --neustart) NEUSTART=ja ;;
    *) fehler "Aufruf: $0 [alles|bauen|umschalten|caddy|firewall|herkunft|pruefen|status|zurueck] [--neustart]" ;;
  esac
done

# ---------- Einstellungen lesen und prüfen (nie sourcen)
WAMO_UI_URL=""; NEXT_PUBLIC_SUPABASE_URL=""; NEXT_PUBLIC_WAMO_KERN_BUERO=""; WAMO_UI_TLS=""; WAMO_UI_HSTS=""
NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER=""
UI_HOST=""; UI_ORIGIN=""

lies_konfig() {
  if [ ! -f "$KONF" ]; then
    [ -f "$HERE/wamo-ui/wamo-ui.conf.beispiel" ] || fehler "$HERE/wamo-ui/wamo-ui.conf.beispiel fehlt."
    install -d -m 755 "$BASIS"
    install -m 644 "$HERE/wamo-ui/wamo-ui.conf.beispiel" "$KONF"
    echo "  Einstellungen angelegt: $KONF (Vorgabe Variante A)"
  fi
  local zeile name wert
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
  local url
  for url in "$WAMO_UI_URL" "$NEXT_PUBLIC_SUPABASE_URL" "$NEXT_PUBLIC_WAMO_KERN_BUERO"; do
    [[ "$url" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] \
      || fehler "$KONF: Adresse '$url' ungültig (nur https://Host:Port, ohne Pfad)."
  done
  local besitzer modus
  read -r besitzer modus < <(stat -c '%U %a' "$KONF")
  { [ "$besitzer" = wamocon ] && (( ! (8#$modus & 8#022) )); }     || fehler "$KONF gehört nicht wamocon oder ist für Gruppe/alle beschreibbar ($besitzer, $modus)."
  UI_ORIGIN="$WAMO_UI_URL"
  UI_HOST="${WAMO_UI_URL#https://}"
  [ "${UI_HOST##*:}" = "$UI_PORT_ERLAUBT" ] && [[ "$UI_HOST" == *:* ]] \
    || fehler "$KONF: WAMO_UI_URL muss auf :$UI_PORT_ERLAUBT enden (anderer Port: Compose von wamo-caddy und Firewall anpassen)."
  UI_HOST="${UI_HOST%%:*}"
  case "$WAMO_UI_TLS" in intern|automatisch) ;; *) fehler "$KONF: WAMO_UI_TLS muss intern oder automatisch sein." ;; esac
  case "$WAMO_UI_HSTS" in ja|nein) ;; *) fehler "$KONF: WAMO_UI_HSTS muss ja oder nein sein." ;; esac
  if [ "$WAMO_UI_HSTS" = ja ] && [[ "$UI_HOST" =~ ^[0-9.]+$ ]]; then
    fehler "$KONF: HSTS gilt nur für Domainnamen, bei einer IP-Adresse ignorieren Browser den Header (WAMO_UI_HSTS=nein)."
  fi
  if [ "$WAMO_UI_TLS" = intern ] && ! [[ "$UI_HOST" =~ ^(192\.168\.178\.136|sokrates2|spark-651f\.local)$ ]]; then
    fehler "$KONF: Die WAMO-CA stellt nur Zertifikate für 192.168.178.136, sokrates2 und spark-651f.local aus (Name Constraints)."
  fi
  # Supabase und Büro-Kern gehören zur selben Domain wie die Oberfläche oder zu den Adressen der WAMO-CA; sonst käme ein
  # fremder Host in CSP und JavaScript
  local h
  for url in "$NEXT_PUBLIC_SUPABASE_URL" "$NEXT_PUBLIC_WAMO_KERN_BUERO"; do
    h="${url#https://}"; h="${h%%:*}"
    [[ "$h" == "$UI_HOST" || "$h" =~ ^(192\.168\.178\.136|sokrates2|spark-651f\.local)$ ]]       || fehler "$KONF: Host '$h' ($url) passt weder zu WAMO_UI_URL noch zu den Adressen von Sokrates-2."
  done
  [[ "$NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER" =~ ^([0-9]{1,3})?$ ]] \
    || fehler "$KONF: NEXT_PUBLIC_WAMO_ANMELDUNG_MAX_STUNDEN_BROWSER muss leer oder eine ganze Zahl sein."
}

# ---------- Hilfen
# Ruhe: keine Sitzung im Kern (keine UDP-Sockets des Prozesses, keine Journalzeile in den letzten 5 Minuten).
# Wer eine Sitzung führt, hat WebRTC-Medien auf einem UDP-Port des Kerns; ein Neustart des Kerns oder von wamo-caddy
# unterbräche das Gespräch bzw. kurz die Anmeldung.
# Liste der erlaubten Herkünfte, mit der der laufende Kern gestartet wurde (nur diese eine Variable, nie die übrige Umgebung)
kern_herkuenfte() {
  local pid
  pid="$(systemctl show wamo-core -p MainPID --value 2>/dev/null || echo 0)"
  [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" != 0 ] || return 0
  sudo -n bash -c 'tr "\0" "\n" < /proc/$1/environ | grep "^PIPECAT_ALLOWED_ORIGINS=" | cut -d= -f2-' _ "$pid" 2>/dev/null || true
}

kern_ruhig() {
  # Keine Sitzung in einer der Instanzen: die alte Unit wamo-core und die Slots wamo-core@a und wamo-core@b (im Slot-Betrieb hat die alte Unit
  # keine MainPID, die Slots tragen die Sitzungen). Läuft keine, gilt der Kern als ruhig.
  local unit pid udp log roh_ss roh_log
  for unit in wamo-core wamo-core@a wamo-core@b; do
    pid="$(systemctl show "$unit" -p MainPID --value 2>/dev/null || echo 0)"
    if [ -z "$pid" ] || [ "$pid" = 0 ]; then continue; fi
    # Fail-closed: Lässt sich der Zustand nicht lesen (kein sudo, ss oder journalctl fehlgeschlagen), gilt der Kern als nicht ruhig
    sudo -n true 2>/dev/null || { echo "  Kern nicht prüfbar: sudo -n nicht verfügbar" >&2; return 1; }
    roh_ss="$(sudo -n ss -uanpH 2>/dev/null)" || { echo "  Kern nicht prüfbar: ss fehlgeschlagen" >&2; return 1; }
    udp="$(grep -c "pid=$pid," <<<"$roh_ss" || true)"
    roh_log="$(sudo -n journalctl -u "$unit" --since '-5 min' --no-pager -q 2>/dev/null)" \
      || { echo "  Kern nicht prüfbar: journalctl fehlgeschlagen" >&2; return 1; }
    # abgewiesene Anfragen ohne Anmeldung (401) sind keine Sitzung
    log="$(grep -vE 'Zugang abgelehnt|401 Unauthorized|^$' <<<"$roh_log" | wc -l || true)"
    if [ "${udp:-0}" != 0 ] || [ "${log:-0}" != 0 ]; then
      echo "  Kern nicht ruhig ($unit): UDP-Sockets $udp, Journalzeilen der letzten 5 Minuten $log" >&2
      return 1
    fi
  done
  return 0
}

caddy_neu_laden() { docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; }

# ---------- bauen, umschalten, zurück: alles Weitere steht in wamo-ui-umschalten.sh (Farben blau und grün)
umschalter() {
  [ -f "$HERE/wamo-ui-umschalten.sh" ] || fehler "$HERE/wamo-ui-umschalten.sh fehlt (python scripts/wamo_ui_sokrates2.py verteilen)."
  bash "$HERE/wamo-ui-umschalten.sh" "$@"
}

bauen() { umschalter bauen; }

umschalten() { umschalter umschalten; }

zurueck() { umschalter zurueck; }

# Name des Containers, der gerade live ist (aus der Kopfzeile der Include-Datei); leer, wenn es noch keinen gibt
live_container() {
  local kopf
  kopf="$(sed -n '1p' "$INCLUDE_DATEI" 2>/dev/null || true)"
  case "$kopf" in
    "# live=blau "*) echo wamo-ui-blau ;;
    "# live=gruen "*) echo wamo-ui-gruen ;;
    "# live=alt "*) echo wamo-ui ;;
    *) echo "" ;;
  esac
}

# ---------- Caddy
site_erzeugen() {
  local text tls hsts
  text="$(cat "$HERE/wamo-ui/wamo-ui.caddy.vorlage")"
  if [ "$WAMO_UI_TLS" = intern ]; then tls="tls internal"; else tls=""; fi
  if [ "$WAMO_UI_HSTS" = ja ]; then hsts='Strict-Transport-Security "max-age=31536000"'; else hsts=""; fi
  text="${text//__ADRESSE__/$WAMO_UI_URL}"
  text="${text//__TLS__/$tls}"
  text="${text//__HSTS__/$hsts}"
  printf '%s\n' "$text"
}

caddy() {
  echo "== Caddy: Site $WAMO_UI_URL"
  [ -d "$CADDY_DIR/etc" ] || fehler "$CADDY_DIR fehlt (zuerst wamo-tls-einrichten.sh)."
  [ -f "$HERE/wamo-ui/wamo-ui.caddy.vorlage" ] && [ -f "$HERE/wamo-caddy/docker-compose.yml" ] \
    || fehler "Vorlage oder Compose von wamo-caddy fehlt in $HERE (verteilen --mit-firewall-und-caddy)."
  if [ -z "$(docker ps -q -f name=^wamo-caddy$)" ] && ss -tlnH "sport = :$UI_PORT_ERLAUBT" | grep -q .; then
    fehler "Port $UI_PORT_ERLAUBT ist schon belegt, Abbruch."
  fi
  sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $UI_PORT_ERLAUBT" \
    || fehler "Firewall-Regel für $UI_PORT_ERLAUBT ist nicht aktiv (zuerst der Schritt firewall)."

  # Die Site bindet die Include-Datei mit der live gesetzten Farbe ein, sie muss also vorher da sein. Beim ersten Mal bleibt
  # der bisherige einzelne Container wamo-ui live; "umschalten" löst ihn ohne Lücke durch wamo-ui-blau ab. Die Datei hat die
  # Endung .inc und wird vom Import "kern/*.caddy" nicht als Site geladen.
  umschalter include-anlegen

  umask 077
  local tmp
  tmp="$(mktemp -d "$CADDY_DIR/.arbeit.XXXXXX")"
  ARBEIT="$tmp"
  mkdir -p "$tmp/etc"
  cp -a "$CADDY_DIR/etc/." "$tmp/etc/"
  mkdir -p "$tmp/etc/kern"
  site_erzeugen > "$tmp/etc/kern/wamo-ui.caddy"
  chmod 755 "$tmp" "$tmp/etc" "$tmp/etc/kern"
  chmod 644 "$tmp/etc/kern/"* "$tmp/etc/Caddyfile"
  # NET_BIND_SERVICE: Das Image versieht caddy mit dieser Fähigkeit, ohne sie im Begrenzungssatz startet das Programm nicht
  docker run --rm --network none --cap-drop ALL --cap-add NET_BIND_SERVICE --read-only --security-opt no-new-privileges:true \
    -v "$tmp/etc:/etc/caddy:ro" "$CADDY_IMAGE" caddy adapt --config /etc/caddy/Caddyfile >/dev/null
  echo "  Konfiguration gültig"

  local compose_neu=nein
  cmp -s "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_DIR/docker-compose.yml" || compose_neu=ja
  if [ "$compose_neu" = ja ]; then
    # Neuanlage von wamo-caddy: 55443 und 55444 sind wenige Sekunden weg. Nur in einer ruhigen Minute.
    kern_ruhig || fehler "Abbruch: Gerade läuft eine Sitzung. wamo-caddy wird neu angelegt, sobald niemand spricht (Skript erneut ausführen)."
  fi
  # Rückweg bei einem Fehler: alte Site-Datei (oder keine) und altes Compose wiederherstellen, Caddy neu laden
  local site_alt="$tmp/site.vorher" compose_alt="$tmp/compose.vorher"
  if [ -f "$SITE_DATEI" ]; then cp -a "$SITE_DATEI" "$site_alt"; fi
  if [ -f "$CADDY_DIR/docker-compose.yml" ]; then cp -a "$CADDY_DIR/docker-compose.yml" "$compose_alt"; fi
  zuruecksetzen() {
    echo "  FEHLER: Caddy-Änderung wird zurückgenommen" >&2
    if [ -f "$site_alt" ]; then install -m 644 "$site_alt" "$SITE_DATEI"; else rm -f "$SITE_DATEI"; fi
    if [ "$compose_neu" = ja ] && [ -f "$compose_alt" ]; then
      install -m 644 "$compose_alt" "$CADDY_DIR/docker-compose.yml"
      ( cd "$CADDY_DIR" && docker compose up -d ) >&2 || true
    else
      caddy_neu_laden >&2 || true
    fi
    fehler "Zurückgenommen. Bitte Protokoll prüfen (docker logs wamo-caddy)."
  }
  install -m 644 "$tmp/etc/kern/wamo-ui.caddy" "$SITE_DATEI"
  if [ "$compose_neu" = ja ]; then
    install -m 644 "$HERE/wamo-caddy/docker-compose.yml" "$CADDY_DIR/docker-compose.yml"
    echo "  Compose von wamo-caddy geändert (Port $UI_PORT_ERLAUBT, Netz wamo-ui-netz): Container wird neu angelegt"
    ( cd "$CADDY_DIR" && docker compose up -d ) || zuruecksetzen
  else
    caddy_neu_laden || zuruecksetzen
    echo "  Caddy neu geladen"
  fi
  local i code=000
  for i in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 --cacert "$CA" "https://192.168.178.136:55443/auth/v1/health" || true)"
    [ "$code" != 000 ] && break
    sleep 2
  done
  echo "  55443 (API): HTTP $code"
  [ "$code" != 000 ] || zuruecksetzen
}

# ---------- Firewall
firewall() {
  echo "== Firewall: Port $UI_PORT_ERLAUBT"
  if sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $UI_PORT_ERLAUBT"; then
    echo "  Regel steht bereits"
    return 0
  fi
  grep -q "$UI_PORT_ERLAUBT" "$HERE/wamo-firewall.sh" || fehler "$HERE/wamo-firewall.sh ohne Port $UI_PORT_ERLAUBT (verteilen --mit-firewall-und-caddy)."
  sudo -n bash "$HERE/wamo-firewall-einrichten.sh" | grep -E '^(==|  )' | sed -n '1,20p'
  sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $UI_PORT_ERLAUBT" \
    || fehler "Firewall-Regel für $UI_PORT_ERLAUBT ist nicht aktiv."
  echo "  Regel aktiv (per systemctl reload, ohne Lücke)"
}

# Slot-Betrieb: läuft eine Instanz der Vorlage wamo-core@.service, gehört der Kern den Slots. Die alte Unit wamo-core.service wird dann nie
# neu gestartet (sie liefe neben den Slots); das Drop-in gilt ab dem nächsten Kern-Wechsel über wamo-core-slots.sh umschalten-kern.
slot_betrieb() {
  # Slot-Betrieb, solange eine Instanz der Vorlage in Gang ist (auch im Fenster activating beim automatischen Neustart, in dem der Dienst noch nicht als aktiv gilt)
  # oder sich der Zustand nicht sicher lesen lässt: ein Fehler beim Prüfen gilt als Slot-Betrieb (fail-closed), die alte Unit wird dann nicht
  # gestartet. Die Slot-Verweise liegen in einem Ordner root:wamo-core 0750, für wamocon nur mit sudo sichtbar.
  local s zustand
  for s in a b; do
    zustand="$(systemctl show -p ActiveState --value "wamo-core@$s" 2>/dev/null)" || return 0
    [ -n "$zustand" ] || return 0
    if grep -qE '^(active|activating|reloading|deactivating)$' <<<"$zustand"; then return 0; fi
  done
  sudo -n true 2>/dev/null || return 0
  for s in a b; do
    if sudo -n test -e "/opt/wamo-core/slots/$s" || sudo -n test -L "/opt/wamo-core/slots/$s"; then return 0; fi
  done
  return 1
}

# ---------- Herkunft des Kerns (PIPECAT_ALLOWED_ORIGINS)
herkunft() {
  echo "== Kern: erlaubte Herkunft $UI_ORIGIN"
  local basis neu laufend
  basis="$(grep -m1 '^Environment=PIPECAT_ALLOWED_ORIGINS=' "$UNIT" | cut -d= -f3- || true)"
  [ -n "$basis" ] || fehler "PIPECAT_ALLOWED_ORIGINS fehlt in $UNIT."
  case ",$basis," in
    *",$UI_ORIGIN,"*) neu="$basis" ;;
    *) neu="$basis,$UI_ORIGIN" ;;
  esac
  [[ "$neu" =~ ^[A-Za-z0-9:/.,-]+$ ]] || fehler "Liste der Herkünfte enthält unerwartete Zeichen."
  local soll
  soll="$(printf '[Service]\n# Erlaubte Browser-Herkünfte des Kerns, ergänzt um die Oberfläche (wamo-ui-einrichten.sh). Entfernen: wamo-ui-rueckweg.sh\nEnvironment=PIPECAT_ALLOWED_ORIGINS=%s\n' "$neu")"
  if [ -f "$DROPIN" ] && [ "$(sudo -n cat "$DROPIN")" = "$soll" ]; then
    echo "  Drop-in unverändert"
  else
    sudo -n install -d -m 755 -o root -g root "$DROPIN_DIR"
    printf '%s\n' "$soll" | sudo -n tee "$DROPIN" >/dev/null
    sudo -n chmod 644 "$DROPIN"
    sudo -n systemctl daemon-reload
    echo "  Drop-in $DROPIN geschrieben"
  fi
  if sudo -n grep -q '^PIPECAT_ALLOWED_ORIGINS=' /etc/wamo/core.env 2>/dev/null; then
    echo "  WARNUNG: /etc/wamo/core.env setzt PIPECAT_ALLOWED_ORIGINS selbst und hätte Vorrang vor dem Drop-in" >&2
  fi
  if slot_betrieb; then
    echo "  Slot-Betrieb: wamo-core wird hier nie neu gestartet (auch nicht mit --neustart). Die Herkunft gilt ab dem nächsten Kern-Wechsel:" \
      "umschalten-kern nutzen (sudo bash ~/wamo-infra/wamo-core-slots.sh umschalten-kern <release>, spiegelt die Drop-ins), dann bewaehren 30 und ausleiten."
    return 0
  fi
  laufend="$(kern_herkuenfte)"
  if [ "$laufend" = "$neu" ]; then
    echo "  wamo-core kennt die Herkunft bereits"
    return 0
  fi
  if [ "$NEUSTART" != ja ]; then
    echo "  OFFEN: wamo-core läuft noch mit der alten Liste. Neustart (nur ohne laufendes Gespräch):" \
      "bash ~/wamo-infra/wamo-ui-einrichten.sh herkunft --neustart"
    return 0
  fi
  if ! kern_ruhig; then
    echo "  Neustart NICHT ausgeführt: Es läuft eine Sitzung. Später erneut: bash ~/wamo-infra/wamo-ui-einrichten.sh herkunft --neustart" >&2
    return 3
  fi
  sudo -n systemctl restart wamo-core
  sleep 5
  systemctl is-active --quiet wamo-core || { sudo -n journalctl -u wamo-core -n 20 --no-pager >&2; fehler "wamo-core läuft nach dem Neustart nicht."; }
  echo "  wamo-core neu gestartet"
}

# ---------- Prüfung
http_kopf() { curl -s -D - -o /dev/null --max-time 10 --cacert "$CA" "$@"; }

pruefen() {
  echo "== Prüfung"
  local kopf code zeit ergebnis ipv6 info
  kopf="$(http_kopf "$WAMO_UI_URL/" || true)"
  code="$(head -n1 <<<"$kopf" | awk '{print $2}')"
  echo "  GET $WAMO_UI_URL/: HTTP ${code:-keine Antwort}"
  [ "$code" = 200 ] || { echo "  FEHLER: erwartet 200" >&2; return 1; }
  zeit="$(curl -s -o /dev/null -w '%{time_total} s, %{size_download} Byte, %{http_version}' --max-time 10 --cacert "$CA" "$WAMO_UI_URL/")"
  echo "  Seite: $zeit"
  grep -qi '^content-security-policy:' <<<"$kopf" || { echo "  FEHLER: keine CSP" >&2; return 1; }
  local csp
  csp="$(grep -i '^content-security-policy:' <<<"$kopf")"
  if grep -qE 'localhost|127\.0\.0\.1|:7860' <<<"$csp"; then echo "  FEHLER: CSP nennt den lokalen Kern" >&2; return 1; fi
  grep -qF "$NEXT_PUBLIC_WAMO_KERN_BUERO" <<<"$csp" && grep -qF "$NEXT_PUBLIC_SUPABASE_URL" <<<"$csp" \
    || { echo "  FEHLER: CSP ohne Anmeldung oder Büro-Kern" >&2; return 1; }
  echo "  CSP: nur eigene Seite, Anmeldung und Büro-Kern, kein lokaler Kern"
  for h in x-frame-options x-content-type-options referrer-policy permissions-policy cross-origin-opener-policy; do
    grep -qi "^$h:" <<<"$kopf" || { echo "  FEHLER: Header $h fehlt" >&2; return 1; }
  done
  if grep -qiE '^(server|x-powered-by):' <<<"$kopf"; then echo "  FEHLER: server oder x-powered-by sichtbar" >&2; return 1; fi
  echo "  Header: X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP; kein Server/X-Powered-By"
  if [ "$WAMO_UI_HSTS" = ja ]; then
    grep -qi '^strict-transport-security:' <<<"$kopf" || { echo "  FEHLER: HSTS fehlt" >&2; return 1; }
  else
    grep -qi '^strict-transport-security:' <<<"$kopf" && { echo "  FEHLER: HSTS gesetzt, aber nicht gewollt" >&2; return 1; }
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 --cacert "$CA" -X POST -d x "$WAMO_UI_URL/")"
  [ "$code" = 405 ] || { echo "  FEHLER: POST ergibt HTTP $code, erwartet 405" >&2; return 1; }
  echo "  POST abgewiesen (405)"
  if [[ "$UI_HOST" =~ ^[0-9.]+$ ]]; then
    ergebnis="$(openssl s_client -connect "$UI_HOST:$UI_PORT_ERLAUBT" -CAfile "$CA" -verify_ip "$UI_HOST" -verify_return_error -brief </dev/null 2>&1 || true)"
  else
    ergebnis="$(openssl s_client -connect "$UI_HOST:$UI_PORT_ERLAUBT" -servername "$UI_HOST" -CAfile "$CA" -verify_hostname "$UI_HOST" -verify_return_error -brief </dev/null 2>&1 || true)"
  fi
  grep -q '^Verification: OK' <<<"$ergebnis" || { echo "  TLS: Prüfung FEHLGESCHLAGEN" >&2; return 1; }
  echo "  TLS: Kette und Adresse geprüft ($(grep -m1 '^Protocol version' <<<"$ergebnis" | sed 's/^Protocol version: *//'))"
  ipv6="$(ss -tlnH "sport = :$UI_PORT_ERLAUBT" | awk '{print $4}' | grep -F '[' || true)"
  [ -z "$ipv6" ] || { echo "  FEHLER: $UI_PORT_ERLAUBT lauscht auf IPv6: $ipv6" >&2; return 1; }
  # Alle vorhandenen Container der Oberfläche (blau, grün und der einzelne der früheren Fassung) müssen gleich gehärtet sein;
  # die live gesetzte Farbe muss laufen und gesund sein.
  local live c
  live="$(live_container)"
  [ -n "$live" ] || { echo "  FEHLER: keine live gesetzte Farbe (Include-Datei $INCLUDE_DATEI fehlt oder ist ungültig)" >&2; return 1; }
  for c in wamo-ui wamo-ui-blau wamo-ui-gruen; do
    docker inspect "$c" >/dev/null 2>&1 || continue
    [ -z "$(docker port "$c" 2>/dev/null)" ] || { echo "  FEHLER: $c veröffentlicht einen Port" >&2; return 1; }
    info="$(docker inspect -f 'readonly={{.HostConfig.ReadonlyRootfs}} capdrop={{.HostConfig.CapDrop}} sec={{.HostConfig.SecurityOpt}} mem={{.HostConfig.Memory}} cpu={{.HostConfig.NanoCpus}} pids={{.HostConfig.PidsLimit}} restart={{.HostConfig.RestartPolicy.Name}} user={{.Config.User}} health={{.State.Health.Status}} netz={{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$c")"
    echo "  Container $c: $info"
    if grep -q 'readonly=true' <<<"$info" && grep -q 'capdrop=\[ALL\]' <<<"$info" && grep -q 'no-new-privileges' <<<"$info" \
      && grep -q 'user=node' <<<"$info" && grep -q 'netz=wamo-ui-netz $' <<<"$info"; then
      :
    else
      echo "  FEHLER: Härtung von $c unvollständig" >&2
      return 1
    fi
  done
  grep -q 'health=healthy' <<<"$(docker inspect -f 'health={{.State.Health.Status}}' "$live")" \
    || { echo "  FEHLER: live gesetzter Container $live ist nicht gesund" >&2; return 1; }
  echo "  live: $live"
  echo "  Netz-Isolation (aus dem Container: Gateway, Router, Internet, Host):"
  local iso
  iso="$(docker exec "$live" node -e "
const net=require('net');
const t=(h,p)=>new Promise(r=>{const s=net.connect({host:h,port:p,timeout:2000});s.on('connect',()=>{s.destroy();r('ERREICHBAR')});s.on('timeout',()=>{s.destroy();r('gesperrt')});s.on('error',()=>r('gesperrt'))});
(async()=>{for(const [h,p] of [['172.25.0.1',22],['192.168.178.1',80],['1.1.1.1',443],['192.168.178.136',55321],['192.168.178.136',22]])console.log('    '+h+':'+p+' '+await t(h,p))})()")"
  echo "$iso"
  if grep -q ERREICHBAR <<<"$iso"; then echo "  FEHLER: Container erreicht etwas außerhalb seines Netzes" >&2; return 1; fi
  # Bekannte Grenze: wamo-caddy hängt im selben Netz und nimmt auf allen Schnittstellen an (README, Abschnitt 12.5)
  local caddy_info
  caddy_info="$(docker exec "$live" node -e "
const net=require('net');
const t=(h,p)=>new Promise(r=>{const s=net.connect({host:h,port:p,timeout:2000});s.on('connect',()=>{s.destroy();r('erreichbar')});s.on('timeout',()=>{s.destroy();r('gesperrt')});s.on('error',()=>r('gesperrt'))});
Promise.all([55443,55444].map(p=>t('wamo-caddy',p))).then(a=>console.log('55443 '+a[0]+', 55444 '+a[1]))" 2>/dev/null || true)"
  echo "  Bekannte Grenze: wamo-caddy aus dem Container: ${caddy_info:-nicht prüfbar} (wie ein LAN-Gerät ohne Anmeldung)"
  echo "  Alles bestanden."
}

status() {
  echo "== Status"
  lies_konfig
  echo "  Adresse: $WAMO_UI_URL (TLS $WAMO_UI_TLS, HSTS $WAMO_UI_HSTS)"
  docker ps -a --filter 'name=^wamo-ui(-blau|-gruen)?$' --format '  Container: {{.Names}} {{.Status}} ({{.Image}})'
  docker image ls wamo-ui --format '  Image: {{.Repository}}:{{.Tag}} {{.CreatedSince}} {{.Size}}'
  [ -f "$SITE_DATEI" ] && echo "  Caddy-Site: vorhanden" || echo "  Caddy-Site: fehlt"
  [ -f "$INCLUDE_DATEI" ] && echo "  Farbe: $(sed -n '1p' "$INCLUDE_DATEI" | sed 's/^# //')" || echo "  Farbe: Include-Datei fehlt"
  sudo -n iptables -w 5 -S WAMO-FW 2>/dev/null | grep -q "ctorigdstport $UI_PORT_ERLAUBT" \
    && echo "  Firewall: Port $UI_PORT_ERLAUBT offen für Loopback und Büro-LAN" || echo "  Firewall: Regel für $UI_PORT_ERLAUBT fehlt"
  [ -f "$DROPIN" ] && echo "  Herkunft: Drop-in vorhanden" || echo "  Herkunft: Drop-in fehlt"
  echo "  Kern läuft mit PIPECAT_ALLOWED_ORIGINS=$(kern_herkuenfte)"
  kern_ruhig && echo "  Kern: ruhig (keine Sitzung)" || echo "  Kern: Sitzung aktiv oder kürzlich aktiv"
}

lies_konfig
case "$BEFEHL" in
  status) status ;;
  zurueck) zurueck ;;
  bauen) bauen ;;
  umschalten) umschalten ;;
  caddy) caddy ;;
  firewall) firewall ;;
  herkunft) herkunft ;;
  pruefen) pruefen ;;
  alles)
    firewall
    caddy
    umschalten
    rc=0
    herkunft || rc=$?
    pruefen
    [ "$rc" = 0 ] || { echo "Fertig bis auf den Neustart von wamo-core (Rückgabe $rc, siehe oben)." >&2; exit "$rc"; }
    ;;
esac
echo "Fertig."
