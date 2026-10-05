#!/bin/sh
# Pull the latest code and restart the bot. Run on the VM from /opt/chronochunk.
set -e

cd /opt/chronochunk
git -C app pull --ff-only
docker compose up -d --build
docker image prune -f
