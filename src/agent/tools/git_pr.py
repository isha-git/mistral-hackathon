"""Git PR tool — push branch and create a GitHub pull request via REST API."""

import asyncio
import os
from typing import AsyncGenerator, ClassVar

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


class GitPrArgs(BaseModel):
    title: str = Field(description="Pull request title")
    body: str = Field(default="", description="Pull request body/description")
    base_branch: str = Field(default="main", description="Base branch to merge into")
    head_branch: str | None = Field(
        default=None, description="Head branch (defaults to current branch)"
    )
    draft: bool = Field(default=False, description="Create as draft PR")


class GitPrResult(BaseModel):
    pr_url: str = ""
    pr_number: int = 0
    output: str
    success: bool


class GitPrConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


async def _run_git(*args: str) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip(), stderr.decode().strip(), proc.returncode


class GitPr(BaseTool[GitPrArgs, GitPrResult, GitPrConfig, BaseToolState]):
    description: ClassVar[str] = (
        "Push the current branch and create a GitHub pull request. "
        "Requires GITHUB_TOKEN environment variable."
    )

    async def run(
        self, args: GitPrArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GitPrResult, None]:
        token = os.environ.get("GITHUB_BOT_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            yield GitPrResult(output="GITHUB_BOT_TOKEN/GITHUB_TOKEN not set", success=False)
            return

        # Determine head branch
        head = args.head_branch
        if not head:
            out, _, rc = await _run_git("branch", "--show-current")
            if rc != 0 or not out:
                yield GitPrResult(output="Could not determine current branch", success=False)
                return
            head = out

        # Parse owner/repo from remote
        remote_url, _, rc = await _run_git("remote", "get-url", "origin")
        if rc != 0:
            yield GitPrResult(output="No 'origin' remote found", success=False)
            return

        match = OWNER_REPO_RE.search(remote_url)
        if not match:
            yield GitPrResult(
                output=f"Could not parse owner/repo from remote URL: {remote_url}",
                success=False,
            )
            return

        owner = match.group("owner")
        repo = match.group("repo")

        # Push branch
        _, push_err, push_rc = await _run_git("push", "-u", "origin", head)
        if push_rc != 0:
            # Strip token from error output
            safe_err = push_err.replace(token, "***")
            yield GitPrResult(output=f"Push failed: {safe_err}", success=False)
            return

        # Create PR via GitHub REST API
        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        payload = {
            "title": args.title,
            "body": args.body,
            "head": head,
            "base": args.base_branch,
            "draft": args.draft,
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
            data = resp.json()
            yield GitPrResult(
                pr_url=data.get("html_url", ""),
                pr_number=data.get("number", 0),
                output=f"PR #{data.get('number')} created: {data.get('html_url')}",
                success=True,
            )
        else:
            yield GitPrResult(
                output=f"GitHub API error ({resp.status_code}): {resp.text[:500]}",
                success=False,
            )
