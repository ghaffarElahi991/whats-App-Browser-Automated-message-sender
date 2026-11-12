#!/bin/bash

set -u

PROJECT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIRECTORY" || exit 1

clear
echo "Mayfair WhatsApp Web Sender"
echo "Updating code from GitHub..."
echo "Folder: $PROJECT_DIRECTORY"
echo

if ! command -v git >/dev/null 2>&1; then
    echo "Git was not found. Install the Apple Command Line Tools with:"
    echo "xcode-select --install"
    exit_status=1
elif [[ ! -d ".git" ]]; then
    echo "This folder is not a Git repository."
    exit_status=1
elif git pull; then
    echo
    echo "Code update completed successfully."
    exit_status=0
else
    exit_status=$?
    echo
    echo "Code update failed. Review the Git message above."
fi

echo
read -r -p "Press Return to close this window..." _response
exit "$exit_status"
