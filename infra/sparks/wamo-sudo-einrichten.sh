#!/usr/bin/env bash
# sudo ohne Passwort für genau die Einrichtungsschritte des Remote Desktop auf einem Spark, auf dem das Dienstkonto sonst ein
# sudo-Passwort braucht (Caesar). Einmal vom Nutzer selbst mit sudo auszuführen, das Passwort tippt er am Terminal.
#
# Was entsteht (nichts davon ist für das Dienstkonto beschreibbar):
#   /usr/local/lib/wamo-rd/                 root:root 0755, darin vier Skripte (root:root 0755): wamo-rd-konto-einrichten.sh,
#                                           wamo-rd-ziel-einrichten.sh, wamo-rd-ausgang-einrichten.sh, xrdp-einhegen.sh
#   /etc/sudoers.d/wamo-rd-ziel             root:root 0440, vorher mit visudo -cf geprüft
# Die Freigabe nennt nur feste, vollständige Befehlszeilen dieser Skripte (kein Platzhalter, kein bash, kein Skript aus einem
# Home-Ordner). Sie ändert sich nur, wenn dieses Skript mit anderen Werten erneut läuft. Würde sudo ein Skript im Home des
# Dienstkontos freigeben, wäre das ein Weg zu root für jeden, der das Konto benutzt; darum liegen die Skripte in /usr/local/lib.
#
# Ablauf (vom PC aus, im Repo-Hauptordner; ssh-Zugang mit eigenem Konto, das sudo darf):
#   scp -r infra/sparks CAESAR:wamo-sparks-neu
#   ssh -t CAESAR "sudo bash ~/wamo-sparks-neu/wamo-sudo-einrichten.sh --trocken"      # zeigt nur an
#   ssh -t CAESAR "sudo bash ~/wamo-sparks-neu/wamo-sudo-einrichten.sh"                # einrichten
#   ssh -t CAESAR "sudo bash ~/wamo-sparks-neu/wamo-sudo-einrichten.sh --zurueck"      # Rückweg: Freigabe und Skripte entfernen
#
#   --benutzer NAME   Konto, das die Befehle ohne Passwort ausführen darf (Vorgabe wamocon)
#   --quelle NETZ     Absender für Port 3389 und Rückantworten, IPv4 a.b.c.d/nn mit nn von 16 bis 32 (Vorgabe 192.168.178.136/32,
#                     das Gateway auf Sokrates-2)
#   --ziel NAME       Name des Ziels im Gateway (a-z, 0-9, bis 16 Zeichen, Vorgabe caesar)
#
# Danach (als wamocon, ohne Passwort), Beispiel für Caesar, vom PC aus:
#   ssh wamo-caesar "sudo -n /usr/local/lib/wamo-rd/wamo-rd-ziel-einrichten.sh --quelle 192.168.178.136/32 --neustart"
#   ssh wamo-caesar "sudo -n /usr/local/lib/wamo-rd/wamo-rd-konto-einrichten.sh --fern --ziel caesar" \
#     | ssh wamo-sokrates2 "sudo -n bash ~/wamo-sparks/wamo-rd-konto-einrichten.sh --uebernehmen --ziel caesar"
#   ssh wamo-caesar "sudo -n /usr/local/lib/wamo-rd/wamo-rd-ausgang-einrichten.sh --gateway 192.168.178.136/32"
#
# Einstellung für Proben und Tests (nicht für den Betrieb): WAMO_SUDO_WURZEL stellt allen Dateipfaden einen Wegwerf-Ordner voran
# und hebt die Pflicht zu root auf (visudo wird dann nicht aufgerufen).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WURZEL="${WAMO_SUDO_WURZEL:-}"
BIB="$WURZEL/usr/local/lib/wamo-rd"
SUDOERS="$WURZEL/etc/sudoers.d/wamo-rd-ziel"
SKRIPTE=(wamo-rd-konto-einrichten.sh wamo-rd-ziel-einrichten.sh wamo-rd-ausgang-einrichten.sh xrdp-einhegen.sh)
BENUTZER=wamocon
QUELLE=192.168.178.136/32
ZIEL=caesar
TROCKEN=nein
ZURUECK=nein

fehler() { echo "$*" >&2; exit 1; }

if [ -n "$WURZEL" ]; then
  [[ "$WURZEL" =~ ^/[A-Za-z0-9_./-]+$ ]] || fehler "Ungültiger WAMO_SUDO_WURZEL: $WURZEL"
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --trocken) TROCKEN=ja ;;
    --zurueck) ZURUECK=ja ;;
    --benutzer)
      shift
      [ $# -gt 0 ] && [[ "$1" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || fehler "Ungültiger Wert für --benutzer."
      BENUTZER="$1"
      ;;
    --quelle)
      shift
      [ $# -gt 0 ] && [[ "$1" =~ ^(10\.([0-9]{1,3}\.){2}[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3})/(1[6-9]|2[0-9]|3[0-2])$ ]] \
        || fehler "Ungültiger Wert für --quelle (privates Netz a.b.c.d/nn mit nn von 16 bis 32)."
      QUELLE="$1"
      ;;
    --ziel)
      shift
      [ $# -gt 0 ] && [[ "$1" =~ ^[a-z0-9]{1,16}$ ]] || fehler "Ungültiger Wert für --ziel (a-z, 0-9, bis 16 Zeichen)."
      ZIEL="$1"
      ;;
    *) fehler "Aufruf: $0 [--trocken] [--zurueck] [--benutzer NAME] [--quelle NETZ] [--ziel NAME]" ;;
  esac
  shift
