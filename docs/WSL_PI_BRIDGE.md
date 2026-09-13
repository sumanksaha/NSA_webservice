# WSL Ubuntu Dev Stack

## Overview

Development for NSA Webservice runs inside **WSL2 Ubuntu 26.04 (Resolute)** with
a full Linux toolchain. The Windows host only provides editors (VS Code/Cursor)
via the **Remote-WSL** extension.

## Architecture

```
┌──────────────────────────────────────────────┐
│  Windows Host                                │
│  ┌────────────┐ ┌──────────┐ ┌────────────┐  │
│  │ VS Code    │ │ Cursor   │ │ pi-node    │  │
│  │ (editor)   │ │ (editor) │ │ (Codebuff) │  │
│  └─────┬──────┘ └────┬─────┘ └────────────┘  │
│        │ Remote-WSL   │                       │
├────────┼──────────────┼───────────────────────┤
│        ▼              ▼                       │
│  ┌──────────────────────────────────────┐     │
│  │  WSL2 Ubuntu 26.04                   │     │
│  │  ┌──────────┐ ┌──────────┐           │     │
│  │  │ Python   │ │ Node.js  │           │     │
│  │  │ 3.14     │ │ 22 LTS   │           │     │
│  │  └──────────┘ └──────────┘           │     │
│  │  ┌──────────┐ ┌──────────┐           │     │
│  │  │PostgreSQL│ │ Redis    │           │     │
│  │  │ 18       │ │ 7.x     │           │     │
│  │  └──────────┘ └──────────┘           │     │
│  │  ┌──────────┐ ┌──────────┐           │     │
│  │  │ WeasyPrint│ │ Git     │           │     │
│  │  │ (PDF)    │ │ 2.53    │           │     │
│  │  └──────────┘ └──────────┘           │     │
│  │  Project: ~/NSA_webservice           │     │
│  └──────────────────────────────────────┘     │
└──────────────────────────────────────────────┘
```

## Path Mapping

| From | To | Notes |
|------|----|-------|
| Windows → WSL | `C:\github\NSA_webservice` → `/mnt/c/github/NSA_webservice` | Slow I/O (cross-filesystem) |
| WSL native | `~/NSA_webservice` → `/home/suman_saha/NSA_webservice` | **Fast I/O — use this** |
| WSL → Windows | `/home/suman_saha/...` → `\\wsl$\Ubuntu\home\suman_saha\...` | For editor access |

## WSL Configuration (`/etc/wsl.conf`)

```ini
[boot]
systemd=true

[automount]
enabled=true
options="metadata,umask=22,fmask=11"

[network]
generateResolvConf=true

[interop]
enabled=true
appendWindowsPath=false          # ← Key: stops Windows PATH pollution

[user]
default=suman_saha
```

## Installed Dev Stack (WSL)

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.14.4 | App runtime, RAG pipeline |
| Node.js | 22 LTS | Build tools, npm packages |
| PostgreSQL | 18 | Primary database |
| Redis | 7.x | QStash task-status store |
| Git | 2.53.0 | Version control |
| WeasyPrint | system | PDF generation |
| build-essential | — | C compiler, headers |

## Setup Script

Run from a WSL terminal:

```bash
bash /mnt/c/github/NSA_webservice/scripts/wsl_dev_install.sh
```

Or after restarting WSL (if .wsl.conf was updated):

```bash
wsl --shutdown  # from PowerShell
# Then open a new WSL terminal and run the script
```

## Quick Start (from WSL terminal)

```bash
# First time only: install dev stack
bash /mnt/c/github/NSA_webservice/scripts/wsl_dev_install.sh

# Copy project to native WSL filesystem (one-time)
cp -r /mnt/c/github/NSA_webservice/* ~/NSA_webservice/
cp /mnt/c/github/NSA_webservice/.env ~/NSA_webservice/
cp -r /mnt/c/github/NSA_webservice/.git ~/NSA_webservice/
cd ~/NSA_webservice

# Create Python venv + install deps
python3 -m venv venv
source venv/bin/activate
pip install -e '.[dev]'

# Setup database
flask db upgrade

# Start the app
python app.py
# → http://localhost:8000
```

## Opening the Project in VS Code / Cursor

From the WSL terminal:

```bash
cd ~/NSA_webservice
code .       # VS Code (with WSL Remote extension)
cursor .     # Cursor (with WSL Remote extension)
```

Both editors connect to the WSL filesystem natively — no slow `/mnt/c/` I/O.

## Services

```bash
# Check status
sudo systemctl status postgresql
sudo systemctl status redis-server

# Start/stop/restart
sudo systemctl restart postgresql
sudo systemctl restart redis-server

# Connect to PostgreSQL
psql -U suman_saha -d nsa_db
```

## .bashrc Environment

Added to `~/.bashrc`:

```bash
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
```

## Windows Cleanup

After confirming everything works in WSL, see `docs/WSL_CLEANUP_GUIDE.md`
for the list of Windows tools that can be safely uninstalled (saves ~2.7 GB
disk + ~300 MB RAM).
