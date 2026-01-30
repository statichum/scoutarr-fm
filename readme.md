# scoutarr.fm

Scoutarr.fm pulls ListenBrainz recommendations and imports artists into Lidarr and inserts the Weekly Explore playlist from ListenBrainz into PlexAmp 
(uses the previous week's weekly explore so that it can take advantage of added artists and tracks being recommended)

Scoutarr currently supports:
- ListenBrainz **Weekly Exploration** playlist
- ListenBrainz **Collaborative Filtering** recommendations
- Plexamp playlist insertion

---

## Requirements
- Docker Compose
- Lidarr + API key
- ListenBrainz account + user token
- Plexamp and X-Plex key

---

## Installation

Clone the repo:
```bash
git clone https://github.com/statichum/scoutarr-fm.git
```

Config files go into the .config/ folder
Note - use as many config files as you like for any number of users/setups.
Name config files however you prefer, they will be used as long as theyre in config file and have .yaml extension.

- Put config file(s) in place and edit:
```bash
cd scoutarr-fm
mkdir config
cp config.yaml.example ./config/config-swedishgary.yaml
cd config
nano config-swedishgary.yaml
cd ..
```

I recommend to first run with dry-run enabled to verify output, then disable dry-run to allow Lidarr imports.
Run once:
```bash
docker compose run --rm scoutarr
```

Set up Cron to run the contianer on a weekly basis:
Cron example
```bash
0 9 * * 2 cd /docker/scoutarr-fm && docker compose run --rm scoutarr >> logs/cron.log 2>&1
```

---
