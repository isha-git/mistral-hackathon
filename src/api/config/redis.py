import redis
from functools import lru_cache
from src.api.config.settings import get_settings


@lru_cache()
def get_redis_client() -> redis.Redis:
    """Get cached Redis client instance."""
    settings = get_settings()
    return redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
