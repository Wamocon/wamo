#!/usr/bin/env bash
# Kern-Kanal der Entwicklungsaufträge einrichten und bedienen (Paket R10, Dispatcher Version 7, Plan docs/entwicklung-im-betrieb.md).
# Der Kern des Sprachassistenten (wamo-core, Betriebsart Büro) ruft den Auftragsdispatcher über ssh mit einem eigenen Schlüssel auf,
# der nur auf diesem Server entsteht und ihn nie verlässt. Dieses Skript legt Schlüssel, Regeln und Schalter an. Es startet, stoppt
# und startet keinen Dienst neu: der Kern wechselt ausschließlich über wamo-core-slots.sh umschalten-kern.
#
# Aufrufformen (Exit-Codes: 0 ok, 1 Fehler, 2 falsche Aufrufform, 3 Vorbedingung nicht erfüllt, 4 teilweise umgesetzt):
#   wamo-job-kern-einrichten.sh schluessel [--trocken] [--ablauf JJJJMMTT] [--hostfingerabdruck SHA256:...]
#       Vorbedingungen (OpenSSH, /usr/bin/ssh, fail2ban, Dispatcher 7, sshd-Block, Ruhe), Schlüssel für wamo-core, known_hosts aus dem
#       lokalen Hostschlüssel (Abgleich gegen --hostfingerabdruck, Pflicht außer mit --trocken), Zeile in authorized_keys.d/wamo-kern,
#       kernkanal.conf mit KANAL=zu, Rauchtest installieren, Ja-Probe ping, bei Fehler Rücknahme der Zeile. --trocken schreibt nichts.
#   wamo-job-kern-einrichten.sh kanal --arten A,B --repos O/N[,O/N] [--dienste D[,D]] [--max-turns N] [--max-budget-usd X]
#   wamo-job-kern-einrichten.sh kanal zu
#       Regeln des Kern-Kanals öffnen oder schließen. Öffnen und Ändern nur ohne laufenden Kern-Auftrag, GitHub-Arten nur, wenn
#       claude --help (als wamo) --max-turns und --max-budget-usd kennt. kanal zu geht immer.
#   wamo-job-kern-einrichten.sh schalter an|aus <release> [--profil <user_id>[,<user_id>]] [--kosten-auftrag X] [--kosten-tag X]
#       Drop-in wamo-entwicklung-buero.conf der Slot-Vorlage schreiben (an) oder entfernen (aus), daemon-reload, dann
#       wamo-core-slots.sh umschalten-kern <release>. Scheitert das vor dem Umschalten, kommt das vorige Drop-in zurück. Wurde schon
#       umgeschaltet, bleibt es und das Skript endet mit Exit 4. Gleicher Stand ist ein No-op.
#   wamo-job-kern-einrichten.sh notaus-aufheben
#       Sperrdatei entfernen. Der Kanal bleibt zu (der Notaus hat KANAL=zu gesetzt und das Schlüsselpaar beiseitegelegt): erst schluessel
#       legt ein neues Paar an und setzt die Zeile neu, erst kanal öffnet. schluessel verweigert, solange die Sperrdatei besteht.
#   wamo-job-kern-einrichten.sh status
#       Resttage, Kanalzustand, Schalter je Slot (nur Namen und Schalterwert), laufende Kern-Aufträge, Zahl der Kern-Ordner, Abweichungen.
#
# Dateien (alle nur auf dem Server; der öffentliche Schlüssel des Kerns liegt nie in ~/wamo-infra):
#   /var/lib/wamo-core/.ssh/id_ed25519_wamo_jobs       wamo-core 0600 (ssh-keygen per runuser, nur wenn er fehlt)
#   /var/lib/wamo-core/.ssh/known_hosts_wamo_jobs      wamo-core 0644, eine Zeile aus dem lokalen Hostschlüssel
#   /etc/ssh/authorized_keys.d/wamo-kern                root 0644, genau eine Zeile restrict,from=...,command=... (ohne expiry-time)
#   /etc/wamo/kernkanal.conf                            root:wamo 0640, SCHLUESSEL=wert (der Dispatcher prüft Besitzer, Rechte und Werte)
#   /etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf   root 0644, der Schalter (ohne Spiegel-Marke)
#   /opt/wamo-core/bin/wamo-job-kern-rauchtest          root 0755, aus wamo_job_kern_rauchtest.py
#   /var/backups/wamo-job-kern/<JJJJMMTT-HHMMSS>/       Sicherungen (root 0700), /var/log/wamo-job-kern/aktionen.jsonl die Bedienspur
# Die PC-Datei authorized_keys.d/wamo fasst dieses Skript nie an. Jede Bedienhandlung schreibt eine Zeile ohne Werte in aktionen.jsonl und
# per logger -t wamo-job-kern ins Journal. Geheimnisse (Schlüssel, Tokens) werden nie ausgegeben.
#
#   ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh schluessel --trocken"
#   ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-einrichten.sh schluessel --ablauf <JJJJMMTT> --hostfingerabdruck <SHA256:...>"
# Probelauf in einem Wegwerf-Ordner (ohne root, ohne systemd, mit Attrappen in $WURZEL/attrappen):
#   WAMO_JOB_KERN_WURZEL=/tmp/kern-probe bash wamo-job-kern-einrichten.sh status
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_JOB_KERN_WURZEL:-}"   # nur für den Probelauf; leer = das echte System

# Standardwerte beim Anlegen von kernkanal.conf (Schnittstelle I2; der Dispatcher lehnt Werte außerhalb der Bereiche ab)
STD_KANAL=zu
STD_ABLAUF_TAGE=30
STD_MAX_TURNS=40
STD_MAX_BUDGET_USD=2.00
STD_MAX_START_STUNDE=4
STD_MAX_START_TAG=8
STD_MAX_LAUFZEIT_TAG_S=14400
STD_MAX_FOLGEN=5
STD_MIN_ABSTAND_S=60
STD_MIN_PLATZ_MB=2048
STD_MAX_VERSUCH_STUNDE=20
STD_KOSTEN_AUFTRAG_USD=3
STD_KOSTEN_TAG_USD=10

