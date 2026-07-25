# Line Endings Configuration - Executive Summary

**Last Updated:** 2026-07-25  
**Project:** NSA_webservice  
**Deployment Target:** Linux  
**Status:** ✅ Configured

---

## Quick Start

### For You (Current Setup)

The configuration has been applied to this repository. Skip to [Verification](#verification).

### For Teammates

Each team member must run:

```bash
# Windows
scripts\setup-git-line-endings.bat
scripts\convert-line-endings.bat

# macOS/Linux
git config --global core.autocrlf false
git config --global core.safecrlf true
git config --global core.filemode false
```

---

## The Problem

```
LF will be replaced by CRLF the next time Git touches it.
```

**Root Cause:** `core.autocrlf=true` conflicts with `.gitattributes` rules.

**Solution:** Disable Git's automatic line ending conversion and let `.gitattributes` be the single source of truth.

---

## Configuration Files

| File | Purpose |
|------|---------|
| [`.gitattributes`](.gitattributes) | Git-level line ending rules |
| [`.editorconfig`](.editorconfig) | Editor-level formatting standards |
| [`.vscode/settings.json`](.vscode/settings.json) | VS Code-specific settings |
| [`scripts/setup-git-line-endings.bat`](scripts/setup-git-line-endings.bat) | Windows one-time setup |
| [`scripts/convert-line-endings.bat`](scripts/convert-line-endings.bat) | Convert existing files to LF |
| [`scripts/verify-line-endings.sh`](scripts/verify-line-endings.sh) | Verify configuration |
| [`docs/LINE_ENDINGS_SETUP.md`](docs/LINE_ENDINGS_SETUP.md) | Complete documentation |

---

## Line Ending Strategy

| File Type | Line Ending | Reason |
|-----------|-------------|--------|
| Python (`*.py`) | LF | Required for Linux deployment |
| Shell scripts (`*.sh`, `*.bash`) | LF | Linux execution |
| Configuration (`*.json`, `*.yaml`, `*.toml`) | LF | Cross-platform standard |
| Web files (`*.html`, `*.css`, `*.js`, `*.ts`) | LF | Modern web standard |
| SQL (`*.sql`) | LF | Database deployment |
| Dockerfile | LF | Linux containers |
| **Batch (`*.bat`, `*.cmd`)** | **CRLF** | Windows-native |
| **PowerShell (`*.ps1`, `*.psm1`)** | **CRLF** | Windows-native |
| Binary (`*.png`, `*.zip`, `*.db`) | Binary | No modification |

**Rule:** LF for everything except Windows-native `.bat` and `.ps1` files.

---

## Git Settings Applied

```bash
core.autocrlf=false      # Disable Git's built-in conversion (use .gitattributes)
core.safecrlf=true       # Prevent accidental commits of wrong line endings
core.filemode=false      # Ignore file permission changes
core.whitespace=cr-at-eol # Treat trailing CR as whitespace
```

**Why:** `.gitattributes` becomes the single source of truth, eliminating the conflict that caused the warning.

---

## Verification

Run these commands to confirm everything works:

```bash
# 1. Check Git config
git config --list | grep -E "core\.(autocrlf|safecrlf|filemode)"

# Expected:
# core.autocrlf=false
# core.safecrlf=true
# core.filemode=false

# 2. Verify .gitattributes is applied
git check-attr -a -- app.py

# Expected:
# app.py: text: set
# app.py: eol: lf

# 3. Check for CRLF in text files (should show nothing)
git ls-files -z | xargs -0 file | findstr CRLF

# 4. Run automated verification script
# Windows: bash scripts/verify-line-endings.sh
# Linux/macOS: ./scripts/verify-line-endings.sh
```

---

## Teammate Setup Instructions

### Windows Developers

```powershell
# One-time setup
scripts\setup-git-line-endings.bat

# Convert existing files (if repo already cloned)
scripts\convert-line-endings.bat

# Install EditorConfig extension
code --install-extension EditorConfig.EditorConfig
```

### macOS Developers

```bash
git config --global core.autocrlf false
git config --global core.safecrlf true
git config --global core.filemode false
code --install-extension EditorConfig.EditorConfig
```

### Linux Developers

```bash
git config --global core.autocrlf false
git config --global core.safecrlf true
git config --global core.filemode false
code --install-extension EditorConfig.EditorConfig
```

---

## Converting Existing Files (One-Time Only)

**Only run this ONCE after all teammates have configured Git.**

```bash
# 1. Remove files from index (keeps working directory)
git rm --cached -r .

# 2. Re-add all files (Git applies .gitattributes rules)
git reset --hard

# Alternative (Git 2.16+):
# git add --renormalize .

# 3. Verify no CRLF remains
git ls-files -z | xargs -0 file | findstr CRLF
# Should output nothing

# 4. Commit the normalization
git commit -m "chore: normalize line endings to LF"

# 5. Push
git push
```

---

## How This Prevents Future Warnings

1. **Git sees `.gitattributes`** → Applies rules before checkout/save
2. **`core.autocrlf=false`** → Git stops trying to be "helpful"
3. **`core.safecrlf=true`** → Blocks commits that violate rules
4. **VS Code `files.eol="\n"`** → Editor always saves with LF
5. **EditorConfig** → Additional editor-level enforcement

**Result:** No more "LF will be replaced by CRLF" warnings ever.

---

## Detailed Documentation

For complete details, see [docs/LINE_ENDINGS_SETUP.md](docs/LINE_ENDINGS_SETUP.md).

---

## Summary

- ✅ Problem diagnosed: `core.autocrlf=true` conflicts with `.gitattributes`
- ✅ Strategy: LF everywhere except `.bat`/`.cmd`/`.ps1`/`.psm1`
- ✅ Files created: `.gitattributes`, `.editorconfig`, `.vscode/settings.json`, scripts, docs
- ✅ Git configuration applied and verified
- ✅ Teammate instructions provided for Windows/macOS/Linux
- ✅ Verification script created
- ✅ Ready for enterprise deployment

**Next step:** Team members should run the setup script and convert existing files.

---

**Document Version:** 1.0  
**Maintained By:** DevOps Team