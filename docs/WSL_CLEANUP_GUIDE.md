# WSL Migration — Windows Cleanup Guide

> After switching your dev stack to WSL Ubuntu, the following Windows tools are
> redundant and can be safely uninstalled to reclaim disk space and reduce
> background services.

---

## ✅ Safe to Uninstall (Redundant with WSL)

### 1. Docker Desktop (~2 GB disk + background services)

**Why:** You're running PostgreSQL/Redis natively in WSL. No Docker containers needed for this project.

```powershell
winget uninstall Docker.DockerDesktop
# or: Apps & Features → Docker Desktop → Uninstall
```

After uninstalling:
- Remove `C:\Program Files\Docker\` if it remains
- Remove `C:\Users\Suman saha\AppData\Local\Docker\` (images/containers data)
- Remove `C:\Users\Suman saha\.docker\` if present
- **Frees ~2 GB disk + stops Docker Desktop Service from running at startup**

### 2. Windows PostgreSQL 18 (~300 MB disk + service)

**Why:** PostgreSQL 18 is now running inside WSL. Having two instances is confusing and wastes ~200 MB RAM.

```powershell
# Stop the service first
Stop-Service postgresql-x64-18
# Then uninstall
winget uninstall PostgreSQL.PostgreSQL.18
# or: Apps & Features → PostgreSQL 18 → Uninstall
```

After uninstalling:
- Remove `C:\Program Files\PostgreSQL\` if it remains
- **Frees ~300 MB disk + stops postgresql-x64-18 service (saves ~200 MB RAM)**

### 3. Python via Microsoft Store (~200 MB)

**Why:** Python 3.14 is installed in WSL. Windows Python is only used by the Windows Store stub.

```powershell
winget uninstall Python.Python.3.13
# or: Settings → Apps → Installed apps → Python 3.13 → Uninstall
```

After uninstalling:
- Remove `C:\Users\Suman saha\AppData\Local\Packages\PythonSoftwareFoundation.*` if it remains
- **Frees ~200 MB disk**

### 4. hermes-agent Python venv (~200 MB)

**Why:** If hermes is only used on Windows and you're moving to WSL, its bundled Python is redundant. **Only uninstall if you no longer use hermes on Windows.**

```powershell
# Remove the hermes venv (hermes will recreate if needed)
Remove-Item -Recurse "C:\Users\Suman saha\AppData\Local\hermes\hermes-agent\venv" -ErrorAction SilentlyContinue
```

---

## ⚠️ Uninstall with Caution

### 5. Git for Windows (~500 MB)

**Why:** WSL has Git 2.53 installed. Windows Git is used by:
- VS Code / Cursor Git integration (they can use WSL Git via Remote-WSL)
- GitHub Desktop (if installed)
- Git Bash (you're in it right now)

**Recommendation:** Keep for now — VS Code/Cursor use it for the source control panel on the Windows side. If you only ever edit code via WSL (e.g., `code .` from WSL terminal), you can remove it later.

### 6. Rustup (~1 GB)

**Why:** The project has a Rust PyO3 normalizer (`rust/` directory). If you build it from WSL, the Windows Rust toolchain is redundant.

```powershell
rustup self uninstall
```

**Keep if:** You build Rust projects from Windows editors (VS Code/Cursor).

### 7. MiKTeX + TeXstudio (~500 MB)

**Why:** LaTeX compilation. Only needed if you create LaTeX documents from Windows. If you use `weasyprint` for all PDFs (which the project does), these are unnecessary.

**Recommendation:** Keep if you write LaTeX documents. Uninstall if purely for this project.

---

## ❌ Do NOT Uninstall

| Tool | Why |
|------|-----|
| **Node.js (pi-node)** | This is Codebuff's runtime — required for the AI agent |
| **Git for Windows** | IDE source control integration (VS Code/Cursor) |
| **VS Code / Cursor / Kiro** | Editors — use WSL Remote extension for Linux dev |
| **Ghostscript** | WeasyPrint dependency for PDF generation |
| **Adobe Acrobat** | PDF viewer |
| **Microsoft Office** | Productivity |
| **Firefox / Opera** | Browsers |
| **RipGrep** | Used by editors for search |
| **FFmpeg** | Used by Audacity |
| **Audacity / GIMP / Paint.NET** | Personal tools |
| **Obsidian / Xmind** | Personal tools |
| **Dell drivers** | Hardware support |
| **Intel drivers** | Hardware support |
| **.NET runtimes** | Used by various Windows apps |
| **Visual C++ runtimes** | Used by many Windows apps |

---

## 📊 Disk Space Recovery Summary

| Uninstall | Disk Saved | RAM Saved |
|-----------|-----------|-----------|
| Docker Desktop | ~2 GB | ~100 MB |
| PostgreSQL 18 (Windows) | ~300 MB | ~200 MB |
| Python 3.13 (Store) | ~200 MB | — |
| hermes venv (optional) | ~200 MB | — |
| **Total** | **~2.7 GB** | **~300 MB** |

---

## 🔄 After Uninstalling

1. **Restart your computer** to fully release all services and file locks
2. Open a **new WSL terminal** (or run `wsl --shutdown` from PowerShell, then reopen)
3. Verify the clean PATH:
   ```bash
   echo $PATH
   # Should show only Linux paths, no /mnt/c/ entries
   ```
4. Verify services:
   ```bash
   systemctl status postgresql
   systemctl status redis-server
   ```
5. Test the app:
   ```bash
   cd ~/NSA_webservice
   source venv/bin/activate
   python app.py
   ```
