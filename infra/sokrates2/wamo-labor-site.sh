#!/usr/bin/env bash
# Gemeinsame Bausteine für die Caddy-Site des Labor-Dienstes (Vorlage wamo-caddy/kern/wamo-labor.caddy). Wird mit "source" geladen, nie allein ausgeführt:
#   wamo-labor-einrichten.sh              Einrichtung der Site (feste Adressen und Adressen der Betreuer-Geräte, Prüfung, Reload)
#   wamo-labor-geraete-aktualisieren.sh   Aktualisierer der Betreuer-Geräte (installiert in /usr/local/sbin, diese Datei in /usr/local/lib/wamo-labor-geraete)
#   wamo-labor-geraete-einrichten.sh      Einrichten und Rückweg der Betreuer-Geräte
# Eine Stelle für die Einsetz-Logik: Die Liste der Adressen (Server selbst, feste Adressen aus betreuer-adressen, Adressen der Betreuer-Geräte aus dem Stand) und
# das Einsetzen in die Vorlage. Die Datei setzt kein "set", ruft nie exit selbst und braucht vom Aufrufer die Funktion fehler MELDUNG (gibt aus und beendet).
# Der Aufrufer legt bei Bedarf die Variablen ARBEIT (Wegwerf-Ordner, räumt der Aufrufer weg) und CADDY_OWNER/CADDY_GROUP (Besitzer der Caddy-Ordner) an.

# Der Server selbst: Loopback, das Gateway 172.24.0.1 von wamo-caddy_default (unter dem Caddy Verbindungen des Hosts über den Docker-Proxy sieht) und die
# eigene LAN-Adresse. Muss mit dem Schnipsel labor_intern_adressen in der Vorlage übereinstimmen (ein Test prüft das).
INTERN="127.0.0.0/8 172.24.0.1/32 192.168.178.136/32"
CADDY_PORTS="55443 55444 55445 55446"   # die Dienste hinter wamo-caddy, die ein Neuanlegen oder ein Fehler lahmlegen würde
LAN_PRAEFIX="192.168.178."              # nur Adressen aus dem Büro-LAN 192.168.178.0/24 gelten für Betreuer-Geräte (Namensauflösung nie darüber hinaus)
GERAETE_FRIST_S=43200                   # so lange (12 Stunden) bleibt die zuletzt gute Adresse eines Geräts zugelassen, das nicht mehr antwortet
MAX_NAMEN=16                            # höchstens so viele Gerätenamen in betreuer-geraete
NAME_MUSTER='^[A-Za-z0-9][A-Za-z0-9-]{0,14}$'   # ein Gerätename: 1 bis 15 Zeichen (NetBIOS-Länge), am Anfang kein Bindestrich
CADDY_OWNER="${CADDY_OWNER:-}"
CADDY_GROUP="${CADDY_GROUP:-}"

# ---------- Adressen
# Eine Adresse für die Betreuer: a.b.c.d oder a.b.c.d/n mit n von 24 bis 32, vier Zahlen ohne führende Nullen, nie 0.x, nie Loopback (gilt
# ohnehin), nie Multicast und Link-Local
adresse_gueltig() {
  local o
  [[ "$1" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})(/([0-9]{1,2}))?$ ]] || return 1
  for o in "${BASH_REMATCH[@]:1:4}"; do (( o <= 255 )) || return 1; done
  (( ${BASH_REMATCH[6]:-32} >= 24 && ${BASH_REMATCH[6]:-32} <= 32 )) || return 1
  if (( BASH_REMATCH[1] == 0 || BASH_REMATCH[1] == 127 || BASH_REMATCH[1] >= 224 )) \
    || (( BASH_REMATCH[1] == 169 && BASH_REMATCH[2] == 254 )); then return 1; fi
}

# Die Zeilen einer Adressdatei (eine Adresse oder ein Netz je Zeile, Kommentare mit #) als eine Zeile, durch Leerzeichen getrennt. Eine ungültige Zeile
# bricht ab (nie stillschweigend weglassen). Eine fehlende Datei ergibt eine leere Liste.
adressen_lesen() {
  local datei="$1" liste="" zeile wert
  if [ -r "$datei" ]; then
    while IFS= read -r zeile || [ -n "$zeile" ]; do
      wert="$(tr -d " \t\r\"'" <<<"${zeile%%#*}")"
      [ -n "$wert" ] || continue
      adresse_gueltig "$wert" || fehler "Ungültige Adresse in $datei: $wert (a.b.c.d oder a.b.c.d/n mit n von 24 bis 32)."
      liste="$liste${liste:+ }$wert"
    done < "$datei"
  fi
  printf '%s\n' "$liste"
}

