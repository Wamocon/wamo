#!/usr/bin/env bash
# Ausgangsregel für das Konto wamo-rd (Remote Desktop, docs/remote-desktop.md Abschnitt 5.2, README Abschnitt 16).
#
# Warum: Die Fernsitzung von wamo-rd läuft als Prozess auf dem Spark selbst. Auf Sokrates-2 erreicht sie sonst alle Dienste auf
# Loopback und im LAN (Kern, Supabase, Docker-Proxys, Router, die anderen Sparks). Die Firewall der Sparks lässt Loopback überall
# durch. Diese Regel beschränkt NUR Pakete, die Prozesse des Benutzers wamo-rd selbst senden (meta skuid), auf das Internet:
#   erlaubt   alles außer den unten genannten Bereichen (öffentliche IPv4-Adressen), dazu DNS über den Stub 127.0.0.53,
#             die Rückantworten an das Gateway (nur Pakete bestehender Verbindungen in das Netz des Gateways), optional einzelne
#             Loopback-Ports (--loopback-port), falls eine Sitzung sie braucht
#   gesperrt  Loopback 127.0.0.0/8, LAN und Docker-Netze (10/8, 172.16/12, 192.168/16), Link-Local, CGNAT, Multicast und
#             Reserviertes; IPv6 vollständig (es gibt kein sicheres "nur Internet" ohne die Präfixe des LANs zu kennen)
# Unix-Sockets sind kein Netzverkehr und bleiben unberührt (X11, D-Bus, xorgxrdp, Pulse laufen darüber). Prozesse anderer
# Benutzer (xrdp, Gateway, Kern, wamocon) sehen die Regel nie. Sie gilt in eigener Tabelle inet wamo_rd_ausgang; fremde Tabellen,
# ufw und Docker werden nicht angefasst.
#
# Aufruf (auf dem Spark, der Nutzer führt es selbst aus; das sudo-Passwort tippt er am Terminal, das Skript kennt es nie):
#   sudo bash wamo-rd-ausgang-einrichten.sh --trocken     # zeigt Regeltext und Schritte, ändert nichts
#   sudo bash wamo-rd-ausgang-einrichten.sh               # einrichten (wiederholbar: unverändert heißt keine Änderung)
#   sudo bash wamo-rd-ausgang-einrichten.sh --status      # Stand der Tabelle und der Zähler anzeigen
#   sudo bash wamo-rd-ausgang-einrichten.sh --zurueck     # Rückweg (nur, was dieses Skript gesetzt hat)
#   Weitere Wahl:  --gateway NETZ   Netz des Gateways für die Rückantworten (Vorgabe 172.27.0.0/24, das Netz wamo-rd-ziel)
#                  --loopback-port N   einen Loopback-Port für wamo-rd erlauben (mehrfach, höchstens 8; Vorgabe keiner)
#
# Fail-closed: xrdp und xrdp-sesman starten nur mit geladener Regel (Drop-in mit Requires). Fehlt das Konto wamo-rd oder lehnt nft
# den Text ab, bleibt xrdp aus statt ohne Beschränkung zu laufen. Probe nach dem Einrichten: infra/sokrates2/wamo_rd_rauchtest.py
# --betrieb (Verbotsproben als wamo-rd) und die Zähler mit --status. Zeigt ein Zähler "kein LAN" Treffer, die nicht erwartet sind,
# ist das der Beleg, dass etwas in der Sitzung das LAN erreichen wollte.
#
# Sicherung: Eine vorhandene, abweichende Regeldatei wird vor dem Ersetzen nach /var/backups/wamo-rd-ausgang kopiert. Was gesetzt
# wurde, steht in /var/lib/wamo-rd-ausgang/zustand; --zurueck nutzt es.
#
# Einstellung für Proben und Tests (nicht für den Betrieb): WAMO_RD_AUSGANG_WURZEL stellt allen Dateipfaden einen Wegwerf-Ordner
# voran und hebt die Pflicht zu root auf.
set -euo pipefail

