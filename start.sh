#!/bin/bash
# Quick start script for Weekly Report Generator

echo "🚀 Starting Weekly Report Generator..."
echo ""
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-5000}
export HOST PORT

# Local dev defaults
export DEBUG=${DEBUG:-1}

echo "📝 Open your browser and go to: http://${HOST}:${PORT}"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Use venv Python if present (has python-dotenv); else system python3
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python app.py
else
  python3 app.py
fi
