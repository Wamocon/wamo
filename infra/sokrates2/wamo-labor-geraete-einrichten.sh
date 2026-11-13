#!/usr/bin/env bash
# Betreuer-Geräte des Testlabors über ihren Gerätenamen zulassen (statt über feste Adressen im Router): Aktualisierer, systemd-Unit und Zeitgeber einrichten.
# Nur als root (sudo), wie die übrigen Einrichtungsskripte. Wiederholbar (idempotent). Voraussetzung: wamo-labor-einrichten.sh ist gelaufen (Site, /etc/wamo-labor).
# Abschnitt 19.1 in infra/sokrates2/README.md beschreibt Ablauf, Grenzen und was nur am Server prüfbar ist.
#
# Ablage: infra/sokrates2 im Home von wamocon (nie in /tmp). Vom PC aus (Repo-Wurzel):
#   ssh sokrates2 "rm -rf ~/wamo-infra" && scp -r infra/sokrates2 sokrates2:wamo-infra && ssh sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-geraete-einrichten.sh einrichten --trocken"
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-labor-geraete-einrichten.sh einrichten"
#   ssh sokrates2 "sudo nano /etc/wamo-labor/betreuer-geraete"          # ein Gerätename je Zeile, gilt nach höchstens einer Minute
#
#   bash wamo-labor-geraete-einrichten.sh [einrichten|status|rueckweg] [--trocken]
#
#   einrichten  (Standard) in dieser Reihenfolge: Vorprüfung, Sicherung, Installation, Aktivierung, Prüfung
#   status      zeigt Namen und Zustände (Dateien, Timer, Namensliste, Stand mit Name, Adresse und Alter), nie Token
#   rueckweg    Timer aus, Site aus betreuer-adressen allein wiederherstellen (Prüfung mit caddy adapt, caddy reload), Unit, Aktualisierer, Bibliothek und
#               Stand entfernen. Die Listen betreuer-geraete und betreuer-adressen bleiben (der Inhalt gehört dem Betreiber).
#   --trocken   Lesen und Prüfen wie sonst, aber jede Änderung (Dateien, systemctl, docker, Caddy) nur anzeigen
#
# Was einrichten tut (vor jeder Änderung eine Sicherung unter /var/backups/wamo-labor/<JJJJMMTT-HHMMSS>/ ohne Token und ohne Daten):
#   Vorprüfung   Werkzeuge (docker, flock, timeout, nmblookup, avahi-resolve, systemctl, logger), wamo-caddy läuft, die Site wamo-labor.caddy besteht, die Dateien dieses Ordners
#                sind da, betreuer-geraete (Namen, streng) und betreuer-adressen sind gültig. avahi-daemon aus ist nur eine Warnung (NetBIOS antwortet dann allein).
#   Installation /usr/local/sbin/wamo-labor-geraete-aktualisieren (root 0755), /usr/local/lib/wamo-labor-geraete/ mit der Bibliothek wamo-labor-site.sh und der Vorlage
#                wamo-labor.caddy (root 0644), /etc/systemd/system/wamo-labor-geraete.service und .timer (root 0644, der Ordner von wamo-caddy eingesetzt),
#                /etc/wamo-labor/betreuer-geraete als Vorlage mit Kommentaren, nur wenn sie fehlt.
#   Aktivierung  systemctl daemon-reload, systemctl enable --now wamo-labor-geraete.timer (30 s nach dem Start, danach jede Minute).
#   Prüfung      Ein Lauf des Aktualisierers über die Unit (systemctl start wamo-labor-geraete.service), der Timer ist aktiv, der Stand besteht, dann der Status.
#   Fehler       Scheitert ein Schritt nach der Sicherung, nimmt das Skript die Installation zurück (die vorigen Fassungen kommen aus der Sicherung zurück, was neu war, wird
#                entfernt, der Timer geht aus, wenn er vorher nicht lief). Die Listen des Betreibers bleiben.
#
# Es fasst weder den Container des Labor-Dienstes noch die Firewall noch den Kern an, nie ein Token, und startet wamo-caddy nie neu (nur caddy reload ohne Ausfall, über den Aktualisierer).
#
# Probelauf ohne root (nur für Tests): WAMO_LABOR_GERAETE_WURZEL=/pfad/zu/einem/wegwerf/ordner stellt allen Pfaden unter /etc, /usr, /var und /run diesen Ordner voran
# (WAMO_LABOR_CADDY_DIR nennt den Ordner von wamo-caddy). Die Prüfung ruft dann den installierten Aktualisierer direkt auf, statt die Unit über systemd zu starten.
# docker, systemctl, nmblookup, avahi-resolve, flock, curl und logger stammen aus dem PATH (Attrappen).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
W="${WAMO_LABOR_GERAETE_WURZEL:-}"
ETC="$W/etc/wamo-labor"
NAMEN_DATEI="$ETC/betreuer-geraete"
ZUSTAND="$W/var/lib/wamo-labor-geraete"   # StateDirectory der Unit (Stand), schreibbar für die Unit, nicht /etc/wamo-labor
STAND_DATEI="$ZUSTAND/betreuer-geraete.stand"
BETREUER_ADRESSEN="$ETC/betreuer-adressen"
AKTUALISIERER="$W/usr/local/sbin/wamo-labor-geraete-aktualisieren"
LIB="$W/usr/local/lib/wamo-labor-geraete"
UNITS="$W/etc/systemd/system"
DIENST_UNIT="$UNITS/wamo-labor-geraete.service"
TIMER_UNIT="$UNITS/wamo-labor-geraete.timer"
RUN="$W/run/wamo-labor-geraete"
SPERRE="$RUN/lock"
SICHERUNG="$W/var/backups/wamo-labor"
CADDY_DIR="${WAMO_LABOR_CADDY_DIR:-/home/wamocon/wamo-tls/caddy}"
SITE_DATEI="$CADDY_DIR/etc/kern/wamo-labor.caddy"
# Ziel und Name in der Sicherung (die Datei dort heißt <Name>.vorher): Diese Dateien installiert das Skript und nimmt es bei einem Fehler zurück
ZIELE=("$AKTUALISIERER|wamo-labor-geraete-aktualisieren" "$LIB/wamo-labor-site.sh|wamo-labor-site.sh" "$LIB/wamo-labor.caddy|wamo-labor.caddy.vorlage"
  "$DIENST_UNIT|wamo-labor-geraete.service" "$TIMER_UNIT|wamo-labor-geraete.timer")

