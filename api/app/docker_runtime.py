from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path, PurePosixPath
from uuid import uuid4

import docker
from docker.errors import NotFound

from .config import Settings

logger = logging.getLogger(__name__)


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

    async def create_container(self) -> tuple[str, str]:
        """Create a new browser container on demand. Returns (container_id, container_name)."""
        return await asyncio.to_thread(self._create_container_sync)

    def _create_container_sync(self) -> tuple[str, str]:
        suffix = uuid4().hex[:12]
        name = f"{self.settings.browser_container_prefix}-{suffix}"
        labels = {
            "com.browserplatform.role": "session",
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
                "/var/log": "size=16m",
                "/root": "size=128m",
            },
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels=labels,
            restart_policy={"Name": "no"},
        )
        return container.id, name

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

    async def browserctl(self, container_name: str, *args: str) -> tuple[int, str]:
        return await self.exec(container_name, ["/usr/local/bin/browserctl", *args])

    async def start_browser(self, container_name: str) -> None:
        code, output = await self.browserctl(container_name, "start")
        if code != 0:
            raise RuntimeError(f"failed to start browser in {container_name}: {output}")

    async def wait_for_display(self, container_name: str, attempts: int = 20, delay: float = 0.5) -> None:
        """Wait until Xvfb display is up inside the container.

        We check for an Xvfb process before starting Chromium to avoid
        'Missing X server or $DISPLAY' errors when the container has just started.
        """
        for _ in range(attempts):
            code, output = await self.exec(
                container_name,
                ["sh", "-lc", "ps aux | grep '[X]vfb' || true"],
            )
            if code == 0 and "Xvfb" in output:
                return
            await asyncio.sleep(delay)
        raise RuntimeError(f"display server in {container_name} did not become ready in time")

    async def stop_browser(self, container_name: str) -> None:
        await self.browserctl(container_name, "stop")

    async def reset_profile(self, container_name: str) -> None:
        code, output = await self.browserctl(container_name, "reset-profile")
        if code != 0:
            raise RuntimeError(f"failed to reset profile in {container_name}: {output}")

    async def cleanup_profile_locks(self, container_name: str) -> None:
        """Remove Chromium profile lock files after restoring a profile.

        When we reuse a saved user profile in a *new* container, Chromium may
        see old lock files (Singleton*) and refuse to start, thinking the
        profile is in use on another machine. We can safely delete these.
        """
        cmd = [
            "sh",
            "-lc",
            (
                f'cd "{self.settings.browser_profile_dir}" 2>/dev/null || exit 0; '
                "rm -f Singleton* lockfile *.lock 2>/dev/null || true"
            ),
        ]
        # We don't care about exit code here; best-effort cleanup.
        await self.exec(container_name, cmd)

    async def browser_status(self, container_name: str) -> str:
        _, output = await self.browserctl(container_name, "status")
        return output.strip()

    async def save_profile(self, container_name: str, archive_path: Path) -> None:
        await asyncio.to_thread(self._save_profile_sync, container_name, archive_path)

    def _save_profile_sync(self, container_name: str, archive_path: Path) -> None:
        container = self.client.containers.get(container_name)
        profile_dir = PurePosixPath(self.settings.browser_profile_dir)
        target_dir = str(profile_dir.parent)
        profile_name = profile_dir.name
        logger.info(
            "save_profile_started",
            extra={
                "container_name": container_name,
                "archive_path": str(archive_path),
                "profile_dir": self.settings.browser_profile_dir,
                "tar_workdir": target_dir,
            },
        )
        exec_id = self.client.api.exec_create(
            container.id,
            ["tar", "-cf", "-", "-C", target_dir, profile_name],
        )["Id"]
        exec_socket = self.client.api.exec_start(exec_id, socket=True)
        raw_socket = getattr(exec_socket, "_sock", exec_socket)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        stderr_chunks: list[bytes] = []
        buffer = b""
        try:
            with archive_path.open("wb") as file_handle:
                while True:
                    chunk = raw_socket.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while len(buffer) >= 8:
                        stream_type = buffer[0]
                        payload_size = int.from_bytes(buffer[4:8], "big")
                        frame_size = 8 + payload_size
                        if len(buffer) < frame_size:
                            break
                        payload = buffer[8:frame_size]
                        buffer = buffer[frame_size:]
                        if stream_type == 1:
                            file_handle.write(payload)
                            total_bytes += len(payload)
                        elif stream_type in {2, 3}:
                            stderr_chunks.append(payload)
        finally:
            exec_socket.close()
        result = self.client.api.exec_inspect(exec_id)
        stderr_output = b"".join(stderr_chunks).decode("utf-8", errors="ignore").strip()
        logger.info(
            "save_profile_finished",
            extra={
                "container_name": container_name,
                "archive_path": str(archive_path),
                "profile_dir": self.settings.browser_profile_dir,
                "exit_code": result["ExitCode"],
                "bytes_written": total_bytes,
                "archive_exists": archive_path.exists(),
                "stderr_output": stderr_output,
            },
        )
        if result["ExitCode"] != 0:
            logger.error(
                "save_profile_failed",
                extra={
                    "container_name": container_name,
                    "archive_path": str(archive_path),
                    "profile_dir": self.settings.browser_profile_dir,
                    "exit_code": result["ExitCode"],
                    "bytes_written": total_bytes,
                    "stderr_output": stderr_output,
                },
            )
            archive_path.unlink(missing_ok=True)
            raise RuntimeError(f"failed to save profile archive for {container_name}")

    async def restore_profile(self, container_name: str, archive_path: Path) -> None:
        if not archive_path.exists():
            return
        await asyncio.to_thread(self._restore_profile_sync, container_name, archive_path)

    def _restore_profile_sync(self, container_name: str, archive_path: Path) -> None:
        container = self.client.containers.get(container_name)
        data = archive_path.read_bytes()
        target_dir = str(PurePosixPath(self.settings.browser_profile_dir).parent)
        exec_id = self.client.api.exec_create(
            container.id,
            ["tar", "-xf", "-", "-C", target_dir],
            stdin=True,
        )["Id"]
        exec_socket = self.client.api.exec_start(exec_id, socket=True)
        raw_socket = getattr(exec_socket, "_sock", exec_socket)
        try:
            raw_socket.sendall(data)
            raw_socket.shutdown(socket.SHUT_WR)
            while raw_socket.recv(4096):
                pass
        finally:
            exec_socket.close()

        result = self.client.api.exec_inspect(exec_id)
        if result["ExitCode"] != 0:
            logger.error(
                "restore_profile_failed",
                extra={
                    "container_name": container_name,
                    "archive_path": str(archive_path),
                    "target_dir": target_dir,
                    "exit_code": result["ExitCode"],
                },
            )
            raise RuntimeError(f"failed to restore profile archive for {container_name}")
