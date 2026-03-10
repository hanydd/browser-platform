# Cloud Browser Platform

An MVP cloud browser platform for running isolated browser sessions for agents.

This project provides a control plane that can create and manage one browser container per session, expose CDP for agent control, and provide a noVNC viewer URL for human observation.

## Goals

- Give each agent an isolated browser environment.
- Let agents connect through CDP-compatible clients such as Playwright, Puppeteer, and `agent-browser`.
- Let humans watch the same browser session through a browser-based viewer.
- Preserve per-user browser profile data across sessions.
- Keep the architecture simple enough for an MVP, while leaving room for long-term expansion.

## Problems This Project Solves

- Shared browsers cause cross-user contamination in cookies, local storage, cache, and login state.
- Running multiple browsers inside one process space makes isolation, resource control, and cleanup difficult.
- Agent control and human viewing often use different browser instances, which causes mismatch between what the agent does and what the user sees.
- Starting a new isolated browser from scratch is slow without pooling.

This project addresses those problems by using:

- One container per assigned browser session
- A warm pool of pre-started browser containers
- A control plane for session lifecycle and routing
- CDP access for agents
- noVNC access for humans

## Architecture

### Main Components

- `browser/`
  - Browser runtime image
  - Runs Chromium, Xvfb, x11vnc, noVNC, and helper scripts
- `api/`
  - FastAPI control plane
  - Uses `uv` for Python dependency management
  - Manages pool containers and session lifecycle through Docker SDK
- `redis`
  - Stores session state and pool metadata
- `traefik`
  - Reserved as ingress middleware
  - Included in Compose, but not the primary verified entrypoint at the moment

### Session Model

Each session gets:

- A dedicated browser container
- Its own Chromium profile directory
- A CDP endpoint
- A viewer URL for noVNC

The current implementation returns a path-aware `viewer_url`, so noVNC connects to the correct per-session WebSocket path.

## Current Verified Entrypoints

The following entrypoint has been verified in the current implementation:

- API and session access: `http://localhost:8000`

Typical session URLs returned by the API:

- `cdp_url`
- `cdp_http_url`
- `vnc_url`
- `viewer_url`

## Middleware Access

### API

- Base URL: `http://localhost:8000`
- Health check: `GET /healthz`
- Metrics: `GET /metrics`
- Session API:
  - `POST /api/sessions`
  - `GET /api/sessions`
  - `GET /api/sessions/{session_id}`
  - `DELETE /api/sessions/{session_id}`
  - `POST /api/sessions/{session_id}/keep-alive`
  - `GET /api/pool`

### Traefik

Traefik is included in `docker-compose.yaml` and exposed on:

- `http://localhost:8080`

However:

- The Traefik dashboard is not enabled in the current repo configuration.
- The current MVP has been validated primarily through the API entrypoint on port `8000`.
- In some Windows + Docker Desktop environments, the Docker provider may require extra configuration before Traefik routing works as expected.

If you want to enable the Traefik dashboard later, you can add the dashboard flags and expose Traefik's internal port explicitly.

### Redis

Redis is internal by default and is not published to the host.

## Build

This project has two build targets:

- Browser image: `agent-desk:latest`
- API image: `browser-platform-api:latest`

### Build with Docker Compose

```bash
docker compose build browser-image api
```

### Build browser image directly

```bash
docker build -t agent-desk:latest ./browser
```

### Build API image directly

```bash
docker build -t browser-platform-api:latest ./api
```

## Run

### Start using prebuilt images

```bash
docker compose up -d --no-build redis api traefik
```

### Start and build if needed

```bash
docker compose up -d redis api traefik
```

## Stop

```bash
docker compose down
```

To also remove stale session containers created from the pool:

```bash
docker rm -f $(docker ps -aq --filter "name=browser-pool-")
```

Adjust the cleanup command for your shell if needed.

## API Authentication

The API currently uses a static API key from Compose:

- `API_KEY=change-me`

You can pass it as:

- `x-api-key: change-me`
- `Authorization: Bearer change-me`

## Example: Create a Session

```bash
curl -X POST "http://localhost:8000/api/sessions" \
  -H "x-api-key: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "persist_profile": true,
    "metadata": {
      "source": "manual-test"
    }
  }'
```

Typical response fields:

```json
{
  "session_id": "...",
  "cdp_url": "ws://localhost:8000/sessions/.../cdp/devtools/browser/...?token=change-me",
  "cdp_http_url": "http://localhost:8000/sessions/.../cdp?token=change-me",
  "vnc_url": "http://localhost:8000/sessions/.../vnc/?token=change-me",
  "viewer_url": "http://localhost:8000/sessions/.../vnc/vnc.html?autoconnect=true&resize=scale&path=sessions/.../vnc/websockify&token=change-me"
}
```

## Agent Access

### agent-browser

```bash
agent-browser.cmd --cdp "ws://localhost:8000/sessions/<session_id>/cdp/devtools/browser/<browser_id>?token=change-me" open https://example.com
```

### Playwright

```ts
import { chromium } from "playwright";

const browser = await chromium.connectOverCDP(
  "http://localhost:8000/sessions/<session_id>/cdp?token=change-me"
);
```

### Puppeteer

Use the returned `cdp_url` as the browser WebSocket endpoint.

## Human Viewing

Open the returned `viewer_url` in a browser.

Important:

- Use the `viewer_url` returned by the API directly.
- Do not manually remove the `path` query parameter.
- The viewer uses token bootstrap plus cookie-based access for subsequent noVNC static assets and WebSocket requests.

## Profiles and Persistence

Per-user browser profile archives are stored through the API service volume:

- `profile-archives:/data/profiles`

When a session starts:

- The API restores the user's archived browser profile if available.

When a session ends:

- The API saves the profile archive back to persistent storage.

## Warm Pool

The API maintains a pool of idle browser containers.

Relevant environment variables:

- `POOL_MIN_SIZE`
- `POOL_MAX_SIZE`
- `DEFAULT_TTL_SECONDS`
- `HOUSEKEEPING_INTERVAL_SECONDS`

This reduces the latency of creating a new browser session.

## Security Notes

Current hardening measures include:

- Read-only filesystem for API and Traefik containers
- `no-new-privileges`
- Memory limits
- Internal Redis by default
- Session-scoped access paths

Still recommended for production:

- Replace the static API key with a real auth system
- Put the platform behind HTTPS
- Rotate credentials
- Add audit logs and access control
- Enable a proper ingress strategy for Traefik or another gateway

## Local Python Development

The API project uses `uv`.

### Install dependencies locally

```bash
cd api
uv sync
```

### Run the API locally

```bash
cd api
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Project Status

This repository is currently an MVP.

The main architectural choices are intentionally aligned with long-term expansion:

- Per-session browser isolation
- Explicit session lifecycle management
- Pool-based startup optimization
- Separate control plane and browser runtime
- Stable API-returned URLs instead of HTML rewriting hacks

## Chinese Documentation

See `README.zh-CN.md`.
