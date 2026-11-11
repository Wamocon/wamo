#!/usr/bin/env bash
# Ablaufprobe für F7 (Paket FP1-1): belegt, dass sshd die Option expiry-time in authorized_keys wirklich durchsetzt.
# Die Schlüsselzeile des Kerns bekommt von wamo-job-kern-einrichten.sh schluessel zusätzlich expiry-time (der Tag nach ABLAUF) als zweite
# Schicht neben ABLAUF im Dispatcher. Diese Probe zeigt am echten sshd dieses Servers, dass die Schicht trägt, ohne die Produktion zu berühren:
# Sie startet eine ZWEITE sshd-Instanz, nur auf 127.0.0.1:22222, mit eigener Konfiguration (keine Include-Zeile, kein Match), eigenem
# Hostschlüssel, eigener authorized_keys, einem festen ForceCommand (Echo) und einem Wegwerf-Schlüssel. Angemeldet wird ein Wegwerf-Benutzer
# (wamo-ablaufprobe, weder wamo noch root, AllowUsers), den die Probe anlegt und danach wieder löscht. Alles entsteht in einem Ordner unter /run
# und wird beim Beenden (trap, auch bei Abbruch) aufgeräumt. Nichts in /etc, kein Dienst, kein Journal der Produktion (die Instanz schreibt
# in eine eigene Logdatei, sshd -E), kein fail2ban-Eintrag. Alle Proben sind nur am Server prüfbar.
#
#   ssh wamo-sokrates2 "sudo bash ~/wamo-infra/wamo-job-kern-ablaufprobe.sh [--trocken]"
#       --trocken prüft nur die Vorbedingungen (root, Programme, Benutzer und Port frei) und schreibt nichts.
#
# Proben (die Tabelle am Ende hält das Ergebnis fest; ok, FEHLER oder notiert; ein FEHLER ergibt Exit 1):
#   A   expiry-time drei Minuten voraus (Form JJJJMMTTHHMM): die Anmeldung gelingt.
#   B   nach dem Ablauf: die Anmeldung scheitert (Permission denied), die Journalzeilen der Instanz stehen wörtlich in der Ausgabe.
#   B2  Kontrolle: dieselbe Zeile ohne expiry-time wird wieder angenommen (die Ablehnung in B kam also vom Ablauf).
#   C   eine vor dem Ablauf geöffnete Sitzung bleibt über den Ablauf hinaus bestehen (ein Ablauf trennt keine laufende Sitzung).
#   D1  reines Datum von gestern: abgelehnt. D2  von morgen: angenommen. D3  von heute: wird nur notiert (nach Annahme 7 gilt ein reines
#       Datum ab 00:00 Systemzeit, also abgelehnt; wer anderes beobachtet, muss den Wert expiry-time neu bewerten).
#   E   ein Tippfehler im Optionsnamen (expiry-tme) macht die Zeile ungültig: die Anmeldung scheitert.
#   F   Zeitzone der Grenze laut timedatectl, nur notiert.
# Die Probe dauert rund vier Minuten (A bis C warten auf die Minute des Ablaufs). Sie bricht nach A ab, wenn schon A scheitert.
#
# Exit-Codes: 0 alle Proben ok, 1 eine Probe oder ein Schritt ist fehlgeschlagen, 2 falsche Aufrufform, 3 Vorbedingung nicht erfüllt.
# Probelauf in einem Wegwerf-Ordner (ohne root, ohne sshd, mit Attrappen in $WURZEL/attrappen und einer Uhr aus $WURZEL/attrappen/jetzt):
#   WAMO_JOB_KERN_WURZEL=/tmp/ablauf-probe bash wamo-job-kern-ablaufprobe.sh
set -euo pipefail
umask 077
unset TZ   # sshd rechnet expiry-time in der Systemzeitzone: date soll dieselbe Zone nehmen

