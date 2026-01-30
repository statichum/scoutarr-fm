#!/usr/bin/env python3
"""
ListenBrainz Weekly Exploration → Plex playlist

Primary match: track search
Fallback: album → children → track scoring
"""

import argparse
import difflib
import re
import sys
import unicodedata
import urllib.parse
import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any

import requests
import yaml

LB_API = "https://api.listenbrainz.org"
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

# ------------------ ListenBrainz ------------------

@dataclass
class LBTrack:
    artist: str
    title: str
    album: str

def lb_json(url, token):
    h = {"Accept": "application/json", "Authorization": f"Token {token}"}
    r = requests.get(url, headers=h, timeout=30)
    r.raise_for_status()
    return r.json()

def weekly_tracks(user, token):
    meta = lb_json(f"{LB_API}/1/user/{user}/playlists/createdfor", token)
    pl = meta["playlists"][0]["playlist"]
    uuid = pl["identifier"].split("/")[-1]
    data = lb_json(f"{LB_API}/1/playlist/{uuid}", token)
    tracks = []
    for t in data["playlist"]["track"]:
        tracks.append(LBTrack(t["creator"], t["title"], t["album"]))
    return pl["title"], tracks

# ------------------ Plex ------------------

@dataclass
class PlexTrack:
    rk: str
    title: str
    artist: str
    album: str
    original: str

def plex_xml(url, token, path, params=None):
    h = {"X-Plex-Token": token}
    r = requests.get(url + path, headers=h, params=params, timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.text)

def plex_machine(url, token):
    return plex_xml(url, token, "/identity").attrib["machineIdentifier"]

def plex_section(url, token, name):
    root = plex_xml(url, token, "/library/sections")
    for d in root.findall("Directory"):
        if d.attrib["title"].casefold() == name.casefold():
            return d.attrib["key"]
    raise RuntimeError("Library not found")

def plex_search_track(url, token, sid, q):
    root = plex_xml(url, token, f"/library/sections/{sid}/search",
                    {"type": "10", "query": q})
    out = []
    for t in root.findall("Track"):
        out.append(PlexTrack(
            t.attrib["ratingKey"],
            t.attrib.get("title",""),
            t.attrib.get("grandparentTitle",""),
            t.attrib.get("parentTitle",""),
            t.attrib.get("originalTitle",""),
        ))
    return out

def plex_search_album(url, token, sid, q):
    root = plex_xml(url, token, f"/library/sections/{sid}/search",
                    {"type": "9", "query": q})
    return [d.attrib["ratingKey"] for d in root.findall("Directory")]

def plex_album_tracks(url, token, album_rk):
    root = plex_xml(url, token, f"/library/metadata/{album_rk}/children")
    out = []
    for t in root.findall("Track"):
        out.append(PlexTrack(
            t.attrib["ratingKey"],
            t.attrib.get("title",""),
            t.attrib.get("grandparentTitle",""),
            t.attrib.get("parentTitle",""),
            t.attrib.get("originalTitle",""),
        ))
    return out

# ------------------ Scoring ------------------

def score(lb: LBTrack, p: PlexTrack) -> float:
    title = 0.6*jaccard(lb.title, p.title) + 0.4*seq(lb.title, p.title)
    artist = max(
        0.6*jaccard(lb.artist, p.artist) + 0.4*seq(lb.artist, p.artist),
        0.6*jaccard(lb.artist, p.original) + 0.4*seq(lb.artist, p.original),
    )
    album = 0.6*jaccard(lb.album, p.album) + 0.4*seq(lb.album, p.album)
    return 0.5*title + 0.35*artist + 0.15*album

# ------------------ Main ------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    lb = cfg["listenbrainz"]
    plex = cfg["plex"]

    url = plex["plex-url"]
    token = plex["plex-token"]
    section = plex_section(url, token, plex["plex-library"])
    machine = plex_machine(url, token)

    print(f"✓ Plex machine ID: {machine}")
    print(f"✓ Plex library ID: {section}")

    print("→ Fetching Weekly Exploration…")
    title, tracks = weekly_tracks(lb["username"], lb["user_token"])
    print(f"✓ {len(tracks)} tracks")

    matched = []
    unmatched = []

    for t in tracks:
        best = None
        best_s = 0

        # Primary: track search
        for h in plex_search_track(url, token, section, t.title):
            s = score(t, h)
            if s > best_s:
                best, best_s = h, s

        # Fallback: album → tracks
        if not best or best_s < args.threshold:
            for ar in plex_search_album(url, token, section, t.album):
                for h in plex_album_tracks(url, token, ar):
                    s = score(t, h)
                    if s > best_s:
                        best, best_s = h, s

        if best and best_s >= args.threshold:
            matched.append(best.rk)
            if args.verbose:
                print(f"  ✓ {t.artist} — {t.title}  →  {best.artist} / {best.album} / {best.title}  ({best_s:.2f})")
        else:
            unmatched.append(t)
            if args.verbose:
                print(f"  ✗ {t.artist} — {t.title}")

    print(f"✓ Matched {len(matched)} tracks")

    if unmatched:
        print("\n✗ Unmatched tracks:")
        for t in unmatched:
            print(f"  - {t.artist} — {t.title}")

    if not matched:
        print("✗ No matches — aborting")
        sys.exit(1)

    uri = f"server://{machine}/com.plexapp.plugins.library/library/metadata/" + ",".join(matched)
    name = f'{plex["pl-name"]} – {dt.date.today().isocalendar()[0]} W{dt.date.today().isocalendar()[1]:02d}'
    r = requests.post(url+"/playlists",
                      headers={"X-Plex-Token": token},
                      params={"type":"audio","title":name,"smart":"0","uri":uri})
    r.raise_for_status()
    print("✓ Playlist created")

if __name__ == "__main__":
    main()
