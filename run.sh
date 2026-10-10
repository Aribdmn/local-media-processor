#!/usr/bin/env bash
# Local Media Processor - single-click launcher (macOS / Linux)
set -e
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
    ./.venv/bin/python main.py
else
    uv run python main.py
fi
