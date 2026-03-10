from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from redis.asyncio import Redis
from websockets import connect as ws_connect

from .config import get_settings
from .docker_runtime import DockerRuntime
from .logging_setup import configure_logging
from .models import HealthResponse, SessionCreateRequest
from .service import SessionService
from .store import RedisStore

logger = logging.getLogger(__name__)
SESSION_CREATE_COUNTER = Counter("browser_platform_sessions_created_total", "Total created browser sessions")
SESSION_DELETE_COUNTER = Counter("browser_platform_sessions_deleted_total", "Total deleted browser sessions")
ACTIVE_SESSIONS_GAUGE = Gauge("browser_platform_active_sessions", "Active browser sessions")
POOL_IDLE_GAUGE = Gauge("browser_platform_pool_idle", "Idle containers in the warm pool")


def build_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def strip_auth_query_items(query_params):
    return [(key, value) for key, value in query_params.multi_items() if key not in {"token", "api_key"}]


def extract_api_key(headers, query_params) -> str | None:
    auth = headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    return headers.get("x-api-key") or query_params.get("api_key") or query_params.get("token")


async def authorize_request(request: Request) -> None:
    settings = request.app.state.settings
    token = extract_api_key(request.headers, request.query_params)
    if token != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")


async def authorize_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    token = extract_api_key(websocket.headers, websocket.query_params)
    if token != settings.api_key:
        await websocket.close(code=4401, reason="invalid api key")
        raise RuntimeError("unauthorized websocket")


async def housekeeping_loop(app: FastAPI) -> None:
    settings = app.state.settings
    while True:
        try:
            await app.state.session_service.housekeeping_once()
            pool = await app.state.session_service.pool_stats()
            sessions = await app.state.store.list_sessions()
            ACTIVE_SESSIONS_GAUGE.set(len(sessions))
            POOL_IDLE_GAUGE.set(pool.idle)
        except Exception:
            logger.exception("housekeeping_loop_failed")
        await asyncio.sleep(settings.housekeeping_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    store = RedisStore(redis)
    runtime = DockerRuntime(settings)
    service = SessionService(settings, store, runtime)

    app.state.settings = settings
    app.state.redis = redis
    app.state.store = store
    app.state.runtime = runtime
    app.state.session_service = service

    await service.startup()
    task = asyncio.create_task(housekeeping_loop(app))
    app.state.housekeeping_task = task
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await redis.aclose()


app = FastAPI(title="Browser Platform API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info(
        "request_complete",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
        },
    )
    return response


@app.get("/healthz", response_model=HealthResponse)
async def healthz(request: Request):
    ok = await request.app.state.store.ping()
    return HealthResponse(ok=ok)


@app.get("/metrics")
async def metrics(request: Request):
    await authorize_request(request)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/sessions")
