#!/usr/bin/env python3

import socket
socket.has_ipv6 = False
import sys
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from config_loader import list_config_files
from state import load_state, save_state
from listenbrainz_core import (
    lb_get_weekly_exploration_playlists,
    lb_get_playlist,
    lb_extract_artists_from_playlist,
    lb_extract_tracks_from_playlist,
    lb_get_cf_artists,
)

from lidarr_sidecar import lidarr_run_import
from plex_sidecar import plex_run_playlists

USER_AGENT = "scoutarr.fm/0.6 (christuckey.uk)"



# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

from datetime import datetime

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {msg}", flush=True)


def load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}




def enabled(cfg: Dict[str, Any], *keys, default=False) -> bool:
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return bool(cur)


def build_week_id_from_title(title: str) -> Optional[str]:
    """
    "Weekly Exploration for user, week of 2026-01-26 Mon"
    -> "2026-W05"
    """
    import re
    from datetime import date

    m = re.search(r"week of (\d{4}-\d{2}-\d{2})", title)
    if not m:
        return None

    y, mo, d = map(int, m.group(1).split("-"))
    iso = date(y, mo, d).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_key_from_title(title: str) -> Optional[Tuple[int, int]]:
    import re
    from datetime import date

    m = re.search(r"week of (\d{4}-\d{2}-\d{2})", title)
    if not m:
        return None

    y, mo, d = map(int, m.group(1).split("-"))
    iso = date(y, mo, d).isocalendar()
    return (iso.year, iso.week)


