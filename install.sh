#!/bin/bash

echo
echo "  ___   ___ ___ ___                      ___      _"
echo " |   \\ / __/ __/ __| ___ _ ___ _____ _ _| _ ) ___| |_"
echo " | |) | (__\\__ \\__ \\/ -_) '_\\ V / -_) '_| _ \\/ _ \\  _|"
echo " |___/ \\___|___/___/\\___|_|  \\_/\\___|_| |___/\\___/\\__|"
echo

# Check if Python is in PATH
if ! command -v python &> /dev/null; then
    echo "python is not in your PATH."
    echo "Please choose 'Add python to the environment' in your Python installer."
    echo "Press any key to continue..."
    read -n 1
    exit 9009
fi

# Set virtual environment path
VENV="$HOME/.dcssb"

# Check if virtual environment exists
if [ ! -d "$VENV" ]; then
    echo "Creating the Python Virtual Environment. This may take some time..."
    python -m pip install --upgrade pip
    python -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/python" -m pip install -r requirements.txt
fi

# Run the install.py script with arguments
"$VENV/bin/python" install.py "$@"

echo "Press any key to continue..."
read -n 1
