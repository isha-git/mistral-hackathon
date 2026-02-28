"""Git clone tool — clone a GitHub repository."""

import asyncio
import os
import re
from typing import AsyncGenerator, ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.core.types import ToolStreamEvent

_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$"
)


class GitCloneArgs(BaseModel):
    repo_url: str = Field(description="HTTPS GitHub URL to clone (e.g. https://github.com/owner/repo)")
    target_dir: str = Field(
        default="",
        description="Directory to clone into (defaults to repo name from URL)",
    )


class GitCloneResult(BaseModel):
    output: str
    success: bool


class GitCloneConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class GitClone(BaseTool[GitCloneArgs, GitCloneResult, GitCloneConfig, BaseToolState]):
    description: ClassVar[str] = (
        "Clone a GitHub repository via HTTPS. "
        "Only public github.com URLs are accepted unless GITHUB_TOKEN is set."
    )

    async def run(
        self, args: GitCloneArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GitCloneResult, None]:
        # Validate URL
        if not _GITHUB_URL_RE.match(args.repo_url):
            yield GitCloneResult(
                output=f"Invalid repo URL. Must be https://github.com/owner/repo",
                success=False,
            )
            return

        # Block path traversal
        if ".." in args.target_dir:
            yield GitCloneResult(output="Path traversal not allowed in target_dir", success=False)
            return

        # Derive target directory from repo URL if not specified
        target_dir = args.target_dir
        if not target_dir:
            repo_name = args.repo_url.rstrip("/").rsplit("/", 1)[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]
            target_dir = repo_name

        # Build clone URL with token for private repos
        clone_url = args.repo_url
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            # Inject token: https://x-access-token:TOKEN@github.com/owner/repo
            clone_url = args.repo_url.replace(
                "https://github.com/", f"https://x-access-token:{token}@github.com/"
            )

        # If target already exists and is a git repo, cd into it and pull
        git_dir = os.path.join(target_dir, ".git")
        if os.path.isdir(git_dir):
            os.chdir(target_dir)
            pull_proc = await asyncio.create_subprocess_exec(
                "git", "pull", "--ff-only",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            pull_out, pull_err = await pull_proc.communicate()
            combined = (pull_out.decode() + "\n" + pull_err.decode()).strip()
            if token:
                combined = combined.replace(token, "***")
            yield GitCloneResult(
                output=f"Repo already cloned. Pulled latest.\n{combined}\nWorking directory: {os.getcwd()}",
                success=True,
            )
            return

        cmd = ["git", "clone", "--depth", "50", clone_url, target_dir]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        combined = (stdout.decode() + "\n" + stderr.decode()).strip()
        # Strip token from output to prevent leaks
        if token:
            combined = combined.replace(token, "***")

        # After successful clone, cd into the cloned repo so subsequent
        # tools (safe_write_file, git_pr) operate inside it with origin set.
        if proc.returncode == 0:
            if os.path.isdir(target_dir):
                os.chdir(target_dir)
                combined += f"\nChanged working directory to {os.getcwd()}"

        yield GitCloneResult(
            output=combined,
            success=proc.returncode == 0,
        )
