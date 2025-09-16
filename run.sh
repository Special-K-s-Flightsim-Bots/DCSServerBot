#!/bin/bash

echo
echo "  ___   ___ ___ ___                      ___      _"
echo " |   \\ / __/ __/ __| ___ _ ___ _____ _ _| _ ) ___| |_"
echo " | |) | (__\\__ \\__ \\/ -_) '_\\ V / -_) '_| _ \\/ _ \\  _|"
echo " |___/ \\___|___/___/\\___|_|  \\_/\\___|_| |___/\\___/\\__|"
echo

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
    python -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    "$VENV/bin/pip" install pip-tools
    "$VENV/bin/pip-sync" requirements.txt
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