async def create_session(payload: SessionCreateRequest, request: Request):
    await authorize_request(request)
    try:
        response = await request.app.state.session_service.create_session(payload, build_base_url(request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    SESSION_CREATE_COUNTER.inc()
    sessions = await request.app.state.store.list_sessions()
    ACTIVE_SESSIONS_GAUGE.set(len(sessions))
    return response


@app.get("/api/sessions")
async def list_sessions(request: Request):
    await authorize_request(request)
    return await request.app.state.session_service.list_sessions(build_base_url(request))


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    await authorize_request(request)
    response = await request.app.state.session_service.get_session(session_id, build_base_url(request))
    if not response:
        raise HTTPException(status_code=404, detail="session not found")
    return response


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request):
    await authorize_request(request)
    await request.app.state.session_service.delete_session(session_id)
    SESSION_DELETE_COUNTER.inc()
    sessions = await request.app.state.store.list_sessions()
    ACTIVE_SESSIONS_GAUGE.set(len(sessions))
    return Response(status_code=204)


@app.post("/api/sessions/{session_id}/keep-alive")
async def keep_alive(session_id: str, request: Request):
    await authorize_request(request)
    try:
        return await request.app.state.session_service.keep_alive(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@app.get("/api/pool")
async def pool_stats(request: Request):
    await authorize_request(request)
    return await request.app.state.session_service.pool_stats()


async def _get_target(request: Request, session_id: str) -> tuple[str, str]:
    session = await request.app.state.store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session.container_ip, session.container_name


def _build_target_url(container_ip: str, port: int, path: str, query_params) -> str:
    path_part = path.lstrip("/")
    target = f"http://{container_ip}:{port}"
    if path_part:
        target = f"{target}/{path_part}"
    query = urlencode(strip_auth_query_items(query_params))
    if query:
        target = f"{target}?{query}"
    return target


async def _proxy_http(request: Request, session_id: str, path: str, port: int) -> Response:
    await authorize_request(request)
    session = await request.app.state.store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    container_ip = session.container_ip
    target = _build_target_url(container_ip, port, path, request.query_params)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        response = await client.request(
            request.method,
            target,
            headers=headers,
            content=await request.body(),
        )
    filtered_headers = {
        k: v
        for k, v in response.headers.items()
        if k.lower() not in {"content-encoding", "transfer-encoding", "connection"}
    }
    content = response.content
    if port == 9222 and response.headers.get("content-type", "").startswith("application/json"):
        try:
            payload = response.json()
            prefix = f"{build_base_url(request).rstrip('/')}/sessions/{session_id}/cdp"
            token = request.app.state.settings.api_key
            ws_prefix = prefix.replace("http://", "ws://").replace("https://", "wss://")

            def rewrite_item(item):
                if isinstance(item, dict) and "webSocketDebuggerUrl" in item:
                    ws_url = item["webSocketDebuggerUrl"]
                    ws_path = ws_url.split("/", 3)[-1] if ws_url.startswith("ws://") or ws_url.startswith("wss://") else ws_url.lstrip("/")
                    item["webSocketDebuggerUrl"] = f"{ws_prefix}/{ws_path}?token={token}"
                return item

            if isinstance(payload, dict):
                payload = rewrite_item(payload)
            elif isinstance(payload, list):
                payload = [rewrite_item(item) for item in payload]
            content = json.dumps(payload).encode("utf-8")
            filtered_headers["content-type"] = "application/json"
        except (ValueError, TypeError):
            pass
    return Response(content=content, status_code=response.status_code, headers=filtered_headers)


@app.api_route("/sessions/{session_id}/cdp", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.api_route("/sessions/{session_id}/cdp/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_cdp_http(session_id: str, request: Request, path: str = ""):
    return await _proxy_http(request, session_id, path, 9222)


@app.api_route("/sessions/{session_id}/vnc", methods=["GET", "POST", "OPTIONS"])
@app.api_route("/sessions/{session_id}/vnc/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def proxy_vnc_http(session_id: str, request: Request, path: str = ""):
    return await _proxy_http(request, session_id, path, 6080)


async def _pump_ws_client_to_remote(client_ws: WebSocket, remote_ws) -> None:
    while True:
        message = await client_ws.receive()
        if message["type"] == "websocket.disconnect":
            break
        if message.get("text") is not None:
            await remote_ws.send(message["text"])
        elif message.get("bytes") is not None:
            await remote_ws.send(message["bytes"])


async def _pump_ws_remote_to_client(client_ws: WebSocket, remote_ws) -> None:
    async for message in remote_ws:
        if isinstance(message, bytes):
            await client_ws.send_bytes(message)
        else:
            await client_ws.send_text(message)


async def _proxy_ws(websocket: WebSocket, session_id: str, path: str, port: int) -> None:
    await authorize_websocket(websocket)
    session = await websocket.app.state.store.get_session(session_id)
    if not session:
        await websocket.close(code=4404, reason="session not found")
        return

    query = urlencode(strip_auth_query_items(websocket.query_params))
    url = f"ws://{session.container_ip}:{port}"
    if path:
        url = f"{url}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"

    await websocket.accept()
    try:
        async with ws_connect(url, max_size=None) as remote_ws:
            await asyncio.gather(
                _pump_ws_client_to_remote(websocket, remote_ws),
                _pump_ws_remote_to_client(websocket, remote_ws),
            )
    except WebSocketDisconnect:
        return
    except Exception:
        await websocket.close(code=1011)


@app.websocket("/sessions/{session_id}/cdp/{path:path}")
async def proxy_cdp_ws(websocket: WebSocket, session_id: str, path: str):
    await _proxy_ws(websocket, session_id, path, 9222)


@app.websocket("/sessions/{session_id}/vnc/{path:path}")
async def proxy_vnc_ws(websocket: WebSocket, session_id: str, path: str):
    await _proxy_ws(websocket, session_id, path, 6080)
