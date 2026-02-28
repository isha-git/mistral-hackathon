"""
Direct Python API integration with Mistral Vibe.
Loads session history from vibe's session directories.
"""

import os
import json
import re
import tempfile
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Import vibe direct API (no TUI)
from vibe.core.paths.config_paths import unlock_config_paths

unlock_config_paths()
from vibe.core.programmatic import run_programmatic
from vibe.core.config import VibeConfig
from vibe.core.types import OutputFormat, LLMMessage, Role

logger = logging.getLogger(__name__)


@dataclass
class VibeResult:
    """Result from running vibe."""

    success: bool
    output: str
    files_changed: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _load_session_messages(working_dir: str) -> list[LLMMessage]:
    """
    Load messages from vibe's latest session in the working directory.

    Returns list of LLMMessage objects for previous_messages parameter.
    """
    sessions_dir = Path(working_dir) / "sessions"
    if not sessions_dir.exists():
        return []

    # Find all session directories
    session_dirs = [d for d in sessions_dir.iterdir() if d.is_dir()]
    if not session_dirs:
        return []

    # Sort by modification time (latest first)
    session_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

    messages = []
    for session_dir in session_dirs:
        messages_file = session_dir / "messages.jsonl"
        if not messages_file.exists():
            continue

        try:
            with open(messages_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    msg_data = json.loads(line)

                    # Convert vibe's format to LLMMessage
                    role_str = msg_data.get("role", "").lower()
                    content = msg_data.get("content", "")

                    if role_str == "user":
                        role = Role.user
                    elif role_str == "assistant":
                        role = Role.assistant
                    else:
                        continue  # Skip system/tool messages for continuity

                    messages.append(LLMMessage(role=role, content=content))

            logger.info(
                f"Loaded {len(messages)} messages from session {session_dir.name}"
            )
            return messages  # Return messages from latest valid session

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load session {session_dir}: {e}")
            continue

    return []


def run_vibe_task(
    prompt: str,
    working_dir: Optional[str] = None,
    max_turns: int = 10,
    previous_messages: Optional[list] = None,
) -> VibeResult:
    """
    Run a vibe task programmatically without TUI.
    Loads conversation history from vibe's session files.

    Args:
        prompt: The task description/prompt
        working_dir: Working directory (creates temp if not provided)
        max_turns: Maximum number of turns
        previous_messages: Optional list of previous messages (deprecated, use working_dir sessions)

    Returns:
        VibeResult with success status and output
    """
    # Setup working directory
    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="vibe_work_")

    os.makedirs(working_dir, exist_ok=True)

    # Initialize git if needed
    git_dir = Path(working_dir) / ".git"
    if not git_dir.exists():
        import subprocess

        subprocess.run(["git", "init"], cwd=working_dir, capture_output=True)

    logger.info(f"Running vibe task in {working_dir}")
    logger.info(f"Prompt: {prompt[:100]}...")

    # Load previous messages from vibe's session files
    session_messages = _load_session_messages(working_dir)

    # Use provided previous_messages if given, otherwise use loaded sessions
    if previous_messages:
        # Convert dict format to LLMMessage if needed
        final_messages = []
        for msg in previous_messages:
            if isinstance(msg, dict):
                role_str = msg.get("role", "user")
                content = msg.get("content", "")
                role = Role.assistant if role_str == "assistant" else Role.user
                final_messages.append(LLMMessage(role=role, content=content))
            elif isinstance(msg, LLMMessage):
                final_messages.append(msg)
    else:
        final_messages = session_messages

    if final_messages:
        logger.info(
            f"Continuing with {len(final_messages)} previous messages from session"
        )

    # Save and change directory
    original_dir = os.getcwd()
    os.chdir(working_dir)

    try:
        # Setup vibe config directory if not exists
        vibe_home = Path(os.environ.get("VIBE_HOME", Path.home() / ".vibe"))
        vibe_home.mkdir(parents=True, exist_ok=True)

        # Create minimal config - keep session logging enabled so vibe tracks history
        config_file = vibe_home / "config.toml"
        if not config_file.exists():
            config_file.write_text("""[session_logging]
enabled = true
save_dir = "sessions"

[telemetry]
enabled = false
""")

        # Load config and run
        config = VibeConfig.load()

        output = run_programmatic(
            config=config,
            prompt=prompt,
            max_turns=max_turns,
            output_format=OutputFormat.TEXT,
            previous_messages=final_messages if final_messages else None,
        )

        result = output or "Task completed with no output"

        logger.info(f"Vibe completed successfully")
        logger.info(f"Output: {result[:200]}...")

        # Extract file changes from output
        files_changed = []
        if "File created:" in result:
            files_changed = re.findall(r"File created:\s*`?([^`\n]+)", result)

        return VibeResult(
            success=True,
            output=result,
            files_changed=files_changed,
        )

    except Exception as e:
        logger.error(f"Vibe error: {e}")
        return VibeResult(
            success=False,
            output="",
            error=str(e),
        )
    finally:
        os.chdir(original_dir)
