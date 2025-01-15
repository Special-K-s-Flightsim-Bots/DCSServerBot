#!/bin/bash

# Check if Python is installed and in the PATH
if ! command -v python &> /dev/null; then
    echo "python is not in your PATH."
    echo "Please choose 'Add python to the environment' in your Python installer."
    echo "Press any key to continue..."
    read -n 1
    exit 9009
fi

# Path to the virtual environment
VENV="$HOME/.dcssb"

# Check if the virtual environment exists
if [ ! -d "$VENV" ]; then
    echo "Creating the Python Virtual Environment. This may take some time..."
    python -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/pip" install -r requirements.txt
fi

# Run the `mizedit.py` script with all passed arguments
"$VENV/bin/python" mizedit.py "$@"
