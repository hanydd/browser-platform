from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key: str = Field(default="change-me", alias="API_KEY")
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")
    browser_image: str = Field(default="agent-desk:latest", alias="BROWSER_IMAGE")
    docker_network: str = Field(default="browser-platform", alias="DOCKER_NETWORK")
    browser_resolution: str = Field(default="1280x800x24", alias="BROWSER_RESOLUTION")
    browser_profile_dir: str = Field(default="/tmp/chrome-data", alias="BROWSER_PROFILE_DIR")
    browser_mem_limit: str = Field(default="2g", alias="BROWSER_MEM_LIMIT")
    browser_nano_cpus: int = Field(default=2_000_000_000, alias="BROWSER_NANO_CPUS")
    browser_shm_size: str = Field(default="2g", alias="BROWSER_SHM_SIZE")
    default_ttl_seconds: int = Field(default=1800, alias="DEFAULT_TTL_SECONDS")
    housekeeping_interval_seconds: int = Field(default=15, alias="HOUSEKEEPING_INTERVAL_SECONDS")
    profile_archive_dir: str = Field(default="/data/profiles", alias="PROFILE_ARCHIVE_DIR")
    browser_container_prefix: str = Field(default="browser-session", alias="BROWSER_CONTAINER_PREFIX")
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")

    @property
    def profile_archive_path(self) -> Path:
        path = Path(self.profile_archive_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