KERN_IP=192.168.178.136
APP="$WURZEL/opt/wamo"
KERNAPP="$WURZEL/opt/wamo-core"
KERN_DATEI="$WURZEL/etc/ssh/authorized_keys.d/wamo-kern"
PC_DATEI="$WURZEL/etc/ssh/authorized_keys.d/wamo"
SSHD_DROPIN="$WURZEL/etc/ssh/sshd_config.d/wamo.conf"
HOSTKEY_PUB="$WURZEL/etc/ssh/ssh_host_ed25519_key.pub"
KONFIG="$WURZEL/etc/wamo/kernkanal.conf"
NOTAUS="$WURZEL/etc/wamo/kernkanal.notaus"
DROPIN_ORDNER="$WURZEL/etc/systemd/system/wamo-core@.service.d"
DROPIN="$DROPIN_ORDNER/wamo-entwicklung-buero.conf"
DROPIN_ALT="$WURZEL/etc/systemd/system/wamo-core.service.d/wamo-entwicklung-buero.conf"
SSH_ORDNER="$WURZEL/var/lib/wamo-core/.ssh"
KEY="$SSH_ORDNER/id_ed25519_wamo_jobs"
KNOWN="$SSH_ORDNER/known_hosts_wamo_jobs"
SICHERUNG_BASIS="$WURZEL/var/backups/wamo-job-kern"
LOGORDNER="$WURZEL/var/log/wamo-job-kern"
AKTIONEN="$LOGORDNER/aktionen.jsonl"
RAUCHTEST="$KERNAPP/bin/wamo-job-kern-rauchtest"
JOBS="$WURZEL/srv/wamo/jobs"
PROJEKTE="$WURZEL/srv/wamo/projekte"
PROC="$WURZEL/proc"
WAECHTER="$APP/waechter"
NAME_RE='^v[0-9A-Za-z._-]{1,40}$'
UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
KONFIG_SCHLUESSEL=(KANAL ABLAUF ARTEN REPOS DIENSTE MAX_TURNS MAX_BUDGET_USD MAX_START_STUNDE MAX_START_TAG MAX_LAUFZEIT_TAG_S MAX_FOLGEN
  MIN_ABSTAND_S MIN_PLATZ_MB MAX_VERSUCH_STUNDE)
COREENV=/usr/local/sbin/wamo-core-env
SLOTS=(bash "$HERE/wamo-core-slots.sh")
PYTHON3=/usr/bin/python3
if [ -n "$WURZEL" ]; then   # Probelauf: Programme kommen aus $WURZEL/attrappen
  PATH="$WURZEL/attrappen:$PATH"
  COREENV=wamo-core-env
  SLOTS=(bash "$WURZEL/attrappen/wamo-core-slots.sh")
  PYTHON3="${WAMO_JOB_KERN_PYTHON:-python3}"
fi

fehler() { echo "wamo-job-kern-einrichten: $*" >&2; exit 1; }
aufruf() {
  echo "wamo-job-kern-einrichten: $*" >&2
  echo "Aufruf: schluessel [--trocken] [--ablauf JJJJMMTT] [--hostfingerabdruck SHA256:...] | kanal --arten A,B --repos O/N[,O/N]" \
    "[--dienste D] [--max-turns N] [--max-budget-usd X] | kanal zu | schalter an|aus <release> [--profil <user_id>]" \
    "[--kosten-auftrag X] [--kosten-tag X] | notaus-aufheben | status" >&2
  exit 2
}
vorbedingung() { echo "wamo-job-kern-einrichten: Vorbedingung nicht erfüllt: $*" >&2; exit 3; }
teilweise() { echo "wamo-job-kern-einrichten: teilweise umgesetzt: $*" >&2; exit 4; }

# ---------------------------------------------------------------- Bedienspur (nie Werte)
AKTION=""
aktion_schreiben() {
  mkdir -p "$LOGORDNER" 2>/dev/null || return 0
  chmod 0700 "$LOGORDNER" 2>/dev/null || true
  printf '{"t":%s,"aktion":"%s","ergebnis":"%s"}\n' "$(date +%s)" "$1" "$2" >> "$AKTIONEN" 2>/dev/null || true
  chmod 0640 "$AKTIONEN" 2>/dev/null || true
  logger -t wamo-job-kern "aktion=$1 ergebnis=$2" 2>/dev/null || true
}
abschluss() {
  local rc=$?
  if [ -n "$AKTION" ]; then aktion_schreiben "$AKTION" "$([ "$rc" = 0 ] && echo ok || echo fehler)"; fi
  return "$rc"
}
trap abschluss EXIT

# ---------------------------------------------------------------- Hilfen
SICHERUNG=""
sicherungsordner() {   # legt den Ordner dieses Laufs an (root 0700)
  if [ -z "$SICHERUNG" ]; then SICHERUNG="$SICHERUNG_BASIS/$(date -u +%Y%m%d-%H%M%S)"; fi
  mkdir -p "$SICHERUNG"
  chmod 0700 "$SICHERUNG_BASIS" "$SICHERUNG" 2>/dev/null || true
}
sichern() {   # sichert eine vorhandene Datei außerhalb des Baums von wamo-core (/etc, /opt); Geheimnisse werden nie gesichert
  [ -f "$1" ] || return 0
  sicherungsordner
  cp -p "$1" "$SICHERUNG/$(basename "$1")"
}

# Der Baum /var/lib/wamo-core/.ssh gehört dem Kern (wamo-core). Ein gekaperter Kern kann ihn durch einen Link ersetzen oder eine Temp-Datei
# vorwegnehmen: root fasst ihn deshalb nie an. Jede Dateiarbeit dort (anlegen, lesen, schreiben, umbenennen, löschen) läuft als wamo-core
# (als_kern), root schreibt nur nach /etc, /var/backups und /var/log. Im Probelauf gibt es den Wechsel des Benutzers nur mit der Attrappe runuser.
als_kern() {
  if [ -n "$WURZEL" ] && [ ! -x "$WURZEL/attrappen/runuser" ]; then "$@"
  else ( cd / && runuser -u wamo-core -- "$@" ); fi
}

ssh_baum_pruefen() {   # vor jeder Nutzung: kein Link, ein Ordner, im echten Betrieb im Besitz von wamo-core
  local d
  if [ -L "$SSH_ORDNER" ]; then vorbedingung "${SSH_ORDNER#"$WURZEL"} ist ein Link (der Kern hat den Ordner ersetzt?): nichts angefasst"; fi
  if [ -e "$SSH_ORDNER" ]; then
    [ -d "$SSH_ORDNER" ] || vorbedingung "${SSH_ORDNER#"$WURZEL"} ist kein Ordner"
    [ -n "$WURZEL" ] || [ "$(stat -c %U "$SSH_ORDNER")" = wamo-core ] || vorbedingung "${SSH_ORDNER#"$WURZEL"} gehört nicht wamo-core"
  fi
  for d in "$KEY" "$KEY.pub" "$KNOWN"; do
    if als_kern test -L "$d"; then vorbedingung "${d#"$WURZEL"} ist ein Link: nichts angefasst"; fi
  done
}

sichern_kern() {   # sichert eine Datei aus dem Baum von wamo-core: gelesen wird als wamo-core, root schreibt nur in den Sicherungsordner
  als_kern test -f "$1" || return 0
  sicherungsordner
  als_kern cat "$1" > "$SICHERUNG/$(basename "$1")"
}

schreibe_datei() {   # ziel modus besitzer; Inhalt von stdin; atomar (temporäre Datei, Rechte, dann umbenennen)
  local ziel="$1" modus="$2" besitzer="$3" tmp
  mkdir -p "$(dirname "$ziel")"
  tmp="$(mktemp "$(dirname "$ziel")/.$(basename "$ziel").XXXXXX")"
  cat > "$tmp"
  chmod "$modus" "$tmp"
  [ -n "$WURZEL" ] || chown "$besitzer" "$tmp"
  mv -f "$tmp" "$ziel"
}

gleich_wie() {   # gleich_wie <datei> <text>: hat die Datei genau diesen Inhalt?
  [ -f "$1" ] && [ "$(cat "$1")" = "$2" ]
}