WURZEL="${WAMO_JOB_KERN_WURZEL:-}"   # nur für den Probelauf; leer = das echte System
PORT=22222
BENUTZER=wamo-ablaufprobe
VORAUS_MIN=3          # expiry-time der Proben A bis C: so viele Minuten voraus (abgerundet auf die volle Minute)
HALTEN_REST_S=30      # die gehaltene Sitzung (C) endet so viele Sekunden nach dem Ablauf
NACH_ABLAUF_S=5       # B beginnt so viele Sekunden nach dem Ablauf
SSHD=/usr/sbin/sshd
SSH=/usr/bin/ssh
KEYGEN=/usr/bin/ssh-keygen
BASIS=/run            # unter /run: sshd (StrictModes) verlangt Ordner, die nur root schreiben darf, und /tmp ist es nicht
if [ -n "$WURZEL" ]; then   # Probelauf: Programme kommen aus $WURZEL/attrappen
  PATH="$WURZEL/attrappen:$PATH"
  SSHD=sshd SSH=ssh KEYGEN=ssh-keygen
  BASIS="$WURZEL/run"
fi

fehler() { echo "wamo-job-kern-ablaufprobe: $*" >&2; exit 1; }
aufruf() { echo "wamo-job-kern-ablaufprobe: $*" >&2; echo "Aufruf: [--trocken]" >&2; exit 2; }
vorbedingung() { echo "wamo-job-kern-ablaufprobe: Vorbedingung nicht erfüllt: $*" >&2; exit 3; }

TROCKEN=0
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=1 ;;
    *) aufruf "unbekannte Angabe: $arg" ;;
  esac
done

# ---------------------------------------------------------------- Umgebung: echtes System oder Probelauf
if [ -z "$WURZEL" ]; then
  [ "$(id -u)" -eq 0 ] || fehler "Bitte mit sudo ausführen."
  [ "$(uname -s)" = Linux ] || vorbedingung "die Probe läuft nur auf dem Server (Linux)"
else
  [ "$(id -u)" -ne 0 ] || fehler "Der Probelauf (WAMO_JOB_KERN_WURZEL) läuft nicht als root."
  case "$WURZEL" in
    /|*//*|*/..|*/../*|*/.|*/./*|*[!A-Za-z0-9._/-]*|[!/]*)
      fehler "WAMO_JOB_KERN_WURZEL muss ein absoluter Ordner aus A-Z, a-z, 0-9, Punkt, Unterstrich, Bindestrich und Schrägstrich ohne // und ohne . oder .. als Pfadteil sein." ;;
  esac
  echo "Probelauf unter $WURZEL (kein root, kein echter sshd, keine echten Anmeldungen)"
fi

# ---------------------------------------------------------------- Aufräumen (immer, auch bei Abbruch)
ARBEIT="" SSHD_PID="" HALTEN_PID="" ANGELEGT=0
aufraeumen() {
  local rc=$?
  trap - EXIT
  if [ -n "$HALTEN_PID" ]; then kill "$HALTEN_PID" 2>/dev/null || true; fi
  if [ -n "$SSHD_PID" ]; then kill "$SSHD_PID" 2>/dev/null || true; wait "$SSHD_PID" 2>/dev/null || true; fi
  if [ "$ANGELEGT" = 1 ]; then   # nur der Benutzer, den diese Probe selbst angelegt hat
    pkill -KILL -u "$BENUTZER" 2>/dev/null || true
    userdel "$BENUTZER" 2>/dev/null || echo "Hinweis: der Benutzer $BENUTZER konnte nicht entfernt werden: bitte von Hand (userdel $BENUTZER)" >&2
  fi
  if [ -n "$ARBEIT" ] && [ -d "$ARBEIT" ]; then rm -rf -- "$ARBEIT"; fi
  return "$rc"
}
trap aufraeumen EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

# ---------------------------------------------------------------- Vorbedingungen
port_lauscher() {   # die lokalen Adressen, an denen etwas auf dem Port der Probe lauscht (eine je Zeile)
  ss -ltn 2>/dev/null | awk -v p="$PORT" '$4 ~ (":" p "$") { print $4 }'
}

echo "== 0. Vorbedingungen (nur lesend)"
for programm in "$SSHD" "$SSH" "$KEYGEN" useradd userdel getent ss pkill; do
  command -v "$programm" >/dev/null 2>&1 || vorbedingung "$programm fehlt"
done
if getent passwd "$BENUTZER" >/dev/null 2>&1; then
  vorbedingung "der Benutzer $BENUTZER besteht schon (Rest eines früheren Laufs?): erst prüfen, dass ihn niemand braucht, dann userdel $BENUTZER"
