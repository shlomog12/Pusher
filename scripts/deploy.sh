#!/usr/bin/env sh
# Rebuild the image and replace the running container. Run on the server from
# the project directory; the state volume is kept, so nothing is sent twice.
set -eu

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "Missing .env in $(pwd)" >&2
    exit 1
fi

# Build first: if it fails, the current container keeps running.
docker build -t pusher .

docker rm -f pusher >/dev/null 2>&1 || true
docker run -d --name pusher --restart unless-stopped \
    --env-file .env -e STATE_FILE=/data/state.json \
    -v pusher-data:/data pusher

# Drop the previous image so the small server disk doesn't fill up.
docker image prune -f >/dev/null
docker ps --filter name=pusher --format '{{.Names}}: {{.Status}}'