heute() { date +%Y%m%d; }
tage_bis() {   # Tage von heute bis JJJJMMTT (negativ: vorbei)
  echo $(( ( $(date -d "$1" +%s) - $(date -d "$(heute)" +%s) ) / 86400 ))
}

pruefe_herkunft() {   # wie in den übrigen Einrichtungsskripten: nichts Untergeschobenes im Ordner dieses Skripts
  local erlaubt=" 0 $(id -u) ${SUDO_UID:-} " uid modus pfad
  while read -r uid modus pfad; do
    if [[ "$erlaubt" != *" $uid "* ]] || (( 8#$modus & 8#022 )); then
      echo "Unsicherer Ablageort: $pfad (Besitzer $uid, Rechte $modus). Ordner im eigenen Home ablegen und mit chmod -R go-w schützen." >&2
      exit 1
    fi
  done < <(stat -c '%u %a %n' "$(dirname "$HERE")"; find "$HERE" \( -type l -printf '%U 777 %p\n' \) -o -printf '%U %m %p\n')
}

kern_auftrag_laeuft() {   # läuft der Kern-Auftrag in diesem Ordner? flock auf .lauf (der Dispatcher hält ihn), sonst status.json
  local ordner="$1"
  if [ -f "$ordner/.lauf" ] && command -v flock >/dev/null 2>&1; then
    ! flock -n "$ordner/.lauf" true 2>/dev/null
  else
    [ -f "$ordner/status.json" ] && grep -q '"ergebnis": "läuft"' "$ordner/status.json"
  fi
}

kern_auftraege() {   # Auftragsordner mit kanal=kern, einer je Zeile
  local a
  [ -d "$JOBS" ] || return 0
  for a in "$JOBS"/*/auftrag.json; do
    [ -f "$a" ] || continue
    grep -Eq '"kanal": ?"kern"' "$a" && dirname "$a"
  done
  return 0
}

laufende_kern_auftraege() {
  local o
  while read -r o; do
    [ -n "$o" ] || continue
    if kern_auftrag_laeuft "$o"; then basename "$o"; fi
  done < <(kern_auftraege)
}

ruhe_pruefen() {   # kein laufender Kern-Auftrag, kein offener Totmannschalter des sshd-Blocks
  local lauf offen
  lauf="$(laufende_kern_auftraege | tr '\n' ' ')"
  [ -z "${lauf// /}" ] || vorbedingung "Ruhe fehlt: ein Kern-Auftrag läuft ($lauf). Erst beenden lassen oder kanal zu und notaus nutzen."
  offen="$(systemctl list-timers --all --no-legend 2>/dev/null | grep -c 'wamo-sshd-rueckfall' || true)"
  [ "${offen:-0}" = 0 ] || vorbedingung "Ruhe fehlt: ein Totmannschalter des sshd-Blocks ist offen (erst sudo touch /run/wamo-sshd-bestaetigt)."
}

# ---------------------------------------------------------------- Prüfung der Werte (Bereiche nach I2)
gueltiges_datum() {   # JJJJMMTT, echtes Datum
  [[ "$1" =~ ^[0-9]{8}$ ]] && [ "$(date -d "$1" +%Y%m%d 2>/dev/null || true)" = "$1" ]
}

pruefe_ablauf() {
  local tage
  gueltiges_datum "$1" || aufruf "--ablauf $1 ist kein Datum der Form JJJJMMTT"
  tage="$(tage_bis "$1")"
  [ "$tage" -ge 0 ] || aufruf "--ablauf liegt in der Vergangenheit"
  [ "$tage" -le 400 ] || aufruf "--ablauf liegt mehr als 400 Tage voraus"
}

pruefe_zahl() {   # name wert klein gross
  [[ "$2" =~ ^(0|[1-9][0-9]{0,8})$ ]] && [ "$2" -ge "$3" ] && [ "$2" -le "$4" ] || aufruf "$1 muss eine ganze Zahl von $3 bis $4 sein"
}

pruefe_betrag() {   # name wert klein gross: Punkt als Dezimaltrenner, höchstens zwei Stellen
  [[ "$2" =~ ^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$ ]] || aufruf "$1 muss eine Dezimalzahl mit Punkt und höchstens zwei Stellen sein"
  awk -v v="$2" -v k="$3" -v g="$4" 'BEGIN { exit !(v + 0 >= k + 0 && v + 0 <= g + 0) }' || aufruf "$1 muss zwischen $3 und $4 liegen"
}

pruefe_repos() {
  local r name n=0 gesehen=" "
  [ -n "$1" ] || return 0
  [[ "$1" =~ ^[A-Za-z0-9._/,-]+$ ]] || aufruf "--repos: nur Buchstaben, Ziffern, Punkt, Unterstrich, Bindestrich, Schrägstrich und Komma (eine Zeile)"
  local -a liste
  IFS=, read -r -a liste <<< "$1"
  for r in "${liste[@]}"; do
    n=$((n + 1))
    [[ "$r" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$ ]] || aufruf "--repos: $r ist kein Repo der Form owner/name"
    name="${r#*/}"
    case "${name,,}" in wamo|.|..|*.git) aufruf "--repos: $r ist nicht zulässig (nie das WAMO-Repo)" ;; esac
    case "$gesehen" in *" ${r,,} "*) aufruf "--repos: $r steht doppelt" ;; esac
    gesehen="$gesehen${r,,} "
  done
  [ "$n" -le 10 ] || aufruf "--repos: höchstens 10 Repos"
}

pruefe_profil() {
  # eine oder mehrere user_id (UUID, klein geschrieben), durch Komma getrennt, in einer Zeile
  local p
  local -a profile
  [[ "$1" =~ ^[0-9a-f-]{36}(,[0-9a-f-]{36})*$ ]] || aufruf "--profil: nur klein geschriebene user_id-Werte (UUID), durch Komma getrennt, in einer Zeile"
  IFS=, read -r -a profile <<< "$1"
  for p in "${profile[@]}"; do [[ "$p" =~ $UUID_RE ]] || aufruf "--profil: keine gültige user_id (UUID, klein geschrieben)"; done
}

pruefe_dienste() {
  local d
  [ -n "$1" ] || return 0
  [[ "$1" =~ ^[a-z0-9_,]+$ ]] || aufruf "--dienste: nur a-z, 0-9, Unterstrich und Komma (eine Zeile)"
  local -a liste
  IFS=, read -r -a liste <<< "$1"
  for d in "${liste[@]}"; do
    [[ "$d" =~ ^[a-z][a-z0-9_]{1,29}$ ]] || aufruf "--dienste: $d ist kein Dienstname"
    case "$d" in supabase|github|github_lesen) aufruf "--dienste: $d ist nie zulässig" ;; esac
  done
}

werkzeugkarte_py() {   # Python mit der Werkzeugkarte (Kopie in /opt/wamo/waechter); gibt auf stdout aus, was der Aufrufer fragt
  "$PYTHON3" -I -c "import sys; sys.path.insert(0, sys.argv[1]); import werkzeugkarte as w; $1" "$WAECHTER" "${@:2}"
}

