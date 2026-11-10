#!/usr/bin/env bash
# WAMO-Auftragsläufer auf Sokrates-2: führt einen Befehl (Claude Code oder den Wächter-Selbsttest) für
# genau einen Projektordner in einer bubblewrap-Sandbox aus, als Benutzer wamo, mit Zeit- und
# Ressourcengrenzen. Installiert als /opt/wamo/bin/wamo-job.sh (root:wamo 0750, /opt/wamo gehört root).
#
# Aufruf (durch wamo-job-dispatch oder den Rauchtest, immer als wamo):
#   wamo-job.sh --auftrag <job> --projekt <name> [--modus lauf|pruefen] [--zeitlimit <s>] [--einheit-zusatz <wort>] -- befehl ...
#
# Modus pruefen (Befehl pruefen des Dispatchers, ein Befehl der Abnahme im Projekt): dieselbe Sandbox, nur ENGER. Eigener
# Netz-Namensraum ohne Netz (nichts hinein, nichts hinaus, auch nicht zum Loopback des Hosts), kein Claude (weder die
# Konfiguration des Auftrags noch Claude Code noch Plugins), kein Zustand des Auftrags (keine Freigaben, kein Socket), nur
# die Umgebungsnamen ERLAUBT_PRUEFEN_RE (Git-Hooks aus, nie ein Token), Zeitlimit höchstens 120 s, engere Grenzen für
# Speicher, Prozesse, CPU und Platz. Beschreibbar bleiben das Projekt, /tmp, .npm und .cache. Seccomp, Landlock, keine
# Capabilities und die Ausgangsregeln gelten unverändert.
#
# Der Auftragsordner /srv/wamo/jobs/<job> muss vom Dispatcher vorbereitet sein (claude/, zustand/,
# zustand/approvals.json, etc/passwd, etc/group). Die Umgebung kommt vom Aufrufer; nur die Namen in
# ERLAUBT_RE gelangen in die Sandbox (Token nie über argv).
#
# Was die Sandbox sieht (alles andere existiert darin nicht):
#   /usr (ohne /usr/local/lib/wamo), /etc nur aus einer Allowlist (eigene passwd/group), /proc (eigener
#   PID-Namensraum), /dev (nur null, zero, random, tty usw.), /tmp (tmpfs), /srv/wamo als tmpfs mit:
#     .local/bin .local/share/claude         nur lesbar (Claude Code, uv, Werkzeuge aus uv tool)
#     .local/share/uv/tools .fnm .npm-global nur lesbar (mcp-atlassian, Node, MCP-Server aus npm -g)
#     .gitconfig                             nur lesbar
#     .npm .cache                            je Auftrag eigener Ordner auf der Platte
#     .claude                                je Auftrag (Sitzungen für --resume); Plugins aus
#                                            /opt/wamo/claude-plugins (ECC ohne Hooks, root-eigen, nur lesbar),
#                                            Plugin-Daten je Auftrag
#     projekte                               eigenes, schreibgeschütztes tmpfs; darin nur projekte/<projekt>,
#                                            beschreibbar, der einzige dauerhafte Arbeitsort
#   /opt/wamo/waechter                       Wächter und Abschottung, nur lesbar (derselbe Pfad wie auf dem Host)
#   /etc/claude-code/managed-settings.json   root-eigen, nur lesbar: Wächter-Hooks, Sperrliste, nur verwaltete Hooks
#   /run/wamo/zustand                        pending.jsonl beschreibbar, approvals.json nur lesbar
#   /run/wamo/projekt                        Verweis auf den Projektordner (für den Wächter)
#   Nicht sichtbar: /etc/wamo, /srv/wamo/.ssh, die echte Konfiguration von wamo (.claude.json, .config,
#   Vercel-Anmeldung), /home, /root, andere Aufträge und Projekte, Geräte (GPU, Platten), Prozesse außerhalb.
#   Alle Pfade, die Hooks und Wächter nutzen, liegen auf Mountpunkten oder der schreibgeschützten Wurzel; sie
#   lassen sich in der Sandbox weder umbenennen noch ersetzen (der Selbsttest prüft das bei jedem Lauf).
#
# Netz: bleibt an (Claude-API, GitHub, npm), aber ohne Wege ins Innere:
#   * Ausgangsfilter (wamo-egress.nft, Tabelle inet wamo_egress für uid wamo): Internet nur auf TCP 80 und 443,
#     DNS an 127.0.0.53, auf Loopback nur 127.0.0.1:20000-20999 (Entwicklungsserver des Auftrags); IPv6, LAN,
#     Docker-Netze, private Netze und eigene Adressen sind abgewiesen.
#   * Landlock (abschottung.py): kein TCP zu Ports, auf denen der Host beim Start lauscht, und zu
#     NETZ_SPERRPORTS; keine abstrakten Unix-Sockets außerhalb (X11).
#   * seccomp (abschottung.py --seccomp-bpf, Pflicht): socket() mit MPTCP oder SCTP (umgingen Landlock) sowie
#     AF_PACKET, AF_VSOCK, AF_ALG; io_uring, keyctl, bpf, perf_event_open, userfaultfd, process_vm_*, mount und
#     Verwandte, unshare, setns, kexec, Kernelmodule; TCP Fast Open (MSG_FASTOPEN). clone3 bekommt ENOSYS.
#   Aufträge teilen sich Loopback; deshalb läuft höchstens einer gleichzeitig (Dispatcher).
#   Im Modus pruefen gilt das alles nicht: dort gibt es kein Netz (eigener Netz-Namensraum, siehe oben).
#
# Grenzen (systemd-run --user, Lingering für wamo): MemoryMax, MemorySwapMax=0, TasksMax, CPUQuota,
# niedrigere CPU- und IO-Gewichtung, RuntimeMaxSec; prlimit für offene Dateien, Dateigröße, keine
# Core-Dateien; timeout für das harte Zeitlimit. Werte in /opt/wamo/wamo-job.conf (root-eigen). Dort steht
# auch CLAUDE_VERSION: weicht die installierte Claude-Code-Version ab (Update), startet nichts, bis
# wamo-job-einrichten.sh erneut lief und der Rauchtest bestanden ist.
#
# Was die Sandbox nicht leistet: Die Token der Auftragsart liegen darin in der Umgebung. Ein feindlicher
# Agent kann sie lesen und mit curl oder einem eigenen Programm nutzen; der Wächter bremst das nur.
set -euo pipefail
umask 077

