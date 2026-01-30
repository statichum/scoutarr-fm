#!/usr/bin/env python3
"""
plex_sidecar.py

Consumes Scoutarr "contract" weekly playlist tracks and creates Plex playlists.

Logic (same as PoC):
  1) Track search (type=10) using the LB track title as query, score candidates
  2) If no match, search album (type=9) using LB album title, fetch album children,
     score tracks within album
Scoring uses Plex grandparentTitle + Plex originalTitle for artist matching.

Playlists:
  - Weekly archive: "{pl-name} – YYYY Wxx" (based on contract weekly.previous.week_id)
  - Optional "last week": "{pl-name} – Last Week" (recreated each run)
Retention:
  - Keeps latest N weekly playlists matching prefix, deletes older (never deletes Last Week)
"""

import datetime as dt
import difflib
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_THRESHOLD = 0.72

# ------------------ Normalisation ------------------

APOS = {"’": "'", "‘": "'", "`": "'", "ʼ": "'"}
SEP_RE = re.compile(r"[\/\-\–\—&:,;+]+")
NONWORD_RE = re.compile(r"[^\w\s']+")

def norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    for k, v in APOS.items():
        s = s.replace(k, v)
    s = s.casefold()
    s = SEP_RE.sub(" ", s)
    s = NONWORD_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def tokens(s: str) -> set:
    return set(norm(s).split())

def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0

