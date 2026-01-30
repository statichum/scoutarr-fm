#!/usr/bin/env python3

import socket
socket.has_ipv6 = False

import sys
import time
import glob
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional

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


def log(msg: str):
    print(msg, flush=True)


def load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_config_files() -> List[Path]:
    # Prefer mounted /config
    if CONFIG_DIR.exists():
        files = sorted(Path(p) for p in glob.glob(str(CONFIG_DIR / "*.y*ml")))
        if files:
            return files

    # Fallback to repo local /config (handy for non-docker runs)
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
    LB title looks like:
      "Weekly Exploration for chris41304130, week of 2026-01-26 Mon"
    We’ll keep a stable "YYYY-Www" derived from the date.
    """
    import re
    from datetime import date

    m = re.search(r"week of (\d{4}-\d{2}-\d{2})", title)
    if not m:
        return None
    y, mo, d = map(int, m.group(1).split("-"))
    iso = date(y, mo, d).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def main():
    log("\nscoutarr.fm — Core + Sidecars\n")

    cfg_files = list_config_files()
    if not cfg_files:
        log("✗ No config files found in /config (or ./config fallback).")
        sys.exit(1)

    log(f"→ Found {len(cfg_files)} config file(s)\n")

    for cfg_path in cfg_files:
        log("=" * 80)
        log(f"Config: {cfg_path.name}")
        log("=" * 80)

        cfg = load_yaml(cfg_path)
        dry_run = cfg.get("recommender", {}).get("dry_run", True)

        # Decide what we need to fetch from ListenBrainz
        lidarr_enabled = enabled(cfg, "lidarr", "enabled", default=False)
        plex_enabled = enabled(cfg, "plex", "enabled", default=False)

        weekly_enabled = enabled(cfg, "listenbrainz", "weekly-exploration", default=False)
        cf_enabled = enabled(cfg, "listenbrainz", "collaborative-filtering", default=False)

        need_current_week = lidarr_enabled and weekly_enabled
        need_prev_week = plex_enabled and cfg.get("plex", {}).get("last-week-exp", False)

        if not (need_current_week or need_prev_week or (lidarr_enabled and cf_enabled)):
            log("→ Nothing enabled that requires ListenBrainz data (skipping).")
            continue

        # Fetch weekly exploration playlist list once (only if we need weekly)
        weekly_list = []
        if need_current_week or need_prev_week:
            weekly_list = lb_get_weekly_exploration_playlists(cfg, user_agent=USER_AGENT)
            if not weekly_list:
                log("⚠ No Weekly Exploration playlists available for this user.")
                # You might still want CF-only
                if not (lidarr_enabled and cf_enabled):
                    continue

        # Pick current and previous playlists (newest first)
        current_meta = weekly_list[0] if len(weekly_list) >= 1 else None
        prev_meta = weekly_list[1] if len(weekly_list) >= 2 else None

        # Shared data contract object (what sidecars consume)
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
            "artists": {
                # merged pool for Lidarr sidecar
                # key: artist_mbid -> {name, sources:set()}
            },
        }

        # Pull CURRENT week playlist details only if needed
        if need_current_week and current_meta:
            pl = lb_get_playlist(cfg, current_meta["mbid"], user_agent=USER_AGENT)
            week_id = build_week_id_from_title(pl.get("title", "")) or "unknown-week"
            contract["weekly"]["current"] = {
                "mbid": current_meta["mbid"],
                "title": pl.get("title"),
                "week_id": week_id,
                "tracks": lb_extract_tracks_from_playlist(pl),
            }

            # Weekly artists -> artist pool
            weekly_artists = lb_extract_artists_from_playlist(pl, source="weekly-exploration")
            for a in weekly_artists:
                entry = contract["artists"].setdefault(a["mbid"], {"name": a["name"], "sources": set()})
                entry["name"] = a["name"]
                entry["sources"].add("weekly-exploration")

            log(f"→ Weekly(current): {pl.get('title')}")

        # Pull PREVIOUS week playlist details only if needed (for Plex later)
        if need_prev_week and prev_meta:
            pl = lb_get_playlist(cfg, prev_meta["mbid"], user_agent=USER_AGENT)
            week_id = build_week_id_from_title(pl.get("title", "")) or "unknown-week"
            contract["weekly"]["previous"] = {
                "mbid": prev_meta["mbid"],
                "title": pl.get("title"),
                "week_id": week_id,
                "tracks": lb_extract_tracks_from_playlist(pl),
            }
            log(f"→ Weekly(previous): {pl.get('title')}")
        elif need_prev_week and not prev_meta:
            log("⚠ Plex enabled but no 'previous week' playlist exists yet (need 2 weeks of data).")

        # CF artists only if Lidarr enabled + CF enabled
        if lidarr_enabled and cf_enabled:
            cf_artists = lb_get_cf_artists(cfg, user_agent=USER_AGENT)
            for a in cf_artists:
                entry = contract["artists"].setdefault(a["mbid"], {"name": a["name"], "sources": set()})
                entry["name"] = a["name"]
                entry["sources"].add("collaborative-filtering")

            log(f"→ CF artists: {len(cf_artists)}")

        # --- Call sidecars conditionally ---

        # Lidarr sidecar
        if lidarr_enabled and contract["artists"]:
            lidarr_run_import(cfg, contract, user_agent=USER_AGENT)
        elif lidarr_enabled:
            log("⚠ Lidarr enabled but artist pool is empty (nothing to import).")

        # Plex sidecar (previous week → Plex playlists)
        if plex_enabled and contract["weekly"]["previous"]:
            try:
                plex_run_playlists(cfg, contract, user_agent=USER_AGENT)
            except Exception as e:
                log(f"✗ Plex sidecar failed: {e}")
        elif plex_enabled:
            log("⚠ Plex enabled but no 'previous week' playlist exists yet (need 2 weeks of data).")

        log("")  # spacing between configs

    log("Done.\n")


if __name__ == "__main__":
    main()
