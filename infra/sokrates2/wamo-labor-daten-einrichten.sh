#!/usr/bin/env bash
# Labor-Daten des Testlabors auf Sokrates-2 einrichten (Konzept docs/testlabor-wamo.md, Abschnitt 3.6 und 3.8, Dateien in infra/sokrates2/labor-daten/).
# Der Kern liest die Dateien bei jedem Sitzungsstart (voice-core/labor_daten.py). Dieses Skript legt sie root-eigen unter /etc/wamo-labor/daten ab, denn der
# Benutzer wamo-core kann /etc/wamo (root:wamo 0750, dort liegen die Geheimnisse) nicht durchqueren, also auch nichts darunter lesen. /etc/wamo bleibt
# unberührt, es gibt dort keine ACL und keine Rechteänderung. Das Skript startet nichts neu und kennt keine Geheimnisse. Es gibt nie Inhalte aus, nur Namen,
# Rechte und Prüfsummen (die ersten 16 Zeichen von SHA-256, wie in der Logzeile "Labor-Daten:" des Sitzungsstarts). Nur als root (sudo). Wiederholbar: ein
# zweiter Lauf mit gleichem Inhalt ändert die Dateien nicht.
#
#   wamo-labor-daten-einrichten.sh einrichten [--persona standard|streng] [--release NAME] [--trocken]
#       Prüft die Lieferung mit dem echten Lader (in einem Wegwerf-Ordner), sichert den vorigen Stand, installiert die Dateien, prüft sie am Ziel noch einmal
#       mit dem Lader und nimmt bei jeder Ablehnung alles zurück. --persona wählt, welche Fassung der Regeln als persona-zusatz.md liegt (ohne Angabe
#       bleibt die vorhandene Fassung, sonst standard). persona-zusatz.streng.md wird immer mit abgelegt. Danach setzt es für den Kern genau eine Einstellung,
#       WAMO_LABOR_DATEN=/etc/wamo-labor/daten, über /usr/local/sbin/wamo-core-env (stdin, auch bei unverändertem Stand, gleicher Wert). Sie gilt erst mit dem
#       nächsten Kern-Release, der Kern wird hier nie neu gestartet. --trocken zeigt nur den Plan.
#   wamo-labor-daten-einrichten.sh status [--release NAME]
#       Rechte, Prüfsummen, Vergleich mit der Lieferung, gewählte Fassung, Urteil des Laders, ob WAMO_LABOR_DATEN beim Kern gesetzt ist (nur gesetzt oder nicht),
#       Sicherungen.
#   wamo-labor-daten-einrichten.sh rueckweg [--release NAME] [--trocken]
#       Stellt den Stand der letzten Sicherung für dieses Ziel wieder her (Dateien, die es dort nicht gab, werden entfernt). Der Stand davor bleibt als
#       <Zeit>.vor-rueckweg liegen. Die Einstellung des Kerns bleibt unverändert.
#
# Voraussetzung: /etc/wamo-labor muss existieren und ein echter Ordner sein (zuerst wamo-labor-einrichten.sh, es legt ihn als root:root 0755 an).
# Aufbau auf dem Server:
#   /etc/wamo-labor/daten/                             root:root 0755, keine Links, nie /etc/wamo
#   /etc/wamo-labor/daten/<Datei>                      root:root 0644, die acht Dateien der Lieferung
#   /var/backups/wamo-labor-daten/<JJJJMMTT-HHMMSS>/   root 0700, Sicherung des vorigen Stands (die Datei VORHER nennt Ziel und Stand und schließt die Sicherung ab)
#
# Der Lader: labor_daten.py aus dem Release des Kerns (/opt/wamo-core/releases/<Name>/voice-core) mit dessen venv. Ohne --release gilt das jüngste Release,
# das die Datei enthält; ohne venv /usr/bin/python3. Fehlt dort loguru, bricht das Skript vor jeder Änderung mit Exit 3 ab. Der Lader läuft als wamo-core
# und bekommt den Ordner wie im Kern über die Umgebung (WAMO_LABOR_DATEN, labor_daten.verzeichnis_aus_env), damit die Probe auch zeigt, ob der Kern die
# Dateien lesen darf. Exit-Codes: 0 ok, 1 Fehler (bei Ablehnung ist alles zurückgenommen), 2 falsche Aufrufform, 3 Vorbedingung nicht erfüllt.
#
#   scp -r infra/sokrates2 wamo-sokrates2:wamo-infra && ssh wamo-sokrates2 "chmod -R go-w ~/wamo-infra"
#   ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-daten-einrichten.sh einrichten --trocken"
#   ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-labor-daten-einrichten.sh einrichten --persona standard"
#
# Probelauf ohne root in einem Wegwerf-Ordner (kein Benutzerwechsel, Besitzer und Modus der Testmaschine zählen für den Lader nicht):
#   WAMO_LABOR_DATEN_WURZEL=/tmp/probe WAMO_LABOR_DATEN_LADER=voice-core WAMO_LABOR_DATEN_PYTHON=python bash wamo-labor-daten-einrichten.sh status
set -euo pipefail
umask 022

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_LABOR_DATEN_WURZEL:-}"   # nur für den Probelauf; leer ist das echte System
QUELLE="$HERE/labor-daten"
ELTERN="$WURZEL/etc/wamo-labor"   # legt wamo-labor-einrichten.sh an (root:root 0755), der Kern kann ihn durchqueren
ZIEL="$ELTERN/daten"
KERN_DATEN_PFAD=/etc/wamo-labor/daten   # der Wert für den Kern: immer der Pfad am Server, nie ein Pfad der Probewurzel
CORE_ENV=/usr/local/sbin/wamo-core-env
[ -z "$WURZEL" ] || CORE_ENV="$WURZEL/usr/local/sbin/wamo-core-env"
SICHERUNG_BASIS="$WURZEL/var/backups/wamo-labor-daten"
KERN_APP="$WURZEL/opt/wamo-core"
KERN_BENUTZER=wamo-core
NAME_RE='^v[0-9A-Za-z._-]{1,40}$'
REGEL=persona-zusatz.md
STRENG=persona-zusatz.streng.md
DATEN=(kontakte.md anforderung-vorlage.md wissen-istqb.md bericht-vorlage.md ausgang.md ctfl-zuordnung.json)
ALLE=("$REGEL" "$STRENG" "${DATEN[@]}")
OWNER=(-o root -g root)
[ -z "$WURZEL" ] || OWNER=()
SICHERUNG_MODUS=0700   # im Probelauf 0755: Git Bash unter Windows kann einem Ordner den Modus 0700 nicht geben, ohne ihn selbst zu sperren
[ -z "$WURZEL" ] || SICHERUNG_MODUS=0755

