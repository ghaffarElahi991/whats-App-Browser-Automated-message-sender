#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIRECTORY"

PYTHON_COMMAND="${PYTHON_COMMAND:-python3}"

if ! command -v "$PYTHON_COMMAND" >/dev/null 2>&1; then
    echo "Error: Python 3.10 or newer was not found." >&2
    echo "macOS: install Python from https://www.python.org/downloads/macos/" >&2
    exit 1
fi

echo "Preparing the Python environment..."
"$PYTHON_COMMAND" run.py --install

echo "Running non-sending verification tests..."
"$PYTHON_COMMAND" run.py --test

case "$(uname -s)" in
    Darwin)
        if [[ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]] \
            && [[ ! -x "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]]; then
            echo "Warning: Google Chrome was not found." >&2
            echo "Install it from https://www.google.com/chrome/ before sending." >&2
        fi
        ;;
    Linux)
        if ! command -v google-chrome >/dev/null 2>&1 \
            && ! command -v google-chrome-stable >/dev/null 2>&1 \
            && ! command -v chromium >/dev/null 2>&1 \
            && ! command -v chromium-browser >/dev/null 2>&1; then
            echo "Warning: Google Chrome or Chromium was not found." >&2
            echo "Install a supported browser before sending." >&2
        fi
        ;;
    *)
        echo "Warning: install.sh is intended for macOS and Linux." >&2
        ;;
esac

echo
echo "Installation completed successfully."
echo "Start the application with: $PYTHON_COMMAND run.py"
