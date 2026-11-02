#!/bin/bash

set -u

PROJECT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIRECTORY" || exit 1

clear
echo "Mayfair WhatsApp Web Sender"
echo "macOS installer"
echo

PYTHON_COMMAND=""
for candidate in \
    python3.15 \
    /Library/Frameworks/Python.framework/Versions/3.15/bin/python3.15 \
    /opt/homebrew/bin/python3.15 \
    /usr/local/bin/python3.15; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_COMMAND="$(command -v "$candidate")"
        break
    fi
done

if [[ -z "$PYTHON_COMMAND" ]]; then
    echo "Python 3.15 was not found."
    echo "Install it from https://www.python.org/downloads/macos/"
    echo
    read -r -p "Press Return to close this window..." _response
    exit 1
fi

if PYTHON_COMMAND="$PYTHON_COMMAND" /bin/bash "$PROJECT_DIRECTORY/install.sh"; then
    echo
    echo "Installation completed."
    echo "You can now double-click 'Start on macOS.command'."
    exit_status=0
else
    exit_status=$?
    echo
    echo "Installation failed. Review the messages above."
fi

echo
read -r -p "Press Return to close this window..." _response
exit "$exit_status"
