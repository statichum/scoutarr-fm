#!/usr/bin/env python3

import difflib
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests

DEFAULT_THRESHOLD = 0.72


# ------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------

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

def jaccard(a: str, b: str) -> float:
    sa, sb = set(norm(a).split()), set(norm(b).split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0

def seq(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


# ------------------------------------------------------------
# Data
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# Plex API
# ------------------------------------------------------------

def _plex_xml(base, token, path, params=None):
    h = {"X-Plex-Token": token, "Accept": "application/xml"}
    r = requests.get(base.rstrip("/") + path, headers=h, params=params, timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.text)

def _plex_machine_id(base, token):
    return _plex_xml(base, token, "/identity").attrib["machineIdentifier"]

def _plex_section_id(base, token, name):
    root = _plex_xml(base, token, "/library/sections")
    for d in root.findall("Directory"):
        if d.attrib.get("title", "").casefold() == name.casefold():
            return d.attrib["key"]
    raise RuntimeError(f"Plex library not found: {name}")

def _plex_search_track(base, token, sid, q):
    root = _plex_xml(base, token, f"/library/sections/{sid}/search", {"type": "10", "query": q})
    out = []
    for t in root.findall("Track"):
        out.append(PlexTrack(
            rk=t.attrib["ratingKey"],
            title=t.attrib.get("title", ""),
            artist=t.attrib.get("grandparentTitle", ""),
            album=t.attrib.get("parentTitle", ""),
            original=t.attrib.get("originalTitle", ""),
        ))
    return out

def _plex_search_album(base, token, sid, q):
    root = _plex_xml(base, token, f"/library/sections/{sid}/search", {"type": "9", "query": q})
    return [d.attrib["ratingKey"] for d in root.findall("Directory")]

def _plex_album_tracks(base, token, rk):
    root = _plex_xml(base, token, f"/library/metadata/{rk}/children")
    out = []
    for t in root.findall("Track"):
        out.append(PlexTrack(
            rk=t.attrib["ratingKey"],
            title=t.attrib.get("title", ""),
            artist=t.attrib.get("grandparentTitle", ""),
            album=t.attrib.get("parentTitle", ""),
            original=t.attrib.get("originalTitle", ""),
        ))
    return out


# ------------------------------------------------------------
# Matching
# ------------------------------------------------------------

def _score(lb: LBTrack, p: PlexTrack) -> float:
    title = 0.6 * jaccard(lb.title, p.title) + 0.4 * seq(lb.title, p.title)
    artist = max(
        0.6 * jaccard(lb.artist, p.artist) + 0.4 * seq(lb.artist, p.artist),
        0.6 * jaccard(lb.artist, p.original) + 0.4 * seq(lb.artist, p.original),
    )
    album = 0.6 * jaccard(lb.album, p.album) + 0.4 * seq(lb.album, p.album)
    return 0.5 * title + 0.35 * artist + 0.15 * album


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def plex_run_playlists(cfg: Dict, contract: Dict, user_agent: str = "") -> None:
    plex = cfg.get("plex") or {}
    if not plex.get("enabled"):
        return

    weekly = contract.get("weekly", {}).get("previous")
    if not weekly:
        print("⚠ Plex: no previous week provided.")
        return

    base = plex.get("plex-url")
    token = plex.get("plex-token")
    library = plex.get("plex-library")
    prefix = plex.get("pl-name", "ListenBrainz Weekly Explore")

    if not base or not token or not library:
        print("✗ Plex config incomplete.")
        return

    machine = _plex_machine_id(base, token)
    section = _plex_section_id(base, token, library)

    year, week = weekly["week_id"].split("-")
    title = f"{prefix} {week} {year}"



    tracks = [
        LBTrack(
            artist=t.get("artist", ""),
            title=t.get("title", ""),
            album=t.get("album", ""),
        )
        for t in weekly["tracks"]
    ]
    matched = []

    print(f"→ Creating Plex playlist: {title}")

    for t in tracks:
        best, score = None, 0.0
        for h in _plex_search_track(base, token, section, t.title):
            s = _score(t, h)
            if s > score:
                best, score = h, s

        if not best or score < DEFAULT_THRESHOLD:
            for ar in _plex_search_album(base, token, section, t.album):
                for h in _plex_album_tracks(base, token, ar):
                    s = _score(t, h)
                    if s > score:
                        best, score = h, s

        if best and score >= DEFAULT_THRESHOLD:
            matched.append(best.rk)

    if not matched:
        print("✗ Plex: no matched tracks.")
        return

    uri = f"server://{machine}/com.plexapp.plugins.library/library/metadata/" + ",".join(matched)
    requests.post(
        base.rstrip("/") + "/playlists",
        headers={"X-Plex-Token": token},
        params={"type": "audio", "title": title, "smart": "0", "uri": uri},
        timeout=30,
    ).raise_for_status()

    print(f"✓ Plex playlist created: {title}")
