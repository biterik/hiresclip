#!/bin/bash
# install.sh - set up hiresclip on macOS using conda (miniforge/mambaforge/miniconda).
#
# What it does:
#   1. creates (or updates) the conda environment "hiresclip" from environment.yml
#   2. copies hiresclip.py to ~/bin/hiresclip.py
#   3. runs a self-check
#   4. prints the exact command line to paste into the Shortcuts "Run Shell Script" action
#
# It does not need Homebrew and never pip-installs into a system or Homebrew Python.
#
# Environment overrides:
#   HIRESCLIP_ENV   name of the conda env (default: hiresclip)
#   CONDA_EXE       path to the conda executable, if it is not on PATH
set -euo pipefail
 
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${HIRESCLIP_ENV:-hiresclip}"
TARGET_DIR="$HOME/bin"
TARGET="$TARGET_DIR/hiresclip.py"
 
if [ "$(uname -s)" != "Darwin" ]; then
    echo "install.sh: hiresclip only works on macOS (the clipboard part needs AppKit)." >&2
    exit 1
fi
 
# --- 1. find conda ---------------------------------------------------------
find_conda() {
    if [ -n "${CONDA_EXE:-}" ] && [ -x "$CONDA_EXE" ]; then
        echo "$CONDA_EXE"; return
    fi
    if command -v conda >/dev/null 2>&1; then
        command -v conda; return
    fi
    local c
    for c in "$HOME/miniforge3/bin/conda" "$HOME/mambaforge/bin/conda" \
             "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" \
             "/opt/homebrew/Caskroom/miniforge/base/bin/conda" \
             "/opt/miniconda3/bin/conda" "/opt/anaconda3/bin/conda"; do
        if [ -x "$c" ]; then echo "$c"; return; fi
    done
    return 1
}
 
if ! CONDA="$(find_conda)"; then
    cat >&2 <<'EOF'
install.sh: conda not found.
Install Miniforge (https://github.com/conda-forge/miniforge) or set CONDA_EXE to
your conda executable, then run this script again.
EOF
    exit 1
fi
echo "Using conda: $CONDA"
 
# --- 2. create or update the environment -----------------------------------
if "$CONDA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "Environment '$ENV_NAME' exists - updating it from environment.yml"
    "$CONDA" env update -n "$ENV_NAME" -f "$HERE/environment.yml" --prune
else
    echo "Creating environment '$ENV_NAME' from environment.yml"
    "$CONDA" env create -n "$ENV_NAME" -f "$HERE/environment.yml"
fi
 
# Locate the env's interpreter without relying on `conda run` output, which
# may carry extra lines (e.g. the "newer conda available" notice).
PREFIX="$("$CONDA" env list | awk -v n="$ENV_NAME" '$1 == n { print $NF }' | head -n 1)"
if [ -z "$PREFIX" ] || [ ! -d "$PREFIX" ]; then
    PREFIX="$(dirname "$(dirname "$CONDA")")/envs/$ENV_NAME"
fi
PYTHON="$PREFIX/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "install.sh: could not locate the python interpreter of env '$ENV_NAME'" >&2
    echo "  looked for: $PYTHON" >&2
    echo "  'conda env list' reports:" >&2
    "$CONDA" env list >&2
    exit 1
fi
 
# --- 3. install the script -------------------------------------------------
mkdir -p "$TARGET_DIR"
cp "$HERE/hiresclip.py" "$TARGET"
chmod +x "$TARGET"
echo "Installed $TARGET"
 
# --- 4. self-check ---------------------------------------------------------
echo
echo "Self-check:"
"$PYTHON" "$TARGET" --check
 
# --- 5. print the Shortcuts command ----------------------------------------
cat <<EOF
 
============================================================================
Paste this line into the "Run Shell Script" action of your Shortcut
(Shell: zsh, Input: none):
 
$PYTHON $TARGET
 
Optional flags: --dpi 300   --no-svg   --svg-dir /some/folder
See README.md, section "Installation", for the Shortcuts setup.
============================================================================
EOF
