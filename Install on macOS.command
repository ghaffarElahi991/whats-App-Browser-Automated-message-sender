#!/bin/bash

set -u

PROJECT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIRECTORY" || exit 1

clear
echo "Mayfair WhatsApp Web Sender"
echo "macOS installer"
echo

if /bin/bash "$PROJECT_DIRECTORY/install.sh"; then
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
