from fastapi import FastAPI, Request
import logging
import json

QUEUE_FILE = "/tmp/scoutarr.queue"
app = FastAPI()
logging.basicConfig(level=logging.INFO)


@app.post("/webhook")
async def webhook(request: Request):

    try:
        payload = await request.json()
    except:
        form = await request.form()
        if "payload" not in form:
            return {"status": "ignored"}
        payload = json.loads(form["payload"])


    event = payload.get("event")
    if event != "media.rate":
        return {"status": "ignored"}

    logging.info("[WEBHOOK DEBUG] Full payload below:")
    logging.info(json.dumps(payload, indent=2))

    metadata = payload.get("Metadata", {})

    title = metadata.get("title", "unknown")

    artist = "unknown"
    if metadata.get("grandparentTitle"):
        artist = metadata.get("grandparentTitle")
    elif metadata.get("originalTitle"):
        artist = metadata.get("originalTitle")

    track_mbid = None
    for guid in metadata.get("Guid", []):
        gid = guid.get("id", "")
        if gid.startswith("mbid://"):
            track_mbid = gid.replace("mbid://", "")
            break

    if not track_mbid:
        track_mbid = "NO_MBID"
        logging.info(f"[WEBHOOK] ⚠️ no MBID, using fallback: {artist} - {title}")


    rating = metadata.get("userRating")
    if rating is not None:
        rating = float(rating)

    if rating == 10.0:
        score = 1
    elif rating == 2.0:
        score = -1
    else:
        score = 0

    plex_user = payload.get("Account", {}).get("title", "")

    logging.info(f"[WEBHOOK] ⭐ {artist} - {title} → {score} [{plex_user}]")

    with open(QUEUE_FILE, "a") as f:
        f.write(f"{track_mbid}|{score}|{plex_user}|{artist}|{title}\n")

    return {"status": "ok"}