fi
if [ -n "$(port_lauscher)" ]; then
  vorbedingung "auf Port $PORT lauscht schon etwas ($(port_lauscher | tr '\n' ' ')): die Probe braucht ihn frei"
fi
echo "  Benutzer $BENUTZER frei, Port $PORT frei"
if [ "$TROCKEN" = 1 ]; then
  echo "Trockenlauf: nichts wird geändert. Die Probe würde in einem Ordner unter ${BASIS} eine sshd-Instanz auf 127.0.0.1:$PORT starten,"
  echo "  den Benutzer $BENUTZER anlegen, die Proben A bis F ausführen (rund vier Minuten) und alles wieder entfernen."
  exit 0
fi

# ---------------------------------------------------------------- Aufbau der Instanz
echo "== 1. Aufbau: eigener Hostschlüssel, Wegwerf-Schlüssel, eigene Konfiguration unter $BASIS"
ARBEIT="$(mktemp -d "$BASIS/wamo-ablaufprobe.XXXXXX")"
chmod 0755 "$ARBEIT"   # sshd liest authorized_keys als der angemeldete Benutzer: der Ordner muss durchsuchbar sein, bleibt aber root-eigen
"$KEYGEN" -q -t ed25519 -N '' -C wamo-ablaufprobe-host -f "$ARBEIT/hostkey"
"$KEYGEN" -q -t ed25519 -N '' -C wamo-ablaufprobe -f "$ARBEIT/probe_key"
read -r KEYTYP KEYBLOB _ < "$ARBEIT/probe_key.pub"
read -r HOSTTYP HOSTBLOB _ < "$ARBEIT/hostkey.pub"
printf '[127.0.0.1]:%s %s %s\n' "$PORT" "$HOSTTYP" "$HOSTBLOB" > "$ARBEIT/known_hosts"
: > "$ARBEIT/sshd.log"
cat > "$ARBEIT/force.sh" <<'FORCE'
#!/bin/sh
# der feste Befehl der Probe: ein Echo, und für Probe C ein Halten von höchstens 300 Sekunden (sonst nichts, nie eine Shell)
echo wamo-ablaufprobe-ok
case "${SSH_ORIGINAL_COMMAND:-}" in
  "halten "[0-9]*)
    n="${SSH_ORIGINAL_COMMAND#halten }"
    case "$n" in *[!0-9]*) exit 0 ;; esac
    [ "$n" -le 300 ] || n=300
    sleep "$n"
    echo noch-da ;;
esac
FORCE
chmod 0644 "$ARBEIT/force.sh"
cat > "$ARBEIT/sshd_config" <<KONF
# Wegwerf-Konfiguration der Ablaufprobe: nur Loopback, nur ein Benutzer, nur dieser Schlüssel, nur ein Echo. Nie die Konfiguration der Produktion.
Port $PORT
ListenAddress 127.0.0.1
AddressFamily inet
HostKey $ARBEIT/hostkey
PidFile $ARBEIT/sshd.pid
LogLevel DEBUG1
AllowUsers $BENUTZER
PermitRootLogin no
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
HostbasedAuthentication no
GSSAPIAuthentication no
UsePAM no
StrictModes yes
AuthorizedKeysFile $ARBEIT/authorized_keys
AuthorizedKeysCommand none
AuthorizedPrincipalsFile none
ForceCommand /bin/sh $ARBEIT/force.sh
AllowTcpForwarding no
AllowAgentForwarding no
AllowStreamLocalForwarding no
X11Forwarding no
PermitTunnel no
PermitTTY no
PermitUserRC no
PermitUserEnvironment no
MaxAuthTries 2
MaxSessions 2
LoginGraceTime 20
PrintMotd no
UseDNS no
KONF
chmod 0600 "$ARBEIT/sshd_config" "$ARBEIT/hostkey" "$ARBEIT/probe_key"
"$SSHD" -t -f "$ARBEIT/sshd_config" || vorbedingung "sshd -t lehnt die Konfiguration der Probe ab: sie wird nicht gestartet"

