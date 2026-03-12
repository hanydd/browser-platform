from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from urllib.parse import urlparse

import httpx

from .config import Settings
from .docker_runtime import DockerRuntime
from .models import KeepAliveResponse, SessionCreateRequest, SessionRecord, SessionResponse, utcnow
from .store import RedisStore

logger = logging.getLogger(__name__)


def _safe_user_key(user_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", user_id)


class SessionService:
    def __init__(self, settings: Settings, store: RedisStore, runtime: DockerRuntime):
        self.settings = settings
        self.store = store
        self.runtime = runtime
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        await self.runtime.ensure_network()
        await self.reconcile_state()

    async def create_session(self, payload: SessionCreateRequest, base_url: str) -> SessionResponse:
        existing = await self.store.get_user_session(payload.user_id)
        if existing:
            if existing.expires_at <= utcnow() or existing.status != "running":
                await self._release_session(existing)
            else:
                raise ValueError(f"user {payload.user_id} already has an active session")

        ttl_seconds = payload.ttl_seconds or self.settings.default_ttl_seconds
        container_id, container_name = await self.runtime.create_container()

        try:
            # Wait until Xvfb is up before starting Chromium, otherwise
            # we may see 'Missing X server or $DISPLAY' and CDP never comes up.
            await self.runtime.wait_for_display(container_name)
            if payload.persist_profile:
                await self.runtime.restore_profile(
                    container_name,
                    self.profile_archive_path(payload.user_id),
                )
                # Clear any stale Chromium lock files from the restored profile.
                await self.runtime.cleanup_profile_locks(container_name)
            await self.runtime.start_browser(container_name)
            container_ip, browser_ws_path = await self.wait_for_browser(container_name)
        except Exception:
            try:
                await self.runtime.remove_container(container_name)
            except Exception:
                logger.exception("create_session_cleanup_failed", extra={"container_name": container_name})
            raise

        session = SessionRecord.create(
            user_id=payload.user_id,
            container_id=container_id,
            container_name=container_name,
            container_ip=container_ip,
            browser_ws_path=browser_ws_path,
            ttl_seconds=ttl_seconds,
            persist_profile=payload.persist_profile,
            metadata=payload.metadata,
        )
        session.status = "running"
        await self.store.save_session(session)
        return self.to_response(session, base_url)

    async def delete_session(self, session_id: str) -> None:
        session = await self.store.get_session(session_id)
        if not session:
            return
        await self._release_session(session)

    async def keep_alive(self, session_id: str) -> KeepAliveResponse:
        session = await self.store.get_session(session_id)
        if not session:
            raise KeyError(session_id)
        session.expires_at = utcnow() + timedelta(seconds=self.settings.default_ttl_seconds)
        session.last_used_at = utcnow()
        await self.store.save_session(session)
        return KeepAliveResponse(session_id=session.session_id, expires_at=session.expires_at)

    async def get_session(self, session_id: str, base_url: str) -> SessionResponse | None:
        session = await self.store.get_session(session_id)
        if not session:
            return None
        return self.to_response(session, base_url)

    async def list_sessions(self, base_url: str) -> list[SessionResponse]:
        sessions = await self.store.list_sessions()
        return [self.to_response(session, base_url) for session in sessions]

    async def housekeeping_once(self) -> None:
        await self.reconcile_state()
        sessions = await self.store.list_sessions()
        now = utcnow()
        for session in sessions:
            if session.expires_at <= now:
                await self._release_session(session)

    async def reconcile_state(self) -> None:
        sessions = await self.store.list_sessions()
        for session in sessions:
            if not await self.runtime.container_exists(session.container_name):
                logger.warning(
                    "session container missing, deleting session record",
                    extra={"session_id": session.session_id},
                )
                await self.store.delete_session(session.session_id, session.user_id)

    async def _release_session(self, session: SessionRecord) -> None:
        session.status = "releasing"
        await self.store.save_session(session)
        archive_path = self.profile_archive_path(session.user_id)

        try:
            await self.runtime.stop_browser(session.container_name)
        except Exception:
            logger.exception(
                "session_stop_browser_failed",
                extra={"session_id": session.session_id, "container_name": session.container_name},
            )

        if session.persist_profile:
            try:
                logger.info(
                    "session_profile_persist_started",
                    extra={
                        "session_id": session.session_id,
                        "user_id": session.user_id,
                        "container_name": session.container_name,
                        "archive_path": str(archive_path),
                    },
                )
                await self.runtime.save_profile(session.container_name, archive_path)
                logger.info(
                    "session_profile_persist_finished",
                    extra={
                        "session_id": session.session_id,
                        "user_id": session.user_id,
                        "container_name": session.container_name,
                        "archive_path": str(archive_path),
                        "archive_exists": archive_path.exists(),
                        "archive_size": archive_path.stat().st_size if archive_path.exists() else 0,
                    },
                )
            except Exception:
                logger.exception(
                    "session_profile_persist_failed",
                    extra={
                        "session_id": session.session_id,
                        "user_id": session.user_id,
                        "container_name": session.container_name,
                        "archive_path": str(archive_path),
                    },
                )

        await self.store.delete_session(session.session_id, session.user_id)

        try:
            await self.runtime.remove_container(session.container_name)
        except Exception:
            logger.exception(
                "session_release_remove_container_failed",
                extra={"session_id": session.session_id, "container_name": session.container_name},
            )

    async def wait_for_browser(self, container_name: str) -> tuple[str, str]:
        container_ip = await self.runtime.get_container_ip(container_name)
        async with httpx.AsyncClient(timeout=2.0) as client:
            for _ in range(30):
                try:
                    response = await client.get(f"http://{container_ip}:9222/json/version")
                    if response.status_code == 200:
                        payload = response.json()
                        ws_url = payload.get("webSocketDebuggerUrl", "ws://placeholder/devtools/browser")
                        return container_ip, urlparse(ws_url).path
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        raise RuntimeError(f"browser in {container_name} did not become ready in time")

    def profile_archive_path(self, user_id: str):
        return self.settings.profile_archive_path / f"{_safe_user_key(user_id)}.tar"

    def to_response(self, session: SessionRecord, base_url: str) -> SessionResponse:
        prefix = f"{base_url.rstrip('/')}/sessions/{session.session_id}"
        token = self.settings.api_key
        query_join = "&" if "?" in session.browser_ws_path else "?"
        ws_prefix = prefix.replace("http://", "ws://").replace("https://", "wss://")
        vnc_ws_path = f"sessions/{session.session_id}/vnc/websockify"
        return SessionResponse(
            session_id=session.session_id,
            user_id=session.user_id,
            status=session.status,
            created_at=session.created_at,
            expires_at=session.expires_at,
            cdp_url=f"{ws_prefix}/cdp{session.browser_ws_path}{query_join}token={token}",
            cdp_http_url=f"{prefix}/cdp?token={token}",
            vnc_url=f"{prefix}/vnc/?token={token}",
            viewer_url=f"{prefix}/vnc/vnc.html?autoconnect=true&resize=scale&path={vnc_ws_path}&token={token}",
        )
