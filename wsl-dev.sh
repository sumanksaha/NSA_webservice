#!/bin/bash
# ============================================================================
# WSL Dev Wrapper — run any command inside WSL Ubuntu with clean PATH
# ============================================================================
# Usage from Windows (PowerShell/CMD):
#   wsl -d Ubuntu -- bash wsl-dev.sh <command> [args...]
#
# Examples:
#   wsl -d Ubuntu -- bash wsl-dev.sh python app.py
#   wsl -d Ubuntu -- bash wsl-dev.sh flask db upgrade
#   wsl -d Ubuntu -- bash wsl-dev.sh pytest tests/ -v
#   wsl -d Ubuntu -- bash wsl-dev.sh npm run build
#
# From within WSL, just run commands directly:
#   cd ~/NSA_webservice
#   python app.py
# ============================================================================

# Set clean Linux PATH (no Windows PATH pollution)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.local/bin"

# Navigate to project
cd "$HOME/NSA_webservice" 2>/dev/null || cd "/mnt/c/github/NSA_webservice"

# Activate venv if it exists
if [ -f "./venv/bin/activate" ]; then
    source ./venv/bin/activate
fi

# Run the command
echo "[wsl-dev] $*"
exec "$@"
