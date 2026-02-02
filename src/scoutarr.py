#!/usr/bin/env python3

import socket
socket.has_ipv6 = False

import sys
import glob
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

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

CONFIG_DIR = Path("/config")
FALLBACK_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def log(msg: str):
    print(msg, flush=True)


def load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_config_files() -> List[Path]:
    if CONFIG_DIR.exists():
        files = sorted(Path(p) for p in glob.glob(str(CONFIG_DIR / "*.y*ml")))
        if files:
            return files

    if FALLBACK_CONFIG_DIR.exists():
        files = sorted(Path(p) for p in glob.glob(str(FALLBACK_CONFIG_DIR / "*.y*ml")))
        if files:
            return files

    return []


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

        weekly_ranked: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
        for meta in weekly_list:
            key = week_key_from_title(meta.get("title", ""))
            if key:
                weekly_ranked.append((key, meta))

        weekly_ranked.sort(key=lambda x: x[0], reverse=True)

        current_meta = weekly_ranked[0][1] if len(weekly_ranked) >= 1 else None
        prev_meta = weekly_ranked[1][1] if len(weekly_ranked) >= 2 else None

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

        if current_meta and need_weekly:
            pl = lb_get_playlist(cfg, current_meta["mbid"], user_agent=USER_AGENT)
            week_id = build_week_id_from_title(pl.get("title", ""))

            contract["weekly"]["current"] = {
                "mbid": current_meta["mbid"],
                "title": pl.get("title"),
                "week_id": week_id,
                "tracks": lb_extract_tracks_from_playlist(pl),
            }

            artists = lb_extract_artists_from_playlist(pl, source="weekly-exploration")
            for a in artists:
                entry = contract["artists"].setdefault(
                    a["mbid"], {"name": a["name"], "sources": set()}
                )
                entry["sources"].add("weekly-exploration")

            log(f"→ Weekly(current): {pl.get('title')}")

        if plex_enabled and prev_meta:
            pl = lb_get_playlist(cfg, prev_meta["mbid"], user_agent=USER_AGENT)
            week_id = build_week_id_from_title(pl.get("title", ""))

            contract["weekly"]["previous"] = {
                "mbid": prev_meta["mbid"],
                "title": pl.get("title"),
                "week_id": week_id,
                "tracks": lb_extract_tracks_from_playlist(pl),
            }

            log(f"→ Weekly(previous): {pl.get('title')}")
        elif plex_enabled:
            log("⚠ Plex enabled but no previous week available (need 2 weeks).")

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

        if plex_enabled and contract["weekly"]["previous"]:
            plex_run_playlists(cfg, contract, user_agent=USER_AGENT)

        log("")

    log("Done.\n")


if __name__ == "__main__":
    main()