pruefe_arten() {   # Liste der Arten; setzt GITHUB_ART=1, wenn eine Art eine GitHub-Stufe hat
  local a
  [ -n "$1" ] || aufruf "--arten fehlt (kanal braucht mindestens eine Art)"
  [[ "$1" =~ ^[a-z_,]+$ ]] || aufruf "--arten: nur a-z, Unterstrich und Komma (eine Zeile)"
  local -a liste
  IFS=, read -r -a liste <<< "$1"
  [ -f "$WAECHTER/werkzeugkarte.py" ] || vorbedingung "$WAECHTER/werkzeugkarte.py fehlt (wamo-job-einrichten.sh)"
  for a in "${liste[@]}"; do
    [[ "$a" =~ ^[a-z_]{2,30}$ ]] || aufruf "--arten: $a ist keine Auftragsart"
    werkzeugkarte_py 'sys.exit(0 if sys.argv[2] in w.AUFTRAGSARTEN else 1)' "$a" 2>/dev/null \
      || aufruf "--arten: $a ist keine bekannte Auftragsart"
    if werkzeugkarte_py 'sys.exit(0 if w.github_stufe(sys.argv[2]) else 1)' "$a" 2>/dev/null; then GITHUB_ART=1; fi
  done
  [ "$(printf '%s\n' "${liste[@]}" | sort | uniq -d | wc -l)" = 0 ] || aufruf "--arten: eine Art steht doppelt"
}

# ---------------------------------------------------------------- kernkanal.conf lesen und schreiben
declare -A K
konfig_standard() {
  K=([KANAL]="$STD_KANAL" [ABLAUF]="$(date -d "+$STD_ABLAUF_TAGE days" +%Y%m%d)" [ARTEN]="" [REPOS]="" [DIENSTE]=""
     [MAX_TURNS]="$STD_MAX_TURNS" [MAX_BUDGET_USD]="$STD_MAX_BUDGET_USD" [MAX_START_STUNDE]="$STD_MAX_START_STUNDE"
     [MAX_START_TAG]="$STD_MAX_START_TAG" [MAX_LAUFZEIT_TAG_S]="$STD_MAX_LAUFZEIT_TAG_S" [MAX_FOLGEN]="$STD_MAX_FOLGEN"
     [MIN_ABSTAND_S]="$STD_MIN_ABSTAND_S" [MIN_PLATZ_MB]="$STD_MIN_PLATZ_MB" [MAX_VERSUCH_STUNDE]="$STD_MAX_VERSUCH_STUNDE")
}

KONFIG_FEHLER=""
konfig_lesen() {   # liest die vorhandene Datei über die Standardwerte; unbekannte Schlüssel stoppen (nichts darf verloren gehen)
  local zeile name wert bekannt k
  konfig_standard
  if [ ! -f "$KONFIG" ]; then KONFIG_FEHLER="${KONFIG#"$WURZEL"} fehlt (erst schluessel)"; return 1; fi
  while IFS= read -r zeile || [ -n "$zeile" ]; do
    zeile="${zeile%$'\r'}"
    if [[ -z "$zeile" || "$zeile" == \#* ]]; then continue; fi
    name="${zeile%%=*}"; wert="${zeile#*=}"
    bekannt=0
    for k in "${KONFIG_SCHLUESSEL[@]}"; do if [ "$k" = "$name" ]; then bekannt=1; fi; done
    if [ "$bekannt" != 1 ]; then KONFIG_FEHLER="kernkanal.conf enthält den unbekannten Schlüssel $name (von Hand prüfen)"; return 1; fi
    K[$name]="$wert"
  done < "$KONFIG"
}
konfig_lesen_hart() { konfig_lesen || vorbedingung "$KONFIG_FEHLER"; }

konfig_text() {
  local k
  echo "# Regeln des Kern-Kanals der Entwicklungsaufträge. Nur über wamo-job-kern-einrichten.sh ändern (root:wamo 0640)."
  for k in "${KONFIG_SCHLUESSEL[@]}"; do echo "$k=${K[$k]}"; done
}

konfig_schreiben() {   # sichert die alte Datei, schreibt atomar, nur wenn sich etwas ändert
  local neu
  neu="$(konfig_text)"
  if gleich_wie "$KONFIG" "$neu"; then echo "  kernkanal.conf unverändert"; return 0; fi
  sichern "$KONFIG"
  printf '%s\n' "$neu" | schreibe_datei "$KONFIG" 0640 root:wamo
  echo "  kernkanal.conf geschrieben (KANAL=${K[KANAL]})"
}

# ---------------------------------------------------------------- Aufrufform lesen
KOMMANDO="${1:-}"
[ -n "$KOMMANDO" ] || aufruf "Befehl fehlt"
shift
TROCKEN=0 ABLAUF="" HOSTFP="" ARTEN="" REPOS="" DIENSTE="" MAX_TURNS="" MAX_BUDGET="" PROFIL="" KOSTEN_AUFTRAG="" KOSTEN_TAG=""
RELEASE="" SCHALTER="" ZU=0 GITHUB_ART=0
optionswert() { [ "$#" -ge 2 ] && [ -n "${2:-}" ] && [[ "$2" != --* ]] || aufruf "$1 braucht einen Wert"; }
case "$KOMMANDO" in
  schluessel)
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --trocken) TROCKEN=1; shift ;;
        --ablauf) optionswert "$@"; ABLAUF="$2"; shift 2 ;;
        --hostfingerabdruck) optionswert "$@"; HOSTFP="$2"; shift 2 ;;
        *) aufruf "unbekannte Option für schluessel: $1" ;;
      esac
    done
    [ -z "$ABLAUF" ] || pruefe_ablauf "$ABLAUF"
    if [ -n "$HOSTFP" ]; then [[ "$HOSTFP" =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] || aufruf "--hostfingerabdruck muss die Form SHA256:<43 Zeichen> haben"; fi
    [ "$TROCKEN" = 1 ] || [ -n "$HOSTFP" ] || aufruf "--hostfingerabdruck fehlt (Wert aus scripts/wamo-tunnel-einrichten.ps1, Abgleich mit dem Hostschlüssel dieses Servers)" ;;
  kanal)
    if [ "${1:-}" = zu ]; then
      [ "$#" -eq 1 ] || aufruf "kanal zu kennt keine weiteren Angaben"
      ZU=1
    else
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --arten) optionswert "$@"; ARTEN="$2"; shift 2 ;;
          --repos) optionswert "$@"; REPOS="$2"; shift 2 ;;
          --dienste) optionswert "$@"; DIENSTE="$2"; shift 2 ;;
          --max-turns) optionswert "$@"; MAX_TURNS="$2"; pruefe_zahl --max-turns "$2" 1 200; shift 2 ;;
          --max-budget-usd) optionswert "$@"; MAX_BUDGET="$2"; pruefe_betrag --max-budget-usd "$2" 0.01 20; shift 2 ;;
          *) aufruf "unbekannte Option für kanal: $1" ;;
        esac
      done
      [ -n "$ARTEN" ] || aufruf "kanal braucht --arten (oder kanal zu)"
      pruefe_repos "$REPOS"
      pruefe_dienste "$DIENSTE"
    fi ;;
  schalter)
    SCHALTER="${1:-}"; RELEASE="${2:-}"
    case "$SCHALTER" in an|aus) ;; *) aufruf "schalter braucht an oder aus" ;; esac
    [[ "$RELEASE" =~ $NAME_RE ]] || aufruf "Release-Name ungültig (v plus Buchstaben, Ziffern, Punkt, Strich, Unterstrich)"
    shift 2
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --profil) optionswert "$@"; PROFIL="$2"; shift 2 ;;
        --kosten-auftrag) optionswert "$@"; KOSTEN_AUFTRAG="$2"; pruefe_betrag --kosten-auftrag "$2" 0.01 100000; shift 2 ;;
        --kosten-tag) optionswert "$@"; KOSTEN_TAG="$2"; pruefe_betrag --kosten-tag "$2" 0.01 100000; shift 2 ;;
        *) aufruf "unbekannte Option für schalter: $1" ;;
      esac
    done
    if [ "$SCHALTER" = an ]; then
      [ -n "$PROFIL" ] || aufruf "schalter an braucht --profil <user_id>"
      pruefe_profil "$PROFIL"
    elif [ -n "$PROFIL$KOSTEN_AUFTRAG$KOSTEN_TAG" ]; then
      aufruf "schalter aus kennt weder --profil noch --kosten-auftrag noch --kosten-tag"
    fi ;;
  notaus-aufheben|status)
    [ "$#" -eq 0 ] || aufruf "$KOMMANDO kennt keine weiteren Angaben" ;;
  *) aufruf "unbekannter Befehl: $KOMMANDO" ;;
