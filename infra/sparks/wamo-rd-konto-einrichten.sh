#!/usr/bin/env bash
# Konto wamo-rd für den Remote Desktop auf einem Spark (Stufe 2 des Konzepts docs/remote-desktop.md, Abschnitt 5). Das Konto ist
# das einzige, mit dem das Gateway am Spark anmeldet. Es hat kein sudo, keine Gruppe docker, kein SSH und ein zufälliges
# Passwort, das NUR in /etc/wamo-rd/ziele.env steht (0640 root:wamo-rd-gateway) und nie auf dem Bildschirm, in einem Argument,
# im Protokoll oder im Audit erscheint.
#
# Zuerst auf Sokrates-2 (dort liegen das Gateway und die Datei ziele.env, angelegt durch wamo-rd-einrichten.sh konto):
#   sudo bash wamo-rd-konto-einrichten.sh --trocken          # zeigt nur an
#   sudo bash wamo-rd-konto-einrichten.sh                    # Konto und Passwort anlegen
#   sudo bash wamo-rd-konto-einrichten.sh --rotieren         # neues Passwort (Anforderung, alle 90 Tage, nach jedem Verdacht)
#   sudo bash wamo-rd-konto-einrichten.sh --status           # nur prüfen und anzeigen
#   sudo bash wamo-rd-konto-einrichten.sh --sperren          # Konto sperren, Sitzungen beenden (Passwort bleibt, Konto ohne Login)
#   sudo bash wamo-rd-konto-einrichten.sh --entfernen        # Rückweg: Konto, Gruppe und Eintrag in ziele.env entfernen
# Für Sokrates-1 und Caesar (Stufe 5 und 6) gilt dieselbe Technik; die Übergabe des Passworts an das Gateway auf einem anderen
# Rechner ist noch nicht gebaut. Darum läuft dieses Skript vorerst nur dort, wo /etc/wamo-rd/ziele.env liegt.
#
#   --ziel NAME   Name des Ziels (a-z, 0-9, bis 16 Zeichen), Teil des Schlüssels WAMO_RD_PASSWORT_NAME in ziele.env; Vorgabe sokrates2
#
# Was angelegt wird: Gruppe wamo-rd-ts (nur für die Anmeldeprüfung von xrdp, Mitglied allein wamo-rd), Benutzer wamo-rd mit eigener
# Gruppe, Home /home/wamo-rd (0700), Shell bash (die Desktopsitzung braucht eine). Erlaubte Gruppen: wamo-rd, wamo-rd-ts und, falls
# nötig, render und video. Jede weitere Gruppe (sudo, docker, adm, lpadmin ...) bricht das Skript ab. SSH: wamo-rd darf nicht in
# AllowUsers stehen (die Spark-Konfiguration erlaubt SSH nur für aufgeführte Konten); das Skript prüft es mit sshd -T und ändert
# an sshd nichts.
#
# Einstellung für Proben und Tests (nicht für den Betrieb): WAMO_RD_KONTO_WURZEL stellt den Dateipfaden einen Wegwerf-Ordner voran
# und hebt die Pflicht zu root auf.
set -euo pipefail

WURZEL="${WAMO_RD_KONTO_WURZEL:-}"
KONTO=wamo-rd
GRUPPE_TS=wamo-rd-ts
GRUPPE_GATEWAY=wamo-rd-gateway
HOME_KONTO=/home/wamo-rd
ETC_RD="$WURZEL/etc/wamo-rd"
ENV_DATEI="$ETC_RD/ziele.env"
ZIEL=sokrates2
ERLAUBTE_GRUPPEN="wamo-rd wamo-rd-ts render video"

if [ -n "$WURZEL" ]; then
  [[ "$WURZEL" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo "Ungültiger WAMO_RD_KONTO_WURZEL: $WURZEL" >&2; exit 2; }
fi

TROCKEN=nein
AKTION=anlegen
while [ $# -gt 0 ]; do
  case "$1" in
    --trocken) TROCKEN=ja ;;
    --rotieren) AKTION=rotieren ;;
    --status) AKTION=status ;;
    --sperren) AKTION=sperren ;;
    --entfernen) AKTION=entfernen ;;
    --ziel)
      shift
      [ $# -gt 0 ] && [[ "$1" =~ ^[a-z0-9]{1,16}$ ]] || { echo "Ungültiger Wert für --ziel (a-z, 0-9, bis 16 Zeichen)." >&2; exit 2; }
      ZIEL="$1"
      ;;
    *) echo "Aufruf: $0 [--trocken] [--ziel NAME] [--rotieren|--status|--sperren|--entfernen]" >&2; exit 2 ;;
  esac
  shift
done
VARIABLE="WAMO_RD_PASSWORT_$(tr '[:lower:]' '[:upper:]' <<<"$ZIEL")"

fehler() { echo "$*" >&2; exit 1; }
hat() { command -v "$1" >/dev/null 2>&1; }
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}