WURZEL="${WAMO_RD_AUSGANG_WURZEL:-}"
KONTO=wamo-rd
TABELLE=wamo_rd_ausgang
GATEWAY=172.27.0.0/24
LOOPBACK_PORTS=()
if [ -n "$WURZEL" ]; then
  [[ "$WURZEL" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo "Ungültiger WAMO_RD_AUSGANG_WURZEL: $WURZEL" >&2; exit 2; }
fi

DIR="$WURZEL/etc/wamo-rd-ausgang"
NFT_DATEI="$DIR/regeln.nft"
UNIT="$WURZEL/etc/systemd/system/wamo-rd-ausgang.service"
DROPINS=("$WURZEL/etc/systemd/system/xrdp-sesman.service.d/wamo-rd-ausgang.conf" "$WURZEL/etc/systemd/system/xrdp.service.d/wamo-rd-ausgang.conf")
SICHERUNG="$WURZEL/var/backups/wamo-rd-ausgang"
ZUSTAND_DIR="$WURZEL/var/lib/wamo-rd-ausgang"
ZUSTAND="$ZUSTAND_DIR/zustand"
RESOLV="$WURZEL/etc/resolv.conf"

TROCKEN=nein
ZURUECK=nein
NUR_STATUS=nein
while [ $# -gt 0 ]; do
  case "$1" in
    --trocken) TROCKEN=ja ;;
    --zurueck) ZURUECK=ja ;;
    --status) NUR_STATUS=ja ;;
    --gateway)
      shift
      [ $# -gt 0 ] && [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]] || { echo "Ungültiger Wert für --gateway (a.b.c.d/nn)." >&2; exit 2; }
      GATEWAY="$1"
      ;;
    --loopback-port)
      shift
      [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]{1,5}$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ] || { echo "Ungültiger Wert für --loopback-port (1 bis 65535)." >&2; exit 2; }
      [ "${#LOOPBACK_PORTS[@]}" -lt 8 ] || { echo "Höchstens 8 Loopback-Ports." >&2; exit 2; }
      LOOPBACK_PORTS+=("$((10#$1))")
      ;;
    *) echo "Aufruf: $0 [--trocken] [--status] [--zurueck] [--gateway NETZ] [--loopback-port N]" >&2; exit 2 ;;
  esac
  shift
done

fehler() { echo "$*" >&2; exit 1; }
hat() { command -v "$1" >/dev/null 2>&1; }
# Jede Änderung läuft über tu: im Trockenlauf wird sie nur angezeigt
tu() {
  if [ "$TROCKEN" = ja ]; then printf '  [trocken] %s\n' "$*"; else "$@"; fi
}

if [ -z "$WURZEL" ] && [ "$TROCKEN" = nein ] && [ "$NUR_STATUS" = nein ] && [ "$(id -u)" != 0 ]; then
  fehler "Bitte mit sudo ausführen (oder mit --trocken bzw. --status ansehen)."
fi

# ---------- Regeltext (deterministisch, ein Lauf mit denselben Angaben ergibt dieselbe Datei)
nft_inhalt() {
  local ports=""
  if [ "${#LOOPBACK_PORTS[@]}" -gt 0 ]; then
    ports="$(printf '%s\n' "${LOOPBACK_PORTS[@]}" | sort -n -u | paste -sd, - | sed 's/,/, /g')"
  fi
  cat <<NFT
# WAMO Remote Desktop: Ausgang des Kontos $KONTO nur ins Internet. Erzeugt von infra/sparks/wamo-rd-ausgang-einrichten.sh;
# entfernen: --zurueck. Eigene Tabelle, fremde Regeln bleiben unberührt. Nur Pakete von Sockets des Benutzers $KONTO.
table inet $TABELLE
delete table inet $TABELLE
table inet $TABELLE {
	set gesperrt4 {
		type ipv4_addr
		flags interval
		elements = { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.0.0.0/24, 192.168.0.0/16, 198.18.0.0/15, 224.0.0.0/3 }
	}
	chain ausgang {
		type filter hook output priority -10; policy accept;
		meta skuid $KONTO jump wamo_rd
	}
	chain wamo_rd {
		ip daddr $GATEWAY ct state established,related counter accept comment "Rueckantworten an das Gateway"
		ip daddr 127.0.0.53 meta l4proto { tcp, udp } th dport 53 counter accept comment "DNS ueber systemd-resolved"
NFT
  if [ -n "$ports" ]; then
    printf '\t\tip daddr 127.0.0.1 tcp dport { %s } counter accept comment "erlaubte Loopback-Ports"\n' "$ports"
  fi
  cat <<NFT
		meta nfproto ipv6 counter drop comment "kein IPv6"
		ip daddr @gesperrt4 counter drop comment "kein LAN, kein Loopback"
		counter accept comment "Internet"
	}
}
NFT
}

unit_inhalt() {
  cat <<UNIT_ENDE
# WAMO: lädt die Ausgangsregel für das Konto $KONTO beim Start. Erzeugt von infra/sparks/wamo-rd-ausgang-einrichten.sh; entfernen: --zurueck.
[Unit]
Description=WAMO: Ausgang des Kontos $KONTO nur ins Internet
Wants=network-pre.target
Before=network-pre.target xrdp.service xrdp-sesman.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/wamo-rd-ausgang/regeln.nft
ExecStop=-/usr/sbin/nft delete table inet $TABELLE

[Install]
WantedBy=multi-user.target
UNIT_ENDE
}

