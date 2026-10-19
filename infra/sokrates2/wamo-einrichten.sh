#!/usr/bin/env bash
# WAMO auf Sokrates-2 einrichten: eigener Systembenutzer, Werkzeuge fuer Entwicklungsauftraege
# (Claude Code, gh, Vercel- und Supabase-CLI), ECC-Plugin und REA-MCP-Server.
#
# Ausfuehren auf Sokrates-2 als wamocon:   sudo bash wamo-einrichten.sh
# Wiederholbar (idempotent). Richtet KEINE Geheimnisse ein, die kommen von Hand nach
# /etc/wamo/geheim.env (siehe README.md im selben Ordner).
#
# Bewusst NICHT: Benutzer wamo bekommt weder sudo noch die docker-Gruppe (docker = root-Rechte).
# Der System-Node (v18) bleibt unveraendert; wamo nutzt ein eigenes Node 22 im Home.
set -euo pipefail

WAMO_USER="${WAMO_USER:-wamo}"
WAMO_HOME="${WAMO_HOME:-/srv/wamo}"
NODE_MAJOR="${NODE_MAJOR:-22}"
REA_VERSION="${REA_VERSION:-6.5.0}"
ECC_MARKETPLACE="${ECC_MARKETPLACE:-https://github.com/affaan-m/ECC.git}"
GIT_NAME="${WAMO_GIT_NAME:-WAMO (WAMOCON)}"
GIT_EMAIL="${WAMO_GIT_EMAIL:-wamo@wamocon.com}"

if [ "$(id -u)" -ne 0 ]; then echo "Bitte mit sudo ausfuehren." >&2; exit 1; fi
if [ "$(dpkg --print-architecture)" != "arm64" ]; then echo "Hinweis: Architektur ist nicht arm64." >&2; fi

echo "== 1. Systembenutzer und Verzeichnisse"
if ! id "$WAMO_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$WAMO_HOME" --shell /bin/bash "$WAMO_USER"
fi
install -d -o "$WAMO_USER" -g "$WAMO_USER" -m 750 "$WAMO_HOME" "$WAMO_HOME/app" "$WAMO_HOME/projekte" "$WAMO_HOME/jobs" "$WAMO_HOME/logs"
install -d -o root -g "$WAMO_USER" -m 750 /etc/wamo
if [ ! -f /etc/wamo/geheim.env ]; then
  install -o root -g "$WAMO_USER" -m 640 /dev/null /etc/wamo/geheim.env
  cat > /etc/wamo/geheim.env <<'ENV'
# WAMO-Geheimnisse auf Sokrates-2 (Rechte 640 root:wamo). Werte von Hand eintragen, nie committen.
# Claude Code: entweder API-Schluessel ODER Token aus "claude setup-token" (Abo)
ANTHROPIC_API_KEY=
CLAUDE_CODE_OAUTH_TOKEN=
# GitHub: Token der GitHub App WAMO oder fein granularer Token eines Bot-Kontos
GH_TOKEN=
# Vercel: Team-Token mit Ablaufdatum
VERCEL_TOKEN=
VERCEL_ORG_ID=
# Supabase Cloud: persoenlicher Zugriffstoken (Management-API, CLI, MCP)
SUPABASE_ACCESS_TOKEN=
# Sprachdienste und Gateway
SONIOX_API_KEY=
LITELLM_API_KEY=
ENV
fi

echo "== 2. Systempakete (git, curl, jq, bubblewrap, gh, supabase)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl jq ca-certificates gnupg bubblewrap >/dev/null
if ! command -v gh >/dev/null 2>&1; then
  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq && apt-get install -y -qq gh >/dev/null
fi
if ! command -v supabase >/dev/null 2>&1; then
  # Supabase-CLI als .deb aus dem offiziellen Release (global per npm wird nicht unterstuetzt)
  deb_url="$(curl -fsSL https://api.github.com/repos/supabase/cli/releases/latest \
    | jq -r '.assets[] | select(.name | test("linux_arm64\\.deb$")) | .browser_download_url' | head -n1)"
  if [ -n "$deb_url" ]; then
    tmp="$(mktemp --suffix=.deb)"; curl -fsSL "$deb_url" -o "$tmp"; apt-get install -y -qq "$tmp" >/dev/null; rm -f "$tmp"
  else
    echo "Supabase-CLI: kein arm64-Paket gefunden, bitte von Hand pruefen." >&2
  fi
