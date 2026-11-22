FROM python:3.12-slim

# Build with --build-arg WITH_SCRAPER=true to support TWEET_SOURCE=scraper
ARG WITH_SCRAPER=false

WORKDIR /app
ENV PYTHONUNBUFFERED=1 STATE_FILE=/data/state.json

COPY requirements.txt requirements-scraper.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_SCRAPER" = "true" ]; then \
         pip install --no-cache-dir -r requirements-scraper.txt \
         && playwright install --with-deps chromium \
         && rm -rf /var/lib/apt/lists/*; \
       fi

COPY pusher ./pusher

VOLUME ["/data"]
CMD ["python", "-m", "pusher"]