dropin_inhalt() {
  printf '# WAMO: xrdp nur mit geladener Ausgangsregel für %s (wamo-rd-ausgang-einrichten.sh)\n[Unit]\nRequires=wamo-rd-ausgang.service\nAfter=wamo-rd-ausgang.service\n' "$KONTO"
}

zustand_setzen() {
  [ "$TROCKEN" = nein ] || return 0
  install -d -m 755 "$ZUSTAND_DIR"
  { [ -f "$ZUSTAND" ] && grep -v "^$1=" "$ZUSTAND" || true; printf '%s=%s\n' "$1" "$2"; } > "$ZUSTAND.neu"
  mv -f "$ZUSTAND.neu" "$ZUSTAND"
}

# Datei atomar ersetzen: erst schreiben, dann umbenennen
datei_schreiben() {
  local ziel="$1" inhalt="$2" modus="${3:-644}"
  install -d -m 755 "$(dirname "$ziel")"
  printf '%s\n' "$inhalt" > "$ziel.neu"
  chmod "$modus" "$ziel.neu"
  mv -f "$ziel.neu" "$ziel"
}

tabelle_steht() { hat nft && nft list table inet "$TABELLE" >/dev/null 2>&1; }

# ---------- Vorprüfung
vorpruefung() {
  echo "== Vorprüfung"
  if [ -z "$WURZEL" ]; then
    hat nft || fehler "nft fehlt (sudo apt-get install nftables)."
    local uid
    uid="$(id -u "$KONTO" 2>/dev/null)" || fehler "Konto $KONTO fehlt (zuerst wamo-rd-konto-einrichten.sh)."
    [[ "$uid" =~ ^[0-9]+$ ]] && [ "$uid" -ge 1000 ] || fehler "Konto $KONTO hat die Kennung $uid; erwartet ist ein normales Konto (ab 1000), nie root oder ein Systemkonto."
    echo "  Konto $KONTO vorhanden (Kennung $uid)"
    local gr
    gr="$(id -nG "$KONTO")"
    case " $gr " in *" sudo "*|*" docker "*|*" root "*) fehler "$KONTO ist in einer privilegierten Gruppe ($gr); die Ausgangsregel ersetzt das nicht. Abbruch." ;; esac
  else
    echo "  Probemodus (WAMO_RD_AUSGANG_WURZEL): das Konto wird nicht geprüft"
  fi
  if [ -r "$RESOLV" ]; then
    local ns
    ns="$(awk '/^nameserver[[:space:]]/ {print $2}' "$RESOLV" | paste -sd' ' -)"
    if [ "$ns" != 127.0.0.53 ]; then
      echo "  WARNUNG: /etc/resolv.conf nennt \"${ns:-keinen Nameserver}\" statt 127.0.0.53. Die Regel erlaubt nur DNS über den Stub;" \
        "Namensauflösung in der Sitzung geht sonst nicht (oder die Regel muss um den Nameserver ergänzt werden)."
    else
      echo "  Namensauflösung über den Stub 127.0.0.53 (erlaubt)"
    fi
  fi
}

