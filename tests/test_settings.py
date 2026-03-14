"""Tests for configuration settings defaults."""

from src.api.config.settings import Settings


def test_settings_defaults():
    """All settings should have sensible defaults."""
    s = Settings(api_key="test-key")

    assert s.app_name == "OpenCode API"
    assert s.debug is False
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.redis_url == "redis://localhost:6379/0"
    assert s.redis_job_ttl == 86400
    assert s.job_max_timeout == 1800
    assert s.job_retry_count == 3
    assert s.job_retry_delay == 5
    assert s.opencode_timeout == 1800


def test_settings_api_key_required():
    """API key is required when not in env or .env file."""
    import pytest
    from unittest.mock import patch

    # Patch env to remove API_KEY and disable .env file loading
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(Exception):
            Settings(_env_file=None)