if [ -z "$WURZEL" ] && [ "$TROCKEN" = nein ] && [ "$AKTION" != status ] && [ "$(id -u)" != 0 ]; then
  fehler "Bitte mit sudo ausführen (oder mit --trocken bzw. --status ansehen). Das sudo-Passwort tippt der Nutzer selbst am Terminal."
fi

konto_da() { id "$KONTO" >/dev/null 2>&1; }

# ---------- Prüfungen (nur lesend)
gruppen_pruefen() {
  local g gruppen
  gruppen="$(id -nG "$KONTO" 2>/dev/null)" || return 0
  for g in $gruppen; do
    case " $ERLAUBTE_GRUPPEN " in
      *" $g "*) ;;
      *) fehler "$KONTO ist in der Gruppe '$g'. Erlaubt sind nur: $ERLAUBTE_GRUPPEN. Abbruch (nichts weiter geändert)." ;;
    esac
  done
  case " $gruppen " in *" $GRUPPE_TS "*) ;; *) fehler "$KONTO fehlt in der Gruppe $GRUPPE_TS." ;; esac
  echo "  Gruppen: $gruppen"
}

sudo_pruefen() {
  local ausgabe
  hat sudo || { echo "  sudo nicht installiert"; return 0; }
  ausgabe="$(LC_ALL=C sudo -l -U "$KONTO" 2>&1 || true)"
  if grep -qiE 'not allowed|nicht (berechtigt|erlaubt)' <<<"$ausgabe" || ! grep -qE '\(.*\)' <<<"$ausgabe"; then
    echo "  sudo: nicht erlaubt"
  else
    fehler "$KONTO darf sudo ausführen. Abbruch."
  fi
}

ssh_pruefen() {
  local ausgabe erlaubt
  hat sshd || { echo "  SSH: kein sshd vorhanden"; return 0; }
  ausgabe="$(sshd -T -C "user=$KONTO,host=localhost,addr=192.168.178.136" 2>/dev/null || true)"
  [ -n "$ausgabe" ] || { echo "  SSH: Konfiguration ohne root nicht lesbar, bitte von Hand prüfen (sshd -T)"; return 0; }
  erlaubt="$(grep -iE '^allowusers ' <<<"$ausgabe" || true)"
  # Muster mit * oder ? in AllowUsers und eine AllowGroups-Liste lassen sich hier nicht sicher auswerten: dann nicht als gesperrt zählen
  if grep -qE '[*?]' <<<"$erlaubt" || grep -qiE '^allowgroups ' <<<"$ausgabe"; then
    fehler "SSH: AllowUsers mit Muster oder AllowGroups lässt sich nicht sicher auswerten. Bitte DenyUsers $KONTO in sshd setzen."
  fi
  if grep -iqE "^denyusers (.* )?$KONTO( |\$)" <<<"$ausgabe"; then
    echo "  SSH: gesperrt (DenyUsers)"
  elif [ -n "$erlaubt" ] && ! grep -qE "[[:space:]]$KONTO([[:space:]]|\$)" <<<"$erlaubt"; then
    echo "  SSH: gesperrt (AllowUsers nennt $KONTO nicht)"
  else
    fehler "SSH ist für $KONTO nicht gesperrt: AllowUsers fehlt oder nennt das Konto. Bitte sshd so einstellen, dass $KONTO nicht anmelden kann (z. B. DenyUsers $KONTO), und das Skript erneut ausführen."
  fi
}

gateway_pruefen() {
  getent group "$GRUPPE_GATEWAY" >/dev/null 2>&1 || fehler "Gruppe $GRUPPE_GATEWAY fehlt: zuerst auf Sokrates-2 'bash wamo-rd-einrichten.sh konto' ausführen."
  [ -d "$ETC_RD" ] || fehler "$ETC_RD fehlt: zuerst auf Sokrates-2 'bash wamo-rd-einrichten.sh konto' ausführen."
}

# ---------- Passwort
passwort_erzeugen() {
  local pw
  # tr endet mit SIGPIPE, sobald head genug hat; deshalb "|| true" im Block
  pw="$({ LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom || true; } | head -c 32)"
  [ "${#pw}" = 32 ] || fehler "Zufallsquelle liefert kein Passwort der Länge 32."
  printf '%s' "$pw"
}

