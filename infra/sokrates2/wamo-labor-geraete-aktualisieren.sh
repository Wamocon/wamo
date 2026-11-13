#!/usr/bin/env bash
# Betreuer-Geräte des Testlabors über ihren Gerätenamen zulassen, statt über feste Adressen im Router. Installiert nach /usr/local/sbin/wamo-labor-geraete-aktualisieren
# (nur root) durch wamo-labor-geraete-einrichten.sh und von wamo-labor-geraete.timer jede Minute aufgerufen:
#   sudo /usr/local/sbin/wamo-labor-geraete-aktualisieren [--trocken]
#
# Was ein Lauf tut:
#   1. Namen und feste Adressen lesen und prüfen: /etc/wamo-labor/betreuer-geraete (ein Gerätename je Zeile, Kommentare mit #, nur 1 bis 15 Zeichen aus
#      A-Z, a-z, 0-9 und Bindestrich, am Anfang kein Bindestrich, höchstens 16 Namen) und /etc/wamo-labor/betreuer-adressen. Ein ungültiger Wert wird klar gemeldet
#      (Ausgang 1) und nie stillschweigend weggelassen. Der Entzug hängt aber nicht an gültigen Eingaben: Bei einem Fehler streicht der Lauf trotzdem abgelaufene
#      Einträge (12 Stunden) und entfernte Namen und setzt die Site neu (nur Entzug). Nur neue Zulassungen halten an; schon zugelassene Adressen, die weiter antworten, bleiben.
#   2. Sperre (flock auf /run/wamo-labor-geraete/lock, dieselbe wie in wamo-labor-einrichten.sh, wamo-labor-rueckweg.sh und wamo-labor-geraete-einrichten.sh):
#      Läuft schon ein Lauf oder ändert gerade ein Einrichten die Site, endet dieser sofort ohne Änderung.
#   3. Jeden Namen auflösen, alle Namen gleichzeitig: NetBIOS (nmblookup -- NAME) und mDNS (avahi-resolve -4 -n -- NAME.local). Die Antworten beider Quellen gelten
#      zusammen (Vereinigung), das DNS des Routers wird nie gefragt (es liefert veraltete Adressen). Es zählen nur IPv4-Adressen im Büro-LAN 192.168.178.0/24 (.1 bis .254);
#      virtuelle Adressen (172.x, Docker, WSL, VPN) fallen weg. Antwortet ein Name mit mehr als 4 Adressen, ist das verdächtig (Flut oder Täuschung): Es wird nichts Neues
#      zugelassen, der letzte Stand bleibt, und der Lauf warnt (höchstens einmal je Stunde) im Journal. Die echte Adresse wird so nie verdrängt.
#   4. Mit dem zuletzt eingesetzten Stand vergleichen (/var/lib/wamo-labor-geraete/betreuer-geraete.stand, root 0644: Name, Adresse, Zeitpunkt der letzten Antwort).
#      Antwortet ein Gerät (zum Beispiel nach dem Ruhezustand), ersetzt die neue Antwort die alten Adressen dieses Namens. Antwortet es nicht, bleibt die zuletzt gute
#      Adresse höchstens 12 Stunden nach der letzten Antwort zugelassen, danach fällt sie weg. Ein Name, der aus der Liste verschwindet, verliert seine Adressen sofort.
#      Zeitpunkte in der Zukunft (Zeitsprung, Fälschung) zählen als jetzt.
#   5. Ändert sich die Menge der Adressen, wird die Caddy-Site neu zusammengesetzt (Vorlage /usr/local/lib/wamo-labor-geraete/wamo-labor.caddy, der Server selbst, feste
#      Adressen aus betreuer-adressen, die Adressen der Geräte: dieselbe Logik wie wamo-labor-einrichten.sh, geteilt in wamo-labor-site.sh), mit caddy adapt in einem
#      Wegwerf-Container ohne Netz geprüft (Image-ID von wamo-caddy, --pull=never), installiert und mit caddy reload ohne Ausfall geladen. Danach müssen 55443 bis 55446
#      antworten. Eine neue Zulassung wartet, bis der letzte Reload mindestens 3 Minuten zurückliegt, ein reiner Entzug nie.
#      Schlägt eine Stufe fehl, bleibt die vorherige Site-Datei aktiv (bei einem Fehler nach dem Installieren kommt sie zurück und wird neu geladen), der Stand bleibt
#      unverändert, der Lauf endet mit Ausgang 1. Die nächste Minute versucht es erneut. Bleibt die Menge gleich, gibt es weder Prüfung noch Reload noch Ausgabe.
#   6. Den Stand schreiben (atomar, nur wenn sich die Menge ändert oder der letzte Schreibzeitpunkt mehr als 5 Minuten zurückliegt) und die Änderungen melden: je Zeile
#      "Gerät NAME: ADRESSE zugelassen" oder "... entfernt (Grund)". Ohne Änderung bleibt es still. Die Meldungen gehen auf stdout (unter systemd ins Journal, Kennung
#      wamo-labor-geraete) und bei einem Aufruf von Hand zusätzlich mit logger -t wamo-labor-geraete.
#
# Was dieser Lauf nie anfasst: den Container des Labor-Dienstes, die Firewall, den Kern, Token (er liest und nennt keine) und das Compose von wamo-caddy.
# Er fragt keinen Namen außerhalb der Liste, nimmt nie einen Namen als Teil eines Befehls (nur als ein geprüftes Argument nach "--") und setzt nie eine Adresse außerhalb des
# Büro-LANs ein. Werte aus den Eingabedateien stehen in Meldungen nur maskiert (printf %q) und auf 40 Zeichen gekürzt.
#
# Grenzen: Die Namensauflösung im LAN ist nicht kryptografisch gesichert. Ein fremdes Gerät im Büro-LAN könnte einen Namen vortäuschen und so die Seiten /betreuer und
# /api/betreuer/ erreichen. Der Betreuer-Token bleibt die eigentliche Absicherung. Eine neue Adresse gilt frühestens mit dem nächsten Lauf und frühestens 3 Minuten nach
# dem letzten Reload. NetBIOS und mDNS erreichen nur Geräte im selben Netz (Windows, Mac, Linux). Ein Gerät, das beides nicht beantwortet, braucht eine feste Adresse in
# betreuer-adressen. Die 12-Stunden-Frist wird nur durchgesetzt, solange die Läufe gelingen.
#
# --trocken   löst die Namen auf und zeigt, was sich ändern würde (Auflösung, Meldungen, neue Adressliste), ohne zu sperren, zu schreiben, zu laden oder zu melden.
# Ausgang: 0 ok (auch ohne Änderung und bei gehaltener Sperre), 1 Fehler (auch ein Fehler in den Eingaben), 2 falsche Aufrufform.
#
# Probelauf ohne root (nur für Tests): WAMO_LABOR_GERAETE_WURZEL=/pfad/zu/einem/wegwerf/ordner stellt allen Pfaden unter /etc, /usr, /var und /run diesen Ordner voran,
# WAMO_LABOR_GERAETE_JETZT=<Sekunden> ersetzt die Uhrzeit (Prüfung der 12-Stunden-Frist), WAMO_LABOR_CADDY_DIR den Ordner von wamo-caddy
# (Standard /home/wamocon/wamo-tls/caddy). nmblookup, avahi-resolve, docker, curl, flock und logger stammen dann aus dem PATH (Attrappen).
set -euo pipefail
umask 077
export LC_ALL=C

