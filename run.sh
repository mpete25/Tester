#!/bin/bash
# Start Twitch Highlight Finder and open it in the browser.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/streamlit ]; then
  echo "Programmet er ikke sat op endnu. Kør først:  ./scripts/setup_macos.sh"
  exit 1
fi
PORT="${THF_PORT:-8501}"
URL="http://localhost:$PORT"
echo "Twitch Highlight Finder kører på $URL  (stop med Ctrl+C)"
if [ "${THF_NO_BROWSER:-}" != "1" ]; then
  ( sleep 3; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) >/dev/null 2>&1 &
fi
exec .venv/bin/streamlit run app.py --server.headless true --server.address localhost --server.port "$PORT" "$@"
