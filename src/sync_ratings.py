#!/usr/bin/env python3

import requests
import xml.etree.ElementTree as ET
import yaml
from datetime import datetime
import time
import os
import argparse

import logging
logging.basicConfig(level=logging.INFO)

MB_HEADERS = {
    "User-Agent": "Scoutarr-fm/1.0"
}

from config_loader import list_config_files

def get_mb_sleep(mb_url):
    if "musicbrainz.org" in mb_url:
        return 1
    return 0

# -------------------------
# Logging
# -------------------------
def log(msg):
    print(msg, flush=True)


def header(config_name):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log("")
    log("============================================================")
    log(f" Plex → ListenBrainz Rating Sync — {now}")
    log(f" Config: {config_name}")
    log("============================================================")
    log("")


# -------------------------
# Fallback recording search
# -------------------------

def fallback_recording_search(mb_url, artist, title):
    try:
        search_url = f"{mb_url}/ws/2/recording"

        params = {
            "query": f'recording:"{title}" AND artist:"{artist}"',
            "fmt": "json"
        }

        r = requests.get(search_url, params=params, headers=MB_HEADERS, timeout=20)

        if r.status_code != 200:
            log(f"  → Search failed ({r.status_code})")
            return None

        data = r.json()
        recordings = data.get("recordings", [])

        if not recordings:
            log("  → No strict results, retrying relaxed search")

            params = {
                "query": f'recording:"{title}"',
                "fmt": "json"
            }

            r = requests.get(search_url, params=params, headers=MB_HEADERS, timeout=20)

            if r.status_code != 200:
                log(f"  → Relaxed search failed ({r.status_code})")
                return None

            data = r.json()
            recordings = data.get("recordings", [])

        if not recordings:
            log("  → No recordings returned")
            return None

        recordings.sort(key=lambda x: x.get("score", 0), reverse=True)

        top = recordings[0]

        rec_id = top.get("id")
        rec_title = top.get("title", "")
        rec_artist = " ".join(
            a.get("name", "") for a in top.get("artist-credit", [])
        )
        rec_score = top.get("score", 0)

        log(f"  → Top candidate: {rec_artist} - {rec_title} (score={rec_score})")

        if rec_score >= 80 and rec_id:
            return rec_id

        log("  → Top result score too low")
        return None

    except Exception as e:
        log(f"  → Fallback error: {e}")
        return None




def fallback_release_by_title(mb_url, track_mbid, title):
    try:
        search_url = f"{mb_url}/ws/2/release"
        params = {"query": title, "fmt": "json"}

        r = requests.get(search_url, params=params, headers=MB_HEADERS, timeout=10)
        data = r.json()

        for rel in data.get("releases", []):
            release_id = rel["id"]

            log(f"    → Checking release {release_id} (title fallback)")

            rel_url = f"{mb_url}/ws/2/release/{release_id}"
            rel_params = {"inc": "recordings", "fmt": "json"}

            r = requests.get(
                rel_url,
                params=rel_params,
                headers=MB_HEADERS,
                timeout=10
            )

            if r.status_code != 200:
                continue

            rel_data = r.json()

            for media in rel_data.get("media", []):
                for track in media.get("tracks", []):
                    if track.get("id") == track_mbid:
                        return track.get("recording", {}).get("id")

        return None

    except Exception as e:
        log(f"  → Title fallback error: {e}")
        return None


