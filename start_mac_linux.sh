#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if [ "$(uname -s)" = "Darwin" ]; then
    for candidate in \
        python3.15 \
        /Library/Frameworks/Python.framework/Versions/3.15/bin/python3.15 \
        /opt/homebrew/bin/python3.15 \
        /usr/local/bin/python3.15; do
        if command -v "$candidate" >/dev/null 2>&1; then
            exec "$candidate" run.py
        fi
    done
fi

exec python3 run.py