def seq(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()

# ------------------ Data ------------------

@dataclass
class LBTrack:
    artist: str
    title: str
    album: str

@dataclass
class PlexTrack:
    rk: str
    title: str
    artist: str
    album: str
    original: str

@dataclass
class PlexPlaylist:
    rk: str
    title: str

# ------------------ Plex HTTP ------------------

def _plex_xml(base: str, token: str, path: str, params: Optional[Dict[str, str]] = None) -> ET.Element:
    h = {"X-Plex-Token": token, "Accept": "application/xml"}
    r = requests.get(base.rstrip("/") + path, headers=h, params=params, timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.text)

def _plex_machine_id(base: str, token: str) -> str:
    return _plex_xml(base, token, "/identity").attrib["machineIdentifier"]

def _plex_section_id(base: str, token: str, name: str) -> str:
    root = _plex_xml(base, token, "/library/sections")
    for d in root.findall("Directory"):
        if d.attrib.get("title", "").casefold() == name.casefold():
            return d.attrib["key"]
    raise RuntimeError(f"Plex library not found: {name}")

def _plex_search_track(base: str, token: str, sid: str, q: str) -> List[PlexTrack]:
    root = _plex_xml(base, token, f"/library/sections/{sid}/search", {"type": "10", "query": q})
    out: List[PlexTrack] = []
    for t in root.findall("Track"):
        out.append(PlexTrack(
            rk=t.attrib["ratingKey"],
            title=t.attrib.get("title", ""),
            artist=t.attrib.get("grandparentTitle", ""),
            album=t.attrib.get("parentTitle", ""),
            original=t.attrib.get("originalTitle", ""),
        ))
    return out

def _plex_search_album(base: str, token: str, sid: str, q: str) -> List[str]:
    root = _plex_xml(base, token, f"/library/sections/{sid}/search", {"type": "9", "query": q})
    return [d.attrib["ratingKey"] for d in root.findall("Directory")]

def _plex_album_tracks(base: str, token: str, album_rk: str) -> List[PlexTrack]:
    root = _plex_xml(base, token, f"/library/metadata/{album_rk}/children")
    out: List[PlexTrack] = []
    for t in root.findall("Track"):
        out.append(PlexTrack(
            rk=t.attrib["ratingKey"],
            title=t.attrib.get("title", ""),
            artist=t.attrib.get("grandparentTitle", ""),
            album=t.attrib.get("parentTitle", ""),
            original=t.attrib.get("originalTitle", ""),
        ))
    return out

def _plex_list_playlists(base: str, token: str) -> List[PlexPlaylist]:
    # This returns playlists of all types; we filter by playlistType="audio" when present.
    root = _plex_xml(base, token, "/playlists")
    out: List[PlexPlaylist] = []
    for p in root.findall("Playlist"):
        # Plex uses playlistType="audio" for music playlists
        if p.attrib.get("playlistType") and p.attrib.get("playlistType") != "audio":
            continue
        rk = p.attrib.get("ratingKey")
        title = p.attrib.get("title", "")
        if rk and title:
            out.append(PlexPlaylist(rk=rk, title=title))
    return out

def _plex_delete_playlist(base: str, token: str, playlist_rk: str) -> None:
    h = {"X-Plex-Token": token}
    r = requests.delete(base.rstrip("/") + f"/playlists/{playlist_rk}", headers=h, timeout=30)
    # 200/204 typical; if already gone, Plex may return 404, which we can ignore
    if r.status_code not in (200, 201, 204, 404):
        r.raise_for_status()

def _plex_create_playlist(base: str, token: str, machine_id: str, title: str, rating_keys: List[str]) -> None:
    uri = f"server://{machine_id}/com.plexapp.plugins.library/library/metadata/" + ",".join(rating_keys)
    h = {"X-Plex-Token": token}
    r = requests.post(
        base.rstrip("/") + "/playlists",
        headers=h,
        params={"type": "audio", "title": title, "smart": "0", "uri": uri},
        timeout=30,
    )
    r.raise_for_status()

# ------------------ Matching ------------------

def _score(lb: LBTrack, p: PlexTrack) -> float:
    title = 0.6 * jaccard(lb.title, p.title) + 0.4 * seq(lb.title, p.title)

    # artist: allow match against Plex grandparentTitle OR Plex originalTitle
    artist = max(
        0.6 * jaccard(lb.artist, p.artist) + 0.4 * seq(lb.artist, p.artist),
        0.6 * jaccard(lb.artist, p.original) + 0.4 * seq(lb.artist, p.original),
    )

    album = 0.6 * jaccard(lb.album, p.album) + 0.4 * seq(lb.album, p.album)
    return 0.5 * title + 0.35 * artist + 0.15 * album

def _best_match_for_track(
    base: str,
    token: str,
    sid: str,
    lb_track: LBTrack,
    threshold: float,
) -> Tuple[Optional[PlexTrack], float]:
    best: Optional[PlexTrack] = None
    best_s = 0.0

    # 1) Primary: track search by title
    for h in _plex_search_track(base, token, sid, lb_track.title):
        s = _score(lb_track, h)
        if s > best_s:
            best, best_s = h, s

    if best and best_s >= threshold:
        return best, best_s

    # 2) Fallback: album search → album children
    # (this is the Osees fix)
    for ar in _plex_search_album(base, token, sid, lb_track.album):
        for h in _plex_album_tracks(base, token, ar):
            s = _score(lb_track, h)
            if s > best_s:
                best, best_s = h, s

    if best and best_s >= threshold:
        return best, best_s

    return None, best_s

# ------------------ Playlist naming + retention ------------------

_WEEK_ID_RE = re.compile(r"(\d{4})-W(\d{2})")
_TITLE_WEEK_SUFFIX_RE = re.compile(r" – (\d{4}) W(\d{2})$")

def _week_id_to_title_suffix(week_id: str) -> Optional[str]:
    """
    contract uses "YYYY-Www"
    playlist wants " – YYYY Wxx"
    """
    m = _WEEK_ID_RE.fullmatch(week_id.strip())
    if not m:
        return None
    return f" – {m.group(1)} W{m.group(2)}"

def _parse_week_from_title(title: str) -> Optional[Tuple[int, int]]:
    m = _TITLE_WEEK_SUFFIX_RE.search(title)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

def _apply_retention(base: str, token: str, prefix: str, keep_weeks: int, verbose: bool) -> None:
    if keep_weeks <= 0:
        return

    pls = _plex_list_playlists(base, token)
    candidates: List[Tuple[int, int, PlexPlaylist]] = []

    for p in pls:
        if not p.title.startswith(prefix + " – "):
            continue
        # never consider "Last Week"
        if p.title.endswith(" – Last Week"):
            continue
        wk = _parse_week_from_title(p.title)
        if not wk:
            continue
        y, w = wk
        candidates.append((y, w, p))

    # Sort newest first
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # Keep first N, delete rest
    for i, (_, _, p) in enumerate(candidates):
        if i < keep_weeks:
            continue
        if verbose:
            print(f"  → Retention: deleting old playlist: {p.title}")
        _plex_delete_playlist(base, token, p.rk)

# ------------------ Public sidecar entry ------------------

def plex_run_playlists(cfg: Dict[str, Any], contract: Dict[str, Any], user_agent: str = "") -> None:
    plex_cfg = cfg.get("plex") or {}
    if not plex_cfg.get("enabled", False):
        return

    weekly_prev = ((contract.get("weekly") or {}).get("previous") or {})
    tracks_raw = weekly_prev.get("tracks") or []
    week_id = weekly_prev.get("week_id") or ""

    if not tracks_raw or not week_id:
        print("⚠ Plex sidecar: no previous-week tracks/week_id available (skipping).", flush=True)
        return

    base = plex_cfg.get("plex-url")
    token = plex_cfg.get("plex-token")
    library = plex_cfg.get("plex-library")
    prefix = plex_cfg.get("pl-name", "ListenBrainz Weekly Explore")
    verbose = bool(contract.get("dry_run") is False)  # not perfect, but keeps things simple
    # NOTE: core already has its own verbose mode; we also accept cfg flag below:
    # if you want, pass contract["verbose"] from core later.

    if not base or not token or not library:
        print("✗ Plex sidecar: missing plex-url / plex-token / plex-library in config.", flush=True)
        return

    # Optional overrides
    threshold = float(plex_cfg.get("threshold", DEFAULT_THRESHOLD))
    keep_weeks = int(plex_cfg.get("pl-retention", 6))
    last_week_enabled = bool(plex_cfg.get("last-week-exp", False))

    # Convert contract tracks into LBTrack list
    lb_tracks: List[LBTrack] = []
    for t in tracks_raw:
        # expect lb_extract_tracks_from_playlist output: {artist,title,album}
        lb_tracks.append(LBTrack(
            artist=t.get("artist", "") or "",
            title=t.get("title", "") or "",
            album=t.get("album", "") or "",
        ))

    # Resolve Plex identifiers
    machine_id = _plex_machine_id(base, token)
    section_id = _plex_section_id(base, token, library)

    print(f"✓ Plex: machine ID {machine_id}", flush=True)
    print(f"✓ Plex: library '{library}' (section {section_id})", flush=True)

    suffix = _week_id_to_title_suffix(week_id)
    if not suffix:
        print(f"⚠ Plex sidecar: week_id not in expected format YYYY-Www: '{week_id}' (skipping).", flush=True)
        return

    weekly_title = f"{prefix}{suffix}"
    last_week_title = f"{prefix} – Last Week"

    # Retention before creating new one (so we don't immediately delete it)
    _apply_retention(base, token, prefix, keep_weeks, verbose=True)

    # Match tracks
    matched_keys: List[str] = []
    unmatched: List[LBTrack] = []

    print(f"→ Building Plex playlist from {len(lb_tracks)} track(s): {weekly_title}", flush=True)

    for t in lb_tracks:
        best, best_s = _best_match_for_track(base, token, section_id, t, threshold)
        if best:
            matched_keys.append(best.rk)
            # always print “PoC style” match line if we can
            print(f"  ✓ {t.artist} — {t.title}  →  {best.artist} / {best.album} / {best.title}  ({best_s:.2f})", flush=True)
        else:
            unmatched.append(t)
            print(f"  ✗ {t.artist} — {t.title}", flush=True)

    print(f"✓ Matched {len(matched_keys)} track(s)", flush=True)

    if unmatched:
        print("\n✗ Unmatched tracks:", flush=True)
        for t in unmatched:
            print(f"  - {t.artist} — {t.title}", flush=True)

    if not matched_keys:
        print("✗ Plex sidecar: no matches — not creating playlist.", flush=True)
        return

    # Replace weekly archive playlist if it already exists
    existing = _plex_list_playlists(base, token)
    for p in existing:
        if p.title == weekly_title:
            print(f"→ Replacing existing weekly playlist: {weekly_title}", flush=True)
            _plex_delete_playlist(base, token, p.rk)
            break

    _plex_create_playlist(base, token, machine_id, weekly_title, matched_keys)
    print(f"✓ Created/updated weekly playlist: {weekly_title}", flush=True)

    # Optional: "Last Week" convenience playlist (always replaced)
    if last_week_enabled:
        existing = _plex_list_playlists(base, token)
        for p in existing:
            if p.title == last_week_title:
                print(f"→ Replacing existing 'Last Week' playlist: {last_week_title}", flush=True)
                _plex_delete_playlist(base, token, p.rk)
                break
        _plex_create_playlist(base, token, machine_id, last_week_title, matched_keys)
        print(f"✓ Created/updated 'Last Week' playlist: {last_week_title}", flush=True)
