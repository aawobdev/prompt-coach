#!/bin/bash
# ============================================================================
# prompt-coach installer
# ============================================================================
# Installs prompt-coach as a real command on PATH via `uv tool install`,
# which already does everything a manual venv+symlink script would (this is
# simpler than Hermes's own setup-hermes.sh because uv's tool-install
# mechanism handles the PATH wiring itself -- see DECISIONS.md 2026-07-30).
#
# Usage:
#   ./install.sh
# ============================================================================

set -e

# No colors when stdout isn't a terminal (piped/redirected/logged) --
# matches the rest of the codebase's TTY-awareness (cli.py's
# Console(force_terminal=False, ...), sync_with_progress's is_terminal
# check). Without this, raw escape bytes end up in logs/captures.
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    YELLOW=''
    CYAN=''
    RED=''
    NC=''
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}prompt-coach installer${NC}"
echo ""

if ! command -v uv >/dev/null 2>&1; then
    echo -e "${RED}uv is not installed.${NC}"
    echo "Install it first: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "Installing prompt-coach as a uv tool..."
uv tool install --force .

if command -v prompt-coach >/dev/null 2>&1; then
    echo -e "${GREEN}prompt-coach is on PATH: $(command -v prompt-coach)${NC}"
else
    echo -e "${YELLOW}prompt-coach was installed but isn't on PATH yet.${NC}"
    echo "Run 'uv tool update-shell' (or restart your shell), then re-run this script."
    exit 0
fi

echo ""
read -r -p "Run the setup wizard now? [Y/n] " run_setup
run_setup=${run_setup:-Y}
if [[ "$run_setup" =~ ^[Yy] ]]; then
    exec prompt-coach setup
else
    echo "Run 'prompt-coach setup' whenever you're ready."
fi