APP=/opt/wamo
HEIM=/srv/wamo
JOBS=$HEIM/jobs
PROJEKTE=$HEIM/projekte
BWRAP=/usr/local/lib/wamo/bwrap
KONF=$APP/wamo-job.conf
WAECHTER_INNEN=/opt/wamo/waechter
SECCOMP=$APP/waechter/seccomp.bpf
PLUGINS=$APP/claude-plugins

# Standardwerte; wamo-job.conf (root:wamo 0640) darf sie überschreiben
ZEITLIMIT_S=1800
ZEITLIMIT_MAX_S=7200
SPEICHER_MAX=8G
CPU_QUOTA=400%
PROZESSE_MAX=1024
TMP_MB=4096
HEIM_TMP_MB=1024
DATEI_MAX_MB=4096
OFFENE_DATEIEN=8192
NETZ_SPERRPORTS="4000 8000 8001 11434"
NETZ_IMMER_ERLAUBT="53 80 443"
ABSCHOTTUNG=an
CLAUDE_VERSION=""
# Modus pruefen: engere Grenzen als der Auftragslauf (wamo-job.conf darf sie anpassen)
PRUEFEN_SPEICHER_MAX=4G
PRUEFEN_PROZESSE_MAX=256
PRUEFEN_CPU_QUOTA=200%
PRUEFEN_TMP_MB=1024
PRUEFEN_DATEI_MAX_MB=1024
if [ -r "$KONF" ]; then
  # shellcheck source=/dev/null
  . "$KONF"
fi
# Das Zeitlimit im Modus pruefen steht fest im Skript: wamo-job.conf kann es nicht verlängern
ZEITLIMIT_PRUEFEN_S=120

fehler() { echo "wamo-job.sh: $*" >&2; exit 70; }  # nie 2: das hieße "Hook sperrt"