# ---------- Einrichten
einrichten() {
  vorpruefung
  echo "== Regel: Ausgang von $KONTO nur ins Internet (Tabelle inet $TABELLE)"
  local neu_nft neu_unit neu_dropin gleich=ja f
  neu_nft="$(nft_inhalt)"
  neu_unit="$(unit_inhalt)"
  neu_dropin="$(dropin_inhalt)"
  [ -f "$NFT_DATEI" ] && [ "$(cat "$NFT_DATEI")" = "$neu_nft" ] || gleich=nein
  [ -f "$UNIT" ] && [ "$(cat "$UNIT")" = "$neu_unit" ] || gleich=nein
  for f in "${DROPINS[@]}"; do
    [ -f "$f" ] && [ "$(cat "$f")" = "$neu_dropin" ] || gleich=nein
  done
  if [ "$gleich" = ja ] && tabelle_steht; then
    echo "  Regel steht schon, unverändert (Gateway-Netz $GATEWAY, Loopback-Ports: ${LOOPBACK_PORTS[*]:-keine})"
    return 0
  fi
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] Datei $NFT_DATEI:"
    sed 's/^/    /' <<<"$neu_nft"
    echo "  [trocken] Unit $UNIT (Type=oneshot, lädt die Datei beim Start)"
    for f in "${DROPINS[@]}"; do echo "  [trocken] Drop-in $f (Requires und After: xrdp nur mit geladener Regel)"; done
    echo "  [trocken] systemctl daemon-reload; systemctl enable wamo-rd-ausgang.service; systemctl restart wamo-rd-ausgang.service"
    echo "  Trockenlauf: es wurde nichts geändert."
    return 0
  fi
  # Sicherung einer abweichenden Regeldatei
  if [ -f "$NFT_DATEI" ] && [ "$(cat "$NFT_DATEI")" != "$neu_nft" ]; then
    install -d -m 700 "$SICHERUNG"
    cp -a "$NFT_DATEI" "$SICHERUNG/regeln.nft.vor-wamo-$(date +%Y%m%d-%H%M%S)"
    echo "  Alte Regeldatei gesichert nach $SICHERUNG"
  fi
  # Erst prüfen (nft -c ändert nichts), dann übernehmen
  install -d -m 755 "$DIR"
  printf '%s\n' "$neu_nft" > "$NFT_DATEI.pruef"
  hat nft || { rm -f "$NFT_DATEI.pruef"; fehler "nft fehlt, nichts geändert."; }
  nft -c -f "$NFT_DATEI.pruef" || { rm -f "$NFT_DATEI.pruef"; fehler "nft lehnt die Regeln ab, nichts geändert."; }
  rm -f "$NFT_DATEI.pruef"
  datei_schreiben "$NFT_DATEI" "$neu_nft"
  datei_schreiben "$UNIT" "$neu_unit"
  for f in "${DROPINS[@]}"; do datei_schreiben "$f" "$neu_dropin"; done
  tu systemctl daemon-reload
  tu systemctl enable wamo-rd-ausgang.service
  tu systemctl restart wamo-rd-ausgang.service
  zustand_setzen gateway "$GATEWAY"
  zustand_setzen loopback_ports "${LOOPBACK_PORTS[*]:-}"
  tabelle_steht || fehler "Die Tabelle inet $TABELLE ist nach dem Laden nicht da (journalctl -u wamo-rd-ausgang). Rückweg: --zurueck"
  nft list table inet "$TABELLE" | sed 's/^/  /'
  echo "  Regel geladen. Wirkt auf neue Verbindungen sofort, auf bestehende Sitzungen von $KONTO bei deren nächstem Paket."
  echo "  Probe: python3 infra/sokrates2/wamo_rd_rauchtest.py --betrieb (Verbotsproben als $KONTO)."
}

# ---------- Status
status() {
  echo "== Status"
  if ! hat nft; then
    echo "  nft nicht installiert"
  elif tabelle_steht; then
    echo "  Tabelle inet $TABELLE: geladen"
    nft list table inet "$TABELLE" 2>/dev/null | grep -E 'counter|skuid' | sed 's/^[[:space:]]*/  /' || true
  else
    echo "  Tabelle inet $TABELLE: nicht geladen (oder ohne root nicht lesbar)"
  fi
  [ -f "$NFT_DATEI" ] && echo "  Regeldatei: $NFT_DATEI" || echo "  Regeldatei: fehlt"
  [ -f "$UNIT" ] && echo "  Unit: vorhanden" || echo "  Unit: fehlt"
  if hat systemctl; then echo "  Dienst: $(systemctl is-active wamo-rd-ausgang.service 2>/dev/null || true)"; fi
  [ -f "$ZUSTAND" ] && sed 's/^/  gesetzt: /' "$ZUSTAND" || true
}

# ---------- Rückweg
zurueck() {
  echo "== Rückweg"
  local f
  tu systemctl disable --now wamo-rd-ausgang.service
  if hat nft; then
    if [ "$TROCKEN" = ja ]; then
      echo "  [trocken] nft delete table inet $TABELLE"
    else
      nft delete table inet "$TABELLE" 2>/dev/null || true
    fi
  fi
  for f in "${DROPINS[@]}" "$UNIT" "$NFT_DATEI"; do tu rm -f "$f"; done
  for f in "${DROPINS[@]}"; do tu rmdir "$(dirname "$f")" 2>/dev/null || true; done
  tu rmdir "$DIR" 2>/dev/null || true
  tu systemctl daemon-reload
  if [ "$TROCKEN" = nein ]; then rm -f "$ZUSTAND"; rmdir "$ZUSTAND_DIR" 2>/dev/null || true; fi
  echo "  Die Sicherungen bleiben liegen: $SICHERUNG"
  echo "Rückweg fertig. Das Konto $KONTO erreicht jetzt wieder alles, was die Firewall des Rechners zulässt."
}

if [ "$NUR_STATUS" = ja ]; then
  status
elif [ "$ZURUECK" = ja ]; then
  zurueck
else
  einrichten
fi
