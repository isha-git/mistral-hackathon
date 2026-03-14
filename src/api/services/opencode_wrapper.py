"""
OpenCode REST API integration.
Connects to an `opencode serve` instance via HTTP and SSE for interactive coding sessions.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from src.api.config.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result from running an OpenCode task."""

    success: bool
    output: str
    session_id: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    error: Optional[str] = None
    needs_permission: bool = False
    permission_id: Optional[str] = None
    permission_description: Optional[str] = None


def _get_base_url() -> str:
    settings = get_settings()
    return settings.opencode_url.rstrip("/")


def _get_auth() -> httpx.BasicAuth | None:
    settings = get_settings()
    if settings.opencode_server_password:
        return httpx.BasicAuth(
            username=settings.opencode_server_username,
            password=settings.opencode_server_password,
        )
    return None


def create_session() -> str:
    """Create a new OpenCode session. Returns session ID."""
    base_url = _get_base_url()
    auth = _get_auth()

    response = httpx.post(
        f"{base_url}/session",
        json={},
        auth=auth,
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    session_id = data.get("id") or data.get("data", {}).get("id")
    logger.info(f"Created OpenCode session: {session_id}")
    return session_id


def send_prompt(session_id: str, prompt: str) -> AgentResult:
    """
    Send a prompt to an OpenCode session and collect the response.
    Streams SSE events to capture output, permission requests, and completion.
    """
    base_url = _get_base_url()
    auth = _get_auth()
    settings = get_settings()

    logger.info(f"Sending prompt to session {session_id}: {prompt[:100]}...")

    try:
        # Send prompt (async — returns immediately, agent starts working)
        # Use connect=10s so we detect stuck sessions quickly,
        # but allow the full timeout for the LLM to finish generating.
        prompt_response = httpx.post(
            f"{base_url}/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": prompt}],
            },
            auth=auth,
            timeout=httpx.Timeout(
                connect=10.0,
                read=120.0,
                write=10.0,
                pool=10.0,
            ),
        )
        prompt_response.raise_for_status()

        # Collect result from response
        result_data = prompt_response.json()
        output_text = _extract_text_from_response(result_data)

        logger.info(f"OpenCode completed. Output preview: {output_text[:200]}...")

        return AgentResult(
            success=True,
            output=output_text,
            session_id=session_id,
        )

    except httpx.TimeoutException:
        logger.error(f"OpenCode request timed out for session {session_id}")
        return AgentResult(
            success=False,
            output="",
            error="Request timed out",
            session_id=session_id,
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenCode HTTP error: {e.response.status_code} {e.response.text}")
        return AgentResult(
            success=False,
            output="",
            error=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            session_id=session_id,
        )
    except Exception as e:
        logger.error(f"OpenCode error: {e}")
        return AgentResult(
            success=False,
            output="",
            error=str(e),
            session_id=session_id,
        )


def respond_to_permission(session_id: str, permission_id: str, allow: bool = True):
    """Respond to an OpenCode permission request."""
    base_url = _get_base_url()
    auth = _get_auth()

    response = httpx.post(
        f"{base_url}/session/{session_id}/permissions/{permission_id}",
        json={"response": "allow" if allow else "deny"},
        auth=auth,
        timeout=30.0,
    )
    response.raise_for_status()
    logger.info(
        f"Responded to permission {permission_id}: {'allow' if allow else 'deny'}"
    )


def get_session_messages(session_id: str) -> list[dict]:
    """Get all messages in a session."""
    base_url = _get_base_url()
    auth = _get_auth()

    response = httpx.get(
        f"{base_url}/session/{session_id}/message",
        auth=auth,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def run_opencode_task(
    prompt: str,
    working_dir: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AgentResult:
    """
    High-level: send a prompt to OpenCode and get the result.
    Creates a new session if none provided.

    Args:
        prompt: The task description/prompt
        working_dir: Working directory (OpenCode server must have access)
        session_id: Existing OpenCode session ID to continue

    Returns:
        AgentResult with success status, output, and session ID for continuity
    """
    try:
        if not session_id:
            session_id = create_session()

        result = send_prompt(session_id, prompt)

        # If the existing session failed (e.g. stale after restart), retry with a fresh one
        if not result.success and session_id:
            logger.warning(
                f"Session {session_id} failed, creating fresh session and retrying"
            )
            session_id = create_session()
            result = send_prompt(session_id, prompt)

        return result

    except Exception as e:
        logger.error(f"OpenCode task error: {e}")
        return AgentResult(
            success=False,
            output="",
            error=str(e),
            session_id=session_id,
        )


def _extract_text_from_response(data: dict) -> str:
    """Extract text content from OpenCode API response."""
    # Response may be a message object with parts
    parts = data.get("parts", [])
    if not parts and "data" in data:
        parts = data["data"].get("parts", [])

    text_parts = []
    for part in parts:
        if isinstance(part, dict):
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif "content" in part:
                text_parts.append(str(part["content"]))
            elif "text" in part:
                text_parts.append(part["text"])

    if text_parts:
        return "\n".join(text_parts).strip()

    # Fallback: try common response shapes
    if isinstance(data, dict):
        for key in ("text", "content", "result", "message"):
            if key in data and isinstance(data[key], str):
                return data[key]

    return json.dumps(data) if data else "Task completed with no output"
