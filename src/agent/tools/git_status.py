"""Git status tool — view repository status and current branch."""

import asyncio
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


class GitStatusArgs(BaseModel):
    short: bool = Field(default=False, description="Use short format output")


class GitStatusResult(BaseModel):
    branch: str
    status: str
    success: bool


class GitStatusConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class GitStatus(BaseTool[GitStatusArgs, GitStatusResult, GitStatusConfig, BaseToolState]):
    description: ClassVar[str] = "Show the current git status and branch name."

    async def run(
        self, args: GitStatusArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GitStatusResult, None]:
        # Get current branch
        branch_proc = await asyncio.create_subprocess_exec(
            "git", "branch", "--show-current",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        branch_out, _ = await branch_proc.communicate()
        branch = branch_out.decode().strip() or "(detached)"

        # Get status
        cmd = ["git", "status"]
        if args.short:
            cmd.append("--short")

        status_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        status_out, status_err = await status_proc.communicate()

        output = status_out.decode().strip()
        if status_proc.returncode != 0:
            output = status_err.decode().strip()

        yield GitStatusResult(
            branch=branch,
            status=output,
            success=status_proc.returncode == 0,
        )
