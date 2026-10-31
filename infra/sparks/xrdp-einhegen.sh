#!/usr/bin/env bash
# xrdp auf einem Spark einhegen (Remote Desktop, Stufe 0, docs/remote-desktop.md Abschnitt 2.3). Gedacht für Caesar, wo xrdp
# auf *:3389 ohne Beschränkung lauscht. Das Skript macht zwei Dinge und sonst nichts:
#   1. /etc/xrdp/sesman.ini: AllowRootLogin=false (kein Anmelden als root über RDP)
#   2. Port 3389 nur für die Quelladresse von Sokrates-2 (192.168.178.136) und lokal (Loopback)
#      - ufw aktiv: zwei Regeln mit dem Kommentar wamo-xrdp, eingefügt VOR vorhandene Erlaubnisse
#      - sonst: eigene nftables-Tabelle inet wamo_xrdp (fremde Tabellen und Regeln bleiben unberührt), dauerhaft über die
#        Unit wamo-xrdp-sperre.service
# Es aktualisiert NICHTS von selbst (kein apt), startet xrdp nicht neu und zeigt nur Sicherheitsmerkmale an
# (Paketversion, Kandidat im Index, Nennung von CVE-2025-68670 im Changelog).
#
# Aufruf (auf dem Spark, der Nutzer führt es selbst aus; das sudo-Passwort tippt er am Terminal, das Skript kennt es nie):
#   sudo bash xrdp-einhegen.sh --trocken     # zeigt nur an, ändert nichts (auch ohne sudo lauffähig, dann ohne ufw-Stand)
#   sudo bash xrdp-einhegen.sh               # einhegen
#   sudo bash xrdp-einhegen.sh --neustart    # einhegen und xrdp-sesman neu starten, aber nur ohne laufende Sitzung
#   sudo bash xrdp-einhegen.sh --status      # nur Sicherheitsmerkmale und Stand der Sperre anzeigen
#   sudo bash xrdp-einhegen.sh --zurueck     # Rückweg (nur, was dieses Skript gesetzt hat)
#
# AllowRootLogin wirkt erst nach einem Neustart von xrdp-sesman. Der Port ist sofort beschränkt.
# Wiederholbar: Ist alles schon gesetzt, ändert ein weiterer Lauf nichts und legt keine neue Sicherung an.
#
# Sicherung: vor jeder Änderung an sesman.ini entsteht /etc/xrdp/sesman.ini.vor-wamo-ZEITSTEMPEL; Stand von ufw und die
# ufw-Regeldateien liegen in /var/backups/wamo-xrdp. Was vorher galt, steht in /var/lib/wamo-xrdp/zustand; --zurueck stellt
# genau das wieder her (nur die eine Zeile in sesman.ini, nicht die ganze Datei) und entfernt die eigenen Regeln.
#
# Einstellungen für Proben und Tests (nicht für den Betrieb): WAMO_XRDP_WURZEL stellt allen Dateipfaden einen Wegwerf-Ordner
# voran und hebt die Pflicht zu root auf; WAMO_XRDP_QUELLE ersetzt die Quelladresse (nur IPv4).
set -euo pipefail

WURZEL="${WAMO_XRDP_WURZEL:-}"
QUELLE="${WAMO_XRDP_QUELLE:-192.168.178.136}"
PORT=3389
MARKE=wamo-xrdp
NFT_TABELLE=wamo_xrdp

