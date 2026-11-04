#!/bin/bash
# SOURCED by the launchers: resolve a uv executable, installing it for this user when it is absent.
# Sets UVEXE, and puts the install directory on PATH so child processes (update.py) find uv too.
# Deliberately contains no `return`/`exit`, so sourcing it can never end the caller early.

UVEXE="${UVEXE:-}"
if [ -z "$UVEXE" ]; then
    if command -v uv >/dev/null 2>&1; then
        UVEXE=uv
    elif [ -x "$HOME/.local/bin/uv" ]; then
        UVEXE="$HOME/.local/bin/uv"
        PATH="$HOME/.local/bin:$PATH"
        export PATH
    else
        echo "uv was not found - installing it for this user ..." >&2
        # uv's standalone installer puts the binary in ~/.local/bin (see uv's installation docs).
        curl -LsSf https://astral.sh/uv/install.sh | sh || true
        if [ -x "$HOME/.local/bin/uv" ]; then
            UVEXE="$HOME/.local/bin/uv"
            PATH="$HOME/.local/bin:$PATH"
            export PATH
        else
            echo "***  WARNING  *** uv could not be installed automatically." >&2
            echo "Install it from https://docs.astral.sh/uv/ and try again." >&2
            UVEXE=""
        fi
    fi
fi