TROCKEN=nein
BEFEHL=einrichten
ARBEIT=""
UNIT_TMP=""
SICHERUNG_ORDNER=""
CADDY_OWNER=""
CADDY_GROUP=""
UNDO=()

usage() {
  echo "Aufruf: $0 [einrichten|status|rueckweg] [--trocken]" >&2
  exit 2
}
fehler() { echo "$*" >&2; exit 1; }
# Im Trockenlauf wird eine Vorbedingung nur gemeldet (im Echtlauf bricht das Skript ab)
muss() {
  if [ "$TROCKEN" = ja ]; then echo "  WARNUNG (im Echtlauf Abbruch): $*" >&2; else fehler "$*"; fi
}
# Jede Änderung läuft über tu: im Trockenlauf wird sie nur angezeigt
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}

aufraeumen() {
  local rc=$? i
  trap - EXIT
  if [ "$rc" -ne 0 ] && [ "${#UNDO[@]}" -gt 0 ]; then
    for ((i = ${#UNDO[@]} - 1; i >= 0; i--)); do "${UNDO[$i]}" || true; done
  fi
  case "$ARBEIT" in */.arbeit.*) rm -rf "$ARBEIT" ;; esac
  [ -z "$UNIT_TMP" ] || rm -f "$UNIT_TMP"
  exit "$rc"
}
trap aufraeumen EXIT

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten): alles gehört root oder dem aufrufenden Benutzer, nichts ist
# für die Gruppe oder alle beschreibbar, keine symbolischen Links
pruefe_ordner() {
  local ordner="$1" erlaubt uid modus pfad
  erlaubt=" 0 $(id -u) ${SUDO_UID:-} "
  while read -r uid modus pfad; do
    if [[ "$erlaubt" != *" $uid "* ]] || (( 8#$modus & 8#022 )); then
      echo "Unsicherer Ablageort: $pfad (Besitzer $uid, Rechte $modus). Ordner im eigenen Home ablegen und" \
        "mit chmod -R go-w schützen." >&2
      exit 1
    fi
  done < <(stat -c '%u %a %n' "$(dirname "$ordner")"; find "$ordner" \( -type l -printf '%U 777 %p\n' \) -o -printf '%U %m %p\n')
}

# ---------- Vorprüfung
dateien_pruefen() {
  local f
  for f in wamo-labor-geraete-aktualisieren.sh wamo-labor-site.sh wamo-labor-geraete.service wamo-labor-geraete.timer wamo-caddy/kern/wamo-labor.caddy; do
    [ -f "$HERE/$f" ] || fehler "$HERE/$f fehlt (infra/sokrates2 vollständig verteilen)."
  done
  bash -n "$HERE/wamo-labor-geraete-aktualisieren.sh" || fehler "$HERE/wamo-labor-geraete-aktualisieren.sh hat einen Syntaxfehler."
}

vorpruefung() {
  echo "== Vorprüfung"
  local w namen fest plaetze
  for w in docker flock timeout nmblookup avahi-resolve systemctl logger curl awk sort paste; do
    command -v "$w" >/dev/null 2>&1 || muss "$w fehlt (gebraucht für den Aktualisierer der Betreuer-Geräte)."
  done
  dateien_pruefen
  [ -d "$ETC" ] || muss "$ETC fehlt (zuerst wamo-labor-einrichten.sh einrichten)."
  [ -n "$(docker ps -q -f name=^wamo-caddy$ 2>/dev/null || true)" ] || muss "wamo-caddy läuft nicht (zuerst wamo-tls-einrichten.sh)."
  [ -f "$SITE_DATEI" ] || muss "Die Site $SITE_DATEI fehlt (zuerst wamo-labor-einrichten.sh einrichten)."
  if ! systemctl is-active --quiet avahi-daemon 2>/dev/null; then
    echo "  WARNUNG: avahi-daemon läuft nicht, mDNS-Antworten fehlen (NetBIOS allein genügt nur für Windows-Geräte)." >&2
  fi
  plaetze="$(grep -cvE '^[[:space:]]*(#|$)' "$ETC/plaetze-adressen" 2>/dev/null || true)"
  if [ "${plaetze:-0}" -gt 0 ]; then
    echo "  WARNUNG: plaetze-adressen enthält Einträge. Die Firewall kennt keine Namen: Ein Betreuer-PC mit wechselnder Adresse erreicht 55447 nur," \
      "wenn seine Adresse oder das Büro-/24 dort steht (ohne Einträge gilt das Büro-LAN)." >&2
  fi
  namen="$(namen_lesen "$NAMEN_DATEI")"
  fest="$(adressen_lesen "$BETREUER_ADRESSEN")"
  echo "  Gerätenamen in betreuer-geraete: $(printf '%s\n' "$namen" | grep -c . || true)"
  echo "  feste Adressen in betreuer-adressen: ${fest:-keine}"
}

# ---------- Sicherung
kopie() { if [ -f "$1" ]; then cp -p "$1" "$2"; fi; }

sichern() {
  echo "== Sicherung (vor jeder Änderung)"
  local zeit n=1 z
  zeit="$(date +%Y%m%d-%H%M%S)"
  SICHERUNG_ORDNER="$SICHERUNG/$zeit"
  while [ -e "$SICHERUNG_ORDNER" ]; do n=$((n + 1)); SICHERUNG_ORDNER="$SICHERUNG/$zeit-$n"; done
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] Sicherung nach $SICHERUNG_ORDNER: Aktualisierer, Bibliothek, Vorlage, Unit, Timer, betreuer-geraete, Stand, Site"
    return 0
  fi
  mkdir -p "$SICHERUNG" "$SICHERUNG_ORDNER"
  chmod 0700 "$SICHERUNG" "$SICHERUNG_ORDNER"
  for z in "${ZIELE[@]}"; do kopie "${z%%|*}" "$SICHERUNG_ORDNER/${z##*|}.vorher"; done
  kopie "$NAMEN_DATEI" "$SICHERUNG_ORDNER/betreuer-geraete.vorher"
  kopie "$STAND_DATEI" "$SICHERUNG_ORDNER/betreuer-geraete.stand.vorher"
  kopie "$SITE_DATEI" "$SICHERUNG_ORDNER/wamo-labor.caddy.site.vorher"
  echo "  $SICHERUNG_ORDNER"
}

# ---------- Installation
# Die Unit mit dem Ordner von wamo-caddy anstelle des Platzhalters (der Ordner besteht nur aus A-Z, a-z, 0-9 und / _ . ~ -)
unit_installieren() {
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] $HERE/wamo-labor-geraete.service nach $DIENST_UNIT installieren (__CADDY_DIR__ = $CADDY_DIR)"
    return 0
  fi
  UNIT_TMP="$(mktemp)"
  sed "s|__CADDY_DIR__|$CADDY_DIR|g" "$HERE/wamo-labor-geraete.service" > "$UNIT_TMP"
  install -m 0644 "$UNIT_TMP" "$DIENST_UNIT"
  rm -f "$UNIT_TMP"
  UNIT_TMP=""
}

# Eine Datei mit Kommentaren, nur wenn sie fehlt (der Inhalt gehört dem Betreiber und bleibt unberührt)
namensliste_vorlage() {
  if [ "$TROCKEN" = ja ]; then echo "  [trocken] $NAMEN_DATEI (Vorlage mit Kommentaren), falls sie fehlt"; return 0; fi
  if [ -f "$NAMEN_DATEI" ]; then echo "  betreuer-geraete vorhanden (unverändert)"; return 0; fi
  cat > "$NAMEN_DATEI" <<'VORLAGE'
# Betreuer-Geräte des Testlabors, die /betreuer und /api/betreuer/ über ihren Gerätenamen erreichen (statt über feste Adressen in betreuer-adressen).
# Ein Gerätename je Zeile (der Computername, 1 bis 15 Zeichen aus A-Z, a-z, 0-9 und Bindestrich, am Anfang kein Bindestrich). Kommentare beginnen mit #.
# Der Aktualisierer (wamo-labor-geraete.timer) löst die Namen jede Minute über NetBIOS und mDNS auf und lässt die gefundenen Adressen im Büro-LAN
# (192.168.178.0/24) zu, höchstens 4 je Name. Antwortet ein Gerät nicht (Ruhezustand), bleibt seine zuletzt bekannte Adresse höchstens 12 Stunden zugelassen.
# Eine Änderung an dieser Datei gilt nach spätestens einer Minute. Ein ungültiger Name hält den Aktualisierer an (Meldung im Journal, Kennung wamo-labor-geraete).
# Beispiel (ohne # eintragen):
# WMC-H-04-NS
VORLAGE
  chmod 0644 "$NAMEN_DATEI"
  echo "  betreuer-geraete als Vorlage angelegt"
}

# Der Besitzer der Caddy-Ordner (wamocon) für die Dateien, die dieses Skript dort ablegt
caddy_besitzer_setzen() {
  if [ -z "$W" ]; then CADDY_OWNER="$(stat -c %U "$CADDY_DIR")"; CADDY_GROUP="$(stat -c %G "$CADDY_DIR")"; fi
}

# Site und Stand aus der Sicherung zurücksetzen (der Prüflauf am Ende kann beide geändert haben), unter der gemeinsamen Sperre. Fehler hier ändern den Ausgang nicht.
undo_site_und_stand() {
  sperre_nehmen "$SPERRE" 60 || echo "  WARNUNG: Die Sperre war nicht zu bekommen (ein Lauf des Aktualisierers ist aktiv), Site und Stand werden trotzdem zurückgesetzt." >&2
  if [ -f "$SICHERUNG_ORDNER/wamo-labor.caddy.site.vorher" ] && [ -f "$SITE_DATEI" ] && ! cmp -s "$SICHERUNG_ORDNER/wamo-labor.caddy.site.vorher" "$SITE_DATEI"; then
    caddy_besitzer_setzen
    caddy_installieren 644 "$SICHERUNG_ORDNER/wamo-labor.caddy.site.vorher" "$SITE_DATEI" >&2 || true
    caddy_neu_laden >/dev/null 2>&1 || true
  fi
  if [ -f "$SICHERUNG_ORDNER/betreuer-geraete.stand.vorher" ]; then
    mkdir -p "$ZUSTAND"
    cp -p "$SICHERUNG_ORDNER/betreuer-geraete.stand.vorher" "$STAND_DATEI"
  else
    rm -f "$STAND_DATEI"
  fi
  rm -f "$ZUSTAND/betreuer-geraete.versuch"
  rmdir "$ZUSTAND" 2>/dev/null || true
  sperre_loesen
}

# Nimmt die Installation zurück: vorige Fassungen aus der Sicherung, was neu war, wird entfernt, der Timer geht aus, wenn er vorher nicht lief.
# Auch Site und Stand kommen aus der Sicherung zurück.
undo_installation() {
  local z ziel name
  echo "  FEHLER: Die Installation wird zurückgenommen" >&2
  [ -f "$SICHERUNG_ORDNER/wamo-labor-geraete.timer.vorher" ] || systemctl disable --now wamo-labor-geraete.timer >&2 || true
  undo_site_und_stand
  for z in "${ZIELE[@]}"; do
    ziel="${z%%|*}"
    name="${z##*|}"
    if [ -f "$SICHERUNG_ORDNER/$name.vorher" ]; then cp -p "$SICHERUNG_ORDNER/$name.vorher" "$ziel"; else rm -f "$ziel"; fi
  done
  rmdir "$LIB" 2>/dev/null || true
  systemctl daemon-reload >&2 || true
  echo "  Zurückgenommen. Die Listen betreuer-geraete und betreuer-adressen bleiben unberührt." >&2
}

installieren() {
  echo "== Installation"
  [ "$TROCKEN" = ja ] || UNDO+=(undo_installation)
  tu install -d -m 0755 "$LIB" "$UNITS" "$ZUSTAND" "$(dirname "$AKTUALISIERER")"
  tu install -m 0755 "$HERE/wamo-labor-geraete-aktualisieren.sh" "$AKTUALISIERER"
  tu install -m 0644 "$HERE/wamo-labor-site.sh" "$LIB/wamo-labor-site.sh"
  tu install -m 0644 "$HERE/wamo-caddy/kern/wamo-labor.caddy" "$LIB/wamo-labor.caddy"
  unit_installieren
  tu install -m 0644 "$HERE/wamo-labor-geraete.timer" "$TIMER_UNIT"
  namensliste_vorlage
  [ "$TROCKEN" = ja ] || echo "  Aktualisierer, Bibliothek, Vorlage, Unit und Timer installiert"
}

aktivieren() {
  echo "== Aktivierung"
  tu systemctl daemon-reload
  tu systemctl enable wamo-labor-geraete.timer   # ohne --now: Der Timer startet erst nach dem Prüflauf (sonst liefen beide gleichzeitig)
}

# ---------- Prüfung und Status
pruefen() {
  echo "== Prüfung"
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] systemctl start wamo-labor-geraete.service (ein Lauf des Aktualisierers), dann systemctl start wamo-labor-geraete.timer, Timer aktiv, Stand vorhanden"
    return 0
  fi
  if [ -n "$W" ]; then
    "$AKTUALISIERER" || fehler "Der Probelauf des Aktualisierers ist fehlgeschlagen."
  else
    systemctl start wamo-labor-geraete.service \
      || { journalctl -u wamo-labor-geraete.service -n 20 --no-pager >&2 || true; fehler "Der Lauf des Aktualisierers (systemctl start wamo-labor-geraete.service) ist fehlgeschlagen."; }
  fi
  systemctl start wamo-labor-geraete.timer || fehler "Der Timer wamo-labor-geraete.timer ließ sich nicht starten."
  systemctl is-active --quiet wamo-labor-geraete.timer || fehler "Der Timer wamo-labor-geraete.timer läuft nicht."
  [ -f "$STAND_DATEI" ] || fehler "Der Lauf hat keinen Stand geschrieben ($STAND_DATEI)."
  echo "  Aktualisierer gelaufen, Timer aktiv, Stand vorhanden"
  status
}