# Die Teile zu einer Zeile verbinden, leere Teile fallen weg (nie ein doppeltes Leerzeichen in der Site)
liste_bilden() {
  local teil liste=""
  for teil in "$@"; do [ -z "$teil" ] || liste="$liste${liste:+ }$teil"; done
  printf '%s\n' "$liste"
}

# ---------- Betreuer-Geräte
# Die Gerätenamen aus der Datei betreuer-geraete: je Zeile ein Name, Kommentare mit #, Ausgabe in Großbuchstaben ohne Doppelte, ein Name je Zeile. Ein ungültiger
# Name bricht ab (nie stillschweigend weglassen), ebenso mehr als MAX_NAMEN Namen. Eine fehlende Datei ergibt keine Namen.
namen_lesen() {
  local datei="$1" zeile wert name nr=0 n=0 gesehen=" "
  [ -r "$datei" ] || return 0
  while IFS= read -r zeile || [ -n "$zeile" ]; do
    nr=$((nr + 1))
    wert="${zeile%%#*}"
    wert="${wert//$'\r'/}"
    wert="${wert#"${wert%%[![:space:]]*}"}"
    wert="${wert%"${wert##*[![:space:]]}"}"
    [ -n "$wert" ] || continue
    [[ "$wert" =~ $NAME_MUSTER ]] \
      || fehler "Ungültiger Gerätename in $datei, Zeile $nr: $(printf '%q' "$wert") (erlaubt: 1 bis 15 Zeichen aus A-Z, a-z, 0-9 und Bindestrich, am Anfang kein Bindestrich). Es wurde nichts geändert."
    name="${wert^^}"
    case "$gesehen" in *" $name "*) continue ;; esac
    gesehen="$gesehen$name "
    n=$((n + 1))
    printf '%s\n' "$name"
  done < "$datei"
  (( n <= MAX_NAMEN )) || fehler "Mehr als $MAX_NAMEN Gerätenamen in $datei (gefunden: $n). Es wurde nichts geändert."
}