passwort_setzen() {
  # Der Wert steht nur in dieser Variablen (eingebautes printf, nie in einem Argument), geht per stdin an chpasswd und in die Datei.
  if [ "$TROCKEN" = ja ]; then
    printf '  [trocken] neues Passwort (32 Zeichen, zufällig) für %s setzen und als %s nach %s schreiben (0640 root:%s); wird nie angezeigt\n' \
      "$KONTO" "$VARIABLE" "$ENV_DATEI" "$GRUPPE_GATEWAY"
    return 0
  fi
  local pw neu
  pw="$(passwort_erzeugen)"
  neu="$(umask 0137 && mktemp "$ETC_RD/.ziele.env.XXXXXX")"
  { [ -f "$ENV_DATEI" ] && grep -v "^$VARIABLE=" "$ENV_DATEI" || true; } > "$neu"
  printf '%s=%s\n' "$VARIABLE" "$pw" >> "$neu"
  chmod 0640 "$neu"
  if [ -z "$WURZEL" ]; then chown "root:$GRUPPE_GATEWAY" "$neu"; fi
  if ! printf '%s:%s\n' "$KONTO" "$pw" | chpasswd; then
    rm -f "$neu"
    fehler "chpasswd ist fehlgeschlagen, nichts geändert."
  fi
  if ! mv -f "$neu" "$ENV_DATEI"; then
    rm -f "$neu"
    fehler "Passwort am Konto geändert, aber $ENV_DATEI nicht geschrieben. Bitte sofort erneut: $0 --rotieren"
  fi
  pw=""
  echo "  Passwort gesetzt und in $ENV_DATEI als $VARIABLE gespeichert (nicht angezeigt)"
}

env_hat_passwort() { [ -r "$ENV_DATEI" ] && grep -q "^$VARIABLE=." "$ENV_DATEI"; }

# ---------- Aktionen
anlegen() {
  echo "== Konto $KONTO"
  gateway_pruefen
  local neu_konto=nein
  getent group "$GRUPPE_TS" >/dev/null 2>&1 || tu groupadd --system "$GRUPPE_TS"
  if konto_da; then
    echo "  Konto vorhanden: $(id "$KONTO")"
  else
    neu_konto=ja
    tu useradd --create-home --home-dir "$HOME_KONTO" --shell /bin/bash --user-group --groups "$GRUPPE_TS" \
      --comment "WAMO Remote Desktop" -K UMASK=077 "$KONTO"
  fi
  tu chmod 0700 "$WURZEL$HOME_KONTO"
  if [ "$TROCKEN" = nein ]; then gruppen_pruefen; sudo_pruefen; ssh_pruefen; fi
  if [ "$neu_konto" = ja ] || ! env_hat_passwort; then
    passwort_setzen
  else
    echo "  Passwort unverändert (Rotation mit --rotieren)"
  fi
  echo "  Fertig. Eintrag $VARIABLE steht in $ENV_DATEI (0640 root:$GRUPPE_GATEWAY); das Passwort wird nirgends angezeigt."
}

rotieren() {
  echo "== Passwort von $KONTO rotieren"
  gateway_pruefen
  konto_da || fehler "Konto $KONTO fehlt: zuerst ohne Option ausführen."
  if [ "$TROCKEN" = nein ]; then gruppen_pruefen; sudo_pruefen; ssh_pruefen; fi
  passwort_setzen
  if [ "$TROCKEN" = nein ]; then echo "  Das Gateway liest die Datei beim nächsten Verbindungsaufbau; laufende Sitzungen bleiben bestehen."; fi
}

status() {
  echo "== Status von $KONTO"
  if konto_da; then
    echo "  Konto: vorhanden ($(id "$KONTO"))"
    gruppen_pruefen
    sudo_pruefen
    ssh_pruefen
  else
    echo "  Konto: fehlt"
  fi
  if env_hat_passwort; then echo "  Passwort: $VARIABLE gesetzt in $ENV_DATEI (Wert wird nicht angezeigt)"; else echo "  Passwort: nicht gesetzt oder Datei nicht lesbar"; fi
}

sperren() {
  echo "== Konto $KONTO sperren"
  konto_da || fehler "Konto $KONTO fehlt."
  tu usermod --lock --expiredate 1 "$KONTO"
  tu pkill -u "$KONTO" || true
  echo "  gesperrt (Rückgängig: usermod --unlock --expiredate '' $KONTO, danach --rotieren)"
}

entfernen() {
  echo "== Konto $KONTO entfernen"
  if konto_da; then
    tu usermod --lock --expiredate 1 "$KONTO"
    tu pkill -u "$KONTO" || true
    tu userdel --remove "$KONTO"
  else
    echo "  Konto war nicht vorhanden"
  fi
  if getent group "$GRUPPE_TS" >/dev/null 2>&1; then tu groupdel "$GRUPPE_TS"; fi
  if [ -f "$ENV_DATEI" ] && grep -q "^$VARIABLE=" "$ENV_DATEI"; then
    if [ "$TROCKEN" = ja ]; then
      echo "  [trocken] Zeile $VARIABLE aus $ENV_DATEI entfernen"
    else
      local neu
      neu="$(umask 0137 && mktemp "$ETC_RD/.ziele.env.XXXXXX")"
      grep -v "^$VARIABLE=" "$ENV_DATEI" > "$neu" || true
      chmod 0640 "$neu"
      if [ -z "$WURZEL" ]; then chown "root:$GRUPPE_GATEWAY" "$neu"; fi
      mv -f "$neu" "$ENV_DATEI"
    fi
  fi
  echo "  entfernt"
}

case "$AKTION" in
  anlegen) anlegen ;;
  rotieren) rotieren ;;
  status) status ;;
  sperren) sperren ;;
  entfernen) entfernen ;;
esac