zeile_setzen() {   # die Optionen der einen Zeile in authorized_keys der Instanz (atomar), dazu Schlüsseltyp, Schlüssel und Kommentar
  printf '%s %s %s wamo-ablaufprobe\n' "$1" "$KEYTYP" "$KEYBLOB" > "$ARBEIT/authorized_keys.neu"
  chmod 0644 "$ARBEIT/authorized_keys.neu"
  mv -f "$ARBEIT/authorized_keys.neu" "$ARBEIT/authorized_keys"
}
optionen() { printf 'restrict,from="127.0.0.1",%s' "$1"; }   # wie die Zeile der Produktion (restrict, from=), dazu die Option der Probe

echo "== 2. Wegwerf-Benutzer $BENUTZER und sshd-Instanz auf 127.0.0.1:$PORT"
useradd --system --no-create-home --home-dir / --shell /bin/sh --password '*' --comment "wamo Ablaufprobe Wegwerf-Benutzer" "$BENUTZER"
ANGELEGT=1
"$SSHD" -D -f "$ARBEIT/sshd_config" -E "$ARBEIT/sshd.log" &
SSHD_PID=$!
for _ in $(seq 1 20); do
  [ -z "$(port_lauscher)" ] || break
  sleep 1
done
[ -n "$(port_lauscher)" ] || fehler "die sshd-Instanz der Probe lauscht nach 20 Sekunden nicht auf Port $PORT (Log: $(tail -n 3 "$ARBEIT/sshd.log" | tr '\n' ' '))"
LAUSCHER="$(port_lauscher | tr '\n' ' ' | sed 's/ $//')"
kill -0 "$SSHD_PID" 2>/dev/null || fehler "die sshd-Instanz der Probe ist beendet, am Port lauscht etwas anderes ($LAUSCHER): die Probe wird abgebrochen"
[ "$LAUSCHER" = "127.0.0.1:$PORT" ] || fehler "die sshd-Instanz lauscht auf $LAUSCHER statt nur auf 127.0.0.1:$PORT: die Probe wird abgebrochen"
echo "  lauscht nur auf $LAUSCHER ($("$SSH" -V 2>&1 | head -n1))"

# ---------------------------------------------------------------- Hilfen für die Proben
SSH_ARGV=("$SSH" -F none -n -i "$ARBEIT/probe_key" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$ARBEIT/known_hosts" -o GlobalKnownHostsFile=none -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
  -o PreferredAuthentications=publickey -o ClearAllForwardings=yes -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3
  -o LogLevel=ERROR -T -p "$PORT" "$BENUTZER@127.0.0.1")
VERSUCH_RC=0 VERSUCH_AUS=""
versuch() { VERSUCH_AUS="$("${SSH_ARGV[@]}" "$@" 2>&1)" && VERSUCH_RC=0 || VERSUCH_RC=$?; }
angenommen() { [ "$VERSUCH_RC" = 0 ] && [[ "$VERSUCH_AUS" == *wamo-ablaufprobe-ok* ]]; }
abgelehnt() { [ "$VERSUCH_RC" != 0 ] && [[ "$VERSUCH_AUS" == *"Permission denied"* ]]; }   # ein Netzfehler zählt nie als Ablehnung

jetzt() { date +%s; }
log_zeilen() { wc -l < "$ARBEIT/sshd.log" | tr -d ' '; }
log_seit() {   # die Zeilen der Instanz seit der Marke, nur die zur Anmeldung und zum Ablauf, wörtlich (höchstens 6, je 220 Zeichen)
  tail -n "+$(($1 + 1))" "$ARBEIT/sshd.log" | grep -iE 'expir|key options|failed publickey|accepted publickey|not permitted|bad ownership' | cut -c1-220 | head -n 6 || true
}

