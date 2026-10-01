#!/usr/bin/env bash
# ===  KS ToolBox — launch (Linux / macOS)  ===
# First run auto-creates the venv + installs deps; after that it just opens.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
    echo "First run - setting up KS ToolBox (one time)..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/python main.py