datei_status() {
  if [ -f "$2" ]; then echo "  $1: vorhanden"; else echo "  $1: fehlt"; fi
}

status_stand() {
  local name adr zeit jetzt n=0
  jetzt="$(date +%s)"
  while read -r name adr zeit; do
    echo "    $name $adr (letzte Antwort vor $(( (jetzt - zeit) / 60 )) Minuten)"
    n=$((n + 1))
  done < <(stand_lesen "$STAND_DATEI")
  [ "$n" -gt 0 ] || echo "    keine Adressen im Stand"
}

status() {
  echo "== Status"
  datei_status Aktualisierer "$AKTUALISIERER"
  datei_status Bibliothek "$LIB/wamo-labor-site.sh"
  datei_status Vorlage "$LIB/wamo-labor.caddy"
  datei_status "Unit wamo-labor-geraete.service" "$DIENST_UNIT"
  datei_status "Timer wamo-labor-geraete.timer" "$TIMER_UNIT"
  echo "  Timer: $(systemctl is-enabled wamo-labor-geraete.timer 2>/dev/null || true), $(systemctl is-active wamo-labor-geraete.timer 2>/dev/null || true)"
  echo "  letzter Lauf: $(systemctl is-failed wamo-labor-geraete.service 2>/dev/null || true)"
  datei_status betreuer-geraete "$NAMEN_DATEI"
  echo "  Gerätenamen: $(namen_lesen "$NAMEN_DATEI" | paste -sd' ' - || true)"
  echo "  Stand der Betreuer-Geräte (betreuer-geraete.stand):"
  status_stand
  if [ -f "$SITE_DATEI" ]; then echo "  Caddy-Site: vorhanden"; else echo "  Caddy-Site: fehlt"; fi
}