def fallback_artist_release_scan(mb_url, track_mbid, artist):
    try:
        r = requests.get(
            f"{mb_url}/ws/2/artist",
            params={"query": artist, "fmt": "json"},
            headers=MB_HEADERS,
            timeout=10
        )
        data = r.json()

        if not data.get("artists"):
            return None

        artist_id = data["artists"][0]["id"]


        r = requests.get(
            f"{mb_url}/ws/2/release",
            params={"artist": artist_id, "fmt": "json"},
            headers=MB_HEADERS,
            timeout=10
        )

        if r.status_code != 200:
            return None

        data = r.json()

        for rel in data.get("releases", []):
            release_id = rel["id"]

            log(f"    → Checking release {release_id} (artist fallback)")

            rel_url = f"{mb_url}/ws/2/release/{release_id}"
            rel_params = {"inc": "recordings", "fmt": "json"}

            r = requests.get(
                rel_url,
                params=rel_params,
                headers=MB_HEADERS,
                timeout=10
            )

            if r.status_code != 200:
                continue

            rel_data = r.json()


            for media in rel_data.get("media", []):
                for track in media.get("tracks", []):
                    if track.get("id") == track_mbid:
                        return track.get("recording", {}).get("id")

        return None

    except Exception as e:
        log(f"  → Artist fallback error: {e}")
        return None

def resolve_recording_from_tid(mb_url, track_mbid):
    try:
        log(f"  [DEBUG] resolve_recording_from_tid: querying tid:{track_mbid}")
        r = requests.get(
            f"{mb_url}/ws/2/recording",
            params={
                "query": f"tid:{track_mbid}",
                "fmt": "json"
            },
            headers=MB_HEADERS,
            timeout=10
        )

        if r.status_code != 200:
            log(f"  [DEBUG] resolve_recording_from_tid: non-200 status {r.status_code}")
            return None

        data = r.json()
        log(f"  [DEBUG] resolve_recording_from_tid: status={r.status_code}, recordings={len(data.get('recordings', []))}")
        recs = data.get("recordings", [])

        if not recs:
            return None

        if recs[0].get("score", 0) < 90:
            log(f"  [DEBUG] resolve_recording_from_tid: top score too low ({recs[0].get('score', 0)})")
            return None

        return recs[0]["id"]

    except Exception as e:
        log(f"  [DEBUG] resolve_recording_from_tid exception: {e}")
        return None

# -------------------------
# Process track
# -------------------------

