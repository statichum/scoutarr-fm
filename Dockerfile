FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY src/ ./src/
COPY config.yaml /config/config.yaml

# Default command = run scoutarr once
CMD ["python", "/app/src/scoutarr.py"]
