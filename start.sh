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

echo "Starting queue worker..."
python3 /app/src/queue_worker.py &

echo "Starting API..."
exec uvicorn src.webhook:app --host 0.0.0.0 --port 8787