TABELLE=() JOURNAL=() FEHLER_ZAHL=0
ergebnis() {   # kennung was erwartet beobachtet urteil (ok, FEHLER oder notiert)
  TABELLE+=("| $1 | $2 | $3 | $4 | $5 |")
  printf '  %-8s %s: %s\n' "$5" "$1" "$4"
  if [ "$5" = FEHLER ]; then FEHLER_ZAHL=$((FEHLER_ZAHL + 1)); fi
}
journal() {   # kennung zeilen: hält die Journalzeilen der Instanz wörtlich fest
  local z
  while IFS= read -r z; do [ -z "$z" ] || JOURNAL+=("$1: $z"); done <<< "$2"
}
ende() {   # Tabelle, Journal, Exit
  echo "== Ergebnis ($("$SSH" -V 2>&1 | head -n1), alle Proben nur am Server prüfbar)"
  echo "| Probe | Was | Erwartet | Beobachtet | Ergebnis |"
  echo "| --- | --- | --- | --- | --- |"
  printf '%s\n' "${TABELLE[@]}"
  if [ "${#JOURNAL[@]}" -gt 0 ]; then
    echo "Journal der Probe-Instanz (wörtlich, aus der eigenen Logdatei, nicht aus dem Journal der Produktion):"
    printf '  %s\n' "${JOURNAL[@]}"
  fi
  if [ "$FEHLER_ZAHL" = 0 ]; then echo "Ablaufprobe bestanden: sshd setzt expiry-time durch."; exit 0; fi
  echo "Ablaufprobe NICHT bestanden: $FEHLER_ZAHL Probe(n) mit FEHLER. expiry-time ist dann keine zweite Schicht, auf die man sich verlassen darf." >&2
  exit 1
}

# ---------------------------------------------------------------- Proben
echo "== 3. Proben A bis C: expiry-time $VORAUS_MIN Minuten voraus"
t0="$(jetzt)"
ablauf_epoch=$(( (t0 + VORAUS_MIN * 60) / 60 * 60 ))
ablauf_text="$(date -d "@$ablauf_epoch" +%Y%m%d%H%M)"
zeile_setzen "$(optionen "expiry-time=\"$ablauf_text\"")"
versuch probe
if angenommen; then
  ergebnis A "expiry-time $ablauf_text (3 Minuten voraus)" "Anmeldung gelingt" "Anmeldung gelang" ok
else
  ergebnis A "expiry-time $ablauf_text (3 Minuten voraus)" "Anmeldung gelingt" "Anmeldung scheiterte (Exit $VERSUCH_RC, $(printf '%s' "$VERSUCH_AUS" | head -n1 | cut -c1-100)): sshd kennt die Option nicht oder lehnt die Zeile ab" FEHLER
  journal A "$(log_seit 0)"
  echo "Die Probe wird nach A abgebrochen: ohne A sagen die weiteren Proben nichts." >&2
  ende
fi

# C beginnt hier: die Sitzung wird vor dem Ablauf geöffnet und hält bis kurz danach
halten_s=$(( ablauf_epoch + HALTEN_REST_S - $(jetzt) ))
"${SSH_ARGV[@]}" "halten $halten_s" > "$ARBEIT/halten.out" 2> "$ARBEIT/halten.err" &   # ein einfacher Befehl: $! ist die PID von ssh selbst
HALTEN_PID=$!
for _ in $(seq 1 15); do
  if grep -q wamo-ablaufprobe-ok "$ARBEIT/halten.out" 2>/dev/null; then break; fi
  sleep 1
done
sitzung_offen=0
if grep -q wamo-ablaufprobe-ok "$ARBEIT/halten.out" 2>/dev/null; then sitzung_offen=1; fi

echo "  warte auf den Ablauf ($ablauf_text) plus $NACH_ABLAUF_S Sekunden ..."
ziel=$(( ablauf_epoch + NACH_ABLAUF_S ))
while [ "$(jetzt)" -lt "$ziel" ]; do
  rest=$(( ziel - $(jetzt) ))
  [ "$rest" -le 15 ] || rest=15
  sleep "$rest"
done

marke="$(log_zeilen)"
versuch probe
if abgelehnt; then
  ergebnis B "nach dem Ablauf" "Anmeldung scheitert" "Permission denied, die Zeile gilt nicht mehr" ok
else
  ergebnis B "nach dem Ablauf" "Anmeldung scheitert" "Anmeldung nicht abgelehnt (Exit $VERSUCH_RC): sshd setzt den Ablauf NICHT durch" FEHLER
fi
journal B "$(log_seit "$marke")"
sitzung_lebt=0
if [ "$sitzung_offen" = 1 ] && kill -0 "$HALTEN_PID" 2>/dev/null; then sitzung_lebt=1; fi

zeile_setzen 'restrict,from="127.0.0.1"'   # B2: dieselbe Zeile ohne expiry-time
versuch probe
if angenommen; then
  ergebnis B2 "Kontrolle: dieselbe Zeile ohne expiry-time" "Anmeldung gelingt" "Anmeldung gelang" ok