W="${WAMO_LABOR_GERAETE_WURZEL:-}"
ETC="$W/etc/wamo-labor"
NAMEN_DATEI="$ETC/betreuer-geraete"
BETREUER_ADRESSEN="$ETC/betreuer-adressen"
ZUSTAND="$W/var/lib/wamo-labor-geraete"   # StateDirectory der Unit: der Stand liegt nicht unter /etc/wamo-labor (dort darf die Unit nichts schreiben)
STAND_DATEI="$ZUSTAND/betreuer-geraete.stand"
LIB="$W/usr/local/lib/wamo-labor-geraete"
VORLAGE="$LIB/wamo-labor.caddy"
RUN="$W/run/wamo-labor-geraete"
SPERRE="$RUN/lock"
CADDY_DIR="${WAMO_LABOR_CADDY_DIR:-/home/wamocon/wamo-tls/caddy}"
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-labor.caddy"
MAX_JE_NAME=4          # mehr Antworten je Name sind verdächtig: nichts Neues zulassen
SUCHFRIST_S=8          # so lange darf eine Namensauflösung (je Quelle) dauern
RELOAD_ABSTAND_S=180   # neue Zulassungen: frühestens so lange nach dem letzten Reload (Entzug nie)
STAND_ABSTAND_S=300    # ohne Änderung wird der Stand höchstens alle 5 Minuten geschrieben
WARN_ABSTAND_S=3600    # dieselbe Warnung höchstens einmal je Stunde
TROCKEN=nein
EINGABE_FEHLER=nein
JETZT=""
GELADEN=0
GESCHRIEBEN=0
GEWARNT=0
WARN_NEU=nein
AUFLOESUNG=""
STAND_TMP=""
ARBEIT=""
NAMEN=()
FEST=""
ALT_ZEILEN=()
NEU=()
MELDUNGEN=()
WARNUNGEN=()
declare -A ALT=() FRISCH=() ANZAHL=()