JOB="" PROJEKT="" ZEIT="" ZUSATZ="" MODUS="lauf"
while [ $# -gt 0 ]; do
  case "$1" in
    --auftrag) JOB=${2-}; shift 2 ;;
    --projekt) PROJEKT=${2-}; shift 2 ;;
    --modus) MODUS=${2-}; shift 2 ;;
    --zeitlimit) ZEIT=${2-}; shift 2 ;;
    --einheit-zusatz) ZUSATZ=${2-}; shift 2 ;;
    --) shift; break ;;
    *) fehler "unbekanntes Argument" ;;
  esac
done
[ $# -gt 0 ] || fehler "Befehl fehlt"
[[ $JOB =~ ^[0-9a-f]{16,32}$ ]] || fehler "Auftrags-ID ungültig"
[[ $PROJEKT =~ ^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$ && $PROJEKT != *--* && $PROJEKT != wamo ]] || fehler "Projektname ungültig"
[[ $MODUS =~ ^(lauf|pruefen)$ ]] || fehler "Modus ungültig (lauf oder pruefen)"
ZUSATZ=${ZUSATZ:-$MODUS}
[[ $ZUSATZ =~ ^[a-z]{1,12}$ ]] || fehler "Einheit ungültig"
if [ "$MODUS" = pruefen ]; then
  # Enger als der Auftragslauf, nie weiter: Speicher, Prozesse, CPU, Platz und das Zeitlimit
  SPEICHER_MAX=$PRUEFEN_SPEICHER_MAX CPU_QUOTA=$PRUEFEN_CPU_QUOTA PROZESSE_MAX=$PRUEFEN_PROZESSE_MAX
  TMP_MB=$PRUEFEN_TMP_MB DATEI_MAX_MB=$PRUEFEN_DATEI_MAX_MB
  ZEIT=${ZEIT:-$ZEITLIMIT_PRUEFEN_S}
else
  ZEIT=${ZEIT:-$ZEITLIMIT_S}
fi
[[ $ZEIT =~ ^[0-9]{1,6}$ ]] && (( ZEIT >= 1 && ZEIT <= ZEITLIMIT_MAX_S )) || fehler "Zeitlimit ungültig"
if [ "$MODUS" = pruefen ]; then
  (( ZEIT <= ZEITLIMIT_PRUEFEN_S )) || fehler "Zeitlimit im Modus pruefen höchstens $ZEITLIMIT_PRUEFEN_S Sekunden"
fi
for zahl in "$TMP_MB" "$HEIM_TMP_MB" "$DATEI_MAX_MB" "$OFFENE_DATEIEN" "$PROZESSE_MAX"; do
  [[ $zahl =~ ^[0-9]{1,9}$ ]] || fehler "wamo-job.conf: Zahl ungültig"
done

JOBDIR=$JOBS/$JOB
PROJDIR=$PROJEKTE/$PROJEKT
for pfad in "$JOBDIR" "$JOBDIR/claude" "$JOBDIR/zustand" "$JOBDIR/etc" "$PROJDIR"; do
  [ -d "$pfad" ] && [ ! -L "$pfad" ] || fehler "Ordner fehlt oder ist ein Link: $pfad"
done
for pfad in "$JOBDIR/zustand/approvals.json" "$JOBDIR/etc/passwd" "$JOBDIR/etc/group"; do
  [ -f "$pfad" ] && [ ! -L "$pfad" ] || fehler "Datei fehlt oder ist ein Link: $pfad"
done
[ -x "$BWRAP" ] || fehler "$BWRAP fehlt (wamo-job-einrichten.sh ausführen)"
[ -r "$APP/claude-code/managed-settings.json" ] || fehler "managed-settings.json fehlt"
[ -s "$SECCOMP" ] || fehler "seccomp-Filter $SECCOMP fehlt (wamo-job-einrichten.sh ausführen)"
[ -f "$PLUGINS/installed_plugins.json" ] && [ -d "$PLUGINS/data" ] && [ ! -e "$PLUGINS/cache/ecc/ecc/ohne-hooks/hooks" ]   || fehler "ECC-Kopie ohne Hooks in $PLUGINS fehlt (wamo-job-einrichten.sh ausführen)"
case "$ABSCHOTTUNG" in an|aus) ;; *) fehler "wamo-job.conf: ABSCHOTTUNG muss an oder aus sein" ;; esac
CLAUDE_ECHT=$(readlink -f "$HEIM/.local/bin/claude" || true)
[[ -n $CLAUDE_VERSION && $CLAUDE_ECHT == "$HEIM/.local/share/claude/versions/$CLAUDE_VERSION" ]] \
  || fehler "Claude-Code-Version weicht ab (erwartet ${CLAUDE_VERSION:-?}): wamo-job-einrichten.sh und Rauchtest nötig"

