"""Tests for webhook endpoint authentication."""

import pytest
from unittest.mock import patch, AsyncMock

import fakeredis
from fastapi.testclient import TestClient

from src.api.models.job import Reply

_fake_redis = fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def client():
    """TestClient with Redis mocked to avoid real connections."""
    with (
        patch("src.api.config.redis.get_redis_client", return_value=_fake_redis),
        patch("src.api.main.get_redis_client", return_value=_fake_redis),
    ):
        from src.api.main import app

        yield TestClient(app)


@pytest.fixture
def valid_message():
    return {
        "sender": "1234567890",
        "type": "text",
        "text": "Build me a todo app",
    }


def test_webhook_rejects_missing_secret(client, valid_message):
    """When webhook_secret is set, missing header should be rejected."""
    with patch("src.api.routes.webhook.get_settings") as mock_settings:
        mock_settings.return_value.webhook_secret = "my-secret"
        response = client.post("/webhook", json=valid_message)
        assert response.status_code == 401


def test_webhook_rejects_wrong_secret(client, valid_message):
    """When webhook_secret is set, wrong header should be rejected."""
    with patch("src.api.routes.webhook.get_settings") as mock_settings:
        mock_settings.return_value.webhook_secret = "my-secret"
        response = client.post(
            "/webhook",
            json=valid_message,
            headers={"X-Webhook-Secret": "wrong-secret"},
        )
        assert response.status_code == 401


def test_webhook_accepts_correct_secret(client, valid_message):
    """When webhook_secret matches, request should be accepted."""
    with (
        patch("src.api.routes.webhook.get_settings") as mock_settings,
        patch(
            "src.api.routes.webhook.process_message", new_callable=AsyncMock
        ) as mock_pipeline,
    ):
        mock_settings.return_value.webhook_secret = "my-secret"
        mock_pipeline.return_value = Reply(type="text", text="Got it!")
        response = client.post(
            "/webhook",
            json=valid_message,
            headers={"X-Webhook-Secret": "my-secret"},
        )
        assert response.status_code == 200
        assert response.json()["type"] == "text"


def test_webhook_no_secret_configured_passes(client, valid_message):
    """When webhook_secret is not configured, all requests pass through."""
    with (
        patch("src.api.routes.webhook.get_settings") as mock_settings,
        patch(
            "src.api.routes.webhook.process_message", new_callable=AsyncMock
        ) as mock_pipeline,
    ):
        mock_settings.return_value.webhook_secret = None
        mock_pipeline.return_value = Reply(type="text", text="Got it!")
        response = client.post("/webhook", json=valid_message)
        assert response.status_code == 200


def test_webhook_rejects_non_text_message(client):
    """Non-text messages should get a fallback reply."""
    msg = {"sender": "123", "type": "audio", "media_base64": "abc"}
    with (
        patch("src.api.routes.webhook.get_settings") as mock_settings,
        patch(
            "src.api.routes.webhook.process_message", new_callable=AsyncMock
        ) as mock_pipeline,
    ):
        mock_settings.return_value.webhook_secret = None
        mock_pipeline.return_value = Reply(
            type="text", text="I only understand text messages for now."
        )
        response = client.post("/webhook", json=msg)
        assert response.status_code == 200
