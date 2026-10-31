#!/usr/bin/env bash
# Spark als Ziel des Remote-Desktop-Gateways einrichten, Betriebsart xrdp (Stufe 2 des Konzepts docs/remote-desktop.md,
# Abschnitte 2.1, 2.2 und 2.4). Vorgesehen für Sokrates-2 als erstes Ziel; dieselbe Technik gilt später für Sokrates-1 und Caesar.
# Das Skript setzt Konfiguration, installiert und aktualisiert aber NIE ein Paket. Es berührt weder die Sitzung von wamocon
# noch die gnome-remote-desktop-Einrichtung: xrdp startet für wamo-rd eine eigene Xorg-Sitzung (Display ab :10), nie die Konsole (seat0).
#
# Entscheidung xrdp statt gnome-remote-desktop (GRD): Das Konzept empfiehlt xrdp als Zielzustand, weil nur er auf dieser Hardware
# (NVIDIA GB10, Xorg ohne Monitor) nachweislich läuft (Caesar). GRD im Systemmodus ist dort ungeprüft und bräuchte zwei Anmeldungen
# (Prüfplan in infra/sokrates2/README.md, Abschnitt "Remote Desktop (Gateway)"). Läuft GRD später ohne zweite Anmeldung, ist es wegen
# des unprivilegierten Dienstes vorzuziehen; das Gateway ändert sich dadurch nicht (nur security-Wert und Zertifikatsprüfung).
#
# Reihenfolge am Ziel (der Nutzer führt sie selbst mit sudo aus):
#   1. sudo bash wamo-rd-konto-einrichten.sh                        # Konto wamo-rd, Gruppe wamo-rd-ts, Passwort in ziele.env
#   2. Port 3389 beschränken, BEVOR xrdp installiert ist (sonst lauscht er nach der Installation kurz offen im LAN). Auf
#      Sokrates-2 selbst ist die Quelle das Netz des Gateways:
#      sudo env WAMO_XRDP_QUELLE=172.27.0.0/24 bash xrdp-einhegen.sh --nur-sperre --trocken     # erst ansehen, dann ohne --trocken
#   3. sudo apt-get install xrdp xorgxrdp                           # bewusst von Hand (apt changelog xrdp: CVE-2025-68670 prüfen)
#   4. sudo bash wamo-rd-ziel-einrichten.sh --trocken               # zeigt nur an
#      sudo bash wamo-rd-ziel-einrichten.sh --neustart              # einrichten, xrdp-sesman neu starten (nur ohne Sitzung)
#      sudo bash wamo-rd-ziel-einrichten.sh --status                # nur anzeigen
#      sudo bash wamo-rd-ziel-einrichten.sh --zurueck               # Rückweg
# Dieses Skript ruft Schritt 2 selbst auf (xrdp-einhegen.sh --nur-sperre), falls er noch fehlt; Schritt 2 vorab bleibt trotzdem
# der sichere Weg, weil er VOR der Installation von xrdp liegt.
#
#   --quelle NETZ   Absender, die Port 3389 erreichen dürfen (IPv4 oder a.b.c.d/nn). Vorgabe 172.27.0.0/24 (das Netz wamo-rd-ziel
#                   des Gateways auf Sokrates-2). Auf Sokrates-1 und Caesar: 192.168.178.136/32.
#
# Was geändert wird (alles mit Sicherung als .vor-wamo-rd-ZEITSTEMPEL neben der Datei, nur wenn sich etwas ändert):
#   /etc/xrdp/sesman.ini  [Security] AllowRootLogin=false, AlwaysGroupCheck=true, TerminalServerUsers=wamo-rd-ts
#                         [Sessions] KillDisconnected=true, DisconnectedTimeLimit=900, IdleTimeLimit=600
#   /etc/xrdp/xrdp.ini    [Globals] security_layer=tls, crypt_level=high, ssl_protocols=TLSv1.2, TLSv1.3
#                         [Channels] rdpdr=false, rdpsnd=false, cliprdr=false (keine Laufwerke, kein Ton, keine Zwischenablage)
#   /home/wamo-rd/.xsessionrc  GNOME-Sitzung unter Xorg für das Konto (nur diese Datei im Home)
#   Port 3389: nur --quelle und Loopback (xrdp-einhegen.sh --nur-sperre: ufw oder eigene nftables-Tabelle wamo_xrdp)
# Nicht geändert: Pakete, Dienste ein/aus (xrdp bleibt, wie er ist), die Sitzung von wamocon, GRD, sshd.
#
# Einstellung für Proben und Tests (nicht für den Betrieb): WAMO_RD_ZIEL_WURZEL stellt den Dateipfaden einen Wegwerf-Ordner voran
# und hebt die Pflicht zu root auf (es wird dann auch xrdp-einhegen.sh mit derselben Wurzel aufgerufen).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_RD_ZIEL_WURZEL:-}"
KONTO=wamo-rd
GRUPPE_TS=wamo-rd-ts
QUELLE=172.27.0.0/24
SESMAN="$WURZEL/etc/xrdp/sesman.ini"
XRDP_INI="$WURZEL/etc/xrdp/xrdp.ini"
XSESSIONRC="$WURZEL/home/wamo-rd/.xsessionrc"
MARKE="# WAMO Remote Desktop (wamo-rd-ziel-einrichten.sh)"