# Umgebung als Allowlist (Token kommen vom Dispatcher, nie über argv). Im Modus pruefen eine eigene, engere: kein einziger
# Token, nichts von Claude, nichts von der Auftragsart; selbst wenn der Aufrufer eines mitschickte, käme es nicht hinein.
ERLAUBT_RE='^(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_CUSTOM_HEADERS|ANTHROPIC_BASE_URL|GH_TOKEN|VERCEL_TOKEN|VERCEL_ORG_ID|SUPABASE_ACCESS_TOKEN|JIRA_URL|JIRA_EMAIL|JIRA_API_KEY|JIRA_API_TOKEN|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_[0-9]|GIT_CONFIG_VALUE_[0-9]|XDG_RUNTIME_DIR)$'
ERLAUBT_PRUEFEN_RE='^(GIT_CONFIG_COUNT|GIT_CONFIG_KEY_[0-9]|GIT_CONFIG_VALUE_[0-9]|XDG_RUNTIME_DIR)$'
[ "$MODUS" != pruefen ] || ERLAUBT_RE=$ERLAUBT_PRUEFEN_RE
while IFS= read -r name; do
  [[ $name =~ $ERLAUBT_RE ]] || unset "$name" 2>/dev/null || true
done < <(compgen -e)
export PATH=/usr/bin:/bin
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Gesperrte Ports: alles, worauf der Host gerade lauscht, plus feste Zusatzports, ohne die immer erlaubten.
# Fällt ss aus oder findet nichts (mindestens sshd lauscht immer), startet nichts: sonst wäre die Sperre still offen.
SS_AUSGABE=$(ss -tlnH) || fehler "ss fehlgeschlagen, lauschende Ports nicht bestimmbar"
HOST_PORTS=$(awk '{print $4}' <<<"$SS_AUSGABE" | sed -nE 's/.*:([0-9]+)$/\1/p' | sort -un)
[ -n "$HOST_PORTS" ] || fehler "keine lauschenden Ports gefunden, Portsperre nicht bestimmbar"
SPERRPORTS=$(
  { printf '%s\n' $HOST_PORTS; printf '%s\n' $NETZ_SPERRPORTS; } \
    | sort -un | grep -vxF -f <(printf '%s\n' $NETZ_IMMER_ERLAUBT) | paste -sd, -
)

# Modus pruefen: kein Netz. Ein eigener Netz-Namensraum, darin nur lo (von bwrap angeschaltet, ohne Verbindung zum Host): nichts
# kommt hinein oder hinaus, auch nicht zum Loopback des Hosts. Ausgangsfilter und Landlock bleiben als zweite Schicht bestehen.
NETZ_OPTION=--share-net
[ "$MODUS" != pruefen ] || NETZ_OPTION=--unshare-net

ARGS=(
  --die-with-parent --new-session
  --unshare-user --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try "$NETZ_OPTION"
  --disable-userns
  --uid "$(id -u)" --gid "$(id -g)" --hostname wamo-auftrag --cap-drop ALL
  --seccomp 9
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/sbin /sbin --symlink usr/lib /lib
  --tmpfs /usr/local/lib/wamo
  --proc /proc --dev /dev
  --perms 1777 --size $((TMP_MB * 1024 * 1024)) --tmpfs /tmp
  --dir /var --symlink ../tmp /var/tmp --dir /run --dir /run/wamo
)
[ -e /lib64 ] && ARGS+=(--symlink usr/lib64 /lib64)

