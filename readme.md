# scoutarr.fm

Scoutarr.fm pulls ListenBrainz recommendations and imports artists into Lidarr.

It is designed to be:
- deterministic (MBID-based, no fuzzy matching)
- safe (dry-run mode by default)
- automation-friendly (Docker + cron, no long-running container)

Scoutarr currently supports:
- ListenBrainz **Weekly Exploration** playlist
- ListenBrainz **Collaborative Filtering** recommendations
- Optional local MusicBrainz mirror for fast, reliable lookups
- Skipping artists already present in Lidarr
- Adding only missing artists with configured profiles, root folder, and tags

---

## Requirements
- Docker Compose
- Lidarr + API key
- ListenBrainz account + user token

---

## Installation

Clone the repo:

```bash
git clone https://github.com/statichum/scoutarr-fm.git
```

Config files go into the .config/ folder - Put config file(s) in place and edit:
Note - use as many config files as you like for any nubmer of users/setups. name files however you prefer.

```bash
cd scoutarr-fm
cp config.yaml.example ./config/config-1.yaml
nano config-1.yaml
```

First run with dry-run enabled to verify output, then disable dry-run to allow Lidarr imports.

Run once:

```bash
docker compose run --rm scoutarr
```

Cron example (weekly, Tuesday 09:00 local time):

```bash
0 9 * * 2 cd /docker/scoutarr-fm && docker compose run --rm scoutarr >> logs/cron.log 2>&1
```

---
