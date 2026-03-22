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

# 1.0 Clone the repo:

```bash
git clone https://github.com/statichum/scoutarr-fm.git
```

# 2.0 Set up config:

Config files go into the .config/ folder - Put config file(s) in place and edit:
Note - use as many config files as you like for any number of users/setups.
Name config files however you prefer, they will be used as long as theyre in config file and have .yaml extension.

```bash
cd scoutarr-fm
mkdir config
cp config.yaml.example ./config/config-swedishgary.yaml
cd config
nano config-swedishgary.yaml
```

#3.0 Set plex webook

Go to Plex > Settings > Webhooks
Set your Scputarr url here

'''
http://localhost:8787/webhook
'''

#4.0 First run

On first run, Scoutarr will sync all ratings with Listenbrainz, run it using compose up and watch the output for issues
This will take some time to complete.

```
docker compose up
```

Any new/changed ratings are synced on the fly from webhooks.

Cron inside the container runs two things:
- Full ratings sync weekly at 1am on Sundays to ensure all tracks are synced correctly.
- Listenbrainz recommmendations loop to insert artists into Lidarr and create playlists, this is run weekly on Tuesdays at 3am (Listenbrainz cretes weekly explore playlists on early on Monday of your selected timezone but I've noticed some days LB glitches and things run later than expected - running on Tuesday is much safer.) 



---

