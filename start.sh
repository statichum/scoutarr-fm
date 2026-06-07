#!/usr/bin/env bash
set -e

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*"
}

log "Starting Scoutarr..."

# Start cron
log "Starting cron..."
cron &

cat <<'EOF'
   (
    \
     )
##-------->        Scoutarr.fm
     )
    /
   (
EOF


# -------------------------
# CRON CHECK
# -------------------------
log "Running cron sanity check..."

if ! pgrep cron > /dev/null; then
  log "[CRON CHECK] cron is NOT running"
  exit 1
else
  log "[CRON CHECK] cron process is running"
fi

if [ ! -f /etc/cron.d/scoutarr ]; then
  log "[CRON CHECK] cron file missing"
  exit 1
else
  log "[CRON CHECK] cron file exists"
fi

perm=$(stat -c "%a" /etc/cron.d/scoutarr)
if [ "$perm" != "644" ]; then
  log "[CRON CHECK] cron file permissions incorrect ($perm)"
  exit 1
else
  log "[CRON CHECK] cron file permissions OK (644)"
fi

log "[CRON CHECK] Loaded jobs:"
cat /etc/cron.d/scoutarr

log "[CRON CHECK] Running test job..."
python3 - <<EOF
print("[CRON TEST] Python execution works")
EOF
log "[CRON CHECK] test execution complete"

# -------------------------
# Continue startup
# -------------------------

log "Starting queue worker..."
python3 /app/src/queue_worker.py &
QUEUE_PID=$!
log "[STARTUP] queue_worker.py started (pid=$QUEUE_PID)"

log "Starting API..."
uvicorn src.webhook:app --host 0.0.0.0 --port 8787 &
UVICORN_PID=$!
log "[STARTUP] uvicorn started (pid=$UVICORN_PID)"

while true; do
    if ! kill -0 "$QUEUE_PID" 2>/dev/null; then
        log "[ERROR] queue_worker.py exited"
        break
    fi

    if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
        log "[ERROR] uvicorn exited"
        break
    fi

    sleep 1
done

for pid in $QUEUE_PID $UVICORN_PID; do
    if kill -0 "$pid" 2>/dev/null; then
        log "[STATUS] pid=$pid is still running"
    else
        log "[STATUS] pid=$pid has exited"
    fi
done

log "[PROCESS LIST]"
ps aux

log "[SHUTDOWN] Terminating remaining processes..."

kill $QUEUE_PID $UVICORN_PID 2>/dev/null || true

wait || true