# /etc als Allowlist: nur, was Shell, Node, Python, git, gh, npm, uv und Chrome brauchen
ETC_ERLAUBT="alternatives bash.bashrc ca-certificates ca-certificates.conf debian_version environment fonts gai.conf
  gdb gitconfig gnutls host.conf hostname hosts inputrc java-8-openjdk ld.so.cache ld.so.conf ld.so.conf.d
  locale.alias locale.conf localtime lsb-release machine-id magic magic.mime mime.types mtab netconfig networks
  nsswitch.conf os-release pki profile protocols python3 python3.12 resolv.conf rpc services shells ssl terminfo
  timezone wgetrc xattr.conf xml"
for name in $ETC_ERLAUBT; do
  pfad=/etc/$name
  if [ -L "$pfad" ]; then
    echt=$(readlink -f "$pfad" || true)
    case "$echt" in
      /usr/*|/proc/*) ARGS+=(--symlink "$(readlink "$pfad")" "$pfad") ;;
      /etc/*) ;;  # zeigt auf einen anderen Eintrag in /etc: nicht nötig
      *) [ -e "$echt" ] && ARGS+=(--ro-bind "$echt" "$pfad") ;;
    esac
  elif [ -e "$pfad" ]; then
    ARGS+=(--ro-bind "$pfad" "$pfad")
  fi
done
ARGS+=(--ro-bind "$JOBDIR/etc/passwd" /etc/passwd --ro-bind "$JOBDIR/etc/group" /etc/group)

# Home als tmpfs, darin nur das Nötige
ARGS+=(
  --perms 0700 --size $((HEIM_TMP_MB * 1024 * 1024)) --tmpfs "$HEIM"
  --ro-bind "$HEIM/.local/bin" "$HEIM/.local/bin"
)
# Claude Code gibt es nur im Auftragslauf; im Modus pruefen fehlt es in der Sandbox (der Verweis claude in .local/bin zeigt dann
# ins Leere)
if [ "$MODUS" = lauf ]; then
  ARGS+=(--ro-bind "$HEIM/.local/share/claude" "$HEIM/.local/share/claude")
fi
ARGS+=(
  --ro-bind-try "$HEIM/.local/share/uv/tools" "$HEIM/.local/share/uv/tools"
  --ro-bind-try "$HEIM/.local/share/uv/python" "$HEIM/.local/share/uv/python"
  --ro-bind "$HEIM/.fnm" "$HEIM/.fnm"
  --ro-bind "$HEIM/.npm-global" "$HEIM/.npm-global"
  --ro-bind-try "$HEIM/.gitconfig" "$HEIM/.gitconfig"
)
if [ "$MODUS" = lauf ]; then
  ARGS+=(--bind "$JOBDIR/claude" "$HEIM/.claude")
fi
# Caches und Plugin-Daten je Auftrag auf der Platte (kein gemeinsamer, vergiftbarer Cache zwischen
# Aufträgen). Plugins: nicht die von wamo, sondern die root-eigene Kopie des ECC-Plugins ohne Hooks
# (wamo-job-ecc.py); Skills, Agenten und Befehle heißen gleich, Hooks laufen keine. Im Modus pruefen keine Plugins.
for teil in npm cache plugin-daten; do
  [ ! -L "$JOBDIR/$teil" ] || fehler "$teil ist ein Link"
  mkdir -p "$JOBDIR/$teil"
done
ARGS+=(
  --bind "$JOBDIR/npm" "$HEIM/.npm"
  --bind "$JOBDIR/cache" "$HEIM/.cache"
)
if [ "$MODUS" = lauf ]; then
  ARGS+=(
    --ro-bind "$PLUGINS" "$HEIM/.claude/plugins"
    --bind "$JOBDIR/plugin-daten" "$HEIM/.claude/plugins/data"
  )
fi

# Sicherheitsrelevantes zuletzt: spätere Mounts überdecken alles, was frühere umgelenkt haben könnten.
# projekte ist ein eigenes tmpfs (Mountpunkt, nicht umbenennbar) und danach schreibgeschützt; Wächter,
# Managed-Settings und /run/wamo liegen auf der Wurzel, die zum Schluss schreibgeschützt wird.
ARGS+=(
  --perms 0755 --size 1048576 --tmpfs "$PROJEKTE"
  --bind "$PROJDIR" "$PROJEKTE/$PROJEKT"
  --remount-ro "$PROJEKTE"
  --ro-bind "$APP/waechter" "$WAECHTER_INNEN"
  --ro-bind "$APP/claude-code" /etc/claude-code
)
# Der Zustand des Auftrags (Freigaben, Anfragen, Socket) gehört nur zum Auftragslauf; im Modus pruefen gibt es ihn nicht
if [ "$MODUS" = lauf ]; then
  ARGS+=(
    --bind "$JOBDIR/zustand" /run/wamo/zustand
    --ro-bind "$JOBDIR/zustand/approvals.json" /run/wamo/zustand/approvals.json
  )
fi
ARGS+=(
  --symlink "$PROJEKTE/$PROJEKT" /run/wamo/projekt
  --remount-ro /
  --chdir "$PROJEKTE/$PROJEKT"
)
# Umgebung in der Sandbox. Auftragslauf: wie bisher. Modus pruefen: nur das Nötige, nichts von Claude, kein GH_*, kein Token
SETENV_LAUF=(
  --setenv HOME "$HEIM" --setenv USER wamo --setenv LOGNAME wamo --setenv SHELL /bin/bash
  --setenv PATH "$HEIM/.local/bin:$HEIM/.npm-global/bin:$HEIM/.fnm/aliases/default/bin:/usr/local/bin:/usr/bin:/bin"
  --setenv TMPDIR /tmp --setenv LANG C.UTF-8 --setenv TZ Europe/Berlin --setenv TERM dumb
  --setenv CLAUDE_CONFIG_DIR "$HEIM/.claude" --setenv DISABLE_AUTOUPDATER 1
  --setenv GIT_TERMINAL_PROMPT 0 --setenv GH_PROMPT_DISABLED 1 --setenv NO_UPDATE_NOTIFIER 1
  --setenv npm_config_update_notifier false
  --unsetenv XDG_RUNTIME_DIR
)
SETENV_PRUEFEN=(
  --setenv HOME "$HEIM" --setenv USER wamo --setenv LOGNAME wamo --setenv SHELL /bin/bash
  --setenv PATH "$HEIM/.local/bin:$HEIM/.npm-global/bin:$HEIM/.fnm/aliases/default/bin:/usr/local/bin:/usr/bin:/bin"
  --setenv TMPDIR /tmp --setenv LANG C.UTF-8 --setenv TZ Europe/Berlin --setenv TERM dumb
  --setenv CI 1 --setenv NO_COLOR 1 --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PYTHONIOENCODING utf-8
  --setenv GIT_TERMINAL_PROMPT 0 --setenv NO_UPDATE_NOTIFIER 1 --setenv npm_config_update_notifier false
  --unsetenv XDG_RUNTIME_DIR
)
if [ "$MODUS" = lauf ]; then
  ARGS+=("${SETENV_LAUF[@]}")
else
  ARGS+=("${SETENV_PRUEFEN[@]}")
fi

INNEN=()
if [ "$ABSCHOTTUNG" = an ]; then
  INNEN=(/usr/bin/python3 -I "$WAECHTER_INNEN/abschottung.py" --sperrports "$SPERRPORTS" --)
fi

EINHEIT="wamo-job-$JOB-$ZUSATZ-$(date +%s%N)"
# --as=unlimited: der Dispatcher begrenzt seinen eigenen Adressraum (weiche Grenze); Claude Code braucht mehr
exec /usr/bin/systemd-run --user --scope --quiet --collect --unit="$EINHEIT" \
  -p MemoryMax="$SPEICHER_MAX" -p MemorySwapMax=0 -p TasksMax="$PROZESSE_MAX" \
  -p CPUQuota="$CPU_QUOTA" -p CPUWeight=50 -p IOWeight=50 -p RuntimeMaxSec=$((ZEIT + 60)) \
  /usr/bin/prlimit --nofile="$OFFENE_DATEIEN" --core=0 --fsize=$((DATEI_MAX_MB * 1024 * 1024)) --as=unlimited \
  /usr/bin/timeout --kill-after=15 "$ZEIT" \
  /usr/bin/nice -n 10 "$BWRAP" "${ARGS[@]}" "${INNEN[@]}" "$@" 9<"$SECCOMP"
