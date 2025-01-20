#!/bin/bash

echo
echo "  ___   ___ ___ ___                      ___      _"
echo " |   \\ / __/ __/ __| ___ _ ___ _____ _ _| _ ) ___| |_"
echo " | |) | (__\\__ \\__ \\/ -_) '_\\ V / -_) '_| _ \\/ _ \\  _|"
echo " |___/ \\___|___/___/\\___|_|  \\_/\\___|_| |___/\\___/\\__|"
echo

# Check if Python is installed
if ! command -v python &>/dev/null; then
    echo "python is not in your PATH."
    echo "Choose 'Add python to the environment' in your Python installer."
    echo "Please press any key to continue..."
    read -n 1 -s
    exit 127
fi

ARGS=("$@")
node_name=$(hostname)

# Parse optional -n argument
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n)
            node_name="$2"
            shift
            ;;
    esac
    shift
done

# Remove existing PID file
rm -f "dcssb_${node_name}.pid"

VENV="$HOME/.dcssb"

# Create virtual environment if it doesn't exist
if [[ ! -d "$VENV" ]]; then
    echo "Creating the Python Virtual Environment. This may take some time..."
    python -m pip install --upgrade pip
    python -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/python" -m pip install --no-cache-dir --prefer-binary -r requirements.txt
fi

PROGRAM="run.py"

# Main loop
while true; do
    "$VENV/bin/python" "$PROGRAM" "${ARGS[@]}"
    EXIT_CODE=$?

    if [[ $EXIT_CODE -eq -1 ]]; then
        PROGRAM="run.py"
    elif [[ $EXIT_CODE -eq -3 ]]; then
        PROGRAM="update.py"
    elif [[ $EXIT_CODE -eq -2 ]]; then
        echo "Please press any key to continue..."
        read -n 1 -s
        break
    else
        break
    fi
done
