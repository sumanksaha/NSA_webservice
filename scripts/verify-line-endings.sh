#!/bin/bash
# =============================================================================
# Line Endings Verification Script
# For Python/FastAPI projects deployed on Linux
# Run this to verify that line endings are correctly configured
# =============================================================================

set -e

echo "================================================================================"
echo "Verifying Line Endings Configuration"
echo "================================================================================"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0

# =============================================================================
# Check 1: Git Configuration
# =============================================================================
echo "[1/6] Checking Git configuration..."
AUTOCRLF=$(git config --get core.autocrlf)
SAFECRLF=$(git config --get core.safecrlf)
FILEMODE=$(git config --get core.filemode)

if [ "$AUTOCRLF" = "false" ] && [ "$SAFECRLF" = "true" ]; then
    echo -e "${GREEN}✓${NC} Git configuration correct (autocrlf=$AUTOCRLF, safecrlf=$SAFECRLF, filemode=$FILEMODE)"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Git configuration incorrect: core.autocrlf=$AUTOCRLF, core.safecrlf=$SAFECRLF, core.filemode=$FILEMODE"
    echo -e "${YELLOW}  Run: git config --global core.autocrlf false && git config --global core.safecrlf true${NC}"
    ((FAIL++))
fi

# =============================================================================
# Check 2: .gitattributes Exists
# =============================================================================
echo "[2/6] Checking .gitattributes..."
if [ -f ".gitattributes" ]; then
    echo -e "${GREEN}✓${NC} .gitattributes file exists"
    ((PASS++))
else
    echo -e "${RED}✗${NC} .gitattributes file not found"
    ((FAIL++))
fi

# =============================================================================
# Check 3: No CRLF in Critical Files
# =============================================================================
echo "[3/6] Checking critical files for CRLF..."

# Check Python files
PY_CRLF=$(git ls-files -z '*.py' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$PY_CRLF" -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No CRLF found in Python files"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Found CRLF in $PY_CRLF Python files"
    ((FAIL++))
fi

# Check shell scripts
SH_CRLF=$(git ls-files -z '*.sh' '*.bash' '*.zsh' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$SH_CRLF" -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No CRLF found in shell scripts"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Found CRLF in $SH_CRLF shell scripts"
    ((FAIL++))
fi

# =============================================================================
# Check 4: No CRLF in Configuration Files
# =============================================================================
echo "[4/6] Checking configuration files for CRLF..."

# Check YAML files
YAML_CRLF=$(git ls-files -z '*.yaml' '*.yml' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$YAML_CRLF" -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No CRLF found in YAML files"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Found CRLF in $YAML_CRLF YAML files"
    ((FAIL++))
fi

# Check JSON files
JSON_CRLF=$(git ls-files -z '*.json' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$JSON_CRLF" -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No CRLF found in JSON files"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Found CRLF in $JSON_CRLF JSON files"
    ((FAIL++))
fi

# Check TOML files
TOML_CRLF=$(git ls-files -z '*.toml' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$TOML_CRLF" -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No CRLF found in TOML files"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Found CRLF in $TOML_CRLF TOML files"
    ((FAIL++))
fi

# Check Markdown files
MD_CRLF=$(git ls-files -z '*.md' | xargs -0 file 2>/dev/null | grep -c "CRLF" || true)
if [ "$MD_CRLF" -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No CRLF found in Markdown files"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Found CRLF in $MD_CRLF Markdown files"
    ((FAIL++))
fi

# =============================================================================
# Check 5: Git Attributes Applied Correctly
# =============================================================================
echo "[5/6] Verifying .gitattributes are applied..."

# Check a Python file
if git ls-files '*.py' | head -1 | xargs git check-attr eol 2>/dev/null | grep -q "lf"; then
    echo -e "${GREEN}✓${NC} Python files configured for LF"
    ((PASS++))
else
    echo -e "${RED}✗${NC} Python files not configured for LF"
    ((FAIL++))
fi

# Check .gitattributes itself
if git check-attr eol -- .gitattributes 2>/dev/null | grep -q "lf"; then
    echo -e "${GREEN}✓${NC} .gitattributes configured for LF"
    ((PASS++))
else
    echo -e "${RED}✗${NC} .gitattributes not configured for LF"
    ((FAIL++))
fi

# =============================================================================
# Check 6: VS Code Configuration
# =============================================================================
echo "[6/6] Checking VS Code configuration..."

if [ -f ".vscode/settings.json" ]; then
    if grep -q '"files.eol": "\\n"' .vscode/settings.json 2>/dev/null; then
        echo -e "${GREEN}✓${NC} VS Code configured for LF (files.eol=\"\\n\")"
        ((PASS++))
    else
        echo -e "${YELLOW}⚠${NC} VS Code settings.json exists but files.eol may not be set"
        ((FAIL++))
    fi
else
    echo -e "${YELLOW}⚠${NC} .vscode/settings.json not found (optional but recommended)"
fi

if [ -f ".editorconfig" ]; then
    echo -e "${GREEN}✓${NC} .editorconfig exists"
    ((PASS++))
else
    echo -e "${YELLOW}⚠${NC} .editorconfig not found (optional but recommended)"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "================================================================================"
echo "Verification Summary"
echo "================================================================================"
echo ""
TOTAL=$((PASS + FAIL))
echo -e "Total checks: $TOTAL"
echo -e "${GREEN}Passed: $PASS${NC}"
echo -e "${RED}Failed: $FAIL${NC}"
echo ""

if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}✓ All critical checks passed!${NC}"
    echo ""
    echo "Your repository is properly configured for cross-platform development."
    echo "No line ending warnings should appear."
    exit 0
else
    echo -e "${RED}✗ Some checks failed.${NC}"
    echo ""
    echo "Please review the failed checks above and:"
    echo "1. Run: scripts/setup-git-line-endings.bat (Windows) or configure Git manually"
    echo "2. Run: scripts/convert-line-endings.bat (Windows) or git add --renormalize ."
    echo "3. Commit the changes"
    echo "4. Re-run this verification script"
    exit 1
fi