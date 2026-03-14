from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Configuration
    api_key: str
    app_name: str = "OpenCode API"
    app_version: str = "0.1.0"
    debug: bool = False

    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000

    # Redis Configuration
    redis_url: str = "redis://localhost:6379/0"
    redis_job_ttl: int = 86400  # 24 hours in seconds

    # Celery Configuration
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # OpenCode Configuration
    opencode_url: str = "http://opencode:4096"
    opencode_provider_id: str = "anthropic"
    opencode_model_id: str = "claude-sonnet-4-20250514"
    opencode_timeout: int = 1800  # 30 minutes in seconds
    opencode_server_password: str | None = None
    opencode_server_username: str = "opencode"

    # Job Configuration
    job_max_timeout: int = 1800  # 30 minutes in seconds
    job_retry_count: int = 3
    job_retry_delay: int = 5  # seconds

    # WhatsApp callback - where to send agent questions/completions back to the user
    whatsapp_callback_url: str | None = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Set Celery URLs from Redis URL if not explicitly set
        if self.celery_broker_url is None:
            self.celery_broker_url = self.redis_url
        if self.celery_result_backend is None:
            self.celery_result_backend = self.redis_url


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
