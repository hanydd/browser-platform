from __future__ import annotations

import json

from redis.asyncio import Redis

from .models import PoolContainerRecord, SessionRecord


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

    async def save_pool_container(self, item: PoolContainerRecord) -> None:
        await self.redis.set(f"pool:{item.container_name}", item.model_dump_json())

    async def get_pool_container(self, container_name: str) -> PoolContainerRecord | None:
        raw = await self.redis.get(f"pool:{container_name}")
        if not raw:
            return None
        return PoolContainerRecord.model_validate_json(raw)

    async def list_pool_containers(self) -> list[PoolContainerRecord]:
        items: list[PoolContainerRecord] = []
        async for key in self.redis.scan_iter(match="pool:*"):
            if key == "pool:idle":
                continue  # pool:idle is a SET, not a string
            if key.startswith("pool:") and key.count(":") == 1:
                raw = await self.redis.get(key)
                if raw:
                    items.append(PoolContainerRecord.model_validate_json(raw))
        items.sort(key=lambda item: item.created_at)
        return items

    async def delete_pool_container(self, container_name: str) -> None:
        await self.redis.delete(f"pool:{container_name}")
        await self.redis.srem("pool:idle", container_name)

    async def mark_pool_idle(self, container_name: str) -> None:
        await self.redis.sadd("pool:idle", container_name)

    async def mark_pool_busy(self, container_name: str) -> None:
        await self.redis.srem("pool:idle", container_name)

    async def pop_idle_pool_container(self) -> str | None:
        value = await self.redis.spop("pool:idle")
        return value or None

    async def idle_pool_count(self) -> int:
        return int(await self.redis.scard("pool:idle"))

    async def total_pool_count(self) -> int:
        count = 0
        async for key in self.redis.scan_iter(match="pool:*"):
            if key == "pool:idle":
                continue
            if key.startswith("pool:") and key.count(":") == 1:
                count += 1
        return count
