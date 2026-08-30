# Agent Relay — Gateway

The self-hosted gateway half of Agent Relay: a hands-free voice chat system
for Even Realities G2 smart glasses, connecting your own AI backend (Hermes,
or any OpenAI-compatible server like LM Studio/Ollama) and your own
speech-to-text service to the glasses over Tailscale.

The G2 glasses app itself lives in a separate, private companion repo (not
public — it points here for setup). This repo is just the gateway: the
Docker container + Tailscale identity + backend adapters that the glasses
app talks to over one WebSocket connection.

```
[G2 glasses] <--BLE--> [Phone: Even Hub app] <--WiFi/Tailscale--> [This gateway] --> [Your AI backend]
                                                                        \--> [Your STT service]
```

## What this repo is

- A small aiohttp server (`gateway/server.py`) speaking one WebSocket
  protocol to the glasses app — session management, voice transcription,
  and chat, all over that single connection (see the protocol documented
  in `server.py`'s own docstring).
- Runs in Docker with its **own Tailscale identity**, so the glasses can
  reach it from anywhere without exposing it to the public internet.
- Proxies to a pluggable chat backend (`gateway/backends/`) — Hermes by
  default, or any OpenAI-compatible `/v1/chat/completions` server (LM
  Studio, Ollama, etc.) — and to your own STT service
  (`gateway/stt_client.py`).
- Holds your backend API keys server-side only. They never reach the
  glasses app or the phone.

## Requirements

- **[Docker](https://docs.docker.com/get-docker/)** and Docker Compose (bundled with Docker Desktop)
- A **[Tailscale](https://tailscale.com/)** account (free tier works) — the
  gateway gets its own tailnet identity so the glasses can reach it from
  anywhere
- An **AI backend** reachable on your tailnet — one of:
  - A Hermes-compatible agent server (built against
    [Nous Research's Hermes](https://github.com/NousResearch); needs
    `POST/GET/DELETE /api/sessions`, `POST /api/sessions/{id}/chat/stream`
    with the SSE event shape documented in `gateway/backends/hermes.py`)
  - **Any OpenAI-compatible `/v1/chat/completions` server** —
    [LM Studio](https://lmstudio.ai/) or **[Ollama](https://ollama.com/)**
    (its OpenAI-compatible API is on its normal port, `11434`, no separate
    flag needed), or similar. Simpler to point at than Hermes, but none of
    these have a native multi-session concept, so the gateway keeps sessions
    in memory instead (see [Known limitations](#known-limitations))
- A **speech-to-text service** reachable on your tailnet. This was built
  against a self-hosted multipart-upload STT server; see
  `gateway/stt_client.py` for the exact request/response shape it expects.
  **If your STT service has a different API, edit that one file** — it's a
  small, isolated adapter. Note: whatever you run, watch out for port
  3000 — it's the default for a lot of local dev tooling (React dev
  servers, etc.), so if your STT service also defaults there, you'll likely
  want to move it to something else (this project's own reference instance
  runs on 5001 for exactly that reason).

## Setup

1. Clone this repo.
2. Fill in `.env` — pick one:
   - **Setup wizard (recommended):** `python3 setup_wizard.py` — no
     dependencies, opens a local form in your browser, links straight to
     where each value comes from, writes `.env` for you. Safe to re-run any
     time to change a setting later.
   - **Manual:** `cp .env.example .env` and fill it in yourself:

     | Variable | What it is |
     |---|---|
     | `TAILSCALE_AUTH_KEY` | A **reusable** key from your [Tailscale admin console](https://login.tailscale.com/admin/settings/keys). Must be reusable, not single-use — the container restarts during development. If your tailnet has device approval on, approve the new node once at `https://login.tailscale.com/admin/machines` after first start. |
     | `TAILSCALE_HOSTNAME` | The name your gateway will use on your tailnet (default `agent-relay-even-g2`) — this becomes the address you'll enter in the glasses app's setup screen. |
     | `BACKEND` | `hermes` or `lm_studio` |
     | `HERMES_BASE_URL` / `HERMES_API_KEY` | Your [Hermes](https://github.com/NousResearch) instance's address and API key |
     | `LM_STUDIO_BASE_URL` / `LM_STUDIO_MODEL` | Only needed if `BACKEND=lm_studio` — e.g. `http://<host>:1234` for [LM Studio](https://lmstudio.ai/), `http://<host>:11434` for [Ollama](https://ollama.com/). `LM_STUDIO_MODEL` is optional if your server only has one model loaded. |
     | `SOGNI_BASE_URL` / `SOGNI_API_KEY` / etc. | Your STT service's address and credentials |

3. `docker compose up --build` — builds the gateway and brings up its own
   Tailscale node, serving on port 9091.
4. Find its address: `docker compose exec gateway tailscale status` — note
   the hostname (e.g. `agent-relay-even-g2.yourtailnet.ts.net`). Enter this
   in the glasses app's setup screen (see the app's own repo/README).

## Design notes

A few decisions that aren't obvious from the code alone:

- **The G2 app's network permission whitelist accepts wildcards at
  runtime** — confirmed live on real hardware: `ws://*.ts.net:9091`
  matches any Tailscale address, not just the one used when the app was
  built. This is what lets one shared glasses-app build work against
  anyone's own gateway, configured via a runtime setup screen rather than
  baked in per-build. (The packaging tool's own manifest validation doesn't
  check the whitelist's contents either way — this had to be verified by
  actually sideloading a build and watching it connect.)
- **Everything is WebSocket, not REST.** Plain `fetch()` calls from inside
  the Even Hub WebView failed outright on a real device while the WebSocket
  connection worked fine at the same time. This gateway still exposes REST
  endpoints for manual `curl` debugging, but the glasses app itself never
  calls them.
- **Active reconnection, not just a passive timer**, is the glasses app's
  responsibility, but it's worth knowing why the gateway sends a WebSocket
  **heartbeat** (every 20s): mobile NATs aggressively kill idle
  connections, and regular traffic both keeps the path alive and lets this
  server detect a truly dead peer faster than waiting on a read that never
  comes.
- **A local session registry** (`gateway/session_registry.py`). A real
  agent backend's session list can include cron jobs, other chat clients,
  everything — this gateway filters to sessions it actually created or was
  told to attach, or the glasses' session list would be unusable.

## Known limitations

- The generic OpenAI-compatible backend (LM Studio, Ollama, etc.) keeps its
  own in-memory sessions (no persistence across gateway restarts) rather
  than the registry/cron-filter machinery the Hermes backend uses, since
  none of these servers have a native session concept to proxy.
- No authentication on the gateway's own WebSocket/REST endpoints beyond
  Tailscale network membership — anyone who can reach this gateway over
  your tailnet has full access, same as anyone else on your tailnet would.
  Fine for a personal setup; worth knowing if you ever share tailnet access
  more broadly.
