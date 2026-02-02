import time
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional


LB_CF_RECORDING = "https://api.listenbrainz.org/1/cf/recommendation/user"
LB_CREATED_FOR = "https://api.listenbrainz.org/1/user/{user}/playlists/createdfor"
LB_PLAYLIST = "https://api.listenbrainz.org/1/playlist"

# ------------------------------------------------------------
# ListenBrainz retry / backoff handling
# ------------------------------------------------------------

LB_BACKOFF_SCHEDULE = [
    10,
    30,
    120,
    300,
    600,
    900,
]

def lb_get_with_backoff(
    url: str,
    *,
    headers: Dict[str, str],
    params: Dict[str, Any] | None = None,
    timeout: int = 20,
):
    """
    Perform a GET request to ListenBrainz with a slow, bounded backoff.
    Retries on network errors and retryable HTTP status codes.
    """
    last_error: Exception | None = None
    attempts = [0] + LB_BACKOFF_SCHEDULE

    for attempt, delay in enumerate(attempts, start=1):
        if delay > 0:
            print(f"ListenBrainz retry {attempt - 1}, sleeping {delay}s...")
            time.sleep(delay)

        print(f"→ ListenBrainz attempt {attempt}: GET {url}")

        try:
            r = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
            )

            # Success
            if r.status_code < 400:
                print(f"ListenBrainz success ({r.status_code})")
                return r

            if r.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"HTTP {r.status_code}")
                print(f"ListenBrainz HTTP {r.status_code}, will retry")
                continue

            print(f"✗ ListenBrainz HTTP {r.status_code}, not retryable")
            r.raise_for_status()


        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "unknown"
            print(f"✗ ListenBrainz HTTP {status}, not retryable")
            raise

        except requests.exceptions.RequestException as e:
            last_error = e
            print(f"⚠ ListenBrainz request error: {type(e).__name__}: {e}")
            continue


    raise RuntimeError(
        f"ListenBrainz request failed after {len(LB_BACKOFF_SCHEDULE) + 1} attempts: {url}"
    ) from last_error



def lb_headers(token: str, user_agent: str) -> Dict[str, str]:
    return {
        "Authorization": f"Token {token}",
        "User-Agent": user_agent,
    }


def parse_lb_date(date_str: str) -> datetime:
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


def lb_get_weekly_exploration_playlists(cfg: Dict[str, Any], user_agent: str) -> List[Dict[str, str]]:
    """
    Returns list of weekly-exploration playlists sorted newest->oldest:
      [{ "mbid": "...", "title": "...", "date": "..." }, ...]
    """
    token = cfg["listenbrainz"]["user_token"]
    user = cfg["listenbrainz"]["username"]

    r = lb_get_with_backoff(
        LB_CREATED_FOR.format(user=user),
        headers=lb_headers(token, user_agent),
        timeout=20,
    )

    r.raise_for_status()

    playlists = r.json().get("playlists", [])
    weekly = []

    for p in playlists:
        playlist = p.get("playlist", {})
        jspf = playlist.get("extension", {}).get("https://musicbrainz.org/doc/jspf#playlist", {})
        algo = jspf.get("additional_metadata", {}).get("algorithm_metadata", {})

        if algo.get("source_patch") == "weekly-exploration":
            mbid = playlist.get("identifier", "").split("/")[-1]
            weekly.append({
                "mbid": mbid,
                "title": playlist.get("title", ""),
                "date": playlist.get("date", ""),
            })

    weekly.sort(key=lambda x: parse_lb_date(x["date"]), reverse=True)
    return weekly


def lb_get_playlist(cfg: Dict[str, Any], playlist_mbid: str, user_agent: str) -> Dict[str, Any]:
    token = cfg["listenbrainz"]["user_token"]

    r = lb_get_with_backoff(
        f"{LB_PLAYLIST}/{playlist_mbid}",
        headers=lb_headers(token, user_agent),
        timeout=20,
    )
    return r.json().get("playlist", {})


def lb_extract_artists_from_playlist(playlist: Dict[str, Any], source: str) -> List[Dict[str, str]]:
    artists = []
    for track in playlist.get("track", []):
        meta = track.get("extension", {}).get("https://musicbrainz.org/doc/jspf#track", {})
        for a in meta.get("additional_metadata", {}).get("artists", []):
            mbid = a.get("artist_mbid")
            name = a.get("artist_credit_name")
            if mbid and name:
                artists.append({"mbid": mbid, "name": name, "source": source})
    return artists


def lb_extract_tracks_from_playlist(playlist: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Minimal normalized track objects for Plex matching later.
    Keep duration + album + artist + title + identifiers where available.
    """
    out = []
    for t in playlist.get("track", []):
        meta = t.get("extension", {}).get("https://musicbrainz.org/doc/jspf#track", {})
        add = meta.get("additional_metadata", {}) or {}

        artist_name = t.get("creator")
        album = t.get("album")
        title = t.get("title")
        duration_ms = t.get("duration")

        recording_mbid = None
        for ident in meta.get("identifier", []) if isinstance(meta.get("identifier"), list) else []:
            if "musicbrainz.org/recording/" in ident:
                recording_mbid = ident.split("/")[-1]

        artist_mbids = []
        for a in add.get("artists", []):
            if a.get("artist_mbid"):
                artist_mbids.append(a["artist_mbid"])

        out.append({
            "title": title,
            "artist": artist_name,
            "album": album,
            "duration_ms": duration_ms,
            "artist_mbids": artist_mbids,
            "recording_mbid": recording_mbid,
        })

    return out


def mb_base(cfg: Dict[str, Any]) -> str:
    base = cfg.get("musicbrainz", {}).get("musicbrainz_url", "https://musicbrainz.org").rstrip("/")
    return f"{base}/ws/2"


def get_primary_artist_from_recording(cfg: Dict[str, Any], recording_mbid: str, user_agent: str, retries: int = 3) -> Optional[Dict[str, str]]:
    url = f"{mb_base(cfg)}/recording/{recording_mbid}"

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                url,
                params={"inc": "artist-credits", "fmt": "json"},
                headers={"User-Agent": user_agent},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()

            if not data.get("artist-credit"):
                return None

            artist = data["artist-credit"][0]["artist"]
            return {"name": artist["name"], "mbid": artist["id"]}

        except Exception:
            time.sleep(0.5)

    return None


def lb_get_cf_artists(cfg: Dict[str, Any], user_agent: str) -> List[Dict[str, str]]:
    token = cfg["listenbrainz"]["user_token"]
    user = cfg["listenbrainz"]["username"]

    r = lb_get_with_backoff(
        f"{LB_CF_RECORDING}/{user}/recording",
        headers=lb_headers(token, user_agent),
        params={"count": 100},
        timeout=20,
    )


    if r.status_code == 204:
        return []

    r.raise_for_status()

    if not r.text.strip():
        return []

    data = r.json()
    payload = data.get("payload", {})
    mbids = payload.get("mbids", [])

    artists: List[Dict[str, str]] = []

    for item in mbids:
        rec = item.get("recording_mbid")
        if not rec:
            continue

        a = get_primary_artist_from_recording(cfg, rec, user_agent=user_agent)
        if a:
            artists.append({
                "name": a["name"],
                "mbid": a["mbid"],
                "source": "collaborative-filtering",
            })

        time.sleep(0.2)

    return artists
