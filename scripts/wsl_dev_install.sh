#!/bin/bash
# ============================================================================
# NSA Webservice — WSL Ubuntu Dev Stack Setup
# ============================================================================
# Run this inside WSL Ubuntu terminal:
#   bash /mnt/c/github/NSA_webservice/scripts/wsl_dev_install.sh
#
# What it does:
#   1. Configures .wsl.conf (stops Windows PATH pollution, enables systemd)
#   2. Installs Node.js 22 LTS, PostgreSQL 18, Redis, Python dev deps
#   3. Sets up clean Linux PATH in .bashrc
#   4. Creates PostgreSQL user + database
#   5. Sets up Python venv + installs project dependencies
# ============================================================================
set -e

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

step()  { echo -e "\n${CYAN}=== Step $1/$TOTAL_STEPS: $2 ===${NC}"; }
ok()    { echo -e "  ${GREEN}✓ $1${NC}"; }
warn()  { echo -e "  ${YELLOW}⚠ $1${NC}"; }
fail()  { echo -e "  ${RED}✗ $1${NC}"; exit 1; }

TOTAL_STEPS=7
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIN_PROJECT="/mnt/c/github/NSA_webservice"
WSL_PROJECT="$HOME/NSA_webservice"

# ============================================================================
step 1 "Configuring .wsl.conf"
# ============================================================================
if [ -f /etc/wsl.conf ]; then
    if grep -q "appendWindowsPath=false" /etc/wsl.conf 2>/dev/null; then
        ok ".wsl.conf already has appendWindowsPath=false"
    else
        warn ".wsl.conf needs updating (appendWindowsPath=false)"
        echo "  The following will be written to /etc/wsl.conf:"
        echo "  ┌──────────────────────────────────────────────┐"
        echo "  │ [boot] systemd=true                          │"
        echo "  │ [automount] enabled=true, metadata           │"
        echo "  │ [network] generateResolvConf=true             │"
        echo "  │ [interop] appendWindowsPath=false             │"
        echo "  │ [user] default=suman_saha                     │"
        echo "  └──────────────────────────────────────────────┘"
        echo ""
        sudo tee /etc/wsl.conf > /dev/null << 'WSLEOF'
[boot]
systemd=true

[automount]
enabled=true
options="metadata,umask=22,fmask=11"

[network]
generateResolvConf=true

[interop]
enabled=true
appendWindowsPath=false

[user]
default=suman_saha
WSLEOF
        ok ".wsl.conf written"
        echo ""
        echo -e "${YELLOW}⚠ IMPORTANT: WSL must be restarted for this to take effect.${NC}"
        echo "  Run this from Windows PowerShell:  wsl --shutdown"
        echo "  Then re-open your WSL terminal."
        echo ""
        read -p "  Press Enter to continue (or Ctrl+C to stop and restart WSL first)... "
    fi
else
    sudo tee /etc/wsl.conf > /dev/null << 'WSLEOF'
[boot]
systemd=true

[automount]
enabled=true
options="metadata,umask=22,fmask=11"

[network]
generateResolvConf=true

[interop]
enabled=true
appendWindowsPath=false

[user]
default=suman_saha
WSLEOF
    ok ".wsl.conf created"
fi

# ============================================================================
step 2 "Installing core build dependencies"
# ============================================================================
sudo apt-get update -qq
sudo apt-get install -y -qq \
    curl wget gnupg2 ca-certificates lsb-release software-properties-common \
    build-essential pkg-config \
    libffi-dev libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
    libsqlite3-dev liblzma-dev libncurses-dev libxml2-dev libxmlsec1-dev \
    libjpeg-dev libpq-dev > /dev/null 2>&1
ok "Build dependencies installed"

# ============================================================================
step 3 "Installing Node.js 22 LTS"
# ============================================================================
if command -v node &>/dev/null; then
    ok "Node.js already installed: $(node --version)"
else
    echo "  Adding NodeSource repository..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - > /dev/null 2>&1
    sudo apt-get install -y -qq nodejs > /dev/null 2>&1
    ok "Node.js installed: $(node --version), npm: $(npm --version)"
fi

# ============================================================================
step 4 "Installing PostgreSQL 18"
# ============================================================================
if command -v psql &>/dev/null; then
    ok "PostgreSQL already installed: $(psql --version)"