[[ "$QUELLE" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "Ungültige Quelladresse: $QUELLE" >&2; exit 2; }
if [ -n "$WURZEL" ]; then
  [[ "$WURZEL" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo "Ungültiger WAMO_XRDP_WURZEL: $WURZEL" >&2; exit 2; }
fi

SESMAN="$WURZEL/etc/xrdp/sesman.ini"
XRDP_INI="$WURZEL/etc/xrdp/xrdp.ini"
CHANGELOG="$WURZEL/usr/share/doc/xrdp/changelog.Debian.gz"
SICHERUNG="$WURZEL/var/backups/wamo-xrdp"
ZUSTAND_DIR="$WURZEL/var/lib/wamo-xrdp"
ZUSTAND="$ZUSTAND_DIR/zustand"
NFT_DIR="$WURZEL/etc/wamo-xrdp"
NFT_DATEI="$NFT_DIR/sperre.nft"
UNIT="$WURZEL/etc/systemd/system/wamo-xrdp-sperre.service"
UFW_USER_DATEIEN=("$WURZEL/etc/ufw/user.rules" "$WURZEL/etc/ufw/user6.rules")

TROCKEN=nein
ZURUECK=nein
NEUSTART=nein
NUR_STATUS=nein
for arg in "$@"; do
  case "$arg" in
    --trocken) TROCKEN=ja ;;
    --zurueck) ZURUECK=ja ;;
    --neustart) NEUSTART=ja ;;
    --status) NUR_STATUS=ja ;;
    *) echo "Aufruf: $0 [--trocken] [--neustart] [--status] [--zurueck]" >&2; exit 2 ;;
  esac
done

fehler() { echo "$*" >&2; exit 1; }
hat() { command -v "$1" >/dev/null 2>&1; }
# Jede Änderung läuft über tu: im Trockenlauf wird sie nur angezeigt
tu() {
  if [ "$TROCKEN" = ja ]; then
    printf '  [trocken] %s\n' "$*"
  else
    "$@"
  fi
}

if [ -z "$WURZEL" ] && [ "$TROCKEN" = nein ] && [ "$NUR_STATUS" = nein ] && [ "$(id -u)" != 0 ]; then
  fehler "Bitte mit sudo ausführen (oder mit --trocken bzw. --status ansehen)."
fi

