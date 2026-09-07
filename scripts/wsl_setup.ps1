# WSL Ubuntu Setup Script
# Run from Windows PowerShell: .\scripts\wsl_setup.ps1
# This writes .wsl.conf, restarts WSL, and installs the full dev stack.

$ErrorActionPreference = "Stop"

Write-Host "=== Step 1: Writing .wsl.conf ===" -ForegroundColor Cyan

$wslConf = @"
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
"@

Set-Content -Path "\\wsl.localhost\Ubuntu\etc\wsl.conf" -Value $wslConf -Force
Write-Host "  .wsl.conf written successfully" -ForegroundColor Green

Write-Host ""
Write-Host "=== Step 2: Restarting WSL (appendWindowsPath=false will take effect) ===" -ForegroundColor Cyan
wsl --shutdown
Start-Sleep -Seconds 3
Write-Host "  WSL shut down. It will restart on next wsl command." -ForegroundColor Green

Write-Host ""
Write-Host "=== Step 3: Installing dev stack inside WSL ===" -ForegroundColor Cyan
Write-Host "  This will install: Node.js 22 LTS, PostgreSQL 18, Redis, and dev dependencies."
Write-Host "  You will be prompted for your WSL sudo password once."

$installScript = @'
#!/bin/bash
set -e
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

echo "--- Updating package lists ---"
sudo apt-get update -qq

echo "--- Installing core utilities + dev dependencies ---"
sudo apt-get install -y -qq \
  curl wget gnupg2 ca-certificates lsb-release software-properties-common \
  build-essential pkg-config \
  libffi-dev libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
  libsqlite3-dev liblzma-dev libncurses-dev libxml2-dev libxmlsec1-dev \
  libjpeg-dev zlib1g-dev libpq-dev

echo "--- Installing Node.js 22 LTS ---"
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y -qq nodejs
echo "Node: $(node --version)  npm: $(npm --version)"

echo "--- Installing PostgreSQL 18 ---"
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg
sudo apt-get update -qq
sudo apt-get install -y -qq postgresql-18 postgresql-client-18
echo "PostgreSQL installed. Starting..."
sudo systemctl start postgresql
sudo systemctl enable postgresql

echo "--- Installing Redis ---"
sudo apt-get install -y -qq redis-server
echo "Redis installed. Starting..."
sudo systemctl start redis-server
sudo systemctl enable redis-server

echo "--- Installing Python 3.12+ (if not already present) ---"
sudo apt-get install -y -qq python3 python3-pip python3-venv python3-dev
echo "Python: $(python3 --version)"

echo "--- Installing Git (if not already present) ---"
sudo apt-get install -y -qq git

echo "--- Installing WeasyPrint dependencies ---"
sudo apt-get install -y -qq \
  weasyprint \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
  shared-mime-info fonts-dejavu fonts-liberation

echo ""
echo "=== Dev stack installed ==="
echo "  Python:  $(python3 --version)"
echo "  Node:    $(node --version)"
echo "  npm:     $(npm --version)"
echo "  Git:     $(git --version)"
echo "  pg:      $(psql --version)"
echo "  Redis:   $(redis-server --version)"
echo ""
echo "--- Setting up PostgreSQL user and database ---"
sudo -u postgres createuser -s suman_saha 2>/dev/null || echo "  User suman_saha already exists in PostgreSQL"
sudo -u postgres createdb nsa_db -O suman_saha 2>/dev/null || echo "  Database nsa_db already exists"
echo ""
echo "--- Verifying services ---"
sudo systemctl is-active postgresql && echo "  PostgreSQL: running" || echo "  PostgreSQL: NOT running"
sudo systemctl is-active redis-server && echo "  Redis: running" || echo "  Redis: NOT running"
'@

# Write install script to WSL temp, then execute it
$scriptPath = "//wsl.localhost/Ubuntu/home/suman_saha/wsl_dev_install.sh"
Set-Content -Path $scriptPath -Value $installScript -Encoding UTF8 -NoNewline

Write-Host "  Running install script inside WSL (enter sudo password when prompted)..."
wsl -d Ubuntu -- bash /home/suman_saha/wsl_dev_install.sh

Write-Host ""
Write-Host "=== Step 4: Setting up WSL user environment ===" -ForegroundColor Cyan

$bashrcAppend = @'

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

# RAG torch threads
export RAG_TORCH_THREADS=4
'@

$bashrcPath = "//wsl.localhost/Ubuntu/home/suman_saha/.bashrc_wsl_env"
Set-Content -Path $bashrcPath -Value $bashrcAppend -Encoding UTF8 -NoNewline

# Source it from .bashrc
wsl -d Ubuntu -- bash -c '
  if ! grep -q "WSL Dev Environment" ~/.bashrc 2>/dev/null; then
    echo "" >> ~/.bashrc
    echo "# Source WSL dev environment" >> ~/.bashrc
    echo "[ -f ~/.bashrc_wsl_env ] && source ~/.bashrc_wsl_env" >> ~/.bashrc
    echo "  Added WSL dev environment to .bashrc"
  else
    echo "  WSL dev environment already in .bashrc"
  fi
'

Write-Host ""
Write-Host "=== Step 5: Creating project directory ===" -ForegroundColor Cyan
wsl -d Ubuntu -- bash -c '
  mkdir -p /home/suman_saha/NSA_webservice
  echo "  Created /home/suman_saha/NSA_webservice"
'

Write-Host ""
Write-Host "=== SETUP COMPLETE ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps (run inside WSL terminal):"
Write-Host "  1. cd ~/NSA_webservice"
Write-Host "  2. cp /mnt/c/github/NSA_webservice/.env .     # copy your env file"
Write-Host "  3. cp -r /mnt/c/github/NSA_webservice/* .      # copy project files"
Write-Host "     (or use git clone if the repo is on GitHub)"
Write-Host "  4. python3 -m venv venv && source venv/bin/activate"
Write-Host "  5. pip install -e '.[dev]'"
Write-Host "  6. python app.py"
Write-Host ""
Write-Host "Services running in WSL:"
Write-Host "  PostgreSQL: localhost:5432"
Write-Host "  Redis:      localhost:6379"
