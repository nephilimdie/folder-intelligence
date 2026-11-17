#!/bin/bash
# 🤖 folderIntelligence - Avvia chat con AI locale
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" __main__.py "${1:-$HOME/Downloads}" --interactive
