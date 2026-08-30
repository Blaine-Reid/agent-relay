#!/bin/sh
set -e

if [ -z "$TAILSCALE_AUTH_KEY" ]; then
  echo "FATAL: TAILSCALE_AUTH_KEY is not set — refusing to start with no tailnet identity." >&2
  exit 1
fi

mkdir -p /var/lib/tailscale /var/run/tailscale

tailscaled \
  --state=/var/lib/tailscale/tailscaled.state \
  --socket=/var/run/tailscale/tailscaled.sock &

echo "Waiting for tailscaled socket..."
for i in $(seq 1 30); do
  [ -S /var/run/tailscale/tailscaled.sock ] && break
  sleep 0.5
done
if [ ! -S /var/run/tailscale/tailscaled.sock ]; then
  echo "FATAL: tailscaled never created its socket." >&2
  exit 1
fi

tailscale --socket=/var/run/tailscale/tailscaled.sock up \
  --authkey="$TAILSCALE_AUTH_KEY" \
  --hostname="${TAILSCALE_HOSTNAME:-agent-relay-even-g2}" \
  --accept-routes

echo "Tailscale up:"
tailscale --socket=/var/run/tailscale/tailscaled.sock status

exec python server.py