if [ -n "$WURZEL" ]; then
  [[ "$WURZEL" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo "Ungültiger WAMO_RD_ZIEL_WURZEL: $WURZEL" >&2; exit 2; }
fi

TROCKEN=nein
ZURUECK=nein
NEUSTART=nein
NUR_STATUS=nein
while [ $# -gt 0 ]; do
  case "$1" in
    --trocken) TROCKEN=ja ;;
    --zurueck) ZURUECK=ja ;;
    --neustart) NEUSTART=ja ;;
    --status) NUR_STATUS=ja ;;
    --quelle)
      shift
      [ $# -gt 0 ] && [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$ ]] || { echo "Ungültiger Wert für --quelle." >&2; exit 2; }
      QUELLE="$1"
      ;;
    *) echo "Aufruf: $0 [--trocken] [--neustart] [--status] [--zurueck] [--quelle NETZ]" >&2; exit 2 ;;
  esac
  shift
done

fehler() { echo "$*" >&2; exit 1; }
hat() { command -v "$1" >/dev/null 2>&1; }
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}

if [ -z "$WURZEL" ] && [ "$TROCKEN" = nein ] && [ "$NUR_STATUS" = nein ] && [ "$(id -u)" != 0 ]; then
  fehler "Bitte mit sudo ausführen (oder mit --trocken bzw. --status ansehen)."
fi

ZEIT="$(date +%Y%m%d-%H%M%S)"

