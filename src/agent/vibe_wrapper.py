"""
Direct Python API integration with Mistral Vibe.
Loads session history from vibe's session directories.

Restricts the agent to read-only code access + custom git tools.
"""

import asyncio
import os
import json
import re
import tempfile
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable

# Import vibe direct API (no TUI)
from vibe.core.paths.config_paths import unlock_config_paths

unlock_config_paths()
from vibe.core.programmatic import run_programmatic
from vibe.core.config import VibeConfig
from vibe.core.types import OutputFormat, LLMMessage, Role

# Streaming-specific imports
from vibe import __version__ as _vibe_version
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.output_formatters import create_formatter
from vibe.core.types import (
    AssistantEvent,
    EntrypointMetadata,
    ClientMetadata,
    ToolCallEvent,
    ToolResultEvent,
)
from vibe.core.utils import ConversationLimitException

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


def _has_origin_remote(repo_path: str) -> bool:
    """Check if a git repo has an 'origin' remote configured."""
    try:
        git_config = os.path.join(repo_path, ".git", "config")
        if not os.path.isfile(git_config):
            return False
        with open(git_config) as f:
            return '[remote "origin"]' in f.read()
    except OSError:
        return False


def _enter_cloned_repo(working_dir: str) -> None:
    """
    If working_dir itself is not a git repo with an origin remote,
    look for a subdirectory that is (i.e. a previously cloned repo)
    and cd into it.  This handles continuation sessions where git_clone
    ran in a prior session and changed CWD, but the new session starts
    in the parent directory.
    """
    # Already inside a repo with origin? Nothing to do.
    if _has_origin_remote(working_dir):
        return

    # Scan immediate subdirectories for a cloned git repo with origin
    candidates = []
    try:
        for entry in os.scandir(working_dir):
            if entry.is_dir() and _has_origin_remote(entry.path):
                candidates.append(entry.path)
    except OSError:
        return

    if len(candidates) == 1:
        os.chdir(candidates[0])
        logger.info(f"Auto-entered cloned repo at {candidates[0]}")
    elif len(candidates) > 1:
        # Multiple repos — pick the most recently modified one
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        os.chdir(candidates[0])
        logger.info(f"Auto-entered most recent cloned repo at {candidates[0]}")


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
    _enter_cloned_repo(working_dir)

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


# ---------------------------------------------------------------------------
# Structured event formatting for progress page
# ---------------------------------------------------------------------------


def _make_tool_call_event(event: ToolCallEvent) -> dict:
    """Build a structured dict for a tool call event."""
    args_dict = event.args.model_dump() if event.args else {}
    return {
        "event": "tool_call",
        "tool": event.tool_name,
        "args": args_dict,
    }


def _make_tool_result_event(event: ToolResultEvent) -> dict:
    """Build a structured dict for a tool result event."""
    return {
        "event": "tool_result",
        "tool": event.tool_name,
        "result": str(event.result) if event.result else None,
        "error": event.error,
        "duration": event.duration,
    }


def run_vibe_task_streaming(
    prompt: str,
    working_dir: str | None = None,
    max_turns: int = 10,
    previous_messages: list | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> VibeResult:
    """
    Run a vibe task with real-time progress callbacks.

    Mirrors run_vibe_task() but iterates over AgentLoop events directly,
    firing ``on_progress(event_dict)`` on tool calls, tool results, and
    assistant messages so the caller can relay them to a progress page.

    ``on_progress`` is called synchronously but should be non-blocking.
    """
    _inject_github_token()
    logger.info(f"GITHUB_TOKEN present: {bool(os.environ.get('GITHUB_TOKEN'))}")

    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="vibe_work_")

    parent = Path(working_dir).parent
    parent.mkdir(parents=True, exist_ok=True)
    os.makedirs(working_dir, exist_ok=True)

    logger.info(f"Running vibe streaming task in {working_dir}")
    logger.info(f"Prompt: {prompt[:100]}...")

    # Load previous messages (same logic as run_vibe_task)
    session_messages = _load_session_messages(working_dir)
    if previous_messages:
        final_messages: list[LLMMessage] = []
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
        logger.info(f"Continuing with {len(final_messages)} previous messages")

    original_dir = os.getcwd()
    os.chdir(working_dir)
    _enter_cloned_repo(working_dir)

    try:
        # Setup vibe config (same as run_vibe_task)
        vibe_home = Path(os.environ.get("VIBE_HOME", Path.home() / ".vibe"))
        vibe_home.mkdir(parents=True, exist_ok=True)
        config_file = vibe_home / "config.toml"
        if not config_file.exists():
            config_file.write_text(
                "[session_logging]\nenabled = true\nsave_dir = \"sessions\"\n\n"
                "[telemetry]\nenabled = false\n"
            )

        config = VibeConfig.load()
        config.tool_paths = [_TOOLS_DIR]
        config.enabled_tools = list(_ENABLED_TOOLS)

        formatter = create_formatter(OutputFormat.TEXT)

        agent_loop = AgentLoop(
            config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            message_observer=formatter.on_message_added,
            max_turns=max_turns,
            max_price=None,
            enable_streaming=False,
            entrypoint_metadata=EntrypointMetadata(
                agent_entrypoint="programmatic",
                agent_version=_vibe_version,
                client_name="vibe_programmatic_streaming",
                client_version=_vibe_version,
            ),
        )

        async def _async_run() -> str | None:
            try:
                if final_messages:
                    non_system = [
                        m for m in final_messages if not (m.role == Role.system)
                    ]
                    agent_loop.messages.extend(non_system)
                    logger.info(f"Loaded {len(non_system)} messages from session")

                agent_loop.emit_new_session_telemetry()

                async for event in agent_loop.act(_AGENT_INSTRUCTIONS + prompt):
                    # --- progress callbacks ---
                    if on_progress:
                        try:
                            if isinstance(event, ToolCallEvent):
                                on_progress(_make_tool_call_event(event))
                            elif isinstance(event, ToolResultEvent):
                                on_progress(_make_tool_result_event(event))
                            elif isinstance(event, AssistantEvent):
                                on_progress({
                                    "event": "assistant",
                                    "content": event.content,
                                })
                        except Exception:
                            logger.debug("on_progress callback error", exc_info=True)

                    # --- standard formatter handling ---
                    formatter.on_event(event)
                    if isinstance(event, AssistantEvent) and event.stopped_by_middleware:
                        raise ConversationLimitException(event.content)

                return formatter.finalize()
            finally:
                await agent_loop.telemetry_client.aclose()

        output = asyncio.run(_async_run())
        result = output or "Task completed with no output"

        logger.info("Vibe streaming completed successfully")
        logger.info(f"Output: {result[:200]}...")

        files_changed = []
        if "File created:" in result:
            files_changed = re.findall(r"File created:\s*`?([^`\n]+)", result)

        return VibeResult(
            success=True,
            output=result,
            files_changed=files_changed,
        )

    except Exception as e:
        logger.error(f"Vibe streaming error: {e}")
        return VibeResult(
            success=False,
            output="",
            error=str(e),
        )
    finally:
        os.chdir(original_dir)
