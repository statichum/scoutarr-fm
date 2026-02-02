import time
import requests
from typing import Dict, Any


def log(msg: str):
    print(msg, flush=True)


def fmt_sources(sources) -> str:
    return "+".join(sorted(list(sources))) if sources else "unknown"


def lidarr_headers(cfg: Dict[str, Any], user_agent: str) -> Dict[str, str]:
    return {
        "X-Api-Key": cfg["lidarr"]["api_key"],
        "User-Agent": user_agent,
    }


def lidarr_get(cfg: Dict[str, Any], path: str, user_agent: str):
    r = requests.get(
        f'{cfg["lidarr"]["url"]}/api/v1/{path}',
        headers=lidarr_headers(cfg, user_agent),
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def lidarr_lookup_artist(cfg: Dict[str, Any], mbid: str, user_agent: str):
    r = requests.get(
        f'{cfg["lidarr"]["url"]}/api/v1/artist/lookup',
        headers=lidarr_headers(cfg, user_agent),
        params={"term": f"mbid:{mbid}"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if data else None


def resolve_lidarr_ids(cfg: Dict[str, Any], user_agent: str):
    qp = lidarr_get(cfg, "qualityprofile", user_agent)
    mp = lidarr_get(cfg, "metadataprofile", user_agent)
    tags = lidarr_get(cfg, "tag", user_agent)

    qp_id = next(x["id"] for x in qp if x["name"] == cfg["lidarr"]["quality_profile"])
    mp_id = next(x["id"] for x in mp if x["name"] == cfg["lidarr"]["metadata_profile"])
    tag_ids = [t["id"] for t in tags if t["label"] in cfg["lidarr"].get("tags", [])]

    return qp_id, mp_id, tag_ids


def lidarr_add_artist(cfg: Dict[str, Any], artist_obj: Dict[str, Any], qp_id: int, mp_id: int, tag_ids, user_agent: str):
    payload = {
        "artistName": artist_obj["artistName"],
        "foreignArtistId": artist_obj["foreignArtistId"],
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
        headers=lidarr_headers(cfg, user_agent),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()


def lidarr_run_import(cfg: Dict[str, Any], contract: Dict[str, Any], user_agent: str):
    dry_run = contract.get("dry_run", True)
    artists = contract.get("artists", {})

    ranked = sorted(
        artists.items(),
        key=lambda kv: (len(kv[1].get("sources", set())), kv[1].get("name", "")),
        reverse=True,
    )

    log(f"\nLidarr sidecar — {len(ranked)} artist(s)")
    if dry_run:
        log("DRY RUN — no Lidarr changes will be made\n")
        for i, (mbid, data) in enumerate(ranked, 1):
            log(f"{i:>3}. {data['name']}  [{fmt_sources(data.get('sources'))}]")
        return

    log("\nLIVE MODE — importing into Lidarr\n")

    qp_id, mp_id, tag_ids = resolve_lidarr_ids(cfg, user_agent)

    for mbid, data in ranked:
        src = fmt_sources(data.get("sources"))
        lookup = lidarr_lookup_artist(cfg, mbid, user_agent)

        if lookup and lookup.get("id"):
            log(f"SKIP ✓ {data['name']}  [{src}]")
            continue

        if not lookup:
            log(f"SKIP ⚠ {data['name']}  [{src}] (lookup returned nothing)")
            continue

        log(f"ADD  + {data['name']}  [{src}]")
        lidarr_add_artist(cfg, lookup, qp_id, mp_id, tag_ids, user_agent)
        time.sleep(0.3)

    log("\n✓ Lidarr import complete.\n")
