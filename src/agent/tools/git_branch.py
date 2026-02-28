"""Git branch tool — create and checkout a new branch."""

import asyncio
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

# Allows typical branch names: feature/foo, fix-bar, release/1.0
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,249}$")


class GitBranchArgs(BaseModel):
    branch_name: str = Field(description="Name for the new branch")
    checkout: bool = Field(default=True, description="Checkout the branch after creating it")


class GitBranchResult(BaseModel):
    output: str
    success: bool


class GitBranchConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class GitBranch(BaseTool[GitBranchArgs, GitBranchResult, GitBranchConfig, BaseToolState]):
    description: ClassVar[str] = "Create a new git branch and optionally check it out."

    async def run(
        self, args: GitBranchArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GitBranchResult, None]:
        if not _BRANCH_RE.match(args.branch_name):
            yield GitBranchResult(
                output="Invalid branch name. Use alphanumeric characters, hyphens, underscores, dots, and slashes.",
                success=False,
            )
            return

        if ".." in args.branch_name:
            yield GitBranchResult(output="Branch name must not contain '..'", success=False)
            return

        if args.checkout:
            cmd = ["git", "checkout", "-b", args.branch_name]
        else:
            cmd = ["git", "branch", args.branch_name]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        output = (stdout.decode() + "\n" + stderr.decode()).strip()

        yield GitBranchResult(output=output, success=proc.returncode == 0)