# ---------- Rückweg
# Die gemeinsame Sperre mit dem Aktualisierer: Ein Lauf, der gerade läuft, wird abgewartet (höchstens 60 Sekunden), nie unterbrochen
sperren() {
  sperre_nehmen "$SPERRE" 60 || fehler "Ein Lauf des Aktualisierers hält die Sperre $SPERRE (nach 60 Sekunden aufgegeben): Es wurde nichts geändert, bitte in einer Minute wiederholen."
}

# Sperre lösen und die Reste im Laufzeitordner entfernen (Wegwerf-Ordner der Prüfung). Die Sperrdatei bleibt immer: Eine gelöschte Datei ließe zwei Läufe mit
# verschiedenen Dateien sperren. Die Sicherungen unter /var/backups/wamo-labor bleiben ebenfalls.
laufzeit_aufraeumen() {
  if [ "$TROCKEN" = ja ]; then echo "  [trocken] Sperre lösen, Reste in $RUN entfernen (die Sperrdatei bleibt)"; return 0; fi
  sperre_loesen
  find "$RUN" -mindepth 1 -maxdepth 1 -name '.arbeit.*' -exec rm -rf {} + 2>/dev/null || true
}

# Die Site aus der Vorlage mit dem Server selbst und den festen Adressen allein (ohne Betreuer-Geräte): prüfen, installieren, laden. Gibt 1 zurück, wenn das scheitert.
site_ohne_geraete() {
  local vorlage="$LIB/wamo-labor.caddy" liste image alt
  [ -f "$vorlage" ] || vorlage="$HERE/wamo-caddy/kern/wamo-labor.caddy"
  [ -f "$SITE_DATEI" ] || { echo "  keine Site vorhanden"; return 0; }
  liste="$(betreuer_liste "$BETREUER_ADRESSEN" "" "" weich)" || return 1   # nur die gültigen Zeilen (enger): Ein Tippfehler blockiert den Rückweg nicht
  if [ "$TROCKEN" = ja ]; then echo "  [trocken] Site aus $vorlage mit remote_ip $liste prüfen (caddy adapt), installieren, caddy reload"; return 0; fi
  caddy_besitzer_setzen
  image="$(caddy_image_laufend)" || { echo "  FEHLER: Das Image von wamo-caddy ließ sich nicht lesen." >&2; return 1; }
  arbeit_anlegen "$CADDY_DIR"
  site_erzeugen "$vorlage" "$liste" > "$ARBEIT/etc/kern/wamo-labor.caddy"
  if cmp -s "$ARBEIT/etc/kern/wamo-labor.caddy" "$SITE_DATEI"; then echo "  Die Site enthält schon nur die festen Adressen"; return 0; fi
  arbeit_pruefen "$image" || { echo "  FEHLER: Die Prüfung der Site (caddy adapt) ist fehlgeschlagen, die Site bleibt, wie sie ist." >&2; return 1; }
  alt="$ARBEIT/site.vorher"
  cp -a "$SITE_DATEI" "$alt"
  if caddy_installieren 644 "$ARBEIT/etc/kern/wamo-labor.caddy" "$SITE_DATEI" && caddy_neu_laden >/dev/null && caddy_ports_pruefen "$(date +%s)" >/dev/null; then
    echo "  Site ohne Betreuer-Geräte geladen (nur der Server selbst und betreuer-adressen)"
    return 0
  fi
  caddy_installieren 644 "$alt" "$SITE_DATEI" || true
  caddy_neu_laden >/dev/null 2>&1 || true
  echo "  FEHLER: Die Site ließ sich nicht laden, die alte Site ist wieder eingesetzt." >&2
  return 1
}

