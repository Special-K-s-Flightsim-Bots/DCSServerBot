#!/bin/bash

# Check if Python can run successfully and get the version
python_version=$(python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>/dev/null)

# If Python is not installed or fails to run
if [ -z "$python_version" ]; then
    echo "Python is not installed, not callable, or not in your PATH."
    echo "Please ensure Python is installed and available."
    echo "Press any key to continue..."
    read -n 1
    exit 9009
fi

# Required minimum Python version
required_version="3.10"

# Compare Python versions
if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "Python version must be >= 3.10. Detected version: $python_version"
    echo "Please upgrade your Python installation."
    exit 1
fi

# Path to the virtual environment
VENV="$HOME/.dcssb"

# Check if the virtual environment exists
if [ ! -d "$VENV" ]; then
    echo "Creating the Python Virtual Environment. This may take some time..."
    python -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/pip" install pip-tools
    "$VENV/bin/pip-sync" requirements.txt
fi

# Run the `mizedit.py` script with all passed arguments
"$VENV/bin/python" mizedit.py "$@"
