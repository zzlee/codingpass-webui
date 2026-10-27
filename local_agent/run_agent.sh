#!/usr/bin/env bash
# CodingPass Local Agent Launcher (Linux / macOS)
cd "$(dirname "$0")"
echo "=== CodingPass Local Agent ==="
if command -v python3 &>/dev/null; then
    python3 agent.py
elif command -v python &>/dev/null; then
    python agent.py
else
    echo "Error: Python 3 is required to run CodingPass Local Agent."
    exit 1
fi