# ---------- Lesehilfen
# Wert eines Schlüssels in einer ini-Datei (letzter Treffer, Kommentare mit ; oder # ignoriert), optional nur in einem Abschnitt
ini_wert() {
  local datei="$1" schluessel="$2" abschnitt="${3:-}"
  [ -r "$datei" ] || return 0
  awk -v s="$schluessel" -v ab="$abschnitt" '
    /^[[:space:]]*\[/ { aktuell = $0; gsub(/[][[:space:]]/, "", aktuell); next }
    /^[[:space:]]*[;#]/ { next }
    {
      if (ab != "" && aktuell != ab) next
      zeile = $0
      eq = index(zeile, "=")
      if (eq == 0) next
      name = substr(zeile, 1, eq - 1); gsub(/[[:space:]]/, "", name)
      if (name == s) { wert = substr(zeile, eq + 1); sub(/[;#].*$/, "", wert); gsub(/^[[:space:]]+|[[:space:]]+$/, "", wert); ergebnis = wert }
    }
    END { print ergebnis }
  ' "$datei"
}

ist_aus() { case "$(tr '[:upper:]' '[:lower:]' <<<"${1:-}")" in false|0|no|off) return 0 ;; *) return 1 ;; esac; }

# Ausgaben erst in eine Variable (kein "| grep -q"): Mit pipefail würde ein früh beendeter Leser das Ergebnis verfälschen
ufw_aktiv() {
  local s
  hat ufw || return 1
  s="$(ufw status 2>/dev/null || true)"
  [[ "$s" == "Status: active"* ]]
}
ufw_lesbar() { hat ufw && ufw status >/dev/null 2>&1; }

sperre_art_ist() {
  # Welche eigene Sperre steht schon? (ufw, nft oder leer)
  local u=""
  if hat ufw; then u="$(ufw show added 2>/dev/null || true)"; fi
  if [[ "$u" == *"$MARKE"* ]]; then echo ufw; return; fi
  if hat nft && nft list table inet "$NFT_TABELLE" >/dev/null 2>&1; then echo nft; return; fi
  echo ""
}

# ---------- 1. Sicherheitsmerkmale (nur lesend)
merkmale() {
  echo "== Sicherheitsmerkmale von xrdp"
  local version="" policy installiert="" kandidat="" treffer wert
  if hat dpkg-query; then version="$(dpkg-query -W -f='${Version}' xrdp 2>/dev/null || true)"; fi
  if [ -z "$version" ]; then
    echo "  xrdp: Paket nicht installiert (dpkg-query)"
  else
    echo "  Paketversion: xrdp $version"
    if hat apt; then
      echo "  apt list --installed: $(apt list --installed xrdp 2>/dev/null | grep -E '^xrdp/' | head -n1 || true)"
    fi
    if hat apt-cache; then
      policy="$(apt-cache policy xrdp 2>/dev/null || true)"
      installiert="$(awk '/^ *Installed:/ { print $2; exit }' <<<"$policy")"
      kandidat="$(awk '/^ *Candidate:/ { print $2; exit }' <<<"$policy")"
      echo "  apt-cache policy: installiert $installiert, Kandidat $kandidat"
      if [ -n "$kandidat" ] && [ "$kandidat" != "(none)" ] && [ "$kandidat" != "$installiert" ]; then
        echo "  UPDATE STEHT AN: $installiert -> $kandidat (CVE-2025-68670, Pufferüberlauf vor der Anmeldung)."
        echo "    Dieses Skript aktualisiert nichts. Vorher ansehen: apt changelog xrdp | grep -n CVE-2025-68670"
        echo "    Dann bewusst: sudo apt-get install --only-upgrade xrdp"
      else
        echo "  Kein neueres Paket im Index (apt-get update vorher ausführen, falls lange her)."
      fi
    fi
    if [ -r "$CHANGELOG" ] && hat zcat; then
      treffer="$(zcat "$CHANGELOG" 2>/dev/null | grep -c 'CVE-2025-68670' || true)"
      if [ "${treffer:-0}" -gt 0 ]; then
        echo "  Changelog des installierten Pakets nennt CVE-2025-68670: ja ($treffer Zeilen), Rückportierung wahrscheinlich enthalten."
      else
        echo "  Changelog des installierten Pakets nennt CVE-2025-68670: nein. Der Patchstand ist NICHT belegt."
      fi
    else
      echo "  Changelog nicht lesbar: Patchstand NICHT belegt (apt changelog xrdp prüfen)."
    fi
  fi
  if [ -r "$SESMAN" ]; then
    echo "  sesman.ini: AllowRootLogin=$(ini_wert "$SESMAN" AllowRootLogin Security), AlwaysGroupCheck=$(ini_wert "$SESMAN" AlwaysGroupCheck Security)," \
      "KillDisconnected=$(ini_wert "$SESMAN" KillDisconnected Sessions), IdleTimeLimit=$(ini_wert "$SESMAN" IdleTimeLimit Sessions)"
  else
    echo "  sesman.ini: nicht lesbar ($SESMAN)"
  fi
  if [ -r "$XRDP_INI" ]; then
    wert="security_layer=$(ini_wert "$XRDP_INI" security_layer), cliprdr=$(ini_wert "$XRDP_INI" cliprdr Channels), rdpdr=$(ini_wert "$XRDP_INI" rdpdr Channels)"
    echo "  xrdp.ini: $wert (Zwischenablage, Laufwerke und Sicherheitsstufe: Stufe 6 des Konzepts, hier unverändert)"
  fi
  if hat ss; then
    echo "  Lauscht auf Port $PORT: $(ss -tlnH "sport = :$PORT" 2>/dev/null | awk '{ print $4 }' | paste -sd' ' - || true)"
  fi
  if ufw_lesbar; then
    if ufw_aktiv; then echo "  Firewall: ufw aktiv"; else echo "  Firewall: ufw installiert, aber nicht aktiv (dann gilt die nftables-Tabelle $NFT_TABELLE)"; fi
  elif hat ufw; then
    echo "  Firewall: ufw vorhanden, Zustand ohne root nicht lesbar"
  else
    echo "  Firewall: kein ufw (dann gilt die nftables-Tabelle $NFT_TABELLE)"
  fi
  local art
  art="$(sperre_art_ist)"
  echo "  Eigene Sperre für Port $PORT: ${art:-keine}"
}

# ---------- 2. Sicherung
ZEIT="$(date +%Y%m%d-%H%M%S)"
sicherung_anlegen() {
  echo "== Sicherung"
  tu install -d -m 700 "$SICHERUNG"
  tu cp -p "$SESMAN" "$SESMAN.vor-wamo-$ZEIT"
  echo "  $SESMAN.vor-wamo-$ZEIT"
  local f
  for f in "${UFW_USER_DATEIEN[@]}"; do
    if [ -r "$f" ]; then
      tu cp -p "$f" "$SICHERUNG/$(basename "$f").$ZEIT"
      echo "  $SICHERUNG/$(basename "$f").$ZEIT"
    fi
  done
  if ufw_lesbar && [ "$TROCKEN" = nein ]; then
    ufw status numbered > "$SICHERUNG/ufw-status.$ZEIT.txt" 2>/dev/null || true
    chmod 600 "$SICHERUNG/ufw-status.$ZEIT.txt"
    echo "  $SICHERUNG/ufw-status.$ZEIT.txt"
  fi
}

zustand_setzen() {
  # schreibt SCHLÜSSEL=WERT, ersetzt vorhandene Zeile; nur wenn der Schlüssel noch fehlt, wenn "nur_neu" gesetzt ist
  local schluessel="$1" wert="$2" nur_neu="${3:-}"
  [ "$TROCKEN" = nein ] || { printf '  [trocken] Zustand %s=%s\n' "$schluessel" "$wert"; return 0; }
  install -d -m 700 "$ZUSTAND_DIR"
  [ -f "$ZUSTAND" ] || install -m 600 /dev/null "$ZUSTAND"
  if grep -q "^$schluessel=" "$ZUSTAND"; then
    [ -z "$nur_neu" ] || return 0
    local tmp
    tmp="$(mktemp "$ZUSTAND.XXXXXX")"
    grep -v "^$schluessel=" "$ZUSTAND" > "$tmp" || true
    printf '%s=%s\n' "$schluessel" "$wert" >> "$tmp"
    cat "$tmp" > "$ZUSTAND"
    rm -f "$tmp"
  else
    printf '%s=%s\n' "$schluessel" "$wert" >> "$ZUSTAND"
  fi
}

zustand_lesen() {
  [ -r "$ZUSTAND" ] || return 0
  grep -m1 "^$1=" "$ZUSTAND" | cut -d= -f2- || true
}

# ---------- 3. sesman.ini
# Neuer Inhalt mit AllowRootLogin=false im Abschnitt [Security] (ersetzt, fügt am Ende des Abschnitts ein oder legt ihn an)
sesman_neu_erzeugen() {
  awk '
    /^[[:space:]]*\[/ {
      if (insec && !done) { print "AllowRootLogin=false"; done = 1 }
      insec = ($0 ~ /^[[:space:]]*\[Security\][[:space:]]*$/)
    }
    insec && /^[[:space:]]*AllowRootLogin[[:space:]]*=/ { if (!done) { print "AllowRootLogin=false"; done = 1 } next }
    { print }
    END {
      if (insec && !done) print "AllowRootLogin=false"
      else if (!done) { print ""; print "[Security]"; print "AllowRootLogin=false" }
    }
  ' "$SESMAN"
}

sesman_datei_ersetzen() {
  # Atomar im selben Ordner, Rechte und Besitzer der Vorlage bleiben
  local tmp
  tmp="$(mktemp "$SESMAN.neu.XXXXXX")"
  cat > "$tmp"
  chmod --reference="$SESMAN" "$tmp"
  chown --reference="$SESMAN" "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$SESMAN"
}

root_login_sperren() {
  echo "== sesman.ini: AllowRootLogin=false"
  local jetzt
  jetzt="$(ini_wert "$SESMAN" AllowRootLogin Security)"
  if ist_aus "$jetzt"; then
    echo "  schon gesetzt (AllowRootLogin=$jetzt), nichts zu tun"
    return 0
  fi
  echo "  bisher: AllowRootLogin=${jetzt:-<nicht gesetzt>}"
  sicherung_anlegen
  zustand_setzen allowrootlogin_vorher "${jetzt:-<fehlt>}" nur_neu
  if [ "$TROCKEN" = ja ]; then
    echo "  [trocken] neuer Inhalt (Unterschied zur Datei):"
    diff <(cat "$SESMAN") <(sesman_neu_erzeugen) | sed 's/^/    /' || true
  else
    sesman_neu_erzeugen | sesman_datei_ersetzen
    echo "  gesetzt: AllowRootLogin=$(ini_wert "$SESMAN" AllowRootLogin Security)"
  fi
}

# ---------- 4. Port 3389
ufw_regel() {
  # ufw_regel POSITION ARGUMENTE...: an Position einfügen, wenn es schon Regeln gibt, sonst anhängen
  local pos="$1"
  shift
  local liste
  liste="$(ufw status numbered 2>/dev/null || true)"
  if grep -q '^\[ *1\]' <<<"$liste"; then
    tu ufw insert "$pos" "$@"
  else
    tu ufw "$@"
  fi
}

sperre_ufw() {
  echo "== Port $PORT über ufw (nur $QUELLE und Loopback)"
  local hinzugefuegt
  hinzugefuegt="$(ufw show added 2>/dev/null || true)"
  if grep -qE "allow from ${QUELLE//./\\.} .*port $PORT .*$MARKE" <<<"$hinzugefuegt" \
    && grep -qE "deny .*$PORT.*$MARKE" <<<"$hinzugefuegt"; then
    echo "  Regeln stehen schon"
    return 0
  fi
  # Die Erlaubnis für das Gateway zuerst, die Sperre danach; beide VOR den vorhandenen Regeln (sonst gewinnt eine breite
  # Erlaubnis wie "allow from 192.168.178.0/24"). Loopback erlaubt ufw selbst vor allen Benutzerregeln.
  ufw_regel 1 allow from "$QUELLE" to any port "$PORT" proto tcp comment "$MARKE"
  ufw_regel 2 deny proto tcp from any to any port "$PORT" comment "$MARKE"
  zustand_setzen sperre ufw
  if [ "$TROCKEN" = nein ]; then ufw status numbered | grep -E "$PORT" | sed 's/^/  /' || true; fi
}

nft_inhalt() {
  cat <<NFT
# WAMO: Port $PORT (xrdp) nur von $QUELLE und lokal. Eigene Tabelle, fremde Regeln bleiben unberührt.
# Erzeugt von infra/sparks/xrdp-einhegen.sh; entfernen: --zurueck.
table inet $NFT_TABELLE
delete table inet $NFT_TABELLE
table inet $NFT_TABELLE {
	chain eingang {
		type filter hook input priority -10; policy accept;
		tcp dport $PORT ip saddr { 127.0.0.1, $QUELLE } accept
		tcp dport $PORT ip6 saddr ::1 accept
		tcp dport $PORT counter drop
	}
}
NFT
}

unit_inhalt() {
  cat <<UNIT_ENDE
# WAMO: lädt die Sperre für Port $PORT (xrdp) beim Start. Erzeugt von infra/sparks/xrdp-einhegen.sh; entfernen: --zurueck.
[Unit]
Description=WAMO: Port $PORT (xrdp) nur von $QUELLE und lokal
Wants=network-pre.target
Before=network-pre.target xrdp.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/wamo-xrdp/sperre.nft
ExecStop=-/usr/sbin/nft delete table inet $NFT_TABELLE

[Install]
WantedBy=multi-user.target
UNIT_ENDE
}

sperre_nft() {
  echo "== Port $PORT über nftables (Tabelle inet $NFT_TABELLE, nur $QUELLE und Loopback)"
  hat nft || fehler "Weder aktives ufw noch nft vorhanden: Port $PORT kann nicht beschränkt werden (Abbruch, nichts geändert)."
  local neu_nft neu_unit
  neu_nft="$(nft_inhalt)"
  neu_unit="$(unit_inhalt)"
  if [ "$TROCKEN" = nein ]; then
    install -d -m 755 "$NFT_DIR" "$(dirname "$UNIT")"
    printf '%s\n' "$neu_nft" > "$NFT_DATEI.neu"
    # Erst prüfen, dann übernehmen (nft -c ändert nichts)
    nft -c -f "$NFT_DATEI.neu" || { rm -f "$NFT_DATEI.neu"; fehler "nft lehnt die Regeln ab, nichts geändert."; }
    install -m 644 "$NFT_DATEI.neu" "$NFT_DATEI"
    rm -f "$NFT_DATEI.neu"
    printf '%s\n' "$neu_unit" > "$UNIT.neu"
    install -m 644 "$UNIT.neu" "$UNIT"
    rm -f "$UNIT.neu"
  else
    echo "  [trocken] Datei $NFT_DATEI:"
    sed 's/^/    /' <<<"$neu_nft"
    echo "  [trocken] Unit $UNIT (Type=oneshot, lädt die Datei beim Start)"
  fi
  tu systemctl daemon-reload
  tu systemctl enable --now wamo-xrdp-sperre.service
  zustand_setzen sperre nft
  if [ "$TROCKEN" = nein ]; then nft list table inet "$NFT_TABELLE" 2>/dev/null | sed 's/^/  /' || true; fi
}

port_sperren() {
  local art
  art="$(sperre_art_ist)"
  if [ "$art" = nft ]; then
    echo "== Port $PORT: eigene nftables-Tabelle steht schon, wird erneuert"
    sperre_nft
  elif ufw_aktiv; then
    sperre_ufw
  elif [ "$TROCKEN" = ja ] && hat ufw && ! ufw_lesbar; then
    echo "== Port $PORT: ufw ist ohne root nicht lesbar. Geplant für den Fall \"ufw aktiv\":"
    sperre_ufw
    echo "   und für den Fall \"ufw nicht aktiv\":"
    sperre_nft
  else
    sperre_nft
  fi
}

# ---------- 5. Neustart von xrdp-sesman
sesman_neu_starten() {
  echo "== xrdp-sesman"
  if [ "$NEUSTART" != ja ]; then
    echo "  Kein Neustart (Option --neustart). AllowRootLogin wirkt erst danach:"
    echo "    sudo systemctl restart xrdp-sesman    (nur ohne laufende Sitzung)"
    return 0
  fi
  if hat pgrep && pgrep -x xrdp-chansrv >/dev/null 2>&1; then
    echo "  Es läuft eine xrdp-Sitzung (xrdp-chansrv). Kein Neustart; später wiederholen oder von Hand neu starten."
    return 0
  fi
  tu systemctl restart xrdp-sesman
  [ "$TROCKEN" = ja ] || echo "  xrdp-sesman neu gestartet"
}

einhegen() {
  [ -r "$SESMAN" ] || fehler "$SESMAN nicht lesbar: ist xrdp installiert?"
  merkmale
  echo
  root_login_sperren
  port_sperren
  sesman_neu_starten
  echo
  echo "== Ergebnis"
  echo "  AllowRootLogin=$(ini_wert "$SESMAN" AllowRootLogin Security) (wirksam nach Neustart von xrdp-sesman)"
  echo "  Eigene Sperre für Port $PORT: $(sperre_art_ist || true)"
  if [ "$TROCKEN" = ja ]; then echo "  Trockenlauf: es wurde nichts geändert."; fi
}

# ---------- Rückweg
zurueck() {
  echo "== Rückweg"
  local vorher art f
  vorher="$(zustand_lesen allowrootlogin_vorher)"
  if [ -n "$vorher" ] && [ -r "$SESMAN" ]; then
    if [ "$vorher" = "<fehlt>" ]; then
      echo "  sesman.ini: Zeile AllowRootLogin entfernen (war vorher nicht gesetzt)"
      if [ "$TROCKEN" = ja ]; then
        echo "  [trocken] Zeile AllowRootLogin in [Security] entfernen"
      else
        awk '/^[[:space:]]*\[/ { insec = ($0 ~ /^[[:space:]]*\[Security\][[:space:]]*$/) }
             insec && /^[[:space:]]*AllowRootLogin[[:space:]]*=/ { next } { print }' "$SESMAN" | sesman_datei_ersetzen
      fi
    else
      echo "  sesman.ini: AllowRootLogin=$vorher (Stand vor WAMO)"
      if [ "$TROCKEN" = ja ]; then
        echo "  [trocken] AllowRootLogin=$vorher setzen"
      else
        [[ "$vorher" =~ ^[A-Za-z0-9]{1,10}$ ]] || fehler "Gespeicherter Wert ungültig, bitte von Hand aus .vor-wamo-* wiederherstellen."
        awk -v w="$vorher" '/^[[:space:]]*\[/ { insec = ($0 ~ /^[[:space:]]*\[Security\][[:space:]]*$/) }
             insec && /^[[:space:]]*AllowRootLogin[[:space:]]*=/ { print "AllowRootLogin=" w; next } { print }' "$SESMAN" | sesman_datei_ersetzen
      fi
    fi
  else
    echo "  sesman.ini: kein gespeicherter Zustand, nichts zurückzusetzen."
    echo "  Falls nötig: Sicherungen ansehen: ls $SESMAN.vor-wamo-*"
  fi
  art="$(sperre_art_ist)"
  if [ "$art" = ufw ] || { hat ufw && [ "$(zustand_lesen sperre)" = ufw ]; }; then
    echo "  ufw: eigene Regeln entfernen"
    tu ufw --force delete allow from "$QUELLE" to any port "$PORT" proto tcp
    tu ufw --force delete deny proto tcp from any to any port "$PORT"
  fi
  if [ -e "$UNIT" ] || [ "$art" = nft ] || [ "$(zustand_lesen sperre)" = nft ]; then
    echo "  nftables: Tabelle $NFT_TABELLE, Unit und Datei entfernen"
    tu systemctl disable --now wamo-xrdp-sperre.service
    if hat nft; then
      if [ "$TROCKEN" = ja ]; then
        echo "  [trocken] nft delete table inet $NFT_TABELLE"
      else
        nft delete table inet "$NFT_TABELLE" 2>/dev/null || true
      fi
    fi
    for f in "$UNIT" "$NFT_DATEI"; do
      tu rm -f "$f"
    done
    tu rmdir "$NFT_DIR" 2>/dev/null || true
    tu systemctl daemon-reload
  fi
  if [ "$TROCKEN" = nein ]; then rm -f "$ZUSTAND"; rmdir "$ZUSTAND_DIR" 2>/dev/null || true; fi
  echo "  Die Sicherungen bleiben liegen: $SESMAN.vor-wamo-* und $SICHERUNG"
  sesman_neu_starten
  echo "Rückweg fertig."
}

if [ "$NUR_STATUS" = ja ]; then
  merkmale
elif [ "$ZURUECK" = ja ]; then
  zurueck
else
  einhegen
fi
