#!/bin/bash

echo
echo "  ___   ___ ___ ___                      ___      _"
echo " |   \\ / __/ __/ __| ___ _ ___ _____ _ _| _ ) ___| |_"
echo " | |) | (__\\__ \\__ \\/ -_) '_\\ V / -_) '_| _ \\/ _ \\  _|"
echo " |___/ \\___|___/___/\\___|_|  \\_/\\___|_| |___/\\___/\\__|"
echo

# Resolve the node name first: the environment is per node, so every launcher must agree on it.
ARGS=("$@")
node_name=$(hostname)

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n)
            node_name="$2"
            shift
            ;;
    esac
    shift
done

# Set virtual environment path
VENV="$HOME/.dcssb-$node_name"

# Prefer the environment's own interpreter, so no Python has to sit on PATH.
PYTHON="$VENV/bin/python"
[ -x "$PYTHON" ] || PYTHON=python

# Check if Python can run successfully and get the version
python_version=$("$PYTHON" -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>/dev/null)

# If Python is not installed or fails to run
if [ -z "$python_version" ]; then
    echo "No Python was found - neither in this node's environment nor on your PATH."
    echo "Please ensure Python is installed and available."
    echo "Press any key to continue..."
    read -n 1
    exit 1
fi

# Required minimum Python version
required_version="3.11"

# Compare Python versions
if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "Python version must be >= $required_version. Detected version: $python_version"
    echo "Please upgrade your Python installation."
    exit 1
fi

# Resolve uv for this session, installing it for this user if it is missing (see get_uv.sh).
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/get_uv.sh"

# Keep the package cache on the same filesystem as the environment, so uv can link packages into
# it instead of copying them. Set UV_CACHE_DIR yourself to place the cache elsewhere.
export UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.uv-cache}"

# Check if virtual environment exists
if [ ! -d "$VENV" ]; then
    if [ -z "$UVEXE" ]; then
        echo "uv is required to create this node's environment and is not available."
        echo "Install it from https://docs.astral.sh/uv/ and try again."
        exit 1
    fi
    # requirements.local is an optional extra requirements file (see plugins/README.md).
    REQ=(requirements.txt)
    if [ -f requirements.local ]; then
        REQ+=(requirements.local)
    fi

    echo "Creating the Python Virtual Environment. This may take some time..."
    "$UVEXE" venv "$VENV"
    "$UVEXE" pip sync --python "$VENV/bin/python" "${REQ[@]}"
fi

# The environment exists now, so run with its own interpreter.
PYTHON="$VENV/bin/python"

# Run the install.py script with arguments
"$PYTHON" install.py "${ARGS[@]}"

echo "Press any key to continue..."
read -n 1
