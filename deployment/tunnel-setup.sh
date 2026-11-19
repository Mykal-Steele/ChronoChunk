#!/bin/sh
# One time: makes the Cloudflare Tunnel that puts the bot's Uniloom sign-in pages on
# https://chrono.oakar.dev (docs/uniloom-plan.md, section 4). Run on the VM as chrono:
#   sudo -u chrono sh /opt/chronochunk/app/deployment/tunnel-setup.sh
# It prints a Cloudflare link. Open it, sign in and pick oakar.dev. The rest is automatic.
# Running it again on a VM that has the tunnel only rewrites the config.
set -e

HOSTNAME=chrono.oakar.dev
NAME=chronochunk
DIR=/opt/chronochunk/data/cloudflared
IMAGE=cloudflare/cloudflared:2026.10.7

umask 077
mkdir -p "$DIR"
chmod 700 "$DIR"

# login always saves its certificate in ~/.cloudflared, so the folder is mounted there. Not under
# /home/nonroot: the image's own user owns that, and this runs as chrono
cloudflared() {
    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/cf \
        -v "$DIR:/cf/.cloudflared" "$IMAGE" "$@"
}

if [ ! -f "$DIR/credentials.json" ]; then
    # cert.pem lets cloudflared make tunnels and DNS records in the account, so it is only kept for this
    [ -f "$DIR/cert.pem" ] || cloudflared tunnel login
    cloudflared tunnel create --credentials-file /cf/.cloudflared/credentials.json "$NAME"
    cloudflared tunnel route dns "$NAME" "$HOSTNAME"
fi

ID=$(sed -n 's/.*"TunnelID":"\([^"]*\)".*/\1/p' "$DIR/credentials.json")
[ -n "$ID" ] || { echo "no TunnelID in $DIR/credentials.json" >&2; exit 1; }

# Only the two sign-in pages go to the bot. Everything else, such as /status, gets a 404 at Cloudflare
cat > "$DIR/config.yml" <<EOF
tunnel: $ID
credentials-file: /etc/cloudflared/credentials.json
ingress:
  - hostname: $HOSTNAME
    path: ^/oauth/(start|callback|client\.json)$
    service: http://bot:8080
  - service: http_status:404
EOF

# Running the tunnel needs only credentials.json, so the account-wide certificate goes
rm -f "$DIR/cert.pem"
chmod 600 "$DIR"/*
echo "tunnel $ID ready for https://$HOSTNAME"
