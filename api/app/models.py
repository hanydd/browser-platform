from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionCreateRequest(BaseModel):
    user_id: str
    ttl_seconds: int | None = None
    persist_profile: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionRecord(BaseModel):
    session_id: str
    user_id: str
    container_id: str
    container_name: str
    container_ip: str
    browser_ws_path: str
    status: Literal["starting", "running", "expired", "releasing"]
    persist_profile: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        container_id: str,
        container_name: str,
        container_ip: str,
        browser_ws_path: str,
        ttl_seconds: int,
        persist_profile: bool,
        metadata: dict[str, Any],
    ) -> "SessionRecord":
        now = utcnow()
        return cls(
            session_id=uuid4().hex,
            user_id=user_id,
            container_id=container_id,
            container_name=container_name,
            container_ip=container_ip,
            browser_ws_path=browser_ws_path,
            status="starting",
            persist_profile=persist_profile,
            metadata=metadata,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            last_used_at=now,
        )


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    status: str
    created_at: datetime
    expires_at: datetime
    cdp_url: str
    cdp_http_url: str
    vnc_url: str
    viewer_url: str


class KeepAliveResponse(BaseModel):
    session_id: str
    expires_at: datetime


class HealthResponse(BaseModel):
    ok: bool = True
