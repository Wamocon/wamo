#!/usr/bin/env bash
# Rückweg des Kern-Kanals der Entwicklungsaufträge (Paket R10, Gegenstück zu wamo-job-kern-einrichten.sh).
# Wirkt ohne Neustart des Kerns und ohne Eingriff in den PC-Kanal. Es startet, stoppt und startet keinen Dienst neu.
#
#   wamo-job-kern-rueckweg.sh notaus [--trocken]
#       Notaus in fester Reihenfolge (Schnittstelle I10), damit kein Start durch die Lücke rutscht:
#         1. Sperrdatei /etc/wamo/kernkanal.notaus anlegen: ab jetzt weist der Dispatcher jeden Kern-Befehl außer ping und status ab,
#            auch ein Start, der die erste Prüfung schon bestand, scheitert an der zweiten vor der Token-Erzeugung.
#         2. Zeile in /etc/ssh/authorized_keys.d/wamo-kern sichern und löschen (der Kern kommt nicht mehr durch sshd), KANAL=zu in
#            kernkanal.conf setzen und das Schlüsselpaar von wamo-core beiseitelegen (Endung .notaus-<Zeit>, als wamo-core): ein späteres
#            notaus-aufheben öffnet nichts von allein, schluessel legt ein NEUES Paar an, nie der womöglich kompromittierte Schlüssel.
#         3. Alle Aufträge mit kanal=kern aus /srv/wamo/jobs/*/auftrag.json per lokalem cancel beenden (Sandbox gestoppt, Token widerrufen),
#            die laufenden zuerst. Ein fehlgeschlagenes cancel wird gezählt und gemeldet, bricht aber nie vor Schritt 4 ab.
#         4. Erneut suchen, bis kein Kern-Auftrag mehr läuft (neue Funde werden ebenfalls beendet), und das Ergebnis protokollieren.
#       Wieder öffnen: wamo-job-kern-einrichten.sh notaus-aufheben, dann schluessel und kanal.
#   wamo-job-kern-rueckweg.sh --entfernen [--trocken]
#       Entfernt Schlüssel (authorized_keys.d/wamo-kern und das Schlüsselpaar von wamo-core), known_hosts, kernkanal.conf und die
#       Sperrdatei. Verweigert (Exit 3), solange das Drop-in wamo-entwicklung-buero.conf besteht (erst schalter aus) oder ein
#       Kern-Auftrag läuft. Der private Schlüssel wird nie gesichert.
#   --trocken zeigt nur, was geschähe, und ändert nichts.
#
# Exit-Codes: 0 ok, 1 Fehler, 2 falsche Aufrufform, 3 Vorbedingung nicht erfüllt. Jede Bedienhandlung schreibt eine Zeile ohne Werte in
# /var/log/wamo-job-kern/aktionen.jsonl und per logger -t wamo-job-kern ins Journal. Sicherungen: /var/backups/wamo-job-kern/<Zeit>/.
#   ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-rueckweg.sh notaus"
# Probelauf in einem Wegwerf-Ordner (ohne root, mit Attrappen in $WURZEL/attrappen und einem Dispatcher-Ersatz unter $WURZEL/opt/wamo/bin):
#   WAMO_JOB_KERN_WURZEL=/tmp/kern-probe bash wamo-job-kern-rueckweg.sh notaus
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_JOB_KERN_WURZEL:-}"   # nur für den Probelauf; leer = das echte System
APP="$WURZEL/opt/wamo"
KERN_DATEI="$WURZEL/etc/ssh/authorized_keys.d/wamo-kern"
KONFIG="$WURZEL/etc/wamo/kernkanal.conf"
NOTAUS="$WURZEL/etc/wamo/kernkanal.notaus"
DROPIN="$WURZEL/etc/systemd/system/wamo-core@.service.d/wamo-entwicklung-buero.conf"
SSH_ORDNER="$WURZEL/var/lib/wamo-core/.ssh"
KEY="$SSH_ORDNER/id_ed25519_wamo_jobs"
KNOWN="$SSH_ORDNER/known_hosts_wamo_jobs"
SICHERUNG_BASIS="$WURZEL/var/backups/wamo-job-kern"
LOGORDNER="$WURZEL/var/log/wamo-job-kern"
AKTIONEN="$LOGORDNER/aktionen.jsonl"
JOBS="$WURZEL/srv/wamo/jobs"
SUCHFRIST_S="${WAMO_JOB_KERN_SUCHFRIST_S:-30}"   # so lange wird nach dem cancel gewartet, bis kein Kern-Auftrag mehr läuft
[ -z "$WURZEL" ] || PATH="$WURZEL/attrappen:$PATH"