esac

# ---------------------------------------------------------------- Umgebung: echtes System oder Probelauf
if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || fehler "Bitte mit sudo ausführen."
  case "$KOMMANDO" in schluessel|schalter) pruefe_herkunft ;; esac
else
  [ "$(id -u)" -ne 0 ] || fehler "Der Probelauf (WAMO_JOB_KERN_WURZEL) läuft nicht als root."
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      fehler "WAMO_JOB_KERN_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich ohne // und ohne . oder .. als Pfadteil sein." ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd, keine Ja-Probe)"
fi

# ---------------------------------------------------------------- schluessel
cmd_schluessel() {
  local ssh_version dispatcher zeile typ blob pub fp host_fp htyp hblob hosteintrag stand_zeile neu_ablauf
  echo "== 0. Vorbedingungen (nur lesend)"
  ssh_version="$(ssh -V 2>&1 | head -n1 | sed -n 's/^OpenSSH_\([0-9]*\)\.\([0-9]*\).*/\1.\2/p')"
  [[ "$ssh_version" =~ ^[0-9]+\.[0-9]+$ ]] || vorbedingung "OpenSSH-Version nicht lesbar (ssh -V)"
  [ "$(printf '%s\n' 7.6 "$ssh_version" | sort -V | head -n1)" = 7.6 ] || vorbedingung "OpenSSH $ssh_version ist zu alt (restrict und ExposeAuthInfo brauchen 7.6 oder neuer)"
  echo "  OpenSSH $ssh_version"
  if [ -z "$WURZEL" ]; then [ -x /usr/bin/ssh ] || vorbedingung "/usr/bin/ssh fehlt (Paket openssh-client)"; else command -v ssh >/dev/null || vorbedingung "ssh fehlt"; fi
  fail2ban_pruefen
  dispatcher="$APP/bin/wamo-job-dispatch"
  grep -Eq '^VERSION = "([7-9]|[1-9][0-9]+)"$' "$dispatcher" 2>/dev/null \
    || vorbedingung "der Dispatcher ist älter als Version 7 (sonst bekäme der Kern die Rechte des PC-Kanals): erst wamo-job-einrichten.sh"
  [ -f "$WAECHTER/wamo_job_kernkanal.py" ] || vorbedingung "$WAECHTER/wamo_job_kernkanal.py fehlt (wamo-job-einrichten.sh)"
  grep -Eq '^[[:space:]]*ExposeAuthInfo yes$' "$SSHD_DROPIN" 2>/dev/null && grep -q 'authorized_keys.d/%u-kern' "$SSHD_DROPIN" \
    || vorbedingung "der sshd-Block fehlt ExposeAuthInfo oder die zweite AuthorizedKeysFile (wamo-job-einrichten.sh, Bestätigung nicht vergessen)"
  [ -f "$HOSTKEY_PUB" ] || vorbedingung "die Datei des Hostschlüssels ${HOSTKEY_PUB#"$WURZEL"} fehlt"
  if [ -z "$WURZEL" ]; then
    id wamo-core >/dev/null 2>&1 || vorbedingung "Benutzer wamo-core fehlt"
    [ "$(id -nG wamo-core)" = wamo-core ] || vorbedingung "wamo-core ist in weiteren Gruppen (nie in der Gruppe wamo)"
  fi
  [ -f "$PC_DATEI" ] || echo "  Hinweis: ${PC_DATEI#"$WURZEL"} fehlt (wird nicht angefasst)"
  [ ! -e "$NOTAUS" ] || vorbedingung "die Sperrdatei ${NOTAUS#"$WURZEL"} besteht (erst notaus-aufheben, dann schluessel: ein Notaus verlangt ein neues Schlüsselpaar)"
  ssh_baum_pruefen
  ruhe_pruefen
  host_fp="$(ssh-keygen -l -E sha256 -f "$HOSTKEY_PUB" | awk '{print $2}')"
  if [ -n "$HOSTFP" ] && [ "$HOSTFP" != "$host_fp" ]; then
    vorbedingung "der Hostschlüssel dieses Servers ($host_fp) passt nicht zu --hostfingerabdruck: falscher Server oder falscher Wert"
  fi
  echo "  Hostschlüssel $host_fp${HOSTFP:+ (stimmt mit --hostfingerabdruck überein)}"
  if [ "$TROCKEN" = 1 ]; then
    echo "Trockenlauf: nichts wird geändert. Es würden angelegt oder geprüft:"
    echo "  Schlüssel ${KEY#"$WURZEL"} (nur wenn er fehlt), ${KNOWN#"$WURZEL"}, ${KERN_DATEI#"$WURZEL"} (genau eine Zeile)," \
      "${KONFIG#"$WURZEL"} (KANAL=$STD_KANAL), Rauchtest ${RAUCHTEST#"$WURZEL"}, Ja-Probe ping"
    return 0
  fi
  AKTION=schluessel

  echo "== 1. Schlüssel von wamo-core (verlässt den Server nie; die Arbeit im Baum von wamo-core läuft als wamo-core)"
  ssh_baum_pruefen
  als_kern mkdir -p "$SSH_ORDNER"
  als_kern chmod 0700 "$SSH_ORDNER"
  if ! als_kern test -f "$KEY"; then
    als_kern ssh-keygen -q -t ed25519 -N '' -C wamo-kern-jobs -f "$KEY"
    echo "  neuer Schlüssel angelegt"
  else
    echo "  Schlüssel vorhanden"
  fi
  # root liest den öffentlichen Teil nur über wamo-core und prüft ihn streng: genau eine Zeile ssh-ed25519 <68 Zeichen Base64> [Kommentar]
  pub="$(als_kern cat "$KEY.pub" 2>/dev/null)" || fehler "der öffentliche Schlüssel fehlt neben ${KEY#"$WURZEL"}"
  [[ "$pub" =~ ^ssh-ed25519\ ([A-Za-z0-9+/]{68})(\ [A-Za-z0-9._@-]{1,64})?$ ]] \
    || fehler "der öffentliche Schlüssel hat nicht die Form ssh-ed25519 <Base64> (genau eine Zeile)"
  typ=ssh-ed25519
  blob="${BASH_REMATCH[1]}"
  fp="$(ssh-keygen -l -E sha256 -f <(printf '%s %s\n' "$typ" "$blob") | awk '{print $2}')"
  [[ "$fp" =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] || fehler "der öffentliche Schlüssel ist kein gültiger ed25519-Schlüssel"

  echo "== 2. known_hosts aus dem lokalen Hostschlüssel"
  read -r htyp hblob _ < "$HOSTKEY_PUB"
  hosteintrag="$KERN_IP $htyp $hblob"
  if [ "$(als_kern cat "$KNOWN" 2>/dev/null || true)" = "$hosteintrag" ]; then echo "  unverändert"
  else
    sichern_kern "$KNOWN"
    printf '%s\n' "$hosteintrag" | als_kern bash -c 'umask 022 && cat > "$1.neu" && chmod 0644 "$1.neu" && mv -f "$1.neu" "$1"' _ "$KNOWN"
    echo "  geschrieben"
  fi

  echo "== 3. Zeile in authorized_keys.d/wamo-kern (genau eine, ohne expiry-time)"
  zeile="restrict,from=\"$KERN_IP\",command=\"/opt/wamo/bin/wamo-job-dispatch\" $typ $blob wamo-kern-jobs"
  stand_zeile=""
  if gleich_wie "$KERN_DATEI" "$zeile"; then echo "  unverändert ($fp)"
  else
    sichern "$KERN_DATEI"
    stand_zeile=neu
    printf '%s\n' "$zeile" | schreibe_datei "$KERN_DATEI" 0644 root:root
    echo "  geschrieben ($fp)"
  fi

  echo "== 4. kernkanal.conf (Kanal bleibt zu, bis kanal ihn öffnet)"
  if [ -f "$KONFIG" ]; then
    konfig_lesen_hart
    if [ -n "$ABLAUF" ]; then K[ABLAUF]="$ABLAUF"; fi
  else
    konfig_standard
    [ -z "$ABLAUF" ] || K[ABLAUF]="$ABLAUF"
  fi
  konfig_schreiben

  echo "== 5. Rauchtest installieren"
  [ -f "$HERE/wamo_job_kern_rauchtest.py" ] || fehler "$HERE/wamo_job_kern_rauchtest.py fehlt"
  mkdir -p "$KERNAPP/bin"
  if [ -f "$RAUCHTEST" ] && cmp -s <(tr -d '\r' < "$HERE/wamo_job_kern_rauchtest.py") "$RAUCHTEST"; then
    echo "  ${RAUCHTEST#"$WURZEL"} unverändert"
  else
    sichern "$RAUCHTEST"
    tr -d '\r' < "$HERE/wamo_job_kern_rauchtest.py" | schreibe_datei "$RAUCHTEST" 0755 root:root
    echo "  ${RAUCHTEST#"$WURZEL"} geschrieben"
  fi

  echo "== 6. Ja-Probe ping (als wamo-core mit den Eigenschaften der Unit)"
  if [ -n "$WURZEL" ]; then
    echo "  (Probelauf: keine Ja-Probe)"
  elif ! "$RAUCHTEST" --ping; then
    echo "Ja-Probe fehlgeschlagen: die Zeile des Kerns wird zurückgenommen." >&2
    if [ "$stand_zeile" = neu ] && [ -f "$SICHERUNG/wamo-kern" ]; then cp -p "$SICHERUNG/wamo-kern" "$KERN_DATEI"
    elif [ "$stand_zeile" = neu ]; then rm -f "$KERN_DATEI"; fi
    fehler "Ja-Probe fehlgeschlagen, ${KERN_DATEI#"$WURZEL"} zurückgenommen"
  fi
  neu_ablauf="${K[ABLAUF]}"
  echo "Fertig. Schlüssel $fp, Ablauf $neu_ablauf ($(tage_bis "$neu_ablauf") Tage), Kanal ${K[KANAL]}. Weiter: Rauchtest --produktiv, dann schalter an."
}

fail2ban_pruefen() {   # fail2ban muss die eigene Adresse und Loopback ausnehmen (sonst träfe ein Bann Supavisor und die API des Kerns)
  local jails jail ignoreself ignoreip
  if ! command -v fail2ban-client >/dev/null 2>&1; then echo "  fail2ban nicht installiert"; return 0; fi
  if [ -z "$WURZEL" ] && ! systemctl is-active --quiet fail2ban 2>/dev/null; then echo "  fail2ban nicht aktiv"; return 0; fi
  jails="$(fail2ban-client status 2>/dev/null | sed -n 's/.*Jail list:[[:space:]]*//p' | tr ',' ' ' || true)"
  [ -n "${jails// /}" ] || { echo "  fail2ban nicht erreichbar oder ohne Jails"; return 0; }
  for jail in $jails; do
    ignoreself="$(fail2ban-client get "$jail" ignoreself 2>/dev/null || echo false)"
    ignoreip="$(fail2ban-client get "$jail" ignoreip 2>/dev/null | tr '\n' ' ' || true)"
    if [ "$ignoreself" = true ]; then continue; fi
    "$PYTHON3" -I -c '
import ipaddress, sys
netze = []
for teil in sys.argv[2].replace("|-", " ").replace("`-", " ").replace("-", " ").split():
    try:
        netze.append(ipaddress.ip_network(teil, strict=False))
    except ValueError:
        pass
fehlt = [a for a in sys.argv[1].split(",") if not any(ipaddress.ip_address(a) in n for n in netze)]
sys.exit(1 if fehlt else 0)' "$KERN_IP,127.0.0.1" "$ignoreip" \
      || vorbedingung "fail2ban (Jail $jail) nimmt $KERN_IP und 127.0.0.1 nicht aus (ignoreip oder ignoreself): ein Bann träfe Supavisor und die API des Kerns"
  done
  echo "  fail2ban nimmt $KERN_IP und 127.0.0.1 aus"
}

# ---------------------------------------------------------------- kanal
claude_flags_pruefen() {   # kennt die installierte Claude-Code-Version beide Flags? (als wamo)
  local hilfe claude="/srv/wamo/.local/bin/claude"
  [ -z "$WURZEL" ] || claude="$WURZEL/attrappen/claude"
  if [ -n "$WURZEL" ]; then hilfe="$("$claude" --help 2>&1 || true)"
  else hilfe="$(runuser -u wamo -- env -i HOME=/srv/wamo PATH=/usr/bin:/bin "$claude" --help 2>&1 || true)"; fi
  grep -q -- '--max-turns' <<<"$hilfe" && grep -q -- '--max-budget-usd' <<<"$hilfe" \
    || vorbedingung "claude --help (als wamo) kennt --max-turns oder --max-budget-usd nicht: der Kern-Kanal bleibt für GitHub-Arten zu (Claude Code anheben)"
}

cmd_kanal() {
  AKTION=kanal
  local tage
  if [ "$ZU" = 1 ]; then
    konfig_lesen_hart
    K[KANAL]=zu
    konfig_schreiben
    echo "Kanal zu: neue Starts und Freigaben werden abgewiesen, laufende Aufträge enden regulär."
    return 0
  fi
  [ -f "$KERN_DATEI" ] || vorbedingung "${KERN_DATEI#"$WURZEL"} fehlt (erst schluessel)"
  [ ! -e "$NOTAUS" ] || vorbedingung "die Sperrdatei ${NOTAUS#"$WURZEL"} besteht (erst notaus-aufheben, dann schluessel)"
  ruhe_pruefen
  konfig_lesen_hart
  [[ "${K[ABLAUF]}" =~ ^[0-9]{8}$ ]] && tage="$(tage_bis "${K[ABLAUF]}")" || vorbedingung "ABLAUF in kernkanal.conf ist ungültig (schluessel --ablauf)"
  [ "$tage" -ge 0 ] || vorbedingung "die Schlüsselzeile ist abgelaufen (${K[ABLAUF]}): schluessel --ablauf <JJJJMMTT>"
  pruefe_arten "$ARTEN"
  if [ "$GITHUB_ART" = 1 ]; then
    [ -n "$REPOS" ] || aufruf "GitHub-Arten brauchen --repos (genau die Repos, die der Kern öffnen darf)"
    claude_flags_pruefen
  fi
  K[ARTEN]="$ARTEN"
  K[REPOS]="${REPOS,,}"
  K[DIENSTE]="$DIENSTE"
  [ -z "$MAX_TURNS" ] || K[MAX_TURNS]="$MAX_TURNS"
  [ -z "$MAX_BUDGET" ] || K[MAX_BUDGET_USD]="$MAX_BUDGET"
  K[KANAL]=offen
  konfig_schreiben
  echo "Kanal offen für: ${K[ARTEN]}${K[REPOS]:+ mit Repos ${K[REPOS]}}. Ablauf ${K[ABLAUF]} ($tage Tage)."
}

# ---------------------------------------------------------------- schalter
slot_release() {   # Release (Ordnername) hinter /opt/wamo-core/slots/<slot>
  basename "$(readlink "$KERNAPP/slots/$1" 2>/dev/null || true)" 2>/dev/null || true
}

release_laeuft() {   # läuft ein Slot mit diesem Release? (aus wamo-core-slots.sh status)
  local ausgabe zeile slot ziel zustand treffer=""
  ausgabe="$("${SLOTS[@]}" status 2>/dev/null || true)"
  while read -r zeile; do
    case "$zeile" in "Slot a: "*|"Slot b: "*) ;; *) continue ;; esac
    read -r _ slot ziel zustand <<< "$zeile"
    if [ "${ziel##*/}" = "$1" ] && [ "$zustand" = active ]; then treffer="${slot%:}"; fi
  done <<< "$ausgabe"
  [ -n "$treffer" ]
}

