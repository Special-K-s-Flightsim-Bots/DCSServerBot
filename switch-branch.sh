#!/bin/bash

# Enable error handling
set -e

# Get the directory of the script file
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$script_dir"

# Get current branch from .git/HEAD
if [ ! -f .git/HEAD ]; then
    echo "Error: .git/HEAD file not found. Are you inside a Git repository?"
    exit 1
fi

branch=$(awk '{print $2}' .git/HEAD)
branch=${branch#refs/heads/}  # Trim the branch name

# Switch to the other branch
if [ "$branch" == "master" ]; then
    read -p "Switch to development branch? [y/N] " choice
    case "$choice" in
        [yY])
            git checkout development
            ./update.sh  # Call the equivalent `update.cmd` script in Bash
            ;;
        *)
            echo "Operation aborted."
            exit 0
            ;;
    esac
elif [ "$branch" == "development" ]; then
    read -p "Switch to master branch? [y/N] " choice
    case "$choice" in
        [yY])
            git checkout master
            ./update.sh  # Call the equivalent `update.cmd` script in Bash
            ;;
        *)
            echo "Operation aborted."
            exit 0
            ;;
    esac
else
    echo "Unknown branch: $branch"
    exit 1
fi
