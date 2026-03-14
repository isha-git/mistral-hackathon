import os
import pytest
from unittest.mock import patch

# Set required env vars before any app imports
os.environ["API_KEY"] = "test-api-key"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"

# Clear the settings cache so our env vars take effect
from src.api.config.settings import get_settings

get_settings.cache_clear()


@pytest.fixture
def fake_redis():
    """Provide a fakeredis instance for testing."""
    import fakeredis

    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def job_service(fake_redis):
    """Provide a JobService backed by fakeredis."""
    with patch(
        "src.api.services.job_service.get_redis_client", return_value=fake_redis
    ):
        from src.api.services.job_service import JobService

        svc = JobService()
        svc.redis = fake_redis
        yield svc


@pytest.fixture
def settings():
    """Provide test settings."""
    from src.api.config.settings import Settings

    return Settings(api_key="test-api-key")
