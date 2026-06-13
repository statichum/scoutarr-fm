# scoutarr.fm

Scoutarr.fm is a CLI tool for Lidarr, Plex/Plexamp and Listenbrainz users (no polished web UI here)
- Pulls ListenBrainz recommendations based on Weekly explore and collaborative-filtering recommendations and adds artists into Lidarr
- Inserts weekly explore playlists into Plex/Plexamp
- Syncs 5-star and 1-star rated tracks from Plex/Plexamp with Love/Hate respectively on Listenbrainz

---

## Requirements
- Scrobling to Listenbrainz (If using Plexamp see Multi-Scrobbler or https://eavesdrop.fm
- Docker Compose
- Lidarr + API key
- ListenBrainz account + user token
- Plexamp and X-Plex key

---

## Installation

# 1.0 Create docker compose file:

Create directory and compose file

```
mkdir scoutarr-fm
cd scoutarr-fm
nano docker-compose.yml
```

Set up compose, paste this into the file:

```
services:
  scoutarr:
    image: ghcr.io/statichum/scoutarr-fm
    container_name: scoutarr-fm
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    volumes:
      - ./config/:/config/
      - /etc/localtime:/etc/localtime:ro
    restart: unless-stopped
    ports:
      - "8787:8787"

```

# 2.0 Pull image

```
docker compose pull
```

# 2.0 Set up config:

Config files go into the .config/ folder - Put config file(s) in place and edit:
Note - use as many config files as you like for any number of users/setups.
Name config files however you prefer, they will be used as long as theyre in config file and have .yaml extension.

```bash
mkdir config
cd scoutarr-fm
mkdir config
cp config.yaml.example ./config/config-swedishgary.yaml
cd config
nano config-swedishgary.yaml
```

# 3.0 Set plex webook

Go to Plex > Settings > Webhooks
Set your Scputarr url here:

- http://localhost:8787/webhook


# 4.0 First run

```
docker compose up
```

Run initial playlist reation, and artist check

```
docker exec -it scoutarr-fm python3 /app/src/scoutarr.py
```
Cron inside the container runs this every 3 hours to check for new artists and playlists


---

