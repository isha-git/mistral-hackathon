"""Safe write file tool — writes files only on a non-main branch, then commits, pushes, and creates a PR."""

import asyncio
import os
import random
from typing import AsyncGenerator, ClassVar

import anyio
import httpx
from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.core.types import ToolStreamEvent

from src.agent.tools import OWNER_REPO_RE

_MAX_CONTENT_BYTES = 64 * 1024  # 64 KB cap

# Things bunnies like to eat
_BUNNY_SNACKS = [
    "carrots", "clover", "dandelions", "parsley", "basil", "cilantro",
    "mint", "lettuce", "kale", "spinach", "arugula", "celery",
    "blueberries", "raspberries", "strawberries", "apples", "bananas",
    "watermelon", "peaches", "pears", "cherries", "plums", "mangoes",
    "papaya", "thyme", "oregano", "rosemary", "dill", "fennel",
    "chamomile", "lavender", "marigolds", "pansies", "sunflowers",
    "petunias", "nasturtiums", "violets", "hibiscus", "honeysuckle",
    "wheatgrass", "oats", "hay", "timothy", "alfalfa", "cloverleaf",
    "turnips", "radishes", "broccoli", "cucumbers", "bellpeppers",
    "pumpkin", "zucchini", "endive", "watercress", "bokchoy",
    "cookies",  # treat day
]


class SafeWriteFileArgs(BaseModel):
    path: str = Field(description="Relative file path to write (no '..' allowed)")
    content: str = Field(description="File content to write")
    overwrite: bool = Field(
        default=False,
        description="Must be True to overwrite an existing file",
    )
    commit_message: str = Field(
        default="",
        description="Git commit message. Defaults to 'Add <filename>' if empty.",
    )


class SafeWriteFileResult(BaseModel):
    path: str
    bytes_written: int
    branch: str
    branch_created: bool
    committed: bool = False
    pushed: bool = False
    pr_url: str = ""


class SafeWriteFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class SafeWriteFile(
    BaseTool[SafeWriteFileArgs, SafeWriteFileResult, SafeWriteFileConfig, BaseToolState]
):
    description: ClassVar[str] = (
        "Write content to a file, commit, push, and create a GitHub PR. "
        "Creates a bunny_loves_ branch on first write. "
        "Subsequent writes reuse the same branch and PR."
    )

    # Class-level state: remember the branch and PR we created for this session
    _session_branch: ClassVar[str | None] = None
    _session_pr_url: ClassVar[str | None] = None

    async def _current_branch(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--abbrev-ref", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    async def _ensure_git_repo(self) -> None:
        """Initialize a git repo if we're not inside one."""
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--git-dir",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            await asyncio.create_subprocess_exec(
                "git", "init",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

    async def _branch_exists(self, name: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--verify", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0

    async def _create_branch(self) -> str:
        # Shuffle so we don't always try the same order
        snacks = list(_BUNNY_SNACKS)
        random.shuffle(snacks)

        for snack in snacks:
            branch_name = f"bunny_loves_{snack}"
            if await self._branch_exists(branch_name):
                continue
            proc = await asyncio.create_subprocess_exec(
                "git", "checkout", "-b", branch_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return branch_name

        # All single snacks taken — try combos: bunny_loves_carrots_and_mint
        combos = [
            (a, b) for a in _BUNNY_SNACKS for b in _BUNNY_SNACKS if a != b
        ]
        random.shuffle(combos)

        for a, b in combos:
            branch_name = f"bunny_loves_{a}_and_{b}"
            if await self._branch_exists(branch_name):
                continue
            proc = await asyncio.create_subprocess_exec(
                "git", "checkout", "-b", branch_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return branch_name

        raise RuntimeError("Over 3000 branches and still hungry? That's one well-fed bunny.")

    async def run(
        self, args: SafeWriteFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | SafeWriteFileResult, None]:
        # --- Validate path ---
        if ".." in args.path or os.path.isabs(args.path):
            yield SafeWriteFileResult(
                path=args.path, bytes_written=0, branch="", branch_created=False
            )
            return

        # --- Enforce content size ---
        content = args.content
        if len(content.encode()) > _MAX_CONTENT_BYTES:
            content = content[: _MAX_CONTENT_BYTES]

        # --- Ensure we're inside a git repo ---
        await self._ensure_git_repo()

        # --- Branch safety: always write on a bunny_loves_ ---
        branch_created = False

        if SafeWriteFile._session_branch is not None:
            # Reuse the branch we already created this session
            current = await self._current_branch()
            if current != SafeWriteFile._session_branch:
                proc = await asyncio.create_subprocess_exec(
                    "git", "checkout", SafeWriteFile._session_branch,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            branch = SafeWriteFile._session_branch
        else:
            # First write this session — always create a fresh branch
            branch = await self._create_branch()
            SafeWriteFile._session_branch = branch
            branch_created = True

        # --- Check overwrite ---
        target = anyio.Path(args.path)
        if await target.exists() and not args.overwrite:
            yield SafeWriteFileResult(
                path=args.path, bytes_written=0, branch=branch, branch_created=False
            )
            return

        # --- Write file ---
        parent = target.parent
        if parent != anyio.Path("."):
            await parent.mkdir(parents=True, exist_ok=True)

        async with await target.open("w") as f:
            await f.write(content)

        # --- Auto-commit the written file ---
        committed = False
        pushed = False
        pr_url = ""
        filename = os.path.basename(args.path)
        msg = args.commit_message or f"Add {filename}"

        try:
            # git add
            add_proc = await asyncio.create_subprocess_exec(
                "git", "add", args.path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await add_proc.communicate()

            if add_proc.returncode == 0:
                commit_proc = await asyncio.create_subprocess_exec(
                    "git", "commit", "-m", msg,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await commit_proc.communicate()
                committed = commit_proc.returncode == 0
        except Exception:
            pass  # Non-fatal — file is still written

        # --- Auto-push (use bot token if available) ---
        bot_token = os.environ.get("GITHUB_BOT_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        if committed and bot_token:
            try:
                # Set remote URL with bot token so push is authored by bot
                remote_proc = await asyncio.create_subprocess_exec(
                    "git", "remote", "get-url", "origin",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                remote_out, _ = await remote_proc.communicate()
                remote_url = remote_out.decode().strip()

                if remote_proc.returncode == 0 and "github.com" in remote_url:
                    match = OWNER_REPO_RE.search(remote_url)
                    if match:
                        owner = match.group("owner")
                        repo = match.group("repo")
                        authed_url = f"https://x-access-token:{bot_token}@github.com/{owner}/{repo}.git"
                        await asyncio.create_subprocess_exec(
                            "git", "remote", "set-url", "origin", authed_url,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )

                push_proc = await asyncio.create_subprocess_exec(
                    "git", "push", "-u", "origin", branch,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await push_proc.communicate()
                pushed = push_proc.returncode == 0
            except Exception:
                pass

        # --- Auto-create PR (once per session) ---
        if pushed and SafeWriteFile._session_pr_url is None:
            try:
                token = bot_token
                # Parse owner/repo from origin
                remote_proc = await asyncio.create_subprocess_exec(
                    "git", "remote", "get-url", "origin",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                remote_out, _ = await remote_proc.communicate()
                remote_url = remote_out.decode().strip()

                match = OWNER_REPO_RE.search(remote_url)
                if match and token:
                    owner = match.group("owner")
                    repo = match.group("repo")

                    api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
                    payload = {
                        "title": msg,
                        "body": f"Auto-generated PR for branch `{branch}`.",
                        "head": branch,
                        "base": "main",
                        "draft": False,
                    }

                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            api_url,
                            json=payload,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.github+json",
                                "X-GitHub-Api-Version": "2022-11-28",
                            },
                            timeout=30.0,
                        )

                    if resp.status_code in (200, 201):
                        pr_url = resp.json().get("html_url", "")
                        SafeWriteFile._session_pr_url = pr_url
            except Exception:
                pass  # Non-fatal

        # Return cached PR URL for subsequent writes
        if not pr_url and SafeWriteFile._session_pr_url:
            pr_url = SafeWriteFile._session_pr_url

        yield SafeWriteFileResult(
            path=args.path,
            bytes_written=len(content.encode()),
            branch=branch,
            branch_created=branch_created,
            committed=committed,
            pushed=pushed,
            pr_url=pr_url,
        )
