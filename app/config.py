import os

import psutil
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Service
    VERSION: str = "0.1.0"

    # Database / cache
    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379/0"

    # LLM
    LLM_BASE_URL: str
    LLM_API_KEY: str = ""
    LLM_MODEL: str
    LLM_RETRIEVE_MODEL: str = ""

    # Auth
    ADMIN_EMAIL: str
    ADMIN_PASSWORD: str
    JWT_SECRET: str
    JWT_EXPIRE_HOURS: int = 1
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Storage
    FILES_DIR: str = "/data/files"
    UPLOAD_DIR: str = "/tmp/uploads"

    # Limits / behaviour
    RATE_LIMIT_PER_MINUTE: int = 0

    # SMTP (optional welcome emails)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Adaptive properties — computed from host RAM ──────────────────────────

    @property
    def max_file_size_mb(self) -> int:
        """Max upload size; honours MAX_FILE_SIZE_MB env override."""
        env_val = os.environ.get("MAX_FILE_SIZE_MB")
        if env_val:
            return int(env_val)
        ram_gb = psutil.virtual_memory().total / (1024**3)
        if ram_gb <= 4:
            return 20
        if ram_gb <= 8:
            return 50
        if ram_gb <= 16:
            return 100
        return 200

    @property
    def worker_max_jobs(self) -> int:
        """Concurrent indexing jobs; honours WORKER_MAX_JOBS env override."""
        env_val = os.environ.get("WORKER_MAX_JOBS")
        if env_val:
            return int(env_val)
        ram_gb = psutil.virtual_memory().total / (1024**3)
        if ram_gb <= 4:
            return 1
        if ram_gb <= 8:
            return 2
        if ram_gb <= 16:
            return 3
        return 4

    @property
    def db_pool_size(self) -> int:
        ram_gb = psutil.virtual_memory().total / (1024**3)
        if ram_gb <= 4:
            return 5
        if ram_gb <= 8:
            return 10
        return 20


settings = Settings()
