#!/bin/sh

# Keep the root command stable as platform-specific URL support grows.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR" || exit 1

if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON_BIN=python3
fi

exec "$PYTHON_BIN" -m trend_analysis.analyze_url "$@"