# Der Stand der Betreuer-Geräte (betreuer-geraete.stand): je Zeile Name, Adresse im Büro-LAN und Zeitpunkt der letzten Antwort (Sekunden seit 1970). Gültige
# Zeilen kommen auf stdout, kaputte werden mit Hinweis auf stderr übersprungen (die Datei schreibt nur der Aktualisierer; nie wird eine kaputte Zeile eingesetzt).
stand_lesen() {
  local datei="$1" zeile name adr zeit rest nr=0
  [ -r "$datei" ] || return 0
  while IFS= read -r zeile || [ -n "$zeile" ]; do
    nr=$((nr + 1))
    zeile="${zeile%%#*}"
    zeile="${zeile//$'\r'/}"
    [[ "$zeile" =~ [^[:space:]] ]] || continue
    name="" adr="" zeit="" rest=""
    read -r name adr zeit rest <<<"$zeile" || true
    if [[ "$name" =~ $NAME_MUSTER ]] && [[ "$zeit" =~ ^[0-9]{1,12}$ ]] && [ -z "$rest" ] && [[ "$adr" == "$LAN_PRAEFIX"* ]] \
      && [[ "$adr" != */* ]] && adresse_gueltig "$adr"; then
      printf '%s %s %s\n' "$name" "$adr" "$((10#$zeit))"
    else
      echo "Hinweis: Zeile $nr in $datei ist ungültig und wird übersprungen." >&2
    fi
  done < "$datei"
}

# Die Adressen aus dem Stand, deren letzte Antwort höchstens GERAETE_FRIST_S zurückliegt (DATEI JETZT): in einer Zeile, sortiert, ohne Doppelte
geraete_adressen() {
  stand_lesen "$1" | awk -v jetzt="$2" -v frist="$GERAETE_FRIST_S" 'jetzt - $3 <= frist { print $2 }' \
    | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n -u | paste -sd' ' -
}

# Die Adressen für die Site: der Server selbst, die festen Adressen aus ADRESSDATEI und, wenn STANDDATEI und JETZT folgen, die Adressen der Betreuer-Geräte
betreuer_liste() {
  local fest geraete=""
  fest="$(adressen_lesen "$1")" || exit 1
  if [ -n "${2:-}" ]; then geraete="$(geraete_adressen "$2" "$3")" || exit 1; fi
  liste_bilden "$INTERN" "$fest" "$geraete"
}

# ---------- Sperre
# Die gemeinsame Sperre auf Dateikennung 9 (Datei immer <Wurzel>/run/wamo-labor-geraete/lock, den Ort kennen alle Skripte). Wer die Site oder wamo-caddy ändert
# (Aktualisierer, wamo-labor-einrichten.sh, Einrichten und Rückweg der Betreuer-Geräte, wamo-labor-rueckweg.sh), nimmt sie, damit sich nie zwei Läufe überschneiden
# und keiner eine frische Site mit altem Stand überschreibt. DATEI SEKUNDEN: 0 = nicht warten, sonst höchstens so lange. Rückgabe 1, wenn ein anderer Lauf sie hält.
sperre_nehmen() {
  mkdir -p "$(dirname "$1")"
  exec 9> "$1"
  if [ "$2" = 0 ]; then flock -n 9; else flock -w "$2" 9; fi
}
sperre_loesen() { exec 9>&-; }

# ---------- Site
# Die Vorlage (VORLAGE) mit der Adressliste (LISTE) anstelle des Platzhalters auf stdout
site_erzeugen() {
  local text
  text="$(cat "$1")"
  text="${text//__BETREUER_ADRESSEN__/$2}"
  printf '%s\n' "$text"
}

# Wegwerf-Ordner im Ordner von wamo-caddy (Docker bindet ihn ein) mit einer Kopie von etc/ (ARBEIT, der Aufrufer legt die neue Site nach
# $ARBEIT/etc/kern/wamo-labor.caddy und räumt den Ordner später weg)
arbeit_anlegen() {
  umask 077
  ARBEIT="$(mktemp -d "$1/.arbeit.XXXXXX")"
  mkdir -p "$ARBEIT/etc/kern"
  cp -a "$1/etc/." "$ARBEIT/etc/"
}

# Die ganze Konfiguration samt neuer Site in ARBEIT prüfen (caddy adapt in einem Wegwerf-Container ohne Netz), bevor etwas installiert wird. IMAGE ist das
# Caddy-Image. NET_BIND_SERVICE: Das Image versieht caddy mit dieser Fähigkeit, ohne sie im Begrenzungssatz startet das Programm nicht.
arbeit_pruefen() {
  chmod 755 "$ARBEIT" "$ARBEIT/etc" "$ARBEIT/etc/kern"
  chmod 644 "$ARBEIT/etc/kern/"* "$ARBEIT/etc/Caddyfile"
  docker run --rm --network none --cap-drop ALL --cap-add NET_BIND_SERVICE --read-only --security-opt no-new-privileges:true \
    -v "$ARBEIT/etc:/etc/caddy:ro" "$1" caddy adapt --config /etc/caddy/Caddyfile >/dev/null
}

# Das Image, mit dem wamo-caddy gerade läuft (für die Prüfung mit caddy adapt)
caddy_image_laufend() {
  docker inspect -f '{{.Config.Image}}' wamo-caddy | tr -d '\r'
}

caddy_neu_laden() { docker exec wamo-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; }

# Datei in den Ordner von wamo-caddy legen, mit dessen Besitzer (die Ordner gehören wamocon)
caddy_installieren() {
  if [ -n "$CADDY_OWNER" ]; then install -m "$1" -o "$CADDY_OWNER" -g "$CADDY_GROUP" "$2" "$3"; else install -m "$1" "$2" "$3"; fi
}

# Antwortet eine Adresse mit irgendeinem HTTP-Status (nur 000 heißt keine Antwort)? Je Adresse höchstens WARTE_RUNDEN mal WARTE_SEKUNDEN.
antwortet() {
  local url="$1" code runde runden="${WAMO_LABOR_WARTE_RUNDEN:-60}" sek="${WAMO_LABOR_WARTE_SEKUNDEN:-2}"
  [[ "$runden" =~ ^[0-9]+$ ]] || runden=60
  [[ "$sek" =~ ^[0-9]+$ ]] || sek=2
  for ((runde = 0; runde < runden; runde++)); do
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)"
    [ "$code" != 000 ] && return 0
    sleep "$sek"
  done
  return 1
}

# 55443 bis 55446 müssen antworten (nach einer Änderung an Caddy und nach jeder Rücknahme). Die Dauer ab START wird genannt: Sie ist am Server
# zu messen, vorab gibt es dafür keine Zusage.
caddy_ports_pruefen() {
  local start="$1" p fehlt=""
  for p in $CADDY_PORTS; do antwortet "https://192.168.178.136:$p/" || fehlt="$fehlt $p"; done
  if [ -n "$fehlt" ]; then
    echo "  FEHLER: Von wamo-caddy antworten die Ports$fehlt nicht (55443 bis 55446 gehören anderen Diensten, docker logs wamo-caddy)" >&2
    return 1
  fi
  echo "  Ausfalldauer: $(( $(date +%s) - start )) Sekunden (Zeit zwischen Stopp und erster Antwort aller Ports 55443 bis 55446; am Server zu messen)"
}