drop_in_text() {
  echo "# WAMO: Entwicklungsaufträge im Büro. Nur über wamo-job-kern-einrichten.sh schalter schreiben (ohne Spiegel-Marke)."
  echo "[Service]"
  echo "Environment=WAMO_ENTWICKLUNG_BUERO=an"
  echo "Environment=WAMO_ENTWICKLUNG_ORT=sokrates2"
  echo "Environment=WAMO_ENTWICKLUNG_PROFILE=$PROFIL"
  echo "Environment=WAMO_ENTWICKLUNG_KOSTEN_AUFTRAG_USD=${KOSTEN_AUFTRAG:-$STD_KOSTEN_AUFTRAG_USD}"
  echo "Environment=WAMO_ENTWICKLUNG_KOSTEN_TAG_USD=${KOSTEN_TAG:-$STD_KOSTEN_TAG_USD}"
}

schalter_vorabpruefung() {   # Vorabprüfungen nach E2, alle lesend
  local ziel="$KERNAPP/releases/$RELEASE/voice-core/entwicklung_betrieb.py"
  [ -f "$KERN_DATEI" ] || vorbedingung "${KERN_DATEI#"$WURZEL"} fehlt (erst schluessel)"
  ssh_baum_pruefen
  als_kern test -f "$KEY" && als_kern test -f "$KNOWN" || vorbedingung "Schlüssel oder known_hosts des Kerns fehlen (erst schluessel)"
  [ -x "$RAUCHTEST" ] || vorbedingung "der Rauchtest ${RAUCHTEST#"$WURZEL"} fehlt (erst schluessel)"
  grep -Eq '^SCHRANKEN_VERSION = [1-9][0-9]*$' "$ziel" 2>/dev/null \
    || vorbedingung "das Release $RELEASE kennt die Büro-Schranken nicht (SCHRANKEN_VERSION in voice-core/entwicklung_betrieb.py fehlt): ein altes Release ließe den Schalter wirkungslos"
  if "$COREENV" liste 2>/dev/null | grep -q '^WAMO_ENTWICKLUNG'; then
    vorbedingung "core.env enthält ein WAMO_ENTWICKLUNG*: es hätte vor dem Drop-in Vorrang (wamo-core-env loeschen)"
  fi
  [ ! -e "$DROPIN_ALT" ] || vorbedingung "${DROPIN_ALT#"$WURZEL"} besteht: das Spiegeln der Drop-ins überschriebe den Schalter"
  ruhe_pruefen
}