fehler() { echo "wamo-labor-geraete: $*" >&2; exit 1; }
usage() { echo "Aufruf: $0 [--trocken]" >&2; exit 2; }
# Eine Änderung: auf stdout (unter systemd ist das das Journal) und bei einem Aufruf von Hand zusätzlich ins Journal
melde() {
  echo "$*"
  [ -n "${JOURNAL_STREAM:-}" ] || logger -t wamo-labor-geraete -- "$*" 2>/dev/null || true
}

aufraeumen() {
  local rc=$?
  trap - EXIT
  case "$ARBEIT" in */.arbeit.*) rm -rf "$ARBEIT" ;; esac
  [ -z "$AUFLOESUNG" ] || rm -rf "$AUFLOESUNG"
  [ -z "$STAND_TMP" ] || rm -f "$STAND_TMP"
  exit "$rc"
}
trap aufraeumen EXIT

# ---------- Aufruf und Umgebung
umgebung_pruefen() {
  if [ -n "$W" ]; then
    [[ "$W" =~ ^/[A-Za-z0-9/_.:~-]+$ ]] || fehler "WAMO_LABOR_GERAETE_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9 und / _ . : ~ - sein."
  elif [ "$TROCKEN" = nein ] && [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausführen." >&2
    exit 1
  fi
  JETZT="${WAMO_LABOR_GERAETE_JETZT:-$(date +%s)}"
  [[ "$JETZT" =~ ^[0-9]{1,12}$ ]] || fehler "WAMO_LABOR_GERAETE_JETZT muss eine Zahl (Sekunden seit 1970) sein."
  JETZT="$((10#$JETZT))"   # eine führende Null ist nie oktal
  [ -d "$ETC" ] || fehler "$ETC fehlt (zuerst wamo-labor-einrichten.sh einrichten)."
  [ -f "$LIB/wamo-labor-site.sh" ] || fehler "$LIB/wamo-labor-site.sh fehlt (wamo-labor-geraete-einrichten.sh einrichten)."
  [ -f "$VORLAGE" ] || fehler "$VORLAGE fehlt (wamo-labor-geraete-einrichten.sh einrichten)."
}

# ---------- Eingaben
# Weich gelesen: Ein Fehler in einer der beiden Listen setzt EINGABE_FEHLER (Meldung auf stderr, Ausgang 1 am Ende), die gültigen Zeilen gelten weiter
eingaben_lesen() {
  local roh
  roh="$(namen_lesen "$NAMEN_DATEI" weich)" || EINGABE_FEHLER=ja
  NAMEN=()
  [ -z "$roh" ] || mapfile -t NAMEN <<<"$roh"
  FEST="$(adressen_lesen "$BETREUER_ADRESSEN" weich)" || EINGABE_FEHLER=ja
}

sperren() {
  if ! sperre_nehmen "$SPERRE" 0; then
    echo "Ein anderer Lauf ist aktiv, dieser Lauf ändert nichts."
    exit 0
  fi
}

# Ein Zeitpunkt aus dem Kopf des Standes, nie in der Zukunft (ein Zeitsprung der Uhr sperrte sonst den Reload oder das Schreiben für lange Zeit)
marke_lesen() {
  local wert
  wert="$(stand_marke "$STAND_DATEI" "$1")"
  if (( wert > JETZT )); then wert="$JETZT"; fi
  printf '%s\n' "$wert"
}

# Der alte Stand: je Name die Einträge "Adresse:Zeitpunkt" (die Datei prüft stand_lesen, kaputte Zeilen meldet sie und überspringt sie). Zeitpunkte in der
# Zukunft zählen als jetzt.
stand_einlesen() {
  local name adr zeit
  ALT_ZEILEN=()
  while read -r name adr zeit; do
    name="${name^^}"
    if (( zeit > JETZT )); then zeit="$JETZT"; fi
    ALT_ZEILEN+=("$name $adr $zeit")
    ALT[$name]="${ALT[$name]:+${ALT[$name]} }$adr:$zeit"
  done < <(stand_lesen "$STAND_DATEI")
  GESCHRIEBEN="$(marke_lesen geschrieben)"
  GELADEN="$(marke_lesen geladen)"
  GEWARNT="$(marke_lesen gewarnt)"
}

# ---------- Namensauflösung
# Aus den Antworten (stdin, eine Adresse je Zeile) nur Adressen im Büro-LAN, .1 bis .254, sortiert, ohne Doppelte
lan_filtern() {
  local ip letzte
  while IFS= read -r ip; do
    [[ "$ip" == "$LAN_PRAEFIX"* ]] && [[ "$ip" != */* ]] && adresse_gueltig "$ip" || continue
    letzte="${ip##*.}"
    (( letzte >= 1 && letzte <= 254 )) || continue
    printf '%s\n' "$ip"
  done | sort -t. -k4,4n -u
}

# Die Antworten eines Namens aus beiden Quellen ("--": Der Name ist nie eine Option). Eine Quelle ohne Antwort, mit Fehler oder Zeitüberschreitung ergibt einfach nichts.
aufloesen() {
  {
    timeout "$SUCHFRIST_S" nmblookup -- "$1" 2>/dev/null | awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $1 }' || true
    timeout "$SUCHFRIST_S" avahi-resolve -4 -n -- "$1.local" 2>/dev/null | awk 'NF >= 2 && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $2 }' || true
  } | tr -d '\r' | lan_filtern
}

# Alle Namen gleichzeitig (eine Auflösung dauert bis zu zwei Fristen, viele Namen sollen den Takt von einer Minute nicht sprengen)
frisch_holen() {
  local i name gefunden
  AUFLOESUNG="$(mktemp -d)"
  for i in "${!NAMEN[@]}"; do aufloesen "${NAMEN[$i]}" > "$AUFLOESUNG/$i" & done
  wait
  for i in "${!NAMEN[@]}"; do
    name="${NAMEN[$i]}"
    mapfile -t gefunden < "$AUFLOESUNG/$i"
    ANZAHL[$name]="${#gefunden[@]}"
    FRISCH[$name]="${gefunden[*]}"
  done
}

# Die Adressen, die im alten Stand für einen Namen stehen (NAME), durch Leerzeichen getrennt, mit einem Leerzeichen am Ende
alte_adressen() {
  local e
  for e in ${ALT[$1]:-}; do printf '%s ' "${e%%:*}"; done
}

# Was die Antworten neu zulassen dürfen: Mehr als MAX_JE_NAME Antworten lassen nichts Neues zu (der letzte Stand bleibt, Warnung). Bei einem Fehler in den Eingaben
# gelten nur Adressen, die schon im alten Stand stehen (Bestätigung, nie eine neue Zulassung).
frisch_einschraenken() {
  local name adr alt neu
  WARNUNGEN=()
  for name in "${NAMEN[@]}"; do
    if [ "${ANZAHL[$name]:-0}" -gt "$MAX_JE_NAME" ]; then
      FRISCH[$name]=""
      WARNUNGEN+=("WARNUNG Gerät $name: ${ANZAHL[$name]} Antworten im Büro-LAN (mehr als $MAX_JE_NAME): Es wird nichts Neues zugelassen, der letzte Stand bleibt.")
    elif [ "$EINGABE_FEHLER" = ja ]; then
      alt=" $(alte_adressen "$name")"
      neu=""
      for adr in ${FRISCH[$name]:-}; do
        if [[ "$alt" == *" $adr "* ]]; then neu="$neu${neu:+ }$adr"; fi
      done
      FRISCH[$name]="$neu"
    fi
  done
}

# ---------- Vergleich
# Der neue Stand: Antwortet ein Name, gelten genau seine neuen Adressen. Antwortet er nicht, bleiben seine alten Adressen höchstens GERAETE_FRIST_S nach der
# letzten Antwort (der Zeitpunkt der letzten Antwort bleibt dabei unverändert).
stand_berechnen() {
  local name adr zeit eintrag
  NEU=()
  for name in "${NAMEN[@]}"; do
    if [ -n "${FRISCH[$name]:-}" ]; then
      for adr in ${FRISCH[$name]}; do NEU+=("$name $adr $JETZT"); done
    else
      for eintrag in ${ALT[$name]:-}; do
        adr="${eintrag%%:*}"
        zeit="${eintrag##*:}"
        if (( JETZT - zeit <= GERAETE_FRIST_S )); then NEU+=("$name $adr $zeit"); fi
      done
    fi
  done
}

# Die Adressen aus Zeilen "NAME ADRESSE ZEIT" (als Argumente), sortiert, ohne Doppelte, in einer Zeile
adressmenge() {
  [ "$#" -gt 0 ] || return 0
  printf '%s\n' "$@" | awk '{ print $2 }' | sort -t. -k4,4n -u | paste -sd' ' -
}

# Die Adressen eines Namens aus Zeilen "NAME ADRESSE ZEIT" (NAME, dann die Zeilen)
adressen_von() {
  local name="$1"
  shift
  [ "$#" -gt 0 ] || return 0
  printf '%s\n' "$@" | awk -v n="$name" '$1 == n { print $2 }' | sort -t. -k4,4n -u | paste -sd' ' -
}

# Enthält die neue Menge eine Adresse, die in der alten fehlt? (ja oder nein)
hinzugekommen() {
  local a
  for a in $NEUE_MENGE; do
    if [[ " $ALTE_MENGE " != *" $a "* ]]; then echo ja; return 0; fi
  done
  echo nein
}

# Je Name die Unterschiede zwischen altem und neuem Stand als Meldungen (nur Name und Adresse, nie etwas anderes)
meldungen_bilden() {
  local name a alt neu grund alle=("${NAMEN[@]}")
  MELDUNGEN=()
  for name in "${!ALT[@]}"; do case " ${alle[*]} " in *" $name "*) ;; *) alle+=("$name") ;; esac; done
  for name in "${alle[@]}"; do
    alt="$(adressen_von "$name" "${ALT_ZEILEN[@]}")"
    neu="$(adressen_von "$name" "${NEU[@]}")"
    if [[ " ${NAMEN[*]} " != *" $name "* ]]; then grund="Gerät nicht mehr in der Liste"
    elif [ -n "${FRISCH[$name]:-}" ]; then grund="ersetzt durch die neue Antwort"
    else grund="keine Antwort seit mehr als 12 Stunden"; fi
    for a in $alt; do [[ " $neu " == *" $a "* ]] || MELDUNGEN+=("Gerät $name: $a entfernt ($grund)"); done
    for a in $neu; do [[ " $alt " == *" $a "* ]] || MELDUNGEN+=("Gerät $name: $a zugelassen"); done
  done
}

# ---------- Site
# Die alte Site wieder einsetzen und neu laden. Fehler hier ändern den Ausgang nicht (der Lauf endet ohnehin mit einem Fehler), eine
# Probe der Ports sagt aber, ob die Rücknahme gegriffen hat.
site_zurueck() {
  caddy_installieren 644 "$1" "$SITE_DATEI" || echo "  FEHLER: Die alte Site ließ sich nicht wieder einsetzen ($SITE_DATEI)." >&2
  caddy_neu_laden >/dev/null 2>&1 || true
  caddy_ports_pruefen "$(date +%s)" >/dev/null \
    || echo "  FEHLER: Nach der Rücknahme antworten 55443 bis 55446 nicht, bitte sofort ansehen (docker logs wamo-caddy)." >&2
}

# Die neue Site installieren, laden und prüfen (ARBEIT liegt vor, die neue Site darin ist schon geprüft)
site_laden() {
  local alt="$ARBEIT/site.vorher" ausgabe
  cp -a "$SITE_DATEI" "$alt"
  if caddy_installieren 644 "$ARBEIT/etc/kern/wamo-labor.caddy" "$SITE_DATEI" && ausgabe="$(caddy_neu_laden 2>&1)" && caddy_ports_pruefen "$(date +%s)" >/dev/null; then
    return 0
  fi
  [ -z "${ausgabe:-}" ] || echo "$ausgabe" >&2
  site_zurueck "$alt"
  fehler "Die neue Site ließ sich nicht laden oder die Ports antworteten danach nicht: zurückgenommen, die vorherige Site ist wieder aktiv."
}

# LISTE: Adressen für remote_ip. Jede Stufe, die scheitert, lässt die vorherige Site aktiv.
site_aendern() {
  local liste="$1" image
  [ -f "$SITE_DATEI" ] || fehler "Die Site $SITE_DATEI fehlt (zuerst wamo-labor-einrichten.sh einrichten). Es wurde nichts geändert."
  [ ! -L "$SITE_DATEI" ] || fehler "Die Site $SITE_DATEI ist ein symbolischer Link (Link): Es wurde nichts geändert."
  [ -n "$(docker ps -q -f name=^wamo-caddy$ 2>/dev/null || true)" ] || fehler "wamo-caddy läuft nicht, die Site bleibt unverändert."
  image="$(caddy_image_laufend)" || fehler "Das Image von wamo-caddy ließ sich nicht lesen, die Site bleibt unverändert."
  [ -n "$image" ] || fehler "Das Image von wamo-caddy ließ sich nicht lesen, die Site bleibt unverändert."
  if [ -z "$W" ]; then CADDY_OWNER="$(stat -c %U "$CADDY_DIR")"; CADDY_GROUP="$(stat -c %G "$CADDY_DIR")"; fi
  arbeit_anlegen "$CADDY_DIR" "$RUN"   # der Wegwerf-Ordner liegt im Laufzeitordner der Unit, nicht im Baum von wamocon
  site_erzeugen "$VORLAGE" "$liste" > "$ARBEIT/etc/kern/wamo-labor.caddy"
  arbeit_pruefen "$image" || fehler "Die Prüfung der neuen Site (caddy adapt) ist fehlgeschlagen, die vorherige Site bleibt aktiv."
  site_laden
}

# Der Stand mit den Zeitpunkten im Kopf: wann zuletzt geschrieben, geladen und gewarnt wurde (atomar, root 0644)
stand_schreiben() {
  mkdir -p "$ZUSTAND"
  STAND_TMP="$STAND_DATEI.neu.$$"
  {
    echo "# WAMO Labor: zuletzt eingesetzter Stand der Betreuer-Geräte. Spalten: Gerätename, Adresse, Zeitpunkt der letzten Antwort (Sekunden seit 1970)."
    echo "# Diese Datei schreibt nur wamo-labor-geraete-aktualisieren, bitte nicht von Hand ändern (zuletzt gute Adressen bleiben höchstens 12 Stunden)."
    echo "# geschrieben: $JETZT"
    echo "# geladen: $GELADEN"
    echo "# gewarnt: $GEWARNT"
    [ "${#NEU[@]}" -eq 0 ] || printf '%s\n' "${NEU[@]}"
  } > "$STAND_TMP"
  chmod 0644 "$STAND_TMP"
  mv -f "$STAND_TMP" "$STAND_DATEI"
  STAND_TMP=""
}

# Warnungen höchstens einmal je Stunde (sonst stünde dieselbe Zeile jede Minute im Journal). WARN_NEU=ja heißt: Die Marke gewarnt muss in den Stand.
warnungen_melden() {
  local w
  WARN_NEU=nein
  [ "${#WARNUNGEN[@]}" -gt 0 ] || return 0
  if (( JETZT - GEWARNT < WARN_ABSTAND_S )); then return 0; fi
  for w in "${WARNUNGEN[@]}"; do melde "$w"; done
  GEWARNT="$JETZT"
  WARN_NEU=ja
}

# Anwenden: Eine neue Zulassung wartet, bis der letzte Reload mindestens RELOAD_ABSTAND_S zurückliegt (dann bleibt alles, wie es ist, der Stand auch), ein reiner Entzug
# nie. Der Stand wird geschrieben, wenn sich die Menge ändert, eine Warnung gemeldet wurde oder der letzte Schreibzeitpunkt mehr als STAND_ABSTAND_S zurückliegt.
anwenden() {
  local m
  if [ "$GEAENDERT" = ja ] && [ "$HINZU" = ja ] && (( JETZT - GELADEN < RELOAD_ABSTAND_S )); then return 0; fi
  if [ "$GEAENDERT" = ja ]; then
    site_aendern "$LISTE"
    GELADEN="$JETZT"
  fi
  warnungen_melden
  if [ "$GEAENDERT" = ja ] || [ "$WARN_NEU" = ja ] || (( JETZT - GESCHRIEBEN >= STAND_ABSTAND_S )); then
    stand_schreiben
    for m in "${MELDUNGEN[@]}"; do melde "$m"; done
  fi
}

trocken_zeigen() {
  local name m
  for name in "${NAMEN[@]}"; do echo "[trocken] Gerät $name: Antworten im Büro-LAN: ${ANZAHL[$name]:-0}, es gelten: ${FRISCH[$name]:-keine}"; done
  for m in "${MELDUNGEN[@]}"; do echo "[trocken] Änderung: $m"; done
  for m in "${WARNUNGEN[@]}"; do echo "[trocken] $m"; done
  if [ "$GEAENDERT" = ja ]; then
    echo "[trocken] Die Site würde neu zusammengesetzt, geprüft und geladen (neue Zulassungen frühestens $RELOAD_ABSTAND_S Sekunden nach dem letzten Reload). remote_ip $LISTE"
  else
    echo "[trocken] Die Menge der Adressen bleibt gleich: keine Prüfung, kein Reload."
  fi
}

# ---------- Aufruf
while [ $# -gt 0 ]; do
  case "$1" in
    --trocken) TROCKEN=ja ;;
    *) usage ;;
  esac
  shift
done
umgebung_pruefen
# shellcheck source=wamo-labor-site.sh
. "$LIB/wamo-labor-site.sh"
export WAMO_LABOR_WARTE_RUNDEN="${WAMO_LABOR_WARTE_RUNDEN:-5}"   # nach einem Reload ohne Ausfall genügen wenige Versuche je Port

eingaben_lesen
[ "$TROCKEN" = ja ] || sperren
stand_einlesen
frisch_holen
frisch_einschraenken
stand_berechnen
meldungen_bilden
ALTE_MENGE="$(adressmenge "${ALT_ZEILEN[@]}")"
NEUE_MENGE="$(adressmenge "${NEU[@]}")"
GEAENDERT=nein
[ "$ALTE_MENGE" = "$NEUE_MENGE" ] || GEAENDERT=ja
HINZU="$(hinzugekommen)"
LISTE="$(liste_bilden "$INTERN" "$FEST" "$NEUE_MENGE")"

if [ "$TROCKEN" = ja ]; then
  trocken_zeigen
else
  anwenden
fi
if [ "$EINGABE_FEHLER" = ja ]; then
  echo "wamo-labor-geraete: Die Eingaben sind fehlerhaft (siehe oben): Neue Zulassungen sind angehalten, der Entzug lief weiter." >&2
  exit 1
fi
exit 0