def normalize_playlist_id(identifier: str) -> str:
    return identifier.rstrip("/").split("/")[-1]

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    log("\nscoutarr.fm — Core + Sidecars\n")

    cfg_files = list_config_files()
    if not cfg_files:
        log("✗ No config files found.")
        sys.exit(1)

    log(f"→ Found {len(cfg_files)} config file(s)\n")

    for cfg_path in cfg_files:
        log("=" * 80)
        log(f"Config: {cfg_path.name}")
        log("=" * 80)

        cfg = load_yaml(cfg_path)
        dry_run = cfg.get("recommender", {}).get("dry_run", True)

        lidarr_enabled = enabled(cfg, "lidarr", "enabled")
        plex_enabled = enabled(cfg, "plex", "enabled")

        weekly_enabled = enabled(cfg, "listenbrainz", "weekly-exploration")
        cf_enabled = enabled(cfg, "listenbrainz", "collaborative-filtering")

        need_weekly = weekly_enabled
        need_cf = lidarr_enabled and cf_enabled

        if not (need_weekly or need_cf):
            log("→ Nothing enabled that requires ListenBrainz data.")
            continue

        weekly_list = []
        if weekly_enabled:
            weekly_list = lb_get_weekly_exploration_playlists(cfg, user_agent=USER_AGENT)
            if not weekly_list:
                log("⚠ No Weekly Exploration playlists available.")
                continue

        weekly_ranked: List[Tuple[Tuple[int, int], str, Dict[str, Any]]] = []
        for meta in weekly_list:
            key = week_key_from_title(meta.get("title", ""))
            if key:
                weekly_ranked.append((key, meta.get("date", ""), meta))

        weekly_ranked.sort(
            key=lambda x: (x[0], x[1]),
            reverse=True
        )


        if not weekly_ranked:
            log("⚠ No valid Weekly Exploration playlists found.")
            continue

        state = load_state()

        username = cfg.get("listenbrainz", {}).get("username")

        user_state = state.setdefault(username, {})

        playlist_state = user_state.setdefault("weekly_playlists", {})

        created_plex_weeks = set(
            user_state.setdefault("created_plex_weeks", [])
        )

        imported_lidarr_weeks = set(
            user_state.setdefault("imported_lidarr_weeks", [])
        )

        weekly_unique = []

        seen_weeks = set()

        for _, _, meta in weekly_ranked:
            week_id = build_week_id_from_title(meta.get("title", ""))
            if not week_id:
                continue

            if week_id in seen_weeks:
                continue

            seen_weeks.add(week_id)
            weekly_unique.append(meta)

        current_meta = weekly_unique[0] if len(weekly_unique) >= 1 else None

        new_playlists = []

        for meta in weekly_list:
            playlist_id = meta["mbid"]

            if playlist_id not in playlist_state:
                week_id = build_week_id_from_title(meta.get("title", ""))

                playlist_state[playlist_id] = {
                    "week_id": week_id,
                    "title": meta.get("title"),
                    "imported_to_lidarr": False,
                    "plex_created": False,
                    "first_seen": meta.get("date"),
                }

                new_playlists.append(playlist_id)

        log(f"→ Found {len(new_playlists)} new playlist(s)")

        contract: Dict[str, Any] = {
            "config_name": cfg_path.name,
            "dry_run": dry_run,
            "listenbrainz": {
                "username": cfg.get("listenbrainz", {}).get("username"),
            },
            "weekly": {
                "current": None,
                "previous": None,
            },
            "artists": {},
        }

        current_week_id = None

        if current_meta:
            current_week_id = build_week_id_from_title(
                current_meta.get("title", "")
            )

        if (
            current_meta
            and need_weekly
            and current_week_id not in imported_lidarr_weeks
        ):

            pl = lb_get_playlist(
                cfg,
                current_meta["mbid"],
                user_agent=USER_AGENT
            )

            week_id = build_week_id_from_title(pl.get("title", ""))

            contract["weekly"]["current"] = {
                "mbid": current_meta["mbid"],
                "title": pl.get("title"),
                "week_id": week_id,
                "tracks": lb_extract_tracks_from_playlist(pl),
            }

            artists = lb_extract_artists_from_playlist(
                pl,
                source="weekly-exploration"
            )

            for a in artists:
                entry = contract["artists"].setdefault(
                    a["mbid"],
                    {"name": a["name"], "sources": set()}
                )

                entry["sources"].add("weekly-exploration")

            log(f"→ Weekly(current): {pl.get('title')}")

        if plex_enabled:
            current_week_id = None

            if current_meta:
                current_week_id = build_week_id_from_title(
                    current_meta.get("title", "")
                )

            for playlist_id, info in playlist_state.items():

                week_id = info.get("week_id")

                if not week_id:
                    continue

                if week_id == current_week_id:
                    continue

                if week_id in created_plex_weeks:
                    continue

                log(f"→ Creating Plex playlist for archived week: {week_id}")

                pl = lb_get_playlist(
                    cfg,
                    playlist_id,
                    user_agent=USER_AGENT
                )

                contract["weekly"]["previous"] = {
                    "mbid": playlist_id,
                    "title": pl.get("title"),
                    "week_id": week_id,
                    "tracks": lb_extract_tracks_from_playlist(pl),
                }

                plex_run_playlists(cfg, contract, user_agent=USER_AGENT)

                created_plex_weeks.add(week_id)

                playlist_state[playlist_id]["plex_created"] = True

        if need_cf:
            cf_artists = lb_get_cf_artists(cfg, user_agent=USER_AGENT)
            for a in cf_artists:
                entry = contract["artists"].setdefault(
                    a["mbid"], {"name": a["name"], "sources": set()}
                )
                entry["sources"].add("collaborative-filtering")

            log(f"→ CF artists: {len(cf_artists)}")

        if lidarr_enabled and contract["artists"]:
            lidarr_run_import(cfg, contract, user_agent=USER_AGENT)

            if current_week_id:
                imported_lidarr_weeks.add(current_week_id)

            if current_meta:
                playlist_state[current_meta["mbid"]]["imported_to_lidarr"] = True

        user_state["created_plex_weeks"] = sorted(created_plex_weeks)

        user_state["imported_lidarr_weeks"] = sorted(
            imported_lidarr_weeks
        )

        save_state(state)

        log("→ Saved playlist state.")

        log("")

    log("Done.\n")

if __name__ == "__main__":
    main()
