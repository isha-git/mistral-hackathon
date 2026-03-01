"""Git log tool — view commit history."""

import asyncio
from typing import AsyncGenerator, ClassVar, Literal

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.core.types import ToolStreamEvent


class GitLogArgs(BaseModel):
    max_count: int = Field(default=20, description="Number of commits to show (max 100)")
    path: str | None = Field(default=None, description="Limit to commits touching this path")
    format: Literal["oneline", "short", "medium"] = Field(
        default="oneline", description="Output format"
    )


class GitLogResult(BaseModel):
    output: str
    success: bool


class GitLogConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class GitLog(BaseTool[GitLogArgs, GitLogResult, GitLogConfig, BaseToolState]):
    description: ClassVar[str] = "Show git commit history."

    async def run(
        self, args: GitLogArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GitLogResult, None]:
        count = min(max(args.max_count, 1), 100)

        cmd = ["git", "log", f"--max-count={count}", f"--format={args.format}"]

        if args.path:
            if ".." in args.path:
                yield GitLogResult(output="Path traversal not allowed", success=False)
                return
            cmd.extend(["--", args.path])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        output = stdout.decode().strip() if proc.returncode == 0 else stderr.decode().strip()

        yield GitLogResult(output=output, success=proc.returncode == 0)
