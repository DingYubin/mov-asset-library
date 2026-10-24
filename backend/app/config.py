from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./data/asset_library.db"
    storage_backend: str = "local"
    local_storage_root: str = "./data/storage"
    session_ttl_seconds: int = 8 * 60 * 60
    session_idle_seconds: int = 30 * 60
    cookie_secure: bool = False
    max_upload_bytes: int = 512 * 1024 * 1024

    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = "ap-guangzhou"
    cos_bucket: str = ""
    cos_public_base_url: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def storage_root(self) -> Path:
        return Path(self.local_storage_root).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
