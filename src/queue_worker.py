import time
import subprocess
import os

from datetime import datetime

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}", flush=True)


QUEUE_FILE = "/tmp/scoutarr.queue"

open(QUEUE_FILE, "a").close()

while True:
    if os.path.getsize(QUEUE_FILE) > 0:
        with open(QUEUE_FILE, "r") as f:
            lines = f.readlines()

        first = lines[0].strip()

        with open(QUEUE_FILE, "w") as f:
            f.writelines(lines[1:])

        parts = first.split("|")

        mbid = parts[0]
        score = parts[1]
        plex_user = parts[2]

        artist = "unknown"
        title = "unknown"

        if len(parts) >= 5:
            artist = parts[3]
            title = parts[4]
        log(f"[QUEUE WORKER] Processing: {mbid} ({score}) [{plex_user}]")

        subprocess.run([
            "python3",
            "/app/src/sync_ratings.py",
            "--single",
            mbid,
            score,
            plex_user,
            artist,
            title
        ])

    else:
        time.sleep(2)