cmd_schalter() {
  local neu vorhanden=0 alt_inhalt
  AKTION=schalter
  schalter_vorabpruefung_wenn_an
  if [ "$SCHALTER" = an ]; then
    neu="$(drop_in_text)"
    if gleich_wie "$DROPIN" "$neu" && release_laeuft "$RELEASE"; then echo "Schalter steht schon auf an, Release $RELEASE läuft: nichts zu tun."; return 0; fi
  else
    if [ ! -e "$DROPIN" ] && release_laeuft "$RELEASE"; then echo "Schalter steht schon auf aus, Release $RELEASE läuft: nichts zu tun."; return 0; fi
  fi
  [ ! -f "$DROPIN" ] || { vorhanden=1; alt_inhalt="$(cat "$DROPIN")"; sichern "$DROPIN"; }
  mkdir -p "$DROPIN_ORDNER"
  if [ "$SCHALTER" = an ]; then printf '%s\n' "$neu" | schreibe_datei "$DROPIN" 0644 root:root; else rm -f "$DROPIN"; fi
  echo "  Drop-in ${DROPIN#"$WURZEL"}: $([ "$SCHALTER" = an ] && echo geschrieben || echo entfernt)"
  if systemctl daemon-reload && "${SLOTS[@]}" umschalten-kern "$RELEASE"; then
    echo "Schalter $SCHALTER, Release $RELEASE läuft auf dem neuen Slot. Weiter: wamo-core-slots.sh bewaehren 30, dann ausleiten."
    return 0
  fi
  if release_laeuft "$RELEASE"; then   # schon umgeschaltet: das Drop-in bleibt, sonst liefe der neue Slot mit einer anderen Einstellung
    teilweise "das Release $RELEASE läuft schon auf dem neuen Slot, aber umschalten-kern endete mit einem Fehler. Das Drop-in bleibt. Mit wamo-core-slots.sh status und rueckweg-kern prüfen."
  fi
  if [ "$vorhanden" = 1 ]; then printf '%s\n' "$alt_inhalt" | schreibe_datei "$DROPIN" 0644 root:root; else rm -f "$DROPIN"; fi
  systemctl daemon-reload || true
  fehler "umschalten-kern ist gescheitert, nichts umgeschaltet: das vorige Drop-in ist wieder hergestellt"
}