BEFEHL="${1:-}"
[ "$#" -eq 0 ] || shift
TROCKEN=nein
PERSONA=""
RELEASE=""
STAGE=""
SICHERUNG=""
LADER_ORDNER=""
LADER_PYTHON=""
LADER_FEHLER=""

fehler() { echo "wamo-labor-daten: $*" >&2; exit 1; }
vorbedingung() { echo "wamo-labor-daten: Vorbedingung nicht erfüllt: $*" >&2; exit 3; }
aufruf() {
  echo "wamo-labor-daten: $*" >&2
  echo "Aufruf: $0 einrichten [--persona standard|streng] [--release NAME] [--trocken] | status [--release NAME] | rueckweg [--release NAME] [--trocken]" >&2
  exit 2
}
plan() { printf '  [trocken] %s\n' "$*"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --trocken) [ "$BEFEHL" = einrichten ] || [ "$BEFEHL" = rueckweg ] || aufruf "--trocken gibt es nur bei einrichten und rueckweg"; TROCKEN=ja ;;
    --persona)
      [ "$BEFEHL" = einrichten ] || aufruf "--persona gibt es nur bei einrichten"
      shift
      case "${1:-}" in standard|streng) PERSONA="$1" ;; *) aufruf "--persona verlangt standard oder streng" ;; esac ;;
    --release)
      shift
      [[ "${1:-}" =~ $NAME_RE ]] || aufruf "--release verlangt einen Release-Namen (v plus Buchstaben, Ziffern, Punkt, Strich, Unterstrich)"
      RELEASE="$1" ;;
    *) aufruf "unbekannte Angabe: $1" ;;
  esac
  shift
