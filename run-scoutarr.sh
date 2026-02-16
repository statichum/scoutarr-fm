#!/usr/bin/env bash

LOG="/docker/scoutarr-fm/logs/cron.log"
START_TIME=$(date +%s)
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# Start banner
echo "===== SCOUTARR RUN START $STAMP =====" | tee -a "$LOG"

cd /docker/scoutarr-fm || exit 1

# Run container, stream to terminal + log
docker compose run --rm scoutarr 2>&1 | tee -a "$LOG"
EXIT_CODE=${PIPESTATUS[0]}

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# End banner
echo "===== SCOUTARR RUN END $(date '+%Y-%m-%d %H:%M:%S') (exit: $EXIT_CODE, duration: ${DURATION}s) =====" | tee -a "$LOG"
echo | tee -a "$LOG"

exit $EXIT_CODE
