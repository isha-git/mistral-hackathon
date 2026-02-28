"""
Direct Python API integration with Mistral Vibe.
Loads session history from vibe's session directories.

Restricts the agent to read-only code access + custom git tools.
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

# Read-only built-in tools + custom git tools
_ENABLED_TOOLS = [
    "grep",
    "read_file",
    "safe_write_file",      # replaces write_file — enforces branch safety
    "ask_user_question",
    "task",
    "git_clone",
    "git_branch",
    "git_log",
    "git_diff",
    "git_status",
    "git_pr",
]

_TOOLS_DIR = Path(__file__).parent / "tools"

_AGENT_INSTRUCTIONS = """\
IMPORTANT RULES — follow these for every task:
- You MUST use your tools to complete tasks. Never give the user bash commands to run manually.
- Use `safe_write_file` for ALL file writes. It automatically commits, pushes, and creates a GitHub PR.
- Use `git_clone` to clone repos. Use `read_file` and `grep` to read code.
- Use `git_branch` to checkout existing branches for reading.
- You have GITHUB_TOKEN set. You can push and create PRs via your tools.
- Do NOT say "I cannot do X" — use your available tools instead.
- After using safe_write_file, tell the user the PR URL from the result.

"""


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


def _inject_github_token() -> None:
    """Set GITHUB_TOKEN env var from settings if available."""
    if os.environ.get("GITHUB_TOKEN"):
        return  # Already set
    try:
        from src.api.config.settings import get_settings

        settings = get_settings()
        if settings.github_token:
            os.environ["GITHUB_TOKEN"] = settings.github_token
    except Exception:
        pass  # Settings not available; token may still come from env


def run_vibe_task(
    prompt: str,
    working_dir: Optional[str] = None,
    max_turns: int = 10,
    previous_messages: Optional[list] = None,
) -> VibeResult:
    """
    Run a vibe task programmatically without TUI.
    Loads conversation history from vibe's session files.

    The agent is restricted to read-only tools + custom git tools.
    bash, write_file, and search_replace are NOT available.

    Args:
        prompt: The task description/prompt
        working_dir: Working directory (creates temp if not provided)
        max_turns: Maximum number of turns
        previous_messages: Optional list of previous messages (deprecated, use working_dir sessions)

    Returns:
        VibeResult with success status and output
    """
    # Ensure GITHUB_TOKEN is in the environment
    _inject_github_token()
    logger.info(f"GITHUB_TOKEN present: {bool(os.environ.get('GITHUB_TOKEN'))}")

    # Setup working directory
    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="vibe_work_")

    # Ensure parent dir exists (bind mount may be missing if host dir was deleted)
    parent = Path(working_dir).parent
    parent.mkdir(parents=True, exist_ok=True)
    os.makedirs(working_dir, exist_ok=True)

    # NOTE: Do NOT git-init here. If the agent needs a repo, git_clone will
    # create one with a proper origin. safe_write_file inits git as a fallback.

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

        # Load config and apply tool restrictions
        config = VibeConfig.load()
        config.tool_paths = [_TOOLS_DIR]
        config.enabled_tools = list(_ENABLED_TOOLS)

        # Debug: log tool path state
        logger.info(f"_TOOLS_DIR = {_TOOLS_DIR}")
        logger.info(f"_TOOLS_DIR resolved = {_TOOLS_DIR.resolve()}")
        logger.info(f"_TOOLS_DIR exists = {_TOOLS_DIR.resolve().exists()}")
        logger.info(f"config.tool_paths = {config.tool_paths}")
        logger.info(f"config.enabled_tools = {config.enabled_tools}")

        if _TOOLS_DIR.resolve().exists():
            tool_files = list(_TOOLS_DIR.resolve().glob("*.py"))
            logger.info(f"Tool files found: {[f.name for f in tool_files]}")
        else:
            logger.error(f"TOOLS DIRECTORY DOES NOT EXIST: {_TOOLS_DIR.resolve()}")

        # Diagnostic: verify custom tools are discoverable
        try:
            from vibe.core.tools.manager import ToolManager
            mgr = ToolManager(lambda: config)
            logger.info(f"ToolManager search paths: {mgr._search_paths}")
            available = list(mgr.available_tools.keys())
            logger.info(f"Available tools ({len(available)}): {available}")
            expected = {
                "git_clone", "git_log", "git_diff", "git_status",
                "git_pr", "safe_write_file",
            }
            missing = expected - set(available)
            if missing:
                logger.warning(f"Custom tools NOT discovered: {missing}")
        except Exception as e:
            logger.warning(f"Tool discovery check failed: {e}", exc_info=True)

        output = run_programmatic(
            config=config,
            prompt=_AGENT_INSTRUCTIONS + prompt,
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
