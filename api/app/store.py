from __future__ import annotations

import json

from redis.asyncio import Redis

from .models import SessionRecord


class RedisStore:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def save_session(self, session: SessionRecord) -> None:
        await self.redis.set(f"session:{session.session_id}", session.model_dump_json())
        await self.redis.set(f"user-session:{session.user_id}", session.session_id)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        raw = await self.redis.get(f"session:{session_id}")
        if not raw:
            return None
        return SessionRecord.model_validate_json(raw)

    async def list_sessions(self) -> list[SessionRecord]:
        sessions: list[SessionRecord] = []
        async for key in self.redis.scan_iter(match="session:*"):
            if key.startswith("session:") and key.count(":") == 1:
                raw = await self.redis.get(key)
                if raw:
                    sessions.append(SessionRecord.model_validate_json(raw))
        sessions.sort(key=lambda item: item.created_at)
        return sessions

    async def delete_session(self, session_id: str, user_id: str) -> None:
        await self.redis.delete(f"session:{session_id}")
        current = await self.redis.get(f"user-session:{user_id}")
        if current == session_id:
            await self.redis.delete(f"user-session:{user_id}")

    async def get_user_session(self, user_id: str) -> SessionRecord | None:
        session_id = await self.redis.get(f"user-session:{user_id}")
        if not session_id:
            return None
        return await self.get_session(session_id)

