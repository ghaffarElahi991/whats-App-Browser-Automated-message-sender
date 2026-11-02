#!/bin/bash

set -u

PROJECT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIRECTORY" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.10 or newer was not found."
    echo "Run 'Install on macOS.command' first."
    echo
    read -r -p "Press Return to close this window..." _response
    exit 1
fi

python3 run.py
exit_status=$?

if [[ "$exit_status" -ne 0 ]]; then
    echo
    echo "The application exited with an error."
    read -r -p "Press Return to close this window..." _response
fi

exit "$exit_status"
