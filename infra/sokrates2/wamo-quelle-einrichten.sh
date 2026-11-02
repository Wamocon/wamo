#!/usr/bin/env bash
# Lokale Quelle für den Release-Dienst einrichten (Schritt 1.3, Entscheidung 02.11.2026): ein Bare-Repository auf Sokrates-2, bis die zweite GitHub-App
# und der Netzweg zu GitHub da sind. Wiederholbar. Der Release-Dienst liest nur (Spiegel, quelle.py); abgelegt wird vom PC mit
# scripts/wamo_quelle_sokrates2.py, und zwar nur, was auf GitHub schon in main gemergt ist (der Mensch bleibt das Tor).
#
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-quelle-einrichten.sh"
#   ssh sokrates2 "sudo bash ~/wamo-infra/wamo-quelle-einrichten.sh --entfernen"    Rückweg: Repository und Konfiguration weg
#
# Was das Skript ändert:
#   /var/lib/wamo-quelle/wamo.git      bare, Besitzer wamocon (SSH-Ablage vom PC), für alle lesbar (der Code ist nicht geheim), nur refs/heads/main
#   /etc/wamo-release/quelle.env       root:wamo-release 0640, WAMO_QUELLE_URL=/var/lib/wamo-quelle/wamo.git
#   /etc/wamo-release/git-quelle.gitconfig   root 0644, safe.directory für genau diesen Pfad (git verweigert sonst das fremde Repository)
# Der Release-Dienst führt nie Code aus dem Repository aus (Konzept D.2); Hooks gibt es nur den einen unten, und der gehört root.
set -euo pipefail

BASIS=/var/lib/wamo-quelle
REPO="$BASIS/wamo.git"
ENV_DATEI=/etc/wamo-release/quelle.env
URL="$REPO"

[ "$(id -u)" = 0 ] || { echo "Bitte als root ausführen (sudo)." >&2; exit 1; }

if [ "${1:-}" = --entfernen ]; then
  rm -rf -- "$BASIS"
  rm -f -- "$ENV_DATEI" /etc/wamo-release/git-quelle.gitconfig
  echo "Lokale Quelle entfernt."
  exit 0
fi
[ -z "${1:-}" ] || { echo "Aufruf: $0 [--entfernen]" >&2; exit 2; }

echo "== 1. Repository $REPO"
install -d -o root -g root -m 0755 "$BASIS"
if [ ! -f "$REPO/HEAD" ]; then
  git init --bare --quiet --initial-branch=main "$REPO"
fi
git --git-dir "$REPO" config receive.denyNonFastForwards true
git --git-dir "$REPO" config receive.denyDeletes true
git --git-dir "$REPO" config receive.fsckObjects true
git --git-dir "$REPO" config transfer.fsckObjects true
git --git-dir "$REPO" config core.sharedRepository false
chown -R wamocon:wamocon "$REPO"
# Der Hook gehört root und lässt nur refs/heads/main zu (kein Tag, kein anderer Zweig, kein Löschen)
install -d -o root -g root -m 0755 "$REPO/hooks"
rm -f "$REPO"/hooks/*.sample
cat > "$REPO/hooks/update" <<'HOOK'
#!/bin/sh
# update <ref> <alt> <neu>: nur refs/heads/main, nie löschen
[ "$1" = refs/heads/main ] || { echo "WAMO-Quelle: nur refs/heads/main wird angenommen" >&2; exit 1; }
[ "$3" != 0000000000000000000000000000000000000000 ] || { echo "WAMO-Quelle: main wird nie gelöscht" >&2; exit 1; }
exit 0
HOOK
chown root:root "$REPO/hooks/update"
chmod 0755 "$REPO/hooks/update"
stat -c '  %n %U:%G %a' "$REPO" "$REPO/hooks/update"

echo "== 2. Konfiguration des Release-Dienstes"
install -d -o root -g root -m 0755 /etc/wamo-release
printf '# Lokale Quelle (wamo-quelle-einrichten.sh). Mit der zweiten GitHub-App steht hier stattdessen die https-Adresse.\nWAMO_QUELLE_URL=%s\n' "$URL" > "$ENV_DATEI"
chown root:wamo-release "$ENV_DATEI" 2>/dev/null || chown root:root "$ENV_DATEI"
chmod 0640 "$ENV_DATEI"
# git verweigert fremde Repositories ("dubious ownership"); die globale Datei des Spiegels gibt genau diesen einen Pfad frei
printf '[safe]
	directory = %s
' "$REPO" > /etc/wamo-release/git-quelle.gitconfig
chown root:root /etc/wamo-release/git-quelle.gitconfig
chmod 0644 /etc/wamo-release/git-quelle.gitconfig
stat -c '  %n %U:%G %a' "$ENV_DATEI" /etc/wamo-release/git-quelle.gitconfig
echo "Fertig. Ablegen vom PC: python scripts/wamo_quelle_sokrates2.py ablegen"
