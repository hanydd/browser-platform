from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import docker
from docker.errors import NotFound

from .config import Settings
from .models import PoolContainerRecord, utcnow


class DockerRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = docker.from_env()

    async def ensure_network(self) -> None:
        await asyncio.to_thread(self._ensure_network_sync)

    def _ensure_network_sync(self) -> None:
        try:
            self.client.networks.get(self.settings.docker_network)
        except NotFound:
            self.client.networks.create(self.settings.docker_network, driver="bridge")

    async def create_idle_container(self) -> PoolContainerRecord:
        return await asyncio.to_thread(self._create_idle_container_sync)

    def _create_idle_container_sync(self) -> PoolContainerRecord:
        suffix = uuid4().hex[:12]
        name = f"{self.settings.pool_container_prefix}-{suffix}"
        labels = {
            "com.browserplatform.role": "pool",
            "com.browserplatform.managed": "true",
        }
        container = self.client.containers.run(
            self.settings.browser_image,
            detach=True,
            name=name,
            network=self.settings.docker_network,
            environment={
                "RESOLUTION": self.settings.browser_resolution,
                "CHROME_PROFILE_DIR": self.settings.browser_profile_dir,
            },
            shm_size=self.settings.browser_shm_size,
            mem_limit=self.settings.browser_mem_limit,
            nano_cpus=self.settings.browser_nano_cpus,
            read_only=True,
            tmpfs={
                "/tmp": "exec,size=1536m",
                "/var/run": "size=16m",
                "/root": "size=128m",
            },
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels=labels,
            restart_policy={"Name": "unless-stopped"},
        )
        return PoolContainerRecord(
            container_id=container.id,
            container_name=name,
            status="idle",
            created_at=utcnow(),
        )

    async def get_container(self, container_name: str):
        return await asyncio.to_thread(self.client.containers.get, container_name)

    async def container_exists(self, container_name: str) -> bool:
        return await asyncio.to_thread(self._container_exists_sync, container_name)

    def _container_exists_sync(self, container_name: str) -> bool:
        try:
            self.client.containers.get(container_name)
            return True
        except NotFound:
            return False

    async def remove_container(self, container_name: str) -> None:
        await asyncio.to_thread(self._remove_container_sync, container_name)

    def _remove_container_sync(self, container_name: str) -> None:
        try:
            container = self.client.containers.get(container_name)
            container.remove(force=True)
        except NotFound:
            return

    async def get_container_ip(self, container_name: str) -> str:
        return await asyncio.to_thread(self._get_container_ip_sync, container_name)

    def _get_container_ip_sync(self, container_name: str) -> str:
        container = self.client.containers.get(container_name)
        container.reload()
        return container.attrs["NetworkSettings"]["Networks"][self.settings.docker_network]["IPAddress"]

    async def exec(self, container_name: str, command: list[str]) -> tuple[int, str]:
        return await asyncio.to_thread(self._exec_sync, container_name, command)

    def _exec_sync(self, container_name: str, command: list[str]) -> tuple[int, str]:
        container = self.client.containers.get(container_name)
        result = container.exec_run(command)
        output = result.output.decode("utf-8", errors="ignore") if result.output else ""
        return result.exit_code, output

    async def start_browser(self, container_name: str) -> None:
        code, output = await self.exec(container_name, ["/usr/local/bin/browserctl", "start"])
        if code != 0:
            raise RuntimeError(f"failed to start browser in {container_name}: {output}")

    async def stop_browser(self, container_name: str) -> None:
        await self.exec(container_name, ["/usr/local/bin/browserctl", "stop"])

    async def reset_profile(self, container_name: str) -> None:
        code, output = await self.exec(container_name, ["/usr/local/bin/browserctl", "reset-profile"])
        if code != 0:
            raise RuntimeError(f"failed to reset profile in {container_name}: {output}")

    async def browser_status(self, container_name: str) -> str:
        _, output = await self.exec(container_name, ["/usr/local/bin/browserctl", "status"])
        return output.strip()

    async def save_profile(self, container_name: str, archive_path: Path) -> None:
        await asyncio.to_thread(self._save_profile_sync, container_name, archive_path)

    def _save_profile_sync(self, container_name: str, archive_path: Path) -> None:
        container = self.client.containers.get(container_name)
        bits, _ = container.get_archive(self.settings.browser_profile_dir)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("wb") as file_handle:
            for chunk in bits:
                file_handle.write(chunk)

    async def restore_profile(self, container_name: str, archive_path: Path) -> None:
        if not archive_path.exists():
            return
        await asyncio.to_thread(self._restore_profile_sync, container_name, archive_path)

    def _restore_profile_sync(self, container_name: str, archive_path: Path) -> None:
        container = self.client.containers.get(container_name)
        data = archive_path.read_bytes()
        ok = container.put_archive("/tmp", data)
        if not ok:
            raise RuntimeError(f"failed to restore profile archive for {container_name}")