def process_track(t, score, label, headers, mb_url, username, stats):
    title = t["title"]
    artist = t["artist"]
    album = t["album"]
    track_mbid = t["track_mbid"]
    stats["total"] += 1

    log(f"[{label}] {artist} - {title}")
    log(f"  Track MBID: {track_mbid}")
    log(f"  Resolving recording MBID from track MBID...")


    recording_mbid = None

    # -------------------------
    # NO MBID → search
    # -------------------------
    if track_mbid == "NO_MBID":
        log("  → NO_MBID: using recording search only")

        recording_mbid = fallback_recording_search(
            mb_url, artist, title
        )

        log(f"  → Recording MBID result: {recording_mbid}")

        if recording_mbid:
            log("  → ✅ Found via recording search")

    else:
        # -------------------------
        # Step 1: resolve via tid
        # -------------------------
        recording_mbid = resolve_recording_from_tid(mb_url, track_mbid)

        if recording_mbid:
            log("  → ✅ Found via track MBID (tid)")

        # -------------------------
        # Step 2: fallback (title → release → track MBID)
        # -------------------------
        if not recording_mbid:
            log("  → Fallback (title → release)...")

            recording_mbid = fallback_release_by_title(
                mb_url, track_mbid, title
            )

            if recording_mbid:
                log("  → ✅ Found via title fallback")

        # -------------------------
        # Step 3: fallback (artist → release → track MBID)
        # -------------------------
        if not recording_mbid:
            log("  → Fallback (artist → releases)...")

            recording_mbid = fallback_artist_release_scan(
                mb_url, track_mbid, artist
            )

            if recording_mbid:
                log("  → ✅ Found via artist fallback")

        # -------------------------
        # Step 4: fallback (recording search)
        # -------------------------
        if not recording_mbid:
            log("  → Fallback (recording search)...")

            recording_mbid = fallback_recording_search(
                mb_url, artist, title
            )

            if recording_mbid:
                log("  → ✅ Found via recording search")


    # -------------------------
    # Fail
    # -------------------------
    if not recording_mbid:
        stats["failed"] += 1
        log("  → ❌ Could not resolve recording MBID\n")
        return

    stats["resolved"] += 1
    log(f"  Recording MBID: {recording_mbid}")


    # -------------------------
    # Step 3: check LB
    # -------------------------
    try:
        url = f"https://api.listenbrainz.org/1/feedback/user/{username}/get-feedback-for-recordings"
        params = {"recording_mbids": recording_mbid}

        r = requests.get(url, headers=headers, params=params, timeout=10)
        log(f"  → LB lookup MBID: {recording_mbid}")

        if r.status_code != 200:
            log(f"  → ⚠️ Feedback fetch failed ({r.status_code})")
            current_score = 0
        else:
            data = r.json()
            items = data.get("feedback", [])
            current_score = items[0]["score"] if items else 0

    except Exception as e:
        log(f"  → ⚠️ Feedback error: {e}")
        current_score = 0

    # -------------------------
    # Step 4: update or skip
    # -------------------------

    if current_score == score:
        log(f"  → ✅ Already correct ({score})")

        sleep_time = get_mb_sleep(mb_url)
        log(f"  → Sleep: {sleep_time}s")
        time.sleep(sleep_time)

        log("")
        return


    log(f"  → 🔄 Updating {current_score} → {score}")

    payload = {
        "recording_mbid": recording_mbid,
        "score": score,
    }

    try:
        r = requests.post(
            "https://api.listenbrainz.org/1/feedback/recording-feedback",
            headers=headers,
            json=payload,
        )

        log(f"  [DEBUG] ListenBrainz response status: {r.status_code}")
        log(f"  [DEBUG] ListenBrainz response body: {r.text}")
        if r.status_code == 200:
            stats["updated"] += 1
            log(f"  → OK ({score})")
        else:
            log(f"  → ERROR {r.status_code}: {r.text}")

    except Exception as e:
        log(f"  → ERROR: {e}")

    log("")

    sleep_time = get_mb_sleep(mb_url)
    log(f"  → Sleep: {sleep_time}s")
    time.sleep(sleep_time)

# -------------------------
# Get Plex ID
# -------------------------
def get_music_section_id(plex_url, plex_token, library_name):
    url = f"{plex_url}/library/sections?X-Plex-Token={plex_token}"
    r = requests.get(url)
    root = ET.fromstring(r.content)

    for directory in root.findall("Directory"):
        if directory.get("title") == library_name:
            return directory.get("key")

    raise Exception("Music library not found")


# -------------------------
# Get tracks by rating
# -------------------------
def get_tracks(plex_url, plex_token, section_id, rating):
    url = f"{plex_url}/library/sections/{section_id}/all?type=10&userRating={rating}&X-Plex-Token={plex_token}"
    r = requests.get(url)
    root = ET.fromstring(r.content)

    tracks = []

    for track in root.findall("Track"):
        rating_key = track.get("ratingKey")

        meta_url = f"{plex_url}/library/metadata/{rating_key}?X-Plex-Token={plex_token}"
        meta = requests.get(meta_url)
        meta_root = ET.fromstring(meta.content)

        for t in meta_root.findall(".//Track"):
            title = t.get("title")
            artist = t.get("grandparentTitle")
            album = t.get("parentTitle")

            mbid = None
            for guid in t.findall("Guid"):
                if guid.get("id", "").startswith("mbid://"):
                    mbid = guid.get("id").replace("mbid://", "")
                    break

            if mbid:
                tracks.append({
                    "track_mbid": mbid,
                    "title": title,
                    "artist": artist,
                    "album": album
                })

    return tracks


# -------------------------
# Send feedback
# -------------------------
def send_feedback(tracks, score, label, headers, mb_url, username, stats):
    log(f"\n--- Syncing {label} ({len(tracks)} tracks) ---\n")


    for t in tracks:
        process_track(t, score, label, headers, mb_url, username, stats)


