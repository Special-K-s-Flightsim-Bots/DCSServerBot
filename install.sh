#!/bin/bash

echo
echo "  ___   ___ ___ ___                      ___      _"
echo " |   \\ / __/ __/ __| ___ _ ___ _____ _ _| _ ) ___| |_"
echo " | |) | (__\\__ \\__ \\/ -_) '_\\ V / -_) '_| _ \\/ _ \\  _|"
echo " |___/ \\___|___/___/\\___|_|  \\_/\\___|_| |___/\\___/\\__|"
echo

# Check if Python is callable
if ! python --version &> /dev/null; then
    echo "Python is not installed or not in your PATH, or it's an invalid alias."
    echo "Please choose 'Add python to the environment' in your Python installer."
    echo "Press any key to continue..."
    read -n 1
    exit 9009
fi

# Extract Python version and ensure it's > 3.9
python_version=$(python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
required_version="3.9"

# Compare Python versions
if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "Python version must be > 3.9. Detected version: $python_version"
    echo "Please upgrade your Python installation."
    exit 1
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