# ---------- ini-Hilfen (reiner Text, keine Befehle mit Wirkung)
# Wert eines Schlüssels (letzter Treffer, Kommentare mit ; oder # ignoriert) in einem Abschnitt
ini_wert() {
  local datei="$1" abschnitt="$2" schluessel="$3"
  [ -r "$datei" ] || return 0
  awk -v s="$schluessel" -v ab="$abschnitt" '
    /^[[:space:]]*\[/ { aktuell = $0; gsub(/[][[:space:]]/, "", aktuell); next }
    /^[[:space:]]*[;#]/ { next }
    {
      if (aktuell != ab) next
      eq = index($0, "=")
      if (eq == 0) next
      name = substr($0, 1, eq - 1); gsub(/[[:space:]]/, "", name)
      if (name == s) { wert = substr($0, eq + 1); sub(/[;#].*$/, "", wert); gsub(/^[[:space:]]+|[[:space:]]+$/, "", wert); ergebnis = wert }
    }
    END { print ergebnis }
  ' "$datei"
}

# Gibt den Inhalt der Datei mit ABSCHNITT/SCHLÜSSEL=WERT aus: ersetzt die erste Zeile des Schlüssels im Abschnitt, entfernt weitere,
# fügt sonst am Ende des Abschnitts ein oder legt den Abschnitt am Dateiende an.
ini_setzen() {
  local datei="$1" abschnitt="$2" schluessel="$3" wert="$4"
  awk -v ab="$abschnitt" -v s="$schluessel" -v w="$wert" '
    function kopf(z,   t) { t = z; gsub(/[][[:space:]]/, "", t); return t }
    /^[[:space:]]*\[/ {
      if (insec && !done) { print s "=" w; done = 1 }
      insec = (kopf($0) == ab); gesehen = gesehen || insec
    }
    insec && !/^[[:space:]]*[;#]/ {
      eq = index($0, "=")
      if (eq > 0) { n = substr($0, 1, eq - 1); gsub(/[[:space:]]/, "", n); if (n == s) { if (!done) { print s "=" w; done = 1 } next } }
    }
    { print }
    END {
      if (insec && !done) print s "=" w
      else if (!done) { print ""; print "[" ab "]"; print s "=" w }
    }
  ' "$datei"
}

# Wendet eine Liste "ABSCHNITT SCHLÜSSEL WERT" auf eine Datei an. Gibt den Namen der Arbeitskopie aus.
anwenden() {
  local datei="$1"
  shift
  local arbeit abschnitt schluessel wert tmp
  arbeit="$(mktemp "$datei.neu.XXXXXX")"
  cp "$datei" "$arbeit"
  while [ $# -gt 0 ]; do
    abschnitt="$1"; schluessel="$2"; wert="$3"
    shift 3
    tmp="$(mktemp "$datei.neu.XXXXXX")"
    ini_setzen "$arbeit" "$abschnitt" "$schluessel" "$wert" > "$tmp"
    mv -f "$tmp" "$arbeit"
  done
  printf '%s' "$arbeit"
}

datei_einrichten() {
  # datei_einrichten DATEI ABSCHNITT SCHLÜSSEL WERT ...
  local datei="$1" arbeit
  shift
  [ -r "$datei" ] || fehler "$datei nicht lesbar: ist xrdp installiert (sudo apt-get install xrdp xorgxrdp)?"
  arbeit="$(anwenden "$datei" "$@")"
  if cmp -s "$datei" "$arbeit"; then
    echo "  $datei: schon eingerichtet"
    rm -f "$arbeit"
    return 0
  fi
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] $datei ändern (Unterschied):"
    diff "$datei" "$arbeit" | sed 's/^/    /' || true
    rm -f "$arbeit"
    return 0
  fi
  if [ ! -s "$arbeit" ]; then rm -f "$arbeit"; fehler "Neue $datei ist leer, nichts geändert."; fi
  cp -p "$datei" "$datei.vor-wamo-rd-$ZEIT"
  chmod --reference="$datei" "$arbeit"
  chown --reference="$datei" "$arbeit" 2>/dev/null || true
  mv -f "$arbeit" "$datei"
  echo "  $datei geändert (Sicherung $datei.vor-wamo-rd-$ZEIT)"
}

# ---------- Vorprüfung
vorpruefen() {
  echo "== Vorprüfung"
  getent group "$GRUPPE_TS" >/dev/null 2>&1 || fehler "Gruppe $GRUPPE_TS fehlt: zuerst wamo-rd-konto-einrichten.sh ausführen."
  id "$KONTO" >/dev/null 2>&1 || fehler "Konto $KONTO fehlt: zuerst wamo-rd-konto-einrichten.sh ausführen."
  [ "$(getent group "$GRUPPE_TS" | cut -d: -f4)" = "$KONTO" ] || fehler "Gruppe $GRUPPE_TS darf genau ein Mitglied haben ($KONTO)."
  if [ ! -r "$SESMAN" ] || [ ! -r "$XRDP_INI" ]; then
    fehler "xrdp ist nicht installiert (keine $SESMAN). Zuerst Port 3389 beschränken (xrdp-einhegen.sh --nur-sperre), dann: sudo apt-get install xrdp xorgxrdp"
  fi
  echo "  Konto, Gruppe $GRUPPE_TS und xrdp vorhanden"
}

sperre() {
  echo "== Port 3389: nur $QUELLE und Loopback"
  [ -f "$HERE/xrdp-einhegen.sh" ] || fehler "$HERE/xrdp-einhegen.sh fehlt (infra/sparks vollständig ablegen)."
  local zusatz=()
  [ "$TROCKEN" = nein ] || zusatz=(--trocken)
  WAMO_XRDP_QUELLE="$QUELLE" WAMO_XRDP_WURZEL="$WURZEL" bash "$HERE/xrdp-einhegen.sh" --nur-sperre ${zusatz[@]+"${zusatz[@]}"}
}

konfiguration() {
  echo "== xrdp-Konfiguration"
  datei_einrichten "$SESMAN" \
    Security AllowRootLogin false \
    Security AlwaysGroupCheck true \
    Security TerminalServerUsers "$GRUPPE_TS" \
    Sessions KillDisconnected true \
    Sessions DisconnectedTimeLimit 900 \
    Sessions IdleTimeLimit 600
  datei_einrichten "$XRDP_INI" \
    Globals security_layer tls \
    Globals crypt_level high \
    Globals ssl_protocols "TLSv1.2, TLSv1.3" \
    Channels rdpdr false \
    Channels rdpsnd false \
    Channels cliprdr false
}

sitzung() {
  echo "== Sitzung von $KONTO (GNOME unter Xorg)"
  local inhalt
  inhalt="$MARKE
export GNOME_SHELL_SESSION_MODE=ubuntu
export XDG_CURRENT_DESKTOP=ubuntu:GNOME
export XDG_CONFIG_DIRS=/etc/xdg/xdg-ubuntu:/etc/xdg"
  [ ! -L "$XSESSIONRC" ] || fehler "$XSESSIONRC ist ein symbolischer Link, Abbruch."
  if [ -f "$XSESSIONRC" ] && [ "$(cat "$XSESSIONRC")" = "$inhalt" ]; then
    echo "  $XSESSIONRC: schon eingerichtet"
    return 0
  fi
  if [ -f "$XSESSIONRC" ] && ! head -n1 "$XSESSIONRC" | grep -qF "$MARKE"; then
    fehler "$XSESSIONRC existiert und stammt nicht von diesem Skript, Abbruch."
  fi
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] $XSESSIONRC anlegen (Besitzer $KONTO, 0644)"
  else
    # Das Home gehört dem Konto: mktemp legt mit zufälligem Namen und O_EXCL an (kein vorab gelegter Link wird verfolgt),
    # chown -h und mv -T wirken auf die Datei bzw. den Link selbst, nie auf dessen Ziel
    local tmp
    tmp="$(mktemp "$XSESSIONRC.XXXXXX")"
    printf '%s\n' "$inhalt" > "$tmp"
    chmod 0644 "$tmp"
    if [ -z "$WURZEL" ]; then chown -h "$KONTO:$KONTO" "$tmp"; fi
    mv -T -f "$tmp" "$XSESSIONRC"
    echo "  $XSESSIONRC angelegt"
  fi
}

neustart() {
  echo "== xrdp-sesman"
  if [ "$NEUSTART" != ja ]; then
    echo "  Kein Neustart (Option --neustart). Die Einstellungen wirken für neue Sitzungen erst danach:"
    echo "    sudo systemctl restart xrdp-sesman    (nur ohne laufende Sitzung)"
    return 0
  fi
  if hat pgrep && pgrep -x xrdp-chansrv >/dev/null 2>&1; then
    echo "  Es läuft eine xrdp-Sitzung (xrdp-chansrv). Kein Neustart; später wiederholen oder von Hand neu starten."
    return 0
  fi
  tu systemctl restart xrdp-sesman
  tu systemctl restart xrdp
}

status() {
  echo "== Status (nur lesend)"
  echo "  Konto: $(id "$KONTO" 2>/dev/null || echo fehlt)"
  echo "  Gruppe $GRUPPE_TS: $(getent group "$GRUPPE_TS" 2>/dev/null || echo fehlt)"
  if [ -r "$SESMAN" ]; then
    echo "  sesman.ini: AllowRootLogin=$(ini_wert "$SESMAN" Security AllowRootLogin), AlwaysGroupCheck=$(ini_wert "$SESMAN" Security AlwaysGroupCheck)," \
      "TerminalServerUsers=$(ini_wert "$SESMAN" Security TerminalServerUsers), KillDisconnected=$(ini_wert "$SESMAN" Sessions KillDisconnected)," \
      "DisconnectedTimeLimit=$(ini_wert "$SESMAN" Sessions DisconnectedTimeLimit), IdleTimeLimit=$(ini_wert "$SESMAN" Sessions IdleTimeLimit)"
  else
    echo "  sesman.ini: nicht vorhanden oder nicht lesbar (xrdp nicht installiert?)"
  fi
  if [ -r "$XRDP_INI" ]; then
    echo "  xrdp.ini: security_layer=$(ini_wert "$XRDP_INI" Globals security_layer), crypt_level=$(ini_wert "$XRDP_INI" Globals crypt_level)," \
      "rdpdr=$(ini_wert "$XRDP_INI" Channels rdpdr), rdpsnd=$(ini_wert "$XRDP_INI" Channels rdpsnd), cliprdr=$(ini_wert "$XRDP_INI" Channels cliprdr)"
  fi
  if hat ss; then echo "  Lauscht auf Port 3389: $(ss -tlnH 'sport = :3389' 2>/dev/null | awk '{ print $4 }' | paste -sd' ' - || true)"; fi
  [ -f "$XSESSIONRC" ] && echo "  $XSESSIONRC: vorhanden" || echo "  $XSESSIONRC: fehlt"
}

zurueck() {
  echo "== Rückweg"
  local datei alt
  for datei in "$SESMAN" "$XRDP_INI"; do
    # die älteste Sicherung dieses Skripts ist der Stand vor WAMO
    alt="$(find "$(dirname "$datei")" -maxdepth 1 -name "$(basename "$datei").vor-wamo-rd-*" | sort | head -n1)"
    if [ -n "$alt" ]; then
      echo "  $datei aus $alt wiederherstellen"
      if [ "$TROCKEN" = nein ]; then
        cp -p "$alt" "$datei.zurueck"
        mv -f "$datei.zurueck" "$datei"
      else
        echo "  [trocken] cp -p $alt $datei"
      fi
    else
      echo "  $datei: keine Sicherung dieses Skripts, nichts wiederherzustellen"
    fi
  done
  if [ -f "$XSESSIONRC" ] && head -n1 "$XSESSIONRC" | grep -qF "$MARKE"; then
    tu rm -f "$XSESSIONRC"
  fi
  local zusatz=()
  [ "$TROCKEN" = nein ] || zusatz=(--trocken)
  if [ -f "$HERE/xrdp-einhegen.sh" ]; then
    WAMO_XRDP_QUELLE="$QUELLE" WAMO_XRDP_WURZEL="$WURZEL" bash "$HERE/xrdp-einhegen.sh" --zurueck ${zusatz[@]+"${zusatz[@]}"}
  fi
  neustart
  echo "  Pakete und Dienst-Aktivierung bleiben unverändert (xrdp ggf. von Hand: sudo systemctl disable --now xrdp)."
}

if [ "$NUR_STATUS" = ja ]; then
  status
elif [ "$ZURUECK" = ja ]; then
  zurueck
else
  vorpruefen
  sperre
  konfiguration
  sitzung
  neustart
  echo
  status
  [ "$TROCKEN" = nein ] || echo "Trockenlauf: es wurde nichts geändert."
fi
