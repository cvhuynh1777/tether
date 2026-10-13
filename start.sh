#!/bin/sh
# Start the API in the background, wait until it's healthy, then run the UI
# in the foreground. If either process exits, the container stops and
# Container Apps restarts it.
set -e

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!

for i in $(seq 1 30); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "Backend failed to start" >&2
    exit 1
  fi
  sleep 1
done

exec python -m streamlit run frontend/app.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
