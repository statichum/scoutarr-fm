#!/usr/bin/env bash
set -e

echo "Starting Scoutarr..."

# Start cron
echo "Starting cron..."
cron&

# -------------------------
# CRON CHECK
# -------------------------
echo "Running cron sanity check..."

if ! pgrep cron > /dev/null; then
  echo "[CRON CHECK] cron is NOT running"
  exit 1
else
  echo "[CRON CHECK] cron process is running"
fi

if [ ! -f /etc/cron.d/scoutarr ]; then
  echo "[CRON CHECK] cron file missing"
  exit 1
else
  echo "[CRON CHECK] cron file exists"
fi

perm=$(stat -c "%a" /etc/cron.d/scoutarr)
if [ "$perm" != "644" ]; then
  echo "[CRON CHECK] cron file permissions incorrect ($perm)"
  exit 1
else
  echo "[CRON CHECK] cron file permissions OK (644)"
fi

echo "[CRON CHECK] Loaded jobs:"
cat /etc/cron.d/scoutarr

echo "[CRON CHECK] Running test job..."
python3 - <<EOF
print("[CRON TEST] Python execution works")
EOF
echo "[CRON CHECK] test execution complete"

# -------------------------
# Continue startup
# -------------------------

echo "Running initial full sync..."
python3 /app/src/sync_ratings.py &
SYNC_PID=$!
echo "[STARTUP] sync_ratings.py started (pid=$SYNC_PID)"

echo "Starting queue worker..."
python3 /app/src/queue_worker.py &
QUEUE_PID=$!
echo "[STARTUP] queue_worker.py started (pid=$QUEUE_PID)"

echo "Starting API..."
uvicorn src.webhook:app --host 0.0.0.0 --port 8787 &
UVICORN_PID=$!
echo "[STARTUP] uvicorn started (pid=$UVICORN_PID)"

wait -n

echo "[ERROR] A child process exited"

for pid in $SYNC_PID $QUEUE_PID $UVICORN_PID; do
    if kill -0 "$pid" 2>/dev/null; then
        echo "[STATUS] pid=$pid is still running"
    else
        echo "[STATUS] pid=$pid has exited"
    fi
done

echo "[PROCESS LIST]"
ps aux

echo "[SHUTDOWN] Terminating remaining processes..."

kill $SYNC_PID $QUEUE_PID $UVICORN_PID 2>/dev/null || true

wait || true
