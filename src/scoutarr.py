#!/usr/bin/env python3

import sys
import time
import yaml
import requests
from pathlib import Path
from collections import defaultdict

# --------------------------------------------------
# Paths / Constants
# --------------------------------------------------

CONFIG_PATH = Path("/config/config.yaml")
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

USER_AGENT = "scoutarr.fm/0.5 (christuckey.uk)"

LB_CF_RECORDING = "https://api.listenbrainz.org/1/cf/recommendation/user"
LB_CREATED_FOR = "https://api.listenbrainz.org/1/user/{user}/playlists/createdfor"
LB_PLAYLIST = "https://api.listenbrainz.org/1/playlist"

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def log(msg):
    print(msg, flush=True)


def load_config():
    if not CONFIG_PATH.exists():
        log("✗ Config file not found")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def lb_headers(token):
    return {
        "Authorization": f"Token {token}",
        "User-Agent": USER_AGENT,
    }


def mb_base(cfg):
    base = cfg.get("musicbrainz", {}).get(
        "musicbrainz_url",
        "https://musicbrainz.org"
    ).rstrip("/")
    return f"{base}/ws/2"


# --------------------------------------------------
# MusicBrainz helpers
# --------------------------------------------------

def get_primary_artist_from_recording(cfg, recording_mbid, retries=3):
    url = f"{mb_base(cfg)}/recording/{recording_mbid}"

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                url,
                params={"inc": "artist-credits", "fmt": "json"},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()

            if not data.get("artist-credit"):
                return None

            artist = data["artist-credit"][0]["artist"]
            return {
                "name": artist["name"],
                "mbid": artist["id"],
            }

        except Exception:
            log(f"⚠ MB lookup {recording_mbid} failed (retry {attempt}/{retries})")
            time.sleep(0.5)

    return None


# --------------------------------------------------
# Weekly Exploration
# --------------------------------------------------

def get_weekly_exploration_artists(cfg):
    token = cfg["listenbrainz"]["user_token"]
    user = cfg["listenbrainz"]["username"]

    log("→ Fetching Weekly Exploration playlist")

    r = requests.get(
        LB_CREATED_FOR.format(user=user),
        headers=lb_headers(token),
        timeout=20,
    )
    r.raise_for_status()

    playlists = r.json().get("playlists", [])
    weekly = None

    for p in playlists:
        meta = (
            p["playlist"]["extension"]
            .get("https://musicbrainz.org/doc/jspf#playlist", {})
            .get("additional_metadata", {})
            .get("algorithm_metadata", {})
        )
        if meta.get("source_patch") == "weekly-exploration":
            weekly = p["playlist"]
            break

    if not weekly:
        log("⚠ Weekly Exploration playlist not found")
        return []

    playlist_id = weekly["identifier"].split("/")[-1]

    r = requests.get(
        f"{LB_PLAYLIST}/{playlist_id}",
        headers=lb_headers(token),
        timeout=20,
    )
    r.raise_for_status()

    artists = []

    for track in r.json()["playlist"]["track"]:
        meta = track["extension"]["https://musicbrainz.org/doc/jspf#track"]
        for artist in meta.get("additional_metadata", {}).get("artists", []):
            if artist.get("artist_mbid"):
                artists.append({
                    "name": artist["artist_credit_name"],
                    "mbid": artist["artist_mbid"],
                    "source": "weekly-exploration",
                })

    return artists


# --------------------------------------------------
# Collaborative Filtering
# --------------------------------------------------

def get_cf_artists(cfg):
    token = cfg["listenbrainz"]["user_token"]
    user = cfg["listenbrainz"]["username"]

    log("→ Fetching CF recommended recordings")

    r = requests.get(
        f"{LB_CF_RECORDING}/{user}/recording",
        headers=lb_headers(token),
        params={"count": 100},
        timeout=20,
    )
    r.raise_for_status()

    recordings = r.json()["payload"]["mbids"]
    artists = []

    for item in recordings:
        artist = get_primary_artist_from_recording(cfg, item["recording_mbid"])
        if artist:
            artists.append({
                "name": artist["name"],
                "mbid": artist["mbid"],
                "source": "collaborative-filtering",
            })
        time.sleep(0.2)

    return artists