fehler() { echo "wamo-job-kern-rueckweg: $*" >&2; exit 1; }
aufruf() { echo "wamo-job-kern-rueckweg: $*" >&2; echo "Aufruf: notaus [--trocken] | --entfernen [--trocken]" >&2; exit 2; }
vorbedingung() { echo "wamo-job-kern-rueckweg: Vorbedingung nicht erfüllt: $*" >&2; exit 3; }

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

# ---------------------------------------------------------------- Aufrufform
MODUS="" TROCKEN=0
for arg in "$@"; do
  case "$arg" in
    notaus|--entfernen) [ -z "$MODUS" ] || aufruf "nur ein Befehl: notaus oder --entfernen"; MODUS="$arg" ;;
    --trocken) TROCKEN=1 ;;
    *) aufruf "unbekannte Angabe: $arg" ;;
  esac
done
[ -n "$MODUS" ] || aufruf "Befehl fehlt (notaus oder --entfernen)"

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || fehler "Bitte mit sudo ausführen."
else
  [ "$(id -u)" -ne 0 ] || fehler "Der Probelauf (WAMO_JOB_KERN_WURZEL) läuft nicht als root."
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      fehler "WAMO_JOB_KERN_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich ohne // und ohne . oder .. als Pfadteil sein." ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein systemd)"
fi
[ "$TROCKEN" = 0 ] || echo "Trockenlauf: nichts wird geändert."

# ---------------------------------------------------------------- Hilfen
SICHERUNG=""
sicherungsordner() {
  if [ -z "$SICHERUNG" ]; then SICHERUNG="$SICHERUNG_BASIS/$(date -u +%Y%m%d-%H%M%S)"; fi
  mkdir -p "$SICHERUNG"
  chmod 0700 "$SICHERUNG_BASIS" "$SICHERUNG" 2>/dev/null || true
}
sichern() {   # eine Datei außerhalb des Baums von wamo-core (/etc, /opt)
  [ -f "$1" ] || return 0
  sicherungsordner
  cp -p "$1" "$SICHERUNG/$(basename "$1")"
}

# Der Baum /var/lib/wamo-core/.ssh gehört dem Kern (wamo-core): root fasst ihn nie an (ein gekaperter Kern könnte ihn durch einen Link
# ersetzen). Jede Dateiarbeit dort läuft als wamo-core (als_kern). Im Probelauf gibt es den Wechsel des Benutzers nur mit der Attrappe runuser.
als_kern() {
  if [ -n "$WURZEL" ] && [ ! -x "$WURZEL/attrappen/runuser" ]; then "$@"
  else ( cd / && runuser -u wamo-core -- "$@" ); fi
}

BAUM_FEHLER=""
ssh_baum_ok() {   # kein Link, ein Ordner, im echten Betrieb im Besitz von wamo-core; sonst steht der Grund in BAUM_FEHLER
  local d
  if [ -L "$SSH_ORDNER" ]; then BAUM_FEHLER="${SSH_ORDNER#"$WURZEL"} ist ein Link (der Kern hat den Ordner ersetzt?)"; return 1; fi
  if [ -e "$SSH_ORDNER" ]; then
    if [ ! -d "$SSH_ORDNER" ]; then BAUM_FEHLER="${SSH_ORDNER#"$WURZEL"} ist kein Ordner"; return 1; fi
    if [ -z "$WURZEL" ] && [ "$(stat -c %U "$SSH_ORDNER")" != wamo-core ]; then BAUM_FEHLER="${SSH_ORDNER#"$WURZEL"} gehört nicht wamo-core"; return 1; fi
  fi
  for d in "$KEY" "$KEY.pub" "$KNOWN"; do
    if als_kern test -L "$d"; then BAUM_FEHLER="${d#"$WURZEL"} ist ein Link"; return 1; fi
  done
  return 0
}

sichern_kern() {   # eine Datei aus dem Baum von wamo-core: gelesen wird als wamo-core, root schreibt nur in den Sicherungsordner
  als_kern test -f "$1" || return 0
  sicherungsordner
  als_kern cat "$1" > "$SICHERUNG/$(basename "$1")"
}

tu() {   # tu "Beschreibung" befehl...: im Trockenlauf nur die Beschreibung zeigen
  local text="$1"
  shift
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] $text"; else "$@"; fi
}

kern_auftrag_laeuft() {
  local ordner="$1"
  if [ -f "$ordner/.lauf" ] && command -v flock >/dev/null 2>&1; then
    ! flock -n "$ordner/.lauf" true 2>/dev/null
  else
    [ -f "$ordner/status.json" ] && grep -q '"ergebnis": "läuft"' "$ordner/status.json"
  fi
}

