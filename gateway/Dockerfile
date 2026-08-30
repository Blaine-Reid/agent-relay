# Runtime image: the gateway plus its own Tailscale identity, so the glasses
# app can reach it over the tailnet regardless of the host's own Tailscale
# node. See docker-entrypoint.sh for the tailscaled startup sequence.
FROM python:3.12-alpine

RUN apk add --no-cache tailscale ip6tables iptables

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 9091

ENTRYPOINT ["/app/docker-entrypoint.sh"]
