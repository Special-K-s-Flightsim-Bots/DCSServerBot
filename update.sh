#!/bin/bash

# Check if Python is installed and in the PATH
if ! command -v python &> /dev/null; then
    echo "python is not in your PATH."
    echo "Please choose 'Add python to the environment' in your Python installer."
    exit 1
fi

# Set path to virtual environment
VENV="$HOME/.dcssb"

# Check if virtual environment exists and create it if needed
if [ ! -d "$VENV" ]; then
    echo "Creating the Python Virtual Environment"
    python -m pip install --upgrade pip
    python -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/python" -m pip install -r requirements.txt
fi

# Run the `update.py` script with the `--no-restart` flag and any additional arguments
"$VENV/bin/python" update.py --no-restart "$@"

echo "Please press any key to continue..."
read -n 1
