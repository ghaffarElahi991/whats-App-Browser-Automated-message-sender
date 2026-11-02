#!/bin/bash

set -u

PROJECT_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIRECTORY" || exit 1

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
    echo "Run 'Install on macOS.command' first."
    echo
    read -r -p "Press Return to close this window..." _response
    exit 1
fi

"$PYTHON_COMMAND" run.py
exit_status=$?

if [[ "$exit_status" -ne 0 ]]; then
    echo
    echo "The application exited with an error."
    read -r -p "Press Return to close this window..." _response
fi

exit "$exit_status"