fi

echo "== 3. Werkzeuge im Home von $WAMO_USER (uv, Node $NODE_MAJOR, Vercel-CLI, Claude Code, ECC, REA)"
sudo -u "$WAMO_USER" -H \
  NODE_MAJOR="$NODE_MAJOR" REA_VERSION="$REA_VERSION" ECC_MARKETPLACE="$ECC_MARKETPLACE" \
  GIT_NAME="$GIT_NAME" GIT_EMAIL="$GIT_EMAIL" bash -s <<'EOS'
set -euo pipefail
cd "$HOME"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh

if [ ! -x "$HOME/.fnm/fnm" ]; then
  curl -fsSL https://fnm.vercel.app/install | bash -s -- --install-dir "$HOME/.fnm" --skip-shell
fi
eval "$("$HOME/.fnm/fnm" env)"
"$HOME/.fnm/fnm" install "$NODE_MAJOR" >/dev/null
"$HOME/.fnm/fnm" default "$NODE_MAJOR"
npm config set prefix "$HOME/.npm-global"
command -v vercel >/dev/null 2>&1 || npm install -g vercel >/dev/null

command -v claude >/dev/null 2>&1 || curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"

# ECC-Plugin (Skills, Agenten, Befehle, Hooks) und REA-MCP-Server fuer alle Claude-Code-Laeufe von wamo
claude plugin marketplace add "$ECC_MARKETPLACE" || echo "Hinweis: ECC-Marktplatz bitte nach der Anmeldung erneut hinzufuegen."
claude plugin install ecc@ecc || echo "Hinweis: ECC-Plugin bitte nach der Anmeldung erneut installieren."
claude mcp get rea >/dev/null 2>&1 || claude mcp add --scope user rea -- npx -y "rea-agents@${REA_VERSION}" mcp

# Umgebung in eigener Datei, eingebunden ganz oben in .bashrc (vor dem Abbruch fuer nicht-interaktive
# Shells) und in .profile; so sehen auch Dienste und "bash -c" Node 22, Vercel und Claude Code.
cat > "$HOME/.wamo-umgebung" <<'RC'
# WAMO-Umgebung fuer alle Shells und Dienste des Benutzers wamo (auch nicht-interaktiv)
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
[ -x "$HOME/.fnm/fnm" ] && eval "$("$HOME/.fnm/fnm" env --shell bash)"
set -a; [ -r /etc/wamo/geheim.env ] && . /etc/wamo/geheim.env; set +a
RC
grep -q 'wamo-umgebung' "$HOME/.bashrc" || sed -i '1i [ -r "$HOME/.wamo-umgebung" ] \&\& . "$HOME/.wamo-umgebung"  # wamo-umgebung' "$HOME/.bashrc"
grep -q 'wamo-umgebung' "$HOME/.profile" || echo '[ -r "$HOME/.wamo-umgebung" ] && . "$HOME/.wamo-umgebung"  # wamo-umgebung' >> "$HOME/.profile"
git config --global user.name "$GIT_NAME"
git config --global user.email "$GIT_EMAIL"
git config --global init.defaultBranch main
EOS

echo "== 4. Pruefung"
sudo -u "$WAMO_USER" -H bash -c 'cd "$HOME" && . "$HOME/.wamo-umgebung" && for t in uv node npm vercel claude gh supabase git; do printf "%-9s " "$t"; command -v "$t" || echo "FEHLT"; done; echo "node $(node --version)"; claude mcp list 2>/dev/null | grep -i rea || echo "REA: bitte pruefen"; claude plugin list 2>/dev/null | grep -i ecc || echo "ECC: bitte pruefen"'
echo "Fertig. Naechster Schritt: Geheimnisse in /etc/wamo/geheim.env eintragen (siehe README.md)."
