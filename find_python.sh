#!/usr/bin/env bash
set -e

# Find the newest Python to use

export PATH="$HOME/.local/bin:$HOME/.local/ubersmith_venv/bin:$PATH"

rm -rf "$HOME"/.local/ubersmith_venv

echo "Checking for Python 3.11 or newer..."

PYTHON_BIN=""
NEWEST_VERSION=""

# Newest-first candidate list. Add new minor versions here as they're supported.
CANDIDATES=(python3.13 python3.12 python3.11)

for pybin in "${CANDIDATES[@]}"; do
    command -v "$pybin" &> /dev/null || continue
    PYTHON_BIN=$pybin
    NEWEST_VERSION=$("$pybin" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
    break
done

if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 &> /dev/null; then
        VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo "unknown")
        echo "Error: Python version ($VERSION) is older than 3.11. Please upgrade your Python installation."
    else
        echo "Error: The python3 binary is not installed or not in your PATH. Please check the Installation Documentation."
    fi
    exit 1
fi

echo "Using $PYTHON_BIN ($NEWEST_VERSION)"

# Requires python3-venv on Ubuntu
echo "Creating Ubersmith Python virtual environment..."
"$PYTHON_BIN" -m venv "$HOME"/.local/ubersmith_venv