kern_auftraege() {   # Auftrags-IDs mit kanal=kern, eine je Zeile
  local a
  [ -d "$JOBS" ] || return 0
  for a in "$JOBS"/*/auftrag.json; do
    [ -f "$a" ] || continue
    if grep -Eq '"kanal": ?"kern"' "$a"; then basename "$(dirname "$a")"; fi
  done
  return 0
}

laufende_kern_auftraege() {
  local id
  while read -r id; do
    [ -n "$id" ] || continue
    if kern_auftrag_laeuft "$JOBS/$id"; then echo "$id"; fi
  done < <(kern_auftraege)
}

lokal_cancel() {   # lokaler Aufruf des Dispatchers (ohne SSH-Variablen: Kanal lokal), als wamo
  local id="$1"
  if [ -n "$WURZEL" ]; then env -i PATH="$PATH" bash "$APP/bin/wamo-job-dispatch" cancel "$id" >/dev/null
  else runuser -u wamo -- env -i HOME=/srv/wamo PATH=/usr/bin:/bin /opt/wamo/bin/wamo-job-dispatch cancel "$id" >/dev/null; fi
}

# ---------------------------------------------------------------- notaus
kanal_zu_setzen() {   # KANAL=zu in kernkanal.conf: ein späteres notaus-aufheben und schluessel öffnen den Kanal nie von allein
  [ -f "$KONFIG" ] || return 0
  if grep -qx 'KANAL=zu' "$KONFIG" && [ "$(grep -c '^KANAL=' "$KONFIG")" = 1 ]; then echo "  Kanal steht schon auf zu"; return 0; fi
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] setzt KANAL=zu in ${KONFIG#"$WURZEL"}"; return 0; fi
  sichern "$KONFIG"
  { grep -v '^KANAL=' "$KONFIG" || true; echo "KANAL=zu"; } > "$KONFIG.neu"
  chmod 0640 "$KONFIG.neu"
  [ -n "$WURZEL" ] || chown root:wamo "$KONFIG.neu"
  mv -f "$KONFIG.neu" "$KONFIG"
  echo "  KANAL=zu gesetzt (die vorige Fassung ist gesichert)"
}

schluesselpaar_beiseite() {   # das Paar wird nie wiederverwendet: schluessel legt danach ein neues an. Als wamo-core, mit Zeitstempel.
  local zeit
  if ! ssh_baum_ok; then
    echo "  WARNUNG: Schlüsselpaar NICHT beiseitegelegt: $BAUM_FEHLER. Der Baum von wamo-core wird nicht angefasst; schluessel verweigert, bis er in Ordnung ist." >&2
    return 0
  fi
  zeit="$(date -u +%Y%m%d-%H%M%S)"
  if ! als_kern test -f "$KEY"; then echo "  kein Schlüsselpaar vorhanden"; return 0; fi
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] legt $(basename "$KEY") und $(basename "$KEY").pub beiseite (Endung .notaus-$zeit)"; return 0; fi
  als_kern mv -f "$KEY" "$KEY.notaus-$zeit"
  if als_kern test -f "$KEY.pub"; then als_kern mv -f "$KEY.pub" "$KEY.pub.notaus-$zeit"; fi
  echo "  Schlüsselpaar beiseitegelegt (Endung .notaus-$zeit)"
}

cmd_notaus() {
  local id n=0 rest warte=0 text erledigt="" fehlgeschlagen=0
  local -a laufende alle
  echo "== 1. Sperrdatei anlegen (ab jetzt weist der Dispatcher jeden Kern-Befehl außer ping und status ab)"
  text="Notaus $(date -u +%Y-%m-%dT%H:%M:%SZ) durch wamo-job-kern-rueckweg.sh"
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] legt ${NOTAUS#"$WURZEL"} an"
  else
    mkdir -p "$(dirname "$NOTAUS")"
    printf '%s\n' "$text" > "$NOTAUS.neu"
    chmod 0640 "$NOTAUS.neu"
    [ -n "$WURZEL" ] || chown root:wamo "$NOTAUS.neu"
    mv -f "$NOTAUS.neu" "$NOTAUS"
    AKTION=notaus
    echo "  ${NOTAUS#"$WURZEL"} liegt"
  fi

  echo "== 2. Schlüsselzeile sichern und löschen, Kanal auf zu, Schlüsselpaar beiseite (der Kern kommt nicht mehr durch sshd)"
  if [ -e "$KERN_DATEI" ]; then
    tu "sichert und löscht ${KERN_DATEI#"$WURZEL"}" sichern "$KERN_DATEI"
    tu "löscht ${KERN_DATEI#"$WURZEL"}" rm -f "$KERN_DATEI"
    [ "$TROCKEN" = 1 ] || echo "  Schlüsselzeile gesichert und gelöscht"
  else
    echo "  keine Schlüsselzeile vorhanden"
  fi
  kanal_zu_setzen
  schluesselpaar_beiseite

  echo "== 3. Kern-Aufträge beenden (lokales cancel: Sandbox gestoppt, Token widerrufen; laufende zuerst, nie ein Abbruch vor Schritt 4)"
  mapfile -t laufende < <(laufende_kern_auftraege)
  mapfile -t alle < <(kern_auftraege)
  for id in "${laufende[@]}" "${alle[@]}"; do
    [ -n "$id" ] || continue
    case " $erledigt " in *" $id "*) continue ;; esac
    erledigt="$erledigt $id"
    n=$((n + 1))
    if ! tu "cancel $id" lokal_cancel "$id"; then
      fehlgeschlagen=$((fehlgeschlagen + 1))
      echo "  WARNUNG: cancel $id ist fehlgeschlagen, es geht weiter" >&2
    fi
  done
  echo "  $n Auftrag/Aufträge mit kanal=kern, ${#laufende[@]} liefen, $fehlgeschlagen cancel fehlgeschlagen"

  echo "== 4. Erneut suchen, bis kein Kern-Auftrag mehr läuft"
  if [ "$TROCKEN" = 1 ]; then echo "  [trocken] sucht bis zu $SUCHFRIST_S s"; return 0; fi
  while :; do
    rest="$(laufende_kern_auftraege | tr '\n' ' ')"
    [ -n "${rest// /}" ] || break
    for id in $rest; do lokal_cancel "$id" || true; done   # ein Start, der durch die Lücke kam, wird ebenfalls beendet
    [ "$warte" -lt "$SUCHFRIST_S" ] || fehler "nach $SUCHFRIST_S s laufen noch Kern-Aufträge: $rest"
    sleep 1
    warte=$((warte + 1))
  done
  echo "  kein Kern-Auftrag läuft mehr"
  [ "$fehlgeschlagen" = 0 ] || echo "  Hinweis: $fehlgeschlagen cancel sind fehlgeschlagen, die Aufträge laufen aber nicht mehr" >&2
  echo "Notaus fertig. Wieder öffnen: wamo-job-kern-einrichten.sh notaus-aufheben, dann schluessel und kanal."
}

# ---------------------------------------------------------------- --entfernen
cmd_entfernen() {
  local lauf
  [ ! -e "$DROPIN" ] || vorbedingung "das Drop-in ${DROPIN#"$WURZEL"} besteht (erst schalter aus <release>)"
  lauf="$(laufende_kern_auftraege | tr '\n' ' ')"
  [ -z "${lauf// /}" ] || vorbedingung "ein Kern-Auftrag läuft ($lauf): erst notaus"
  ssh_baum_ok || vorbedingung "$BAUM_FEHLER: nichts angefasst"
  [ "$TROCKEN" = 1 ] || AKTION=schluessel
  echo "== Entfernen (Schlüsselzeile, Schlüsselpaar, known_hosts, kernkanal.conf, Sperrdatei)"
  local f
  for f in "$KERN_DATEI" "$KONFIG" "$NOTAUS"; do
    if [ -e "$f" ]; then
      tu "sichert $(basename "$f")" sichern "$f"
      tu "entfernt ${f#"$WURZEL"}" rm -f "$f"
    fi
  done
  if als_kern test -f "$KNOWN"; then   # im Baum von wamo-core: gesichert und gelöscht als wamo-core
    tu "sichert $(basename "$KNOWN")" sichern_kern "$KNOWN"
    tu "entfernt ${KNOWN#"$WURZEL"}" als_kern rm -f "$KNOWN"
  fi
  # der private Schlüssel wird nie gesichert; auch die beim Notaus beiseitegelegten Paare gehen weg
  tu "entfernt das Schlüsselpaar ${KEY#"$WURZEL"} und die beiseitegelegten Paare" als_kern bash -c 'rm -f -- "$1" "$1.pub" "$1".notaus-* "$1.pub".notaus-*' _ "$KEY"
  [ "$TROCKEN" = 1 ] || echo "Entfernt. Neu einrichten: wamo-job-kern-einrichten.sh schluessel."
}

case "$MODUS" in
  notaus) cmd_notaus ;;
  --entfernen) cmd_entfernen ;;
esac