schalter_vorabpruefung_wenn_an() {
  if [ "$SCHALTER" = an ]; then schalter_vorabpruefung; else ruhe_pruefen; fi
}

# ---------------------------------------------------------------- notaus-aufheben
cmd_notaus_aufheben() {
  AKTION=notaus
  if [ ! -e "$NOTAUS" ]; then echo "Keine Sperrdatei: nichts zu tun."; return 0; fi
  rm -f "$NOTAUS"
  echo "Sperrdatei entfernt. Der Kanal bleibt geschlossen: erst schluessel (setzt die Zeile des Kerns neu), dann kanal (öffnet)."
}

# ---------------------------------------------------------------- status
umgebungs_namen() {   # Namen WAMO_ENTWICKLUNG* aus /proc/<pid>/environ, dazu der Wert nur des Schalters
  local pid="$1" datei="$PROC/$1/environ" eintrag
  [ -r "$datei" ] || { echo "(nicht lesbar)"; return 0; }
  while IFS= read -r -d '' eintrag; do
    case "$eintrag" in
      WAMO_ENTWICKLUNG_BUERO=*) echo -n "WAMO_ENTWICKLUNG_BUERO=${eintrag#*=} " ;;
      WAMO_ENTWICKLUNG*=*) echo -n "${eintrag%%=*} " ;;
    esac
  done < "$datei"
}

schalter_wert() {   # an oder aus aus /proc/<pid>/environ (nur der Schalter)
  local datei="$PROC/$1/environ" eintrag wert=aus
  [ -r "$datei" ] || { echo "?"; return 0; }
  while IFS= read -r -d '' eintrag; do
    [ "${eintrag%%=*}" = WAMO_ENTWICKLUNG_BUERO ] && wert="${eintrag#*=}"
  done < "$datei"
  echo "$wert"
}

cmd_status() {
  local abw=() tage s pid zustand release soll ist n ordner laufend fp
  echo "== Kern-Kanal"
  if [ -f "$KERN_DATEI" ]; then
    fp="$(ssh-keygen -l -E sha256 -f "$KERN_DATEI" 2>/dev/null | awk '{print $2}' || true)"
    echo "Schlüsselzeile: vorhanden${fp:+ ($fp)}"
    if [ "$(wc -l < "$KERN_DATEI")" != 1 ]; then abw+=("die Datei ${KERN_DATEI#"$WURZEL"} hat nicht genau eine Zeile"); fi
    if grep -q 'expiry-time' "$KERN_DATEI"; then abw+=("die Schlüsselzeile enthält expiry-time (der Ablauf steht in kernkanal.conf)"); fi
  else
    echo "Schlüsselzeile: keine"
  fi
  if [ -f "$NOTAUS" ]; then echo "Notaus: Sperrdatei besteht"; abw+=("die Sperrdatei kernkanal.notaus besteht (der Kern-Kanal ist gesperrt)"); else echo "Notaus: nein"; fi
  if [ -f "$KONFIG" ]; then
    konfig_lesen || abw+=("$KONFIG_FEHLER")
    echo "Kanal: ${K[KANAL]} (Arten: ${K[ARTEN]:--}, Repos: ${K[REPOS]:--}, Dienste: ${K[DIENSTE]:--})"
    if [[ "${K[ABLAUF]}" =~ ^[0-9]{8}$ ]]; then
      tage="$(tage_bis "${K[ABLAUF]}")"
      echo "Ablauf: ${K[ABLAUF]} (noch $tage Tage)"
      if [ "$tage" -lt 0 ]; then abw+=("der Ablauf ${K[ABLAUF]} ist überschritten: der Kanal ist zu"); elif [ "$tage" -le 7 ]; then abw+=("der Ablauf ${K[ABLAUF]} ist in $tage Tagen"); fi
    fi
    if [ "${K[KANAL]}" = offen ] && [ ! -f "$KERN_DATEI" ]; then abw+=("der Kanal ist offen, aber die Schlüsselzeile fehlt"); fi
  else
    echo "Kanal: keine kernkanal.conf"
    if [ -f "$KERN_DATEI" ]; then abw+=("die Schlüsselzeile besteht ohne kernkanal.conf"); fi
  fi
  soll=aus
  [ -f "$DROPIN" ] && soll=an
  echo "== Schalter (Drop-in: $soll)"
  for s in a b; do
    zustand="$(systemctl is-active "wamo-core@$s.service" 2>/dev/null || true)"
    release="$(slot_release "$s")"
    pid="$(systemctl show -p MainPID --value "wamo-core@$s.service" 2>/dev/null || echo 0)"
    if [ "$zustand" = active ] && [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
      ist="$(schalter_wert "$pid")"
      echo "Slot $s: aktiv, Release ${release:--}, $(umgebungs_namen "$pid")"
      if [ "$ist" != "$soll" ]; then abw+=("Slot $s hat WAMO_ENTWICKLUNG_BUERO=$ist, das Drop-in sagt $soll (umschalten-kern lädt es neu)"); fi
    else
      echo "Slot $s: ${zustand:-unbekannt}, Release ${release:--}"
    fi
  done
  echo "== Aufträge"
  laufend="$(laufende_kern_auftraege | tr '\n' ' ')"
  echo "Laufende Kern-Aufträge: ${laufend:-keine}"
  n=0
  if [ -d "$PROJEKTE" ]; then for ordner in "$PROJEKTE"/kern-*; do [ -d "$ordner" ] && n=$((n + 1)); done; fi
  echo "Kern-Ordner: $n"
  if [ "$n" -gt 20 ]; then abw+=("mehr als 20 Kern-Ordner (Aufräumen ist ein Folgepaket)"); fi
  if [ "${#abw[@]}" = 0 ]; then echo "Abweichungen: keine"; else
    for s in "${abw[@]}"; do echo "Abweichung: $s"; done
  fi
}

case "$KOMMANDO" in
  schluessel) cmd_schluessel ;;
  kanal) cmd_kanal ;;
  schalter) cmd_schalter ;;
  notaus-aufheben) cmd_notaus_aufheben ;;
  status) cmd_status ;;
esac
