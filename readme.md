Scoutarr.fm pulls ListenBrainz recommendations and imports artists into Lidarr.

git clone https://github.com/yourusername/scoutarr-fm
cd scoutarr-fm
cp config.yaml.example config.yaml
nano config.yaml #add your config details
docker compose run --rm scoutarr

cron example:
0 9 * * 2 cd /docker/scoutarr-fm && docker compose run --rm scoutarr >> logs/cron.log 2>&1