done
case "$BEFEHL" in einrichten|status|rueckweg) ;; *) aufruf "Unterbefehl fehlt oder ist unbekannt" ;; esac
ZUSATZ=""
[ "$TROCKEN" = nein ] || ZUSATZ=" (Trockenlauf)"

# Schutz gegen untergeschobene Dateien (wie in den übrigen Einrichtungsskripten): Der Ordner dieses Skripts, sein Elternordner und alles darin
# (auch labor-daten/) müssen dem Aufrufer, bei sudo dem aufrufenden Benutzer, oder root gehören und dürfen weder für die Gruppe noch für alle
# beschreibbar sein. Symbolische Links sind nicht erlaubt.
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

if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || fehler "Bitte als root ausführen (sudo)."
  pruefe_herkunft
else
  # Der Probelauf schreibt nur in einen Wegwerf-Ordner und läuft nie als root (sonst könnte eine gesetzte Variable das Skript auf fremde Pfade lenken).
  [ "$(id -u)" -ne 0 ] || fehler "Der Probelauf (WAMO_LABOR_DATEN_WURZEL) läuft nicht als root."
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      fehler "WAMO_LABOR_DATEN_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich ohne // und ohne . oder .. sein." ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein Benutzerwechsel, Rechte der Testmaschine)"
fi

aufraeumen() {
  case "$STAGE" in */wamo-labor-stage.*) rm -rf -- "$STAGE" ;; esac
}
trap aufraeumen EXIT

# ---------------------------------------------------------------- Lieferung (Quelle)
quelle_datei() {   # Pfad der Quelle zu einer Zielfassung: die Regeln kommen je nach Fassung aus der Standard- oder der strengen Datei
  if [ "$1" = "$REGEL" ] && [ "$2" = streng ]; then printf '%s\n' "$QUELLE/$STRENG"; else printf '%s\n' "$QUELLE/$1"; fi
}

quelle_pruefen() {
  [ -d "$QUELLE" ] && [ ! -L "$QUELLE" ] || vorbedingung "$QUELLE fehlt (infra/sokrates2 vollständig verteilen)."
  local n
  for n in "${ALLE[@]}"; do
    [ -f "$QUELLE/$n" ] && [ ! -L "$QUELLE/$n" ] || vorbedingung "$QUELLE/$n fehlt oder ist keine reguläre Datei."
  done
}

gleich_quelle() {   # $1 Datei im Ziel, $2 Quelle: gleicher Inhalt, die Zeilenenden der Quelle (CR) zählen nicht
  [ -f "$1" ] && [ ! -L "$1" ] && cmp -s <(tr -d '\r' < "$2") "$1"
}

aktive_persona() {   # keine | standard | streng | abweichend
  if [ ! -f "$ZIEL/$REGEL" ]; then echo keine
  elif gleich_quelle "$ZIEL/$REGEL" "$QUELLE/$STRENG"; then echo streng
  elif gleich_quelle "$ZIEL/$REGEL" "$QUELLE/$REGEL"; then echo standard
  else echo abweichend; fi
}