dateien_entfernen() {
  local z
  for z in "${ZIELE[@]}"; do tu rm -f "${z%%|*}"; done
  tu rm -f "$STAND_DATEI" "$STAND_DATEI".neu.* "$ZUSTAND/betreuer-geraete.versuch"
  if [ "$TROCKEN" = ja ]; then echo "  [trocken] rmdir $LIB und $ZUSTAND"; else rmdir "$LIB" "$ZUSTAND" 2>/dev/null || true; fi
  tu systemctl daemon-reload
  tu systemctl reset-failed wamo-labor-geraete.service || true
}

rueckweg() {
  local site_ok=ja
  sichern
  echo "== 1. Auf einen laufenden Lauf warten (Sperre), dann den Timer abschalten"
  [ "$TROCKEN" = ja ] || sperren   # erst die Sperre: Bekommt der Rückweg sie nicht, ist nichts geändert, auch der Timer läuft weiter
  tu systemctl disable --now wamo-labor-geraete.timer || true
  echo "== 2. Site aus betreuer-adressen allein wiederherstellen"
  site_ohne_geraete || site_ok=nein
  if [ "$site_ok" = nein ]; then
    systemctl enable --now wamo-labor-geraete.timer >&2 || true   # der Timer läuft weiter, damit der 12-Stunden-Entzug nicht stehen bleibt (die Sperre verhindert ein Rennen)
    fehler "Die Site wurde nicht zurückgesetzt. Alles bleibt für den zweiten Versuch (Aktualisierer, Bibliothek, Vorlage, Unit und Stand), der Timer läuft wieder. Nach der Behebung" \
      "wamo-labor-geraete-einrichten.sh rueckweg erneut ausführen. Bis dahin gilt die Site, wie sie ist."
  fi
  echo "== 3. Dateien entfernen (die Listen betreuer-geraete und betreuer-adressen bleiben)"
  dateien_entfernen
  laufzeit_aufraeumen
}

