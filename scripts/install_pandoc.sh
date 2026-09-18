#!/usr/bin/env bash
# Install a pinned pandoc release with AsciiDoc input support (>= 3.8.3).
#
# Why pinned upstream tarballs instead of `apt-get install pandoc`?
# Pandoc gained an AsciiDoc *input* reader only in 3.8.3 (2025-12); the apt
# package on Debian/Ubuntu images is far older, so `pandoc -f asciidoc`
# would fail and the DOCX renderer (app/shared/adoc_renderer.py) would
# silently fall back to the plain-text python-docx path.
#
# Usage: bash scripts/install_pandoc.sh [install_dir]
#   install_dir defaults to /usr/local/bin (override for user-local installs)
#   Skips the download when an existing pandoc already supports AsciiDoc.
#
# Env:
#   PANDOC_VERSION — pinned release (default 3.11)

set -euo pipefail

PANDOC_VERSION="${PANDOC_VERSION:-3.11}"
INSTALL_DIR="${1:-/usr/local/bin}"

# True when a pandoc is on PATH with AsciiDoc input support (>= 3.8.3).
pandoc_ok() {
    command -v pandoc >/dev/null 2>&1 || return 1
    local version
    version="$(pandoc --version | head -n1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1)"
    [ -n "$version" ] || return 1
    [ "$(printf '%s\n' "$version" "3.8.3" | sort -V | head -n1)" = "3.8.3" ]
}

if pandoc_ok; then
    echo "pandoc $(pandoc --version | head -n1 | awk '{print $2}') already supports asciidoc — skipping download"
    exit 0
fi

case "$(uname -m)" in
    x86_64) PANDOC_ARCH="amd64" ;;
    aarch64 | arm64) PANDOC_ARCH="arm64" ;;
    *)
        echo "ERROR: unsupported architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

TARBALL="pandoc-${PANDOC_VERSION}-linux-${PANDOC_ARCH}.tar.gz"
URL="https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/${TARBALL}"

# Upstream ships no checksum file for these assets — verify functionally:
# the binary must run and must list asciidoc as an input format.
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading ${URL}"
curl -fsSL "$URL" -o "${TMP_DIR}/${TARBALL}"

tar -xzf "${TMP_DIR}/${TARBALL}" -C "$TMP_DIR"
BIN="${TMP_DIR}/pandoc-${PANDOC_VERSION}/bin/pandoc"
[ -x "$BIN" ] || { echo "ERROR: pandoc binary not found in tarball" >&2; exit 1; }

"$BIN" --version >/dev/null

if ! "$BIN" --list-input-formats | grep -qx "asciidoc"; then
    echo "ERROR: pandoc ${PANDOC_VERSION} is missing the asciidoc input reader" >&2
    exit 1
fi

if [ -w "$INSTALL_DIR" ]; then
    install -m 0755 "$BIN" "$INSTALL_DIR/pandoc"
elif command -v sudo >/dev/null 2>&1; then
    sudo install -m 0755 "$BIN" "$INSTALL_DIR/pandoc"
else
    install -m 0755 "$BIN" "$INSTALL_DIR/pandoc"
fi

echo "pandoc ${PANDOC_VERSION} installed to ${INSTALL_DIR}/pandoc"
"$INSTALL_DIR/pandoc" --version | head -n1