done

if [ -z "$WURZEL" ] && [ "$TROCKEN" = nein ] && [ "$(id -u)" != 0 ]; then
  fehler "Bitte mit sudo ausführen (oder mit --trocken ansehen). Das sudo-Passwort tippt der Nutzer selbst am Terminal."
fi

tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}

# Der Inhalt der Freigabe: feste, vollständige Befehlszeilen, kein Platzhalter
sudoers_text() {
  local b=/usr/local/lib/wamo-rd
  cat <<EOF
# WAMO Remote Desktop (infra/sparks/wamo-sudo-einrichten.sh): $BENUTZER darf genau diese Befehle ohne Passwort ausführen.
# Die Skripte gehören root und liegen in $b, das Konto kann sie nicht ändern. Entfernen: wamo-sudo-einrichten.sh --zurueck
Cmnd_Alias WAMO_RD_ZIEL = \\
    $b/wamo-rd-konto-einrichten.sh --status, \\
    $b/wamo-rd-konto-einrichten.sh --fern --ziel $ZIEL, \\
    $b/wamo-rd-konto-einrichten.sh --fern --ziel $ZIEL --trocken, \\
    $b/wamo-rd-ziel-einrichten.sh --status, \\
    $b/wamo-rd-ziel-einrichten.sh --quelle $QUELLE --trocken, \\
    $b/wamo-rd-ziel-einrichten.sh --quelle $QUELLE --neustart, \\
    $b/wamo-rd-ausgang-einrichten.sh --status, \\
    $b/wamo-rd-ausgang-einrichten.sh --gateway $QUELLE --trocken, \\
    $b/wamo-rd-ausgang-einrichten.sh --gateway $QUELLE
$BENUTZER ALL=(root) NOPASSWD: WAMO_RD_ZIEL
EOF
}

zurueck() {
  echo "== Rückweg: Freigabe und Skripte entfernen"
  if [ -f "$SUDOERS" ]; then tu rm -f "$SUDOERS"; else echo "  Freigabe war nicht eingerichtet"; fi
  if [ -d "$BIB" ]; then
    for s in "${SKRIPTE[@]}"; do tu rm -f "$BIB/$s"; done
    tu rmdir "$BIB"
  fi
  echo "  Fertig."
}

einrichten() {
  echo "== sudo ohne Passwort für $BENUTZER (nur die Einrichtungsschritte des Remote Desktop, Ziel $ZIEL, Quelle $QUELLE)"
  if [ -z "$WURZEL" ]; then id "$BENUTZER" >/dev/null 2>&1 || fehler "Das Konto $BENUTZER gibt es nicht."; fi
  for s in "${SKRIPTE[@]}"; do
    [ -f "$HERE/$s" ] && [ ! -L "$HERE/$s" ] || fehler "$HERE/$s fehlt oder ist ein Link (infra/sparks vollständig ablegen)."
    bash -n "$HERE/$s" || fehler "$s enthält einen Syntaxfehler. Abbruch, nichts geändert."
  done
  local tmp
  tmp="$(mktemp)"
  sudoers_text > "$tmp"
  if [ -z "$WURZEL" ] && [ "$TROCKEN" = nein ]; then
    command -v visudo >/dev/null 2>&1 || { rm -f "$tmp"; fehler "visudo fehlt: die Freigabe lässt sich nicht prüfen. Abbruch."; }
    visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; fehler "visudo lehnt die Freigabe ab. Abbruch, nichts geändert."; }
    grep -qE '^[#@]includedir /etc/sudoers\.d' /etc/sudoers || { rm -f "$tmp"; fehler "/etc/sudoers bindet /etc/sudoers.d nicht ein. Abbruch."; }
  fi
  tu install -d -m 0755 "$BIB"
  for s in "${SKRIPTE[@]}"; do tu install -m 0755 "$HERE/$s" "$BIB/$s"; done
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] $SUDOERS (0440 root:root) mit diesem Inhalt:"
    sed 's/^/    /' "$tmp"
  else
    tu install -d -m 0755 "$(dirname "$SUDOERS")"
    tu install -m 0440 "$tmp" "$SUDOERS"
    if [ -z "$WURZEL" ]; then chown root:root "$SUDOERS" "$BIB" "$BIB"/*; fi
  fi
  rm -f "$tmp"
  echo "  Fertig. Prüfen als $BENUTZER:  sudo -n -l | grep wamo-rd"
}

if [ "$ZURUECK" = ja ]; then zurueck; else einrichten; fi