persona_bestimmen() {
  local aktiv
  aktiv="$(aktive_persona)"
  if [ -n "$PERSONA" ]; then
    echo "  Fassung der Regeln: $PERSONA (gewählt, bisher: $aktiv)"
    return 0
  fi
  if [ "$aktiv" = streng ]; then PERSONA=streng; else PERSONA=standard; fi
  echo "  Fassung der Regeln: $PERSONA (nicht angegeben, bisher: $aktiv)"
}

# ---------------------------------------------------------------- Lader
PY_OPT=(-X utf8 -B -I)   # UTF-8 ohne Umgebung, keine Bytecode-Dateien im Release, isolierter Modus (keine Umgebungsvariablen, kein Arbeitsordner im Suchpfad)

als_kern() {   # im Betrieb als wamo-core (so liest auch der Kern), im Probelauf direkt
  if [ -n "$WURZEL" ]; then "$@"
  else ( cd / && runuser -u "$KERN_BENUTZER" -- env -i PATH=/usr/bin:/bin HOME=/var/lib/wamo-core "$@" ); fi
}

nur_root_schreibbar() {   # jeder Pfad gehört root und ist weder für die Gruppe noch für alle beschreibbar (der Lader läuft als Code aus dem Release)
  local p uid modus
  for p in "$@"; do
    read -r uid modus < <(stat -c '%u %a' "$p") || return 1
    if [ "$uid" != 0 ] || (( 8#$modus & 8#022 )); then return 1; fi
  done
}

lader_release_waehlen() {   # setzt LADER_ORDNER und LADER_PYTHON aus dem Release des Kerns, gibt sonst 1 zurück
  local rel="" d
  if [ -n "$RELEASE" ]; then rel="$KERN_APP/releases/$RELEASE"
  else
    while IFS= read -r d; do
      if [ -f "$d/voice-core/labor_daten.py" ]; then rel="$d"; break; fi
    done < <(ls -1dt "$KERN_APP"/releases/v* 2>/dev/null || true)
  fi
  if [ -z "$rel" ] || [ ! -f "$rel/voice-core/labor_daten.py" ]; then
    LADER_FEHLER="kein Release des Kerns mit voice-core/labor_daten.py unter $KERN_APP/releases (--release NAME angeben, der Lab-Release muss zuerst angelegt sein)"
    return 1
  fi
  if ! nur_root_schreibbar "$rel" "$rel/voice-core" "$rel/voice-core/labor_daten.py"; then
    LADER_FEHLER="$rel gehört nicht root oder ist für Gruppe oder alle beschreibbar (der Lader läuft als Code aus dem Release)"
    return 1
  fi
  LADER_ORDNER="$rel/voice-core"
  if [ -x "$rel/venv/bin/python" ]; then LADER_PYTHON="$rel/venv/bin/python"; else LADER_PYTHON=/usr/bin/python3; fi
}

lader_suchen() {   # setzt LADER_ORDNER und LADER_PYTHON; gibt 1 zurück und nennt den Grund in LADER_FEHLER
  LADER_FEHLER=""
  if [ -n "$WURZEL" ]; then   # Probelauf: Lader und Interpreter kommen aus der Umgebung
    LADER_ORDNER="${WAMO_LABOR_DATEN_LADER:-}"
    LADER_PYTHON="${WAMO_LABOR_DATEN_PYTHON:-python3}"
    [ -f "$LADER_ORDNER/labor_daten.py" ] || { LADER_FEHLER="Probelauf: WAMO_LABOR_DATEN_LADER zeigt auf keinen Ordner mit labor_daten.py"; return 1; }
  else
    lader_release_waehlen || return 1
  fi
  if ! als_kern "$LADER_PYTHON" "${PY_OPT[@]}" -c 'import loguru' 2>/dev/null; then
    LADER_FEHLER="$LADER_PYTHON findet das Paket loguru nicht, der Lader (labor_daten.py) braucht es. Mit --release NAME ein Release mit venv wählen"
    return 1
  fi
}

lader_vorbereiten() {   # Wegwerf-Ordner (falls noch keiner da ist) mit einem kleinen Programm, das den echten Lader aufruft und nur Namen, Prüfsummen und
                        # Ablehnungstypen ausgibt, nie Inhalt
  if [ -z "$STAGE" ]; then
    STAGE="$(mktemp -d /tmp/wamo-labor-stage.XXXXXX)"
    chmod 0755 "$STAGE"
  fi
  cat > "$STAGE/pruefer.py" <<'PYTHON'
import sys

sys.path.insert(0, sys.argv[1])
import labor_daten as ld

richtlinie = ld.Richtlinie(besitzer=None, verboten_modus=0) if sys.argv[2] == "probe" else ld.Richtlinie()
d = ld.laden(richtlinie=richtlinie)  # ohne Ordner: der Lader fragt WAMO_LABOR_DATEN, genau wie im Kern
for name in ld.DATEIEN_ALLE:
    if name in d.pruefsummen:
        print("    " + name + ": gelesen, Prüfsumme " + d.pruefsummen[name])
    else:
        print("    " + name + ": ABGELEHNT (" + d.abgelehnt.get(name, d.abgelehnt.get("*", "unbekannt")) + ")")
summe = len(d.regeln) + sum(len(text) for _, text in d.daten)
print("    Zeichen gesamt " + str(summe) + " von " + str(ld.MAX_GESAMT) + ", Regelteil " + str(len(d.regeln)) + " von " + str(ld.MAX_REGELN))
sys.exit(0 if not d.abgelehnt and len(d.pruefsummen) == len(ld.DATEIEN_ALLE) else 1)
PYTHON
  chmod 0644 "$STAGE/pruefer.py"
}

lader_pruefen() {   # $1 Ordner mit den Dateien, der Lader bekommt ihn über WAMO_LABOR_DATEN wie im Kern: 0, wenn er keine Datei ablehnt
  local modus=betrieb
  [ -z "$WURZEL" ] || modus=probe
  als_kern env "WAMO_LABOR_DATEN=$1" "$LADER_PYTHON" "${PY_OPT[@]}" "$STAGE/pruefer.py" "$LADER_ORDNER" "$modus"
}

# ---------------------------------------------------------------- Vorbereiten, Vergleichen
staging() {   # Kopie der Lieferung in einem Wegwerf-Ordner (so wird geprüft, was danach installiert wird); die Zeilenenden werden auf LF gebracht
  local persona="$1" n
  lader_vorbereiten
  mkdir "$STAGE/daten"
  chmod 0755 "$STAGE/daten"
  for n in "${ALLE[@]}"; do
    tr -d '\r' < "$(quelle_datei "$n" "$persona")" > "$STAGE/daten/$n"
    chmod 0644 "$STAGE/daten/$n"
  done
}

dateistand() {   # neu | geändert | unverändert
  if [ ! -f "$ZIEL/$1" ]; then echo neu
  elif cmp -s "$STAGE/daten/$1" "$ZIEL/$1"; then echo unverändert
  else echo geändert; fi
}

rechte_ok() {   # $1 Pfad, $2 Modus; im Betrieb muss er root gehören
  local uid modus
  read -r uid modus < <(stat -c '%u %a' "$1") || return 1
  [ "$modus" = "$2" ] && { [ -n "$WURZEL" ] || [ "$uid" = 0 ]; }
}

stand_gleich() {   # alle acht Dateien gleich der Lieferung, Rechte wie vorgesehen
  local n
  [ -d "$ZIEL" ] && [ ! -L "$ZIEL" ] && rechte_ok "$ZIEL" 755 || return 1
  for n in "${ALLE[@]}"; do
    [ -f "$ZIEL/$n" ] && [ ! -L "$ZIEL/$n" ] && cmp -s "$STAGE/daten/$n" "$ZIEL/$n" && rechte_ok "$ZIEL/$n" 644 || return 1
  done
}

ziel_pruefen() {   # nie in einen Link oder an die falsche Stelle schreiben
  [ -d "$ELTERN" ] && [ ! -L "$ELTERN" ] || vorbedingung "$ELTERN muss existieren und ein echter Ordner sein, kein Link (zuerst wamo-labor-einrichten.sh)."
  [ -x "$CORE_ENV" ] || [ -n "$WURZEL" ] || vorbedingung "$CORE_ENV fehlt (zuerst wamo-core-einrichten.sh), ohne ihn lässt sich WAMO_LABOR_DATEN für den Kern nicht setzen."
  [ ! -L "$ZIEL" ] || vorbedingung "$ZIEL ist ein Link: nichts angefasst."
  [ ! -e "$ZIEL" ] || [ -d "$ZIEL" ] || vorbedingung "$ZIEL ist kein Ordner: nichts angefasst."
  local n
  for n in "${ALLE[@]}"; do
    if [ -L "$ZIEL/$n" ] || { [ -e "$ZIEL/$n" ] && [ ! -f "$ZIEL/$n" ]; }; then vorbedingung "$ZIEL/$n ist ein Link oder keine reguläre Datei: nichts angefasst."; fi
  done
}

# ---------------------------------------------------------------- Sicherung und Rückweg
sichern() {   # $1 Endung des Ordnernamens (leer oder .vor-rueckweg): legt die Sicherung des Ist-Stands an
  local name n i=1
  name="$(date -u +%Y%m%d-%H%M%S)"
  SICHERUNG="$SICHERUNG_BASIS/$name$1"
  while [ -e "$SICHERUNG" ]; do i=$((i + 1)); SICHERUNG="$SICHERUNG_BASIS/$name-$i$1"; done
  install -d -m "$SICHERUNG_MODUS" "${OWNER[@]}" "$SICHERUNG_BASIS" "$SICHERUNG"
  i=0
  for n in "${ALLE[@]}"; do
    if [ -f "$ZIEL/$n" ] && [ ! -L "$ZIEL/$n" ]; then cp -p "$ZIEL/$n" "$SICHERUNG/$n"; i=$((i + 1)); fi
  done
  # zuletzt: erst damit gilt die Sicherung als vollständig; die Zeile Ziel bindet sie an diesen Ort (eine Sicherung des früheren Ortes zählt nie)
  printf 'Ziel: %s\nDateien vorher: %s\n' "$ZIEL" "$i" > "$SICHERUNG/VORHER"
  echo "  Sicherung des vorigen Stands: $SICHERUNG ($i Dateien)"
}

wiederherstellen() {   # $1 Sicherungsordner: die acht Dateien wie dort, was dort fehlte, wird entfernt
  local n
  for n in "${ALLE[@]}"; do
    if [ -f "$1/$n" ]; then
      install -d -m 0755 "${OWNER[@]}" "$ZIEL"
      install -m 0644 "${OWNER[@]}" "$1/$n" "$ZIEL/.$n.neu"
      mv -f "$ZIEL/.$n.neu" "$ZIEL/$n"
    else
      rm -f -- "$ZIEL/$n"
    fi
  done
  rm -f -- "$ZIEL"/.*.neu
  rmdir "$ZIEL" 2>/dev/null || true   # nur, wenn nichts mehr darin liegt
}

letzte_sicherung() {   # Pfad der jüngsten vollständigen Sicherung für dieses Ziel (VORHER mit der Zeile Ziel); ohne sie Exit 1
  local d letzte=""
  [ -d "$SICHERUNG_BASIS" ] || return 1
  while IFS= read -r d; do
    if [ -f "$SICHERUNG_BASIS/$d/VORHER" ] && grep -qxF "Ziel: $ZIEL" "$SICHERUNG_BASIS/$d/VORHER"; then letzte="$d"; fi
  done < <(ls -1 "$SICHERUNG_BASIS" | grep -E '^[0-9]{8}-[0-9]{6}(-[0-9])?$' | sort || true)
  [ -n "$letzte" ] || return 1
  printf '%s\n' "$SICHERUNG_BASIS/$letzte"
}

installieren() {
  local n
  install -d -m 0755 "${OWNER[@]}" "$ZIEL"
  for n in "${ALLE[@]}"; do
    install -m 0644 "${OWNER[@]}" "$STAGE/daten/$n" "$ZIEL/.$n.neu"
    mv -f "$ZIEL/.$n.neu" "$ZIEL/$n"
  done
}

# ---------------------------------------------------------------- Einstellung des Kerns
kern_einstellen() {   # genau eine Einstellung: WAMO_LABOR_DATEN, über wamo-core-env und stdin (nie über die Befehlszeile); startet nichts neu
  echo "== Kern: WAMO_LABOR_DATEN=$KERN_DATEN_PFAD (über wamo-core-env)"
  if [ "$TROCKEN" = ja ]; then plan "$CORE_ENV setzen: WAMO_LABOR_DATEN=$KERN_DATEN_PFAD (über stdin)"; return 0; fi
  if [ ! -x "$CORE_ENV" ]; then echo "  Probelauf: kein wamo-core-env in der Wurzel, übersprungen."; return 0; fi
  printf 'WAMO_LABOR_DATEN=%s\n' "$KERN_DATEN_PFAD" | "$CORE_ENV" setzen \
    || fehler "WAMO_LABOR_DATEN ließ sich nicht setzen. Die Dateien sind eingerichtet und vom Lader geprüft, den Lauf wiederholen."
  echo "  Die Einstellung gilt erst mit dem nächsten Kern-Release (sudo bash ~/wamo-infra/wamo-core-slots.sh umschalten-kern <release>)."
  echo "  Der Kern wird hier nie neu gestartet. Bis dahin liest er den Standardort und läuft mit dem reinen Laborprompt."
}

# ---------------------------------------------------------------- Befehle
einrichten() {
  local n
  echo "== Labor-Daten einrichten$ZUSATZ"
  quelle_pruefen
  ziel_pruefen
  persona_bestimmen
  lader_suchen || vorbedingung "$LADER_FEHLER. Nichts wurde geändert."
  staging "$PERSONA"
  echo "== Lieferung mit dem echten Lader prüfen (Wegwerf-Ordner, nichts am Ziel geändert)"
  lader_pruefen "$STAGE/daten" || fehler "Der Lader lehnt die Lieferung ab (siehe oben). Nichts wurde geändert."
  echo "== Ziel $ZIEL"
  for n in "${ALLE[@]}"; do echo "  $n: $(dateistand "$n")"; done
  if stand_gleich; then
    echo "  Alles unverändert, nichts zu tun."
    kern_einstellen
    return 0
  fi
  if [ "$TROCKEN" = ja ]; then
    plan "Sicherung des vorigen Stands nach $SICHERUNG_BASIS/<Zeit>"
    plan "Ordner $ZIEL (root:root 0755) und acht Dateien (root:root 0644) schreiben, Fassung der Regeln: $PERSONA"
    plan "Lader am Ziel prüfen, bei Ablehnung alles zurücknehmen"
    kern_einstellen
    echo "  Trockenlauf: es wurde nichts geändert."
    return 0
  fi
  sichern ""
  installieren
  echo "== Lader am Ziel"
  if ! lader_pruefen "$ZIEL"; then
    echo "  Der Lader lehnt eine Datei am Ziel ab: alles wird zurückgenommen." >&2
    wiederherstellen "$SICHERUNG"
    fehler "Zurückgenommen auf den Stand vor dem Lauf (Sicherung $SICHERUNG)."
  fi
  echo "  Eingerichtet. Der Kern liest die Dateien bei jedem Sitzungsstart, sobald er den Ordner kennt."
  kern_einstellen
}

zeile_status() {   # eine Datei: Name, Prüfsumme, Vergleich mit der Lieferung, Rechte
  local n="$1" summe vergleich quelle
  if [ ! -f "$ZIEL/$n" ]; then echo "  $n: fehlt"; return 0; fi
  summe="$(sha256sum "$ZIEL/$n" | cut -c1-16)"
  quelle="$QUELLE/$n"
  if [ "$n" = "$REGEL" ]; then quelle="$QUELLE/$([ "$(aktive_persona)" = streng ] && echo "$STRENG" || echo "$REGEL")"; fi
  if gleich_quelle "$ZIEL/$n" "$quelle"; then vergleich="gleich der Lieferung"; else vergleich="WEICHT von der Lieferung ab"; fi
  echo "  $n: $summe, $vergleich, $(stat -c '%s Byte, Rechte %a, Besitzer %U:%G' "$ZIEL/$n")"
}

status() {
  local n d anzahl=0
  echo "== Status"
  quelle_pruefen
  if [ -d "$ZIEL" ]; then echo "  Ordner: $ZIEL ($(stat -c 'Rechte %a, Besitzer %U:%G' "$ZIEL"))"; else echo "  Ordner $ZIEL fehlt"; fi
  for n in "${ALLE[@]}"; do zeile_status "$n"; done
  echo "  Fassung der Regeln: $(aktive_persona)"
  echo "== Einstellung des Kerns (nur gesetzt oder nicht, nie der Wert)"
  if [ -x "$CORE_ENV" ]; then
    "$CORE_ENV" liste 2>/dev/null | grep -E '^WAMO_LABOR_DATEN:' | sed 's/^/  /' || echo "  WAMO_LABOR_DATEN: nicht gesetzt"
  else echo "  wamo-core-env nicht vorhanden"; fi
  echo "== Urteil des Laders"
  if [ ! -d "$ZIEL" ]; then echo "  nichts zu prüfen"
  elif lader_suchen; then
    lader_vorbereiten
    lader_pruefen "$ZIEL" || echo "  Der Lader lehnt mindestens eine Datei ab (siehe oben)."
  else echo "  nicht prüfbar: $LADER_FEHLER"; fi
  echo "== Sicherungen"
  if [ -d "$SICHERUNG_BASIS" ]; then
    while IFS= read -r d; do echo "  $d"; anzahl=$((anzahl + 1)); done < <(ls -1 "$SICHERUNG_BASIS" | sort || true)
  fi
  echo "  Anzahl: $anzahl"
}

rueckweg() {
  local letzte n
  echo "== Rückweg$ZUSATZ"
  letzte="$(letzte_sicherung)" || vorbedingung "keine vollständige Sicherung unter $SICHERUNG_BASIS, es gibt nichts zurückzusetzen."
  echo "  Letzte Sicherung: $letzte"
  for n in "${ALLE[@]}"; do
    if [ -f "$letzte/$n" ]; then echo "  $n: wird aus der Sicherung hergestellt"; else echo "  $n: gab es vorher nicht, wird entfernt"; fi
  done
  if [ "$TROCKEN" = ja ]; then
    plan "Stand vor dem Rückweg als <Zeit>.vor-rueckweg sichern, dann die Sicherung herstellen"
    echo "  Trockenlauf: es wurde nichts geändert."
    return 0
  fi
  sichern ".vor-rueckweg"
  wiederherstellen "$letzte"
  echo "  Zurückgesetzt auf den Stand der Sicherung $(basename "$letzte"). Der Kern liest den Stand beim nächsten Sitzungsstart."
  if [ -d "$ZIEL" ] && lader_suchen; then
    lader_vorbereiten
    lader_pruefen "$ZIEL" || echo "  Hinweis: Der Lader lehnt am wiederhergestellten Stand mindestens eine Datei ab (der Stand war schon vorher so)."
  fi
}

case "$BEFEHL" in
  einrichten) einrichten ;;
  status) status ;;
  rueckweg) rueckweg ;;
esac
echo "Fertig."
