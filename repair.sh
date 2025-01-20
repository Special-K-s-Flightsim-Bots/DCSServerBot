#!/bin/bash

# Set path to the virtual environment
VENV="$HOME/.dcssb"

echo "Cleaning up the virtual environment ..."

# Attempt to remove the virtual environment directory
rm -rf "$VENV" > /dev/null 2>&1

# Check if the directory still exists after attempting to remove it
if [ -d "$VENV" ]; then
    echo "**************************************************"
    echo "WARNING: Could not delete the virtual environment."
    echo "Please manually delete the .dcssb directory."
    echo "Directory Path: $VENV"
    echo "**************************************************"
else
    echo "Virtual environment cleaned."
    echo "A new environment will be created on the next DCSServerBot launch."
fi

# Check if Git is installed
if ! command -v git &> /dev/null; then
    # Git not found
    echo "Git executable not found, couldn't reset the repository."
else
    # Git found, reset the repository
    echo "Resetting the GIT repository ..."
    git config --global --add safe.directory "$(pwd)" > /dev/null 2>&1
    git reset --hard > /dev/null 2>&1
    echo "Repository reset."
fi

echo
echo "Please press any key to continue..."
read -n 1
