FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates cron procps \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY start.sh /app/start.sh

RUN chmod +x /app/start.sh

RUN echo "SHELL=/bin/bash" > /etc/cron.d/scoutarr \
 && echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" >> /etc/cron.d/scoutarr \
 && echo "0 1 * * 0 root python3 /app/src/sync_ratings.py > /proc/1/fd/1 2>&1" >> /etc/cron.d/scoutarr \
 && echo "0 3 * * 2 root python3 /app/src/scoutarr.py > /proc/1/fd/1 2>&1" >> /etc/cron.d/scoutarr \
 && chmod 0644 /etc/cron.d/scoutarr \
 && crontab /etc/cron.d/scoutarr

CMD ["/app/start.sh"]