# ---------- Aufruf
while [ $# -gt 0 ]; do
  case "$1" in
    einrichten|status|rueckweg) BEFEHL="$1" ;;
    --trocken) TROCKEN=ja ;;
    *) usage ;;
  esac
  shift
done

if [ -n "$W" ]; then
  [[ "$W" =~ ^/[A-Za-z0-9/_.:~-]+$ ]] || fehler "WAMO_LABOR_GERAETE_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9 und / _ . : ~ - sein."
  echo "Probelauf unter $W (kein root, keine Unit über systemd)"
  [ "${WAMO_LABOR_HERKUNFT:-}" != ja ] || pruefe_ordner "$HERE"   # nur für Tests: der Herkunftsschutz auch im Probelauf
elif [ "$TROCKEN" = nein ] && [ "$BEFEHL" != status ]; then
  [ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
  pruefe_ordner "$HERE"
fi
[[ "$CADDY_DIR" =~ ^/[A-Za-z0-9/_.~-]+$ ]] || fehler "WAMO_LABOR_CADDY_DIR muss ein absoluter Ordner aus A-Z, a-z, 0-9 und / _ . ~ - sein (der Ordner kommt in die Unit)."

# Die gemeinsamen Bausteine der Site (nach dem Herkunftsschutz geladen: Ein Skript allein in einem unsicheren Ordner bricht vorher ab)
[ -f "$HERE/wamo-labor-site.sh" ] || fehler "$HERE/wamo-labor-site.sh fehlt (infra/sokrates2 vollständig verteilen)."
# shellcheck source=wamo-labor-site.sh
. "$HERE/wamo-labor-site.sh"

case "$BEFEHL" in
  status) status ;;
  rueckweg) rueckweg ;;
  einrichten)
    vorpruefung
    [ "$TROCKEN" = ja ] || sperren   # Sicherung, Installation und Aktivierung laufen unter der Sperre, der Prüflauf danach nimmt sie selbst
    sichern
    installieren
    aktivieren
    [ "$TROCKEN" = ja ] || sperre_loesen
    pruefen
    ;;
esac
echo "Fertig."