else
    echo "  Adding PostgreSQL repository..."
    sudo sh -c "echo \"deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main\" > /etc/apt/sources.list.d/pgdg.list"
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg 2>/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq postgresql-18 postgresql-client-18 > /dev/null 2>&1
    ok "PostgreSQL installed: $(psql --version)"
fi

echo "  Ensuring PostgreSQL is running..."
sudo systemctl start postgresql 2>/dev/null || true
sudo systemctl enable postgresql 2>/dev/null || true
if sudo systemctl is-active --quiet postgresql; then
    ok "PostgreSQL is running"
else
    warn "PostgreSQL may need manual start: sudo systemctl start postgresql"
fi

# ============================================================================
step 5 "Installing Redis"
# ============================================================================
if command -v redis-server &>/dev/null; then
    ok "Redis already installed: $(redis-server --version | head -1)"
else
    sudo apt-get install -y -qq redis-server > /dev/null 2>&1
    ok "Redis installed: $(redis-server --version | head -1)"
fi

echo "  Ensuring Redis is running..."
sudo systemctl start redis-server 2>/dev/null || true
sudo systemctl enable redis-server 2>/dev/null || true
if sudo systemctl is-active --quiet redis-server; then
    ok "Redis is running"
else
    warn "Redis may need manual start: sudo systemctl start redis-server"
fi

# ============================================================================
step 6 "Setting up Python environment"
# ============================================================================
sudo apt-get install -y -qq python3 python3-pip python3-venv python3-dev > /dev/null 2>&1
ok "Python: $(python3 --version)"

# WeasyPrint + PDF dependencies
sudo apt-get install -y -qq \
    weasyprint \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    shared-mime-info fonts-dejavu fonts-liberation > /dev/null 2>&1
ok "WeasyPrint + PDF dependencies installed"

# ============================================================================
step 7 "Setting up project directory"
# ============================================================================
# Create clean PATH in .bashrc
if ! grep -q "WSL Dev Environment" ~/.bashrc 2>/dev/null; then
    cat >> ~/.bashrc << 'BASHRC_EOF'

# === WSL Dev Environment ===
# Clean Linux PATH (no Windows PATH pollution)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.local/bin"

# PostgreSQL
export PGHOST=localhost
export PGUSER=suman_saha

# Python
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

# Node
export NODE_OPTIONS="--max-old-space-size=4096"

# RAG torch threads (prevent CPU pegging)
export RAG_TORCH_THREADS=4
BASHRC_EOF
    ok "Added clean PATH + env vars to .bashrc"
else
    ok ".bashrc already configured"
fi

# Create project directory
mkdir -p "$WSL_PROJECT"
ok "Project directory: $WSL_PROJECT"

# Setup PostgreSQL user + database
echo "  Setting up PostgreSQL user and database..."
sudo -u postgres createuser -s suman_saha 2>/dev/null && ok "Created PostgreSQL user: suman_saha" || ok "PostgreSQL user suman_saha already exists"
sudo -u postgres createdb nsa_db -O suman_saha 2>/dev/null && ok "Created PostgreSQL database: nsa_db" || ok "PostgreSQL database nsa_db already exists"

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  WSL Dev Stack Setup Complete!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  Services:"
echo "    PostgreSQL: localhost:5432  (user: suman_saha, db: nsa_db)"
echo "    Redis:      localhost:6379"
echo ""
echo "  ⚠ If .wsl.conf was updated, restart WSL first:"
echo "    From Windows PowerShell:  wsl --shutdown"
echo "    Then re-open WSL terminal."
echo ""
echo "  Next steps to set up the project:"
echo "    1. cd ~/NSA_webservice"
echo "    2. Copy project files from Windows:"
echo "       cp -r /mnt/c/github/NSA_webservice/* ."
echo "       cp /mnt/c/github/NSA_webservice/.env ."
echo "       cp /mnt/c/github/NSA_webservice/.git ."
echo "    3. python3 -m venv venv"
echo "    4. source venv/bin/activate"
echo "    5. pip install -e '.[dev]'"
echo "    6. flask db upgrade"
echo "    7. python app.py"
echo ""
echo "  Or clone fresh from Git:"
echo "    cd ~ && git clone <repo-url> NSA_webservice"
echo "    cd NSA_webservice"
echo "    cp /mnt/c/github/NSA_webservice/.env ."
echo "    python3 -m venv venv && source venv/bin/activate"
echo "    pip install -e '.[dev]'"
echo "    flask db upgrade"
echo "    python app.py"