else
  ergebnis B2 "Kontrolle: dieselbe Zeile ohne expiry-time" "Anmeldung gelingt" "Anmeldung scheiterte (Exit $VERSUCH_RC): B beweist dann nichts" FEHLER
fi

# C: die gehaltene Sitzung muss den Ablauf überlebt haben und regulär enden
for _ in $(seq 1 40); do
  kill -0 "$HALTEN_PID" 2>/dev/null || break
  sleep 5
done
halten_rc=0
wait "$HALTEN_PID" 2>/dev/null || halten_rc=$?
HALTEN_PID=""
if [ "$sitzung_lebt" = 1 ] && grep -q noch-da "$ARBEIT/halten.out" 2>/dev/null; then
  ergebnis C "vor dem Ablauf geöffnete Sitzung" "bleibt bestehen" "lebte nach dem Ablauf und endete regulär" ok
elif [ "$sitzung_offen" = 0 ]; then
  ergebnis C "vor dem Ablauf geöffnete Sitzung" "bleibt bestehen" "die Sitzung kam vor dem Ablauf nicht zustande" FEHLER
else
  ergebnis C "vor dem Ablauf geöffnete Sitzung" "bleibt bestehen" "die Sitzung endete mit dem Ablauf (Exit $halten_rc)" FEHLER
fi

echo "== 4. Proben D1 bis D3: reines Datum"
t1="$(jetzt)"
gestern="$(date -d "@$((t1 - 86400))" +%Y%m%d)"
heute="$(date -d "@$t1" +%Y%m%d)"
morgen="$(date -d "@$((t1 + 86400))" +%Y%m%d)"
zeile_setzen "$(optionen "expiry-time=\"$gestern\"")"
versuch probe
if abgelehnt; then ergebnis D1 "reines Datum von gestern ($gestern)" "abgelehnt" "abgelehnt" ok
else ergebnis D1 "reines Datum von gestern ($gestern)" "abgelehnt" "nicht abgelehnt (Exit $VERSUCH_RC)" FEHLER; fi
zeile_setzen "$(optionen "expiry-time=\"$morgen\"")"
versuch probe
if angenommen; then ergebnis D2 "reines Datum von morgen ($morgen)" "angenommen" "angenommen" ok
else ergebnis D2 "reines Datum von morgen ($morgen)" "angenommen" "nicht angenommen (Exit $VERSUCH_RC)" FEHLER; fi
zeile_setzen "$(optionen "expiry-time=\"$heute\"")"
versuch probe
if abgelehnt; then
  ergebnis D3 "reines Datum von heute ($heute)" "notiert" "abgelehnt: das Datum gilt ab 00:00 (wie Annahme 7), expiry-time am Tag nach ABLAUF schließt genau nach ABLAUF" notiert
elif angenommen; then
  ergebnis D3 "reines Datum von heute ($heute)" "notiert" "angenommen: das Datum gilt bis zum Ende des Tages, das WEICHT von Annahme 7 ab (sshd schlösse einen Tag später als der Dispatcher)" notiert
else
  ergebnis D3 "reines Datum von heute ($heute)" "notiert" "weder angenommen noch abgelehnt (Exit $VERSUCH_RC)" notiert
fi

echo "== 5. Probe E: Tippfehler im Optionsnamen"
zeile_setzen "$(optionen "expiry-tme=\"$morgen\"")"
marke="$(log_zeilen)"
versuch probe
if abgelehnt; then ergebnis E "Tippfehler expiry-tme (Datum von morgen)" "Zeile ungültig, Anmeldung scheitert" "abgelehnt" ok
else ergebnis E "Tippfehler expiry-tme (Datum von morgen)" "Zeile ungültig, Anmeldung scheitert" "nicht abgelehnt (Exit $VERSUCH_RC): ein Tippfehler bliebe unbemerkt" FEHLER; fi
journal E "$(log_seit "$marke")"

echo "== 6. Probe F: Zeitzone der Grenze"
zone="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
[ -n "$zone" ] || zone="unbekannt (timedatectl liefert nichts), date: $(date +%Z)"
ergebnis F "Zeitzone laut timedatectl" "notiert" "$zone, UTC$(date +%z): ein reines Datum gilt ab 00:00 in dieser Zone" notiert

ende