# --------------------------------------------------
# Lidarr helpers
# --------------------------------------------------

def lidarr_headers(cfg):
    return {
        "X-Api-Key": cfg["lidarr"]["api_key"],
        "User-Agent": USER_AGENT,
    }


def lidarr_get(cfg, path):
    r = requests.get(
        f'{cfg["lidarr"]["url"]}/api/v1/{path}',
        headers=lidarr_headers(cfg),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def lidarr_lookup_artist(cfg, mbid):
    r = requests.get(
        f'{cfg["lidarr"]["url"]}/api/v1/artist/lookup',
        headers=lidarr_headers(cfg),
        params={"term": f"mbid:{mbid}"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if data else None


def resolve_lidarr_ids(cfg):
    qp = lidarr_get(cfg, "qualityprofile")
    mp = lidarr_get(cfg, "metadataprofile")
    tags = lidarr_get(cfg, "tag")

    qp_id = next(x["id"] for x in qp if x["name"] == cfg["lidarr"]["quality_profile"])
    mp_id = next(x["id"] for x in mp if x["name"] == cfg["lidarr"]["metadata_profile"])
    tag_ids = [
        t["id"] for t in tags if t["label"] in cfg["lidarr"].get("tags", [])
    ]

    return qp_id, mp_id, tag_ids


def lidarr_add_artist(cfg, artist, qp_id, mp_id, tag_ids):
    payload = {
        "artistName": artist["artistName"],
        "foreignArtistId": artist["foreignArtistId"],
        "qualityProfileId": qp_id,
        "metadataProfileId": mp_id,
        "rootFolderPath": cfg["lidarr"]["root_folder"],
        "monitored": True,
        "monitorNewItems": cfg["lidarr"]["monitor_new"],
        "tags": tag_ids,
        "addOptions": {
            "monitor": cfg["lidarr"]["monitor_existing"],
            "searchForMissingAlbums": cfg["lidarr"]["search_on_add"],
        },
    }

    r = requests.post(
        f'{cfg["lidarr"]["url"]}/api/v1/artist',
        headers=lidarr_headers(cfg),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    log("\nscoutarr.fm — Playlist + CF Edition\n")

    cfg = load_config()
    dry_run = cfg.get("recommender", {}).get("dry_run", True)

    weekly_enabled = cfg["listenbrainz"].get("weekly-exploration", False)
    cf_enabled = cfg["listenbrainz"].get("collaborative-filtering", False)

    artist_pool = defaultdict(lambda: {
        "name": None,
        "count": 0,
        "sources": set(),
    })

    if weekly_enabled:
        for a in get_weekly_exploration_artists(cfg):
            e = artist_pool[a["mbid"]]
            e["name"] = a["name"]
            e["count"] += 1
            e["sources"].add(a["source"])

    if cf_enabled:
        for a in get_cf_artists(cfg):
            e = artist_pool[a["mbid"]]
            e["name"] = a["name"]
            e["count"] += 1
            e["sources"].add(a["source"])

    ranked = sorted(
        artist_pool.items(),
        key=lambda x: x[1]["count"],
        reverse=True,
    )

    log(f"\nFinal artist list: {len(ranked)}")

    if dry_run:
        log("\nDRY RUN — no Lidarr changes will be made\n")
        for i, (mbid, data) in enumerate(ranked, 1):
            log(f"{i:>3}. {data['name']}")
            log(f"     MBID    : {mbid}")
            log(f"     Hits    : {data['count']}")
            log(f"     Sources : {', '.join(sorted(data['sources']))}\n")
        return

    if not cfg["lidarr"]["enabled"]:
        log("Lidarr integration disabled")
        return

    log("\nLIVE MODE — importing into Lidarr\n")

    qp_id, mp_id, tag_ids = resolve_lidarr_ids(cfg)

    for mbid, data in ranked:
        lookup = lidarr_lookup_artist(cfg, mbid)

        if lookup and lookup.get("id"):
            log(f"SKIP ✓ {data['name']}")
            continue

        log(f"ADD  + {data['name']}")
        lidarr_add_artist(cfg, lookup, qp_id, mp_id, tag_ids)
        time.sleep(0.3)

    log("\n✓ Lidarr import complete.\n")


if __name__ == "__main__":
    main()
