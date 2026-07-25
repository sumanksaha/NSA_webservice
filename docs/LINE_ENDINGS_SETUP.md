# Git Line Endings Setup Guide for Enterprise Python/FastAPI Projects

**Last Updated:** 2026  
**Target Deployment:** Linux  
**Project Type:** Python/FastAPI  
**Purpose:** Eliminate LF/CRLF warnings and enforce consistent line endings

---

## Table of Contents

1. [Why This Warning Occurs](#why-this-warning-occurs)
2. [Recommended Line-Ending Strategy](#recommended-line-ending-strategy)
3. [Git Configuration Explained](#git-configuration-explained)
4. [Platform-Specific Setup Instructions](#platform-specific-setup-instructions)
5. [Converting Existing Files](#converting-existing-files)
6. [Verification Steps](#verification-steps)
7. [Troubleshooting](#troubleshooting)
8. [Best Practices](#best-practices)

---

## Why This Warning Occurs

### The Problem

When `core.autocrlf=true` is set on Windows, Git automatically converts LF (Line Feed, `\n`) line endings to CRLF (Carriage Return + Line Feed, `\r\n`) when checking out files, and converts them back to LF when committing.

**The warning occurs because:**
1. You have `core.autocrlf=true` configured locally
2. The `.gitattributes` file specifies `eol=lf` for certain files
3. Git detects a conflict between your global setting and the repository's rules
4. Git warns you that it will "fix" the line endings the next time it touches the file

### Why This Happens in Cross-Platform Projects

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows Developer (core.autocrlf=true)                         │
│  - Checks out file: LF → CRLF (automatic conversion)           │
│  - Edits file: saves with CRLF                                 │
│  - Commits: CRLF → LF (automatic conversion)                   │
│  - Git sees: "Working directory has CRLF, but .gitattributes   │
│               says LF. I'll warn and convert next time."       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Linux/macOS Developer (core.autocrlf=input or false)           │
│  - Checks out file: LF (no conversion needed)                  │
│  - Edits file: saves with LF                                   │
│  - Commits: LF → LF (no conversion)                            │
│  - No warnings!                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Why Python/Linux Deployment Makes This Critical

1. **Python imports:** Python 3 on Linux will refuse to import `.pyc` files if the source file has CRLF line endings
2. **Shebangs:** Shell scripts with CRLF fail with `/bin/bash^M: bad interpreter`
3. **Docker/CI:** Linux containers expect LF line endings; CRLF causes build failures
4. **Consistency:** Diff noise and merge conflicts increase with mixed line endings

---

## Recommended Line-Ending Strategy

### Decision Matrix

| File Type | Line Ending | Rationale |
|-----------|-------------|-----------|
| **Python files** (`*.py`) | **LF** | Required for Linux deployment; Python shebangs and imports |
| **Shell scripts** (`*.sh`, `*.bash`) | **LF** | Linux execution; CRLF breaks interpreters |
| **Configuration files** (`*.json`, `*.yaml`, `*.toml`) | **LF** | Cross-platform standards |
| **Markdown/HTML/CSS/JS** | **LF** | Modern web standards |
| **SQL files** (`*.sql`) | **LF** | Database deployment scripts |
| **Dockerfiles** | **LF** | Linux container builds |
| **Text files** (`*.txt`, `*.md`) | **LF** | Documentation consistency |
| **Windows Batch** (`*.bat`, `*.cmd`) | **CRLF** | Windows-native requirement |
| **PowerShell** (`*.ps1`, `*.psm1`) | **CRLF** | Windows-native requirement |
| **Binary files** (`*.png`, `*.zip`) | **Binary** | No line ending processing |

### Global Standard

**LF for everything except Windows-native batch/PowerShell files.**

This ensures:
- ✅ Linux deployment compatibility
- ✅ Consistent diffs across platforms
- ✅ No Git warnings
- ✅ Shell scripts execute correctly
- ✅ Python modules import cleanly

---

## Git Configuration Explained

### Why We Disable `core.autocrlf`

**Old Approach (Legacy, NOT Recommended):**
```bash
git config --global core.autocrlf true  # Windows
git config --global core.autocrlf input # macOS/Linux
```

Problems:
- ❌ Inconsistent behavior across team members
- ❌ Warnings when `.gitattributes` conflicts with global config
- ❌ Doesn't handle platform-specific needs (e.g., `.bat` files)
- ❌ Violates "configuration as code" principle

**New Approach (Modern 2026 Best Practice):**
```bash
git config --global core.autocrlf false
git config --global core.safecrlf true
```

Benefits:
- ✅ `.gitattributes` becomes the **single source of truth**
- ✅ Repository-defined rules override local settings
- ✅ No warnings
- ✅ Consistent across all platforms
- ✅ Explicit control over each file type

### The Role of `core.safecrlf`

```bash
core.safecrlf=true
```

**What it does:** Prevents you from accidentally committing files with mixed or incorrect line endings.

**Example protection:**
```bash
# If you try to commit a CRLF file that should be LF:
git add myfile.py
git commit -m "fix: update myfile.py"
# ERROR: CRLF would be replaced by LF in myfile.py
```

This safety net ensures the `.gitattributes` rules are enforced.

### Git Configuration Summary

| Setting | Value | Justification |
|---------|-------|---------------|
| `core.autocrlf` | `false` | Disable automatic conversion; let `.gitattributes` handle it |
| `core.safecrlf` | `true` | Prevent accidental commits of mixed line endings |
| `core.autocrlf.input` | `false` | Legacy safety (not needed but harmless) |
| `core.filemode` | `false` | Prevent permission changes from appearing as file modifications |
| `core.whitespace` | `cr-at-eol` | Treat trailing CR as whitespace issue in diffs |

---

## Platform-Specific Setup Instructions

### Windows Developers

#### Option A: Automated Setup (Recommended)

1. **After cloning the repository, run:**
   ```cmd
   scripts\setup-git-line-endings.bat
   ```

2. **Convert existing files:**
   ```cmd
   scripts\convert-line-endings.bat
   ```

3. **Commit the normalization:**
   ```cmd
   git commit -m "chore: normalize line endings to LF"
   git push
   ```

#### Option B: Manual Setup

```cmd
REM Configure Git global settings
git config --global core.autocrlf false
git config --global core.safecrlf true
git config --global core.filemode false
git config --global core.whitespace cr-at-eol

REM Convert existing files
git rm --cached -r .
git reset --hard
git add -A

REM Commit
git commit -m "chore: normalize line endings to LF"
git push
```

#### VS Code Configuration

The repository includes `.vscode/settings.json` which:
- Sets `"files.eol": "\n"` (always LF)
- Enables `editor.formatOnSave` (auto-fixes line endings)
- Configures Black formatter for Python

**Ensure you have the EditorConfig extension installed:**
```powershell
code --install-extension EditorConfig.EditorConfig
```

---

### macOS Developers

```bash
# Configure Git global settings
git config --global core.autocrlf false
git config --global core.safecrlf true
git config --global core.filemode false
git config --global core.whitespace cr-at-eol

# Clone or re-clone the repository
git clone https://github.com/sumanksaha/NSA_webservice.git
cd NSA_webservice

# Verify .gitattributes is present
cat .gitattributes

# No conversion needed (macOS uses LF natively)
# Just commit and push normally
git add -A
git commit -m "chore: normalize line endings to LF"
git push
```

#### VS Code Configuration

Install EditorConfig extension:
```bash
code --install-extension EditorConfig.EditorConfig
```

VS Code will automatically read `.editorconfig` and `.vscode/settings.json`.

---

### Linux Developers

```bash
# Configure Git global settings
git config --global core.autocrlf false
git config --global core.safecrlf true
git config --global core.filemode false
git config --global core.whitespace cr-at-eol

# Clone or re-clone the repository
git clone https://github.com/sumanksaha/NSA_webservice.git
cd NSA_webservice

# Verify .gitattributes is present
cat .gitattributes

# No conversion needed (Linux uses LF natively)
# Just commit and push normally
git add -A
git commit -m "chore: normalize line endings to LF"
git push
```

#### VS Code Configuration

```bash
code --install-extension EditorConfig.EditorConfig
```

---

## Converting Existing Files

### Critical: Do This Once Per Repository

**Only run this ONCE per repository**, after all team members have configured their Git settings.

### Step-by-Step Conversion

```bash
# 1. Ensure nobody else is working on the repo (coordinate with team)
git checkout main
git pull

# 2. Remove all files from the index (keeps working directory)
git rm --cached -r .

# 3. Re-add all files (Git will apply .gitattributes rules)
git reset --hard

# Alternative (safer, no hard reset):
# git add --renormalize .

# 4. Verify no unwanted line endings remain
git ls-files -z | xargs -0 file | grep CRLF
# Should output nothing

# 5. Commit the normalization
git commit -m "chore: normalize line endings to LF per .gitattributes"

# 6. Push to remote
git push origin main

# 7. Tag this commit (optional, for reference)
git tag -a line-ending-normalization-2026 -m "Normalize line endings to LF"
git push origin line-ending-normalization-2026
```

### Alternative: Renormalize Without Removing Files

```bash
# Modern Git (2.16+) supports --renormalize
git add --renormalize .
git commit -m "chore: renormalize line endings"
git push
```

### Handling Large Repositories

For repositories with many files, this may take time:

```bash
# Use parallel processing (Linux/macOS)
git ls-files -z | xargs -0 -n 100 -P 4 file | grep CRLF

# For Windows PowerShell
git ls-files | ForEach-Object { file $_ } | Select-String "CRLF"
```

---

## Verification Steps

### Step 1: Verify Git Configuration

```bash
# Check your local Git config
git config --list | grep -E "core\.(autocrlf|safecrlf|filemode)"

# Expected output:
# core.autocrlf=false
# core.safecrlf=true
# core.filemode=false
```

### Step 2: Verify .gitattributes Is Applied

```bash
# Check Git's internal attributes for a file
git check-attr -a -- app.py

# Expected output:
# app.py: text: set
# app.py: eol: lf
# app.py: diff: python

# For a batch file
git check-attr -a -- scripts/deploy.bat

# Expected output:
# scripts/deploy.bat: text: set
# scripts/deploy.bat: eol: crlf
```

### Step 3: Verify No CRLF in Text Files

```bash
# Linux/macOS (using grep)
git ls-files -z | xargs -0 file | grep -v "CRLF" | grep "ASCII text" | wc -l

# Windows (using findstr)
git ls-files | ForEach-Object { file $_ } | findstr /V "CRLF" | find "ASCII text"

# Expected: All text files should show "ASCII text" (not "ASCII text, with CRLF")
```

### Step 4: Test File Modification

```bash
# Edit a Python file and save it
echo "# Test" >> app.py

# Check line endings
file app.py

# Expected: app.py: ASCII text (no CRLF)

# Check for Git changes
git diff app.py

# Should show: + # Test (with LF, not CRLF)
```

### Step 5: Verify No Warnings on Pull

```bash
# Pull latest changes
git pull

# Expected: NO warnings about "LF will be replaced by CRLF"

# If you see warnings, your Git config is wrong:
git config --global core.autocrlf false
git config --global core.safecrlf true
```

### Step 6: Verify Shell Scripts Work

```bash
# Test a shell script
bash -n scripts/setup.sh

# Expected: No output (no syntax errors from CRLF)
```

### Step 7: Verify Python Imports

```bash
# Test Python imports
python -c "import app"

# Expected: No errors (CRLF in .py files would cause import errors)
```

### Automated Verification Script

Create `scripts/verify-line-endings.sh` (Linux/macOS):

```bash
#!/bin/bash
set -e

echo "=== Verifying Line Endings ==="
echo ""

# Check 1: Git config
echo "[1/5] Checking Git configuration..."
AUTOCRLF=$(git config --get core.autocrlf)
SAFECRLF=$(git config --get core.safecrlf)

if [ "$AUTOCRLF" = "false" ] && [ "$SAFECRLF" = "true" ]; then
    echo "✓ Git configuration correct"
else
    echo "✗ Git configuration incorrect: core.autocrlf=$AUTOCRLF, core.safecrl=$SAFECRLF"
    exit 1
fi

# Check 2: .gitattributes exists
echo "[2/5] Checking .gitattributes..."
if [ -f ".gitattributes" ]; then
    echo "✓ .gitattributes exists"
else
    echo "✗ .gitattributes not found"
    exit 1
fi

# Check 3: No CRLF in Python files
echo "[3/5] Checking Python files for CRLF..."
PY_CRLF=$(git ls-files -z '*.py' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$PY_CRLF" -eq 0 ]; then
    echo "✓ No CRLF in Python files"
else
    echo "✗ Found CRLF in $PY_CRLF Python files"
    exit 1
fi

# Check 4: No CRLF in shell scripts
echo "[4/5] Checking shell scripts for CRLF..."
SH_CRLF=$(git ls-files -z '*.sh' '*.bash' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$SH_CRLF" -eq 0 ]; then
    echo "✓ No CRLF in shell scripts"
else
    echo "✗ Found CRLF in $SH_CRLF shell scripts"
    exit 1
fi

# Check 5: Verify .bat files have CRLF
echo "[5/5] Checking Windows batch files have CRLF..."
if [ -f "scripts/deploy.bat" ]; then
    BAT_EOL=$(file scripts/deploy.bat | grep -c "CRLF" || true)
    if [ "$BAT_EOL" -gt 0 ]; then
        echo "✓ Windows batch files have correct CRLF line endings"
    else
        echo "⚠ Warning: Batch files should have CRLF"
    fi
else
    echo "⊘ No batch files found to check"
fi

echo ""
echo "=== All Checks Passed ==="
```

---

## Troubleshooting

### Problem: "LF will be replaced by CRLF" warning still appears

**Solution:**
```bash
# 1. Check your Git config
git config --global core.autocrlf false
git config --global core.safecrlf true

# 2. Re-normalize the repository
git rm --cached -r .
git reset --hard
git add -A
git commit -m "fix: renormalize line endings"
git push

# 3. If warning persists, check for inherited configs
git config --list --show-origin | grep autocrlf
# Remove any system-level configs
```

### Problem: Files still have CRLF after conversion

**Solution:**
```bash
# Use dos2unix (Linux/macOS)
find . -type f \( -name "*.py" -o -name "*.sh" \) -exec dos2unix {} +

# For Windows, use PowerShell
Get-ChildItem -Recurse -Include *.py,*.sh | ForEach-Object {
    (Get-Content $_.FullName) -replace "`r`n", "`n" | Set-Content $_.FullName
}

# Re-add to Git
git add -A
git commit -m "fix: convert CRLF to LF"
git push
```

### Problem: `.gitattributes` changes not taking effect

**Solution:**
```bash
# Git caches attributes. Clear the cache:
git checkout -- .gitattributes
git add .gitattributes
git commit -m "chore: update .gitattributes"
git push

# Force re-normalization:
git rm --cached -r .
git reset --hard
git add -A
git commit -m "fix: re-normalize after .gitattributes update"
git push
```

### Problem: VS Code still saves with CRLF

**Solution:**
```bash
# 1. Ensure .vscode/settings.json exists and has:
# "files.eol": "\n"

# 2. Install EditorConfig extension
code --install-extension EditorConfig.EditorConfig

# 3. Disable default line ending behavior in VS Code:
# File > Preferences > Settings > Search "eol"
# Set "Files: Eol" to "\n"

# 4. Reload VS Code
```

---

## Best Practices

### 1. Treat `.gitattributes` as Code

- ✅ Review changes in pull requests
- ✅ Document exceptions with comments
- ❌ Don't add rules without team consensus

### 2. Normalize Early, Normalize Often

```bash
# Run normalization script:
# - After adding new file types to .gitattributes
# - After major dependency upgrades
# - During quarterly maintenance
```

### 3. CI/CD Verification

Add to your CI pipeline (GitHub Actions example):

```yaml
- name: Verify Line Endings
  run: |
    # Check for CRLF in text files
    git ls-files -z | xargs -0 file | grep -c "CRLF" && exit 1 || echo "✓ No CRLF found"
```

### 4. Document Exceptions

If you need an exception (e.g., a vendor file with CRLF):

```gitattributes
# Exception: Legacy vendor file (temporary, ticket #1234)
vendor/legacy-file.txt text eol=crlf
```

### 5. Prevent Future Issues

```bash
# Add to your .git/hooks/pre-commit (example):
#!/bin/bash
if git diff --cached --check | grep -q "CRLF"; then
    echo "ERROR: Attempting to commit file with CRLF line endings"
    exit 1
fi
```

---

## Summary Checklist for Each Team Member

### First-Time Setup

- [ ] Configure Git globally:
  ```bash
  git config --global core.autocrlf false
  git config --global core.safecrlf true
  git config --global core.filemode false
  ```
- [ ] Run conversion script (Windows only):
  ```bash
  scripts\convert-line-endings.bat
  ```
- [ ] Install VS Code EditorConfig extension
- [ ] Verify no warnings:
  ```bash
  git status
  # Should show no warnings
  ```

### Ongoing Development

- [ ] Never manually edit `.gitattributes` without discussion
- [ ] Always run `git status` before commits
- [ ] Report any "LF will be replaced" warnings immediately
- [ ] Run verification script quarterly:
  ```bash
  bash scripts/verify-line-endings.sh
  ```

---

## Additional Resources

- [Git Attributes Documentation](https://git-scm.com/docs/gitattributes)
- [EditorConfig Specification](https://editorconfig.org)
- [Git Line Ending Best Practices](https://docs.github.com/en/get-started/getting-started-with-git/configuring-git-to-handle-line-endings)
- [Python Line Ending Handling](https://docs.python.org/3/tutorial/controlflow.html#more-on-defining-functions)

---

## Support

If you encounter issues:

1. Check this document's troubleshooting section
2. Verify your Git version (2.16+ recommended):
   ```bash
   git --version
   ```
3. Ask in team Slack: `#devops-support`
4. Create an issue with label `git-config`

---

**Document Version:** 1.0  
**Last Updated:** 2026-07-25  
**Maintained By:** DevOps Team