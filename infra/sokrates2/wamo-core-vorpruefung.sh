#!/bin/sh
# Vorprüfung vor jedem Start von wamo-core.service (ExecStartPre, läuft als wamo-core mit der Umgebung der Unit).
# Installiert als /opt/wamo-core/bin/wamo-core-vorpruefung durch wamo-core-einrichten.sh. Gibt nie Werte aus.
#
#  * Keine .env im Code-Baum oder darüber: bot.py ruft load_dotenv(override=True) auf, eine solche Datei würde die
#    Einstellungen der Unit überschreiben (etwa WAMO_ENTWICKLUNG oder die Socket-Pfade).
#  * Pflichtwerte aus /etc/wamo/core.env sind gesetzt.
#  * Socket-Verzeichnis /run/wamo-core gehört wamo-core, Gruppe wamocon (gid 1000, Caddy), für andere gesperrt.
#  * Entwicklungsaufträge bleiben aus.
set -eu

fehler() { echo "wamo-core-Vorprüfung: $*" >&2; exit 1; }

for d in /opt/wamo-core/app/voice-core /opt/wamo-core/app /opt/wamo-core /opt /; do
  [ ! -e "$d/.env" ] || fehler "$d/.env gefunden (würde die Unit überschreiben), bitte entfernen"
done
for name in SONIOX_API_KEY ANTHROPIC_API_KEY SUPABASE_PUBLISHABLE_KEY WAMO_DATABASE_URL; do
  eval "wert=\${$name:-}"
  [ -n "$wert" ] || fehler "$name fehlt in /etc/wamo/core.env (vom PC: python scripts/wamo_kern_sokrates2.py alles)"
done
case "${WAMO_DATABASE_URL:-}" in
  *sslmode=verify-full*) ;;
  *) fehler "WAMO_DATABASE_URL ohne sslmode=verify-full" ;;
esac
[ "${WAMO_ENTWICKLUNG:-aus}" = aus ] || fehler "WAMO_ENTWICKLUNG muss aus sein"
[ "${WAMO_CORE_UDS:-}" = /run/wamo-core/core.sock ] || fehler "WAMO_CORE_UDS unerwartet"
eigen="$(stat -c '%U:%g:%a' /run/wamo-core 2>/dev/null || true)"
[ "$eigen" = "wamo-core:1000:750" ] || fehler "/run/wamo-core hat $eigen statt wamo-core:1000:750 (systemd-tmpfiles --create /etc/tmpfiles.d/wamo-core.conf)"
[ -r "${WAMO_CA_FILE:-/nicht}" ] || fehler "WAMO_CA_FILE nicht lesbar"
if [ "${WAMO_PC_REGISTER:-aus}" = an ]; then
  # Tunnel je Gerät: Wurzel der Geräteordner gehört root und ist nur zu betreten (0711), sonst vertraut der Kern ihr nicht
  wurzel="$(stat -c '%U:%a' "${WAMO_PC_SOCKET_WURZEL:-/run/wamo-pc}" 2>/dev/null || true)"
  [ "$wurzel" = "root:711" ] || fehler "${WAMO_PC_SOCKET_WURZEL:-/run/wamo-pc} hat $wurzel statt root:711 (wamo-pc-tunnel-einrichten.sh)"
fi
exit 0