# -------------------------
# Run per config
# -------------------------
def run_config(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    name = os.path.basename(config_path)

    header(name)

    plex = config["plex"]
    lb = config["listenbrainz"]
    username = lb["username"]
    mb = config["musicbrainz"]

    plex_url = plex["plex-url"]
    plex_token = plex["plex-token"]
    library_name = plex["plex-library"]

    mb_url = mb["musicbrainz_url"]

    headers = {
        "Authorization": f"Token {lb['user_token']}",
        "Content-Type": "application/json",
    }

    section_id = get_music_section_id(plex_url, plex_token, library_name)

    log("Fetching ⭐⭐⭐⭐⭐ (loved) tracks from Plex...")
    five_star = get_tracks(plex_url, plex_token, section_id, 10)

    log("Fetching ⭐ (hated) tracks from Plex...")
    one_star = get_tracks(plex_url, plex_token, section_id, 2)

    log(f"\nFound {len(five_star)} loved tracks")
    log(f"Found {len(one_star)} hated tracks")

    love_stats = {"total": 0, "resolved": 0, "failed": 0, "updated": 0}
    hate_stats = {"total": 0, "resolved": 0, "failed": 0, "updated": 0}

    send_feedback(five_star, 1, "LOVE", headers, mb_url, username, love_stats)
    send_feedback(one_star, -1, "HATE", headers, mb_url, username, hate_stats)

    log("\n==================== SUMMARY ====================")
    log(f"Config: {name}\n")

    log("LOVE:")
    log(f"  Total tracks:        {love_stats['total']}")
    log(f"  MBID resolved:       {love_stats['resolved']}")
    log(f"  MBID failed:         {love_stats['failed']}")
    log(f"  Updates sent:        {love_stats['updated']}\n")

    log("HATE:")
    log(f"  Total tracks:        {hate_stats['total']}")
    log(f"  MBID resolved:       {hate_stats['resolved']}")
    log(f"  MBID failed:         {hate_stats['failed']}")
    log(f"  Updates sent:        {hate_stats['updated']}\n")

    log("=================================================\n")
    log("Sync complete.\n")

# -------------------------
# Main
# -------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--single", nargs=5, metavar=("MBID", "SCORE", "USER", "ARTIST", "TITLE"))
    args = parser.parse_args()

    log(f"[DEBUG] args.single = {args.single}")

    if args.single:
        mbid, score, user, artist, title = args.single
        run_single(mbid, int(score), user, artist, title)
        return

    configs = list_config_files()


    if not configs:
        log("No config files found.")
        return

    for config_path in sorted(configs):
        run_config(str(config_path))


# -------------------------
# Single Track Run
# -------------------------


def run_single(track_mbid, score, plex_user, artist, title):
    log(f"\n=== SINGLE TRACK MODE ===")
    log(f"Track MBID: {track_mbid}, Score: {score}\n")

    configs = list_config_files()


    for config_path in configs:
        log(f"[DEBUG] Loading config: {config_path}")
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)


        name = os.path.basename(config_path)
        log(f"\n--- Config: {name} ---")


        name = os.path.basename(config_path)

        config_plex_user = config.get("plex", {}).get("plex-username")

        if config_plex_user != plex_user:
            log(f"[DEBUG] Skipping config {name} (user mismatch: {config_plex_user} != {plex_user})")
            continue

        log(f"\n--- Config: {name} ---")



        lb = config["listenbrainz"]
        username = lb["username"]
        mb_url = config["musicbrainz"]["musicbrainz_url"]

        headers = {
            "Authorization": f"Token {lb['user_token']}",
            "Content-Type": "application/json",
        }

        stats = {"total": 0, "resolved": 0, "failed": 0, "updated": 0}

        t = {
            "track_mbid": track_mbid,
            "title": title,
            "artist": artist,
            "album": "unknown"
        }

        process_track(t, score, "WEBHOOK", headers, mb_url, username, stats)


if __name__ == "__main__":
    main()
